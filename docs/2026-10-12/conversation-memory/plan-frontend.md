---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: expression
summary: Conversation memory, the page — the live transcript window, the new-conversation button and the profile page
---

# Conversation memory — frontend

## Scope

Part of [Conversation memory](plan.md): the problem, the requirements, the diagrams of
the whole system and the slices are there. This file holds what the person
sees. It consumes two contracts and defines neither: the WebSocket messages
(`plan-turn.md`) and the HTTP routes `/memory` (`plan-memory.md`).


## Decisions made

### Transcript window and new conversation

- **Transcript window** (`frontend/src/transcript/`), docked on the right under the
  status label, always visible:
  - a `transcript` message adds a line of the person; the sentences of
    `speech_output` are appended to one line of the avatar per turn; `interrupt` marks
    the avatar's running line as cut;
  - it is a sliding window: it keeps the last `TRANSCRIPT_MAX_LINES` lines
    (`frontend/src/config.js`), drops the oldest, and scrolls to the newest line unless
    the person scrolled up to read;
  - it shows one conversation: it is emptied when a `conversation` message carries
    another id than the one on screen (`null` included). What is on screen is therefore
    never older than what the avatar still remembers, and a person who walks away
    leaves no text behind for the next one;
  - the lines are kept by a pure module (`transcript.js`: add, append, cut, cap,
    reset), the panel only renders them.
- **"Cuộc trò chuyện mới"** button in the header of that window: sends
  `conversation_reset`. The window is emptied by the `conversation` message that
  follows, not by the click, so the screen cannot disagree with the backend.
- Known limits of "real time": speech-to-text runs on the whole utterance, so the
  person's line appears when they stop speaking, not word by word; a sentence of the
  avatar appears when its audio arrives, which can be a moment before it is heard.
  After a barge-in the cut line can therefore show a sentence the avatar did not get to
  say, which the history does not hold.

### Profile page

- A "Hồ sơ" button next to "Tài liệu" opens the profile page
  (`frontend/src/memory/`), laid out as the person's configuration rather than a log:
  - who they are and how to address them (`identity`, `addressing`) as the first fields;
  - their preferences and personality as editable lists, with an "add" row;
  - on top, what the avatar gathered and has not confirmed: approve or reject, with the
    quote it comes from;
  - the imports: drop a file or paste a text, list, open the original, delete.
  Each item shows where it comes from (typed, imported, heard). A guest sees a note
  that login is required.

### Config

`TRANSCRIPT_MAX_LINES` in `frontend/src/config.js`, default 40 (about twenty turns, a
person line and an avatar line each). Beside it, `TRANSCRIPT_SCROLL_SLACK_PX` (default
24): how close to its end the list must be for a new line to scroll it. The profile page calls the backend at
`VITE_API_URL`, as the knowledge panel does.

## Implementation approach

```
frontend/src/transcript/             transcript.js (pure lines), transcript-panel.js
frontend/src/ws-client.js            + sendConversationReset
frontend/src/memory/                 client, profile page, pure helpers
frontend/tests/                      transcript.test.js, memory.test.js
```

## To-do

- [x] Slice 1 — frontend transcript window (`TRANSCRIPT_MAX_LINES`) and
      "Cuộc trò chuyện mới" button
- [ ] Slice 2 — frontend profile page: fields, lists, add, edit, delete, guest note
- [ ] Slice 3 — imports on the profile page: drop a file or paste a text, list, open the
      original, delete
- [ ] Slice 4 — proposals on the profile page: approve or reject, with the quote
- [ ] Unit tests per `test-plan.md` (Frontend — transcript, Frontend — memory),
      `npm run build`
- [ ] The profile page opened in a real browser
