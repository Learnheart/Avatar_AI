"""
MemoryConsolidation — what a logged-in person said in a conversation, gathered into
memory items.

Each turn of the conversation is written to the application database as it ends. When
the conversation closes, one LLM call, outside any turn, reads the turns together with
what is already remembered and answers with operations: a new item, an existing one
stated again, an existing one contradicted. What was heard is only proposed: a new item
is `pending` until the person approves it on the profile page, or, for a preference or a
personality trait, until later conversations state it again. The turns are deleted once
the conversation was read; only the quotes kept as evidence remain. A conversation that
could not be read (the LLM failed, the process was killed) keeps its turns and is read
at the next start, until they are too old to be kept. The conversations of one user are
read one at a time: each reading sees what the one before it stored.

The store, the LLM and the way to drop a user's cached profile are given by
`backend/main.py`.
"""
import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence, TypeVar

from opentelemetry.context import Context
from opentelemetry.trace import Span

from backend.config import AppConfig
from backend.core.contracts import LLM, LLMError, MemoryServiceError
from backend.features.memory.extraction import (
    OP_ADD,
    OP_CONFIRM,
    ExtractedItem,
    InvalidExtraction,
    Operation,
    parse_operations,
)
from backend.features.memory.memory import item_text, one_line
from backend.features.memory.reading import as_document, comparable, finish, quote
from backend.features.memory.store import MemoryStore
from backend.models import (
    MEMORY_SOURCE_CONVERSATION,
    MEMORY_STATUS_ACTIVE,
    MEMORY_STATUS_PENDING,
    MEMORY_STATUS_REJECTED,
    ChatTurn,
    MemoryItem,
    StatedAgain,
    StoredTurn,
)
from backend.utils.database import DatabaseError
from backend.utils.logging import get_logger
from backend.utils.telemetry import fail, tracer

logger = get_logger("memory.consolidation")

T = TypeVar("T")

# The categories a proposed item is promoted in by being stated again. A name or a way
# of addressing is not: the STT mishears a name the same way every time.
_PROMOTED_BY_REPETITION = ("preference", "personality")

# `memory.outcome` of the `memory.consolidate` span.
STORED = "stored"  # items were proposed, or existing ones stated again
NOTHING = "nothing"  # read, nothing to remember
NO_SPEECH = "no_speech"  # nothing of the person was left to read: no LLM call
MISSING = "missing"  # no such conversation, or consolidated already
EXTRACTION_FAILED = "extraction_failed"  # the LLM failed or answered something else
ERROR = "error"


