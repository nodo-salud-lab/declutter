"""plan / check / set orchestration (spec §5.6.4-5, §5.8, §6.1, §6.4, §8 G5, §9, §10.1, §13).

Synthetic projects only (``make_project``); bytes written with ``write_bytes`` when exact.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import fsutil
from declutter_context.model import Config, Project, ReorgConfig
from declutter_context.reorg import Issue, ReorgError, mdblocks
from declutter_context.reorg import manifest as mf
from declutter_context.reorg.manifest import BlockOverride, Manifest, PlanOutcome, UnitOverride
from test_lexicon import _secret_cases

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"
SEED = "ZQX-SEED-7731"
SMALL = Config(codex_budget_bytes=500)

BASE = "# Demo\nIntro del proyecto.\n\n## Cómo correr\nEjecuta make test.\n"
NOTE_L4 = "\n## Nota 2026-10-03\nSolo registro de la sesión.\n"
K_L4 = "\n## Sesión 2026-10-05\n- Nunca uses la base de prueba.\n"
L1C = "\n## Hooks de Claude Code\nConfigura los hooks y skills en settings.json.\n"
G2 = "\n## Datos de pacientes\nTodo dato es confidencial.\n"
GUARDED_L4 = "\n## Cambio 2026-10-06\nNo subas el password al repo.\n"
PUB = "\n## Métricas\nEl benchmark mide la latencia.\n"
ZONE = "\n## Componentes\nVer src/a src/b src/c src/d src/e.\n"
IMPORT = "\n## Sesión 2026-10-07\n@docs/reglas.md\n"
LINK = "\n## Nota 2026-10-08\nVer [guía](docs/guia.md).\n"
DUP = "\n## Notas\nUno.\n\n## Notas\nDos.\n"


# --------------------------------------------------------------------------- helpers


def _plan(
    project: Project,
    *,
    dry_run: bool = False,
    cfg: Config | None = None,
    lang: str = "es",
    now: datetime = NOW,
    extra: tuple[str, ...] = (),
    from_id: str | None = None,
) -> PlanOutcome:
    req = mf.PlanRequest(
        project=project,
        cfg=cfg or Config(),
        lang=lang,
        now=now,
        dry_run=dry_run,
        extra_sources=extra,
        from_id=from_id,
    )
    return mf.plan_project(req)


def _check(project: Project, rid: str = RID, cfg: Config | None = None) -> PlanOutcome:
    return mf.check_run(project, cfg or Config(), rid, now=NOW)


def _claude(project: Project, text: str) -> None:
    (project.root / "CLAUDE.md").write_bytes(text.encode())


def _tree(root: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            out[os.path.relpath(Path(dirpath) / d, root) + "/"] = None
        for f in filenames:
            p = Path(dirpath) / f
            out[os.path.relpath(p, root)] = p.read_bytes()
    return out


def _keys(issues: tuple[Issue, ...] | list[Issue]) -> set[str]:
    return {i.key for i in issues}


def _unit(m: Manifest, heading: str, nth: int = 0) -> mf.UnitEntry:
    return [u for u in m.units if u.heading == heading][nth]


def _manifest_path(project: Project, rid: str = RID) -> Path:
    return project.root / ".declutter" / "reorg" / rid / "manifest.json"


def _edit(project: Project, rid: str = RID, **changes: object) -> Manifest:
    path = _manifest_path(project, rid)
    m = replace(mf.loads(path.read_bytes()), **changes)  # type: ignore[arg-type]
    path.write_bytes(mf.dumps(m))
    return m


def _err(fn: Callable[[], object]) -> ReorgError:
    with pytest.raises(ReorgError) as exc:
        fn()
    return exc.value


# --------------------------------------------------------------------------- writes


def test_plan_dry_run_writes_nothing_anywhere(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4 + NOTE_L4})
    before = _tree(project.root)
    out = _plan(project, dry_run=True)
    assert out.run_dir is None
    assert _tree(project.root) == before
    assert not (project.root / ".declutter").exists()
    assert out.manifest.id == RID


def test_plan_writes_run_folder_snapshot_manifest_reports(make_project: MakeProject) -> None:
    project = make_project()
    raw = ("﻿" + BASE + NOTE_L4).encode().replace(b"\n", b"\r\n")[:-2]
    (project.root / "CLAUDE.md").write_bytes(raw)
    out = _plan(project)
    run = project.root / ".declutter" / "reorg" / RID
    assert out.run_dir == run
    assert out.code == 0, out.issues
    assert (run / "original" / "CLAUDE.md").read_bytes() == raw
    m = mf.loads((run / "manifest.json").read_bytes())
    assert m == out.manifest
    assert (run / "manifest.json").read_bytes() == mf.dumps(out.manifest)
    assert (run / "plan.md").read_text(encoding="utf-8").startswith("# ")
    report = json.loads((run / "report.json").read_bytes())
    assert report["id"] == RID and report["code"] == 0
    assert (project.root / ".declutter" / ".gitignore").read_bytes() == b"*\n"
    assert m.sources[0].bom and m.sources[0].eol == "crlf" and not m.sources[0].final_newline
    assert {o.path for o in m.outputs} >= {"AGENTS.md", "CLAUDE.md", "docs/historial/2026-10.md"}
    assert dict((o.path, o.mode) for o in m.outputs)["CLAUDE.md"] == "rewritten"


# --------------------------------------------------------------------------- G5 and reads


# One case per row of the G5 table (spec §8, §15.1.8), shared with the scanner tests.
@pytest.mark.parametrize(
    "secret",
    [
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "ghp_" + "a1B2" * 9,
        "DB_PASSWORD=" + "Sup3rS3cretValue",
        *(value for _, value in _secret_cases()),
    ],
)
def test_secret_in_source_exits_5_without_declutter_and_without_value(
    make_project: MakeProject, secret: str
) -> None:
    project = make_project({"CLAUDE.md": BASE + f"\n## Otra\nclave {secret}\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 5
    hits = [i for i in err.issues if i.key == "secret"]
    assert hits and hits[0].source == "CLAUDE.md" and hits[0].at == (8, 8)
    assert not (project.root / ".declutter").exists()
    blob = str(err) + repr(err.issues)
    assert secret not in blob and secret[-8:] not in blob


def test_env_file_is_never_read(make_project: MakeProject, monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project({"CLAUDE.md": BASE + NOTE_L4, ".env": "TOKEN=" + "x" * 20 + "\n"})
    seen: list[str] = []
    real_read = fsutil.safe_read_bytes
    real_open = os.open

    def spy_read(path: Path, *roots: Path, **kw: int | None) -> bytes:
        seen.append(str(path))
        return real_read(path, *roots, **kw)

    def spy_open(path: object, *a: object, **kw: object) -> int:
        seen.append(str(path))
        return real_open(path, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fsutil, "safe_read_bytes", spy_read)
    monkeypatch.setattr(os, "open", spy_open)
    out = _plan(project)
    assert out.code == 0
    assert seen
    assert not [p for p in seen if Path(p).name == ".env"]


def test_unsafe_read_source_exit_2(make_project: MakeProject, tmp_path: Path) -> None:
    project = make_project()
    outside = tmp_path / "outside.md"
    outside.write_bytes(BASE.encode())
    (project.root / "CLAUDE.md").symlink_to(outside)
    err = _err(lambda: _plan(project))
    assert err.code == 2
    assert any(i.key == "unsafe_read" and i.source == "CLAUDE.md" for i in err.issues)
    assert not (project.root / ".declutter").exists()


def test_non_utf8_source_exit_2(make_project: MakeProject) -> None:
    project = make_project()
    (project.root / "CLAUDE.md").write_bytes(b"# Demo\n\xff\xfe roto\n")
    err = _err(lambda: _plan(project))
    assert err.code == 2 and "not_utf8" in _keys(err.issues)


def test_open_fence_source_exit_2(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + "\n## Código\n```sh\nmake\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 2
    hit = next(i for i in err.issues if i.key == "open_fence")
    assert hit.source == "CLAUDE.md" and hit.at == (8, 8)


def test_front_matter_in_two_sources_exit_2(make_project: MakeProject) -> None:
    fm = "---\ntitle: x\n---\n"
    project = make_project({"CLAUDE.md": fm + BASE, "AGENTS.md": fm + "# Ag\nTexto.\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 2 and "front_matter_conflict" in _keys(err.issues)


@pytest.mark.parametrize("where", ["AGENTS.override.md", "src/AGENTS.override.md"])
def test_agents_override_md_exit_1(make_project: MakeProject, where: str) -> None:
    project = make_project({"CLAUDE.md": BASE, where: "# override\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 1 and "agents_override" in _keys(err.issues)
    assert not (project.root / ".declutter" / "reorg").exists()


def test_gitignore_without_star_exit_1(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE, ".declutter/.gitignore": "reorg/\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 1 and "gitignore_no_star" in _keys(err.issues)
    assert not (project.root / ".declutter" / "reorg").exists()
    assert (project.root / ".declutter" / ".gitignore").read_bytes() == b"reorg/\n"


def test_both_paths_langs_exit_1(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE, "ESTADO.md": "x\n", "STATE.md": "y\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 1 and "paths_both_langs" in _keys(err.issues)


@pytest.mark.parametrize("how", ["autocrlf", "attributes"])
def test_autocrlf_or_gitattributes_on_source_exit_1(make_project: MakeProject, how: str) -> None:
    files = {"CLAUDE.md": BASE}
    if how == "attributes":
        files[".gitattributes"] = "*.md text\n"
    project = make_project(files, git=True)
    if how == "autocrlf":
        subprocess.run(
            ["git", "-C", str(project.root), "config", "core.autocrlf", "true"], check=True
        )
    err = _err(lambda: _plan(project))
    assert err.code == 1 and "git_normalizes" in _keys(err.issues)
    assert not (project.root / ".declutter").exists()


def test_detached_head_and_blob_differs_exit_1(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE}, git=True)
    _claude(project, BASE + NOTE_L4)  # disk differs from HEAD
    err = _err(lambda: _plan(project))
    assert err.code == 1 and "blob_differs" in _keys(err.issues)
    _claude(project, BASE)
    subprocess.run(
        ["git", "-C", str(project.root), "switch", "-q", "--detach"],
        check=True,
        capture_output=True,
    )
    err = _err(lambda: _plan(project, dry_run=True))
    assert err.code == 1 and _keys(err.issues) == {"detached_head"}


def test_plan_records_git_base(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE}, git=True)
    out = _plan(project, dry_run=True)
    assert out.manifest.git.enabled and out.manifest.git.base_branch == "main"
    assert out.manifest.git.base_commit and len(out.manifest.git.base_commit) == 40


def test_no_sources_exit_1(make_project: MakeProject) -> None:
    project = make_project({"README.md": "x\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 1 and "no_sources" in _keys(err.issues)


def test_extra_source_outside_root_or_not_md_exit_1(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE, "notes.txt": "x\n"})
    err = _err(lambda: _plan(project, extra=("notes.txt",)))
    assert err.code == 1 and "source_invalid" in _keys(err.issues)
    err = _err(lambda: _plan(project, extra=("../fuera.md",)))
    assert err.code == 1 and "source_invalid" in _keys(err.issues)


# --------------------------------------------------------------------------- §6.4 rules


def _rule_1(project: Project) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE)
    _plan(project)
    _claude(project, BASE + "Cambio.\n")
    out = _check(project)
    return out.code, out.issues


def _rule_2(project: Project) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE)
    out = _plan(project)
    units = list(out.manifest.units)
    units[1] = replace(units[1], sha256="0" * 64)
    _edit(project, units=tuple(units))
    out = _check(project)
    return out.code, out.issues


def _override_rule(
    project: Project, extra: str, make: Callable[[Manifest], tuple[object, ...]]
) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE + extra)
    out = _plan(project)
    _edit(project, overrides=make(out.manifest))
    out = _check(project)
    return out.code, out.issues


def _rule_3(project: Project) -> tuple[int, tuple[Issue, ...]]:
    return _override_rule(
        project,
        PUB,
        lambda m: (UnitOverride(_unit(m, "Métricas").heading_key, "dest", "L2:ghost", "zona"),),
    )


def _rule_4(project: Project) -> tuple[int, tuple[Issue, ...]]:
    return _override_rule(
        project,
        G2,
        lambda m: (UnitOverride(_unit(m, "Datos de pacientes").heading_key, "dest", "L4", "r"),),
    )


def _doctored(
    project: Project, text: str, heading: str, fix: Callable[[mf.UnitEntry], mf.UnitEntry]
) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, text)
    out = _plan(project, dry_run=True)
    assert out.context is not None
    units = tuple(fix(u) if u.heading == heading else u for u in out.manifest.units)
    issues = mf.validate(
        replace(out.manifest, units=units),
        out.build,
        out.units,
        out.decisions,
        out.context,
        for_apply=False,
    )
    return 4 if issues else 0, issues


def _rule_5(project: Project) -> tuple[int, tuple[Issue, ...]]:
    def drop_pin(u: mf.UnitEntry) -> mf.UnitEntry:
        return replace(u, blocks=tuple(replace(b, pin=None, pin_lines=None) for b in u.blocks))

    return _doctored(project, BASE + GUARDED_L4, "Cambio 2026-10-06", drop_pin)


def _rule_6(project: Project) -> tuple[int, tuple[Issue, ...]]:
    (project.root / "src").mkdir()
    return _override_rule(
        project,
        PUB,
        lambda m: (UnitOverride(_unit(m, "Métricas").heading_key, "dest", "L2:src", "zona"),),
    )


def _rule_7(project: Project) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE + GUARDED_L4)
    out = _plan(project)
    uid = _unit(out.manifest, "Cambio 2026-10-06").id
    out = mf.set_override(
        project, Config(), RID, uid, dest="L1c", reason="solo para Claude", now=NOW
    )
    return out.code, out.issues


def _rule_8(project: Project) -> tuple[int, tuple[Issue, ...]]:
    return _override_rule(
        project,
        PUB,
        lambda m: (UnitOverride(_unit(m, "Métricas").heading_key, "accept", True, "  "),),
    )


def _rule_9(project: Project) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE)
    _plan(project, cfg=SMALL)
    out = _check(project, cfg=SMALL)
    return out.code, out.issues


def _rule_10(project: Project) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE + K_L4)
    out = _plan(project)
    u = _unit(out.manifest, "Sesión 2026-10-05")
    assert u.card_id == "D-001"
    mf.set_override(project, Config(), RID, u.id, accept=True, reason="revisada", now=NOW)
    folder = project.root / "docs" / "decisiones"
    folder.mkdir(parents=True)
    (folder / "D-001-otra.md").write_bytes(b"# otra\n")
    out = _check(project)
    return out.code, out.issues


def _rule_11(project: Project) -> tuple[int, tuple[Issue, ...]]:
    (project.root / "src").mkdir()
    (project.root / "src" / "CLAUDE.md").write_bytes(b"# Zona sin import\n")
    project_files(project, BASE + ZONE)
    out = _plan(project)
    assert _unit(out.manifest, "Componentes").dest == "L2:src"
    return out.code, out.issues


def _rule_12(project: Project) -> tuple[int, tuple[Issue, ...]]:
    def make(m: Manifest) -> tuple[object, ...]:
        k = _unit(m, "Métricas").heading_key
        return (UnitOverride(k, "keep", True, "mantener"), UnitOverride(k, "dest", "L4", "mover"))

    return _override_rule(project, PUB, make)


def _rule_13(project: Project) -> tuple[int, tuple[Issue, ...]]:
    (project.root / "docs").mkdir()
    (project.root / "docs" / "reglas.md").write_bytes(b"Regla importada.\n")
    return _doctored(project, BASE + IMPORT, "Sesión 2026-10-07", lambda u: replace(u, dest="L4"))


def _rule_14(project: Project) -> tuple[int, tuple[Issue, ...]]:
    (project.root / "AGENTS.md").write_bytes(b"# Ag\nIntro.\n\n## Otra parte\nTexto.\n")
    return _doctored(project, BASE, "Otra parte", lambda u: replace(u, dest="L1c"))


def _rule_15(project: Project) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE + L1C)
    out = _plan(project)
    assert any(w.startswith("pending_confirmation:") for w in out.manifest.warnings)
    out = _check(project)
    return out.code, out.issues


def _rule_16(project: Project) -> tuple[int, tuple[Issue, ...]]:
    project_files(project, BASE + K_L4)
    _plan(project)
    out = _check(project)
    return out.code, out.issues


def project_files(project: Project, claude: str) -> None:
    _claude(project, claude)


RULES: dict[int, Callable[[Project], tuple[int, tuple[Issue, ...]]]] = {
    1: _rule_1,
    2: _rule_2,
    3: _rule_3,
    4: _rule_4,
    5: _rule_5,
    6: _rule_6,
    7: _rule_7,
    8: _rule_8,
    9: _rule_9,
    10: _rule_10,
    11: _rule_11,
    12: _rule_12,
    13: _rule_13,
    14: _rule_14,
    15: _rule_15,
    16: _rule_16,
}


@pytest.mark.parametrize("rule", sorted(RULES))
def test_check_rules(make_project: MakeProject, rule: int) -> None:
    project = make_project()
    code, issues = RULES[rule](project)
    assert f"check.{rule}" in _keys(issues), issues
    assert code == (3 if rule == 9 else 4)
    hit = next(i for i in issues if i.key == f"check.{rule}")
    assert hit.code == (3 if rule == 9 else 4)


def test_check_on_clean_plan_is_0(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + NOTE_L4 + G2})
    out = _plan(project)
    assert out.code == 0, out.issues
    out = _check(project)
    assert out.code == 0, out.issues
    run = project.root / ".declutter" / "reorg" / RID
    assert json.loads((run / "report.json").read_bytes())["code"] == 0


def test_plan_reports_pending_confirmation_and_check_fails_4(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4})
    out = _plan(project)
    uid = _unit(out.manifest, "Sesión 2026-10-05").id
    assert out.code == 0
    assert f"pending_confirmation:{uid}" in out.manifest.warnings
    report = json.loads((out.run_dir / "report.json").read_bytes())  # type: ignore[operator]
    assert report["pending_confirmation"] == [uid]
    chk = _check(project)
    assert chk.code == 4
    assert any(i.key == "check.16" and i.unit == uid for i in chk.issues)


def test_budget_demotion_needs_acceptance(make_project: MakeProject) -> None:
    body = "".join(f"- Nunca cambies el valor número {i} sin revisar.\n" for i in range(12))
    text = BASE + "\n## Regla del caché (2026-10-02)\n" + body
    project = make_project({"CLAUDE.md": text})
    cfg = Config(reorg=ReorgConfig(l1_max_bytes=600))
    out = _plan(project, cfg=cfg)
    u = _unit(out.manifest, "Regla del caché (2026-10-02)")
    assert u.dest == "L4" and "budget:demoted" in u.reasons and u.needs_confirmation
    assert out.code == 0
    assert _check(project, cfg=cfg).code == 4


def test_l1_over_budget_is_a_warning(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    out = _plan(project, cfg=Config(reorg=ReorgConfig(l1_max_bytes=100)), dry_run=True)
    assert out.code == 0
    assert any(w.startswith("l1_over_budget") for w in out.manifest.warnings)


def test_codex_over_100_exit_3_with_levers(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + G2 + GUARDED_L4})
    out = _plan(project, cfg=SMALL)
    assert out.code == 3
    assert "check.9" in _keys(out.issues)
    keys = {
        "lever_review_low",
        "lever_guard_ack",
        "lever_codex_budget",
        "lever_distill",
        "lever_revisit_g2",
    }
    assert set(out.levers) == keys
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in out.levers.values())
    assert out.levers["lever_codex_budget"] > 0
    assert out.levers["lever_guard_ack"] > 0 and out.levers["lever_revisit_g2"] > 0
    report = json.loads((out.run_dir / "report.json").read_bytes())  # type: ignore[operator]
    assert report["levers"] == dict(out.levers)
    assert "recommend" not in json.dumps(report)


def test_no_levers_without_code_3(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    out = _plan(project, dry_run=True)
    assert out.code == 0 and not out.levers


def test_precedence_in_plan(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4})
    out = _plan(project, cfg=SMALL, dry_run=True)
    assert out.code == 3  # pending confirmations are warnings in plan
    assert any(w.startswith("pending_confirmation:") for w in out.manifest.warnings)
    out = _plan(project, cfg=SMALL)
    uid = _unit(out.manifest, "Sesión 2026-10-05").id
    mf.set_override(project, SMALL, RID, uid, accept=True, reason="revisada", now=NOW)
    folder = project.root / "docs" / "decisiones"
    folder.mkdir(parents=True)
    (folder / "D-001-otra.md").write_bytes(b"# otra\n")
    chk = _check(project, cfg=SMALL)
    assert {"check.9", "check.10"} <= _keys(chk.issues)
    assert chk.code == 4


# --------------------------------------------------------------------------- set


def test_set_dest_on_g2_rejected_4(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + G2})
    out = _plan(project)
    before = _manifest_path(project).read_bytes()
    u = _unit(out.manifest, "Datos de pacientes")
    assert u.protected
    err = _err(
        lambda: mf.set_override(project, Config(), RID, u.id, dest="L4", reason="mover", now=NOW)
    )
    assert err.code == 4 and "check.4" in _keys(err.issues)
    assert _manifest_path(project).read_bytes() == before


def test_set_ambiguous_override_4(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + DUP})
    out = _plan(project)
    first, second = (u for u in out.manifest.units if u.heading == "Notas")
    assert second.heading_key == first.heading_key + "~2"
    err = _err(
        lambda: mf.set_override(
            project, Config(), RID, first.heading_key, dest="L4", reason="mover", now=NOW
        )
    )
    assert err.code == 4 and "ambiguous_override" in _keys(err.issues)
    ok = mf.set_override(project, Config(), RID, second.id, dest="L4", reason="mover", now=NOW)
    assert ok.code == 0, ok.issues
    assert _unit(ok.manifest, "Notas", 1).dest == "L4"


def test_set_unknown_unit_exit_1(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    _plan(project)
    err = _err(
        lambda: mf.set_override(project, Config(), RID, "U99", keep=True, reason="r", now=NOW)
    )
    assert err.code == 1 and "unit_unknown" in _keys(err.issues)


def test_set_reason_and_ack_reason_stored_separately(make_project: MakeProject) -> None:
    text = BASE + "\n## Sesión 2026-10-09\n- Nunca subas el password al repo.\n"
    project = make_project({"CLAUDE.md": text})
    out = _plan(project)
    u = _unit(out.manifest, "Sesión 2026-10-09")
    b = u.blocks[0]
    assert b.guard == ("secrets",) and b.pin == "block"
    res = mf.set_override(
        project,
        Config(),
        RID,
        u.heading_key,
        accept=True,
        reason="revisada a mano",
        not_safety=b.id,
        ack_reason="falso positivo del campo",
        now=NOW,
    )
    stored = mf.loads(_manifest_path(project).read_bytes()).overrides
    assert UnitOverride(u.heading_key, "accept", True, "revisada a mano") in stored
    assert BlockOverride(b.key, "falso positivo del campo") in stored
    assert res.code == 0, res.issues
    rb = _unit(res.manifest, "Sesión 2026-10-09").blocks[0]
    assert rb.guard_ack == "falso positivo del campo" and rb.pin is None


def test_set_short_ack_reason_rejected_4(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + GUARDED_L4})
    out = _plan(project)
    u = _unit(out.manifest, "Cambio 2026-10-06")
    err = _err(
        lambda: mf.set_override(
            project, Config(), RID, u.id, not_safety=u.blocks[0].key, ack_reason="corto", now=NOW
        )
    )
    assert err.code == 4 and "check.8" in _keys(err.issues)


def test_set_accept_clears_pending(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4})
    out = _plan(project)
    uid = _unit(out.manifest, "Sesión 2026-10-05").id
    res = mf.set_override(project, Config(), RID, uid, accept=True, reason="revisada", now=NOW)
    assert res.code == 0, res.issues
    report = json.loads((res.run_dir / "report.json").read_bytes())  # type: ignore[operator]
    assert report["pending_confirmation"] == []


def test_set_l1c_requires_dest_with_reason(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + L1C})
    out = _plan(project)
    uid = _unit(out.manifest, "Hooks de Claude Code").id
    res = mf.set_override(project, Config(), RID, uid, accept=True, reason="vale", now=NOW)
    assert res.code == 4 and "check.15" in _keys(res.issues)
    err = _err(lambda: mf.set_override(project, Config(), RID, uid, dest="L1c", now=NOW))
    assert err.code == 4 and "check.8" in _keys(err.issues)
    res = mf.set_override(project, Config(), RID, uid, dest="L1c", reason="solo Claude", now=NOW)
    assert res.code == 0, res.issues


def test_plan_from_imports_overrides_orphans_and_ambiguous(make_project: MakeProject) -> None:
    text = BASE + K_L4 + DUP + GUARDED_L4 + L1C
    project = make_project({"CLAUDE.md": text})
    out = _plan(project)
    m = out.manifest
    k = _unit(m, "Sesión 2026-10-05")
    dup2 = _unit(m, "Notas", 1)
    l1c = _unit(m, "Hooks de Claude Code")
    guarded = _unit(m, "Cambio 2026-10-06")
    cfg = Config()
    mf.set_override(project, cfg, RID, k.id, accept=True, reason="revisada", now=NOW)
    mf.set_override(project, cfg, RID, dup2.id, dest="L4", reason="mover", now=NOW)
    mf.set_override(project, cfg, RID, l1c.id, dest="L1c", reason="solo Claude", now=NOW)
    mf.set_override(
        project,
        cfg,
        RID,
        guarded.id,
        not_safety=guarded.blocks[0].id,
        ack_reason="no es un secreto real",
        now=NOW,
    )
    _claude(project, BASE + K_L4 + DUP + GUARDED_L4)  # the L1c unit is gone
    out2 = _plan(project, from_id=RID)
    assert out2.manifest.id == RID + "-2"
    ovs = out2.manifest.overrides
    assert UnitOverride(k.heading_key, "accept", True, "revisada") in ovs
    assert BlockOverride(guarded.blocks[0].key, "no es un secreto real") in ovs
    assert all(getattr(o, "heading_key", None) != dup2.heading_key for o in ovs)
    assert f"override_orphan:{l1c.heading_key}" in out2.manifest.warnings
    assert f"override_ambiguous:{dup2.heading_key}" in out2.manifest.warnings
    assert _unit(out2.manifest, "Notas", 1).dest == "L1"
    assert out2.code == 0, out2.issues


def test_plan_from_missing_run_exit_1(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    err = _err(lambda: _plan(project, from_id="20260101-0000"))
    assert err.code == 1 and "run_missing" in _keys(err.issues)


def test_check_missing_run_exit_1(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    err = _err(lambda: _check(project))
    assert err.code == 1 and "run_missing" in _keys(err.issues)


# --------------------------------------------------------------------------- zones, cards


def test_zone_with_claude_md_without_import_fails_4(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + ZONE, "src/CLAUDE.md": "# zona\n"})
    out = _plan(project)
    assert out.code == 4
    hit = next(i for i in out.issues if i.key == "check.11")
    assert hit.dest == "src/CLAUDE.md"
    (project.root / "src" / "CLAUDE.md").write_bytes(b"@AGENTS.md\n")
    assert _check(project).code == 0


def test_card_id_taken_between_plan_and_check_4(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4})
    out = _plan(project)
    u = _unit(out.manifest, "Sesión 2026-10-05")
    mf.set_override(project, Config(), RID, u.id, accept=True, reason="revisada", now=NOW)
    folder = project.root / "docs" / "decisiones"
    folder.mkdir(parents=True)
    (folder / f"{u.card_id}-tomada.md").write_bytes(b"# tomada\n")
    chk = _check(project)
    assert chk.code == 4
    assert any(i.key == "check.10" and i.unit == u.id for i in chk.issues)


def test_card_ids_stay_fixed_and_new_ones_do_not_collide(make_project: MakeProject) -> None:
    text = BASE + K_L4 + "\n## Sesión 2026-10-11\nDecidimos usar otra cola.\n"
    project = make_project({"CLAUDE.md": text})
    out = _plan(project)
    first = _unit(out.manifest, "Sesión 2026-10-05")
    other = _unit(out.manifest, "Sesión 2026-10-11")
    assert first.card_id == "D-001" and other.card_id is None
    mf.set_override(project, Config(), RID, first.id, accept=True, reason="r", now=NOW)
    res = mf.set_override(project, Config(), RID, other.id, card=True, reason="ficha", now=NOW)
    assert _unit(res.manifest, "Sesión 2026-10-05").card_id == "D-001"
    assert _unit(res.manifest, "Sesión 2026-10-11").card_id == "D-002"


# --------------------------------------------------------------------------- imports, links


def _import_project(make_project: MakeProject) -> Project:
    return make_project({"CLAUDE.md": BASE + IMPORT, "docs/reglas.md": "Regla importada.\n"})


def test_import_unit_stays_l1_and_loadset_includes_import(make_project: MakeProject) -> None:
    project = _import_project(make_project)
    out = _plan(project, dry_run=True)
    u = _unit(out.manifest, "Sesión 2026-10-07")
    assert u.dest == "L1" and "imports" in u.reasons and u.imports == 1
    loadset = out.manifest.projection["loadset"]
    assert isinstance(loadset, dict)
    assert "docs/reglas.md" in loadset["before"] and "docs/reglas.md" in loadset["after"]
    assert f"import_codex:{u.id}" in out.manifest.warnings
    assert out.code == 0


def test_set_dest_l4_on_import_unit_warns_import_moved_and_loadset_diff(
    make_project: MakeProject,
) -> None:
    project = _import_project(make_project)
    out = _plan(project)
    u = _unit(out.manifest, "Sesión 2026-10-07")
    res = mf.set_override(project, Config(), RID, u.id, dest="L4", reason="mover", now=NOW)
    assert f"import_moved:{u.id}" in res.manifest.warnings
    loadset = res.manifest.projection["loadset"]
    assert isinstance(loadset, dict)
    assert "docs/reglas.md" in loadset["removed"]
    assert "check.13" not in _keys(res.issues)


def test_open_branch_touching_source_warning(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE}, git=True)
    root = str(project.root)

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", root, *args], check=True, capture_output=True)

    git("switch", "-q", "-c", "otra")
    _claude(project, BASE + NOTE_L4)
    git("commit", "-q", "-am", "otra")
    git("switch", "-q", "main")
    out = _plan(project, dry_run=True)
    assert "open_branch_touches_source:otra" in out.manifest.warnings
    assert out.code == 0


def test_relative_link_moved_warning(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + LINK})
    out = _plan(project, dry_run=True)
    u = _unit(out.manifest, "Nota 2026-10-08")
    assert u.dest == "L4"
    assert f"relative_link_moved:{u.id}:{u.file}:1" in out.manifest.warnings


def test_paths_lang_en_detected_with_lang_es(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + NOTE_L4, "docs/history/README.md": "x\n"})
    out = _plan(project, lang="es", dry_run=True)
    assert out.manifest.paths_lang == "en" and out.manifest.lang == "es"
    assert _unit(out.manifest, "Nota 2026-10-03").file == "docs/history/2026-10.md"


# --------------------------------------------------------------------------- determinism, §13


def _second_project(tmp_path: Path, files: dict[str, str]) -> Project:
    home = tmp_path / "otra" / "home"
    root = home / "proj"
    root.mkdir(parents=True)
    (home / ".claude").mkdir()
    for rel, text in files.items():
        (root / rel).write_bytes(text.encode())
    return Project.at(root, home=home, claude_home=home / ".claude")


def test_deterministic_manifest_in_two_project_copies(
    make_project: MakeProject, tmp_path: Path
) -> None:
    files = {"CLAUDE.md": BASE + K_L4 + NOTE_L4 + G2 + GUARDED_L4}
    a = make_project(files)
    b = _second_project(tmp_path, files)
    out_a = _plan(a)
    out_b = _plan(b)
    data_a = (out_a.run_dir / "manifest.json").read_bytes()  # type: ignore[operator]
    data_b = (out_b.run_dir / "manifest.json").read_bytes()  # type: ignore[operator]
    assert data_a == data_b
    assert (out_a.run_dir / "report.json").read_bytes() == (  # type: ignore[operator]
        out_b.run_dir / "report.json"  # type: ignore[operator]
    ).read_bytes()


def test_dry_run_json_identical_between_runs(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4 + NOTE_L4 + L1C})
    one = mf.report_dumps(mf.outcome_report(_plan(project, dry_run=True)))
    two = mf.report_dumps(mf.outcome_report(_plan(project, dry_run=True)))
    assert one == two


def test_symlink_claude_to_agents_single_source(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": BASE + NOTE_L4})
    (project.root / "CLAUDE.md").symlink_to("AGENTS.md")
    out = _plan(project, dry_run=True)
    m = out.manifest
    assert [s.path for s in m.sources] == ["AGENTS.md"]
    assert m.symlink_claude
    modes = {o.path: o.mode for o in m.outputs}
    assert modes["CLAUDE.md"] == "rewritten" and modes["AGENTS.md"] == "rewritten"
    assert m.units[0].id == "U00"


def test_agents_md_previous_is_second_source(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE, "AGENTS.md": "# Ag\nIntro.\n\n## Otra\nTexto.\n"})
    out = _plan(project, dry_run=True)
    assert [s.path for s in out.manifest.sources] == ["CLAUDE.md", "AGENTS.md"]
    assert [u.id for u in out.manifest.units] == ["U00", "U01", "U00b", "U02"]


# --------------------------------------------------------------------------- G6


def test_reports_contain_no_content(make_project: MakeProject) -> None:
    text = (
        f"# Demo\nIntro {SEED}.\n\n## Cómo correr\nEjecuta {SEED}.\n"
        f"\n## Sesión 2026-10-05\n- Nunca uses {SEED}.\n"
        f"\n## Cambio 2026-10-06\nNo subas el password {SEED}.\n"
        + "\n## "
        + "Título largo sintético para recortar en el plan "
        + "y" * 40
        + "\nCuerpo.\n"
    )
    project = make_project({"CLAUDE.md": text})
    out = _plan(project, cfg=SMALL)
    run = out.run_dir
    assert run is not None
    for name in ("plan.md", "report.json"):
        data = (run / name).read_text(encoding="utf-8")
        assert SEED not in data
        assert "y" * 40 not in data
    blob = repr(out.issues) + repr(out.manifest.warnings) + repr(out.levers)
    assert SEED not in blob
    chk = _check(project, cfg=SMALL)
    assert SEED not in repr(chk.issues)


def test_load_run_rejects_invalid_manifest_4(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    _plan(project)
    _manifest_path(project).write_bytes(b"{}")
    err = _err(lambda: mf.load_run(project.root, RID))
    assert err.code == 4 and "manifest_invalid" in _keys(err.issues)


def test_report_units_bytes(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    out = _plan(project, dry_run=True)
    report = mf.outcome_report(out)
    units = report["units"]
    assert isinstance(units, list)
    doc = mdblocks.split_lines(BASE.encode())
    assert units[0]["bytes"] == sum(len(x) + 1 for x in doc.lines[:3])


TRAMPAS = "\n## Trampas conocidas\n- El caché tarda. Luego se arregla.\n"


def test_secret_in_existing_file_to_extend_exit_5(make_project: MakeProject) -> None:
    secret = "AKIA" + "QRSTUVWXYZABCDEF"
    project = make_project(
        {"CLAUDE.md": BASE + TRAMPAS, "docs/trampas.md": f"# Trampas\nclave {secret}\n"}
    )
    err = _err(lambda: _plan(project))
    assert err.code == 5
    hit = next(i for i in err.issues if i.key == "secret")
    assert hit.dest == "docs/trampas.md" and hit.at == (2, 2)
    assert secret not in str(err) + repr(err.issues)
    assert not (project.root / ".declutter").exists()


def test_existing_file_to_extend_with_open_fence_exit_2(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + TRAMPAS, "docs/trampas.md": "# T\n```\ncode\n"})
    err = _err(lambda: _plan(project))
    assert err.code == 2 and "extend_open_structure" in _keys(err.issues)
    assert not (project.root / ".declutter").exists()
