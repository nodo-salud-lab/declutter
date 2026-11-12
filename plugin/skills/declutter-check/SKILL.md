---
name: declutter-check
description: Show a status radar of all the user's registered projects (traffic light, last activity, uncommitted work, open items from ESTADO.md). Use when the user asks what is going on across projects, what to resume, or runs /declutter-check or /declutter-check --deep.
argument-hint: "[--deep]"
---

# declutter-check

Resumen del estado de todos tus repositorios (read-only).

## Preflight

Run `command -v declutter`. If it is missing, reply with exactly one install line and stop:

`pipx install git+https://github.com/nodo-salud-lab/declutter` (not on PyPI yet)

## Default mode

1. Run `declutter radar --md RADAR.md` (writes `RADAR.md` in the current directory; this is the only file it writes).
2. Read `RADAR.md` and summarize in the user's language: which projects are red or yellow, which have uncommitted changes or unpushed commits, and the open items from each `ESTADO.md`. End with 2-3 concrete suggested next moves, framed as options for the user to choose.
3. Rows that say there is no `ESTADO.md` are candidates for `--deep` or for `/declutter:declutter-init`.

If no projects are registered, suggest `declutter radar --discover <folder>` (proposes only; nothing is saved without `--save`) and let the user decide.

## Deep mode (`--deep`)

Only for projects that have no `ESTADO.md`. Follow this order strictly:

1. **Cost first.** Before reading anything, tell the user: this launches one cheap read-only subagent per project; each reads at most ~12 files or snippets (grep, `head`, `tail`), so token use grows with the number of projects. List the projects that would be scanned and ask for explicit consent. Do not continue without a clear yes.
2. Launch the subagents (cheapest available model, read-only tools only: Grep, Read with limits, `head`/`tail`). Hard cap: ~12 reads per project. Never read secrets, `.env` files, or personal or sensitive data; skip anything that looks like them.
3. Each subagent returns a draft state: what the project is, next steps, what the human must decide, blockers, and where to resume. Mark anything not found as missing; never invent it.
4. Show the drafts and offer to write each one as `ESTADO.md` in its project. Write a file only after the user approves that specific project.
