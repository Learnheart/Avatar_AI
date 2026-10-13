"""The conversation, the record of a running turn and the chat messages made of them."""
from backend.core.conversation.history import (
    Conversation,
    TurnRecord,
    chat_messages,
    estimated_tokens,
    persona_lines,
    search_query,
    system_prompt,
    turn_tokens,
)
from backend.config import AppConfig
from backend.models import PERSONA_FIELDS, ChatTurn

SYSTEM_PROMPT = "Bạn là trợ lý."


def _messages(history, user_text, **kwargs):
    return chat_messages(history, user_text, system_prompt=SYSTEM_PROMPT, **kwargs)


# ── Conversation ──────────────────────────────────────────────────────────

def _conversation(
    id: str = "c1", user_id=None, max_tokens: int = 2000, chars_per_token: float = 3.0
) -> Conversation:
    return Conversation(id, user_id, max_tokens=max_tokens, chars_per_token=chars_per_token)


def test_a_conversation_carries_its_id_and_its_user():
    assert _conversation("c1", "user-7").user_id == "user-7"
    guest = _conversation("c2")

    assert (guest.id, guest.user_id, guest.history) == ("c2", None, ())


def test_append_keeps_the_order():
    conversation = _conversation()

    conversation.append(ChatTurn("một", ("Một.",)))
    conversation.append(ChatTurn("hai", ("Hai.",)))

    assert [turn.user_text for turn in conversation.history] == ["một", "hai"]


# ── The token window ──────────────────────────────────────────────────────

def test_tokens_are_estimated_from_the_characters_rounded_up():
    assert estimated_tokens("", 3.0) == 0
    assert estimated_tokens("ba", 3.0) == 1
    assert estimated_tokens("x" * 30, 3.0) == 10
    assert estimated_tokens("x" * 31, 3.0) == 11
    assert estimated_tokens("x" * 30, 2.0) == 15


def test_a_turn_costs_its_two_messages_or_one_when_nothing_was_answered():
    # 4 tokens per message beyond its text.
    assert turn_tokens(ChatTurn("x" * 30, ("y" * 14, "z" * 15)), 3.0) == (10 + 4) + (10 + 4)
    assert turn_tokens(ChatTurn("x" * 30), 3.0) == 10 + 4
    assert turn_tokens(ChatTurn("x" * 30, interrupted=True), 3.0) == 14


def _spoken(name: str, chars: int) -> ChatTurn:
    """A turn without an answer whose utterance is `chars` long, named by its start."""
    return ChatTurn(name + "x" * (chars - len(name)))


def _names(conversation: Conversation) -> list[str]:
    return [turn.user_text[0] for turn in conversation.history]


def test_turns_are_kept_while_they_fit_the_budget():
    conversation = _conversation(max_tokens=42)  # three turns of 10 + 4 tokens

    for name in "abc":
        conversation.append(_spoken(name, 30))

    assert _names(conversation) == ["a", "b", "c"]
    assert conversation.tokens == 42


def test_the_oldest_turns_are_dropped_whole_when_one_more_does_not_fit():
    conversation = _conversation(max_tokens=42)

    for name in "abcd":
        conversation.append(_spoken(name, 30))

    assert _names(conversation) == ["b", "c", "d"]
    assert all(len(turn.user_text) == 30 for turn in conversation.history)
    assert conversation.tokens == 42


def test_a_long_turn_pushes_out_as_many_turns_as_it_needs():
    conversation = _conversation(max_tokens=42)
    for name in "abc":
        conversation.append(_spoken(name, 30))

    conversation.append(_spoken("d", 60))  # 20 + 4 tokens: two of the three must go

    assert _names(conversation) == ["c", "d"]
    assert conversation.tokens == 14 + 24


def test_many_short_turns_stay_where_a_few_long_ones_are_dropped():
    short, long = _conversation(max_tokens=100), _conversation(max_tokens=100)

    for index in range(30):
        short.append(ChatTurn("ừ", ("Vâng.",)))            # (1 + 4) + (2 + 4) tokens
        long.append(ChatTurn("x" * 90, ("y" * 90,)))        # (30 + 4) + (30 + 4) tokens

    assert len(short.history) == 9
    assert len(long.history) == 1
    assert short.tokens <= 100 and long.tokens <= 100


