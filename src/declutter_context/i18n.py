"""Message catalog (English and Spanish) and the ``t`` lookup helper."""

from __future__ import annotations

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "level.green": "green",
        "level.yellow": "yellow",
        "level.red": "red",
        "level.na": "n/a",
        "sig.S1": "Root instruction lines",
        "sig.S2": "Startup tokens (Claude)",
        "sig.S3": "Codex budget",
        "sig.S4": "Changelog ratio",
        "sig.S5": "Memory cap",
        "sig.S6": "Compactions last session",
        "sig.S7": "State freshness",
        "sig.S8": "Branches & worktrees",
        "sig.S9": "Docs without status",
        "unit.lines": "lines",
        "unit.tokens": "tokens",
        "unit.%": "%",
        "unit.days": "days",
        "unit.count": "count",
        "act.split_claude_md": (
            "Move history and zone rules out of CLAUDE.md/AGENTS.md (see guide: 5 layers)"
        ),
        "act.trim_startup": (
            "Cut what loads at startup: split large files, drop unused @imports and rules"
        ),
        "act.codex_budget": (
            "Shrink the AGENTS.md chain below the Codex project-doc budget (32 KiB by default)"
        ),
        "act.codex_reads_nothing": (
            "Codex reads no instructions here (it ignores CLAUDE.md): create AGENTS.md, "
            "e.g. with declutter reorg plan"
        ),
        "act.move_changelog": (
            "Move dated changelog entries from instruction files into docs/historial/"
        ),
        "act.memory_index": (
            "Shorten MEMORY.md: keep one line per entry and move detail into topic files"
        ),
        "act.new_session": (
            "Start a new session with a short handoff: this one compacts too often"
        ),
        "act.update_state": (
            "Update the state file (ESTADO.md) with the current phase and next steps"
        ),
        "act.prune_branches": "Delete merged branches and remove stale worktrees",
        "act.mark_docs": "Add a status: header to old handoff/plan docs (open, done or archived)",
        "next_actions": "Next actions",
        "tokens_note": "token counts are estimates: bytes / {bpt}",
        "col.level": "Lvl",
        "col.project": "Project",
        "col.session": "Session",
        "col.activity": "Last activity",
        "col.dirty": "Dirty",
        "col.ahead": "Ahead",
        "col.next_ai": "Next (AI)",
        "col.human": "Human",
        "col.worst": "Worst signal",
        "radar.no_state": "no state file: run declutter init or /declutter:declutter-check --deep",
        "radar.title": "Project radar",
        "radar.generated": "Generated",
        "radar.next_ai": "Next (AI)",
        "radar.human": "Human decides",
        "radar.blocked": "Blocked",
        "radar.open_items": "Open items",
    },
    "es": {
        "level.green": "verde",
        "level.yellow": "amarillo",
        "level.red": "rojo",
        "level.na": "n/a",
        "sig.S1": "Líneas de instrucción raíz",
        "sig.S2": "Tokens al inicio (Claude)",
        "sig.S3": "Presupuesto Codex",
        "sig.S4": "Proporción de bitácora",
        "sig.S5": "Tope de memoria",
        "sig.S6": "Compactaciones de la última sesión",
        "sig.S7": "Frescura del estado",
        "sig.S8": "Ramas y worktrees",
        "sig.S9": "Documentos sin estado",
        "unit.lines": "líneas",
        "unit.tokens": "tokens",
        "unit.%": "%",
        "unit.days": "días",
        "unit.count": "cantidad",
        "act.split_claude_md": (
            "Saca la historia y las reglas por zona de CLAUDE.md/AGENTS.md (ver guía: 5 capas)"
        ),
        "act.trim_startup": (
            "Reduce lo que se carga al inicio: divide archivos grandes "
            "y quita @imports y reglas sin uso"
        ),
        "act.codex_budget": (
            "Acorta la cadena de AGENTS.md bajo el presupuesto de Codex (32 KiB por defecto)"
        ),
        "act.codex_reads_nothing": (
            "Codex no lee ninguna instrucción aquí (ignora CLAUDE.md): crea AGENTS.md, "
            "por ejemplo con declutter reorg plan"
        ),
        "act.move_changelog": (
            "Mueve las entradas de bitácora con fecha desde los archivos de instrucción "
            "a docs/historial/"
        ),
        "act.memory_index": (
            "Acorta MEMORY.md: una línea por entrada y el detalle en archivos por tema"
        ),
        "act.new_session": (
            "Abre una sesión nueva con un traspaso breve: esta compacta demasiado seguido"
        ),
        "act.update_state": (
            "Actualiza el archivo de estado (ESTADO.md) con la fase actual y los próximos pasos"
        ),
        "act.prune_branches": "Borra las ramas ya fusionadas y quita los worktrees obsoletos",
        "act.mark_docs": (
            "Agrega un encabezado estado: a los traspasos/planes antiguos "
            "(abierto, hecho o archivado)"
        ),
        "next_actions": "Siguientes acciones",
        "tokens_note": "los tokens son estimaciones: bytes / {bpt}",
        "col.level": "Niv",
        "col.project": "Proyecto",
        "col.session": "Sesión",
        "col.activity": "Última actividad",
        "col.dirty": "Sin commit",
        "col.ahead": "Sin subir",
        "col.next_ai": "Sigue (IA)",
        "col.human": "Humano",
        "col.worst": "Peor señal",
        "radar.no_state": "sin ESTADO.md: usa declutter init o /declutter:declutter-check --deep",
        "radar.title": "Radar de repositorios",
        "radar.generated": "Generado",
        "radar.next_ai": "Sigue (IA)",
        "radar.human": "Decide el humano",
        "radar.blocked": "Bloqueado",
        "radar.open_items": "Ítems abiertos",
    },
}


