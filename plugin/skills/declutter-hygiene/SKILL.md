---
name: declutter-hygiene
description: Diagnose the context hygiene of the current project (size of CLAUDE.md, AGENTS.md, memory index and everything loaded at session start) and explain each red or yellow signal with its recommended action. Use when the user asks why context is heavy, wants to audit CLAUDE.md or memory, or runs /declutter-hygiene.
---

# declutter-hygiene

Diagnóstico de higiene de contexto. Fase 1: solo diagnostica, nunca edita.

## Preflight

Run `command -v declutter`. If it is missing, reply with exactly one install line and stop:

`pipx install git+https://github.com/nodo-salud-lab/declutter` (not on PyPI yet)

## Steps

1. Run `declutter measure` in the current project and read its output.
2. For every red and yellow signal: state what it measures, the observed value against its threshold, why it matters for context cost, and the concrete action that fixes it (what to move, trim or split, and into which layer).
3. Order by impact, red first. Green signals get one line at most.
4. Note that tools, skills and MCP servers are not measurable from files; point to `/context` for those.
5. If S1 or S4 is red, suggest `/declutter-reorg` (lossless reorganization into layers) as an option.

## Rules

- Diagnosis only. Do not edit, move or delete any file, even if the fix is obvious.
- Present actions as options for the user to decide; do not choose for them.
- If the user wants to apply a fix, say it is out of scope for this phase and offer to do it as a separate, explicit task.
