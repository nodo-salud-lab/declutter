"""Text rendering of ``declutter reorg`` and the guided flow with its menu (spec §10.2-§10.4).

Everything printed here is metadata only (G6): titles cut to 60 characters, ids, ranges,
counts, bytes, categories and paths. File content is never read into a message.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime

from .. import measure
from ..i18n import t
from ..model import Config, Project, ProofFailure
from . import EXIT_OK, EXIT_THRESHOLD, Issue, Prompter, ReorgError, gitops
from . import apply as ap
from . import manifest as mf
from .manifest import Manifest, UnitEntry

SEP = " · "
SIGNALS = ("S1", "S2", "S3", "S4")
# Keys that change where a unit goes, so G2 rejects them (spec §10.3).
_G2_REJECTS = frozenset("hztmcs")
_DEST_KEYS: Mapping[str, str] = {"h": "L4", "t": "L5t", "m": "L5m", "c": "L1c"}
_UNIT_KEYS = frozenset("kshztmcfu")
_CLOSING_WARNINGS = ("relative_link_moved", "import_moved", "import_depth", "import_codex")


# --------------------------------------------------------------------------- helpers


def _msg(key: str, lang: str, **kw: object) -> str:
    """``reorg.<key>`` from the catalog; the bare key when the catalog has none."""
    text = t(f"reorg.{key}", lang, **kw)
    return key if text == f"reorg.{key}" else text


def _rng(r: tuple[int, int]) -> str:
    return str(r[0]) if r[0] == r[1] else f"{r[0]}-{r[1]}"


def _num(v: object) -> str:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return "n/a"
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _count(u: UnitEntry, name: str) -> int:
    """``K``/``S`` from the classifier reasons (``"K=2"``); 0 when absent."""
    for r in u.reasons:
        if r.startswith(name + "="):
            try:
                return int(r.split("=", 1)[1])
            except ValueError:
                return 0
    return 0


def _safety(u: UnitEntry) -> bool:
    return _count(u, "S") >= 1 or any(b.guard for b in u.blocks)


def _title(u: UnitEntry, lang: str) -> str:
    if not u.heading:
        return _msg("preamble", lang)
    return mf.truncate60(u.heading.replace("\r", " ").replace("\n", " "))


# --------------------------------------------------------------------------- failures


def failure_line(f: ProofFailure | Issue, lang: str) -> str:
    """``C2 U13 CLAUDE.md:202-260 → docs/historial/2026-09.md:41-99: <reason>`` (spec §7).

    A secret is printed as ``CLAUDE.md:123 secreto:aws_access_key`` (G5). Only metadata.
    """
    if isinstance(f, ProofFailure):
        key, who = f.check, f.unit
        source, src, dest, at, detail = f.source, f.src, f.dest, f.at, f.reason
    else:
        key, who = f.key, f.block or f.unit
        source, src, dest, at, detail = f.source, f.src, f.dest, f.at, f.detail
    if key == "secret":
        where = source or dest or ""
        line = at or src
        if line is not None:
            where += f":{line[0]}"
        return f"{where} {_msg('secret', lang)}:{detail}"
    parts = [key]
    if who:
        parts.append(who)
    left: str | None = None
    if source:
        r = src if src is not None else (at if dest is None else None)
        left = f"{source}:{_rng(r)}" if r is not None else source
    right: str | None = None
    if dest:
        right = f"{dest}:{_rng(at)}" if at is not None else dest
    loc = " → ".join(x for x in (left, right) if x)
    if loc:
        parts.append(loc)
    text = _msg(key, lang)
    if detail:
        text += f" ({detail})"
    return " ".join(parts) + ": " + text


# --------------------------------------------------------------------------- plan table


def review_order(m: Manifest) -> tuple[str, ...]:
    """Unit ids in the review order of spec §5.7: low; K >= 1, S = 0 leaving L1; medium;
    the rest in source order (manifest order)."""

    def group(u: UnitEntry) -> int:
        if u.confidence == "low":
            return 0
        if _count(u, "K") >= 1 and _count(u, "S") == 0 and u.dest != "L1":
            return 1
        if u.confidence == "medium":
            return 2
        return 3

    indexed = list(enumerate(m.units))
    indexed.sort(key=lambda p: (group(p[1]), p[0]))
    return tuple(u.id for _, u in indexed)


def _copies_cell(u: UnitEntry) -> str:
    cells = []
    for b in u.blocks:
        if b.pin is None and not b.copy_to:
            continue
        cells.append(f"{b.id}[{','.join(b.guard)}]" if b.guard else b.id)
    return " ".join(cells) or "-"


def _rule_cell(u: UnitEntry) -> str:
    rules = [
        r for r in u.reasons if r.startswith("rule:") or r in ("budget:demoted", "ratchet:demoted")
    ]
    return ",".join(rules) or "-"


def plan_table(m: Manifest, lang: str, *, unit_bytes: Mapping[str, int] | None = None) -> str:
    """The plan table of spec §10.2.2, in the order of §5.7, titles cut to 60 characters.

    ``unit_bytes`` (unit id -> bytes) comes from the plan outcome; the manifest schema has
    no per-unit byte count, so a unit without it shows ``-``.
    """
    sizes = unit_bytes or {}
    pending = set(mf.pending_units(m))
    multi = len({u.source for u in m.units}) > 1
    by_id = {u.id: u for u in m.units}
    cols = ("n", "id", "title", "lines", "bytes", "dest", "card", "copies", "confidence", "rule")
    out = [SEP.join(_msg(f"col.{c}", lang) for c in cols)]
    for n, uid in enumerate(review_order(m), start=1):
        u = by_id[uid]
        lines = f"{u.lines[0]}-{u.lines[1]}"
        if multi:
            lines = f"{u.source}:{lines}"
        card = (u.card_id or _msg("yes", lang)) if u.card else "-"
        cells = [
            f"{n:>2}",
            u.id,
            _title(u, lang),
            lines,
            str(sizes[u.id]) if u.id in sizes else "-",
            u.dest,
            card,
            _copies_cell(u),
            u.confidence,
            _rule_cell(u),
        ]
        if u.protected:
            cells.append(_msg("protected", lang))
        if u.id in pending:
            cells.append(_msg("pending", lang))
        out.append(SEP.join(cells))
    return "\n".join(out)


# --------------------------------------------------------------------------- projection


def _seq(v: object) -> list[object]:
    return list(v) if isinstance(v, list | tuple) else []


def _map(v: object) -> Mapping[str, object]:
    return v if isinstance(v, Mapping) else {}


def _warning_line(w: str, lang: str) -> str:
    key, _, detail = w.partition(":")
    label = _msg(key, lang)
    return f"  {label} ({key}{': ' + detail if detail else ''})"


def projection_text(p: Mapping[str, object], warnings: Sequence[str], lang: str) -> str:
    """Spec §10.2.3: files, S1-S4 before and after, Codex chains, load-set diff, warnings."""
    out = [_msg("proj.files", lang) + ":"]
    for f in _seq(p.get("files")):
        d = _map(f)
        out.append(f"  {d.get('path')}: {_num(d.get('bytes'))} B, {_num(d.get('lines'))}")
    out.append(_msg("proj.signals", lang) + ":")
    before, after = _map(p.get("before")), _map(p.get("after"))
    for sig in SIGNALS:
        out.append(f"  {sig}: {_num(before.get(sig))} → {_num(after.get(sig))}")
    out.append(_msg("proj.codex", lang) + ":")
    for c in _seq(p.get("codex")):
        d = _map(c)
        pct = d.get("pct")
        pct_s = f"{pct:.1f}" if isinstance(pct, int | float) else "n/a"
        out.append(f"  {d.get('name')}: {_num(d.get('bytes'))} B, {pct_s} %")
    ls = _map(p.get("loadset"))
    for name, key in (("proj.added", "added"), ("proj.removed", "removed")):
        items = [str(x) for x in _seq(ls.get(key))]
        out.append(f"{_msg(name, lang)}: {', '.join(items) or _msg('none', lang)}")
    out.append(_msg("proj.warnings", lang) + ":")
    if warnings:
        out.extend(_warning_line(w, lang) for w in warnings)
    else:
        out.append("  " + _msg("none", lang))
    return "\n".join(out)


def levers_text(
    levers: Mapping[str, int],
    lang: str,
    *,
    codex_budget: int | None = None,
    root: str | None = None,
) -> str:
    """Every lever of spec §5.6.5 with its bytes. Lists them; never picks one.

    With ``codex_budget`` (the plan's ``codex_budget_bytes``), ``lever_codex_budget`` also
    shows its concrete command, ``declutter reorg codex-budget [-C root] --bytes N``."""
    keys = [k for k in mf.LEVER_KEYS] + sorted(k for k in levers if k not in mf.LEVER_KEYS)
    out = [_msg("levers.head", lang)]
    for k in keys:
        out.append(f"  {k}: {_msg(k, lang)} — {levers.get(k, 0)} B")
        if k == "lever_codex_budget" and codex_budget is not None:
            cmd = mf.codex_budget_command(codex_budget, levers.get(k, 0), root)
            out.append(f"    {_msg('lever_command', lang, cmd=cmd)}")
    return "\n".join(out)


def outcome_json(report: Mapping[str, object]) -> str:
    """``--json`` output: the report, metadata only."""
    return mf.report_dumps(report).decode("utf-8")


def closing_text(m: Manifest, lang: str) -> str:
    """Spec §10.2.7: before/after, cards to distill, link and import warnings, undo, prune,
    manual merge and the ratchet notice. Offers to install nothing (decision 7)."""
    out = [_msg("step.closing", lang), _msg("proj.signals", lang) + ":"]
    before, after = _map(m.projection.get("before")), _map(m.projection.get("after"))
    for sig in SIGNALS:
        out.append(f"  {sig}: {_num(before.get(sig))} → {_num(after.get(sig))}")
    out.append(_msg("close.cards", lang, n=sum(1 for u in m.units if u.card)))
    shown = [w for w in m.warnings if w.partition(":")[0] in _CLOSING_WARNINGS]
    if shown:
        out.append(_msg("proj.warnings", lang) + ":")
        out.extend(_warning_line(w, lang) for w in shown)
    out.append(_msg("close.undo", lang, id=m.id))
    out.append(_msg("close.prune", lang, id=m.id))
    if m.git.enabled and m.git.base_branch:
        out.append(_msg("close.merge", lang, base=m.git.base_branch, id=m.id))
    out.append(_msg("close.ratchet", lang))
    return "\n".join(out)


# --------------------------------------------------------------------------- menu


class _Menu:
    def __init__(
        self,
        project: Project,
        cfg: Config,
        reorg_id: str,
        prompter: Prompter,
        now: datetime,
        lang: str,
        out: mf.PlanOutcome,
    ) -> None:
        self.project, self.cfg, self.id = project, cfg, reorg_id
        self.p, self.now, self.lang = prompter, now, lang
        self.m = out.manifest
        self.code = out.code
        # The cursor order is fixed when entering the menu (spec §5.7 / §10.3).
        self.order = list(review_order(self.m))
        self.pos = 0

    def unit(self) -> UnitEntry | None:
        if not self.order:
            return None
        uid = self.order[self.pos]
        return next((u for u in self.m.units if u.id == uid), None)

    def show(self) -> None:
        u = self.unit()
        if u is None:
            return
        parts = [f"→ {u.id}", _title(u, self.lang), f"{u.dest} → {u.file}", u.confidence]
        if u.protected:
            parts.append(_msg("protected", self.lang))
        if u.id in mf.pending_units(self.m):
            parts.append(_msg("pending", self.lang))
        self.p.say(SEP.join(parts))

    def report(self, out: mf.PlanOutcome) -> None:
        self.m, self.code = out.manifest, out.code
        if out.code == EXIT_OK:
            self.p.say(_msg("menu.check_ok", self.lang))
        else:
            self.p.say(_msg("menu.check_failed", self.lang, code=out.code))
        for i in out.issues:
            self.p.say("  " + failure_line(i, self.lang))
        if out.code == EXIT_THRESHOLD and out.levers:
            self.p.say(
                levers_text(
                    out.levers,
                    self.lang,
                    codex_budget=out.manifest.budgets.codex_budget_bytes,
                    root=str(self.project.root),
                )
            )

    def errors(self, exc: ReorgError) -> None:
        for i in exc.issues:
            self.p.say("  " + failure_line(i, self.lang))

    def check(self) -> None:
        try:
            self.report(mf.check_run(self.project, self.cfg, self.id, now=self.now))
        except ReorgError as exc:
            self.code = exc.code
            self.errors(exc)

    def edit(self) -> None:
        editor = os.environ.get("EDITOR", "").strip()
        if not editor:
            self.p.say(_msg("menu.no_editor", self.lang))
            return
        path = mf.run_dir(self.project.root, self.id) / mf.MANIFEST_NAME
        try:
            argv = shlex.split(editor)
        except ValueError:
            self.p.say(_msg("menu.editor_invalid", self.lang))
            return
        if not argv:
            self.p.say(_msg("menu.no_editor", self.lang))
            return
        try:
            subprocess.run([*argv, str(path)], check=False)
        except OSError:
            self.p.say(_msg("menu.editor_failed", self.lang))
            return
        self.check()

    def ask(self, key: str) -> str:
        return self.p.ask(_msg(key, self.lang)).strip()

    def act(self, key: str, u: UnitEntry) -> None:
        if key in _G2_REJECTS and u.protected:
            self.p.say(_msg("menu.g2_rejected", self.lang))
            return
        if key == "c" and _safety(u):
            self.p.say(_msg("menu.c_rejected", self.lang))
            return
        if key == "f" and u.dest != "L4":
            self.p.say(_msg("menu.f_only_l4", self.lang))
            return
        dest: str | None = None
        card: bool | None = None
        reason: str | None = None
        not_safety: str | None = None
        ack_reason: str | None = None
        if key == "u":
            guarded = [b for b in u.blocks if b.guard]
            if not guarded:
                self.p.say(_msg("menu.no_guarded", self.lang))
                return
            for b in guarded:
                self.p.say(f"  {b.id} {b.key} [{', '.join(b.guard)}]")
            not_safety = self.ask("menu.ask_block")
            ack_reason = self.ask("menu.ask_ack")
        else:
            if key == "z":
                dest = "L2:" + self.ask("menu.ask_zone").strip("/")
            elif key in _DEST_KEYS:
                dest = _DEST_KEYS[key]
            elif key == "f":
                card = not u.card
            reason = self.ask("menu.ask_reason")
        try:
            out = mf.set_override(
                self.project,
                self.cfg,
                self.id,
                u.id,
                dest=dest,
                card=card,
                keep=key == "k",
                accept=key == "s",
                reason=reason,
                not_safety=not_safety,
                ack_reason=ack_reason,
                now=self.now,
            )
        except ReorgError as exc:
            self.errors(exc)
            return
        self.report(out)

    def run(self) -> str:
        lows = sum(1 for u in self.m.units if u.confidence == "low")
        if lows:
            self.p.say(_msg("menu.low_intro", self.lang, n=lows))
        self.p.say(_msg("menu.help", self.lang))
        self.show()
        while True:
            total = len(self.order)
            key = self.p.ask(
                _msg("menu.prompt", self.lang, pos=self.pos + 1 if total else 0, total=total)
            ).strip()
            if key in ("", "q"):
                self.p.say(_msg("menu.saved", self.lang, id=self.id))
                return "quit"
            if key == "A":
                if self.code == EXIT_OK:
                    return "apply"
                self.p.say(_msg("menu.a_unavailable", self.lang))
            elif key in ("n", "p"):
                if total:
                    step = 1 if key == "n" else -1
                    self.pos = min(max(self.pos + step, 0), total - 1)
                    self.show()
            elif key == "v":
                self.check()
            elif key == "e":
                self.edit()
            elif key in _UNIT_KEYS and len(key) == 1:
                u = self.unit()
                if u is not None:
                    self.act(key, u)
                    self.show()
            else:
                self.p.say(_msg("menu.unknown_key", self.lang))


def run_menu(
    project: Project,
    cfg: Config,
    reorg_id: str,
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
) -> str:
    """The menu of spec §10.3 over run ``reorg_id``. Returns ``"apply"`` or ``"quit"``.

    Entering runs ``check``; each change records the override with
    :func:`manifest.set_override`, which re-runs ``check``. ``A`` is only accepted when the
    last check returned 0. An empty answer (also EOF) quits; the plan stays saved.
    """
    try:
        out = mf.check_run(project, cfg, reorg_id, now=now)
    except ReorgError as exc:
        for i in exc.issues:
            prompter.say(failure_line(i, lang))
        return "quit"
    menu = _Menu(project, cfg, reorg_id, prompter, now, lang, out)
    menu.report(out)
    return menu.run()


# --------------------------------------------------------------------------- guide


def _plan_report(out: mf.PlanOutcome, prompter: Prompter, lang: str) -> None:
    m = out.manifest
    prompter.say(_msg("step.plan", lang))
    prompter.say(plan_table(m, lang, unit_bytes=out.unit_bytes))
    prompter.say(_msg("step.projection", lang))
    prompter.say(projection_text(m.projection, m.warnings, lang))
    for i in out.issues:
        prompter.say(failure_line(i, lang))
    if out.code == EXIT_THRESHOLD and out.levers:
        budget = m.budgets.codex_budget_bytes
        prompter.say(levers_text(out.levers, lang, codex_budget=budget))


def _signals_text(project: Project, cfg: Config, lang: str) -> str:
    m = measure.measure(project, cfg)
    lines = []
    for s in m.signals:
        if s.id in SIGNALS:
            name = t(f"sig.{s.id}", lang)
            lines.append(f"  {s.id} {name}: {_num(s.value)}")
    return "\n".join(lines)


def _before_apply(m: Manifest, use_git: bool, lang: str) -> str:
    out = [_msg("step.before_apply", lang)]
    for mode in ("created", "extended", "rewritten", "control"):
        paths = [o.path for o in m.outputs if o.mode == mode]
        if paths:
            out.append(_msg("apply.files", lang, mode=_msg(f"apply.mode.{mode}", lang)))
            out.extend(f"  {p}" for p in paths)
    if use_git:
        out.append(
            _msg(
                "apply.refs",
                lang,
                branch=f"declutter/reorg-{m.id}",
                tag=f"declutter/pre-reorg-{m.id}",
            )
        )
    else:
        out.append(_msg("apply.no_git", lang))
    pins = sum(1 for u in m.units for b in u.blocks if b.pin is not None)
    cards = sum(1 for u in m.units if u.card)
    out.append(_msg("apply.counts", lang, pins=pins, cards=cards))
    return "\n".join(out)


def run_guide(
    project: Project,
    cfg: Config,
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
    extra_sources: tuple[str, ...] = (),
) -> int:
    """``declutter reorg guide`` (spec §10.2). Without a TTY it is ``plan --dry-run`` and returns
    its code (§10.4); with a TTY it runs steps 1-7."""
    req = mf.PlanRequest(
        project=project,
        cfg=cfg,
        lang=lang,
        now=now,
        dry_run=not prompter.is_tty(),
        extra_sources=extra_sources,
    )
    if not prompter.is_tty():
        prompter.say(_msg("guide_no_tty", lang))
        try:
            out = mf.plan_project(req)
        except ReorgError as exc:
            for i in exc.issues:
                prompter.say(failure_line(i, lang))
            return exc.code
        _plan_report(out, prompter, lang)
        return out.code

    # 1. measurement, sources and the secret scan (inside plan, before parsing)
    prompter.say(_msg("step.measure", lang))
    prompter.say(_signals_text(project, cfg, lang))
    try:
        out = mf.plan_project(req)
    except ReorgError as exc:
        secrets = [i for i in exc.issues if i.key == "secret"]
        if secrets:
            prompter.say(_msg("secrets_found", lang, n=len(secrets)))
        for i in exc.issues:
            prompter.say(failure_line(i, lang))
        return exc.code
    m = out.manifest
    prompter.say(_msg("sources", lang) + ":")
    for s in m.sources:
        prompter.say(f"  {s.path} {s.sha256[:12]} {s.bytes} B {s.lines}")
    prompter.say(_msg("no_secrets", lang))
    # 2-3. plan and projection
    _plan_report(out, prompter, lang)
    # 4. menu
    prompter.say(_msg("step.menu", lang))
    if run_menu(project, cfg, m.id, prompter, now=now, lang=lang) != "apply":
        return out.code
    # 5. before applying
    try:
        checked = mf.check_run(project, cfg, m.id, now=now)
    except ReorgError as exc:
        for i in exc.issues:
            prompter.say(failure_line(i, lang))
        return exc.code
    use_git = gitops.is_repo(project.root)
    prompter.say(_before_apply(checked.manifest, use_git, lang))
    # 6. apply: asks «¿Aplicar?», G9 and the commit, then proves the commit (§11.2)
    res = ap.apply(
        project,
        cfg,
        m.id,
        ap.ApplyOptions(no_git=not use_git),
        prompter,
        now=now,
        lang=lang,
    )
    prompter.say(_msg("apply.result", lang, code=res.code))
    for i in res.issues:
        prompter.say(failure_line(i, lang))
    if res.code != EXIT_OK:
        return res.code
    # 7. closing
    try:
        final, _ = mf.load_run(project.root, m.id)
    except ReorgError:
        final = checked.manifest
    prompter.say(closing_text(final, lang))
    prompter.say(_msg("close.now", lang) + ":")
    prompter.say(_signals_text(project, cfg, lang))
    return EXIT_OK
