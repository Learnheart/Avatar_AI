# Architecture — Magic Mirror AI Avatar

> **Altitude:** High-level, PM-reviewable. Describes capabilities, boundaries, and
> data — not functions, classes, or code.
> **Version:** v1 (modular pipeline rebuild)
> **Status:** Draft — chờ Human Review

---

## 1. Objectives

**Target.** A kiosk avatar that greets a person standing in front of it, listens to
what they say, and answers back in a natural Vietnamese voice — with the on-screen
3D avatar moving its mouth in time with the spoken audio. The MVP proves **one
complete turn end to end**: presence → listen → understand → answer → speak.

**Inputs.**
- Live camera feed (detect that a person is present; drive head motion).
- Live microphone audio (the person's speech).

**Outputs.**
- Spoken audio answer played through the kiosk speaker.
- A 3D avatar that moves its head toward the person and opens/closes its mouth in
  sync with the audio.

**In scope (MVP).** A single, complete conversational turn. Presence detection,
speech-to-text, a language-model answer, text-to-speech, and amplitude-driven
lip-sync.

**Out of scope (MVP).** No long-term memory, no knowledge base, no tool/function
calling, no identity recognition (who the person *is*), no multi-user sessions.

**Constraints.**
- **Latency SLA:** end-to-end answer under **10s**; internal target **first audio
  under ~3.5s** (the number the user actually feels).
- **Hardware (cross-device):** must run on more than one kind of machine from a
  single codebase. Current targets: **Apple Silicon MacBook (M5, 16GB)** and
  **Windows PC + NVIDIA RTX**. The only hardware-dependent part is local inference
  (speech-to-text, and optionally vision); everything else is identical across
  machines.
- **Language:** Vietnamese voice quality is a first-class requirement.
- **Architecture principle:** each capability is a cleanly bounded module that
  *could* become its own service later; the MVP ships as a modular monolith, not a
  distributed system.

---

## 2. Techstacks

| Name | Description | Config / Ability |
|---|---|---|
| Python + async runtime | Backend host process | Runs all backend modules in one process; async coordination between them |
| FastAPI + WebSocket | Single real-time channel to the frontend | One bidirectional connection carries tracking, audio in, and speech out |
| Camera + presence/face detection | Perception — vision | Detects a person is present; produces head orientation to drive the avatar |
| Voice Activity Detection (Silero) | Perception — voice | Marks when the person starts and stops speaking |
| Speech-to-Text (Whisper, self-hosted) | Conversation — understand | Turns captured speech into Vietnamese text. Runs on a device-appropriate backend: **whisper.cpp / MLX (Metal)** on Apple Silicon, **faster-whisper (CUDA)** on NVIDIA, **CPU** as fallback |
| LLM (Gemini, cloud) | Conversation — think | Produces the Vietnamese answer; streams text so speech can start early |
| Text-to-Speech (Viettel / Azure, cloud) | Conversation — voice out | Turns answer text into Vietnamese audio; swappable provider |
| 3D avatar renderer (Three.js + VRM) | Expression — frontend | Renders the avatar, applies head motion, drives mouth from audio loudness |
| Web Audio API | Expression — frontend | Plays the answer audio and measures its loudness for lip-sync |
| Device Capability Router | Picks the local-inference backend at startup | Detects the host (Metal / CUDA / CPU) and loads the matching STT backend behind one stable contract; overridable by config |
| Packaging per platform | Reproducible setup on each target | Platform-specific install (Metal deps on macOS, CUDA deps on Windows); same application code on top |

---

## 3. Architecture

Three capability blocks on a stable spine. **Perception** feeds the **Orchestrator**,
which drives the **Conversation** engine, whose output flows to **Expression**. The
Conversation engine is a swappable box behind a fixed boundary — today a separated
STT→LLM→TTS pipeline, tomorrow (if ever needed) a unified realtime model — without
changing anything around it.

```
                        KIOSK (physical)
        camera ──┐                              ┌── speaker
        mic ─────┤                              │   screen
                 │                              │
        ┌────────┴──────────────────────────────┴─────────┐
        │                 FRONTEND (browser)               │
        │  ┌───────────────┐        ┌───────────────────┐  │
        │  │ mic capture   │        │ 3D AVATAR         │  │
        │  │ (audio in)    │        │ (EXPRESSION)      │  │
        │  └──────┬────────┘        │  • head motion    │  │
        │         │                 │  • lip-sync from  │  │
        │         │       audio out │    audio loudness │  │
        │         │       ┌─────────┤  • idle animation │  │
        │         │       │         └───────────────────┘  │
        └─────────┼───────┼──────────────────────────────┘
                  │       │   one WebSocket channel
        ──────────┼───────┼──────────────────────────────────
                  │       │          BACKEND (one process)
        ┌─────────▼───────┴──────────────────────────────┐
        │                 ORCHESTRATOR                     │
        │   state of the turn · routes data · timing       │
        └───┬───────────────┬───────────────────┬─────────┘
            │               │                   │
     ┌──────▼──────┐  ┌─────▼──────┐     ┌───────▼─────────────────┐
     │ PERCEPTION  │  │ PERCEPTION │     │   CONVERSATION ENGINE   │
     │ vision      │  │ voice      │     │   (swappable boundary)  │
     │ • presence  │  │ • VAD:     │     │                         │
     │ • head      │  │   start /  │     │   ┌─────┐ ┌─────┐ ┌────┐│
     │   orient.   │  │   stop     │     │   │ STT │→│ LLM │→│TTS ││
     └─────────────┘  └────────────┘     │   └─────┘ └─────┘ └────┘│
                                         │   text in → audio out   │
                                         └──────────┬──────────────┘
                                                    │ audio (streamed)
                                                    └──► Orchestrator ──► Frontend
       (vision runs continuously, in parallel with the listen→answer flow)

   external cloud: LLM provider, TTS provider
```

**Why these boundaries.**
- **Capability, not model.** Blocks are drawn by *what they do* (perceive / converse
  / express), so the choice of model lives *inside* a block and can change without
  reshaping the system.
- **Swappable Conversation engine.** STT→LLM→TTS sits behind one boundary with a
  single contract — "audio/text in, text + audio out." A unified realtime model
  could replace the whole box later; the Orchestrator and Expression don't care.
- **Lip-sync lives in the frontend, bound to the audio actually playing.** Mouth
  movement is derived from the loudness of the audio coming out of the speaker, not
  from when a network packet arrived — so it stays in sync regardless of network
  jitter or buffering.
- **Perception is a parallel plane.** Head tracking runs continuously and
  independently of the listen→answer turn; it is not on the critical path to speech.
- **One codebase, many machines — the router lives *inside* STT.** Local inference is
  the only hardware-dependent part. A **Device Capability Router** detects the host at
  startup (Apple Metal / NVIDIA CUDA / CPU) and loads the matching STT backend behind
  one stable contract ("audio in → text out"). It is a detail of how the STT backend is
  chosen (`backend/providers/catalog.py`, device detection in `backend/runtime/`),
  not a new block in the diagram — the capability boundaries above are unchanged no
  matter which backend runs:

  ```
  STT (capability)  —  stable contract: audio in → text out
        ▲
   Device Capability Router  (detect once at startup; config can override)
        │
   ┌────┼───────────────┐
   ▼    ▼               ▼
 Metal  CUDA           CPU
 (M5)  (RTX/Windows)  (fallback)
  ```

---

## 4. Dataflow — the response branch (text → voice → avatar motion)

The one flow worth clarifying, because it carries the sync and latency requirements.

```
LLM emits answer text ──(streamed, sentence by sentence)──►
   TTS turns each sentence into audio as it arrives ──►
      Orchestrator forwards each audio piece over the WebSocket ──►
         FRONTEND:
            ├─ queues and plays the audio through the speaker
            ├─ reads the loudness of the audio AS IT PLAYS
            │     └─ maps loudness → mouth-open amount (amplitude-driven lip-sync)
            └─ while speaking: avatar is in "speaking" state;
               when the audio ends: mouth closes, back to idle
```

**Key properties of this branch.**
- **Streaming, not wait-for-whole.** Speech starts on the first sentence the LLM
  produces, so the person hears a reply around the first-audio target (~3.5s) instead
  of waiting for the full answer.
- **Amplitude-driven lip-sync (MVP choice).** The mouth opens in proportion to how
  loud the audio is. Simple and cheap; it does not form exact vowel shapes. Accurate
  phoneme/viseme mouth shapes are deliberately deferred (see Backlog).
- **Dead-air is the real risk, not raw latency.** Because the SLA (10s) is generous,
  the design priority is covering the 2–4s "thinking" gap (an immediate acknowledging
  motion/filler) rather than shaving milliseconds.

---

## 5. Deployment

**Target environments (cross-device).** The same application runs on either machine;
only the local-inference backend differs, chosen automatically by the Device
Capability Router.

| | Apple Silicon MacBook (M5) | Windows PC + NVIDIA RTX |
|---|---|---|
| STT backend | whisper.cpp / MLX (Metal) | faster-whisper (CUDA) |
| Vision / VAD | CPU / Metal | CPU / CUDA |
| LLM, TTS | Cloud (identical) | Cloud (identical) |
| Install deps | Metal / Apple toolchain | CUDA + cuDNN toolchain |

**What runs where (both machines).**
- **Frontend (browser):** renders the avatar, captures microphone audio, plays answer
  audio, and computes lip-sync locally — identical on both.
- **Backend (one process):** hosts Perception (vision + voice), the Orchestrator, and
  the Conversation engine. The LLM and TTS are external cloud services. Only the STT
  backend is device-specific.

**Packaging is per-platform, application code is shared.** The dependency set differs
by machine (Metal libraries on macOS vs CUDA/cuDNN on Windows), so installation has a
platform-specific path. The router selects the right runtime; it does **not** remove
the need to install the correct dependencies on each machine, nor the need to verify
each backend on its own hardware.

**Blackwell / RTX 50-series note.** On very new NVIDIA cards (e.g. RTX 50-series,
Blackwell / sm_120), the CUDA backend requires a recent toolchain (CUDA 12.8+, cuDNN 9,
a PyTorch/CTranslate2 build that targets sm_120). This is a one-time setup gate on that
machine, independent of the architecture.

**Dependencies that must be reachable.** The LLM provider and the TTS provider are
cloud services; if the network is weak, these two calls — not the local pipeline — are
the latency risk, and this can only be confirmed by testing on the real network.

**Microservice / multi-machine path (not MVP).** Each capability block already has a
clean boundary. To scale out later, a block's boundary is exposed over the network
(RPC/queue) and the block moves to its own service — without redrawing the architecture
above. Distributing a single turn across multiple machines is explicitly out of MVP
scope.

---

## Backlog

> Change trail. The five parts above describe the *current* intended shape; each change
> is recorded here rather than silently rewritten.

- **2026-10-08 — Initial modular-pipeline architecture.** Rebuilt from the previous
  implementation-spec document. Decisions captured: (1) organize by capability
  (Perception / Conversation / Expression), not by model; (2) use a *separated*
  STT→LLM→TTS pipeline rather than a unified multimodal model, justified by the <10s
  SLA removing the latency argument for multimodal, plus control / Vietnamese voice
  quality / vendor-independence favoring separation; (3) keep the Conversation engine
  behind a swappable boundary so a realtime multimodal engine remains a drop-in
  fallback; (4) Expression uses amplitude-driven lip-sync for the MVP.
- **Deferred to backlog (not in MVP):** viseme/phoneme-accurate lip-sync; speaking
  body gestures; emotion-driven expression; long-term memory; knowledge base;
  tool/function calling; identity recognition.
- **2026-10-12 — Knowledge base (first version).** A user uploads documents in the UI;
  they are cut into chunks, embedded and kept in an embedded vector DB (sqlite-vec, one
  file). In a turn, the Conversation engine looks up the passages matching the question
  between STT and LLM and gives them to the LLM with the question; an empty, slow or
  failing knowledge base never blocks the turn. The embedding model is a swappable
  backend like STT / LLM. Upload, list and delete go over HTTP beside the WebSocket.
  Still deferred: authentication on the upload API, OCR of scanned files, hybrid search
  and re-ranking, long-term memory, tool calling. Plan:
  `docs/2026-10-12/knowledge-base/`.
