---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: shared
summary: A GitHub Actions check for the backend (unit tests, lint, complexity, layering, file size) and the rules that keep generated code small and readable
---

# Backend CI quality gate

## Problem statement

- §6 requires the unit tests to pass before a change is done, but nothing enforces it:
  the repository has no CI workflow, no pre-commit hook and no lint configuration. The
  rule holds only as long as whoever commits remembers to run the tests.
- Most of the code is written by coding agents. Unit tests check behaviour; they say
  nothing about a function that grew too long, a wrapper layer nobody needed, or an
  import that crosses a layer of §4.
- The layering rules of §4 are respected today, but only by convention. Nothing would
  flag the first `core/` module that imports a provider.

Measured on `main` on 2026-10-13 (backend, `ruff` 0.15, 381 unit tests passing):

| Check | Result |
|---|---|
| `ruff` default rules, `backend/` + `tests/` | 1 finding: `E741` in `tests/unit/test_telemetry.py:39` |
| `core/` importing `providers/`, `runtime/`, `features/` | none |
| `perception/`, `conversation/` importing `orchestrator/` | none |
| Complexity over 10 (`C901`) | `config.py` `validate` (17), `knowledge/api.py` `create_knowledge_router` (12), `tts/zerotts.py` `synthesize_stream` (12) |
| Branches over 12 (`PLR0912`) | `config.py` `validate` (16) |
| Statements over 50 (`PLR0915`) | `main.py` `create_app` (51) |
| Arguments over 5 (`PLR0913`) | `orchestrator.py` constructor (7), `tracking.py:48` (6) |
| Files over 400 lines | `core/orchestrator/orchestrator.py` (550); next is `conversation/engine.py` (290) |

## Requirements

- Every push, to any branch, runs the backend checks on a clean machine and shows a
  pass / fail status on the commit (and so on its pull request).
- The checks are: backend unit tests, lint, function complexity, the layering rules of
  §4, and a file-size limit.
- The same checks run locally with the commands of §5, with no extra setup.
- The checks need no secret, no network access to a model provider, no camera.
- Coding agents are told, in `CLAUDE.md` / `AGENTS.md`, not to add layers or code beyond
  what the plan asks for.
- The change does not alter the behaviour of the avatar.

Out of scope: the frontend (no CI job, no ESLint), end-to-end tests in CI (they cost
Groq calls and load ZeroTTS), deployment (CD), type checking, coverage thresholds,
duplicate-code detection.

## Decisions made

- **GitHub Actions, one workflow, one job.** The remote is on GitHub. The job installs
  the dependencies as §5 does (`uv`, Python 3.12), then runs `ruff check` and
  `pytest tests/unit -q`. One job keeps the install (the slow part) to one run. The runner is a named
  image (`ubuntu-24.04`), not `ubuntu-latest`, so it changes only when the workflow does. The
  workflow carries three settings: read-only permissions on the repository, one run per
  branch at a time (a newer push cancels the run before it, except on `main`, where a
  run is never cancelled), and a 30-minute timeout.
- **The workflow runs on every push, to any branch.** A branch is checked before it has
  a pull request, the pull request shows the status of its last commit, and nothing runs
  twice. Pull requests from forks are not checked; the repository takes none today.
- **`ruff` is the only new tool**, added to `backend/requirements-dev.txt` and configured
  in `ruff.toml` at the repository root. Rules: the `ruff` defaults (`E4`, `E7`, `E9`,
  `F`) plus `C901`, `PLR0912`, `PLR0913`, `PLR0915`. Style rules stay off.
- **Thresholds are the `ruff` defaults** (complexity 10, branches 12, statements 50),
  with one change: `max-args = 7`. Constructors receive their dependencies as arguments,
  which is how §4 keeps backends swappable; the orchestrator takes 7 today.
- **The complexity rules apply to `backend/` only.** `tests/` gets the default rules.
- **`create_app` in `main.py` goes from 51 to 50 statements or fewer** by moving the
  start of the capture store into one helper beside `build_orchestrator`.
- **Two functions keep an inline `# noqa: C901` with its reason**:
  `create_knowledge_router` and `synthesize_stream`. Both are at 12 because `ruff` adds
  the branches of their nested functions to the outer one; each reads top to bottom and
  splitting them would add indirection, not clarity. `synthesize_stream` is also on the
  speech path, where a refactor would need a real turn (§6.4). New code gets no `noqa`
  without a reason in the plan that introduces it.
- **`validate` in `config.py` is split by concern** (model backends, embedding, tunable
  values) into private methods it calls in order. It is a flat list of independent
  checks, already covered by `tests/unit/test_config.py`; the messages do not change.
