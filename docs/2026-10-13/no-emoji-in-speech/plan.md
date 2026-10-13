---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: llm, tts, persona
summary: The avatar's answers carry no emoji — the LLM is told not to write any, and whatever it still writes is removed from each sentence before it is spoken, shown or remembered
---

# No emoji in what the avatar says

## Problem statement

- Seen in the real run of the avatar persona (2026-10-13, Databricks LLM): with a playful
  personality the model ends its sentences with emoji ("…chút nhỉ 😊 Anh có kế hoạch gì
  hôm nay chưa?", "…em nhớ rõ nè chị! 💕").
- A sentence goes to the TTS as the model wrote it. An emoji is not speech: the TTS
  reads it as noise, as a name, or skips it, depending on the symbol.

## Requirements

- The LLM is told not to write emoji, in the default character and under any persona.
- Whatever the model still writes, no emoji reaches the TTS. The same sentence, without
  its emoji, is what the transcript window shows and what the conversation remembers.
- Vietnamese letters, their tone marks, digits and ordinary punctuation are untouched.
- A sentence made only of emoji is not spoken at all.

## Decisions made

- **Two layers.** The instruction lowers how often it happens; the filter guarantees
  it. Neither replaces the other: an instruction is a request, and a filter alone would
  leave sentences built around a symbol that is no longer there.
- **The instruction** is one rule of `SYSTEM_PROMPT` (the answer is read aloud: words
  only, no emoji), which holds for every persona. (When this was written the persona
  had a prompt of its own, `PERSONA_PROMPT`, carrying the same rule; it is now part of
  `SYSTEM_PROMPT`, see `../avatar-persona/plan.md`.)
- **The filter is in the conversation engine**, on each completed sentence of the LLM
  answer, before it is queued for the TTS: one place, whatever the LLM backend, and the
  events the orchestrator records and sends already carry the cleaned text. It works on
  whole sentences, so an emoji sequence cut between two deltas is still removed whole.
- **What is removed**: the pictograph blocks (U+1F000–U+1FAFF, with the flags and skin
  tones they hold), the symbol and dingbat blocks (U+2300–U+23FF, U+2600–U+27BF,
  U+2B00–U+2BFF), and what only exists to build emoji (variation selector U+FE0F, zero
  width joiner U+200D, keycap U+20E3, tag characters). A run of them, with the spaces
  around it, becomes one space; a space left before a punctuation mark is closed. It is
  a fixed pattern, not a setting.
- **The raw answer stays on the `llm` span** (`llm.answer`), so the trace still shows
  what the model wrote; the `tts` span shows what was spoken.
- The spoken fallback sentence and the texts of `prompts.py` are ours and are not
  filtered.

## Implementation approach

1. `without_emoji` beside `split_sentences` in `core/conversation/engine.py`, applied
   to the sentences and to the tail of the answer; the two prompt sentences. Unit tests
   in `tests/unit/test_engine.py`:
   - an emoji at the end, in the middle and between two sentences is removed, with the
     spacing and the punctuation left clean;
   - a sequence (skin tone, joiner, flag, keycap) is removed whole, also when it arrives
     cut between two deltas;
   - a sentence of emoji alone is not spoken;
   - Vietnamese text, digits and punctuation are returned as given;
   - the TTS, the audio events and `llm.answer` carry what is described above.
2. Real run on the model with a playful persona: the answers as written by the model
   and as spoken.

## To-do

- [x] The filter and the two prompt sentences with unit tests, `CHANGELOG.md`
- [x] Real run (2026-10-13, Databricks LLM, asked directly of the model): before the
      instruction, two answers out of seven under a persona ended with an emoji ("😊",
      "💕"). With the instruction, six questions under a playful persona ("Vui vẻ, nhí
      nhảnh, hay pha trò") and the same six in the default character: no emoji in any
      of the twelve answers, so the filter had nothing to remove; it is covered by the
      unit tests. Not measured on Groq
- [x] Real turn after the change (2026-10-13, `tests/e2e/test_real_persona.py`, Groq
      answering 503 and the Databricks backup taking over the STT and the LLM): the turn
      under the persona is answered "Dạ, em là Bông ạ! Anh cần em hỗ trợ gì không?",
      end to end 4.8 s; first audio 4.2 s against 3.5 s, the gate that already fails on
      this path (`../avatar-persona/plan.md`)
- [ ] Seen in that run, to decide: asked "Ai đặt tên cho bạn vậy?" in the default
      character, the model names itself and its maker instead of the avatar
- [ ] Human verification
