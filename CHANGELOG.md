# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-11-12

First public release. Automatic distillation.

### Added
- `declutter reorg distill-auto ID [-C DIR] [--skip D-NNN ...] [--json] [--dry-run] [--lang es|en]`:
  applies every `ready` card, in `D-NNN` order, on one new branch
  `declutter/distill-auto-<id>-<n>` after the tag `declutter/pre-distill-auto-<id>-<n>`, one commit per
  card with the trailer `Declutter-Distill-Batch: <id>/<n>`, without asking anything and without a
  TTY (it never reads stdin). One line per card; Ctrl+C or SIGTERM stops it cleanly (exit 130).
  A card that fails is restored and skipped; a secret, a concurrent edit or a failed global
  condition halts the batch. With `--json`, stdout carries only the report as JSON and the
  notices go to stderr. The report
  (`.declutter/distill/<id>/auto-<n>/informe.md`, seal GUIDE) is built by the engine from
  journals and measurements and carries the merge and undo commands. Never merges, pushes or
  relaxes the ratchet.
- `declutter reorg distill-undo ID --auto N`: undoes a whole batch (back to the base branch when not
  merged; `git revert` of its commits, after the confirmation word in a TTY, when merged).
  `--card` and `--auto` are mutually exclusive and one of them is required.
- `/declutter:declutter-auto` plugin skill: fills and checks every card (up to 3 rounds each), then
  runs `distill-auto --json` and relays the report as is. `/declutter:declutter-distill` keeps the
  one-card-at-a-time flow.
- `/declutter:declutter` plugin skill, the entry point: measures, offers `/declutter-reorg` when no
  reorganization exists or `/declutter-auto` when one does, and closes with the figures the tools
  print. It never merges, pushes or relaxes the ratchet.

### Changed
- `distill-undo --card` on a card applied by a batch exits 1 and names the batch.
- The distill journal may carry `batch` (absent for 0.3.0 journals).

## [0.3.0] - 2026-11-05

Distillation of the decision cards (not published).

