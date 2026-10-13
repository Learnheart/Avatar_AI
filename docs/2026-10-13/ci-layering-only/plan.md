---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: shared
summary: The backend quality gate stops limiting the size of the code (lines per file, complexity, branches, statements and arguments per function) and keeps what checks where the code lives - the layering - with the lint defaults and the unit tests
---

# Backend quality gate: layering only, no size limits

## Problem

The gate added by `docs/2026-10-12/backend-ci-quality-gate/` limits the size of the code:
400 lines per backend file, and per function a complexity of 10, 12 branches, 50
statements and 7 arguments. The branch `feature/conversation-memory` (50 commits written
before the gate) merged into `main` fails it on 13 lint findings and three files over
400 lines, although its unit tests pass and its layering holds.

The numbers measure length, not whether the code is in the right place. A route factory
or the application wiring is long because it lists many routes or parts, not because it
is misplaced; splitting it to pass a number does not make it better placed.

## Requirements

- The gate no longer fails on the size of a file or of a function.
- It still checks how the code is distributed: the layering of `CLAUDE.md` §4.
- It still catches real mistakes: the `ruff` default rules, and the unit tests.

## Decisions

- **Removed:** the file-size test of `tests/unit/test_code_shape.py` with its limit and
  its exception list, and the four function-size rules of `ruff.toml` (`C901`,
  `PLR0912`, `PLR0913`, `PLR0915`) with their thresholds and the exemption of `tests/`.
- **Kept:** the layering tests (the core imports no provider, runtime or feature;
  perception and conversation do not import the orchestrator) and the test of the
  import scan they rely on; the `ruff` defaults (`E4`, `E7`, `E9`, `F`); the workflow,
  unchanged in what it runs.
- The two `# noqa: C901` comments lose their rule and are removed with their reason.
- The refactors made to pass the limits (the split of the config validation and of the
  capture start) stay: they are behaviour-neutral and nothing gains by undoing them.
- The rules of §4 "Keeping the code small" stay: they are about not writing what is not
  needed, not about a number of lines.
- The earlier plan is not rewritten; this plan supersedes its size limits.

## Implementation approach

On the branch `ci/backend-quality-gate`:

1. `ruff.toml`: the default rules only.
2. `tests/unit/test_code_shape.py`: the size test, its constants and its helper removed.
3. The two `# noqa: C901` comments removed.
4. Wording that names the limits: the workflow header and step name, the comment in
   `backend/requirements-dev.txt`, `CLAUDE.md` / `AGENTS.md` (§4 tree, §5 checks).
5. Verify: `ruff check` and `pytest tests/unit -q` on this branch, and the same two
   commands on a trial merge of `feature/conversation-memory`.

## To-do

- [ ] `ruff.toml` reduced to the default rules
- [ ] File-size test removed from `tests/unit/test_code_shape.py`
- [ ] `# noqa: C901` comments removed
- [ ] Workflow, `requirements-dev.txt`, `CLAUDE.md` / `AGENTS.md` wording
- [ ] `ruff check` and the unit tests pass on the branch
- [ ] Trial merge of `feature/conversation-memory`: result recorded here
- [ ] Human verification
