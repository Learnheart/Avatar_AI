---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: shared
summary: Tests for the backend CI quality gate — shape tests, unchanged config validation, the first CI run
---

# Test plan — backend CI quality gate

## Unit tests (sandbox, `tests/unit/`)

| Area | File | Cases |
|---|---|---|
| Layering | `test_code_shape.py` | no file under `backend/core/` imports `backend.providers`, `backend.runtime` or `backend.features`; no file under `core/perception/` or `core/conversation/` imports `backend.core.orchestrator`; both `import x` and `from x import y` forms are seen |
| File size | `test_code_shape.py` | every backend source file is within the limit, except the listed exception; a listed exception that no longer exists or is back under the limit fails the test |
| Import scan | `test_code_shape.py` | the helper that reads imports is checked on a small source string holding an allowed and a forbidden import, so a broken scan cannot pass silently; relative imports are resolved to absolute names |
| Config | `test_config.py` (existing) | unchanged and still passing after `validate` is split: same errors, same messages, same order of the first failing check |

## Lint

- `ruff check` reports nothing on `backend/` and `tests/`.
- A scratch function with complexity 11 placed under `backend/` is reported, then
  removed: the complexity rule is active, not only configured.

## CI (real environment, GitHub Actions)

No end-to-end test of the avatar runs in CI, and none is needed: the change does not
touch the behaviour of a turn.

| Check | Expected |
|---|---|
| First run on the pushed branch | install, `ruff check` and `pytest tests/unit -q` pass on a clean runner, with no secret set |
| Second run | the dependency cache is restored; the install time of both runs is reported |
| A commit with a failing unit test, on a scratch branch | the check fails on that commit; the branch is then deleted |
