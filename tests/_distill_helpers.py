"""Synthetic fixture for the distillation tests (fase 2b spec §15.1).

A git project whose run is planned, applied and committed with three L4 units with a card
(one with 1 copy, one with 3 copies, one with a ``root+zone:src`` pointer), one L5m unit
with a copy and one L4 unit without a card with a copy (both ``no_card``). Every line is
invented. ``fill`` writes the card sections the way the ``/declutter-distill`` skill would.
"""

from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from conftest import MakeProject
from declutter_context.model import Config, Project
from declutter_context.reorg import apply as ap
from declutter_context.reorg import manifest as mf
from reorg_synth import accept_pending
from test_apply import FakePrompter

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"
SEED = "ZQX7731"

_PAD = " porque el repositorio sintético es público y cualquier persona ajena lo leería"
_PAD2 = " y además lo copiaría a otros servidores sintéticos sin pedir permiso al equipo"

TEXT_ES = (
    "# Demo\nIntro del proyecto sintético.\n\n## Cómo correr\nEjecuta make test.\n"
    "\n## Sesión 2026-10-05\n"
    f"- Nunca subas el password {SEED} de M13-05 ni `docs/x.md` a docs/pub/ antes del "
    f"2026-10-09 ni más de 3 veces{_PAD}{_PAD2}.\n"
    "\n## Sesión 2026-10-06\nDecidimos usar la cola local con 12 hilos.\n"
    f"\n- Nunca subas el password uno{_PAD}.\n"
    f"\n- Siempre rota el password dos antes de 2026-10-07{_PAD}.\n"
    f"\n- Nunca guardes el token tres en .env{_PAD}.\n"
    "\n## Contrato sintético 2026-10-08\n"
    f"- Nunca subas el password de src/a ni src/b ni src/c{_PAD}{_PAD2}.\n"
    "\n## Mapa del repositorio\n- src/: código.\n- El campo password se valida aparte.\n"
    "\n## Nota 2026-10-03\nSolo registro. El campo password se valida aparte.\n"
)

TEXT_EN = (
    "# Demo\nSynthetic project intro.\n\n## How to run\nRun make test.\n"
    "\n## Session 2026-10-05\n"
    "- Never push the password M13-05 to docs/pub/ because the synthetic repository is "
    "public and anyone outside could read it, copy it to other synthetic servers and keep "
    "it forever without asking the team.\n"
)

# A valid invariant per card of TEXT_ES (b1, b2, c, e6 and e7 hold).
INV_ES: Mapping[str, tuple[str, ...]] = {
    "D-001": (
        f"Nunca subas el password {SEED} de M13-05 ni `docs/x.md` a docs/pub/ antes del "
        "2026-10-09 ni más de 3 veces.",
    ),
    "D-002": (
        "- Nunca subas el password uno.",
        "- Siempre rota el password dos antes de 2026-10-07.",
        "- Nunca guardes el token tres en .env.",
    ),
    "D-003": ("Nunca subas el password de src/a, src/b ni src/c.",),
}
INV_EN: Mapping[str, tuple[str, ...]] = {
    "D-001": ("Never push the password M13-05 to docs/pub/.",),
}


@dataclass(frozen=True)
class Fixture:
    project: Project
    lang: str

    @property
    def root(self) -> Path:
        return self.project.root

    def card_path(self, card_id: str) -> Path:
        folder = self.root / ("docs/decisiones" if self.lang == "es" else "docs/decisions")
        return next(folder.glob(f"{card_id}-*.md"))

    def manifest(self) -> mf.Manifest:
        return mf.load_run(self.root, RID)[0]

    def unit(self, heading: str) -> mf.UnitEntry:
        return next(u for u in self.manifest().units if u.heading == heading)