def test_a_turn_larger_than_the_whole_budget_is_not_kept_and_evicts_nothing():
    conversation = _conversation(max_tokens=42)
    for name in "abc":
        conversation.append(_spoken(name, 30))

    conversation.append(_spoken("d", 300))

    assert _names(conversation) == ["a", "b", "c"]
    assert conversation.tokens == 42
    conversation.append(_spoken("e", 30))
    assert _names(conversation) == ["b", "c", "e"]


def test_a_turn_larger_than_the_budget_leaves_an_empty_conversation_empty():
    conversation = _conversation(max_tokens=42)

    conversation.append(_spoken("a", 300))

    assert conversation.history == ()
    assert conversation.tokens == 0


def test_the_estimate_follows_the_characters_per_token():
    generous, careful = _conversation(max_tokens=42), _conversation(max_tokens=42, chars_per_token=1.5)

    for name in "abc":
        generous.append(_spoken(name, 30))
        careful.append(_spoken(name, 30))  # 20 + 4 tokens each at 1.5 characters

    assert _names(generous) == ["a", "b", "c"]
    assert _names(careful) == ["c"]


def test_a_turn_holds_its_sentences_and_its_interrupted_flag():
    turn = ChatTurn("xin chào", ("Chào bạn.", "Bạn cần gì?"), interrupted=True, confidence=0.9)

    assert turn.sentences == ("Chào bạn.", "Bạn cần gì?")
    assert turn.answer == "Chào bạn. Bạn cần gì?"
    assert turn.interrupted and turn.confidence == 0.9


def test_a_turn_with_an_empty_answer_is_kept():
    conversation = _conversation()

    conversation.append(ChatTurn("tôi muốn hỏi"))

    assert conversation.history == (ChatTurn("tôi muốn hỏi", (), False, None),)
    assert conversation.history[0].answer == ""


# ── TurnRecord ────────────────────────────────────────────────────────────

def test_a_turn_without_a_transcript_is_not_recorded():
    never_heard, empty = TurnRecord(), TurnRecord()
    never_heard.spoke("Xin chào.", 1.0, now=0.0)
    empty.heard("  ", None)

    assert never_heard.chat_turn() is None
    assert empty.chat_turn() is None


def test_a_turn_that_played_to_its_end_keeps_every_sentence():
    record = TurnRecord()
    record.heard("xin chào", 0.8)
    record.spoke("Một.", 1.0, now=10.0)
    record.spoke("", 1.0, now=10.0)
    record.spoke("Hai.", 2.0, now=10.1)

    assert record.chat_turn() == ChatTurn("xin chào", ("Một.", "Hai."), False, 0.8)


def test_a_cut_keeps_the_sentences_that_had_started_to_play():
    record = TurnRecord()
    record.heard("xin chào", None)
    # Three sentences of one second, all sent at once: they play one after the other.
    for text in ("Một.", "Hai.", "Ba."):
        record.spoke(text, 1.0, now=10.0)

    record.cut(11.5, interrupted=True)

    assert record.chat_turn() == ChatTurn("xin chào", ("Một.", "Hai."), True, None)


def test_a_sentence_sent_after_the_previous_one_ended_starts_when_it_was_sent():
    record = TurnRecord()
    record.heard("xin chào", None)
    record.spoke("Một.", 1.0, now=10.0)
    record.spoke("Hai.", 1.0, now=15.0)  # the LLM was slow: a silence before it

    record.cut(12.0, interrupted=True)

    assert record.chat_turn().sentences == ("Một.",)


def test_the_fallback_sentence_and_a_sentence_without_audio_are_left_out():
    record = TurnRecord()
    record.heard("xin chào", None)
    record.spoke("Câu không có tiếng.", 0.0, now=10.0)
    record.spoke("Xin lỗi.", 1.0, now=10.0, fallback=True)
    record.spoke("Câu thật.", 1.0, now=10.0)

    assert record.chat_turn().sentences == ("Câu thật.",)


def test_the_first_cut_keeps_its_time_and_a_failure_is_not_an_interruption():
    record = TurnRecord()
    record.heard("xin chào", None)
    record.spoke("Một.", 1.0, now=10.0)
    record.spoke("Hai.", 1.0, now=10.0)

    record.cut(10.5)
    record.cut(99.0)

    assert record.chat_turn() == ChatTurn("xin chào", ("Một.",), False, None)


# ── chat_messages ─────────────────────────────────────────────────────────