@dataclass(frozen=True)
class Changes:
    """What reading one conversation changes in the items of a user."""
    proposed: tuple[MemoryItem, ...] = ()
    stated_again: tuple[StatedAgain, ...] = ()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryConsolidation:
    def __init__(
        self,
        config: AppConfig,
        store: MemoryStore,
        llm: LLM,
        forget_profile: Callable[[str], None],
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """`forget_profile(user_id)` drops the cached profile of a user whose items
        changed (`MemoryService.forget_profile`)."""
        self.config = config
        self._store = store
        self._llm = llm
        self._forget_profile = forget_profile
        self._clock = clock
        # One reading at a time per user, from the items read to the changes stored:
        # two conversations read together would each propose what the other proposes.
        self._reading: dict[str, asyncio.Lock] = {}

    async def record_turn(self, user_id: str, conversation_id: str, turn: ChatTurn) -> None:
        """Keep one turn of a conversation of the user until the conversation is read.
        Raises MemoryServiceError."""
        kept = await self._call(
            self._store.add_turn,
            user_id,
            conversation_id,
            turn.user_text,
            turn.answer,
            turn.confidence,
            turn.interrupted,
            self._clock(),
        )
        if not kept:
            logger.warning({
                "msg": "Turn not kept: its conversation is closed",
                "conversation_id": conversation_id,
            })

    async def consolidate(self, user_id: str, conversation_id: str) -> str:
        """Read a conversation of the user that is over into memory items, in a trace
        of its own; the outcome, as the span names it. A conversation the LLM could not
        read keeps its turns and stays to be read. Raises MemoryServiceError (the
        store): nothing was changed then either."""
        with tracer.start_as_current_span(
            "memory.consolidate",
            context=Context(),  # a trace of its own, whatever the caller was doing
            attributes={"conversation.id": conversation_id, "user.id": user_id},
        ) as span:
            try:
                async with self._reading.setdefault(user_id, asyncio.Lock()):
                    outcome = await self._consolidate(span, user_id, conversation_id)
            except BaseException:
                span.set_attribute("memory.outcome", ERROR)
                raise
            span.set_attribute("memory.outcome", outcome)
        logger.info({
            "msg": "Conversation consolidated",
            "conversation_id": conversation_id,
            "outcome": outcome,
        })
        return outcome

    async def resume(self) -> None:
        """The housekeeping of a start: delete the turns older than
        MEMORY_TRANSCRIPT_TTL_DAYS, then read every conversation that was not read yet,
        closed or left open by a process that was killed, one after the other. Never
        raises: whatever fails is logged, and the next conversation is read."""
        now = self._clock()
        oldest = now - timedelta(days=self.config.MEMORY_TRANSCRIPT_TTL_DAYS)
        try:
            expired = await self._call(self._store.expire_turns, oldest, now)
            # Only the conversations that existed at the start: one opened since then is
            # running, and is read when it closes.
            waiting = await self._call(self._store.unconsolidated_conversations, now)
        except Exception as e:
            logger.error({"msg": "Conversations left to read not found", "error": str(e)})
            return
        logger.info({
            "msg": "Conversations left to read",
            "conversations": len(waiting),
            "turns_expired": expired,
        })
        for user_id, conversation_id in waiting:
            try:
                await self.consolidate(user_id, conversation_id)
            except Exception as e:  # whatever it is, the others are still read
                logger.error({
                    "msg": "Conversation not consolidated",
                    "conversation_id": conversation_id,
                    "error": f"{type(e).__name__}: {e}",
                })

    # ── Reading one conversation ──────────────────────────────────────────

    async def _consolidate(self, span: Span, user_id: str, conversation_id: str) -> str:
        if not await self._call(
            self._store.close_conversation, user_id, conversation_id, self._clock()
        ):
            return MISSING
        turns = await self._call(self._store.list_turns, user_id, conversation_id)
        items = await self._call(self._store.list_items, user_id)
        heard = self._heard(turns)
        span.set_attributes({
            "memory.turns": len(turns),
            "memory.turns_skipped": len(turns) - len(heard),
            "memory.items_known": len(items),
        })
        if not heard:
            # Nothing the person said is left: no request, the turns go all the same.
            await self._call(self._store.consolidate, user_id, conversation_id, self._clock())
            return NO_SPEECH

        # In the request an item is named by a number: short enough to be copied right.
        numbered = {
            str(number): item for number, item in enumerate(self._shown_items(items), start=1)
        }
        operations = await self._ask(span, numbered, heard)
        if operations is None:
            return EXTRACTION_FAILED

        now = self._clock()
        changes = plan_changes(
            operations,
            numbered,
            items,
            user_id=user_id,
            now=now,
            promote_seen_count=self.config.MEMORY_PROMOTE_SEEN_COUNT,
            # A quote is one of what the person said, not of what the avatar answered.
            said=comparable(" ".join(turn.user_text for turn in heard)),
        )
        try:
            counted = await self._call(
                self._store.consolidate,
                user_id,
                conversation_id,
                now,
                changes.proposed,
                changes.stated_again,
            )
        finally:
            self._forget_profile(user_id)
        if counted is None:
            return MISSING
        span.set_attributes(_applied(changes, counted))
        return STORED if changes.proposed or counted else NOTHING

    async def _ask(
        self, span: Span, numbered: dict[str, MemoryItem], heard: Sequence[StoredTurn]
    ) -> Optional[list[Operation]]:
        """What the LLM reads in the turns, given the items under their numbers: the
        valid operations of its answer. None when it failed or answered something else
        than the operations asked for: the span is failed, and says why."""
        known, transcript = self._known(numbered), self._transcript(heard)
        span.set_attributes({"memory.known": known, "memory.transcript": transcript})
        max_chars = self.config.MEMORY_ITEM_MAX_CHARS
        try:
            answer = await self._llm.complete(
                self.config.MEMORY_EXTRACT_PROMPT.format(max_chars=max_chars),
                self.config.MEMORY_EXTRACT_REQUEST.format(items=known, transcript=transcript),
            )
            span.set_attribute("llm.answer", answer)
            operations = parse_operations(
                answer, lambda found: item_text(found, max_chars), numbered.keys()
            )
        except (LLMError, InvalidExtraction) as e:
            fail(span, e)
            logger.error({"msg": "Conversation could not be read", "error": str(e)})
            return None
        span.set_attribute("memory.operations", _as_json([_shown(op) for op in operations]))
        return operations

    def _shown_items(self, items: Sequence[MemoryItem]) -> list[MemoryItem]:
        """The items the request lists, in the order they are stored in: every active
        and pending one, and of the rejected ones the MEMORY_EXTRACT_MAX_REJECTED most
        recently rejected. Rejected items only ever grow in number, and one that is not
        shown is still never proposed again (`plan_changes` compares with all of
        them)."""
        rejected = sorted(
            (item for item in items if item.status == MEMORY_STATUS_REJECTED),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        left_out = {item.id for item in rejected[self.config.MEMORY_EXTRACT_MAX_REJECTED:]}
        return [item for item in items if item.id not in left_out]

    def _heard(self, turns: Sequence[StoredTurn]) -> list[StoredTurn]:
        """The turns that are read: those the STT was sure enough of (a backend that
        gives no confidence is not filtered), the most recent ones when together they
        are longer than MEMORY_CONSOLIDATE_MAX_CHARS."""
        floor = self.config.MEMORY_MIN_STT_CONFIDENCE
        heard = [
            turn for turn in turns
            if turn.stt_confidence is None or turn.stt_confidence >= floor
        ]
        room = self.config.MEMORY_CONSOLIDATE_MAX_CHARS
        recent: list[StoredTurn] = []
        for turn in reversed(heard):
            room -= len(self._lines(turn))
            if room < 0 and recent:
                break
            recent.append(turn)
        return recent[::-1]

    def _lines(self, turn: StoredTurn) -> str:
        """One turn as it is read: what the person said, then what was answered."""
        lines = [self.config.MEMORY_EXTRACT_USER_LINE.format(text=one_line(turn.user_text))]
        answer = one_line(turn.answer_text)
        if answer:
            lines.append(self.config.MEMORY_EXTRACT_AVATAR_LINE.format(text=answer))
        return "\n".join(lines)

    def _transcript(self, turns: Sequence[StoredTurn]) -> str:
        """The turns between the tags the prompt names, as material to read."""
        text = "\n".join(self._lines(turn) for turn in turns)
        return as_document(self.config.MEMORY_EXTRACT_TRANSCRIPT, text)

    def _known(self, numbered: dict[str, MemoryItem]) -> str:
        """What is already remembered, each item under its number."""
        lines = [
            self.config.MEMORY_EXTRACT_ITEM.format(
                id=number, status=item.status, category=item.category, text=item.text
            )
            for number, item in numbered.items()
        ]
        text = "\n".join(lines) or self.config.MEMORY_EXTRACT_NO_ITEMS
        return as_document(self.config.MEMORY_EXTRACT_ITEMS, text)

    async def _call(self, call: Callable[..., T], *args) -> T:
        try:
            return await finish(call, *args)
        except DatabaseError as e:
            raise MemoryServiceError(f"The memory store failed: {e}") from e


def plan_changes(
    operations: Sequence[Operation],
    numbered: dict[str, MemoryItem],
    items: Sequence[MemoryItem],
    *,
    user_id: str,
    now: datetime,
    promote_seen_count: int,
    said: str,
) -> Changes:
    """What the operations read from one conversation change in the items of the user.
    Pure. `numbered` holds the items the request listed, under the id it gave each;
    `items` are all the items of the user (active, pending, rejected), listed or not;
    `said` is what the person said, as `comparable`.

    - `add`: a `pending` item. One that says what an active or pending item says
      already counts as that item being stated again; one that says what a rejected
      item says is dropped.
    - `confirm`: the item was stated again, counted once per conversation. A pending
      preference or personality trait becomes active at `promote_seen_count`, unless
      it proposes to replace another item (a contradiction is settled by the person
      only) or has no quote (nothing shows the person ever said it); a rejected item
      is left alone.
    - `replace`: a `pending` item that proposes to replace an active one, which is not
      touched; of an item that is not active, an `add`.
    """
    by_text = {_same(item.text): item for item in items}
    proposed: dict[str, MemoryItem] = {}  # by what they say
    stated_again: dict[str, StatedAgain] = {}  # by item id

    def state_again(item: MemoryItem) -> None:
        if item.status == MEMORY_STATUS_REJECTED or item.id in stated_again:
            return
        promote = (
            item.status == MEMORY_STATUS_PENDING
            and item.category in _PROMOTED_BY_REPETITION
            and item.replaces_id is None
            and item.evidence is not None
            and item.seen_count + 1 >= promote_seen_count
        )
        stated_again[item.id] = StatedAgain(item.id, item.status, promote)

    def propose(found: ExtractedItem, replaces: Optional[MemoryItem]) -> None:
        said_already = by_text.get(_same(found.text))
        if said_already is not None:
            state_again(said_already)
        elif _same(found.text) not in proposed:
            # A microsecond apart: the items are listed in the order of the answer.
            at = now + timedelta(microseconds=len(proposed))
            proposed[_same(found.text)] = MemoryItem(
                id=uuid.uuid4().hex,
                user_id=user_id,
                category=found.category,
                text=found.text,
                created_at=at,
                updated_at=at,
                status=MEMORY_STATUS_PENDING,
                source=MEMORY_SOURCE_CONVERSATION,
                replaces_id=replaces.id if replaces else None,
                evidence=quote(found.evidence, said),
            )

    for operation in operations:
        if operation.op == OP_ADD:
            propose(operation.item, None)
        elif operation.op == OP_CONFIRM:
            state_again(numbered[operation.id])
        else:  # OP_REPLACE
            target = numbered[operation.id]
            replaceable = target.status == MEMORY_STATUS_ACTIVE
            propose(operation.item, target if replaceable else None)
    return Changes(tuple(proposed.values()), tuple(stated_again.values()))


def _applied(changes: Changes, counted: Sequence[str]) -> dict:
    """What a reading stored, as the attributes of its span. `counted` are the items
    that still were what the reading saw: the others were approved, rejected or deleted
    meanwhile, and left as the person put them."""
    return {
        "memory.items": len(changes.proposed),
        "memory.applied": _as_json({
            "proposed": [
                {
                    "id": item.id,
                    "category": item.category,
                    "text": item.text,
                    "evidence": item.evidence,
                    "replaces_id": item.replaces_id,
                }
                for item in changes.proposed
            ],
            "stated_again": [
                {"id": stated.item_id, "promoted": stated.promote}
                for stated in changes.stated_again
                if stated.item_id in counted
            ],
        }),
    }


def _same(text: str) -> str:
    """The text as two items are compared: one line, letters composed, any case."""
    return comparable(text).casefold()


def _shown(operation: Operation) -> dict:
    """An operation as the span shows it."""
    shown: dict = {"op": operation.op}
    if operation.id is not None:
        shown["id"] = operation.id
    if operation.item is not None:
        shown.update(
            category=operation.item.category,
            text=operation.item.text,
            evidence=operation.item.evidence,
        )
    return shown


def _as_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