# Phase 2a (``declutter reorg`` and ``declutter ratchet``). Kept apart from the phase-1 catalog and
# merged below; both languages must define the same keys (tests enforce it).
_REORG_EN: dict[str, str] = {
    # issue keys: validity rules (spec §6.4)
    "reorg.check.1": "stale plan: a source changed, appeared or disappeared",
    "reorg.check.2": "a source is not readable as recorded",
    "reorg.check.3": "invalid destination",
    "reorg.check.4": "protected unit (G2) must stay in L1",
    "reorg.check.5": "safety block leaves L1 without a literal copy or guard_ack",
    "reorg.check.6": "publishable/device block cannot go to L2 or L1c (G3)",
    "reorg.check.7": "a unit with safety categories cannot go to L1c",
    "reorg.check.8": "an override needs a reason (guard_ack: at least 10 characters)",
    "reorg.check.9": "Codex chain over 100% of its budget",
    "reorg.check.10": "decision card id already taken",
    "reorg.check.11": "zone CLAUDE.md does not import its AGENTS.md",
    "reorg.check.12": "ambiguous or contradictory override",
    "reorg.check.13": "a unit with @imports leaves L1 without an explicit destination",
    "reorg.check.14": "an AGENTS.md unit goes to L1c or a zone without an explicit destination",
    "reorg.check.15": "L1c unit not confirmed (key c or set --dest L1c --reason)",
    "reorg.check.16": "pending acceptance (key s or set --accept --reason)",
    # proof checks (spec §7)
    "reorg.C0": "source differs from the plan",
    "reorg.C1": "primary ranges do not partition the source",
    "reorg.C2": "literal equality failed",
    "reorg.C3": "source reconstruction failed",
    "reorg.C4": "line multiset differs",
    "reorg.C5": "orphan or unbalanced destination content",
    "reorg.C6": "safety line without L1, copy or guard_ack",
    "reorg.C7": "L1 order differs from source order",
    "reorg.C8": "written files differ from outputs",
    "reorg.C8a": "written files differ from outputs (before the commit)",
    "reorg.C8b": "files changed by the commit differ from outputs",
    "reorg.C9": "pin missing or different",
    "reorg.G2": "protected unit outside L1",
    "reorg.G3": "publishable/device content in L2 or L1c",
    # sources and environment
    "reorg.secret": "secret",
    "reorg.unsafe_read": "unsafe read",
    "reorg.unsafe_write": "unsafe write",
    "reorg.not_utf8": "not UTF-8",
    "reorg.nul_byte": "NUL byte",
    "reorg.open_fence": "unclosed code fence",
    "reorg.open_comment": "unclosed HTML comment",
    "reorg.open_front_matter": "unclosed front matter",
    "reorg.front_matter_conflict": "front matter in two sources",
    "reorg.extend_open_structure": "file to extend ends inside an open structure",
    "reorg.agents_override": "AGENTS.override.md present: Codex would ignore AGENTS.md",
    "reorg.gitignore_no_star": ".declutter/.gitignore does not start with *",
    "reorg.paths_both_langs": "paths of both languages exist at once",
    "reorg.lang_invalid": "invalid language",
    "reorg.no_sources": "no CLAUDE.md or AGENTS.md to reorganize",
    "reorg.source_invalid": "invalid --source",
    "reorg.source_unsupported": "unsupported source",
    "reorg.agents_not_source": "AGENTS.md exists but is not a source",
    "reorg.run_id_invalid": "invalid run id",
    # git
    "reorg.detached_head": "detached HEAD",
    "reorg.operation_in_progress": "merge or rebase in progress",
    "reorg.dirty": "uncommitted changes",
    "reorg.index_not_clean": "the index has staged changes",
    "reorg.ref_exists": "branch or tag already exists",
    "reorg.git_normalizes": "git attributes or core.autocrlf normalize this path",
    "reorg.git_normalizes_lf_safe": (
        "git normalizes this path, but text=auto|set + eol=lf (or core.autocrlf=input) "
        "leaves LF-only bytes (no CR) unchanged"
    ),
    "reorg.blob_differs": "HEAD blob differs from the disk",
    "reorg.not_repo": "not a git repository (use --no-git)",
    "reorg.no_git_in_repo": "--no-git inside a repository",
    "reorg.git_error": "git error",
    "reorg.wrong_branch": "not on the reorg branch",
    "reorg.switch_refused": "git refused to switch branch",
    "reorg.revert_conflict": "revert conflict; aborted",
    "reorg.commit_rejected": "commit rejected (a hook may have changed files)",
    "reorg.git_add_failed": "git add of the new outputs failed; nothing was committed",
    "reorg.output_ignored": "a git ignore rule excludes this output; the commit would fail",
    # apply, undo, prune
    "reorg.already_applied": "this run was already applied",
    "reorg.needs_yes": "no TTY: --yes is required",
    "reorg.declined": "not applied",
    "reorg.g9_tty": "plan with guard_ack needs a TTY (G9)",
    "reorg.g9_not_confirmed": "G9 confirmation not given",
    "reorg.concurrent_edit": "file changed by another process during apply; restored",
    "reorg.apply_aborted": "apply aborted and restored",
    "reorg.check_failed": "check failed",
    "reorg.verify_rev_failed": "proof over the commit failed",
    "reorg.interrupted": "interrupted",
    "reorg.journal_invalid": "invalid journal.json",
    "reorg.batch_invalid": "invalid batch.json",
    "reorg.restore_untouched": "file edited after apply: left untouched",
    "reorg.source_not_restored": "source not restored",
    "reorg.prune_state": "cannot prune in this state",
    "reorg.run_missing": "run folder or file missing",
    "reorg.codex_budget": "Codex chain over 100% of its budget",
    "reorg.ratchet_would_fail": "the ratchet would fail after applying",
    "reorg.ratchet_invalid": "invalid ratchet.json or pins.json",
    "reorg.ambiguous_override": "override matches more than one unit or block",
    "reorg.manifest_invalid": "invalid manifest.json",
    "reorg.card_not_l4": "card applies only to units with dest L4",
    "reorg.replace_symlink": "CLAUDE.md is a symlink: use --replace-symlink",
    "reorg.unit_unknown": "unknown unit",
    "reorg.block_unknown": "unknown block",
    "reorg.block_not_guarded": "block has no safety category",
    "reorg.set_nothing": "nothing to set",
    "reorg.ack_without_block": "--ack-reason needs --not-safety",
    # warnings (spec §6.2)
    "reorg.l1_over_budget": "AGENTS.md over the L1 budget",
    "reorg.codex_warn": "Codex chain over the warning threshold",
    "reorg.open_branch_touches_source": "an open branch changes a source",
    "reorg.relative_link_moved": "relative links change folder",
    "reorg.import_moved": "@import leaves the startup load",
    "reorg.import_depth": "@import beyond the depth limit",
    "reorg.import_codex": "Codex does not expand @imports",
    "reorg.pending_confirmation": "pending acceptance",
    "reorg.override_orphan": "override without its unit or block (not applied)",
    "reorg.override_ambiguous": "ambiguous override (not applied)",
    "reorg.zone_over_limit": "zone AGENTS.md over its line limit",
    # levers (spec §5.6.5)
    "reorg.lever_review_low": "Review the low units still in L1 and move them out of L1",
    "reorg.lever_guard_ack": "Mark false-positive safety blocks with u and a reason (G9 each)",
    "reorg.lever_codex_budget": (
        "Project-scoped budget: create .codex/config.toml with project_doc_max_bytes = N and "
        "set codex_budget_bytes = N in declutter.toml (Codex honors it only in a trusted project)"
    ),
    "reorg.lever_command": "command: {cmd}",
    "reorg.set.pending_left": "override saved; {n} pending confirmation(s) left: {ids}",
    # declutter reorg codex-budget
    "reorg.toml_invalid": "not parseable as TOML; nothing written",
    "reorg.cfg_symlink": "is a symlink (or inside one); nothing written",
    "reorg.cannot_edit": "the key cannot be set without changing other lines; nothing written",
    "reorg.codex_config_ignored": (
        "a git ignore rule excludes .codex/config.toml: Codex reads it locally, "
        "but it will not be committed"
    ),
    "reorg.codexcfg.would_write": "would write {path}: {line} ({mode})",
    "reorg.codexcfg.wrote": "wrote {path}: {line} ({mode})",
    "reorg.codexcfg.unchanged": "{path} already has {line}",
    "reorg.codexcfg.mode.created": "new file",
    "reorg.codexcfg.mode.updated": "only this key changes",
    "reorg.codexcfg.header": (
        "# Codex project settings (declutter reorg codex-budget). Codex honors this file only\n"
        "# when the project is trusted.\n"
    ),
    "reorg.codexcfg.trust": (
        "Codex honors .codex/config.toml only when this project is trusted (Codex or the "
        "VS Code extension asks the first time you open it). To trust it by hand, add this "
        "to ~/.codex/config.toml (declutter never writes there):"
    ),
    "reorg.lever_distill": (
        "Distill the cards with /declutter-distill (check: declutter reorg distill-check), which "
        "replaces copies"
    ),
    "reorg.lever_revisit_g2": "Revisit a human decision that keeps a G2 unit in L1",
    # posterior states (spec §11.4)
    "reorg.edited_after": "edited after",
    "reorg.literal_intact": "literal intact",
    "reorg.history_altered": "history altered",
    "reorg.marker_missing": "marker missing",
    "reorg.excerpt_altered": "excerpt altered",
    "reorg.pin_missing": "pin missing",
    "reorg.protected_altered": "protected unit altered",
    "reorg.generated_edited": "generated text edited",
    "reorg.intact": "intact",
    "reorg.excerpt_intact": "excerpt intact",
    "reorg.pin_intact": "pin intact",
    "reorg.protected_intact": "protected unit intact",
    "reorg.released": "released (unpin or repin in ratchet.log)",
    "reorg.control_edited": "control file edited",
    "reorg.file_edited": "lines added outside the plan",
    # ratchet (spec §12.2)
    "ratchet.R1": "root instruction lines over the maximum",
    "ratchet.R2": "Codex budget over the maximum",
    "ratchet.R3": "new dated heading in an instruction file",
    "ratchet.R4": "zone AGENTS.md over its line limit",
    "ratchet.R5": "pinned block missing",
    "ratchet.R6": "literal history block altered or orphan marker",
    # G9
    "reorg.confirm_word": "confirm",
    # guided flow and rendering
    "reorg.step.measure": "1. Current measurement",
    "reorg.step.plan": "2. Plan",
    "reorg.step.projection": "3. Projection",
    "reorg.step.menu": "4. Menu",
    "reorg.step.before_apply": "5. Before applying",
    "reorg.step.closing": "7. Closing",
    "reorg.sources": "Sources",
    "reorg.no_secrets": "secret scan: no match",
    "reorg.secrets_found": "secret scan: {n} match(es); remove and rotate them",
    "reorg.guide_no_tty": "no TTY: running plan --dry-run (writes nothing)",
    "reorg.col.n": "#",
    "reorg.col.id": "id",
    "reorg.col.title": "title",
    "reorg.col.lines": "lines",
    "reorg.col.bytes": "bytes",
    "reorg.col.dest": "destination",
    "reorg.col.card": "card",
    "reorg.col.copies": "copies [categories]",
    "reorg.col.confidence": "confidence",
    "reorg.col.rule": "rule",
    "reorg.pending": "pending acceptance",
    "reorg.protected": "protected (G2)",
    "reorg.preamble": "(preamble)",
    "reorg.yes": "yes",
    "reorg.no": "no",
    "reorg.proj.files": "Files (bytes, lines)",
    "reorg.proj.signals": "S1-S4 before → after",
    "reorg.proj.codex": "Codex chains (% of budget)",
    "reorg.proj.added": "Claude load set, enters",
    "reorg.proj.removed": "Claude load set, leaves",
    "reorg.proj.warnings": "Warnings",
    "reorg.none": "none",
    "reorg.levers.head": "Levers (the human decides; the tool picks none), bytes for this plan:",
    "reorg.close.cards": "Decision cards pending distillation: {n}",
    "reorg.close.undo": "Undo: declutter reorg undo {id}",
    "reorg.close.prune": (
        "Delete the local literal copies when no longer needed: declutter reorg prune {id}"
    ),
    "reorg.close.merge": (
        "Merge by hand when you decide: git switch {base} && git merge declutter/reorg-{id}"
    ),
    "reorg.close.ratchet": (
        "Baseline recorded. Run declutter ratchet check before each commit; "
        "the pre-commit hook is optional (see the guide)"
    ),
    "reorg.close.now": "Measured now",
    "reorg.apply.files": "Files ({mode}):",
    "reorg.apply.mode.created": "created",
    "reorg.apply.mode.extended": "extended, with backup",
    "reorg.apply.mode.rewritten": "rewritten",
    "reorg.apply.mode.control": "control",
    "reorg.apply.refs": "Branch {branch} and tag {tag}",
    "reorg.apply.no_git": "Without git: no branch and no tag",
    "reorg.apply.counts": "Pinned copies: {pins} · decision cards: {cards}",
    "reorg.apply.result": "Apply result: code {code}",
    "reorg.menu.help": (
        "keys: k keep · s accept · h history · z zone · t trap · m map · c Claude-only · "
        "f card · u not safety · n/p next/prev · e editor · v check · A apply · q quit"
    ),
    "reorg.menu.low_intro": "{n} low-confidence unit(s) first, one by one; default destination L1",
    "reorg.menu.prompt": "[{pos}/{total}] key: ",
    "reorg.menu.ask_reason": "Reason: ",
    "reorg.menu.ask_zone": "Zone folder: ",
    "reorg.menu.ask_block": "Block (id or key): ",
    "reorg.menu.ask_ack": "Reason (at least 10 characters): ",
    "reorg.menu.g2_rejected": "protected (G2): this key is not allowed",
    "reorg.menu.c_rejected": "rejected: the unit has safety categories (S >= 1)",
    "reorg.menu.f_only_l4": "card applies only to L4 units",
    "reorg.menu.a_unavailable": "apply is not offered: the last check did not pass",
    "reorg.menu.unknown_key": "unknown key",
    "reorg.menu.no_editor": "EDITOR is not set",
    "reorg.menu.editor_invalid": "EDITOR cannot be parsed (unbalanced quotes?)",
    "reorg.menu.editor_failed": "EDITOR could not be run (missing or not executable)",
    "reorg.menu.no_guarded": "this unit has no safety block",
    "reorg.menu.check_ok": "check passed",
    "reorg.menu.check_failed": "check: code {code}",
    "reorg.menu.saved": "plan kept: declutter reorg apply {id}",
}

