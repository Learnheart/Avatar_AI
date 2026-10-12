from backend.features.knowledge.chunking import split_text


def test_a_short_text_is_one_chunk_with_its_whitespace_squashed():
    assert split_text("  Nghỉ phép   năm\nlà 12 ngày.  ", size=100, overlap=10) == [
        "Nghỉ phép năm là 12 ngày."
    ]


def test_blank_text_gives_no_chunk():
    assert split_text(" \n\n \t ", size=100, overlap=10) == []


def test_paragraphs_are_packed_up_to_the_size():
    text = "\n\n".join(["a" * 40, "b" * 40, "c" * 40])

    chunks = split_text(text, size=90, overlap=0)

    assert chunks == ["a" * 40 + "\n" + "b" * 40, "c" * 40]


def test_the_end_of_a_chunk_is_repeated_at_the_start_of_the_next():
    text = "\n\n".join(["a" * 40, "b" * 20, "c" * 40])

    chunks = split_text(text, size=70, overlap=25)

    assert chunks == ["a" * 40 + "\n" + "b" * 20, "b" * 20 + "\n" + "c" * 40]


def test_the_overlap_never_pushes_a_chunk_over_the_size():
    text = "\n\n".join(["a" * 30, "b" * 30, "c" * 60])

    chunks = split_text(text, size=70, overlap=40)

    assert all(len(chunk) <= 70 for chunk in chunks)
    assert chunks[-1] == "c" * 60


def test_a_long_paragraph_is_cut_at_sentences():
    paragraph = "Câu một dài vừa phải. Câu hai cũng vậy! Câu ba kết thúc?"

    chunks = split_text(paragraph, size=25, overlap=0)

    assert chunks == ["Câu một dài vừa phải.", "Câu hai cũng vậy!", "Câu ba kết thúc?"]


def test_a_sentence_longer_than_the_size_is_hard_cut():
    chunks = split_text("x" * 250, size=100, overlap=0)

    assert [len(chunk) for chunk in chunks] == [100, 100, 50]
