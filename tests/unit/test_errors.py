from backend.utils.errors import summarize_error


class StatusError(Exception):
    status_code = 403


def test_summary_names_the_type_and_keeps_a_short_message():
    assert summarize_error(RuntimeError("onnx failed"), 200) == "RuntimeError: onnx failed"


def test_summary_includes_the_status_code_when_the_error_has_one():
    assert summarize_error(StatusError("blocked"), 200) == "StatusError (status 403): blocked"


def test_long_multiline_body_is_cut_to_one_short_line():
    body = "<html>\n<body>\n" + "<p>blocked by proxy</p>\n" * 1000 + "</body></html>"

    summary = summarize_error(StatusError(body), 200)

    assert "\n" not in summary
    assert summary.startswith("StatusError (status 403): <html> <body> <p>blocked")
    assert len(summary) <= len("StatusError (status 403): ") + 200


def test_error_without_a_message_is_just_its_type():
    assert summarize_error(TimeoutError(), 200) == "TimeoutError"
