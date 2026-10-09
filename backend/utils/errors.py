"""Short, single-line descriptions of exceptions for domain error messages and logs."""


def summarize_error(error: BaseException, max_chars: int) -> str:
    """`Type (status N): first max_chars characters`, on one line.

    Provider errors can carry a whole HTML page; the full text stays reachable through
    exception chaining (`raise ... from error`), not through the message.
    """
    summary = type(error).__name__
    status = getattr(error, "status_code", None)
    if status is not None:
        summary += f" (status {status})"
    text = " ".join(str(error).split())[:max_chars]
    return f"{summary}: {text}" if text else summary
