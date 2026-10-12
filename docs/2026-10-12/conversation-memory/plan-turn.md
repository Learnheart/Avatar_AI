---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: orchestrator, llm, stt
summary: Conversation memory, the turn — the conversation as a user session, its history, what is recorded of a turn, the LLM contract, the knowledge lookup of a follow-up, STT confidence, the WebSocket contract
---

# Conversation memory — the conversation and the turn

## Scope

Part of [Conversation memory](plan.md): the problem, the requirements, the diagrams of
the whole system and the slices are there. This file holds the short-term
memory: everything a guest gets, with no database and no login (slice 1).


## Decisions made

### The conversation

- A **conversation** is the session of one user: `Conversation(id, user_id, history)`
  (`core/conversation/history.py`), `user_id = None` for a guest. Three ids, three
  meanings:

  | Id | Names | Lives |
  |---|---|---|
  | `user_id` | the person (from `current_user_id()`, `None` = guest) | across visits |
  | `conversation_id` | one session of that person: the unit of short-term memory | from the first utterance to the close |
  | `session_id` | one WebSocket connection (unchanged, `ws_manager.py`) | until the socket drops |

- The history belongs to the conversation, and the conversation to one user: the engine
  receives the history of the conversation whose `user_id` is the user of the turn, and
  a conversation is closed before a turn of another user can run. No history is ever
  read by connection alone.
- It is defined by presence, silence and the person's choice, not by the WebSocket, and
  it is owned by the orchestrator (the only owner of state):
  - it opens at the first utterance when none is open;
  - it closes after `CONVERSATION_ABSENCE_SEC` without a face (only while the camera is
    connected), or `CONVERSATION_IDLE_SEC` after the last turn ended, or when the user
    id of the connection changes, or when the client sends `conversation_reset` (the
    "Cuộc trò chuyện mới" button). The first three never close it while a turn is
    running;
  - `conversation_reset` is the only close that may arrive during a turn: the running
    turn is cancelled as by a barge-in (`interrupt` to the client, state back to
    `idle`, the turn recorded as `interrupted` with what was spoken), then the
    conversation closes. It is an ordinary close: for a logged-in user the conversation
    is consolidated like any other. With no conversation open it does nothing;
  - a WebSocket reconnect with the same user keeps it open.
- The orchestrator holds one conversation because the backend serves one connection.
  When sessions per connection are added, the same object becomes one per connection;
  the history and the engine do not change.
- The check runs on every tick of the tracking loop, also with no client connected:
  the camera is tracked on every tick and only the sending of `tracking_update` needs a
  client, so a person leaving while the browser is disconnected still closes the
  conversation. It is measured on a clock injected into the orchestrator (seconds, `time.monotonic` by default) so
  tests move time without touching the event loop's. Idle, absence and change of user
  close the conversation only in state `idle` with no turn task running: the state
  is `idle` a moment before the turn is recorded (its `state` message is being sent),
  and a close decided in that moment would drop the turn.
- Absence counts from the later of the last face seen and the last turn's end (or the
  opening): a person who just spoke was there, also when the camera does not see them.
- Only a recorded turn postpones the close: the idle and absence timers restart when
  a turn enters the history, not when an utterance with an empty transcript ends, so
  repeated noise cannot keep a conversation open.
- It opens at the end of the first utterance, before the `turn` span, so the client is
  told the id before anything of that turn. An utterance with an empty transcript
  leaves it open.
- `handle_connect(websocket, user_id=None)` carries the user of the connection. The
  handshake is `connected`, `state`, then `conversation`. A lost socket does not close
  the conversation; a running turn it cancels is recorded as interrupted.
- `conversation_reset` while `listening` drops the utterance being heard and closes the
  conversation; while `listening` to the first utterance (none open yet) it does
  nothing.
- Closing only drops the conversation and tells the client; nothing else is notified
  in slice 1.
- Known limit: a second person arriving inside `CONVERSATION_ABSENCE_SEC` inherits the
  context of the first. Without identity recognition only a short threshold reduces it.

### History

- The history is a list in RAM (`core/conversation/history.py`, pure): append is O(1),
  the oldest turns are dropped past `MEMORY_HISTORY_MAX_TURNS`. No rolling summary in
  this version: answers are 2–3 sentences, so the cap stays small without a second LLM
  call.

- `history.py` also holds the two pure functions made of a history: the (role, text)
  chat messages of an LLM request and the search query of the knowledge lookup. Two
  utterances that follow each other are joined with a newline.

### Recording a turn

