"""Command-line interface: ``declutter measure | radar | init | hook | statusline | reorg``
and ``declutter ratchet``.

Exit codes:

- 0 ok (``reorg verify`` with "edited after" states included: they are informative);
- 1 usage error (arguments, a missing confirmation, a git precondition, no TTY without
  ``--yes``, ``AGENTS.override.md``, ``verify`` without the local run folder, ...);
- 2 internal error or unprocessable source (unsafe read, not UTF-8, NUL byte, unclosed
  fence, comment or front matter, ...);
- 3 threshold reached (``measure --fail-on``, Codex budget over 100 %, ratchet failure);
- 4 invalid plan or failed proof (``reorg check``, ``reorg verify`` C0-C9, guards G1-G4;
  ``reorg set`` gives 0 when only OTHER units remain pending, and prints how many);
- 5 secret detected (G5);
- 130 interrupted with Ctrl-C (``reorg apply`` restores before returning).

Precedence when several apply (``reorg plan|check|apply|verify|distill-*``,
``ratchet check``):
5 > 2 > 1 > 4 > 3, and 130 overrides everything. Every failure found is listed, not only
the one that decides the code.

``declutter reorg`` and ``declutter ratchet`` take the project root with ``-C DIR`` (default: the
current folder), never as a positional argument.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import NoReturn

from declutter_context import (
    __version__,
    codexcfg,
    config,
    fsutil,
    hooks,
    loadset,
    radar,
    ratchet,
    render,
    scaffold,
)
from declutter_context import measure as measure_mod
from declutter_context import reorg as reorg_pkg
from declutter_context.i18n import t
from declutter_context.model import Config, Level, Project, ProofResult, RegistryEntry
from declutter_context.reorg import Issue, ReorgError, StdPrompter
from declutter_context.reorg import apply as reorg_apply
from declutter_context.reorg import distill as reorg_distill
from declutter_context.reorg import distill_apply as reorg_distill_apply
from declutter_context.reorg import distill_auto as reorg_distill_auto
from declutter_context.reorg import distill_auto_report as reorg_distill_auto_report
from declutter_context.reorg import gitops as reorg_gitops
from declutter_context.reorg import manifest as reorg_manifest
from declutter_context.reorg import proof as reorg_proof
from declutter_context.reorg import render as reorg_render
from declutter_context.reorg import undo as reorg_undo
from declutter_context.reorg.lexicon import build_lexicon

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INTERNAL = 2
EXIT_THRESHOLD = 3
EXIT_PROOF = reorg_pkg.EXIT_PROOF  # 4
EXIT_SECRET = reorg_pkg.EXIT_SECRET  # 5
EXIT_INTERRUPTED = reorg_pkg.EXIT_INTERRUPTED  # 130

_LANGS = ("es", "en")

# CLI-only messages (the shared i18n catalogue is owned by another module).
_MSG: dict[str, dict[str, str]] = {
    "en": {
        "no_registry": (
            "No projects registered ({path}). Use: declutter radar --discover <folder> --save"
        ),
        "discover_none": "No new projects found under {base}.",
        "discover_found": "Projects found under {base} (not in the registry):",
        "discover_saved": "Saved {n} project(s) to {path}.",
        "discover_hint": "Nothing written. Add --save to register them.",
        "md_written": "Wrote {path}",
        "init_plan": "Would create ({n}):",
        "init_nothing": "Nothing to create: all files already exist.",
        "init_created": "Created ({n}):",
        "init_big_claude": (
            "Note: CLAUDE.md is large (S1 is red). It was not touched; "
            "tidying it is a phase-2 task. Run: declutter measure"
        ),
        "init_add_import": (
            "Your CLAUDE.md does not import AGENTS.md, so Claude will not load it. "
            "Add this line to CLAUDE.md by hand: @AGENTS.md"
        ),
        "not_dir": "not a directory: {path}",
    },
    "es": {
        "no_registry": (
            "No hay repositorios registrados ({path}). "
            "Usa: declutter radar --discover <carpeta> --save"
        ),
        "discover_none": "No se encontraron repositorios nuevos en {base}.",
        "discover_found": "Repositorios encontrados en {base} (fuera del registro):",
        "discover_saved": "Se guardaron {n} proyecto(s) en {path}.",
        "discover_hint": "No se escribió nada. Agrega --save para registrarlos.",
        "md_written": "Escrito {path}",
        "init_plan": "Crearía ({n}):",
        "init_nothing": "Nada que crear: todos los archivos ya existen.",
        "init_created": "Creados ({n}):",
        "init_big_claude": (
            "Nota: CLAUDE.md es grande (S1 en rojo). No se tocó; "
            "ordenarlo es tarea de la fase 2. Ejecuta: declutter measure"
        ),
        "init_add_import": (
            "Tu CLAUDE.md no importa AGENTS.md, así que Claude no lo cargará. "
            "Agrega a mano esta línea a CLAUDE.md: @AGENTS.md"
        ),
        "not_dir": "no es un directorio: {path}",
    },
}


def _m(key: str, lang: str, **kw: object) -> str:
    table = _MSG.get(lang, _MSG["en"])
    return table.get(key, _MSG["en"][key]).format(**kw)


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors exit with code 1 instead of 2."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="declutter", description="Measure and track AI-agent context without spending tokens."
    )
    p.add_argument("--version", action="version", version=f"declutter {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    m = sub.add_parser("measure", help="measure a project and print traffic-light signals")
    m.add_argument("path", nargs="?", default=".", help="project folder (default: .)")
    m.add_argument("--json", action="store_true", help="print JSON")
    m.add_argument("--fail-on", choices=("yellow", "red"), help="exit 3 when level is reached")
    m.add_argument("--lang", choices=_LANGS, help="output language")

    r = sub.add_parser("radar", help="overview of all registered projects")
    r.add_argument("--registry", type=Path, help="registry file (default: user config)")
    r.add_argument("--md", nargs="?", const="RADAR.md", metavar="FILE", help="write Markdown")
    r.add_argument("--json", action="store_true", help="print JSON")
    r.add_argument("--discover", type=Path, metavar="DIR", help="list candidate projects in DIR")
    r.add_argument("--save", action="store_true", help="with --discover: add them to the registry")
    r.add_argument("--lang", choices=_LANGS, help="output language")

    i = sub.add_parser("init", help="create the missing context files in a project")
    i.add_argument("path", nargs="?", default=".", help="project folder (default: .)")
    i.add_argument("--lang", choices=_LANGS, help="template language")
    i.add_argument("--dry-run", action="store_true", help="show what would be created")

    h = sub.add_parser("hook", help="Claude Code hook entry points")
    hsub = h.add_subparsers(dest="hook_name", metavar="<hook>")
    hsub.add_parser("session-start", help="SessionStart hook (reads JSON on stdin)")

    sub.add_parser("statusline", help="status line (reads JSON on stdin)")
    _add_reorg(sub)
    _add_ratchet(sub)
    return p


def _with_root(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """``-C DIR`` (default: the current folder); ``reorg``/``ratchet`` never take a
    positional root (spec §10.1)."""
    p.add_argument(
        "-C", dest="root", default=".", metavar="DIR", help="project folder (default: .)"
    )
    return p


def _line_range(value: str) -> tuple[int, int]:
    """``A-B`` with 1 <= A <= B (``ratchet repin --lines``)."""
    a, sep, b = value.partition("-")
    if sep and a.isdigit() and b.isdigit() and 1 <= int(a) <= int(b):
        return int(a), int(b)
    raise argparse.ArgumentTypeError(f"expected A-B with 1 <= A <= B, got {value!r}")


def _positive(value: str) -> int:
    if value.isdigit() and int(value) >= 1:
        return int(value)
    raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")


def _card_id(value: str) -> str:
    """``D-NNN`` (3 or more digits), as ``build`` names the cards."""
    if re.fullmatch(r"D-\d{3,}", value):
        return value
    raise argparse.ArgumentTypeError(f"expected D-NNN, got {value!r}")


def _add_reorg(sub: argparse._SubParsersAction[_Parser]) -> None:
    r = sub.add_parser("reorg", help="lossless reorganization of CLAUDE.md / AGENTS.md")
    r.set_defaults(parser_for_help=r)
    rsub = r.add_subparsers(dest="reorg_command", metavar="<subcommand>")

    g = _with_root(rsub.add_parser("guide", help="guided flow (TTY); without a TTY, a dry run"))
    g.add_argument("--lang", choices=_LANGS, help="output language")

    p = _with_root(rsub.add_parser("plan", help="measure, scan, classify, project, validate"))
    p.add_argument("--dry-run", action="store_true", help="write nothing anywhere")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.add_argument("--source", action="append", default=[], metavar="F", help="extra *.md source")
    p.add_argument("--from", dest="from_id", metavar="ID", help="reuse overrides of run ID")
    p.add_argument("--lang", choices=_LANGS, help="plan and output language")

    c = _with_root(rsub.add_parser("check", help="revalidate a run's manifest"))
    c.add_argument("id", metavar="ID")
    c.add_argument("--json", action="store_true", help="print the report as JSON")

    s = _with_root(rsub.add_parser("set", help="record an override (non-interactive)"))
    s.add_argument("id", metavar="ID")
    s.add_argument("unit", metavar="UNIT", help="unit id or heading_key")
    s.add_argument("--dest", metavar="D", help="destination (L1, L1c, L2:<dir>, L4, L5t, L5m)")
    s.add_argument("--card", choices=("yes", "no"), help="decision card (L4 only)")
    s.add_argument("--keep", action="store_true", help="keep in L1")
    s.add_argument("--accept", action="store_true", help="accept the proposed destination")
    s.add_argument("--reason", metavar="TEXT", help="reason of the unit override")
    s.add_argument("--not-safety", metavar="BLOCK", help="mark BLOCK as not a safety rule")
    s.add_argument("--ack-reason", metavar="TEXT", help="reason of the guard_ack (G9)")

    a = _with_root(rsub.add_parser("apply", help="apply a run (transaction, spec §11)"))
    a.add_argument("id", metavar="ID")
    a.add_argument("--yes", action="store_true", help="answer the apply question (no TTY)")
    a.add_argument("--commit", action="store_true", help="commit on the reorg branch")
    a.add_argument("--no-git", action="store_true", help="apply outside a git repository")
    a.add_argument("--dry-run", action="store_true", help="build and verify, then discard")
    a.add_argument("--replace-symlink", action="store_true", help="replace a CLAUDE.md symlink")

    v = _with_root(rsub.add_parser("verify", help="lossless proof of an applied run"))
    v.add_argument("id", metavar="ID")
    v.add_argument("--rev", metavar="REV", help="read the files from a git revision")
    v.add_argument("--strict", action="store_true", help="fail on any later edit")
    v.add_argument("--json", action="store_true", help="print JSON")

    u = _with_root(rsub.add_parser("undo", help="undo a run"))
    u.add_argument("id", metavar="ID")
    u.add_argument("--yes", action="store_true", help="confirm without a TTY")

    dc = _with_root(
        rsub.add_parser("distill-check", help="may each card's invariant replace its copies?")
    )
    dc.add_argument("id", metavar="ID")
    dc.add_argument("--card", type=_card_id, metavar="D-NNN", help="check only this card")
    dc.add_argument("--json", action="store_true", help="print JSON")
    dc.add_argument("--lang", choices=_LANGS, help="output language")

    da = _with_root(
        rsub.add_parser("distill-apply", help="replace one card's copies by its invariant")
    )
    da.add_argument("id", metavar="ID")
    da.add_argument("--card", type=_card_id, required=True, metavar="D-NNN", help="the card")
    da.add_argument("--commit", action="store_true", help="commit on the distill branch")
    da.add_argument("--yes", action="store_true", help="resume the commit without a TTY")
    da.add_argument("--no-git", action="store_true", help="distill outside a git repository")
    da.add_argument("--dry-run", action="store_true", help="build and verify, then discard")
    da.add_argument("--lang", choices=_LANGS, help="output language")

    dauto = _with_root(
        rsub.add_parser("distill-auto", help="apply every ready card in one batch branch")
    )
    dauto.add_argument("id", metavar="ID")
    dauto.add_argument(
        "--skip",
        type=_card_id,
        action="append",
        default=[],
        metavar="D-NNN",
        help="leave this card out of the batch (repeatable)",
    )
    dauto.add_argument("--json", action="store_true", help="print the report as JSON")
    dauto.add_argument("--dry-run", action="store_true", help="run on a temporary copy")
    dauto.add_argument("--lang", choices=_LANGS, help="output language")

    du = _with_root(rsub.add_parser("distill-undo", help="undo the last distillation (LIFO)"))
    du.add_argument("id", metavar="ID")
    which = du.add_mutually_exclusive_group(required=True)
    which.add_argument("--card", type=_card_id, metavar="D-NNN", help="the card")
    which.add_argument("--auto", type=_positive, metavar="N", help="the whole batch N")
    du.add_argument("--yes", action="store_true", help="confirm without a TTY")
    du.add_argument("--lang", choices=_LANGS, help="output language")

    st = _with_root(rsub.add_parser("status", help="list runs and their state"))
    st.add_argument("id", nargs="?", metavar="ID")
    st.add_argument("--json", action="store_true", help="print JSON")

    pr = _with_root(rsub.add_parser("prune", help="delete a run's local literal copies"))
    pr.add_argument("id", metavar="ID")
    pr.add_argument("--yes", action="store_true", help="confirm without a TTY")

    cb = _with_root(
        rsub.add_parser(
            "codex-budget",
            help="write a project-scoped Codex budget (.codex/config.toml + declutter.toml)",
        )
    )
    cb.add_argument(
        "--bytes",
        type=_positive,
        default=codexcfg.DEFAULT_BYTES,
        metavar="N",
        help=f"budget in bytes (default: {codexcfg.DEFAULT_BYTES})",
    )
    cb.add_argument("--dry-run", action="store_true", help="print what would be written")
    cb.add_argument("--lang", choices=_LANGS, help="output language")


def _add_ratchet(sub: argparse._SubParsersAction[_Parser]) -> None:
    r = sub.add_parser("ratchet", help="guard against context regrowth")
    r.set_defaults(parser_for_help=r)
    rsub = r.add_subparsers(dest="ratchet_command", metavar="<subcommand>")

    c = _with_root(rsub.add_parser("check", help="rules R1-R6 (writes nothing)"))
    c.add_argument("--staged", action="store_true", help="check the git index")
    c.add_argument("--json", action="store_true", help="print JSON")

    x = _with_root(rsub.add_parser("relax", help="raise one maximum, with audit"))
    grp = x.add_mutually_exclusive_group(required=True)
    grp.add_argument("--s1-to", type=_positive, metavar="N", help="new S1 maximum (lines)")
    grp.add_argument("--s3-to", type=_positive, metavar="PCT", help="new S3 maximum (%%)")
    x.add_argument("--reason", required=True, metavar="TEXT", help="reason (audited)")

    up = _with_root(rsub.add_parser("unpin", help="release one pin, with audit"))
    up.add_argument("pin", metavar="P-NNN")
    up.add_argument("--reason", required=True, metavar="TEXT", help="reason (audited)")

    rp = _with_root(rsub.add_parser("repin", help="record the new sha of an edited block"))
    rp.add_argument("pin", metavar="P-NNN")
    rp.add_argument("--lines", required=True, type=_line_range, metavar="A-B", help="block lines")
    rp.add_argument("--reason", required=True, metavar="TEXT", help="reason (audited)")


def _err(msg: str) -> int:
    print(f"declutter: error: {msg}", file=sys.stderr)
    return EXIT_USAGE


def _emit_warnings() -> None:
    for w in config.last_warnings():
        print(f"declutter: warning: {w}", file=sys.stderr)


def _effective_config(root: Path | None, lang: str | None) -> Config:
    cfg = config.load_config(root)
    _emit_warnings()
    if lang:
        cfg = dataclasses.replace(cfg, lang=lang)
    return cfg


def _cmd_measure(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.is_dir():
        return _err(_m("not_dir", "en", path=args.path))
    cfg = _effective_config(root.resolve(), args.lang)
    m = measure_mod.measure(Project.at(root), cfg)
    if args.json:
        print(render.measurement_json(m))
    else:
        print(render.measurement_text(m, cfg.lang, cfg.bytes_per_token))
    if args.fail_on:
        limit = Level.RED if args.fail_on == "red" else Level.YELLOW
        if m.level >= limit:
            return EXIT_THRESHOLD
    return EXIT_OK


def _cmd_radar(args: argparse.Namespace) -> int:
    cfg = _effective_config(None, args.lang)
    lang = cfg.lang
    reg_path: Path = args.registry or config.registry_path()
    entries = radar.load_registry(reg_path)
    for w in radar.last_warnings():
        print(f"declutter: warning: {w}", file=sys.stderr)

    if args.discover is not None:
        if not Path(args.discover).is_dir():
            return _err(_m("not_dir", "en", path=str(args.discover)))
        # Absolute paths, so the registry works from any working directory.
        base: Path = Path(args.discover).resolve()
        known = {e.path.resolve() for e in entries}
        new = [p.resolve() for p in radar.discover(base) if p.resolve() not in known]
        if not new:
            print(_m("discover_none", lang, base=base))
            return EXIT_OK
        print(_m("discover_found", lang, base=base))
        for p in new:
            print(f"  {p}")
        if args.save:
            added = [RegistryEntry(path=p, name=p.name or str(p)) for p in new]
            radar.save_registry(reg_path, [*entries, *added])
            print(_m("discover_saved", lang, n=len(added), path=reg_path))
        else:
            print(_m("discover_hint", lang))
        return EXIT_OK

    if not entries:
        print(_m("no_registry", lang, path=reg_path))
        return EXIT_OK
    rows = radar.build_rows(entries)
    if args.md is not None:
        out = Path(args.md)
        out.write_text(render.radar_markdown(rows, lang, date.today()), encoding="utf-8")
        print(_m("md_written", lang, path=out))
    elif args.json:
        print(render.radar_json(rows))
    else:
        print(render.radar_text(rows, lang))
    return EXIT_OK


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.is_dir():
        return _err(_m("not_dir", "en", path=args.path))
    lang = args.lang or config.default_lang()
    claude_existed = (root / "CLAUDE.md").is_file()
    if args.dry_run:
        plan = scaffold.plan_init(root, lang)
        if plan:
            print(_m("init_plan", lang, n=len(plan)))
            for rel in plan:
                print(f"  {rel}")
        else:
            print(_m("init_nothing", lang))
    else:
        created = scaffold.apply_init(root, lang, date.today())
        if created:
            print(_m("init_created", lang, n=len(created)))
            for rel in created:
                print(f"  {rel}")
        else:
            print(_m("init_nothing", lang))
    if claude_existed:
        cfg = _effective_config(root.resolve(), lang)
        m = measure_mod.measure(Project.at(root), cfg)
        s1 = next((s for s in m.signals if s.id == "S1"), None)
        if s1 is not None and s1.level is Level.RED:
            print()
            print(render.measurement_text(m, lang, cfg.bytes_per_token))
            print()
            print(_m("init_big_claude", lang))
        if not _imports_agents(root / "CLAUDE.md"):
            print(_m("init_add_import", lang))
    return EXIT_OK


def _imports_agents(claude_md: Path) -> bool:
    """True if CLAUDE.md has an ``@AGENTS.md`` import (or cannot be read: stay quiet)."""
    text = fsutil.safe_read_text(claude_md, claude_md.parent.resolve())
    if text is None:
        return True
    return any(Path(raw).name == "AGENTS.md" for raw in loadset.extract_imports(text))


def _cmd_hook(args: argparse.Namespace) -> int:
    if args.hook_name != "session-start":
        return _err("hook: expected 'session-start'")
    try:
        out = hooks.session_start(sys.stdin.read())
        if out:
            print(out)
    except Exception:  # hooks must never fail the session
        pass
    return EXIT_OK


def _cmd_statusline(_args: argparse.Namespace) -> int:
    try:
        out = hooks.statusline(sys.stdin.read())
        if out:
            print(out)
    except Exception:  # the status line must never fail
        pass
    return EXIT_OK


# --------------------------------------------------------------------------- reorg, ratchet


def _now() -> datetime:
    """The clock of ``reorg``/``ratchet`` (injectable in tests)."""
    return datetime.now()


def _open(args: argparse.Namespace, lang_arg: str | None = None) -> tuple[Project, Config] | None:
    """The project at ``-C DIR`` and its effective configuration; ``None`` (after an error
    message) when DIR is not a directory."""
    root = Path(args.root)
    if not root.is_dir():
        _err(_m("not_dir", "en", path=args.root))
        return None
    project = Project.at(root)
    return project, _effective_config(project.root, lang_arg)


def _fail_lines(issues: Sequence[Issue], lang: str) -> None:
    """Every failure on stderr, metadata only (spec §7, G6)."""
    for i in issues:
        print(reorg_render.failure_line(i, lang), file=sys.stderr)


def _print_outcome(
    out: reorg_manifest.PlanOutcome, as_json: bool, lang: str, root: str | None = None
) -> None:
    if as_json:
        print(reorg_render.outcome_json(reorg_manifest.outcome_report(out)))
    else:
        m = out.manifest
        print(reorg_render.plan_table(m, lang, unit_bytes=out.unit_bytes))
        print(reorg_render.projection_text(m.projection, m.warnings, lang))
        if out.code == EXIT_THRESHOLD and out.levers:
            budget = m.budgets.codex_budget_bytes
            print(reorg_render.levers_text(out.levers, lang, codex_budget=budget, root=root))
        if out.run_dir is not None:
            print(t("reorg.menu.saved", lang, id=m.id))
    _fail_lines(out.issues, lang)


def _cmd_reorg_guide(args: argparse.Namespace) -> int:
    opened = _open(args, args.lang)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    return reorg_render.run_guide(project, cfg, StdPrompter(), now=_now(), lang=cfg.lang)


def _cmd_reorg_plan(args: argparse.Namespace) -> int:
    opened = _open(args, args.lang)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    req = reorg_manifest.PlanRequest(
        project=project,
        cfg=cfg,
        lang=cfg.lang,
        now=_now(),
        dry_run=args.dry_run,
        extra_sources=tuple(args.source),
        from_id=args.from_id,
    )
    try:
        out = reorg_manifest.plan_project(req)
    except ReorgError as exc:
        _fail_lines(exc.issues, cfg.lang)
        return exc.code
    _print_outcome(out, args.json, cfg.lang, args.root)
    return out.code


def _cmd_reorg_check(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    try:
        out = reorg_manifest.check_run(project, cfg, args.id, now=_now())
    except ReorgError as exc:
        _fail_lines(exc.issues, cfg.lang)
        return exc.code
    _print_outcome(out, args.json, cfg.lang, args.root)
    return out.code


def _cmd_reorg_set(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    card = None if args.card is None else args.card == "yes"
    try:
        out = reorg_manifest.set_override(
            project,
            cfg,
            args.id,
            args.unit,
            dest=args.dest,
            card=card,
            keep=args.keep,
            accept=args.accept,
            reason=args.reason,
            not_safety=args.not_safety,
            ack_reason=args.ack_reason,
            now=_now(),
        )
    except ReorgError as exc:
        _fail_lines(exc.issues, cfg.lang)
        return exc.code
    code, issues, pending = reorg_manifest.set_exit(out, args.unit)
    _fail_lines(issues, cfg.lang)
    if pending:
        print(t("reorg.set.pending_left", cfg.lang, n=len(pending), ids=", ".join(pending)))
    return code


def _apply_result(reorg_id: str, res: reorg_apply.ApplyOutcome, lang: str) -> int:
    _fail_lines(res.issues, lang)
    state = "planned" if res.journal is None else res.journal.state
    print(f"{reorg_id} {state}")
    return res.code


def _cmd_reorg_apply(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    opts = reorg_apply.ApplyOptions(
        yes=args.yes,
        commit=args.commit,
        no_git=args.no_git,
        dry_run=args.dry_run,
        replace_symlink=args.replace_symlink,
    )
    res = reorg_apply.apply(project, cfg, args.id, opts, StdPrompter(), now=_now(), lang=cfg.lang)
    return _apply_result(args.id, res, cfg.lang)


def _proof_json(res: ProofResult, code: int) -> str:
    def rng(r: tuple[int, int] | None) -> list[int] | None:
        return None if r is None else [r[0], r[1]]

    obj: dict[str, object] = {
        "mode": res.mode,
        "ok": res.ok,
        "code": code,
        "failures": [
            {
                "check": f.check,
                "unit": f.unit,
                "source": f.source,
                "src": rng(f.src),
                "dest": f.dest,
                "at": rng(f.at),
                "reason": f.reason,
            }
            for f in res.failures
        ],
        "states": [{"unit": u, "state": s} for u, s in res.states],
    }
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _cmd_reorg_verify(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    lang = cfg.lang
    try:
        res = reorg_undo.verify_run(project, cfg, args.id, rev=args.rev, strict=args.strict)
    except ReorgError as exc:
        _fail_lines(exc.issues, lang)
        return exc.code
    code = reorg_proof.exit_code(res)
    if args.json:
        print(_proof_json(res, code))
    else:
        for unit, state in res.states:
            print(f"{unit} {t(f'reorg.{state}', lang)}")
    for f in res.failures:
        print(reorg_render.failure_line(f, lang), file=sys.stderr)
    return code


def _cmd_reorg_distill_check(args: argparse.Namespace) -> int:
    opened = _open(args, args.lang)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    lang = cfg.lang
    try:
        rep = reorg_distill.check(
            project.root,
            args.id,
            card=args.card,
            lexicon=build_lexicon(cfg.reorg.extra_lexicon),
        )
    except ReorgError as exc:
        _fail_lines(exc.issues, lang)
        return exc.code
    if args.json:
        sys.stdout.write(reorg_distill.report_json(rep).decode("utf-8"))
    else:
        for line in reorg_distill.report_lines(rep, lang):
            print(line)
    return rep.code


def _distill_result(reorg_id: str, card: str, res: reorg_apply.ApplyOutcome, lang: str) -> int:
    _fail_lines(res.issues, lang)
    state = "planned" if res.journal is None else res.journal.state
    print(f"{reorg_id} {card} {state}")
    return res.code


def _cmd_reorg_distill_apply(args: argparse.Namespace) -> int:
    opened = _open(args, args.lang)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    opts = reorg_distill_apply.DistillOptions(
        commit=args.commit, yes=args.yes, no_git=args.no_git, dry_run=args.dry_run
    )
    res = reorg_distill_apply.apply(
        project, cfg, args.id, args.card, opts, StdPrompter(), now=_now(), lang=cfg.lang
    )
    return _distill_result(args.id, args.card, res, cfg.lang)


class _AutoPrompter:
    """Notices of ``distill-auto``: never reads stdin, never a TTY; to stderr or stdout."""

    def __init__(self, to_stderr: bool) -> None:
        self._err = to_stderr

    def is_tty(self) -> bool:
        return False

    def ask(self, prompt: str) -> str:
        return ""

    def say(self, text: str) -> None:
        print(text, file=sys.stderr if self._err else sys.stdout, flush=True)


def _cmd_reorg_distill_auto(args: argparse.Namespace) -> int:
    opened = _open(args, args.lang)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    opts = reorg_distill_auto.AutoOptions(skip=tuple(args.skip), dry_run=args.dry_run)
    res = reorg_distill_auto.run(
        project, cfg, args.id, opts, _AutoPrompter(args.json), now=_now(), lang=cfg.lang
    )
    _fail_lines(res.issues, cfg.lang)
    if args.json:
        if res.report is not None:
            print(json.dumps(reorg_distill_auto_report.report_obj(res.report), ensure_ascii=False))
        else:
            # No report (nothing to apply, or refused before a batch): keys only, no text.
            print(
                json.dumps(
                    {
                        "schema": "declutter.distill-auto-report/1",
                        "state": "refused" if res.issues else "nothing",
                        "issues": list(dict.fromkeys(i.key for i in res.issues)),
                    },
                    ensure_ascii=False,
                )
            )
    return res.code


def _cmd_reorg_distill_undo(args: argparse.Namespace) -> int:
    opened = _open(args, args.lang)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    if args.auto is not None:
        out = reorg_distill_auto.undo_batch(
            project, cfg, args.id, args.auto, StdPrompter(), now=_now(), lang=cfg.lang
        )
        _fail_lines(out.issues, cfg.lang)
        return out.code
    res = reorg_distill_apply.undo(
        project, cfg, args.id, args.card, StdPrompter(), yes=args.yes, now=_now(), lang=cfg.lang
    )
    return _distill_result(args.id, args.card, res, cfg.lang)


def _cmd_reorg_undo(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    res = reorg_undo.undo(project, args.id, StdPrompter(), yes=args.yes, lang=cfg.lang)
    return _apply_result(args.id, res, cfg.lang)


def _cmd_reorg_status(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    try:
        rows = reorg_undo.status(project.root, args.id)
    except ReorgError as exc:
        _fail_lines(exc.issues, cfg.lang)
        return exc.code
    distills = {r.id: reorg_distill_apply.status(project.root, r.id) for r in rows}
    if args.json:
        data = [
            {**dataclasses.asdict(r), "distills": [dataclasses.asdict(d) for d in distills[r.id]]}
            for r in rows
        ]
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            flag = f" ({t('reorg.interrupted', cfg.lang)})" if r.interrupted else ""
            commit = f" {r.commit}" if r.commit else ""
            print(f"{r.id} {r.state}{flag}{commit}")
            for d in distills[r.id]:
                dflag = f" ({t('reorg.interrupted', cfg.lang)})" if d.interrupted else ""
                line = t(
                    "distill.status.line",
                    cfg.lang,
                    card=d.card,
                    state=d.state,
                    flag=dflag,
                    date=d.date or "-",
                )
                print(line + (f" {d.commit}" if d.commit else ""))
    return EXIT_OK


def _cmd_reorg_prune(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    res = reorg_undo.prune(project, args.id, StdPrompter(), yes=args.yes)
    return _apply_result(args.id, res, cfg.lang)


def _codex_budget_ignored(root: Path) -> str | None:
    """``"<ignore file>:<line>"`` when git would ignore ``.codex/config.toml``."""
    try:
        if not reorg_gitops.is_repo(root):
            return None
        found = reorg_gitops.ignored(root, [codexcfg.CODEX_CONFIG])
    except reorg_gitops.GitError:
        return None
    return found[0][1] if found else None


def _cmd_reorg_codex_budget(args: argparse.Namespace) -> int:
    opened = _open(args, args.lang)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    lang = cfg.lang
    root = project.root
    try:
        writes = codexcfg.prepare(root, args.bytes, t("reorg.codexcfg.header", lang))
    except codexcfg.CodexCfgError as exc:
        _fail_lines([Issue(code=EXIT_USAGE, key=exc.key, source=exc.path or None)], lang)
        return EXIT_USAGE
    for w in writes:
        mode = t(f"reorg.codexcfg.mode.{'created' if w.created else 'updated'}", lang)
        if not w.changed:
            print(t("reorg.codexcfg.unchanged", lang, path=w.rel, line=w.line))
            continue
        if args.dry_run:
            print(t("reorg.codexcfg.would_write", lang, path=w.rel, line=w.line, mode=mode))
            continue
        try:
            codexcfg.write(root, w)
        except codexcfg.CodexCfgError as exc:
            _fail_lines([Issue(code=EXIT_INTERNAL, key=exc.key, source=exc.path or None)], lang)
            return EXIT_INTERNAL
        print(t("reorg.codexcfg.wrote", lang, path=w.rel, line=w.line, mode=mode))
    where = _codex_budget_ignored(root)
    if where is not None:
        issue = Issue(code=0, key="codex_config_ignored", source=codexcfg.CODEX_CONFIG)
        print(
            f"declutter: warning: {reorg_render.failure_line(issue, lang)} ({where})",
            file=sys.stderr,
        )
    print(t("reorg.codexcfg.trust", lang))
    print(f"  [projects.{json.dumps(str(root))}]")
    print('  trust_level = "trusted"')
    return EXIT_OK


_REORG_COMMANDS = {
    "guide": _cmd_reorg_guide,
    "plan": _cmd_reorg_plan,
    "check": _cmd_reorg_check,
    "set": _cmd_reorg_set,
    "apply": _cmd_reorg_apply,
    "verify": _cmd_reorg_verify,
    "distill-check": _cmd_reorg_distill_check,
    "distill-apply": _cmd_reorg_distill_apply,
    "distill-auto": _cmd_reorg_distill_auto,
    "distill-undo": _cmd_reorg_distill_undo,
    "undo": _cmd_reorg_undo,
    "status": _cmd_reorg_status,
    "prune": _cmd_reorg_prune,
    "codex-budget": _cmd_reorg_codex_budget,
}


def _ratchet_line(f: ratchet.RatchetFailure, lang: str) -> str:
    text = t(f"ratchet.{f.rule}", lang)
    if text == f"ratchet.{f.rule}":
        text = f.rule
    detail = f" ({f.detail})" if f.detail else ""
    return f"{f.rule} {f.path}: {text}{detail}"


def _cmd_ratchet_check(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    report = ratchet.check(project, cfg, staged=args.staged)
    if args.json:
        obj = {
            "code": report.code,
            "failures": [dataclasses.asdict(f) for f in report.failures],
        }
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    for f in report.failures:
        print(_ratchet_line(f, cfg.lang), file=sys.stderr)
    return report.code


def _cmd_ratchet_relax(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    return ratchet.relax(
        project,
        s1_to=args.s1_to,
        s3_to=args.s3_to,
        reason=args.reason,
        prompter=StdPrompter(),
        today=_now().date(),
        lang=cfg.lang,
    )


def _cmd_ratchet_unpin(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    return ratchet.unpin(
        project,
        args.pin,
        reason=args.reason,
        prompter=StdPrompter(),
        today=_now().date(),
        lang=cfg.lang,
    )


def _cmd_ratchet_repin(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return EXIT_USAGE
    project, cfg = opened
    return ratchet.repin(
        project,
        args.pin,
        lines=args.lines,
        reason=args.reason,
        prompter=StdPrompter(),
        lexicon=build_lexicon(cfg.reorg.extra_lexicon),
        today=_now().date(),
        lang=cfg.lang,
    )


_RATCHET_COMMANDS = {
    "check": _cmd_ratchet_check,
    "relax": _cmd_ratchet_relax,
    "unpin": _cmd_ratchet_unpin,
    "repin": _cmd_ratchet_repin,
}


def _cmd_reorg(args: argparse.Namespace) -> int:
    name = args.reorg_command
    if not name:
        args.parser_for_help.print_help(sys.stderr)
        return EXIT_USAGE
    return _REORG_COMMANDS[name](args)


def _cmd_ratchet(args: argparse.Namespace) -> int:
    name = args.ratchet_command
    if not name:
        args.parser_for_help.print_help(sys.stderr)
        return EXIT_USAGE
    return _RATCHET_COMMANDS[name](args)


_COMMANDS = {
    "measure": _cmd_measure,
    "radar": _cmd_radar,
    "init": _cmd_init,
    "hook": _cmd_hook,
    "statusline": _cmd_statusline,
    "reorg": _cmd_reorg,
    "ratchet": _cmd_ratchet,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if not args.command:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    try:
        return _COMMANDS[args.command](args)
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as exc:
        print(f"declutter: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
