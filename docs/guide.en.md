# The 5-layer method

declutter measures and scaffolds one convention: **put every piece of project knowledge in the
layer where it is loaded at the right time, at the right cost, and nowhere else.** This guide
explains the layers, how to decide where a paragraph goes, and how to migrate a bloated
`CLAUDE.md`, by hand or with `declutter reorg` (lossless, with a coverage proof), and how the ratchet
keeps it from growing back.

## Why layers

An agent pays for everything it loads at the start of every session, and loses detail every time
the conversation is compacted. A single ever-growing `CLAUDE.md` mixes rules that must always be
present with history that is only occasionally useful. Layers separate them by *when they must be
read*.

## The layers

| Layer | File | Default cap | Loaded |
|---|---|---|---|
| 1. Permanent rules | `AGENTS.md` (canonical) + `CLAUDE.md` containing `@AGENTS.md` plus Claude-only notes | 200 lines / 12 KiB | At session start, in any AI |
| 2. Zone rules | `AGENTS.md` in a subfolder (Codex) + `CLAUDE.md` with `@AGENTS.md` in that subfolder (Claude, on demand) | 150 lines each | When working in that zone |
| 3. Live state | `ESTADO.md` / `STATE.md` | 60 lines | At start (an instruction in `AGENTS.md`; in Claude, optionally a hook) |
| 4. History | `docs/historial/AAAA-MM.md` (`docs/history/YYYY-MM.md`) + `INDICE.md` (`INDEX.md`) | none | Never automatically |
| 5. Decisions and traps | `docs/decisiones/D-NNN-*.md` (`docs/decisions/`) + index, and `docs/trampas.md` (`docs/traps.md`) | none | On demand; the index is read at start |

`AGENTS.md` is canonical because several tools read it (Codex natively). `CLAUDE.md` imports it
with `@AGENTS.md` so Claude Code sees the same rules, and adds only what is Claude-specific.
`declutter init` creates this skeleton in Spanish or English without overwriting anything.

### What goes where

- **Layer 1: rules that are true next month and cost something if forgotten.** Purpose in two
  lines, how to install/run/test, naming and style rules that are not enforced by a linter, and
  the safety invariants (below).
- **Layer 2: rules that only matter in one folder or module.** Keep them next to the code.
- **Layer 3: where things stand today.** Date, phase, next tasks for the AI, decisions waiting for
  the human, blockers, where to resume. It is rewritten, not appended to.
- **Layer 4: what happened.** Dated narrative, per-branch notes, resolved investigations.
- **Layer 5: why things are the way they are.** One file per decision with the reason and the
  rejected alternative; and a list of traps (things that look right and are wrong).

## Decision test for each paragraph

Take each paragraph of the file you are cleaning and ask, in order. The first "yes" decides.

1. **Would omitting it cause harm** (sensitive data leaks, wrong published figures, equipment
   safety, irreversible actions)? -> Layer 1, always. See the safety rule.
2. **Is it a rule that applies in every session, in every folder?** -> Layer 1.
3. **Does it apply only inside one folder or module?** -> Layer 2.
4. **Is it a statement about *now*** (current phase, open tasks, blockers)? -> Layer 3, and if it
   is already stale, it goes to layer 4.
5. **Is it a justification** ("we chose X over Y because...") **or a trap**? -> Layer 5.
6. **Is it a story of what happened** (dated entries, branch logs, resolved bugs)? -> Layer 4.
7. **None of the above and nobody can say when it would be used?** Keep it in layer 4 anyway.
   Never delete (see below).

If a paragraph mixes two answers, split it. A rule plus its history becomes a one-line rule in
layer 1 and a link to the layer 4 or 5 entry.

## The safety rule

