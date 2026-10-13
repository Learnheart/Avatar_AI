---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: stt, expression, knowledge, accounts
summary: What the first manual test found — a sentence Whisper invents is answered as if it had been said, the head mirrors every movement of the person, the two side panels cannot be folded away, and an account could not be created
---

# Fixes from the first manual test

## Problem statement

Seen by a human on 2026-10-13 on the running page (Groq STT and LLM), with the backend
log of that session:

1. **An invented sentence is taken for what was said.** One utterance of 2.5 s was
   transcribed as "Cảm ơn các bạn đã theo dõi và ủng hộ cho kênh lalaschool Để không bỏ
   lỡ những video hấp dẫn" and answered. It is a sentence Whisper is known to produce on
   short or unclear audio (the closing words of the videos it was trained on), not a
   mishearing. The log shows why nothing stopped it: confidence 0.83, and a
   `no_speech_prob` of 0.0, the value this backend gave for every utterance of the
   session, so neither threshold can tell it from speech.
2. **The head follows the person too closely.** The avatar turns its head with every
   movement of the person in front of it, quickly, which reads as mechanical.
3. **The side panels are always in the way or hard to find.** The transcript window is
   always open over the avatar; the documents are behind a button that does not say it
   opens anything.
4. **No account could be created.** The log holds no request to `/auth/register`: the
   page refused the form before sending it. The form asks for a "user name" of
   unaccented letters and digits, which an email address is not, and says so only in a
   line under the buttons.

## Requirements

1. A transcript that is one of the sentences Whisper invents starts no answer: the turn
   ends as if nothing had been said. The list of those sentences can be extended without
   touching code paths.
2. The head moves slowly and on its own, like someone standing and waiting, and no
   longer copies the person. The tilt while thinking stays.
3. The documents and the conversation are two drop-downs: a header that is always
   there, a body that opens and closes when the header is pressed, each remembering how
   it was left.
4. Creating an account works with what a person naturally types. This is settled by
   signing in with an email address, verified by a code: see
   `../sign-in/plan.md`.

## Decisions made

- **Invented sentences are matched by their words, in the engine.** No number the STT
  gives separates them from speech (the log above), so the transcript is compared, in
  lower case and without punctuation, with a list of phrases; one that is found makes
  the turn end like an empty transcript. It is done in the conversation engine, after
  any STT backend. The phrases are texts (`STT_HALLUCINATIONS` in
  `backend/prompts.py`): the ones seen, and the family they belong to (thanks for
  watching, subscribe to the channel, see you in the next video). The `stt` span keeps
  what the STT wrote and says it was dropped.
  - What it costs: a person who really says one of those phrases is not answered. On a
    kiosk nobody thanks viewers for watching.
  - What it does not fix: the person did say something, and it is lost. Asking the STT
    again, or with a hint, is not tried here.
- **The head has a motion of its own.** A slow sway made of a few sine waves of
  different periods on each axis, a pure function of time in `avatar/motion.js`, a few
  degrees wide. Following the person becomes an option of the page that is off
  (`HEAD_FOLLOWS_PERSON`), and when it is on the head turns more slowly than before.
  The backend still tracks the person: presence is what closes a conversation.
- **One small helper makes a drop-down** (`frontend/src/ui/collapsible.js`): a header
  button with `aria-expanded`, a body that is hidden, the choice kept in the browser.
  The documents keep their button in the toolbar, which becomes that header (it shows
  whether the list is open); the transcript window gets a header of its own and folds
  to it. The transcript starts open, the documents closed, as today.

## Implementation approach

1. Engine: drop a transcript that is an invented sentence; the phrases; unit tests.
2. Head: the idle sway and the option to follow; unit tests of the pure function.
3. Panels: the helper, the two drop-downs; unit tests of what is remembered; build.
4. Account creation: in `../sign-in/plan.md`.

## To-do

- [x] Invented sentences dropped by the engine, with unit tests, `CHANGELOG.md`
- [ ] The head moving on its own, with unit tests, `CHANGELOG.md`
- [ ] The two drop-downs, with unit tests, `npm run build`, `CHANGELOG.md`
- [ ] The page in a real browser: both drop-downs opened, closed and remembered across a
      reload
- [ ] Seen in the same log, to decide: one answer of seven sentences took 28 s to
      synthesise and the turn was cut at `TURN_TIMEOUT_SEC` (30 s) with an error; the
      question was answered from a document and the answer ignored the "two or three
      sentences" of the persona
- [ ] Human verification