_REORG_ES: dict[str, str] = {
    "reorg.check.1": "plan obsoleto: una fuente cambió, apareció o desapareció",
    "reorg.check.2": "una fuente no se puede leer como quedó registrada",
    "reorg.check.3": "destino inválido",
    "reorg.check.4": "unidad protegida (G2): debe quedar en L1",
    "reorg.check.5": "bloque de seguridad sale de L1 sin copia literal ni guard_ack",
    "reorg.check.6": "un bloque publishable/device no puede ir a L2 ni a L1c (G3)",
    "reorg.check.7": "una unidad con categorías de seguridad no puede ir a L1c",
    "reorg.check.8": "la anulación necesita motivo (guard_ack: al menos 10 caracteres)",
    "reorg.check.9": "cadena de Codex sobre el 100 % de su presupuesto",
    "reorg.check.10": "id de ficha ya ocupado",
    "reorg.check.11": "el CLAUDE.md de la zona no importa su AGENTS.md",
    "reorg.check.12": "anulación ambigua o contradictoria",
    "reorg.check.13": "una unidad con @importaciones sale de L1 sin destino explícito",
    "reorg.check.14": "una unidad de AGENTS.md va a L1c o a una zona sin destino explícito",
    "reorg.check.15": "unidad L1c sin confirmar (tecla c o set --dest L1c --reason)",
    "reorg.check.16": "pendiente de aceptar (tecla s o set --accept --reason)",
    "reorg.C0": "la fuente difiere del plan",
    "reorg.C1": "los rangos primarios no particionan la fuente",
    "reorg.C2": "falló la igualdad literal",
    "reorg.C3": "falló la reconstrucción de la fuente",
    "reorg.C4": "el multiconjunto de líneas difiere",
    "reorg.C5": "contenido huérfano o desbalanceado en un destino",
    "reorg.C6": "línea de seguridad sin L1, copia ni guard_ack",
    "reorg.C7": "el orden de L1 difiere del de la fuente",
    "reorg.C8": "los archivos escritos difieren de outputs",
    "reorg.C8a": "los archivos escritos difieren de outputs (antes del commit)",
    "reorg.C8b": "los archivos del commit difieren de outputs",
    "reorg.C9": "fijación ausente o distinta",
    "reorg.G2": "unidad protegida fuera de L1",
    "reorg.G3": "contenido publishable/device en L2 o L1c",
    "reorg.secret": "secreto",
    "reorg.unsafe_read": "lectura insegura",
    "reorg.unsafe_write": "escritura insegura",
    "reorg.not_utf8": "no es UTF-8",
    "reorg.nul_byte": "byte nulo",
    "reorg.open_fence": "valla de código sin cerrar",
    "reorg.open_comment": "comentario HTML sin cerrar",
    "reorg.open_front_matter": "front matter sin cerrar",
    "reorg.front_matter_conflict": "front matter en dos fuentes",
    "reorg.extend_open_structure": "el archivo a ampliar termina dentro de una estructura abierta",
    "reorg.agents_override": "hay AGENTS.override.md: Codex ignoraría AGENTS.md",
    "reorg.gitignore_no_star": ".declutter/.gitignore no empieza con *",
    "reorg.paths_both_langs": "existen rutas de los dos idiomas a la vez",
    "reorg.lang_invalid": "idioma inválido",
    "reorg.no_sources": "no hay CLAUDE.md ni AGENTS.md que reorganizar",
    "reorg.source_invalid": "--source inválido",
    "reorg.source_unsupported": "fuente no admitida",
    "reorg.agents_not_source": "AGENTS.md existe pero no es fuente",
    "reorg.run_id_invalid": "id de corrida inválido",
    "reorg.detached_head": "HEAD separado",
    "reorg.operation_in_progress": "merge o rebase en curso",
    "reorg.dirty": "cambios sin commit",
    "reorg.index_not_clean": "el índice tiene cambios preparados",
    "reorg.ref_exists": "la rama o la marca ya existen",
    "reorg.git_normalizes": "atributos de git o core.autocrlf normalizan esta ruta",
    "reorg.git_normalizes_lf_safe": (
        "git normaliza esta ruta, pero text=auto|set + eol=lf (o core.autocrlf=input) "
        "no cambia bytes solo LF (sin CR)"
    ),
    "reorg.blob_differs": "el blob de HEAD difiere del disco",
    "reorg.not_repo": "no es un repositorio git (usa --no-git)",
    "reorg.no_git_in_repo": "--no-git dentro de un repositorio",
    "reorg.git_error": "error de git",
    "reorg.wrong_branch": "no estás en la rama de la reorganización",
    "reorg.switch_refused": "git rechazó cambiar de rama",
    "reorg.revert_conflict": "conflicto al revertir; se abortó",
    "reorg.commit_rejected": "commit rechazado (un hook pudo cambiar archivos)",
    "reorg.git_add_failed": "falló git add de los destinos nuevos; no se hizo commit",
    "reorg.output_ignored": "una regla de ignorar de git excluye este destino; el commit fallaría",
    "reorg.already_applied": "esta corrida ya se aplicó",
    "reorg.needs_yes": "sin TTY: hace falta --yes",
    "reorg.declined": "no se aplicó",
    "reorg.g9_tty": "un plan con guard_ack necesita TTY (G9)",
    "reorg.g9_not_confirmed": "no se dio la confirmación de G9",
    "reorg.concurrent_edit": "otro proceso cambió un archivo durante apply; se restauró",
    "reorg.apply_aborted": "apply abortado y restaurado",
    "reorg.check_failed": "falló check",
    "reorg.verify_rev_failed": "falló la prueba sobre el commit",
    "reorg.interrupted": "interrumpido",
    "reorg.journal_invalid": "journal.json inválido",
    "reorg.batch_invalid": "batch.json inválido",
    "reorg.restore_untouched": "archivo editado después de aplicar: no se toca",
    "reorg.source_not_restored": "fuente no restaurada",
    "reorg.prune_state": "no se puede podar en este estado",
    "reorg.run_missing": "falta la carpeta o un archivo de la corrida",
    "reorg.codex_budget": "cadena de Codex sobre el 100 % de su presupuesto",
    "reorg.ratchet_would_fail": "el trinquete fallaría después de aplicar",
    "reorg.ratchet_invalid": "ratchet.json o pins.json inválido",
    "reorg.ambiguous_override": "la anulación coincide con más de una unidad o bloque",
    "reorg.manifest_invalid": "manifest.json inválido",
    "reorg.card_not_l4": "card solo se aplica a unidades con destino L4",
    "reorg.replace_symlink": "CLAUDE.md es un enlace simbólico: usa --replace-symlink",
    "reorg.unit_unknown": "unidad desconocida",
    "reorg.block_unknown": "bloque desconocido",
    "reorg.block_not_guarded": "el bloque no tiene categoría de seguridad",
    "reorg.set_nothing": "no hay nada que cambiar",
    "reorg.ack_without_block": "--ack-reason necesita --not-safety",
    "reorg.l1_over_budget": "AGENTS.md sobre el presupuesto de L1",
    "reorg.codex_warn": "cadena de Codex sobre el umbral de aviso",
    "reorg.open_branch_touches_source": "una rama abierta cambia una fuente",
    "reorg.relative_link_moved": "enlaces relativos que cambian de carpeta",
    "reorg.import_moved": "una @importación sale de la carga inicial",
    "reorg.import_depth": "una @importación supera la profundidad máxima",
    "reorg.import_codex": "Codex no expande las @importaciones",
    "reorg.pending_confirmation": "pendiente de aceptar",
    "reorg.override_orphan": "anulación sin su unidad o bloque (no se aplica)",
    "reorg.override_ambiguous": "anulación ambigua (no se aplica)",
    "reorg.zone_over_limit": "AGENTS.md de zona sobre su límite de líneas",
    "reorg.lever_review_low": "Revisar las unidades low que quedan en L1 y moverlas fuera de L1",
    "reorg.lever_guard_ack": (
        "Marcar con u y motivo los bloques de seguridad que son falsos positivos (cada uno con G9)"
    ),
    "reorg.lever_codex_budget": (
        "Presupuesto del proyecto: crear .codex/config.toml con project_doc_max_bytes = N y "
        "fijar codex_budget_bytes = N en declutter.toml (Codex lo respeta solo si el proyecto "
        "es de confianza)"
    ),
    "reorg.lever_command": "comando: {cmd}",
    "reorg.set.pending_left": "anulación guardada; quedan {n} confirmaciones pendientes: {ids}",
    # declutter reorg codex-budget
    "reorg.toml_invalid": "no se puede leer como TOML; no se escribió nada",
    "reorg.cfg_symlink": "es un enlace simbólico (o está dentro de uno); no se escribió nada",
    "reorg.cannot_edit": (
        "no se puede fijar la clave sin cambiar otras líneas; no se escribió nada"
    ),
    "reorg.codex_config_ignored": (
        "una regla de ignore de git excluye .codex/config.toml: Codex lo lee en local, "
        "pero no quedará en el commit"
    ),
    "reorg.codexcfg.would_write": "escribiría {path}: {line} ({mode})",
    "reorg.codexcfg.wrote": "escrito {path}: {line} ({mode})",
    "reorg.codexcfg.unchanged": "{path} ya tiene {line}",
    "reorg.codexcfg.mode.created": "archivo nuevo",
    "reorg.codexcfg.mode.updated": "solo cambia esta clave",
    "reorg.codexcfg.header": (
        "# Ajustes de Codex para el proyecto (declutter reorg codex-budget). Codex respeta este\n"
        "# archivo solo si el proyecto es de confianza.\n"
    ),
    "reorg.codexcfg.trust": (
        "Codex respeta .codex/config.toml solo si este proyecto es de confianza (Codex o la "
        "extensión de VS Code lo preguntan la primera vez que lo abres). Para marcarlo a mano, "
        "agrega esto a ~/.codex/config.toml (declutter nunca escribe ahí):"
    ),
    "reorg.lever_distill": (
        "Destilar las fichas con /declutter-distill (revisa: declutter reorg distill-check), que "
        "reemplaza las copias"
    ),
    "reorg.lever_revisit_g2": "Revisar una decisión del humano que deja una unidad G2 en L1",
    "reorg.edited_after": "editado después",
    "reorg.literal_intact": "literal intacto",
    "reorg.history_altered": "historial alterado",
    "reorg.marker_missing": "falta un marcador",
    "reorg.excerpt_altered": "extracto alterado",
    "reorg.pin_missing": "falta una fijación",
    "reorg.protected_altered": "unidad protegida alterada",
    "reorg.generated_edited": "texto generado editado",
    "reorg.intact": "intacto",
    "reorg.excerpt_intact": "extracto intacto",
    "reorg.pin_intact": "fijación intacta",
    "reorg.protected_intact": "unidad protegida intacta",
    "reorg.released": "liberada (unpin o repin en ratchet.log)",
    "reorg.control_edited": "archivo de control editado",
    "reorg.file_edited": "líneas agregadas fuera del plan",
    "ratchet.R1": "líneas de instrucción raíz sobre el máximo",
    "ratchet.R2": "presupuesto de Codex sobre el máximo",
    "ratchet.R3": "título con fecha nuevo en un archivo de instrucciones",
    "ratchet.R4": "AGENTS.md de zona sobre su límite de líneas",
    "ratchet.R5": "falta un bloque fijado",
    "ratchet.R6": "bloque literal del historial alterado o marcador huérfano",
    "reorg.confirm_word": "confirmo",
    "reorg.step.measure": "1. Medición actual",
    "reorg.step.plan": "2. Plan",
    "reorg.step.projection": "3. Proyección",
    "reorg.step.menu": "4. Menú",
    "reorg.step.before_apply": "5. Antes de aplicar",
    "reorg.step.closing": "7. Cierre",
    "reorg.sources": "Fuentes",
    "reorg.no_secrets": "escaneo de secretos: sin coincidencias",
    "reorg.secrets_found": "escaneo de secretos: {n} coincidencia(s); quítalas y rótalas",
    "reorg.guide_no_tty": "sin TTY: se ejecuta plan --dry-run (no escribe nada)",
    "reorg.col.n": "#",
    "reorg.col.id": "id",
    "reorg.col.title": "título",
    "reorg.col.lines": "líneas",
    "reorg.col.bytes": "bytes",
    "reorg.col.dest": "destino",
    "reorg.col.card": "ficha",
    "reorg.col.copies": "copias [categorías]",
    "reorg.col.confidence": "confianza",
    "reorg.col.rule": "regla",
    "reorg.pending": "pendiente de aceptar",
    "reorg.protected": "protegida (G2)",
    "reorg.preamble": "(preámbulo)",
    "reorg.yes": "sí",
    "reorg.no": "no",
    "reorg.proj.files": "Archivos (bytes, líneas)",
    "reorg.proj.signals": "S1-S4 antes → después",
    "reorg.proj.codex": "Cadenas de Codex (% del presupuesto)",
    "reorg.proj.added": "Conjunto de carga de Claude, entran",
    "reorg.proj.removed": "Conjunto de carga de Claude, salen",
    "reorg.proj.warnings": "Advertencias",
    "reorg.none": "ninguna",
    "reorg.levers.head": (
        "Palancas (decide el humano; la herramienta no elige ninguna), bytes de este plan:"
    ),
    "reorg.close.cards": "Fichas pendientes de destilar: {n}",
    "reorg.close.undo": "Deshacer: declutter reorg undo {id}",
    "reorg.close.prune": (
        "Borrar las copias locales cuando ya no hagan falta: declutter reorg prune {id}"
    ),
    "reorg.close.merge": (
        "Fusionar a mano cuando lo decidas: git switch {base} && git merge declutter/reorg-{id}"
    ),
    "reorg.close.ratchet": (
        "Línea base registrada. Ejecuta declutter ratchet check antes de cada commit; "
        "el hook de pre-commit es opcional (ver la guía)"
    ),
    "reorg.close.now": "Medición actual",
    "reorg.apply.files": "Archivos ({mode}):",
    "reorg.apply.mode.created": "a crear",
    "reorg.apply.mode.extended": "a ampliar, con respaldo",
    "reorg.apply.mode.rewritten": "a reescribir",
    "reorg.apply.mode.control": "de control",
    "reorg.apply.refs": "Rama {branch} y marca {tag}",
    "reorg.apply.no_git": "Sin git: sin rama ni marca",
    "reorg.apply.counts": "Copias fijadas: {pins} · fichas: {cards}",
    "reorg.apply.result": "Resultado de aplicar: código {code}",
    "reorg.menu.help": (
        "teclas: k mantener · s aceptar · h historial · z zona · t trampa · m mapa · "
        "c solo Claude · f ficha · u no es seguridad · n/p siguiente/anterior · e editor · "
        "v revalidar · A aplicar · q salir"
    ),
    "reorg.menu.low_intro": (
        "{n} unidad(es) de confianza low primero, una por una; destino por defecto L1"
    ),
    "reorg.menu.prompt": "[{pos}/{total}] tecla: ",
    "reorg.menu.ask_reason": "Motivo: ",
    "reorg.menu.ask_zone": "Carpeta de la zona: ",
    "reorg.menu.ask_block": "Bloque (id o clave): ",
    "reorg.menu.ask_ack": "Motivo (al menos 10 caracteres): ",
    "reorg.menu.g2_rejected": "protegida (G2): esta tecla no se permite",
    "reorg.menu.c_rejected": "rechazado: la unidad tiene categorías de seguridad (S >= 1)",
    "reorg.menu.f_only_l4": "la ficha solo se aplica a unidades L4",
    "reorg.menu.a_unavailable": "no se ofrece aplicar: el último check no pasó",
    "reorg.menu.unknown_key": "tecla desconocida",
    "reorg.menu.no_editor": "EDITOR no está definido",
    "reorg.menu.editor_invalid": "EDITOR no se puede interpretar (¿comillas sin cerrar?)",
    "reorg.menu.editor_failed": "no se pudo ejecutar EDITOR (no existe o no es ejecutable)",
    "reorg.menu.no_guarded": "esta unidad no tiene bloques de seguridad",
    "reorg.menu.check_ok": "check pasó",
    "reorg.menu.check_failed": "check: código {code}",
    "reorg.menu.saved": "el plan queda guardado: declutter reorg apply {id}",
}

