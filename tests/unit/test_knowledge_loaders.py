import pytest

from backend.features.knowledge.loaders import (
    UnreadableDocument,
    UnsupportedDocument,
    extract_text,
)
from tests.unit.knowledge_files import docx_bytes, pdf_bytes


@pytest.mark.parametrize("name", ["note.txt", "NOTE.MD"])
def test_plain_text_is_decoded_as_utf8_without_the_bom(name):
    data = b"\xef\xbb\xbf" + "Lãi suất 5%".encode("utf-8")

    assert extract_text(name, data) == "Lãi suất 5%"


def test_csv_rows_become_lines():
    data = "kỳ hạn,lãi suất\n6 tháng, 5%\n".encode("utf-8")

    assert extract_text("rates.csv", data) == "kỳ hạn, lãi suất\n6 tháng, 5%"


def test_docx_paragraphs_and_tables_are_extracted():
    data = docx_bytes("Quy định nghỉ phép", "Mười hai ngày.", table=[["Cấp", "Ngày"], ["A", "15"]])

    text = extract_text("policy.docx", data)

    assert "Quy định nghỉ phép\n\nMười hai ngày." in text
    assert "Cấp | Ngày" in text and "A | 15" in text


def test_pdf_text_layer_is_extracted():
    assert "Annual leave is twelve days" in extract_text(
        "policy.pdf", pdf_bytes("Annual leave is twelve days")
    )


def test_unsupported_extension_is_rejected():
    with pytest.raises(UnsupportedDocument, match=".exe"):
        extract_text("setup.exe", b"MZ")


@pytest.mark.parametrize("name", ["broken.pdf", "broken.docx"])
def test_a_file_that_cannot_be_parsed_is_unreadable(name):
    with pytest.raises(UnreadableDocument, match="broken"):
        extract_text(name, b"not really that format")
