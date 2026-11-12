# declutter plugin for Claude Code

This plugin adds seven skills and one hook on top of the `declutter` command-line tool, which must be
installed first (`pipx install git+https://github.com/nodo-salud-lab/declutter`; it is not on PyPI yet).

- `/declutter:declutter` is the entry point: it measures, offers `/declutter-reorg` when no
  reorganization exists yet or `/declutter-auto` when one does, and ends by showing what was saved
  with the figures the tools print. It never merges, pushes or relaxes the ratchet.
- `/declutter:declutter-hygiene` measures the current project and explains each yellow or red signal with
  its suggested action. It only diagnoses; it never edits files.
- `/declutter:declutter-check` shows one table for all registered projects. `--deep` offers to draft a
  state file for projects that have none, after warning about the cost.
- `/declutter:declutter-init` previews the missing context files and creates them only after you agree.
- `/declutter:declutter-reorg` reorganizes a bloated `CLAUDE.md`/`AGENTS.md` into the 5 layers with
  `declutter reorg`, losslessly and on a new git branch. It works only from the plan's JSON, never
  decides a destination on its own and asks for your OK before every write, the apply and the
  commit. It never merges or pushes.
- `/declutter:declutter-distill` distills the decision cards that `declutter-reorg` leaves, one card at a
  time: it fills the card strictly from its verbatim literal («no consta» when the literal does not
  say it), checks it with `declutter reorg distill-check` and shows you the before and after. You run
  `declutter reorg distill-apply` yourself in a terminal (it asks for a reason and the word `confirmo`);
  the commit is a separate approval and `declutter reorg distill-undo` undoes the last one.
- `/declutter:declutter-auto` fills every pending card from its literal, checks each one and runs
  `declutter reorg distill-auto` once: the batch lands on a new branch, the report carries the merge
  and undo commands, and merging stays your decision.
- A `SessionStart` hook (matcher `startup`) prints one line when the project is red. It never
  blocks a session and always exits 0.

The engine never uses the network, never calls an AI and writes only inside the project. See the
repository README for signals, thresholds, `declutter reorg`, the ratchet and the status line setup. Supported on macOS and Linux; on
Windows the hook needs Git Bash.