# declutter reorg distill-check (fase 2b spec §5, §12). Reasons and states are metadata only.
_DISTILL_EN: dict[str, str] = {
    "reorg.distill.card_unknown": "the card is not a card of this run",
    "reorg.distill.not_applied": "the run is not applied (journal written or committed needed)",
    "reorg.control_invalid": "control file missing or invalid",
    "distill.state.ready": "ready",
    "distill.state.not_ready": "not ready",
    "distill.state.distilled": "distilled",
    "distill.state.no_card": "no card",
    "distill.warning": "warning",
    "distill.totals": (
        "cards: {cards} ({cards_ready} ready, {cards_not_ready} not ready, {cards_distilled}"
        " distilled); ready cards would remove {bytes_removed} B and add {bytes_added} B;"
        " copies without a card: {no_card} ({no_card_bytes} B)"
    ),
    "distill.reason.pending_what": "PENDING in «What»",
    "distill.reason.pending_invariant": "PENDING in the invariant",
    "distill.reason.pending_optional": "PENDING in an optional section",
    "distill.reason.invariant_empty": "empty invariant",
    "distill.reason.invariant_not_stated": "the invariant only says «not stated»",
    "distill.reason.token_missing": "an anchor token of the literal is missing",
    "distill.reason.token_invented": "an anchor token is not in the unit",
    "distill.reason.deontic_mismatch": "obligations differ (literal≠invariant)",
    "distill.reason.history_altered": "history literal altered",
    "distill.reason.marker_missing": "history marker missing",
    "distill.reason.origin_changed": "the card's origin lines changed",
    "distill.reason.card_structure": "card structure changed",
    "distill.reason.card_state": "unexpected card state line",
    "distill.reason.card_markers": "card markers missing or repeated",
    "distill.reason.card_unbalanced": "card ends inside a fence, comment or front matter",
    "distill.reason.card_unreadable": "card missing, not UTF-8 or unsafe",
    "distill.reason.invariant_unbalanced": "invariant leaves a structure open",
    "distill.reason.invariant_heading": "heading or separator in the invariant",
    "distill.reason.invariant_marker": "declutter marker in the invariant",
    "distill.reason.invariant_import": "@path import in the invariant",
    "distill.reason.invariant_cr": "carriage return in the invariant",
    "distill.reason.invariant_secret": "secret in the card",
    "distill.reason.categories_lost": "safety category lost",
    "distill.reason.not_shorter": "the entry is not shorter than the copies",
    "distill.reason.copy_not_located": "copy not found in AGENTS.md",
    "distill.reason.copy_ambiguous": "copy found more than once",
    "distill.reason.header_changed": "copy header or blank line changed",
    "distill.reason.pin_missing": "copy pin missing",
    "distill.reason.pin_released": "copy pin released (unpin)",
    "distill.reason.index_row_changed": "decisions index row changed",
    "distill.reason.pointer_changed": "pointer line changed",
    "distill.reason.no_copies": "the card has no copy in AGENTS.md",
}

