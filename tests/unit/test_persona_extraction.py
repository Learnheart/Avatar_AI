"""The answer of the LLM that reads a description of the avatar → persona fields."""
import json

import pytest

from backend.features.persona.extraction import InvalidPersonaAnswer, parse_fields


def _answer(fields) -> str:
    return json.dumps(fields, ensure_ascii=False)


def test_the_known_fields_are_kept_in_the_order_of_the_fields():
    answer = _answer({"answer_style": "Ngắn gọn", "name": "Mai", "addressing": "xưng em"})

    fields = parse_fields(answer, 300)

    assert fields == {"name": "Mai", "addressing": "xưng em", "answer_style": "Ngắn gọn"}
    assert list(fields) == ["name", "addressing", "answer_style"]


def test_a_value_is_put_on_one_line():
    assert parse_fields(_answer({"personality": " Vui vẻ,\n hay đùa "}), 300) == {
        "personality": "Vui vẻ, hay đùa"
    }


@pytest.mark.parametrize(
    "dropped",
    [
        {"voice": "trầm"},
        {"role": 7},
        {"role": None},
        {"role": ["gia sư"]},
        {"role": "  \n "},
        {"role": "x" * 301},
    ],
)
def test_an_entry_that_is_no_field_is_dropped_and_the_others_are_kept(dropped):
    assert parse_fields(_answer({"name": "Mai", **dropped}), 300) == {"name": "Mai"}


def test_a_value_as_long_as_the_limit_is_kept():
    assert parse_fields(_answer({"name": "x" * 300}), 300) == {"name": "x" * 300}


def test_an_empty_object_gives_no_field():
    assert parse_fields("{}", 300) == {}


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "Tên của trợ lý là Mai.",
        '```json\n{"name": "Mai"}\n```',
        'Kết quả: {"name": "Mai"}',
        '[{"name": "Mai"}]',
        '"Mai"',
        "null",
    ],
)
def test_an_answer_that_is_not_a_json_object_is_invalid(answer):
    with pytest.raises(InvalidPersonaAnswer):
        parse_fields(answer, 300)
