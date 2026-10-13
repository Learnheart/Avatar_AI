---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: orchestrator, llm
summary: The earlier turns given to the LLM are bounded by an estimated number of tokens instead of a number of turns, so long turns cannot outgrow the budget and short ones are not dropped early
---

# A token window for the conversation history

## Problem statement

- The short-term memory keeps the last `MEMORY_HISTORY_MAX_TURNS` (10) turns of the
  conversation, whatever their length. Ten long turns can cost several times what ten
  short ones do, so the size of a request is not bounded by the setting that is meant
  to bound it; and ten one-word turns are dropped as early as ten long ones although
  they cost almost nothing.

## Requirements

- The history handed to the LLM never exceeds a budget expressed in tokens.
- The window slides: when a new turn does not fit, the oldest turns are dropped, whole,
  until it does. A turn is never cut in the middle.
- Short turns stay longer than long ones: there is no cap on the number of turns.
- No new dependency, and nothing tied to the tokenizer of one provider: the same build
  runs on Groq, on Databricks and on any OpenAI-compatible backend.

## Decisions made

- **The budget is `MEMORY_HISTORY_MAX_TOKENS`** (default 2000) and replaces
  `MEMORY_HISTORY_MAX_TURNS`, which is removed: two limits on the same thing would
  bring back the early drop of short turns. The removed key was never in a release.
- **Tokens are estimated from characters**, not counted by a tokenizer: each backend
  has its own, and none is installed. `MEMORY_HISTORY_CHARS_PER_TOKEN` (default 3.0) is
  how many characters one token holds. Measured on 2026-10-13 on the Databricks model
  with spoken Vietnamese (utterances and answers, about 1000 characters each): 3.3
  characters per token. The default is below the measurement on purpose, so the
  estimate errs on the side of counting too many tokens.
- **A message costs a few tokens beyond its text** (its role and separators), which
  matters when turns are one word long: 4 tokens are counted per message, a constant
  of the code. A turn is one message when nothing was answered, two otherwise.
- **The budget covers the history only.** The rest of a request is bounded by its own
  keys: the persona (`PERSONA_FIELD_MAX_CHARS` per field), the profile
  (`MEMORY_PROMPT_MAX_ITEMS` × `MEMORY_ITEM_MAX_CHARS`), the passages
  (`KNOWLEDGE_TOP_K` × `KNOWLEDGE_CHUNK_CHARS`) and the answer (`LLM_MAX_TOKENS`).
- **A turn larger than the whole budget is not kept, and evicts nothing**: the turns
  already kept stay. The guarantee is that the history fits, and one spoken utterance
  with its two or three sentences of answer is far below the default budget.
- **The conversation owns the window**, as it owned the cap: `Conversation.append`
  drops the oldest turns, the engine and the backends still read what they are handed.
  What is stored for the long-term memory (`turns` table) is not concerned: every
  turn is recorded, and the reading has its own limit
  (`MEMORY_CONSOLIDATE_MAX_CHARS`).

## Implementation approach

One slice: the estimate and the window in `core/conversation/history.py`, the two keys
in `tuning.py` with their validation, the orchestrator opening the conversation with
them. Unit tests:

- `test_history.py`: the estimate of a text and of a turn (answered or not); turns are
  kept while they fit and the oldest are dropped, whole, when one more does not; many
  short turns stay where a few long ones are dropped; a turn over the budget leaves the
  history empty; the order is kept.
- `test_orchestrator.py`: the history given to the engine follows
  `MEMORY_HISTORY_MAX_TOKENS`.
- `test_config.py`: the defaults; a budget under 1, a ratio that is not positive or
  not finite → refused.

## To-do

- [x] The token window with unit tests, `.env.example`, `CHANGELOG.md`,
      `docs/ARCHITECTURE.md`
- [x] Real turn after the change (2026-10-13, `tests/e2e/test_real_persona.py`, Groq
      answering 503, the Databricks backup taking over): passed, first audio 3.0 s, end
      to end 3.8 s. The two turns of that test are far below the budget: the window
      itself is covered by the unit tests, and no real conversation long enough to slide
      has been run
- [x] Real turns on Groq after the change (2026-10-13, the whole `tests/e2e` suite, 8
      passed)
- [ ] To decide: the ratio was measured on the Databricks model only; on the Groq
      model it is assumed
- [ ] Human verification