_DISTILL_ES: dict[str, str] = {
    "reorg.distill.card_unknown": "la ficha no es de esta corrida",
    "reorg.distill.not_applied": (
        "la corrida no está aplicada (se necesita el journal en written o committed)"
    ),
    "reorg.control_invalid": "archivo de control ausente o inválido",
    "distill.state.ready": "lista",
    "distill.state.not_ready": "no lista",
    "distill.state.distilled": "destilada",
    "distill.state.no_card": "sin ficha",
    "distill.warning": "advertencia",
    "distill.totals": (
        "fichas: {cards} ({cards_ready} listas, {cards_not_ready} no listas,"
        " {cards_distilled} destiladas); las listas quitarían {bytes_removed} B y agregarían"
        " {bytes_added} B; copias sin ficha: {no_card} ({no_card_bytes} B)"
    ),
    "distill.reason.pending_what": "PENDIENTE en «Qué»",
    "distill.reason.pending_invariant": "PENDIENTE en la invariante",
    "distill.reason.pending_optional": "PENDIENTE en una sección opcional",
    "distill.reason.invariant_empty": "invariante vacía",
    "distill.reason.invariant_not_stated": "la invariante solo dice «no consta»",
    "distill.reason.token_missing": "falta un token ancla del literal",
    "distill.reason.token_invented": "un token ancla no está en la unidad",
    "distill.reason.deontic_mismatch": "las obligaciones difieren (literal≠invariante)",
    "distill.reason.history_altered": "literal del historial alterado",
    "distill.reason.marker_missing": "falta el marcador del historial",
    "distill.reason.origin_changed": "cambiaron las líneas de origen de la ficha",
    "distill.reason.card_structure": "cambió la estructura de la ficha",
    "distill.reason.card_state": "línea de estado inesperada",
    "distill.reason.card_markers": "marcadores de la ficha ausentes o repetidos",
    "distill.reason.card_unbalanced": (
        "la ficha termina dentro de una valla, comentario o front matter"
    ),
    "distill.reason.card_unreadable": "ficha ausente, no UTF-8 o insegura",
    "distill.reason.invariant_unbalanced": "la invariante deja una estructura abierta",
    "distill.reason.invariant_heading": "título o separador en la invariante",
    "distill.reason.invariant_marker": "marcador declutter en la invariante",
    "distill.reason.invariant_import": "importación @ruta en la invariante",
    "distill.reason.invariant_cr": "retorno de carro en la invariante",
    "distill.reason.invariant_secret": "secreto en la ficha",
    "distill.reason.categories_lost": "se perdió una categoría de seguridad",
    "distill.reason.not_shorter": "la entrada no es más corta que las copias",
    "distill.reason.copy_not_located": "no se encontró la copia en AGENTS.md",
    "distill.reason.copy_ambiguous": "la copia aparece más de una vez",
    "distill.reason.header_changed": "cambió la cabecera o la línea en blanco de la copia",
    "distill.reason.pin_missing": "falta la fijación de la copia",
    "distill.reason.pin_released": "fijación de la copia liberada (unpin)",
    "distill.reason.index_row_changed": "cambió la fila del índice de decisiones",
    "distill.reason.pointer_changed": "cambió la línea del puntero",
    "distill.reason.no_copies": "la ficha no tiene copias en AGENTS.md",
}

