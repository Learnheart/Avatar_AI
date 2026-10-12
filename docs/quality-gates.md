---
author: linhbk3
date: 2026-10-08
status: todo
agents: vision, vad, stt, llm, tts, orchestrator, expression
summary: Per-module, integration, and pipeline quality gates for the modular Avatar AI pipeline.
---

# Quality Gates — Avatar AI Modular Pipeline

Gates that decide when a module is "good enough" to wire into the pipeline. A gate
tests the module's **contract** (input → output + constraints), not its internal code,
so it stays valid when the implementation changes and applies equally to every backend
behind the Device Capability Router (e.g. an STT gate must pass on both the Metal and
the CUDA backend).

## How to read a gate

Each module gate has:

- **Contract** — what goes in, what comes out, and the invariants on the output.
- **Functional** — does it do the right thing? (unit tests, sandbox, automated)
- **Performance** — is it inside the latency / resource budget? (measured, numeric)
- **Resilience** — does it fail safely when inputs are bad or a dependency breaks?

Three levels of verification (per AGENTS.md §2 — unit in sandbox, e2e costs money):

| Level | What it checks | Cost | How often |
|---|---|---|---|
| **Module gate** | One module in isolation, neighbors mocked | Cheap, automated | Every change |
| **Integration gate** | Two/three modules — their contracts line up | Medium | On wiring changes |
| **Pipeline gate** | One real end-to-end turn | Expensive (cloud $) | Before a milestone |

**The gate that matters most is the contract match between modules**, not "the module
works alone." Most pipeline breakage is a format mismatch at a boundary (STT returns a
shape the LLM step didn't expect; TTS returns audio the frontend can't play).

Latency budget referenced below comes from `ARCHITECTURE.md`: **end-to-end < 10s**,
**first audio < ~3.5s**.

---

## 1. Module Gates

### 1.1 Vision (presence + head orientation)

**Contract:** camera frame in → `{present: bool, head_orientation: {pitch, yaw, roll}}`
or "no face". Runs continuously, off the speech critical path.

| Type | Gate criteria |
|---|---|
| Functional | Detects a present face in a known test image; returns "no face" on an empty frame; orientation values within documented clamp range; picks the largest face when several are present |
| Performance | Sustains **≥ 30 FPS** on the dev machine (M5); per-frame processing does not block the async loop; vision never adds latency to the speech path (parallel plane) |
| Resilience | Camera disconnect → emits "no face" / last-known, retries, does not crash; corrupt/black frame handled; after N seconds no face → defined idle behavior |

### 1.2 VAD (speech start/stop) — golden-dataset gated

**Contract:** audio chunk in → `speech_start` / `speech_end` / none.

| Type | Gate criteria |
|---|---|
| Functional | On the **VAD golden dataset** (simple binary: human voice vs background noise), **recall ≥ 95%** on `speech` clips (does not miss real voice) and **false-trigger rate ≤ 5%** on `noise` clips (does not fire on background noise) — thresholds confirmed once the set exists |
| Performance | Per-chunk processing **< 10ms**; end-of-speech decision latency within budget (the "silence confirmation" window is explicit and configurable) |
| Resilience | Noise-only stream never fires; truncated/empty chunk ignored, no crash; threshold is config-driven, not hardcoded |

> See §4.3 for the VAD dataset shape. It is deliberately a simple speech-vs-noise
> classification set — no timeline/segment labels — matching the MVP need.

### 1.3 STT (speech → Vietnamese text) — golden-dataset gated

**Contract:** utterance audio in → Vietnamese text out, **one fixed output shape**
regardless of backend (Metal / CUDA / CPU).

| Type | Gate criteria |
|---|---|
| Functional | On the **STT golden dataset**, **WER ≤ 15%** (target; threshold confirmed once the set exists). Output shape identical across all backends — same fields, same encoding |
| Performance | For a short utterance (~3–5s), transcription **< ~1.5s** on the dev machine; model loaded once at startup and warmed (first-call cold start excluded from the per-request budget) |
| Resilience | Empty/silence audio → empty text, not an error; very long audio handled or safely rejected; unsupported sample rate rejected with a clear error; **every backend must pass this same gate** |

