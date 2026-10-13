"""
The conversation and its history — pure: no clock, no I/O, no state machine.

A conversation is the session of one user: its turns live in RAM, the oldest are dropped
when the history outgrows its budget of tokens. The orchestrator owns it (opens, closes, appends); the engine and the LLM
backends only read the turns they are handed.
"""
import math
from collections import deque
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from backend.models import PERSONA_FIELDS, ChatTurn

SYSTEM, USER, ASSISTANT = "system", "user", "assistant"

# Between two utterances of the person that follow each other without an answer.
_UTTERANCE_SEPARATOR = "\n"

# What a chat message costs beyond its text: its role and its separators.
_MESSAGE_OVERHEAD_TOKENS = 4


def estimated_tokens(text: str, chars_per_token: float) -> int:
    """About how many tokens a model reads the text as, at `chars_per_token` characters
    for one token. An estimate: each backend has its own tokenizer."""
    return math.ceil(len(text) / chars_per_token)


def turn_tokens(turn: ChatTurn, chars_per_token: float) -> int:
    """About how many tokens a turn costs in a request: the message of the person
    and, when something was answered, the message of the avatar."""
    tokens = estimated_tokens(turn.user_text, chars_per_token) + _MESSAGE_OVERHEAD_TOKENS
    if turn.answer:
        tokens += estimated_tokens(turn.answer, chars_per_token) + _MESSAGE_OVERHEAD_TOKENS
    return tokens


class Conversation:
    """One session of one user. `user_id` is None for a guest. The turns it keeps
    never cost more than `max_tokens`, estimated at `chars_per_token` characters for
    one token."""

    def __init__(
        self, id: str, user_id: Optional[str], max_tokens: int, chars_per_token: float
    ) -> None:
        self.id = id
        self.user_id = user_id
        self._max_tokens = max_tokens
        self._chars_per_token = chars_per_token
        self._turns: deque[tuple[ChatTurn, int]] = deque()  # each with its tokens
        self._tokens = 0

    @property
    def history(self) -> tuple[ChatTurn, ...]:
        """The turns kept, oldest first."""
        return tuple(turn for turn, _ in self._turns)

    @property
    def tokens(self) -> int:
        """About how many tokens the turns kept cost in a request."""
        return self._tokens

    def append(self, turn: ChatTurn) -> None:
        """Add a turn, then drop the oldest ones, whole, until the history fits its
        budget: short turns stay longer than long ones. A turn larger than the whole
        budget is not kept, and the turns already kept stay: it evicts nothing."""
        tokens = turn_tokens(turn, self._chars_per_token)
        if tokens > self._max_tokens:
            return
        self._turns.append((turn, tokens))
        self._tokens += tokens
        while self._tokens > self._max_tokens:
            _, dropped = self._turns.popleft()
            self._tokens -= dropped


@dataclass
class _Sentence:
    text: str
    fallback: bool
    first_audio_at: Optional[float] = None
    audio_sec: float = 0.0


class TurnRecord:
    """What one running turn has heard and spoken so far, and the one place where it
    becomes a ChatTurn. Times are seconds of the caller's clock."""

    def __init__(self) -> None:
        self._user_text: Optional[str] = None
        self._confidence: Optional[float] = None
        self._sentences: list[_Sentence] = []
        self._cut_at: Optional[float] = None
        self._interrupted = False

    def heard(self, text: str, confidence: Optional[float]) -> None:
        """The transcript of the turn is known."""
        self._user_text = text
        self._confidence = confidence

    def spoke(self, text: str, audio_sec: float, now: float, *, fallback: bool = False) -> None:
        """One audio message was sent at `now`. A non-empty `text` starts a sentence;
        `audio_sec` is the length of the audio sent with it (0 = text only)."""
        if text:
            self._sentences.append(_Sentence(text, fallback))
        if not self._sentences or audio_sec <= 0:
            return
        sentence = self._sentences[-1]
        if sentence.first_audio_at is None:
            sentence.first_audio_at = now
        sentence.audio_sec += audio_sec

    def cut(self, at: float, *, interrupted: bool = False) -> None:
        """The turn stopped at `at` before playing to its end. The first cut keeps its
        time; `interrupted` says it was cancelled, not that it failed."""
        if self._cut_at is None:
            self._cut_at = at
        self._interrupted = self._interrupted or interrupted

    def chat_turn(self) -> Optional[ChatTurn]:
        """The turn as it happened, or None when nothing was understood. The answer
        holds the sentences that had started to play: a sentence starts when its first
        audio was sent, or when the one before it ends if that is later, and counts when
        that is not after the cut. Left out: the fallback sentence and a sentence that
        got no audio."""
        if self._user_text is None or not self._user_text.strip():
            return None
        spoken: list[str] = []
        previous_end = float("-inf")
        for sentence in self._sentences:
            if sentence.first_audio_at is None:
                continue  # text only: never heard
            start = max(sentence.first_audio_at, previous_end)
            previous_end = start + sentence.audio_sec
            started = self._cut_at is None or start <= self._cut_at
            if started and not sentence.fallback:
                spoken.append(sentence.text)
        return ChatTurn(
            user_text=self._user_text,
            sentences=tuple(spoken),
            interrupted=self._interrupted,
            confidence=self._confidence,
        )


def persona_lines(config, fields: Mapping[str, str]) -> str:
    """The persona as it is written into the system prompt: the fields of
    PERSONA_DEFAULT, each replaced by the one of `fields` that is set, in the order of
    PERSONA_FIELDS, one line per field with its label. `config` holds the texts."""
    persona = {**config.PERSONA_DEFAULT, **{k: v for k, v in fields.items() if v}}
    return "\n".join(
        config.PERSONA_PROMPT_FIELD.format(
            label=config.PERSONA_FIELD_LABELS[name], value=persona[name]
        )
        for name in PERSONA_FIELDS
        if name in persona
    )


def system_prompt(config, persona: str = "") -> str:
    """The system message of a turn: SYSTEM_PROMPT with the persona written into it.
    `persona` is the one a person gave their avatar, as `persona_lines` wrote it; ""
    is the default persona."""
    return config.SYSTEM_PROMPT.format(persona=persona or persona_lines(config, {}))


def chat_messages(
    history: Sequence[ChatTurn], user_text: str, *, system_prompt: str, profile: str = ""
) -> list[tuple[str, str]]:
    """The (role, text) messages of one LLM request: the system prompt, the profile as
    a second system message when there is one, the history, the current utterance.
    A turn without an answer gives a user message alone, and user messages that follow
    each other (the current utterance included) are joined, so the roles alternate."""
    messages = [(SYSTEM, system_prompt)]
    if profile:
        messages.append((SYSTEM, profile))
    pending: list[str] = []  # utterances of the person not answered yet
    for turn in history:
        pending.append(turn.user_text)
        if turn.answer:
            messages.append((USER, _UTTERANCE_SEPARATOR.join(pending)))
            messages.append((ASSISTANT, turn.answer))
            pending = []
    pending.append(user_text)
    messages.append((USER, _UTTERANCE_SEPARATOR.join(pending)))
    return messages


def search_query(history: Sequence[ChatTurn], user_text: str, previous_turns: int) -> str:
    """The text searched in the knowledge base for `user_text`: the last
    `previous_turns` utterances of the person, then the current one. A follow-up alone
    names nothing a search can find."""
    previous = [turn.user_text for turn in history[-previous_turns:]] if previous_turns > 0 else []
    return _UTTERANCE_SEPARATOR.join([*previous, user_text])