# declutter reorg distill-apply / distill-undo (fase 2b spec §8, §9, §10, §12). Metadata only.
_DISTILL_APPLY_EN: dict[str, str] = {
    "reorg.distilled": "distilled (replaced by its invariant, audited in ratchet.log)",
    "reorg.card_distilled": "card distilled and frozen",
    "reorg.control_appended": "control file with audited additions only",
    "reorg.distill_invalid": "distill event in ratchet.log that no distillation could write",
    "reorg.no_baseline": "no ratchet baseline (.declutter/ratchet.json)",
    "reorg.distill_active": "a distillation of this run is active; distill-undo it first",
    "reorg.distill.region_changed": "the region of distilled invariants changed",
    "reorg.distill.card_changed": "the card changed during the confirmation; run it again",
    "distill.reason.entry_mismatch": "the entry in AGENTS.md is not the card's invariant",
    "reorg.distill.copy_not_located": "copy not found in AGENTS.md",
    "reorg.distill.header_changed": "copy header or blank line changed",
    "reorg.distill.not_ready": "the card is not ready",
    "reorg.distill.card_unreadable": "card missing, not UTF-8 or unsafe",
    "reorg.distill.card_markers": "card markers missing or repeated",
    "reorg.distill.pin_missing": "copy pin missing",
    "reorg.distill.index_row_changed": "decisions index row changed",
    "reorg.distill.pointer_changed": "pointer line changed",
    "reorg.distill.not_distilled": "the result does not verify as distilled",
    "reorg.distill.ratchet": "ratchet check fails",
    "reorg.distill.agents_unbalanced": "AGENTS.md would leave a structure open",
    "reorg.distill.run_not_committed": (
        "the run is not committed (git) or written (--no-git); apply it first"
    ),
    "reorg.distill.run_not_ancestor": "the run's commit is not an ancestor of HEAD",
    "reorg.distill.interrupted_other": (
        "another distillation of this run is interrupted; undo it with distill-undo"
    ),
    "reorg.distill.interrupted": "this distillation is interrupted; undo it with distill-undo",
    "reorg.distill.batch_card": "this card belongs to a distill-auto batch; undo the whole batch "
    "(the filled cards are restored, so you can rerun with --skip)",
    "reorg.distill.auto.stopped_card": "batch stopped before writing this card; nothing changed",
    "reorg.distill.auto.locked": "another distill-auto batch of this run is running",
    "reorg.distill.auto.interrupted": (
        "a distill-auto batch of this run was interrupted; undo it first"
    ),
    "reorg.distill.auto.skip_unknown": "--skip names a card that does not exist in this run",
    "reorg.distill.auto.ratchet_human": (
        "the ratchet leaves no room; relaxing a maximum is a human decision"
    ),
    "reorg.distill.auto.halted": "the batch was halted at this card",
    "reorg.distill.auto.dry_run_unsafe": "--dry-run refused: it cannot isolate the project "
    "copy from the real repository; nothing was copied or changed",
    "reorg.distill.auto.dry_run_too_big": "--dry-run refused: the project is too big to copy "
    "(over 2 GiB); use the real run and distill-undo --auto",
    "reorg.distill.auto.no_batch": "no distill-auto batch with this number in this run",
    "reorg.distill.auto.no_tty": "the batch is merged: a terminal is needed to confirm the "
    "revert; --yes never skips it",
    "reorg.distill.auto.not_confirmed": "revert not confirmed; nothing changed",
    "reorg.distill.auto.reconcile_refused": "the tip of the batch branch carries this "
    "batch's trailers but is not the card's own commit; nothing was changed, check it by hand",
    "reorg.distill.auto.head_moved": "the base branch moved while waiting for the "
    "confirmation; nothing was reverted",
    "reorg.distill.auto.revert_partial": "the revert stopped half-way and could not be "
    "aborted; these revert commits remain on the base branch",
    "reorg.distill.no_tty": "a terminal is needed to confirm (G9); --yes never skips it",
    "reorg.distill.no_reason": "the reason is required",
    "reorg.distill.not_confirmed": "confirmation not given (G9); nothing changed",
    "reorg.distill.outside_outputs": "new change outside the declared outputs",
    "reorg.distill.commit_rejected": "the commit was rejected (hook?)",
    "reorg.distill.verify_rev_failed": "verify --rev HEAD failed after the commit",
    "reorg.distill.needs_yes": "no TTY: --yes is required to resume the commit",
    "reorg.distill.already_applied": "this distillation is already written; use --commit",
    "reorg.distill.not_restored": "file did not return to its previous sha",
    "reorg.distill.lifo": "a later distillation of this run must be undone first",
    "distill.apply.head": "Distill {card} (unit {unit}): {n} copies → one invariant",
    "distill.apply.copy": "  {block} {pin} {src} {lines} lines {bytes} B [{cats}]",
    "distill.apply.pins": "  New pins: {inv} (invariant, {inv_lines} lines), "
    "{card} (card, {card_lines} lines)",
    "distill.apply.files": "  Files to write: {files}",
    "distill.apply.agents": "  AGENTS.md: {b0} B / {l0} lines → {b1} B / {l1} lines",
    "distill.apply.tighten": "  Ratchet maxima going down: {items}",
    "distill.apply.tighten_none": "  Ratchet maxima: unchanged",
    "distill.apply.literal": "  The literal stays in {history} and in {card}",
    "distill.apply.ask_reason": "Reason (goes to ratchet.log): ",
    "distill.apply.ask_word": "Type 'confirm' to distill: ",
    "distill.apply.commit_q": "Commit on {branch}? [y/N] ",
    "distill.apply.subject": "declutter(distill {id} {card}): {n} copies → invariant, {delta} B "
    "in AGENTS.md",
    "distill.apply.written": "{card} distilled: {delta} B in AGENTS.md",
    "distill.apply.already": "{card} already distilled; nothing to do",
    "distill.apply.cmds": "The branch and the tag are kept; to delete them by hand:",
    "distill.apply.revert_q": "Revert {sha} on {branch}? [y/N] ",
    "distill.apply.revert_commit_q": "Commit the revert on {branch}? [y/N] ",
    "distill.apply.revert_staged": "The revert is staged without a commit; review and commit "
    "by hand.",
    "distill.apply.revert_subject": "Revert declutter(distill {id} {card})",
    "distill.apply.nothing": "{card}: nothing to undo",
    "distill.apply.untouched": "Left untouched (changed after distill-apply): {path}",
    "distill.apply.card_on_branch": "The filled card {card} stays on {branch}",
    "distill.status.line": "  {card} {state}{flag} {date}",
}

