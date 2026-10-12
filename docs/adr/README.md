# Architecture records

The official architectures of the system, one record per architecture, newest last.
Each record is the **whole** architecture at that point — not a single decision — in
three parts: the context that led to it, its high-level diagram, and its consequences.
A record may add an ROI part (what it costs to run and to build against what it
returns); it is optional.
The rules are in `CLAUDE.md` / `AGENTS.md` §8.

| Document | Answers | Edited afterwards |
|---|---|---|
| `docs/adr/NNNN-<name>.md` | Which architectures were adopted, **why**, and what each one cost | No: a new architecture is a new record that supersedes the last |
| `docs/ARCHITECTURE.md` | The **current** architecture in full detail (tech stack, dataflows, deployment) | Yes, it always shows the current shape |
| `docs/YYYY-MM-DD/<feature>/plan.md` | **How** a change is built, and how far it is | Yes, while the work runs |
| `CHANGELOG.md` | **What** changed for the user | Entries are only added |

The record that is not superseded is the current architecture.

## Index

| ADR | Architecture | Status | Date |
|---|---|---|---|
| [0001](0001-mvp-1-architecture.md) | MVP 1 — one spoken turn, with answers from documents | superseded | 2026-10-12 |
| [0002](0002-mvp-2-architecture.md) | MVP 2 — memory, users and a profile the person controls | accepted | 2026-10-12 |
