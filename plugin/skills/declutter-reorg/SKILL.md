---
name: declutter-reorg
description: Reorganize a bloated CLAUDE.md / AGENTS.md into the 5 context layers without losing a single byte, using the deterministic `declutter reorg` command (lossless proof, safety guards, new git branch, undo). Works only from the JSON report; every destination, acceptance, apply and commit is the user's decision. Use when the user wants to tidy or split CLAUDE.md, when /declutter-hygiene shows S1 or S4 in red, or runs /declutter-reorg.
argument-hint: "[-C DIR]"
---

# declutter-reorg

Reorganización sin pérdida de `CLAUDE.md`/`AGENTS.md` en las 5 capas. La herramienta propone y
prueba; el humano decide cada paso.

## Preflight

Run `command -v declutter`. If it is missing, reply with exactly one install line and stop:

`pipx install git+https://github.com/nodo-salud-lab/declutter` (not on PyPI yet)

## Steps

1. **Dry run.** Run `declutter reorg plan --dry-run --json` (add `-C DIR` if the user passed one). Do
   not open, read or quote `CLAUDE.md`, `AGENTS.md` or any destination file: work only with the
   JSON, which by design carries metadata (ids, line ranges, sizes, hashes), never content. Exit
   code 5 means a secret was detected: report it by file and line only and stop. Codes 1 and 2:
   relay the issues and stop.
2. **Summary in Spanish, stamped GUÍA.** From the JSON, report:
   - the projection before and after (`projection`: S1–S4, bytes and lines per file, Codex % per
     chain, Claude load-set difference);
   - units with `confidence` `low` (they stay in L1 by default and are reviewed one by one);
   - units that leave L1 with an imperative rule and no safety copy (`needs_confirmation` true,
     `dest` other than `L1`, no `copies`): K ≥ 1 and S = 0;
   - units still waiting for acceptance (`pending_confirmation`);
   - pinned copies grouped by category (`copies[].categories`);
   - every entry of `warnings`;
   - if the exit code was 3, the levers in `levers` (keys `lever_*`) with the bytes the JSON gives
     for each one. List them all with their bytes and **do not recommend any**: choosing a lever
     is the user's decision.
3. **Write the plan.** Only after the user's explicit OK, run `declutter reorg plan --json`, which
   writes the manifest under `.declutter/reorg/<id>/`. Then record, unit by unit, each change the
   user decides, with the reason the user gives:
   - accept a proposed destination: `declutter reorg set <id> <unit> --accept --reason "<motivo>"`;
   - confirm a Claude-only unit: `declutter reorg set <id> <unit> --dest L1c --reason "<motivo>"`;
   - other destinations or keeping a unit in L1: `declutter reorg set <id> <unit> --dest L4 --reason "<motivo>"`
     or `declutter reorg set <id> <unit> --keep --reason "<motivo>"`;
   - a block the user says is not a safety rule:
     `declutter reorg set <id> <unit> --not-safety <block> --ack-reason "<motivo>"` (this is a
     `guard_ack`; it forces a TTY confirmation at apply time).

   `set` exits 0 once the override is saved, even when other units are still pending (it prints
   how many are left); 4 means the override itself was rejected; 3 means the Codex budget.

   Never choose a destination, an acceptance or a `guard_ack` on your own initiative, and never
   fill in a reason the user did not give.
4. **Validate.** Run `declutter reorg check <id>`. If it fails, explain each failure by its check id,
   unit id and line range, and go back to step 3 only with the user's decisions.
5. **Apply (second approval).** Look at `guard_acks` in the units of the JSON.
   - No `guard_ack`: ask again and, with a second explicit OK, run `declutter reorg apply <id> --yes`.
     It writes the files on the new branch `declutter/reorg-<id>` after the tag `declutter/pre-reorg-<id>`.
   - With any `guard_ack`: do not run apply. Ask the user to run it in the terminal with
     `! declutter reorg apply <id>`, because guard G9 requires a TTY and `--yes` never skips it.
6. **Commit (separate approval).** Ask whether to commit. Only with an explicit OK, run
   `declutter reorg apply <id> --commit --yes` (it resumes from the `written` journal; this shell has no
   TTY, so `--yes` is required). Never merge and never push: merging into the base branch is the
   user's decision (`git switch <base> && git merge declutter/reorg-<id>`).
7. **Close.** Show the closing summary from the output (measure before and after, decision cards
   to distill, link and import warnings), how to undo (`declutter reorg undo <id>`) and how to delete
   the local literal copies later (`declutter reorg prune <id>`). Remind the user to run
   `declutter ratchet check` before each commit; the pre-commit hook is optional and documented in the
   guide. Do not offer to install anything. If the summary lists decision cards to distill, point
   the user to `/declutter-distill`, which fills and distills them one card at a time (the user decides
   when, and which card first).

## Rules

- Content never leaves the files: do not read sources or destinations, and do not paste unit text
  into the conversation. Headings in the JSON are already truncated metadata.
- Every write needs the user's explicit OK: `plan` without `--dry-run`, each `set`, `apply` and the
  commit are separate approvals.
- Exit codes: 0 ok, 1 usage or precondition, 2 unprocessable source, 3 Codex budget or ratchet,
  4 invalid plan or failed proof, 5 secret. Relay the issues; do not work around them.
- Never merge, push, delete branches or tags, or edit the manifest by hand.
