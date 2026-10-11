"""Features built beside the core turn pipeline (knowledge / RAG, memory, ...).

One package per feature. A feature reaches the turn through a contract in
`backend/core/contracts.py` and is wired in `backend/main.py`; the core never imports
this package.
"""
