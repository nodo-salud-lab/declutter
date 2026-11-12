---
name: declutter-distill
description: Distill the decision cards left by /declutter-reorg, one card at a time (for the automatic batch, use /declutter-auto). Fills each card's What / Why / Rejected alternative / Pitfall / Invariant strictly from its verbatim literal («no consta» when the literal does not say it), checks it with the deterministic `declutter reorg distill-check <id>`, and lets the human replace the card's verbatim safety copies in AGENTS.md by the shorter invariant with `declutter reorg distill-apply <id> --card D-NNN` (TTY confirmation, new git branch, LIFO undo). Every choice, apply and commit is the user's decision. Use after /declutter-reorg when the closing summary lists decision cards to distill, or when the user runs /declutter-distill.
argument-hint: "[-C DIR]"
---

# declutter-distill

Destilado de las fichas de decisión que dejó `/declutter-reorg`, **una ficha por vez**. La herramienta
prueba; la IA de la sesión redacta solo desde el literal; el humano decide y aplica.

Unlike `/declutter-reorg`, this skill **reads content**, but only two spans per card: the card file
itself (its sections and its `## Extractos literales (copia verificada)`) and the unit's verbatim
block in the history (between `<!-- declutter:src … unit=<U> plan=<id> -->` and `<!-- declutter:end <U> -->`).
Never open `CLAUDE.md`, `AGENTS.md`, other cards or any other file, and never paste the literal into
the conversation.

## Preflight

Run `command -v declutter`. If it is missing, reply with exactly one install line and stop:

`pipx install git+https://github.com/<owner>/declutter` (not on PyPI yet; replace `<owner>` with the account that hosts the repository)

Add `-C DIR` to every command below if the user passed one.

## Steps

1. **Find the run.** Run `declutter reorg status` to get the run id (`<id>`, the first word of each
   run line) and the distillations already done (one indented line per card with its state). The
   run must be `committed` (or `written` with `--no-git`); otherwise `distill-apply` exits 1 with
   `distill.run_not_committed` and the user must finish `/declutter-reorg` first.
2. **List the cards.** Run `declutter reorg distill-check <id> --json`. Exit code 5 means a secret was
   detected: report it by file and line only and stop. Codes 1 and 2: relay the issues and stop.
   Summarize in Spanish with the stamp **GUÍA**: cards `ready`, `not_ready` (with their
   `reasons[].key`), `distilled`, the `no_card` copies with their bytes (they stay verbatim in v1),
   and `bytes_removed` / `bytes_added` per card. Exit code 4 here is normal while cards are still
   `PENDIENTE` (`pending_what`, `pending_invariant`).