def git(root: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return res.stdout.decode()


_BUILT: dict[tuple[str, bool], Path] = {}


def applied(make_project: MakeProject, lang: str = "es", *, commit: bool = True) -> Fixture:
    """Plan, accept, apply (and commit) the synthetic run.

    The run is built once per ``(lang, commit)`` in a private temporary folder and then
    copied (``.git`` included) into the test's project, so every test gets its own tree.
    """
    key = (lang, commit)
    if key not in _BUILT:
        base = Path(tempfile.mkdtemp(prefix="declutter-distill-"))
        atexit.register(shutil.rmtree, base, True)
        home = base / "home"
        (home / ".claude").mkdir(parents=True)
        (home / "proj").mkdir()
        _build(Project.at(home / "proj", home=home, claude_home=home / ".claude"), lang, commit)
        _BUILT[key] = home / "proj"
    project = make_project()
    shutil.copytree(_BUILT[key], project.root, symlinks=True, dirs_exist_ok=True)
    return Fixture(project, lang)


def _build(project: Project, lang: str, commit: bool) -> None:
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "t"),
    ):
        git(project.root, *args)
    (project.root / "src").mkdir()
    (project.root / "src" / "keep.txt").write_text("x\n")
    text = TEXT_ES if lang == "es" else TEXT_EN
    (project.root / "CLAUDE.md").write_bytes(text.encode())
    git(project.root, "add", "-A")
    git(project.root, "commit", "-q", "-m", "c")
    cfg = Config()
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=cfg, lang=lang, now=NOW))
    assert out.code in (0, 4), out.issues
    if lang == "es":
        m = out.manifest
        u = next(x for x in m.units if x.heading == "Contrato sintético 2026-10-08")
        res = mf.set_override(project, cfg, RID, u.id, dest="L4", reason="sintético", now=NOW)
        assert res.code in (0, 4), res.issues
    accept_pending(project, cfg, RID, NOW)
    res2 = ap.apply(
        project,
        cfg,
        RID,
        ap.ApplyOptions(yes=True, commit=commit),
        FakePrompter(tty=False),
        now=NOW,
        lang=lang,
    )
    assert res2.code == 0, res2.issues


def _section_titles(lang: str) -> tuple[str, ...]:
    if lang == "es":
        return ("## Qué", "## Por qué", "## Alternativa descartada", "## Trampa")
    return ("## What", "## Why", "## Rejected alternative", "## Pitfall")


def invariant_title(lang: str) -> str:
    return "## Invariante para AGENTS.md" if lang == "es" else "## Invariant for AGENTS.md"


def fill(
    fx: Fixture,
    card_id: str,
    invariant: Sequence[str] | None = None,
    *,
    what: str = "Regla sintética de la sesión.",
    why: str = "no consta",
    alternative: str = "no consta",
    pitfall: str = "no consta",
    keep_help: bool = True,
) -> Path:
    """Write the bodies of the five sections of a card, keeping every other line."""
    path = fx.card_path(card_id)
    if invariant is None:
        invariant = (INV_ES if fx.lang == "es" else INV_EN)[card_id]
    lines = path.read_bytes().decode().split("\n")
    titles = (*_section_titles(fx.lang), invariant_title(fx.lang))
    end = lines.index("<!-- declutter:gen-end card -->")
    help_line = lines[end - 1]
    start = lines.index(titles[0])
    bodies = [[what], [why], [alternative], [pitfall], [*invariant]]
    if keep_help:
        bodies[-1].append(help_line)
    new: list[str] = []
    for title, body in zip(titles, bodies, strict=True):
        new += [title, *body]
    lines[start:end] = new
    path.write_bytes("\n".join(lines).encode())
    return path


def fill_all(fx: Fixture) -> None:
    for card_id in INV_ES if fx.lang == "es" else INV_EN:
        fill(fx, card_id)


def replace_in(path: Path, old: str, new: str, count: int = 1) -> None:
    data = path.read_bytes().decode()
    assert old in data, old
    path.write_bytes(data.replace(old, new, count).encode())
