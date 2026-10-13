---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: accounts, memory, persona
summary: Bring the architecture document in line with the code (sign-in, persona) and move it from Markdown to one self-contained HTML page that is easier to review
---

# Architecture document: in line with the code, and as an HTML page

## Problem

`docs/ARCHITECTURE.md` describes the system as built, but the sign-in and the persona
were added to it in slices and five places were not carried along:

1. The header still says "MVP 2 planned" while the milestone table says it is built.
2. §3.5 says the long-term memory is "built for typed facts"; import and gathering are
   built too.
3. §3.2 lists the WebSocket messages of the conversation and not the two of the
   sign-in (`auth`, `session`).
4. §3.1 shows no sign-in in the browser and no external sign-in provider; §2 says HTTP
   serves "documents and the profile" only.
5. §5 (deployment) names neither the persona, the sign-in, the application database
   nor the external sign-in providers.

The document is also reviewed by a human as raw Markdown: 690 lines, eight Mermaid
diagrams that only render in some viewers, no navigation.

## Requirements

- The document says what the code does, at the same altitude as before (no functions,
  no file names).
- It is one file that opens in a browser by a double click: navigation, rendered
  diagrams, readable tables, light and dark.
- It stays the source of truth agents read: plain semantic HTML, written by hand, no
  build step and no generated copy to keep in sync.
- The `architecture-doc` skill produces this format from now on.

## Decisions

- **One file, `docs/ARCHITECTURE.html`, replaces `docs/ARCHITECTURE.md`.** Two files
  (a Markdown source and a generated page) would drift; the HTML is the document.
- **Diagrams stay Mermaid, as text in the page**, rendered by the Mermaid library
  loaded from a CDN. Without network the page still shows every diagram as its source
  text, so nothing is lost, only the drawing.
- **No new architecture record.** The shape is the one of ADR 0002; this is the
  document catching up, one Backlog row. The version stays v2.
- **The records in `docs/adr/` stay Markdown** and are not rewritten (§8); ADR 0002
  keeps naming the document by the path it had when the record was written. Earlier
  plans are not edited either.
- The first conversion is done once with a throwaway script; afterwards the HTML is
  edited by hand. The skill carries the page template.

## Implementation approach

1. Apply the five corrections and the Backlog row to the text.
2. Convert it to `docs/ARCHITECTURE.html` (template: side navigation, styles, Mermaid
   loader) and delete the Markdown file.
3. Point every reference at the new path: `CLAUDE.md` / `AGENTS.md`, `README.md`,
   `SETUP.md`, `docs/adr/README.md`, `docs/quality-gates.md`, and the check of the
   Backlog links in `tests/unit/test_docs.py`.
4. Update the `architecture-doc` skill (`~/.claude/skills/architecture-doc/`): output is
   one HTML page, diagrams are Mermaid, with the template beside the skill.
5. Verify: `tests/unit/test_docs.py`, and the page opened in a headless browser with
   every diagram rendered.

## To-do

- [x] The five corrections and the Backlog row
- [x] `docs/ARCHITECTURE.html` written, `docs/ARCHITECTURE.md` removed
- [x] References and `tests/unit/test_docs.py` updated
- [x] `architecture-doc` skill and its template updated
- [x] Unit tests pass; the page renders with all its diagrams
- [x] `CHANGELOG.md` entry
- [x] Size controls on each diagram (smaller, larger, fit, 100%, full screen); a diagram
      starts fitted to the width, never under 50%
- [ ] Human verification
