---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: vision, vad, stt, llm, tts, orchestrator, knowledge
summary: Step-by-step tracing of a turn, measurements of the perception loops, and a full capture of what the kiosk saw and heard
---

# Observability: traces, perception measurements, full capture

## Problem statement

- The log is a handful of unrelated JSON lines per turn. Nothing ties them to one turn,
  and none says what went through a stage: the transcript, the passages found, the
  prompt, the answer.
- Perception logs only camera open / fail and the VAD model load. There is no frame
  rate, no processing time, no trace of a person arriving or of a speech segment.
- When an answer is wrong there is nothing to replay: the audio and the camera frames
  are gone.
- The app will be deployed on a server that is not Databricks; the log destination there
  is not chosen yet.

## Requirements

- One trace per turn, one span per step (listen, STT, knowledge lookup, embedding, LLM,
  each TTS sentence, playback), each with its duration and its full input / output.
- Every log line of a turn carries the id of its trace.
- Perception: processing time and rate of the camera and VAD loops as metrics; a person
  arriving / leaving, speech start / end, barge-in, camera loss as events.
- Full capture, when enabled: every tracked camera frame with its tracking result, the
  audio of each utterance and of each answer, and the spans of each turn — on local
  disk, bounded in size.
- The destination is configuration. The code does not depend on which backend receives
  the data, and the app runs the same with none.
- Observability never costs a turn: nothing blocks the event loop, a failing exporter or
  a full disk is logged and dropped.

## Decisions made

- **OpenTelemetry in the code** (traces + metrics), exported over OTLP/HTTP to
  `OTLP_ENDPOINT`. Empty endpoint = nothing is sent. Any OTLP backend can receive it
  (the company's stack, SigNoz, Grafana Tempo/Prometheus, Phoenix); choosing one is a
  deployment decision, not a code change. MLflow was set aside: its value is the
  Databricks workspace, which the target server is not.
- **Every finished span is also a log line** (`Span`, name, duration, attributes, long
  values cut to `TRACE_LOG_VALUE_MAX_CHARS`). The steps of a turn can be read in the
  terminal with no backend at all.
- **Logs stay JSON on stdout** with a `trace_id` field; a log collector ships them. The
  OpenTelemetry log SDK is not used (still marked experimental in Python).
- **Perception is not traced per frame.** 30 frames and 50 audio chunks per second as
  spans would flood any backend and slow the loops. Per-frame values are metrics
  (histograms, counters); state changes are log events; the listening phase is a span of
  the turn it starts.
- **Spans in async generators are not made "current"** (the engine streams): a span
  opened across a `yield` detaches in another context. They are started as children of
  the turn span and ended explicitly.
- **Full capture is off by default** (`CAPTURE_ENABLED`): it stores faces and voices of
  the people in front of the kiosk. When on, a writer thread stores under
  `CAPTURE_DIR/<date>/<hour>/`: `frames/*.jpg`, `tracking.jsonl`, `events.jsonl`, and
  per turn `turns/<time>_<trace id>/utterance.wav`, `answer.wav`, `trace.json` (all
  spans of the turn with their full attributes). The oldest hours are deleted when the
  directory exceeds `CAPTURE_MAX_GB`. A full queue drops the item and counts it.
- **Not captured**: microphone audio outside utterances (silence between turns).
- **Not in this version**: a dashboard, alerts, the choice and deployment of the backend,
  access control and retention policy for the captured data.

## Implementation approach

```
backend/utils/telemetry.py   tracer, meter, setup (OTLP, span log), trace id of the current span
backend/utils/capture.py     CaptureStore: writer thread, layout, size cap; span collector
backend/utils/logging.py     + trace_id on every line
core/conversation/engine.py  spans: stt, knowledge, llm, tts
core/orchestrator/           turn + listen + playback spans, perception metrics and events, capture calls
providers/                   embedding span, fallback event
features/knowledge/          ingest span
```

1. Telemetry core, `trace_id` in logs, spans of the turn + unit tests.
2. Perception metrics and events + unit tests.
3. Capture store, wiring + unit tests.
4. `.env.example`, `CLAUDE.md`, quality gates; real turn with capture on.

## To-do

- [ ] `utils/telemetry.py`, `trace_id` in logs, OTLP export by config
- [ ] Spans: turn, listen, stt, knowledge (+ embedding), llm, tts, playback, ingest
- [ ] Perception metrics and events
- [ ] `utils/capture.py` and its wiring
- [ ] Unit tests per `test-plan.md`
- [ ] `.env.example`, `CLAUDE.md`, `quality-gates.md`
- [ ] Real turn with capture on: the spans, the wav files and the frames are on disk;
      latency with and without capture
- [ ] Export to a real OTLP backend (none is available on the dev machine: no Docker)
- [ ] Human verification