def test_no_history_and_no_profile_give_the_system_prompt_and_the_utterance():
    assert _messages((), "Xin chào") == [("system", SYSTEM_PROMPT), ("user", "Xin chào")]


def test_history_becomes_alternating_messages_before_the_utterance():
    history = (ChatTurn("một", ("Một.", "Rồi.")), ChatTurn("hai", ("Hai.",)))

    assert _messages(history, "ba")[1:] == [
        ("user", "một"), ("assistant", "Một. Rồi."),
        ("user", "hai"), ("assistant", "Hai."),
        ("user", "ba"),
    ]


def test_the_profile_is_a_second_system_message():
    messages = _messages((ChatTurn("một", ("Một.",)),), "hai", profile="Tên: Linh")

    assert messages[:3] == [("system", SYSTEM_PROMPT), ("system", "Tên: Linh"), ("user", "một")]


# ── The persona in the system prompt ──────────────────────────────────────

def test_the_default_persona_has_fields_of_the_persona_only():
    config = AppConfig()

    assert set(config.PERSONA_DEFAULT) <= set(PERSONA_FIELDS)
    assert all(text.strip() for text in config.PERSONA_DEFAULT.values())


def test_without_fields_the_persona_is_the_default_one_in_the_order_of_the_fields():
    config = AppConfig()

    assert persona_lines(config, {}).splitlines() == [
        f"- Vai trò: {config.PERSONA_DEFAULT['role']}",
        f"- Tính cách: {config.PERSONA_DEFAULT['personality']}",
        f"- Cách trả lời: {config.PERSONA_DEFAULT['answer_style']}",
    ]


def test_a_field_that_is_set_replaces_its_default_and_the_others_keep_theirs():
    config = AppConfig()

    lines = persona_lines(config, {"personality": "Nhí nhảnh", "name": "Mai", "avoid": ""})

    assert lines.splitlines() == [
        "- Tên của trợ lý: Mai",
        f"- Vai trò: {config.PERSONA_DEFAULT['role']}",
        "- Tính cách: Nhí nhảnh",
        f"- Cách trả lời: {config.PERSONA_DEFAULT['answer_style']}",
    ]


def test_the_system_prompt_holds_the_persona_it_is_given_or_the_default_one():
    config = AppConfig()

    assert system_prompt(config) == config.SYSTEM_PROMPT.format(persona=persona_lines(config, {}))
    assert system_prompt(config, "- Tên của trợ lý: Mai") == config.SYSTEM_PROMPT.format(
        persona="- Tên của trợ lý: Mai"
    )
    assert "Thân thiện" in system_prompt(config)
    assert "{" not in system_prompt(config)


def test_braces_in_a_persona_are_plain_text_in_the_system_prompt():
    config = AppConfig()

    assert "- Điều cần tránh: {persona} và {0}" in system_prompt(
        config, persona_lines(config, {"avoid": "{persona} và {0}"})
    )


def test_a_turn_with_an_empty_answer_gives_a_user_message_alone_joined_with_the_next():
    history = (
        ChatTurn("một", ("Một.",)),
        ChatTurn("tôi muốn hỏi", interrupted=True),
        ChatTurn("về lãi suất", interrupted=True),
        ChatTurn("kỳ hạn", ("Sáu tháng.",)),
        ChatTurn("còn", interrupted=True),
    )

    messages = _messages(history, "mười hai tháng")[1:]

    assert messages == [
        ("user", "một"), ("assistant", "Một."),
        ("user", "tôi muốn hỏi\nvề lãi suất\nkỳ hạn"), ("assistant", "Sáu tháng."),
        ("user", "còn\nmười hai tháng"),
    ]
    roles = [role for role, _ in messages]
    assert all(a != b for a, b in zip(roles, roles[1:]))


# ── search_query ──────────────────────────────────────────────────────────

def test_the_search_query_is_the_previous_utterances_then_the_current_one():
    history = (ChatTurn("một", ("Một.",)), ChatTurn("hai"), ChatTurn("ba", ("Ba.",)))

    assert search_query(history, "bốn", 1) == "ba\nbốn"
    assert search_query(history, "bốn", 2) == "hai\nba\nbốn"
    assert search_query(history, "bốn", 9) == "một\nhai\nba\nbốn"


def test_the_search_query_is_the_utterance_alone_without_history_or_with_zero():
    assert search_query((), "bốn", 1) == "bốn"
    assert search_query((ChatTurn("ba"),), "bốn", 0) == "bốn"