> WER is measured per the method defined in §4 (STT golden dataset). Each router
> backend is gated independently against the same dataset and the same threshold.

### 1.4 LLM (text → Vietnamese answer) — golden-dataset gated

**Contract:** user text (+ fixed system prompt) in → streamed Vietnamese answer text
out. MVP: no memory, no tools, no knowledge retrieval.

| Type | Gate criteria |
|---|---|
| Functional | On the **LLM golden dataset**, each response satisfies the **form constraints**: Vietnamese, in-persona (financial advisor), length ≤ 2–3 sentences, non-empty, no tool-call/markup leakage. Pass rate **≥ 95%** of golden cases (threshold confirmed once the set exists) |
| Performance | **First token < ~1.5s**; response streams incrementally (not delivered as one final blob) so TTS can start early |
| Resilience | Provider error / timeout → defined fallback (graceful message, no crash); empty or non-Vietnamese model output caught by the form check; network failure surfaces as a recoverable error |

> MVP gates LLM on **form, not content quality** — content scoring is subjective and
> out of MVP scope. The golden dataset fixes inputs + expected form so the gate is
> deterministic. Content-quality scoring is a backlog item.

### 1.5 TTS (Vietnamese text → audio)

**Contract:** answer text in → audio chunks out, in a format the frontend can decode
and play, streamable sentence-by-sentence.

| Type | Gate criteria |
|---|---|
| Functional | Produces audio for a known Vietnamese sentence; output format matches exactly what the frontend's audio player expects (sample rate, encoding, container); supports streaming per sentence |
| Performance | First audio chunk for a short sentence **< ~1s**; streaming so playback can begin before the full answer is synthesized |
| Resilience | Provider error → defined fallback (alternate provider or graceful skip), no crash; empty text → no audio, no error; swapping provider via config does not change the output contract |

### 1.6 Orchestrator (turn state + routing)

**Contract:** owns the turn state machine (idle → listening → thinking → speaking →
idle); routes data between modules; enforces timing. The **only** owner of state.

| Type | Gate criteria |
|---|---|
| Functional | State transitions follow the defined machine; VAD `speech_end` triggers the STT→LLM→TTS flow; barge-in (speech during speaking) interrupts correctly; no module mutates state directly |
| Performance | Routing overhead negligible vs module budgets; a GPU/blocking call never stalls the async event loop (inference runs off the loop) |
| Resilience | Any module error becomes a recoverable signal, not a crash; session timeout handled; a stuck module cannot wedge the state machine (timeout → safe state) |

### 1.7 Expression (frontend: avatar + lip-sync)

**Contract:** receives head-orientation updates and speech-audio chunks → avatar reacts
to those parameters: head motion from rotation values, mouth motion from audio
loudness, plus idle animation.

**MVP scope of this gate:** confirm only that **the avatar moves correctly according to
the parameters it receives** — i.e. the parameter-in → motion-out mapping is right.
Perceptual quality (smoothness, naturalness, how well lip-sync "feels" matched) is NOT
gated here; it is left to visual review.

| Type | Gate criteria |
|---|---|
| Functional (parameter → motion) | A given head_rotation moves the head in the **correct direction and axis** (no mirror/sign flip), and the applied rotation stays **within the clamp range** (no "broken neck"); a given audio loudness drives the mouth value **proportionally** (louder → more open); when audio is silent/ended the mouth returns to 0; an `interrupt` stops motion |
| Performance | Renders without stalling on the dev machine; motion updates track the incoming parameter stream (no accumulating lag) |
| Resilience | Missing avatar blendshape → skip that feature + warn, no crash; audio decode error → skip chunk; WebSocket drop → reconnect; interrupt stops playback immediately and clears the queue |