3. **The user picks one card.** You may sort the cards by bytes, but never choose one yourself.
4. **Read only the two spans.** From the JSON take the card's `path` and `unit`. Read that card.
   Its `## Origen` line «Literal…» gives the history file and anchor, the range in the original
   source and the unit's sha256; read only the block between the unit's `declutter:src` and `declutter:end`
   markers in that history file. Source line `L` of the range `start-end` is line `L - start + 1`
   of that block (the block starts with the unit's `##` title).
5. **Fill the card, strictly from the literal.** Edit only the bodies of `## Qué`, `## Por qué`,
   `## Alternativa descartada`, `## Trampa` and `## Invariante para AGENTS.md` (in English cards:
   `## What`, `## Why`, `## Rejected alternative`, `## Pitfall`, `## Invariant for AGENTS.md`).
   Do not touch line 1 (title), line 2 (`estado: pendiente-destilar`), the `declutter:gen` markers,
   `## Origen` or the literal excerpts. Rules:
   - Everything comes from the literal. When the literal does not say it, write «no consta»
     (en: «not stated»). Never invent a reason, an alternative, a pitfall or a fact.
   - «Qué» and the invariant must not be left `PENDIENTE`, and the invariant cannot be empty or
     only «no consta»: a rule that says nothing cannot replace a literal.
   - The invariant replaces **all** the copies of the card at once (one invariant per card). Copy
     verbatim every number, date, path, identifier, URL and backtick span of the copied literal
     (dates and numbers exactly as written: `07-10-2026` is not `2026-10-07`). Do not add any of
     those that is not in the unit's literal.
   - Keep the same count of each obligation class as the copies: `must_not` (no debe, no se debe,
     prohibido, must not, do not, shall not, forbidden), `never` (nunca, jamás, never), `always`
     (siempre, always), `must` (debe, obligatorio, must, shall, required). Not one more, not one
     less.
   - Keep the safety vocabulary of the copies' categories, so a later reorganization and
     `repin` still recognize it as a safety rule.
   - No headings (`#`, setext, `---`), no `@path` imports, no `<!-- declutter:` markers, no unclosed
     fence or comment, no CR bytes.
   - Shorter: the distilled entry (one generated header line + the invariant + a blank line) must
     have fewer bytes and no more lines than the copies it removes (each with its header and blank
     line). In practice: the invariant has no more lines than all the copies together and clearly
     fewer bytes.
6. **Check.** Run `declutter reorg distill-check <id> --card D-NNN --json`. If it is not `ready`, fix the
   card according to each reason (reasons give class, line and column, never text: re-read the
   literal to locate them) and run it again, at most three rounds. Never weaken a rule to pass
   (do not drop an obligation, an anchor or a category word, and do not edit the literal, the
   history or the check). If it is still not ready, show the reasons to the user and stop.
   While the card is filled but not distilled, `declutter reorg verify <id> --strict` exits 4
   (`generated_edited`); that is expected.
7. **Before / after, stamped BORRADOR.** Show `git diff -- <card path>` (the card only) and, from the
   JSON, the copies that will disappear (block, pin, source range, lines, bytes, categories) and the
   bytes removed and added. Optionally run `declutter reorg distill-apply <id> --card D-NNN --dry-run`
   to print the confirmation summary without writing anything durable. Ask the user to review the
   content and confirm or correct it.
8. **Apply (the human, in a TTY).** With the user's OK, do not run it yourself: ask the user to run
   `! declutter reorg distill-apply <id> --card D-NNN` in the terminal. It lists the copies, pins, files,
   bytes and lines of `AGENTS.md` before and after, then asks for a reason and for the word
   `confirmo` (en: `confirm`). Without a TTY it exits 1 (`distill.no_tty`) and writes nothing;
   `--yes` never skips this confirmation, so never pass `--yes` to get around it. It writes on the
   new branch `declutter/distill-<id>-<D-NNN>` after the tag `declutter/pre-distill-<id>-<D-NNN>`, and may
   offer the commit right away.
   - **R1 caveat:** the first distillation of a run adds a 4-line region
     («Invariantes de seguridad (destiladas)»). If the project is already above `S1_max + 1`,
     `distill-apply` exits 3 (R1) without writing anything. Raising the maximum is the user's
     decision: the user raises it with the `ratchet relax` subcommand (`--s1-to`, with a reason,
     confirmed in a TTY, G9); never propose it as the fix on your own.
9. **Commit (separate approval).** Run `declutter reorg status <id>` to read the card's state. If it is
   `written`, ask whether to commit; only with an explicit OK, run
   `declutter reorg distill-apply <id> --card D-NNN --commit --yes` (it resumes the `written` journal and
   proves the disk again; this shell has no TTY, so `--yes` is required here and only confirms the
   commit). Never run `distill-apply` without `--commit`. Never merge and never push: merging into
   the base branch is the user's decision.
10. **Verify.** Run `declutter reorg verify <id> --strict` and `declutter ratchet check`. Both must exit 0
    (states `distilled`, `card_distilled`, `control_appended`). Relay any failure by its key; do not
    work around it.
11. **Close.** Report the bytes removed from `AGENTS.md`, the cards left, and how to undo:
    `declutter reorg distill-undo <id> --card D-NNN`. Undo is LIFO: only the last distillation of the run
    can be undone (otherwise exit 1 naming the later card). Run it only with the user's explicit
    OK; if the distillation was already merged, it reverts with a confirmation, so ask the user to
    run `! declutter reorg distill-undo <id> --card D-NNN` in the terminal. Then the next card starts again
    at step 3.

## Auto mode

The automatic mode (fill every pending card, then run `distill-auto` once) lives in its own skill:
`/declutter-auto`. This skill only distills one card at a time.

## Outputs

- `distill-apply` and `distill-undo` print as their last stdout line `<id> D-NNN <state>`, where
  `<state>` is `written`, `committed`, `undone` or `planned` (nothing written).
- Errors go to stderr, one per line, with keys `distill.<key>` (for example `distill.no_tty`,
  `distill.already_applied`, `distill.run_not_committed`), metadata only.
- Exit codes: 0 ok (also idempotent «already distilled»), 1 usage, precondition, no TTY, not
  confirmed, LIFO or `already_applied`, 2 unreadable or invalid control file or card, 3 ratchet,
  4 `not_ready`, failed proof or `concurrent_edit`, 5 secret.

## Rules

- Read only the chosen card and its unit's history block. Never read `CLAUDE.md`, `AGENTS.md` or
  other cards, and never paste literal text into the conversation.
- Never invent: «no consta» when the literal does not say it.
- Never choose the card, the reason for the event, a `ratchet relax` or a merge on your own.
- Every write needs the user's explicit OK; the apply is always the human's, in a TTY.
- Never merge, push, delete branches or tags, or edit `.declutter/` files by hand.
