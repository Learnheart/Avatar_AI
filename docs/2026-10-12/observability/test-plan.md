---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: vision, vad, stt, llm, tts, orchestrator, knowledge
summary: Tests for the turn traces, the perception measurements and the full capture
---

# Test plan — observability

## Unit tests (sandbox, `tests/unit/`)

Spans and metrics are read from in-memory exporters installed once for the test session;
the capture store writes into `tmp_path`.

| Area | File | Cases |
|---|---|---|
| Telemetry | `test_telemetry.py` | a finished span is logged with its duration and with long values cut; a log line inside a span carries its `trace_id`, outside it none; no endpoint → no OTLP exporter, an endpoint → one, with the headers |
| Engine | `test_engine_tracing.py` | a turn yields `stt`, `knowledge`, `llm` and one `tts` span per sentence, all children of the caller's span; `stt` has the transcript, `knowledge` the query and one event per passage, `llm` the prompt, the answer and the first-token time, `tts` the sentence and the audio length; a failing lookup, an LLM error and a TTS error mark their span |
| Orchestrator | `test_orchestrator_tracing.py` | one `turn` root span per turn starting at speech start, with a `listen` child and the engine's spans; outcome `completed`, `failed`, `cancelled` (barge-in); perception histograms and the presence / speech events are recorded |
| Capture | `test_capture.py` | a frame is written as JPEG with its tracking line; `CAPTURE_FRAME_FPS` thins the frames; turn audio is a valid WAV; the spans of a trace are written when its root ends; the oldest hour is deleted over the size cap, the current one kept; a full queue drops and counts; disabled → nothing written |

## End-to-end tests (real environment)

- Real turn with `CAPTURE_ENABLED=1`: `trace.json` lists the steps with the transcript,
  the passages and the answer; `utterance.wav` and `answer.wav` play; frames and
  `tracking.jsonl` accumulate. First audio compared with capture off.
- With an OTLP backend reachable: the turn appears there as one trace.