Anything that causes harm if omitted lives in **layer 1**: clinical or personal data handling,
secrets, figures that get published, equipment or patient safety, irreversible actions. Do not put
these in per-folder rules or in `.claude/rules/` files with `paths:` frontmatter: path-scoped rules
may not be reloaded after the conversation is compacted. Layer 1 is the only layer guaranteed to
be in front of the agent at the start of every session. See the
[Claude Code memory documentation](https://code.claude.com/docs/en/memory.md) for how each file is
loaded.

## The state file contract

`declutter radar` and signal S7 read the state file, so its format is a contract. Create it with
`declutter init` and keep this shape (Spanish headings shown in parentheses are accepted aliases):

```markdown
# State — <project>
Updated: 2026-11-04
Phase: <one line>

## Next (AI)            (Siguiente (IA))
- [ ] <task> — <source>

## Human decides        (Decide el humano)
- [ ] <decision> — <source>

## Blocked              (Bloqueado)
- <what> — <why>

## Resume at            (Dónde retomar)
- <path, branch or file>
```

Reading rules: file name `ESTADO.md` or `STATE.md` at the project root; header lines
`Updated:`/`Actualizado:` (the date must be `YYYY-MM-DD`) and `Phase:`/`Fase:`; sections are
matched by heading or alias, ignoring case and accents; items are lines starting with `- ` at
column 0 (indented sub-bullets are ignored); `[x]` counts as done; HTML comments are ignored; a
missing section is not an error, it shows as "—" in the radar. Rewrite the file at the end of every
task, and keep it under about 60 lines. A stale state file turns S7 yellow after 7 days and red
after 21.

## History is never deleted

What is no longer current moves to layer 4; it is never discarded. Cleaning context is a *move*,
not a deletion. Two practices keep this honest:

- **Distilled plus verbatim.** When you condense a long section into a short rule, keep the
  original text in `docs/history/YYYY-MM.md` and link to it from the rule. The agent reads the
  distilled version; a human (or the agent, on demand) can recover the exact original.
- **Coverage check.** Before and after a reorganization, every line of the old file must be found
  somewhere: in a layer, or in the verbatim history. Commit before you start so `git diff` is your
  proof. (`declutter reorg` automates this line-by-line coverage proof.)

## Sessions: one session, one task

Long sessions compact many times and each compaction loses detail (signal S6 counts them). Prefer
short sessions, each with one task: start by reading the state file, finish by updating it. The
state file, not the conversation, is what carries work between sessions. When a session has
compacted more than 5 times, close it, update `ESTADO.md`, and start a new one.

## Migrating a bloated CLAUDE.md by hand (before phase 2)

> Since version 0.2.0, `declutter reorg` does this migration for you, losslessly and with a proof:
> see [Lossless reorganization](#lossless-reorganization-declutter-reorg). These steps remain as a
> reference for the method.

Do this in a branch, with the file under version control.

1. **Measure.** `declutter measure` and note S1, S2 and S4. Commit the current state:
   `git commit -am "chore: snapshot before context cleanup"`.
2. **Scaffold.** `declutter init --dry-run`, then `declutter init`. It will not touch your existing
   `CLAUDE.md`; it creates `AGENTS.md`, the state file and the indexes if they are missing.
3. **Archive the original verbatim.** Copy the old `CLAUDE.md` to `docs/historial/AAAA-MM.md`
   (or `docs/history/YYYY-MM.md`), unchanged. From now on nothing can be lost.
4. **Sort paragraph by paragraph** with the decision test, moving text (not rewriting) into its
   layer: rules into `AGENTS.md`, zone rules into subfolder `AGENTS.md`, today's status into the
   state file, dated entries into the history file, justified choices into `D-NNN` files, traps
   into the traps file. Add one line per decision to the decision index.
5. **Replace `CLAUDE.md`** with `@AGENTS.md` plus the few Claude-only notes. Keep safety rules in
   `AGENTS.md`.
6. **Verify coverage.** Use `git diff` and search for distinctive phrases from the old file to
   confirm each one now lives somewhere. If a paragraph has no home, put it in history.
7. **Measure again.** `declutter measure` should show S1 and S4 green. Commit.
8. **Tell the agent.** Make sure `AGENTS.md` instructs agents to read the state file and the
   decision index first, and to update the state file at the end of each task.

## Lossless reorganization (`declutter reorg`)

`declutter reorg` does deterministically what the previous section describes by hand: it splits
`CLAUDE.md` (and `AGENTS.md`, if present) into the 5 layers without losing a single byte, and
proves it before touching the repository. It does not summarize or rewrite text: every original
line lands verbatim in exactly one destination, and safety rules are also copied verbatim into
`AGENTS.md`.

1. **Plan without writing anything.** `declutter reorg plan --dry-run` shows the unit table (heading,
   lines, bytes, proposed destination, safety copies, confidence and rule), the projection before
   and after (S1–S4, Codex % per chain, files Claude loads at start) and the warnings. If it finds
   a secret it exits with code 5 and writes nothing.
2. **Saved plan.** `declutter reorg plan` writes the manifest under `.declutter/reorg/<id>/` (ignored
   by git). `declutter reorg guide` walks the whole flow with a menu in the terminal.
3. **Your decisions.** No unit carrying an imperative rule leaves layer 1 unless you accept it:
   `declutter reorg set <id> <unit> --accept --reason "<why>"`. To change a destination,
   `--dest L1|L1c|L2:<dir>|L4|L5t|L5m`; to leave it where it is, `--keep`. A unit whose heading
   carries safety vocabulary stays whole in `AGENTS.md` and cannot be moved. `set` exits 0 when
   it saves the override even if other units are still pending (it prints "N pending
   confirmation(s) left: …"); it exits 4 when the override itself is invalid.
4. **Validate.** `declutter reorg check <id>` revalidates the manifest (exit code 4 if anything is off).
5. **Apply.** `declutter reorg apply <id>` creates the tag `declutter/pre-reorg-<id>` and the branch
   `declutter/reorg-<id>`, writes the files and runs the lossless proof again. With `--commit` it
   commits on that branch. It never merges or pushes: merging is your decision
   (`git switch <base> && git merge declutter/reorg-<id>`).
6. **Verify or undo.** `declutter reorg verify <id>` repeats the proof whenever you want;
   `declutter reorg undo <id>` brings the sources back without deleting the branch or the tag;
   `declutter reorg status` lists the runs; `declutter reorg prune <id>` deletes the local verbatim copies
   once you no longer need them.

Exit codes: `0` ok, `1` usage or git precondition, `2` unprocessable source, `3` Codex budget over
100 % or ratchet failure, `4` invalid plan or failed proof, `5` secret detected. When the plan exits
with 3, it lists the possible levers with their bytes (review low-confidence units, mark safety
false positives, raise the Codex budget, distill the decision cards (phase 2b, see
[Distilling the decision cards](#distilling-the-decision-cards-phase-2b)), revisit a protected
unit) and picks none.

**Project-scoped Codex budget.** Codex reads the `AGENTS.md` chain up to `project_doc_max_bytes`
(32 KiB by default, shared by the whole chain: past the limit it cuts the root file and drops the
zone `AGENTS.md` files). The value can be raised for one project with a `.codex/config.toml` at its
root, which Codex honors **only when the project is trusted** (Codex or the VS Code extension asks
the first time you open it; it is stored as `[projects."<path>"] trust_level = "trusted"` in
`~/.codex/config.toml`). If you pick the `lever_codex_budget` lever, the plan shows the concrete
command:

```sh
declutter reorg codex-budget -C <folder> --bytes 81920   # without --bytes: 65536
declutter reorg codex-budget -C <folder> --dry-run       # shows what it would write
```

It writes `project_doc_max_bytes = N` to `.codex/config.toml` (creating it if missing) and
`codex_budget_bytes = N` to `declutter.toml`, changing only that key and leaving the rest of each
file untouched; it refuses with exit code 1 a TOML file it cannot parse or a symlink. It never
touches `~/.codex`: at the end it prints the trust block for you to add if needed. If git ignores
`.codex/config.toml`, it warns (Codex still reads it, but it will not be committed). The `--bytes`
the plan suggests is the smallest multiple of 16 KiB that covers the largest projected chain plus
10 %, with a 64 KiB minimum.

In Claude Code, `/declutter:declutter-reorg` walks these steps working only from the plan's JSON and
asks for your OK before each write, the commit and every decision.

### Distilling the decision cards (phase 2b)

`apply` leaves every safety rule verbatim in `AGENTS.md` (section «Safety invariants (literal,
pending distillation)») and, for units that went to layer 4, a decision card in
`docs/decisions/D-NNN-<slug>.md` (`docs/decisiones/` in Spanish) whose sections say `PENDING`.
Distilling a card means filling it from its literal and replacing **all** its verbatim copies in
`AGENTS.md` by the card's «Invariant for AGENTS.md», one shorter entry under «Safety invariants
(distilled)» that links to the card and to the verbatim literal in the history. Nothing is lost:
the literal stays in `docs/history/` with its sha and in the card's excerpts. Copies of units
without a card (`no_card`) stay verbatim.

1. **Fill the card** from its literal only (in Claude Code, `/declutter:declutter-distill` does it one
   card at a time): «not stated» when the literal does not say it, never invented content.
2. **Check.** `declutter reorg distill-check <id> [--card D-NNN] [--json]` is read-only. A card is
   `ready` when nothing is pending, the invariant keeps every number, date, path, identifier, URL
   and code span of the copies and adds none that is not in the unit, keeps the same count of each
   obligation class (`must_not`, `never`, `always`, `must`) and the categories' safety vocabulary,
   has no headings, `@path` imports, markers or CR, and the entry is shorter in bytes and no longer
   in lines than the copies it removes. Reasons give class, line and column, never text. Exit codes:
   `0` all ready or distilled, `4` some card not ready, `1` usage, `2` unreadable card or control
   file, `5` secret.
3. **Apply in a terminal.** `declutter reorg distill-apply <id> --card D-NNN` works on the new branch
   `declutter/distill-<id>-<D-NNN>` after the tag `declutter/pre-distill-<id>-<D-NNN>`. It lists what will
   change and asks for a reason and the word `confirmo` (en: `confirm`); without a TTY it exits 1
   and writes nothing, and `--yes` never skips it. `--dry-run` builds and proves without writing.
   It pins the new entry and the distilled card (pin roles `invariant` and `card`), appends a
   `distill` event to `.declutter/ratchet.log` and, when the distillation lowers S1 or the Codex
   percentage, a `tighten` event that lowers the ratchet maximums (they never go up).
4. **Commit** as a separate step: `declutter reorg distill-apply <id> --card D-NNN --commit` (add `--yes`
   without a TTY; it only confirms the commit). It never merges or pushes.
5. **Verify.** `declutter reorg verify <id> --strict` and `declutter ratchet check` must exit 0 (states
   `distilled`, `card_distilled`, `control_appended`).
6. **Undo.** `declutter reorg distill-undo <id> --card D-NNN` restores the exact previous bytes. It is
   LIFO: only the last distillation of the run can be undone.

The last line of `distill-apply` and `distill-undo` is `<id> D-NNN <state>` (`written`,
`committed`, `undone` or `planned`); errors go to stderr with keys `distill.<key>`.

**Line budget (R1).** The first distillation of a run adds a 4-line region (blank line, title,
marker and closing marker); after that, entries are never longer in lines than the copies they
replace. If the project is above `S1_max + 1` lines before the first distillation, `distill-apply`
exits 3 without writing anything; raising the maximum is your decision (`declutter ratchet relax`,
confirmed in a terminal).

**Limit.** Distillation alone does not bring layer 1 to 12 KiB: it does not touch the primary
layer-1 units, which remain your decision.

### Automatic distillation (phase 3A)

When many cards are ready, `declutter reorg distill-auto <id>` applies all of them in one go, without
confirming card by card. It works on a new branch `declutter/distill-auto-<id>-<n>`, after the tag
`declutter/pre-distill-auto-<id>-<n>`, with one commit per card. It is a two-step flow:

1. **Fill.** In Claude Code, `/declutter:declutter-auto` fills and checks each card (up to 3
   rounds per card, from its literal only) and leaves untouched the ones that do not get ready.
2. **Apply.** The skill launches `declutter reorg distill-auto <id> --json` once; you can also run it
   yourself. It never reads standard input and never asks for confirmation: it prints one line per
   card. `--skip D-NNN` leaves a card out and `--dry-run` runs everything on a temporary copy.

**Stopping.** Ctrl+C (or the termination signal) stops the batch cleanly with exit 130: if the card
in progress has already started writing, it is completed; otherwise it is cancelled without writing
anything. The ones already applied stay on the branch, and the cards that did not get ready stay
edited but not applied. A card that fails is restored and skipped; a secret, a concurrent edit or a
failed global condition halts the batch. The ratchet is never relaxed by itself: if it exits 3,
raising the maximum is still your decision.

**Report.** It is saved at `.declutter/distill/<id>/auto-<n>/informe.md` (seal GUIDE), built only
from the journals and measurements: applied and skipped cards, `AGENTS.md` before and after, the
ratchet and the commands to merge and to undo. With `--json`, stdout carries only that report as
JSON and the notices go to stderr.

**Merging and undoing.** The engine never merges or pushes: the merge command is in the report and
you run it. To undo the whole batch: `declutter reorg distill-undo <id> --auto <n>` (`--card` and
`--auto` are mutually exclusive). If the batch was not merged, it goes back to the base branch; if
it was merged, it asks for a terminal and the confirmation word, and reverts its commits. When
undoing, the filled cards go back to the filled state they had before the batch (uncommitted
modifications), so the AI's work is not lost and you can rerun with `--skip D-NNN`. If someone edited
a card afterwards, it is left alone and one line names it.

## Ratchet

After a reorganization, `declutter reorg apply` records a baseline in `.declutter/ratchet.json` and
pins the safety copies in `.declutter/pins.json` (both go into the commit). `declutter ratchet check`
compares the project with that baseline and exits with code 3 if `CLAUDE.md` and `AGENTS.md` grow
past the maximum, the Codex budget goes up, a log-style dated heading comes back, a zone
`AGENTS.md` exceeds its line limit, a pinned copy changed, or a verbatim block in the history,
`docs/trampas.md` or `docs/mapa.md` was altered. Each failure comes with its fix. Maximums only go
down; to raise one on purpose there is `declutter ratchet relax`, and to release or refresh a pin,
`declutter ratchet unpin` and `declutter ratchet repin`. All three ask for confirmation in the terminal and
are logged in `.declutter/ratchet.log`. Since 0.3.0, `declutter reorg verify <id> --strict` accepts
control files that only gained audited events (`control_appended`): after a `declutter ratchet unpin`
or a distillation it exits 0 instead of 4. A `ratchet.json` changed by `relax` is still reported as
`control_edited`.

**Manual first.** Run `declutter ratchet check` before each commit (`--staged` checks what is in the
index). The tool does not install any hook.

**Formatters and line endings.** History literals are verified byte for byte. We recommend, as
your decision, excluding `docs/historial/**` (or `docs/history/**`), `docs/trampas.md` and
`docs/mapa.md` from formatters and hooks that rewrite Markdown (`trailing-whitespace`,
`end-of-file-fixer`, `mixed-line-ending`, prettier on `*.md`) and, if your repository normalizes
line endings, marking those paths `-text` in `.gitattributes`.

**`.gitattributes` with `* text=auto eol=lf`.** `plan` and `apply` no longer
stop on that line (or on `core.autocrlf=input`) when no file involved has CRLF or any `\r` byte:
git leaves those bytes unchanged in both directions. You will see the warning
`git_normalizes_lf_safe` with the path and the attribute. With `eol=crlf`, `core.autocrlf=true`,
`text=auto` without `eol`, filters or content with `\r`, they still exit with 1.

**Optional hook.** If you later want git to check it for you, create `.git/hooks/pre-commit` by
hand (executable) with:

```sh
#!/bin/sh
exec declutter ratchet check --staged
```

In CI, the equivalent step is `declutter ratchet check`, which exits with 3 on failure, like
`declutter measure --fail-on red`.

## Keeping it tidy

- Run `declutter measure --fail-on red` in CI, or add the status line and the `SessionStart` hook from
  the README, so problems surface before they cost anything.
- Use `declutter radar` weekly to see which projects have stale or missing state files.
- Tune thresholds in `declutter.toml` if your project legitimately needs more room, but do it on
  purpose and write down why in a decision file.
