"""The shapes of the conversation messages the server sends (the wire contract)."""
from backend.core.orchestrator.ws_manager import conversation_message, transcript_message


def test_transcript_message_carries_the_text_heard():
    assert transcript_message("Lãi suất là bao nhiêu?") == {
        "type": "transcript",
        "text": "Lãi suất là bao nhiêu?",
    }


def test_conversation_message_carries_the_id_of_the_open_conversation():
    assert conversation_message("c-1") == {"type": "conversation", "conversation_id": "c-1"}


def test_conversation_message_carries_null_when_none_is_open():
    assert conversation_message(None) == {"type": "conversation", "conversation_id": None}