### Added
- `declutter reorg distill-apply ID --card D-NNN [-C DIR] [--commit] [--yes] [--no-git] [--dry-run]
  [--lang es|en]`: replaces, one card per call, every verbatim G1 copy of the card
  in `AGENTS.md` by one compact entry (header with the pointer to the history anchor, the card,
  the source range and the unit's sha prefix, then the invariant) under the new region «Invariantes
  de seguridad (destiladas)» / «Safety invariants (distilled)». It runs as a journaled transaction
  on a new branch `declutter/distill-<id>-<D-NNN>` after the tag `declutter/pre-distill-<id>-<D-NNN>`, proves
  the result before and after writing, and always asks in a TTY for a reason and the word
  `confirmo` (en: `confirm`): without a TTY it exits 1 and writes nothing, and `--yes` never skips
  it (it only resumes the commit over a `written` journal). The card's state line, its index row
  and the pointers get the distilled state. Idempotent on an already distilled card. Never merges
  or pushes. The last stdout line is `<id> D-NNN <state>`; errors go to stderr as `distill.<key>`.
- `declutter reorg distill-undo ID --card D-NNN [-C DIR] [--yes] [--lang es|en]`: restores the
  exact previous bytes of every output, including `pins.json` and `ratchet.log`; LIFO (only the last
  distillation of a run); never deletes the branch or the tag.
- `declutter reorg status` lists the distillations of each run with their state and date.
- New pin roles `invariant` (the distilled entry in `AGENTS.md`) and `card` (the distilled card) in
  `.declutter/pins.json` (schema still `declutter.pins/1`), and new `ratchet.log` events
  `distill` (old and new pins, placements changed, files and line deltas, the human's reason) and
  `tighten` (ratchet maximums lowered to what the distillation measured; they never go up). A
  `distill` event releases a `copy` pin only when all its new pins exist, so deleting the entry or
  editing the distilled card fails R5. Readers of 0.2.0 fail closed (exit 2) on the new values.
- `reorg verify` posterior states `distilled`, `card_distilled` and `control_appended`.
- `/declutter:declutter-distill` plugin skill: fills one card at a time strictly from its verbatim
  literal («no consta» when the literal does not say it), checks it with `distill-check`, shows the
  before and after, and leaves `distill-apply` to the human in a TTY; the commit is a separate
  approval. `/declutter:declutter-reorg` now points to it at the close.
- Guide section "Distilling the decision cards (phase 2b)" (English and Spanish).
- `declutter reorg distill-check ID [-C DIR] [--card D-NNN] [--json] [--lang es|en]`: read-only check of whether each decision card's «Invariante para AGENTS.md» may replace
  the card's literal G1 copies in `AGENTS.md`. Conditions: nothing pending (a), anchor tokens in
  both directions (b), deontic classes (c), history sha (d) and the structural and safety
  conditions e1-e10 (balanced Markdown, no headings/markers/`@path`/CR, G5, categories kept,
  shorter entry, copies located and pinned, generated lines intact). Copies of units without a
  card are listed as `no_card` and never change the exit code (0 ready, 4 not ready, 1 usage,
  2 unreadable control file or card, 5 secret). JSON schema `declutter.distill-check/1` with
  `lang` and `paths_lang`. New modules `reorg/anchors.py` and `reorg/distill.py`;
  `section_titles.json` gains the distilled-state keys used by the distillation.
- `declutter reorg codex-budget [-C DIR] [--bytes N] [--dry-run] [--lang es|en]`: the concrete action
  of the `lever_codex_budget` lever. It writes `project_doc_max_bytes = N` to the project's
  `.codex/config.toml` and `codex_budget_bytes = N` to `declutter.toml` (default N = 65536),
  setting only that key and keeping every other byte; it refuses (exit 1) an unparseable TOML file
  or a symlink, warns when git would ignore `.codex/config.toml`, never touches `~/.codex` and
  prints how to mark the project as trusted, which Codex requires before it honors the file.
  When `plan`/`check` exit 3 with `codex_budget`, the levers list shows this command with a
  suggested `--bytes` (smallest multiple of 16 KiB >= 1.1 x the largest projected chain, at least
  64 KiB), also in the JSON report as `lever_commands` (additive).

### Changed
- Accepted behaviour change from 0.2.0: `declutter reorg verify ID --strict` now
  exits 0 after an audited `declutter ratchet unpin` (or a distillation), reporting the control files as
  `control_appended`, because the only change is appended audited events (the run's
  `ratchet.log` is an exact prefix of the current one, the run's pins a prefix of the current list).
  Before, it exited 4 with `control_edited`. A `ratchet.json` changed by `relax` is still
  `control_edited`.
- Distilled entry: compact format with the pointer on the header
  line, and `distill-check` (`not_shorter`) also requires the entry to be no longer in lines than
  the copies it removes, so a distillation never grows S1 beyond the one-time 4-line region; if
  the project is above `S1_max + 1` before the first distillation, `distill-apply` exits 3 (R1)
  without writing, and raising the maximum stays a human decision (`declutter ratchet relax`).
- `reorg set` exits 0 when the override is saved and the only
  remaining issues are pending confirmations of other units; it prints "N pending
  confirmation(s) left: ..." instead of listing them as failures. It still exits 4 when the
  override itself is invalid (or its own unit is still pending) and 3 for `codex_budget`.
  `check` and `apply` are unchanged.
- The reorg JSON report (`report.json`, `--json`) gains the top-level fields `lang` and
  `paths_lang` (additive; the schema stays `declutter.reorg-report/1`).
- S3: with no `AGENTS.md` in the Codex chain but a root `CLAUDE.md` (or
  `.claude/CLAUDE.md`) with instructions, S3 is now yellow with no value (detail
  `codex_reads_nothing`, action `act.codex_reads_nothing`) instead of `n/a`, because Codex does
  not read `CLAUDE.md`. With `AGENTS.md`, S3 measures against `project_doc_max_bytes` from the
  project's `.codex/config.toml` when set (what Codex applies in a trusted project) and adds a
  `budget_mismatch` note when it differs from `codex_budget_bytes`.
- The `lever_codex_budget` text now describes the verified project-scoped route
  (`.codex/config.toml` + `declutter.toml`, trusted project) instead of the global Codex config.
- `reorg plan` and `reorg apply`: a path whose attributes are
  `text=auto|set` with `eol=lf` (and no `filter`, `ident` or `working-tree-encoding`), or a
  repository with `core.autocrlf=input`, no longer exits 1 with `git_normalizes` when every
  relevant byte is CR-free (sources on disk, bytes to write, current and new bytes of files that
  are extended or backed up); `plan` reports it as the warning `git_normalizes_lf_safe`.
  `eol=crlf`, `core.autocrlf=true`, `text` without `eol`, filters and any `\r` still exit 1.

## [0.2.0] - 2026-11-04

Lossless reorganization and the ratchet (not published).

### Added
- `declutter reorg` (`guide`, `plan`, `check`, `set`, `apply`, `verify`, `undo`, `status`, `prune`):
  deterministic, lossless reorganization of the root `CLAUDE.md` (and `AGENTS.md`) into the 5
  layers. Every original line lands verbatim in exactly one destination; a byte-level proof
  (checks C0-C9) runs before writing, after writing and after the commit. Safety guards G1-G9 keep
  safety rules verbatim in `AGENTS.md`, a secret scan stops the run with exit code 5, and units
  carrying imperative rules leave layer 1 only after an explicit acceptance. `apply` works as a
  journaled transaction on a new branch `declutter/reorg-<id>` after a tag `declutter/pre-reorg-<id>`;
  `undo` restores the sources; nothing is ever merged or pushed. Output (text and JSON) carries
  metadata only.
- `declutter ratchet` (`check`, `relax`, `unpin`, `repin`): manual guard against regrowth, with a
  baseline written by `reorg apply`, pinned safety copies, rules R1-R6 (exit code 3) and an
  audited log of every relaxation. No hook is installed; the guide documents an optional
  one-line pre-commit hook and the CI step.
- `/declutter:declutter-reorg` plugin skill: drives `declutter reorg` from the plan's JSON only, asking for
  the user's OK before every decision, write, apply and commit. `/declutter:declutter-hygiene` suggests it
  when S1 or S4 is red.
- `[reorg]` table in `declutter.toml` (`l1_max_bytes`, `l1_max_lines`, `zone_max_lines`,
  `codex_warn_pct` and `[reorg.lexicon]`, whose lists add to the built-in lexicons).
- Guide sections "Lossless reorganization (`declutter reorg`)" and "Ratchet" (English and Spanish).
- Exit codes `4` (invalid plan or failed proof) and `5` (secret detected).

### Changed
- S4 counts `##` headings with the CommonMark-aware parser shared with `reorg` (fenced code,
  HTML comments and front matter are skipped).
- Version 0.2.0. Generated reorganization text ships as package data (`templates_reorg`), apart
  from the `declutter init` templates.

### Fixed
- The `~/.claude/projects/<slug>` folder is now computed like Claude Code does (every
  non-alphanumeric character becomes `-`), so memory (S2, S5) and compactions (S6) are found for
  paths with spaces, accents, `_` or `.`.
- Reads skip FIFOs and other non-regular files, cap decoding at 8 MiB (larger files are measured
  by streaming), never follow symlinked folders while walking, and refuse symlinks whose target
  looks like a secret or leaves the project.
- The measurement cache is keyed by root, cwd and session folder, and its fingerprint covers every
  file of the Claude and Codex load sets, including `~/.claude/CLAUDE.md`, ancestors, global rules
  and `@import` targets.
- `declutter radar --discover <relative dir> --save` stores absolute paths; `declutter init .` fills in the
  project name; `declutter init` survives a dangling `docs` symlink.
- Radar: last activity looks at every local branch and is cached; the `Blocked` section of the
  state file is listed; green rows show `—` as worst signal; registry problems are reported on
  stderr; the `session` field selects the Claude session folder.
- `declutter init` on a large existing `CLAUDE.md` prints the full measurement and warns when it does
  not import `@AGENTS.md`.
- S2 labels each loaded file by its path; S9 matches `plan`/`traspaso`/`handoff` as whole words;
  signal names are the same in text and JSON; hook and status line use the configured language
  and the namespaced `/declutter:declutter-hygiene` command.

## [0.1.0] - Unreleased

First version (not published).

### Added
- `declutter measure`: nine traffic-light signals (S1-S9) computed from files only, with text and JSON
  output, `--lang es|en` and `--fail-on yellow|red` (exit code 3) for CI.
- `declutter radar`: overview of registered projects (traffic light, last activity, uncommitted and
  unpushed work, open items from the state file), with text, `--md` and `--json` output and
  `--discover DIR` / `--save` to find and register projects.
- `declutter init`: creates only the missing context files (`AGENTS.md`, `CLAUDE.md`, state file,
  decision and history indexes, `declutter.toml`) in Spanish or English; never overwrites;
  `--dry-run`.
- `declutter hook session-start`: one-line warning when the project is in the red; never blocks and
  always exits 0.
- `declutter statusline`: context percentage plus the worst signal, for Claude Code's status line.
- Claude Code plugin with `/declutter:declutter-check`, `/declutter:declutter-hygiene` and `/declutter:declutter-init`
  skills and a `SessionStart` hook; marketplace manifest.
- Load-set model for Claude Code (including `@imports` up to 4 hops, rules, `MEMORY.md` cap) and
  Codex (shared `AGENTS.md` byte budget).
- Layered configuration: defaults, then global file, then `declutter.toml` in the project.
- Documentation in English and Spanish, including the 5-layer method guide.

### Security
- Read-only engine, standard library only, no network access. Files that look like secrets are
  never read.
