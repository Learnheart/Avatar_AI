"""What the LLM answered to an extraction request → validated items. Pure."""
import json

import pytest

from backend.features.memory.extraction import ExtractedItem, InvalidExtraction, parse_items

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