- A turn is recorded by the orchestrator when it ends, however it ends (completed,
  barge-in, reset, timeout, failure), provided its transcript is known and not empty. A
  turn whose transcript is empty or never arrived is not recorded. It holds:
  - the **raw transcript** (never the `KNOWLEDGE_PROMPT` wrapping, passages are looked
    up again for each question);
  - the answer **as it was spoken**: the sentences whose audio was sent and had started
    to play. Two things are left out: `LLM_FALLBACK_TEXT` (the engine marks that
    sentence on its event, so an apology is never remembered as an answer) and a
    sentence whose TTS failed (it is sent as text only and was never heard);
  - `interrupted`, set by a barge-in or a reset;
  - the STT confidence.
- **What had started to play is estimated by the orchestrator.** TTS runs faster than
  real time: an answer of 2–3 sentences is usually sent whole before its first sentence
  has finished playing, so "what was sent" would record the whole answer on almost
  every barge-in. The orchestrator knows when the first audio was sent and how long the
  audio of each sentence lasts: a sentence counts when its start (first audio + the
  durations of the sentences before it) is not later than the cut. A turn that played
  to its end keeps every sentence. It is an estimate (the browser starts a little after
  the send) and needs no new message.
- What a running turn has heard and spoken lives in one `TurnRecord` (`history.py`,
  pure) held by the orchestrator, which gives it the clock readings; it becomes a
  `ChatTurn` in one place, when the turn ends. `ChatTurn` holds the raw text, the
  spoken sentences (the answer is their join), `interrupted` and the confidence.
- A sentence starts at the later of the moment its first audio was sent and the
  estimated end of the sentence before it. The cut is taken when the turn is cancelled,
  before the engine is closed.
- `interrupted` is set by every cancellation (barge-in, reset, disconnect, reconnect,
  shutdown). A timeout or a failure also cuts the answer at that moment, without
  `interrupted`.
- **The answer of a recorded turn may be empty**: a barge-in while `thinking` (often
  the person going on after a pause that the VAD took for the end), a timeout, a
  failure, an LLM that produced only the fallback. The turn is kept for what the person
  said. Turned into chat messages, such a turn gives a user message alone, and user
  messages that follow each other, the current utterance included, are joined into
  one: the roles always alternate and the first half of a sentence cut by the VAD is
  not lost.

### `LLM` contract

- The `LLM` contract becomes `stream_answer(user_text, history=(), profile="")`. Both
  backends and `FallbackLLM` map `history` to chat messages and `profile` to a second
  system message placed after `SYSTEM_PROMPT`, so the leading part of the prompt is
  identical for a whole conversation.
- Both backends take their messages from the one function of `history.py` and only map
  (role, text) to their wire shape. In the Responses backend an assistant message has
  content type `output_text`; user and system keep `input_text`.
- The engine is `respond(pcm16, sample_rate, history=())`: it receives the history from
  the orchestrator and stores nothing. In slice 1 it passes an empty `profile`.
- `LLM.complete`, used by the extraction, is in `plan-memory.md`.

### Knowledge lookup

- **The knowledge lookup searches with the previous utterances too.** A follow-up
  ("còn cái kia thì sao?") names nothing a vector search can find. The search query is
  the last `KNOWLEDGE_QUERY_HISTORY_TURNS` utterances of the person (default 1; 0 = the
  current utterance alone) followed by the current one; the question given to the LLM
  stays the current utterance. It costs no LLM call and no extra lookup. The
  `knowledge` span carries the query as it was searched. Known cost: after a change of
  subject the previous utterance pulls passages of the old subject, which
  `KNOWLEDGE_PROMPT` tells the model to ignore; the real run checks both cases.
  Rewriting the follow-up with an LLM is more exact and costs a call before the answer;
  it is left out until a real run shows this is not enough.

### STT confidence

- `Transcript.confidence: Optional[float]` (0–1). The OpenAI-compatible backend asks
  for `verbose_json` and derives it from the segments' `avg_logprob`; no segments in the
  response → `None`. The chat-audio backend (Gemini on Databricks) → `None`.
- **An utterance that is not speech is refused by the STT backend, not filtered
  later.** The same response gives `no_speech_prob` per segment. When every segment is
  above `STT_NO_SPEECH_MAX_PROB`, the backend returns an empty transcript and the turn
  ends as an empty utterance does today: no answer, no line on screen, nothing
  recorded. A turn that was answered is part of the conversation and is recorded as it
  happened, with its confidence; there is no second filter on the history.
- `STT_NO_SPEECH_MAX_PROB` defaults to 1.0 (nothing refused) until the real run gives a
  value: refusing a short real answer ("ừ", "có") is worse than answering a noise. The
  `stt` span carries `stt.no_speech_prob` either way, so the value is chosen from
  traces.
