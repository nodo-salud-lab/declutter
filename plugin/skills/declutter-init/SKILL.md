---
name: declutter-init
description: Scaffold the 5-layer context convention in a project (AGENTS.md, CLAUDE.md, ESTADO.md, docs index files, declutter.toml), creating only what is missing. Previews first and applies only after the user agrees. Use when the user asks to set up or initialize context files, or runs /declutter-init.
argument-hint: "[--lang es|en]"
---

# declutter-init

Crea la estructura de contexto del proyecto; primero vista previa, luego aplica con tu OK.

## Preflight

Run `command -v declutter`. If it is missing, reply with exactly one install line and stop:

`pipx install git+https://github.com/nodo-salud-lab/declutter` (not on PyPI yet)

## Steps

1. Run `declutter init --dry-run` (add `--lang es` or `--lang en` if the user passed one) and show the result: which files would be created and which already exist.
2. If an existing `CLAUDE.md` is large, `declutter init` leaves it untouched, prints the full measurement and a phase-2 note, and says when CLAUDE.md does not import `@AGENTS.md`; relay that and do not try to rewrite it.
3. Ask: "Create these files?" Wait for an explicit yes.
4. Only after the yes, run `declutter init` with the same flags and report what was created.

## Rules

- Never run `declutter init` without the dry run and the user's approval first.
- `declutter init` only creates new files; never overwrite or edit existing ones by hand as part of this skill.