_DISTILL_APPLY_ES: dict[str, str] = {
    "reorg.distilled": "destilada (reemplazada por su invariante, auditada en ratchet.log)",
    "reorg.card_distilled": "ficha destilada y congelada",
    "reorg.control_appended": "archivo de control solo con agregados auditados",
    "reorg.distill_invalid": "evento distill en ratchet.log que ningún destilado pudo escribir",
    "reorg.no_baseline": "no hay línea base del trinquete (.declutter/ratchet.json)",
    "reorg.distill_active": (
        "hay un destilado activo de esta corrida; deshazlo antes con distill-undo"
    ),
    "reorg.distill.region_changed": "cambió la región de invariantes destiladas",
    "reorg.distill.card_changed": "la ficha cambió durante la confirmación; vuelve a correrlo",
    "distill.reason.entry_mismatch": "la entrada en AGENTS.md no es la invariante de la ficha",
    "reorg.distill.copy_not_located": "no se encontró la copia en AGENTS.md",
    "reorg.distill.header_changed": "cambió la cabecera o la línea en blanco de la copia",
    "reorg.distill.not_ready": "la ficha no está lista",
    "reorg.distill.card_unreadable": "ficha ausente, no UTF-8 o insegura",
    "reorg.distill.card_markers": "marcadores de la ficha ausentes o repetidos",
    "reorg.distill.pin_missing": "falta la fijación de la copia",
    "reorg.distill.index_row_changed": "cambió la fila del índice de decisiones",
    "reorg.distill.pointer_changed": "cambió la línea del puntero",
    "reorg.distill.not_distilled": "el resultado no verifica como destilado",
    "reorg.distill.ratchet": "ratchet check falla",
    "reorg.distill.agents_unbalanced": "AGENTS.md quedaría con una estructura abierta",
    "reorg.distill.run_not_committed": (
        "la corrida no está commiteada (git) ni escrita (--no-git); aplícala primero"
    ),
    "reorg.distill.run_not_ancestor": "el commit de la corrida no es ancestro de HEAD",
    "reorg.distill.interrupted_other": (
        "otro destilado de esta corrida quedó interrumpido; deshazlo con distill-undo"
    ),
    "reorg.distill.interrupted": "este destilado quedó interrumpido; deshazlo con distill-undo",
    "reorg.distill.batch_card": "esta ficha es de un lote distill-auto; deshaz el lote completo "
    "(las fichas llenadas quedan restauradas y se puede relanzar con --skip)",
    "reorg.distill.auto.stopped_card": (
        "lote frenado antes de escribir esta ficha; no cambió nada"
    ),
    "reorg.distill.auto.locked": "otro lote distill-auto de esta corrida está en curso",
    "reorg.distill.auto.interrupted": (
        "un lote distill-auto de esta corrida quedó interrumpido; deshazlo antes"
    ),
    "reorg.distill.auto.skip_unknown": "--skip nombra una ficha que no existe en esta corrida",
    "reorg.distill.auto.ratchet_human": (
        "el trinquete no deja espacio; relajar un máximo es una decisión humana"
    ),
    "reorg.distill.auto.halted": "el lote se detuvo en esta ficha",
    "reorg.distill.auto.dry_run_unsafe": "--dry-run rechazado: no puede aislar la copia del "
    "repositorio real; no se copió ni se cambió nada",
    "reorg.distill.auto.dry_run_too_big": "--dry-run rechazado: el proyecto es demasiado grande "
    "para copiarlo (más de 2 GiB); usa la corrida real y distill-undo --auto",
    "reorg.distill.auto.no_batch": "no hay un lote distill-auto con ese número en esta corrida",
    "reorg.distill.auto.no_tty": "el lote está fusionado: hace falta una terminal para "
    "confirmar la reversión; --yes nunca la omite",
    "reorg.distill.auto.not_confirmed": "reversión no confirmada; no se cambió nada",
    "reorg.distill.auto.reconcile_refused": "la punta de la rama del lote lleva los "
    "trailers del lote pero no es el commit de la ficha; no se cambió nada, revísala a mano",
    "reorg.distill.auto.head_moved": "la rama base se movió mientras se esperaba la "
    "confirmación; no se revirtió nada",
    "reorg.distill.auto.revert_partial": "la reversión se detuvo a medias y no se pudo "
    "abortar; estos commits de reversión quedan en la rama base",
    "reorg.distill.no_tty": "hace falta una terminal para confirmar (G9); --yes nunca la omite",
    "reorg.distill.no_reason": "falta el motivo",
    "reorg.distill.not_confirmed": "no se dio la confirmación (G9); no se cambió nada",
    "reorg.distill.outside_outputs": "cambio nuevo fuera de las salidas declaradas",
    "reorg.distill.commit_rejected": "se rechazó el commit (¿hook?)",
    "reorg.distill.verify_rev_failed": "verify --rev HEAD falló después del commit",
    "reorg.distill.needs_yes": "sin TTY: hace falta --yes para reanudar el commit",
    "reorg.distill.already_applied": "este destilado ya está escrito; usa --commit",
    "reorg.distill.not_restored": "el archivo no volvió a su sha anterior",
    "reorg.distill.lifo": "primero hay que deshacer un destilado posterior de esta corrida",
    "distill.apply.head": "Destilar {card} (unidad {unit}): {n} copias → una invariante",
    "distill.apply.copy": "  {block} {pin} {src} {lines} líneas {bytes} B [{cats}]",
    "distill.apply.pins": "  Fijaciones nuevas: {inv} (invariant, {inv_lines} líneas), "
    "{card} (card, {card_lines} líneas)",
    "distill.apply.files": "  Archivos que se escribirán: {files}",
    "distill.apply.agents": "  AGENTS.md: {b0} B / {l0} líneas → {b1} B / {l1} líneas",
    "distill.apply.tighten": "  Máximos del trinquete que bajan: {items}",
    "distill.apply.tighten_none": "  Máximos del trinquete: sin cambio",
    "distill.apply.literal": "  El literal queda en {history} y en {card}",
    "distill.apply.ask_reason": "Motivo (va a ratchet.log): ",
    "distill.apply.ask_word": "Escribe «confirmo» para destilar: ",
    "distill.apply.commit_q": "¿Commit en {branch}? [s/N] ",
    "distill.apply.subject": "declutter(distill {id} {card}): {n} copias → invariante, {delta} B "
    "en AGENTS.md",
    "distill.apply.written": "{card} destilada: {delta} B en AGENTS.md",
    "distill.apply.already": "{card} ya destilada; nada que hacer",
    "distill.apply.cmds": "La rama y la marca se conservan; para borrarlas a mano:",
    "distill.apply.revert_q": "¿Revertir {sha} en {branch}? [s/N] ",
    "distill.apply.revert_commit_q": "¿Commit de la reversión en {branch}? [s/N] ",
    "distill.apply.revert_staged": "La reversión quedó preparada sin commit; revisa y haz "
    "commit a mano.",
    "distill.apply.revert_subject": "Revertir declutter(distill {id} {card})",
    "distill.apply.nothing": "{card}: nada que deshacer",
    "distill.apply.untouched": "No se tocó (cambió después de distill-apply): {path}",
    "distill.apply.card_on_branch": "La ficha llenada {card} sigue en {branch}",
    "distill.status.line": "  {card} {state}{flag} {date}",
}