- The confidence is `exp` of the mean `avg_logprob` of the segments, clamped to 0–1.
  `Transcript.no_speech_prob` is the lowest value over the segments, so "every segment
  above the threshold" is that one value above it. Segments may be objects or dicts, or
  missing.
- To confirm in the real run: Groq returns the segments for the configured model, and
  the richer response does not slow STT.

### WebSocket contract (changed)

Three additions; the existing messages keep their shape.

| Direction | Message | When |
|---|---|---|
| client → server | `{"type": "conversation_reset"}` | the person presses "Cuộc trò chuyện mới" |
| server → client | `{"type": "conversation", "conversation_id": "<id>" \| null}` | a conversation opened (its id) or closed (`null`), for any reason; also sent after `connected`, with the id of the conversation still open or `null` |
| server → client | `{"type": "transcript", "text": "..."}` | speech-to-text finished for the turn and the text is not empty |

- The avatar's side of the transcript needs no new message: `speech_output.text_content`
  already carries each sentence with the first audio chunk of that sentence.
- The client never names a conversation: it asks for a reset and is told the result.
  The server stays the only owner of the conversation.
- The client side of these messages is in `plan-frontend.md`.

### Tunables

All in `backend/tuning.py`, each overridable from `.env` and listed (commented out) in
`.env.example`:

| Key | Meaning | Default |
|---|---|---|
| `CONVERSATION_ABSENCE_SEC` | time without a face after which the conversation closes | 30 |
| `CONVERSATION_IDLE_SEC` | time after the last turn ended after which it closes | 120 |
| `MEMORY_HISTORY_MAX_TURNS` | turns kept in the history | 10 |
| `KNOWLEDGE_QUERY_HISTORY_TURNS` | previous utterances added to the search query, 0 = none | 1 |
| `STT_NO_SPEECH_MAX_PROB` | above it for every segment, the utterance is not speech | 1.0 (nothing refused) |

The first three were chosen with the code and are confirmed in the real run.
`AppConfig.validate()` refuses the two `CONVERSATION_*` at 0 or below,
`MEMORY_HISTORY_MAX_TURNS` below 1, `KNOWLEDGE_QUERY_HISTORY_TURNS` below 0 and
`STT_NO_SPEECH_MAX_PROB` outside 0–1.

### Observability

- `turn` span: `conversation.id`, `user.id` (absent for a guest). Opening and closing
  a conversation are log lines with its id and the reason of the close (`absence`,
  `idle`, `user_changed`, `reset`).
- `stt` span: `stt.confidence`, `stt.no_speech_prob`. `llm` span: `llm.history_turns`,
  `llm.profile`. `knowledge` span: the query as it was searched.

## Implementation approach

```
backend/
  models.py                          + ChatTurn; Transcript.confidence
  core/contracts.py                  LLM.stream_answer(history, profile)
  core/conversation/history.py       Conversation: id, user id, its turns; TurnRecord; chat messages, search query (pure)
  core/conversation/engine.py        history → LLM, search query, fallback sentence marked, confidence on TranscriptEvent
  core/orchestrator/orchestrator.py  conversation open / close / reset, records turns, sends the transcript
  core/orchestrator/ws_manager.py    + transcript_message, conversation_message
  providers/llm/*.py, fallback.py    history, profile
  providers/stt/openai_compatible.py verbose_json → confidence, non-speech refused
  tuning.py                          the keys above
```

## To-do

- [x] Slice 1 — `ChatTurn`, `Conversation` (`history.py`), `LLM.stream_answer(history,
      profile)` in both backends and `FallbackLLM`
- [x] Slice 1 — conversation open / close in the orchestrator, keyed by user id,
      `CONVERSATION_*` and `MEMORY_HISTORY_MAX_TURNS`
- [x] Slice 1 — turns recorded however they end, with what was spoken (estimate of what
      had started to play, fallback text marked by the engine, TTS-failed sentences left
      out); user messages that follow each other joined
- [x] Slice 1 — knowledge lookup with the previous utterances
      (`KNOWLEDGE_QUERY_HISTORY_TURNS`)
- [x] Slice 1 — WebSocket: `conversation_reset`, `conversation`, `transcript`; reset
      during a running turn
- [x] Slice 1 — `Transcript.confidence` from `verbose_json`; non-speech refused by the
      STT backend (`STT_NO_SPEECH_MAX_PROB`); spans
- [x] Unit tests per `test-plan.md`: History, LLM backends, Fallback, STT, Engine,
      Conversation boundary, Recording a turn, Reset and wire messages, Config