> **Architecture note for testability:** the motion mapping (loudness → mouth value with
> clamp/lerp; rotation values → bone rotation with clamp) should be a pure function that
> returns the numeric motion value, separate from the render call. This lets the gate
> assert "parameter X in → motion value Y out" without running a real WebGL render.
> Perceptual quality (smoothness, phase sync, naturalness) is deferred to visual review,
> not an automated gate.

### 1.8 Knowledge (uploaded documents → passages for a question)

**Contract:** a file in → an indexed document (or a defined refusal); a question in →
the most relevant passages out, best first, each with its source and score.

| Type | Gate criteria |
|---|---|
| Functional | An uploaded txt / md / csv / pdf / docx is listed with its chunk count; a question about a fact of an uploaded document returns the passage holding it within the top `KNOWLEDGE_TOP_K`; a deleted document, or one the user has not selected, is never returned; the index and the selection survive a restart |
| Performance | Lookup in a turn **< 1 s**, and never longer than `KNOWLEDGE_TIMEOUT_SEC`; an empty base, or one with no document selected, costs no embedding call |
| Resilience | Unsupported, unreadable, empty or oversized file → refused with a reason, nothing stored; embedding failure on upload → nothing stored; lookup failure or timeout in a turn → the question is answered without passages, the turn completes |

> Retrieval quality (recall on a golden set of question → passage pairs) is not gated
> yet: there is no golden set. Backlog.

### 1.9 Observability (traces, perception measurements, capture)

**Contract:** a turn in → one trace whose spans name every step with its duration,
input and output; the perception loops → metrics and events; with capture on → frames,
turn audio and spans on disk.

| Type | Gate criteria |
|---|---|
| Functional | Each turn is one trace rooted at `turn` (from speech start) with `listen`, `stt`, `knowledge` (when wired), `llm`, one `tts` per sentence and `playback`; a failed, timed-out or cancelled turn says so on its root; every log line of the turn carries its `trace_id`; capture writes a playable `utterance.wav` / `answer.wav` and a `trace.json` per turn |
| Performance | No measurable cost on a turn: with capture on, local stages (TTS first chunk, tracking time per frame) stay within their run-to-run variation; nothing is written or exported on the event loop |
| Resilience | No OTLP endpoint, an unreachable one, a full capture queue or a disk error → the turn is unaffected; dropped capture items are counted; the capture directory stays under `CAPTURE_MAX_GB` once an hour has passed |

---

## 2. Integration Gates

Each gate wires a boundary and proves the two contracts line up.

| Gate | Boundary checked | Pass criteria |
|---|---|---|
| **VAD → Orchestrator → STT** | speech_end actually triggers transcription of the buffered utterance | A spoken clip produces correct text; buffer boundaries are right (no clipped first/last word) |
| **STT → LLM** | STT output shape is exactly what the LLM step consumes | Golden STT text flows into LLM with no reformatting glue; non-ASCII Vietnamese survives intact |
| **LLM → TTS (streaming)** | sentence-by-sentence text drives sentence-by-sentence audio | TTS starts on the first sentence while the LLM is still generating; no wait-for-whole |
| **TTS → Frontend (audio)** | audio format out == audio format the player decodes | Audio plays cleanly; lip-sync reacts to it; no decode errors |
| **Orchestrator ↔ Frontend (WebSocket)** | message types/shapes match both ways | tracking updates, speech-out, and interrupt all round-trip; unknown message rejected safely |
| **Device Router → STT** | both backends satisfy the same STT contract | Metal backend and CUDA backend produce the same output shape and both pass the §1.3 gate |

---

## 3. Pipeline Gate (end-to-end turn)

The expensive gate — a real turn, run sparingly, before a milestone.

| Type | Gate criteria |
|---|---|
| Functional | One full turn works: person present → speaks a Vietnamese utterance → avatar answers in Vietnamese voice with lip-sync. Barge-in works. No memory/tool/knowledge involved (MVP scope) |
| Performance | **First audio < ~3.5s** (the committed internal target); **end-to-end < 10s** (SLA); dead-air gap is covered by an immediate acknowledging cue |
| Resilience | Runs a sustained session without crashing or leaking resources; a mid-turn cloud error (LLM/TTS) degrades gracefully; weak-network behavior is observed and noted (cloud calls are the real latency risk) |
| Cross-device | The pipeline gate passes on **both** target machines — MacBook M5 (Metal) and Windows + RTX (CUDA) |

