"""The conversation, the record of a running turn and the chat messages made of them."""
from backend.core.conversation.history import (
    Conversation,
    TurnRecord,
    chat_messages,
    search_query,
)
from backend.models import ChatTurn

SYSTEM_PROMPT = "Bạn là trợ lý."


def _messages(history, user_text, **kwargs):
    return chat_messages(history, user_text, system_prompt=SYSTEM_PROMPT, **kwargs)


# ── Conversation ──────────────────────────────────────────────────────────

def test_a_conversation_carries_its_id_and_its_user():
    assert Conversation("c1", "user-7", max_turns=3).user_id == "user-7"
    guest = Conversation("c2", None, max_turns=3)

    assert (guest.id, guest.user_id, guest.history) == ("c2", None, ())


def test_append_keeps_the_order():
    conversation = Conversation("c1", None, max_turns=3)

    conversation.append(ChatTurn("một", ("Một.",)))
    conversation.append(ChatTurn("hai", ("Hai.",)))

    assert [turn.user_text for turn in conversation.history] == ["một", "hai"]


def test_past_the_cap_the_oldest_turn_is_dropped():
    conversation = Conversation("c1", None, max_turns=2)

    for text in ("một", "hai", "ba"):
        conversation.append(ChatTurn(text))

    assert [turn.user_text for turn in conversation.history] == ["hai", "ba"]


def test_a_turn_holds_its_sentences_and_its_interrupted_flag():
    turn = ChatTurn("xin chào", ("Chào bạn.", "Bạn cần gì?"), interrupted=True, confidence=0.9)

    assert turn.sentences == ("Chào bạn.", "Bạn cần gì?")
    assert turn.answer == "Chào bạn. Bạn cần gì?"
    assert turn.interrupted and turn.confidence == 0.9


def test_a_turn_with_an_empty_answer_is_kept():
    conversation = Conversation("c1", None, max_turns=2)

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


def test_the_persona_is_a_system_message_before_the_profile():
    messages = _messages((), "hai", profile="Tên: Linh", persona="Tên trợ lý: Mai")

    assert messages == [
        ("system", SYSTEM_PROMPT),
        ("system", "Tên trợ lý: Mai"),
        ("system", "Tên: Linh"),
        ("user", "hai"),
    ]


def test_the_persona_alone_is_the_second_system_message():
    messages = _messages((), "hai", persona="Tên trợ lý: Mai")

    assert messages == [("system", SYSTEM_PROMPT), ("system", "Tên trợ lý: Mai"), ("user", "hai")]


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