_DISTILL_AUTO_EN: dict[str, str] = {
    "distill.auto.start": "Batch {n}: {k} cards ready, {m} skipped. Branch {branch}. "
    "Ctrl+C to stop",
    "distill.auto.applied": "[{i}/{k}] {card} applied · copies: {copies} → invariant · -{bytes} B",
    "distill.auto.skipped": "[{i}/{k}] {card} skipped ({keys})",
    "distill.auto.halted": "[{i}/{k}] {card}: batch halted ({reason})",
    "distill.auto.braked": "Stop received: the batch ends after the current step.",
    "distill.auto.nothing": "Nothing to apply.",
    "distill.auto.dry_run_notice": "Dry run: nothing is written to the project.",
    "distill.auto.report.dry_run": "Dry run: nothing was written to the project",
    "distill.auto.card_kept": "{card}: not restored to its filled state ({path} changed "
    "after the undo or its backup does not match); left as is",
    "distill.auto.ratchet_human": "The ratchet leaves no room. Relaxing a maximum is a "
    "human decision (declutter ratchet relax); the batch did not start.",
    "distill.auto.report.title": "# distill-auto {id} · batch {n}",
    "distill.auto.report.seal": "Seal: GUIDE (procedural report built by the engine).",
    "distill.auto.report.state": "Branch {branch} · tag {tag} · state {state}",
    "distill.auto.report.applied": "## Applied ({n})",
    "distill.auto.report.applied_row": "- {card}: copies: {copies}, -{removed} B / "
    "+{added} B, commit {commit}",
    "distill.auto.report.skipped": "## Skipped ({n})",
    "distill.auto.report.skipped_row": "- {card}: {keys}",
    "distill.auto.report.not_included": "## Not included",
    "distill.auto.report.user_skipped": "- --skip: {cards}",
    "distill.auto.report.distilled_before": "- already distilled: {cards}",
    "distill.auto.report.no_card": "- copies without a card: {bytes} B",
    "distill.auto.report.agents": "## AGENTS.md\n{b0} B / {l0} lines → {b1} B / {l1} lines "
    "({db} B, {dl} lines)",
    "distill.auto.report.tighten": "## Ratchet\nMaxima lowered: {items}",
    "distill.auto.report.tighten_none": "## Ratchet\nNo maximum lowered.",
    "distill.auto.report.pending": "## Filled but not applied\n{paths}",
    "distill.auto.report.commands": "## Commands",
    "distill.auto.report.merge": "Merge: `{cmd}`",
    "distill.auto.report.undo": "Undo: `{cmd}`",
    "distill.auto.report.none": "(none)",
    "distill.auto.undo.done": "Batch {n} undone. The filled cards were left as they were before "
    "the batch. Branch {branch} and tag {tag} are kept.",
    "distill.auto.undo.ask": "Batch {n} is merged into {branch}. Type «confirm» to revert "
    "these {k} commits: ",
    "distill.auto.undo.commit_row": "  {sha} {subject} [{files}]",
}

_DISTILL_AUTO_ES: dict[str, str] = {
    "distill.auto.start": "Lote {n}: {k} fichas listas, {m} saltadas. Rama {branch}. "
    "Ctrl+C para frenar",
    "distill.auto.applied": "[{i}/{k}] {card} aplicada · copias: {copies} → invariante · "
    "-{bytes} B",
    "distill.auto.skipped": "[{i}/{k}] {card} saltada ({keys})",
    "distill.auto.halted": "[{i}/{k}] {card}: lote detenido ({reason})",
    "distill.auto.braked": "Freno recibido: el lote termina tras el paso en curso.",
    "distill.auto.nothing": "Nada que aplicar.",
    "distill.auto.dry_run_notice": "Ensayo (dry-run): nada se escribe en el proyecto.",
    "distill.auto.report.dry_run": "Ensayo (dry-run): nada se escribió en el proyecto",
    "distill.auto.card_kept": "{card}: no se restauró a su estado llenado ({path} cambió "
    "tras el deshacer o su respaldo no coincide); se deja como está",
    "distill.auto.ratchet_human": "El trinquete no deja espacio. Relajar un máximo es "
    "decisión humana (declutter ratchet relax); el lote no empezó.",
    "distill.auto.report.title": "# distill-auto {id} · lote {n}",
    "distill.auto.report.seal": "Sello: GUÍA (informe procedimental armado por el motor).",
    "distill.auto.report.state": "Rama {branch} · marca {tag} · estado {state}",
    "distill.auto.report.applied": "## Aplicadas ({n})",
    "distill.auto.report.applied_row": "- {card}: copias: {copies}, -{removed} B / "
    "+{added} B, commit {commit}",
    "distill.auto.report.skipped": "## Saltadas ({n})",
    "distill.auto.report.skipped_row": "- {card}: {keys}",
    "distill.auto.report.not_included": "## No incluidas",
    "distill.auto.report.user_skipped": "- --skip: {cards}",
    "distill.auto.report.distilled_before": "- ya destiladas: {cards}",
    "distill.auto.report.no_card": "- copias sin ficha: {bytes} B",
    "distill.auto.report.agents": "## AGENTS.md\n{b0} B / {l0} líneas → {b1} B / {l1} líneas "
    "({db} B, {dl} líneas)",
    "distill.auto.report.tighten": "## Trinquete\nMáximos que bajaron: {items}",
    "distill.auto.report.tighten_none": "## Trinquete\nNingún máximo bajó.",
    "distill.auto.report.pending": "## Llenadas pero no aplicadas\n{paths}",
    "distill.auto.report.commands": "## Comandos",
    "distill.auto.report.merge": "Fusionar: `{cmd}`",
    "distill.auto.report.undo": "Deshacer: `{cmd}`",
    "distill.auto.report.none": "(ninguna)",
    "distill.auto.undo.done": "Lote {n} deshecho. Las fichas llenadas se dejaron como estaban "
    "antes del lote. La rama {branch} y la marca {tag} se conservan.",
    "distill.auto.undo.ask": "El lote {n} está fusionado en {branch}. Escribe «confirmo» "
    "para revertir estos {k} commits: ",
    "distill.auto.undo.commit_row": "  {sha} {subject} [{files}]",
}


MESSAGES["en"].update(_REORG_EN)
MESSAGES["es"].update(_REORG_ES)
MESSAGES["en"].update(_DISTILL_EN)
MESSAGES["es"].update(_DISTILL_ES)
MESSAGES["en"].update(_DISTILL_APPLY_EN)
MESSAGES["es"].update(_DISTILL_APPLY_ES)
MESSAGES["en"].update(_DISTILL_AUTO_EN)
MESSAGES["es"].update(_DISTILL_AUTO_ES)


def t(key: str, lang: str, **kw: object) -> str:
    """Translate ``key`` into ``lang``; fall back to English, then to the key itself.

    Keyword arguments are applied with ``str.format``. A template whose placeholders
    do not match ``kw`` is returned unformatted instead of raising.
    """
    template = MESSAGES.get(lang, {}).get(key)
    if template is None:
        template = MESSAGES["en"].get(key)
    if template is None:
        return key
    if not kw:
        return template
    try:
        return template.format(**kw)
    except (KeyError, IndexError, ValueError):
        return template