- **Layering and file size are unit tests, not a second tool.** A new
  `tests/unit/test_code_shape.py` reads the imports of each backend file with `ast`,
  the way `test_docs.py` checks the docs. Relative imports are resolved to absolute
  names from the package of the file, so they are checked like the others. `import-linter` was considered and set aside:
  `backend/` has no `__init__.py` (a namespace package), which its import graph handles
  poorly, and a 40-line test is easier for a developer to read than a second config.
- **File-size limit: 400 lines per backend source file**, with `orchestrator.py` listed
  as the one exception in the test. Whether to split the orchestrator is a separate
  decision with its own plan; the exception is removed then.
- **No package index override in CI.** §5 forbids it in commands and project files, so
  `torch` installs from the default index and the workflow relies on the `uv` cache. If
  the first run is too slow, a CPU-only index for CI is a decision for a human.
- **Rules for coding agents go in §4 and §6** (see below); both files change together,
  as `test_docs.py` requires.
- **Branch protection is set by a human** on GitHub, after the workflow has passed once.

## Implementation approach

Done on its own branch from `main` (`ci/backend-quality-gate`). `main` has no
`CHANGELOG.md` yet (it arrives with `feature/conversation-memory`), so this change adds
no changelog entry.

`feature/conversation-memory` is not part of this change and does not pass the gate as
it stands (measured 2026-10-13: `validate` at complexity 33, `create_memory_router` at
21, `create_app` at 77 statements, the orchestrator constructor at 8 arguments,
`memory/store.py` at 506 lines, `memory/consolidation.py` at 440). It is brought under
the thresholds when it is merged with `main`, in its own plan.

1. **Lint configuration.** `ruff.toml` with the rules and thresholds above;
   `ruff` pinned in `backend/requirements-dev.txt`.
2. **Bring the code under the thresholds**, without changing behaviour:
   - rename the `l` variable in `tests/unit/test_telemetry.py`;
   - split `AppConfig.validate`;
   - move the start of the capture store out of `create_app` into a helper;
   - add the two `# noqa: C901` comments with their reason.
3. **Shape tests.** `tests/unit/test_code_shape.py`:
   - no file under `backend/core/` imports `backend.providers`, `backend.runtime` or
     `backend.features`;
   - no file under `backend/core/perception/` or `backend/core/conversation/` imports
     `backend.core.orchestrator`;
   - no backend source file is longer than the limit, except the listed exception, and
     the exception still exists and is still over the limit (so it cannot go stale).
4. **Workflow.** `.github/workflows/backend-ci.yml`: on `push`; checkout, `uv` with cache, Python 3.12, install both requirements files,
   `ruff check`, `pytest tests/unit -q`.
5. **Docs.** In `CLAUDE.md` and `AGENTS.md`:
   - §4, new rules:
     - no interface, base class or factory for a single implementation;
     - a new file or class is named in the plan before it is written;
     - code that a change replaces is deleted in the same change;
     - no parameter, config key or branch for a case no requirement asks for;
   - §5 Checks: add `ruff check`;
   - §6 item 2: lint passes; a `# noqa` carries its reason;
   - §4 source tree: `ruff.toml`, `.github/workflows/`.
6. **First run.** Push the branch, read the first run. A unit test that depends on the developer's machine (`.env`, the Hugging
   Face cache, a local file) is fixed in the test, not skipped.

## To-do

- [x] Create the branch `ci/backend-quality-gate` from `main`
- [x] Add `ruff.toml` and pin `ruff` in `backend/requirements-dev.txt`
- [x] Rename the ambiguous variable in `tests/unit/test_telemetry.py`
- [x] Split `AppConfig.validate` by concern; `tests/unit/test_config.py` still passes
- [x] Bring `create_app` in `backend/main.py` to 50 statements or fewer
- [x] Add `# noqa: C901` with its reason to `create_knowledge_router` and `synthesize_stream`
- [x] `ruff check` reports nothing
- [x] Add `tests/unit/test_code_shape.py` (layering, file size)
- [x] Add `.github/workflows/backend-ci.yml`
- [x] Update §4, §5, §6 in `CLAUDE.md` and `AGENTS.md` (identical text)
- [x] `pytest tests/unit -q` passes locally
- [x] Push the branch; the first CI run passes
- [x] A scratch branch with a failing unit test fails the check, then is deleted
- [ ] Merge into `main`; the run on `main` passes
- [ ] Human: enable branch protection on `main` requiring the backend check
