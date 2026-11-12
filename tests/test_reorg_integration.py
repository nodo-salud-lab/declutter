"""Integration with phase 1 and idempotence (spec §15.1 items 13 and 16).

``measure`` and ``loadset`` before and after an applied run on an inflated synthetic
fixture, the projection of ``reorg/projection.py`` against ``measure`` on the applied
result, a second ``plan`` over the result (nothing moves) and an ``@ruta`` import unit
from ``plan`` to ``apply``. Synthetic projects only.
"""

from __future__ import annotations

from datetime import datetime

from conftest import MakeProject
from declutter_context.loadset import claude_loadset
from declutter_context.measure import measure
from declutter_context.model import Config, Measurement, Project
from declutter_context.reorg import apply as ap
from declutter_context.reorg import manifest as mf
from declutter_context.reorg import mdblocks
from reorg_synth import accept_pending
from test_apply import FakePrompter, commit_all

NOW = datetime(2026, 11, 4, 15, 30)
NOW2 = datetime(2026, 11, 5, 9, 0)
RID = "20261104-1530"
RID2 = "20261105-0900"

BASE = "# Demo\nIntro del proyecto sintético.\n\n## Cómo correr\nEjecuta make test.\n"
G2 = "\n## Datos de pacientes\nTodo dato sintético es confidencial.\n"
TRAPS = "\n## Trampas conocidas\n- El caché sintético tarda. Parece un bug y no lo es.\n"
MAP = "\n## Mapa del repositorio\n- src/: código sintético.\n- docs/: documentos.\n"
RULE = "\n## Reglas del proyecto\n- Nunca uses la base sintética de producción.\n"


def _history(n: int) -> str:
    """``n`` dated synthetic sessions: the history that inflates the root file."""
    out = []
    for i in range(1, n + 1):
        day = f"2026-10-{i:02d}"
        body = "".join(f"Registro sintético {i}.{j} de la sesión.\n" for j in range(6))
        out.append(f"\n## Sesión {day}\n{body}")
    return "".join(out)


INFLATED = BASE + RULE + G2 + TRAPS + MAP + _history(25)


def _signals(m: Measurement) -> dict[str, float | None]:
    return {s.id: s.value for s in m.signals}


def _git_project(make_project: MakeProject, files: dict[str, str]) -> Project:
    project = make_project(git=True)
    for rel, text in files.items():
        path = project.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    commit_all(project.root)
    return project


def _plan(project: Project, now: datetime = NOW) -> mf.PlanOutcome:
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=Config(), lang="es", now=now))
    assert out.code == 0, out.issues
    return out


def _apply(project: Project, rid: str = RID, now: datetime = NOW) -> None:
    accept_pending(project, Config(), rid, now)
    res = ap.apply(
        project,
        Config(),
        rid,
        ap.ApplyOptions(yes=True, commit=True),
        FakePrompter(tty=False),
        now=now,
        lang="es",
    )
    assert res.code == 0, res.issues


def _loadset_labels(project: Project) -> set[str]:
    ls = claude_loadset(project, Config())
    return {f.path.relative_to(project.root).as_posix() for f in ls.files}


# --------------------------------------------------------------------------- §15.1.16


def test_measure_before_and_after_on_inflated_fixture(make_project: MakeProject) -> None:
    project = _git_project(make_project, {"CLAUDE.md": INFLATED, "src/keep.txt": "x\n"})
    before = _signals(measure(project, Config()))
    assert before["S1"] == len(INFLATED.splitlines())
    assert before["S3"] is None  # no AGENTS.md: n/a
    assert _loadset_labels(project) == {"CLAUDE.md"}

    _plan(project)
    _apply(project)

    after = _signals(measure(project, Config()))
    assert after["S1"] is not None and before["S1"] is not None
    assert after["S1"] < before["S1"]
    assert after["S3"] is not None and after["S3"] > 0
    assert after["S2"] is not None and before["S2"] is not None
    assert after["S2"] < before["S2"]
    claude = (project.root / "CLAUDE.md").read_bytes()
    assert b"@AGENTS.md" in claude
    assert {"CLAUDE.md", "AGENTS.md"} <= _loadset_labels(project)  # @AGENTS.md resolves


