Before working, read `STATE.md` and `docs/decisions/INDEX.md`. This file is canonical for any AI agent.

# {{project_name}}

Template created with declutter on {{date}}. Fill in everything marked `FILL IN`, then delete this line.

## Purpose
- FILL IN: what this project does and for whom, in 2 or 3 lines.

## How to run and test
- FILL IN: install command.
- FILL IN: command to run the project.
- FILL IN: command to run the tests (mandatory before calling a task done).

## Safety invariants
Anything that causes harm if omitted lives here, not in per-folder rules: those may not reload after compaction.
- FILL IN: sensitive or personal data (what must never leave the controlled environment).
- FILL IN: secrets and credentials (never in the repository or in prompts).
- FILL IN: figures or results that get published (how they are verified).
- FILL IN: irreversible actions (delete, publish, send): require explicit confirmation.

## Zones
- FILL IN: folder or module with its own rules. Each zone gets its own `AGENTS.md` (max 150 lines) and a `CLAUDE.md` containing `@AGENTS.md`.

## Where things live
| Layer | File | Cap |
|---|---|---|
| Permanent rules | `AGENTS.md` (this file) and `CLAUDE.md` | 200 lines / 12 KiB |
| Zone rules | `AGENTS.md` in subfolders | 150 lines each |
| Live state | `STATE.md` | 60 lines |
| History | `docs/history/YYYY-MM.md` + `INDEX.md` | no cap, never auto-loaded |
| Decisions and traps | `docs/decisions/D-NNN-*.md` + `INDEX.md`, `docs/traps.md` | no cap, on demand |

## Rules for agents
- At the end of each task, update `STATE.md` (date, phase, next, blockers, where to resume).
- Never append a changelog or history to `AGENTS.md` or `CLAUDE.md`: it goes in `docs/history/YYYY-MM.md`.
- Record each decision as `D-NNN` in `docs/decisions/` with its why and the rejected alternative, and add it to the index.
- Never delete information: what is no longer current moves to history.
- Do not choose for the human what is listed under "Human decides".
- Keep this file at 200 lines or fewer. If it grows, move detail to a zone or to `docs/`.
- When unsure about an irreversible action, stop and ask.
