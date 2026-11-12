---
name: declutter
description: Start here. A short, friendly guide through the whole declutter process in order — measure the context, reorganize CLAUDE.md / AGENTS.md losslessly (/declutter-reorg) if it has not been done yet, otherwise distill the decision cards automatically (/declutter-auto), and finish by showing what was saved using only the figures the tools print. Never merges, pushes or relaxes the ratchet. Use when the user runs /declutter, asks how to start, or wants their agent context tidied.
argument-hint: "[-C DIR]"
---

# declutter

Puerta de entrada: guía el proceso completo, paso a paso, en tono breve y amable. Cada paso lo
hace la skill que corresponde; esta solo ordena el camino y nunca decide por el usuario.

## Preflight

Run `command -v declutter`. If it is missing, reply with exactly one install line and stop:

`pipx install git+https://github.com/<owner>/declutter` (not on PyPI yet; replace `<owner>` with the account that hosts the repository)

Add `-C DIR` to every command below if the user passed one.

## Steps

1. **Measure.** Run `declutter measure` and show the traffic light in two or three lines: the
   overall level and the red or yellow signals with their values. Keep this output: it is the
   "before" for step 4. If everything is green, say so and offer to stop here.
2. **Is there a reorganization already?** Run `declutter reorg status`.
   - No run listed (or none `committed`): offer `/declutter-reorg`, which reorganizes `CLAUDE.md`
     / `AGENTS.md` into layers without losing a byte, on a new git branch. It asks for the
     user's OK before every write, and the apply that needs it still asks for the human's
     confirmation in a TTY. Continue only if the user says yes, and follow that skill.
3. **Reorganization done.** If a run is `committed` and its closing summary or
   `declutter reorg distill-check <id> --json` shows decision cards still pending, offer
   `/declutter-auto` to distill them all in one batch (or `/declutter-distill` to go one card at a
   time). Continue only if the user says yes, and follow the chosen skill.
4. **What was saved.** Run `declutter measure` again and compare it with step 1, signal by signal,
   quoting only the values both outputs print. If a reorganization or a distillation ran, also
   quote the figures from its own report (for `distill-auto`, the `AGENTS.md` line with bytes and
   lines before and after). If a figure is not printed by a tool, say it is not available; never
   estimate, round into a new number or invent a percentage.
5. **Close.** Remind the user that merging the new branch is their decision (the command is in the
   report), that `declutter ratchet check` guards against regrowth, and how to undo: `declutter reorg undo <id>`
   for a reorganization and `declutter reorg distill-undo <id> --auto <n>` for an automatic batch.

## Rules

- One step at a time; ask before moving to the next one.
- Never merge, push, delete branches or tags, or relax the ratchet (`ratchet relax` is always the
  human's decision, in a TTY).
- Never read or paste the content of `CLAUDE.md`, `AGENTS.md` or the cards here: the measuring and
  the proofs are the tool's job.
- Numbers come only from the tools' output. No made-up savings.