def test_projection_equals_measure_after_apply(make_project: MakeProject) -> None:
    project = _git_project(make_project, {"CLAUDE.md": INFLATED, "src/keep.txt": "x\n"})
    out = _plan(project)
    projected = out.manifest.projection
    _apply(project)
    measured = _signals(measure(project, Config()))
    after = projected["after"]
    assert isinstance(after, dict)
    for sid in ("S1", "S2", "S3", "S4"):
        assert after[sid] == measured[sid], sid
    files = projected["files"]
    assert isinstance(files, list)
    for entry in files:
        assert isinstance(entry, dict)
        path = project.root / str(entry["path"])
        assert entry["bytes"] == len(path.read_bytes()), entry["path"]


def test_import_unit_end_to_end(make_project: MakeProject) -> None:
    files = {
        "CLAUDE.md": BASE + "\n## Sesión 2026-10-07\n@docs/reglas.md\n",
        "docs/reglas.md": "Regla importada sintética.\n",
    }
    project = _git_project(make_project, files)
    assert "docs/reglas.md" in _loadset_labels(project)
    out = _plan(project)
    u = next(x for x in out.manifest.units if x.heading == "Sesión 2026-10-07")
    assert u.dest == "L1" and "imports" in u.reasons  # rule 3b
    _apply(project)
    agents = (project.root / "AGENTS.md").read_bytes()
    assert b"@docs/reglas.md" in agents
    assert {"CLAUDE.md", "AGENTS.md", "docs/reglas.md"} <= _loadset_labels(project)


def test_import_unit_moved_to_l4_end_to_end(make_project: MakeProject) -> None:
    files = {
        "CLAUDE.md": BASE + "\n## Sesión 2026-10-07\n@docs/reglas.md\n",
        "docs/reglas.md": "Regla importada sintética.\n",
    }
    project = _git_project(make_project, files)
    out = _plan(project)
    u = next(x for x in out.manifest.units if x.heading == "Sesión 2026-10-07")
    res = mf.set_override(project, Config(), RID, u.id, dest="L4", reason="mover", now=NOW)
    assert f"import_moved:{u.id}" in res.manifest.warnings
    loadset = res.manifest.projection["loadset"]
    assert isinstance(loadset, dict) and "docs/reglas.md" in loadset["removed"]
    _apply(project)
    assert "docs/reglas.md" not in _loadset_labels(project)  # as the projection said


# --------------------------------------------------------------------------- §15.1.13


def _h2(data: bytes) -> list[bytes]:
    lines = mdblocks.split_lines(data).lines
    return [ln for ln in lines if ln.startswith(b"## ")]


def test_idempotent_second_plan_moves_nothing(make_project: MakeProject) -> None:
    project = _git_project(make_project, {"CLAUDE.md": INFLATED, "src/keep.txt": "x\n"})
    _plan(project)
    _apply(project)
    agents_before = (project.root / "AGENTS.md").read_bytes()
    claude_before = (project.root / "CLAUDE.md").read_bytes()

    second = _plan(project, NOW2)
    m = second.manifest
    assert {s.path for s in m.sources} == {"CLAUDE.md", "AGENTS.md"}
    for u in m.units:
        if u.source == "CLAUDE.md":
            # the rewritten preamble (``@AGENTS.md`` and the ``claude`` region) stays
            assert u.dest in ("L1c",), (u.heading, u.dest)
            assert "generated" in u.reasons
        else:
            assert u.dest == "L1", (u.heading, u.dest, u.reasons)
        assert not u.needs_confirmation or "generated" in u.reasons
    by_heading = {u.heading: u for u in m.units}
    for generated in ("Trampas (índice)", "Mapa y zonas"):
        unit = by_heading[generated]
        assert unit.dest == "L1" and "generated" in unit.reasons, generated
    assert mf.pending_units(m) == ()
    # Nothing moves: no history, card, trap or map output is written again.
    moved = {o.path for o in m.outputs if o.mode != "control"} - {"AGENTS.md", "CLAUDE.md"}
    assert moved == set(), moved
    # And the rebuilt root files add no section.
    assert _h2(second.build.files["AGENTS.md"]) == _h2(agents_before)
    assert _h2(second.build.files["CLAUDE.md"]) == _h2(claude_before)
    # In fact the second run rebuilds both root files byte for byte.
    assert second.build.files["AGENTS.md"] == agents_before
    assert second.build.files["CLAUDE.md"] == claude_before