---

## 4. Golden Dataset Shapes (what the gates need)

The gates above depend on two golden datasets. Defining their **shape now** ensures the
sets you build later actually feed the gates. (Content of the sets is created
separately.)

### 4.1 STT golden dataset

Purpose: measure **WER** of the STT module per backend.

| Field | Description |
|---|---|
| `id` | Stable identifier for the sample |
| `audio` | Path to a Vietnamese utterance recording (realistic kiosk conditions: short, conversational, some with background noise) |
| `reference_text` | The exact correct Vietnamese transcription (ground truth) |
| `tags` (optional) | e.g. `clean`, `noisy`, `fast-speech`, `numbers`, `finance-terms` — to see WER per condition |

- **Size (MVP):** ~20–30 utterances is enough for a meaningful gate; weighted toward
  realistic kiosk phrasing and the finance vocabulary the avatar will hear.
- **WER method:** normalize both sides the same way (lowercase, strip punctuation,
  normalize Vietnamese diacritics + numbers), then standard WER = (S+D+I)/N. Fix the
  normalization once and reuse it, so results are comparable across backends and runs.
- **Gate use:** mean WER ≤ 15% (confirm threshold against the first real set); report
  WER per `tag` so a weak condition (e.g. noisy) is visible, not hidden in the average.

### 4.2 LLM golden dataset

Purpose: deterministically check the **form** of the answer (not content quality).

| Field | Description |
|---|---|
| `id` | Stable identifier |
| `input_text` | The user utterance text fed to the LLM (as if from STT) |
| `expect` | Form expectations, not an exact string: `language: vi`, `max_sentences: 3`, `non_empty: true`, `in_persona: true`, `no_tool_or_markup: true` |
| `tags` (optional) | e.g. `greeting`, `finance-question`, `off-topic`, `edge-empty` — to cover the input variety |

- **Size (MVP):** ~20–30 representative inputs, including a few edge cases (empty-ish
  input, off-topic, very long input).
- **Scoring:** each `expect` field is a programmatic check (language detect, sentence
  count, emptiness, persona heuristic, markup/tool-leak scan) → pass/fail per case.
- **Gate use:** ≥ 95% of cases pass all form checks (confirm threshold against the
  first real set). Content/answer-quality scoring is explicitly deferred to backlog.

### 4.3 VAD golden dataset

Purpose: check the one thing that matters for MVP — **tell human voice from background
noise**. Deliberately simple: binary classification, no timeline/segment labels.

| Field | Description |
|---|---|
| `id` | Stable identifier |
| `audio` | A short clip |
| `label` | `speech` (contains human voice) or `noise` (background noise / silence only) |

- **Size (MVP):** ~20–30 clips, split between `speech` and `noise`, with the `noise`
  side drawn from realistic kiosk background (ambient hum, distant chatter, silence).
- **Metric:** **recall on `speech`** (did it catch the voice?) and **false-trigger
  rate on `noise`** (did it wrongly fire?). No WER, no boundary timing.
- **Gate use:** recall ≥ 95% on `speech`, false-trigger ≤ 5% on `noise` (confirm
  thresholds against the first real set).

> Boundary accuracy (start/end timing) and end-of-speech latency are checked by direct
> measurement during integration/pipeline testing, not by this dataset — keeping the
> golden set simple.

---

## Backlog

- Content-quality scoring for LLM answers (beyond form checks) — deferred; MVP gates
  form only.
- Viseme/phoneme-accurate lip-sync gate — deferred (MVP is amplitude-driven).
- Expand golden datasets beyond ~30 samples and add per-condition WER thresholds once
  real kiosk recordings are available.
- Knowledge retrieval golden set (question → expected passage) and a recall@K gate —
  deferred; the first version gates the flow, not the retrieval quality.
