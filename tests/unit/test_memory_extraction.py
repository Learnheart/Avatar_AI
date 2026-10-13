"""What the LLM answered to an extraction request → validated items or operations. Pure."""
import json

import pytest

from backend.features.memory.extraction import (
    ExtractedItem,
    InvalidExtraction,
    Operation,
    parse_items,
    parse_operations,
)

NAME = {"category": "identity", "text": "Tôi tên là Linh.", "evidence": "Mình là Linh"}
TEA = {"category": "preference", "text": "Tôi thích trà.", "evidence": "thích uống trà"}


def _clean(text: str) -> str:
    """The rule of the tests: trimmed, not empty, at most 20 characters."""
    text = text.strip()
    if not text or len(text) > 20:
        raise ValueError("not an item text")
    return text


def _parse(answer) -> list[ExtractedItem]:
    return parse_items(answer if isinstance(answer, str) else json.dumps(answer), _clean)


def test_valid_json_gives_the_items_in_the_order_of_the_answer():
    assert _parse([NAME, TEA]) == [
        ExtractedItem("identity", "Tôi tên là Linh.", "Mình là Linh"),
        ExtractedItem("preference", "Tôi thích trà.", "thích uống trà"),
    ]


def test_vietnamese_written_as_is_or_escaped_is_read_the_same():
    plain = json.dumps([NAME], ensure_ascii=False)
    escaped = json.dumps([NAME], ensure_ascii=True)

    assert parse_items(plain, _clean) == parse_items(escaped, _clean) == _parse([NAME])


def test_whitespace_around_the_array_is_not_prose():
    assert _parse(f"\n  {json.dumps([NAME])}\n") == _parse([NAME])


def test_an_empty_list_is_accepted_and_gives_no_item():
    assert _parse("[]") == []


@pytest.mark.parametrize(
    "entry",
    [
        {**NAME, "category": "hobby"},        # unknown category
        {**NAME, "category": None},
        {**NAME, "category": ["identity"]},
        {"text": "Tôi tên là Linh."},          # no category
        {"category": "identity"},             # missing text
        {**NAME, "text": ""},
        {**NAME, "text": "   "},
        {**NAME, "text": None},
        {**NAME, "text": 42},
        {**NAME, "text": ["Tôi tên là Linh."]},
        {**NAME, "text": "x" * 21},           # does not pass the text rule
        "Tôi tên là Linh.",                   # not an object
        ["identity", "Tôi tên là Linh."],
        None,
        7,
    ],
)
def test_an_entry_that_cannot_be_an_item_is_dropped_and_the_others_kept(entry):
    assert _parse([NAME, entry, TEA]) == _parse([NAME, TEA])
    assert _parse([entry]) == []


def test_the_text_is_stored_as_the_text_rule_returns_it():
    assert _parse([{**NAME, "text": "  Tôi tên là Linh.  "}])[0].text == "Tôi tên là Linh."


@pytest.mark.parametrize("evidence", [None, "", "   ", 3, ["Mình là Linh"]])
def test_an_item_without_a_usable_quote_is_kept_without_evidence(evidence):
    assert _parse([{**NAME, "evidence": evidence}]) == [
        ExtractedItem("identity", "Tôi tên là Linh.", None)
    ]
    assert _parse([{"category": "identity", "text": "Tôi tên là Linh."}])[0].evidence is None


def test_fields_that_were_not_asked_for_are_ignored():
    (item,) = _parse([{**NAME, "status": "pending", "user_id": "an", "id": "x"}])

    assert item == ExtractedItem("identity", "Tôi tên là Linh.", "Mình là Linh")


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "   ",
        "Không có thông tin nào.",
        f"Đây là kết quả:\n{json.dumps([NAME])}",                 # prose before
        f"{json.dumps([NAME])}\nHy vọng giúp được bạn.",          # prose after
        f"```json\n{json.dumps([NAME])}\n```",                    # a code fence
        f"<think>Người dùng tên Linh.</think>\n{json.dumps([NAME])}",
        '[{"category": "identity", "text": "Tôi tên là Linh."',   # cut short
        "[{'category': 'identity', 'text': 'Tôi tên là Linh.'}]",  # not JSON quotes
    ],
)
def test_invalid_json_or_prose_around_the_json_gives_nothing(answer):
    with pytest.raises(InvalidExtraction):
        parse_items(answer, _clean)


@pytest.mark.parametrize(
    "answer", [NAME, {"items": [NAME]}, "Tôi tên là Linh.", 12, None, True]
)
def test_json_that_is_not_an_array_gives_nothing(answer):
    with pytest.raises(InvalidExtraction):
        parse_items(json.dumps(answer), _clean)


# ── Operations: what a conversation does to the items ─────────────────────

KNOWN = ("1", "2")
ADD = {"op": "add", **TEA}
CONFIRM = {"op": "confirm", "id": "1"}
REPLACE = {"op": "replace", "id": "2", **NAME}


def _operations(answer) -> list[Operation]:
    answer = answer if isinstance(answer, str) else json.dumps(answer)
    return parse_operations(answer, _clean, KNOWN)


def test_valid_json_gives_the_operations_in_the_order_of_the_answer():
    assert _operations([ADD, CONFIRM, REPLACE]) == [
        Operation("add", None, ExtractedItem("preference", "Tôi thích trà.", "thích uống trà")),
        Operation("confirm", "1"),
        Operation("replace", "2", ExtractedItem("identity", "Tôi tên là Linh.", "Mình là Linh")),
    ]


def test_an_id_may_be_answered_as_a_number():
    assert _operations([{"op": "confirm", "id": 2}]) == [Operation("confirm", "2")]


@pytest.mark.parametrize(
    "dropped",
    [
        {"op": "add", **TEA, "category": "hobby"},  # unknown category
        {"op": "add", "category": "preference"},  # no text
        {"op": "add", "category": "preference", "text": "   "},
        {"op": "add", "category": "preference", "text": "x" * 21},  # no valid item text
        {"op": "replace", "id": "1", "category": "identity"},  # no text
        {"op": "replace", "id": "9", **NAME},  # not an item of this user
        {"op": "replace", **NAME},  # no id
        {"op": "confirm", "id": "9"},
        {"op": "confirm", "id": None},
        {"op": "confirm", "id": True},
        {"op": "confirm", "id": ["1"]},
        {"op": "confirm"},
        {"op": "delete", "id": "1"},  # no such operation
        TEA,  # no operation at all
        "confirm 1",
        None,
    ],
)
def test_an_operation_that_cannot_be_one_is_dropped_and_the_others_kept(dropped):
    assert _operations([ADD, dropped, CONFIRM]) == _operations([ADD, CONFIRM])


def test_an_add_ignores_an_id_and_a_confirm_ignores_an_item():
    assert _operations([{**ADD, "id": "9"}, {**CONFIRM, **TEA}]) == _operations([ADD, CONFIRM])


def test_an_empty_list_of_operations_is_accepted():
    assert _operations("[]") == []


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "Không có gì để ghi nhớ.",
        f"```json\n{json.dumps([ADD])}\n```",
        f"Đây là kết quả: {json.dumps([ADD])}",
        json.dumps({"operations": [ADD]}),
        json.dumps(ADD),
        "[" + json.dumps(ADD),
    ],
)
def test_an_answer_that_is_not_the_array_gives_no_operation(answer):
    with pytest.raises(InvalidExtraction):
        _operations(answer)
