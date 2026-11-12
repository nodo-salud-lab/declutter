"""``declutter ratchet`` commands: ``check`` (R1-R6, ``--staged``), ``relax``, ``unpin`` and
``repin`` (spec §8 G8, G9, §12.2, §12.3, §15.1 items 12 and 14).

Synthetic projects only (``make_project``); exact bytes written with ``write_bytes``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime

import pytest

from conftest import MakeProject
from declutter_context import ratchet
from declutter_context.model import Config, Pin, Project
from declutter_context.ratchet import RatchetReport, RatchetState
from declutter_context.reorg import apply as ap
from declutter_context.reorg import manifest as mf
from declutter_context.reorg import mdblocks, undo
from declutter_context.reorg.lexicon import build_lexicon
from test_apply import TEXT, FakePrompter, commit_all, git, git_project, plan, run_apply

RID = "20261104-1530"
TODAY = date(2026, 11, 5)
SEED = "ZQX-SEED-7731"
LEX = build_lexicon({})


# --------------------------------------------------------------------------- helpers


def _lines(n: int, word: str = "line") -> str:
    return "".join(f"{word} {i}\n" for i in range(n))


def _sha(lines: Sequence[bytes]) -> str:
    return mdblocks.lines_sha(lines)


def _baseline_log(reorg: str = RID) -> bytes:
    return ratchet.log_line(
        "baseline", date="2026-11-04", reorg=reorg, old=None, new={}, reason="x"
    )


def _setup(
    project: Project,
    state: RatchetState | None = None,
    pins: Sequence[Pin] = (),
    log: bytes | None = None,
) -> None:
    ctl = project.root / ".declutter"
    ctl.mkdir(exist_ok=True)
    st = state or RatchetState(s1_max=100, s3_max_pct=100, reorg=RID)
    (ctl / "ratchet.json").write_bytes(ratchet.ratchet_dumps(st))
    (ctl / "pins.json").write_bytes(ratchet.pins_dumps(pins))
    (ctl / "ratchet.log").write_bytes(_baseline_log() if log is None else log)


def _pin(
    lines: Sequence[bytes],
    *,
    pid: str = "P-001",
    role: str = "copy",
    file: str = "AGENTS.md",
    unit: str = "U01",
) -> Pin:
    return Pin(
        id=pid,
        reorg=RID,
        unit=unit,
        block=None if role == "unit" else f"{unit}.b1",
        file=file,
        role=role,
        categories=("publishable",),
        lines=len(lines),
        sha256=_sha(lines),
    )


def _rules(report: RatchetReport) -> list[str]:
    return [f.rule for f in report.failures]


def _check(project: Project, cfg: Config | None = None, *, staged: bool = False) -> RatchetReport:
    return ratchet.check(project, cfg or Config(), staged=staged)


def _history(units: Mapping[str, Sequence[bytes]]) -> bytes:
    out = [b"# Historial 2026-10", b"estado: historico", b""]
    for uid, body in units.items():
        sha = _sha(body)
        out.append(f'<a id="r{RID}-{uid.lower()}"></a>'.encode())
        out.append(
            (
                f"<!-- declutter:src CLAUDE.md:1-{len(body)} sha256={sha} unit={uid} plan={RID} -->"
            ).encode()
        )
        out.extend(body)
        out.append(f"<!-- declutter:end {uid} -->".encode())
        out.append(b"")
    return b"".join(x + b"\n" for x in out)


# --------------------------------------------------------------------------- R1


def test_r1_border_plus5_passes_plus6_fails(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": _lines(10), "CLAUDE.md": "@AGENTS.md\n"})
    _setup(project, RatchetState(s1_max=6, s3_max_pct=100, reorg=RID))
    report = _check(project)  # 11 lines == 6 + 5
    assert report.code == 0 and report.failures == ()
    (project.root / "AGENTS.md").write_text(_lines(11), encoding="utf-8")
    report = _check(project)  # 12 lines == 6 + 6
    assert report.code == 3
    assert _rules(report) == ["R1"]


# --------------------------------------------------------------------------- R2


def test_r2_codex_over_max(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "x" * 499 + "\n"})  # 500 bytes
    cfg = Config(codex_budget_bytes=1000)
    _setup(project, RatchetState(s1_max=100, s3_max_pct=50, reorg=RID))
    assert _check(project, cfg).code == 0
    _setup(project, RatchetState(s1_max=100, s3_max_pct=49, reorg=RID))
    report = _check(project, cfg)
    assert report.code == 3
    assert _rules(report) == ["R2"]


# --------------------------------------------------------------------------- R3

DATED = "## Nueva función (05-11-2026)"


def test_r3_new_dated_heading_fails_allowed_passes(make_project: MakeProject) -> None:
    body = f"# P\n\n{DATED}\ntexto\n"
    project = make_project({"AGENTS.md": body, "src/AGENTS.md": f"# Z\n{DATED}\n", "src/a.py": ""})
    _setup(project)
    report = _check(project)
    assert report.code == 3
    assert sorted((f.rule, f.path) for f in report.failures) == [
        ("R3", "AGENTS.md"),
        ("R3", "src/AGENTS.md"),
    ]
    key = ratchet.title_key(DATED[3:].strip())
    _setup(project, RatchetState(s1_max=100, s3_max_pct=100, allowed_dated_headings=(key,)))
    assert _check(project).code == 0


def test_r3_in_claude_md_and_disabled_by_flag(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": f"{DATED}\n"})
    _setup(project)
    assert _rules(_check(project)) == ["R3"]
    _setup(project, RatchetState(s1_max=100, s3_max_pct=100, forbid_changelog_headings=False))
    assert _check(project).code == 0


def test_r3_ignores_heading_inside_fence(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": f"# P\n```\n{DATED}\n```\n"})
    _setup(project)
    assert _check(project).code == 0


# --------------------------------------------------------------------------- R4


def test_r4_zone_over_limit(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "# P\n", "src/AGENTS.md": _lines(20)})
    _setup(project, RatchetState(s1_max=100, s3_max_pct=100, zone_max_lines=20))
    assert _check(project).code == 0
    (project.root / "src" / "AGENTS.md").write_text(_lines(21), encoding="utf-8")
    report = _check(project)
    assert report.code == 3
    assert [(f.rule, f.path) for f in report.failures] == [("R4", "src/AGENTS.md")]


# --------------------------------------------------------------------------- R5


def test_r5_missing_pin_and_unit_pin(make_project: MakeProject) -> None:
    block = [b"- nunca publiques la clave"]
    unit = [b"## Datos de pacientes", b"Todo dato es confidencial."]
    agents = b"# P\n\n" + b"\n".join(block) + b"\n\n" + b"\n".join(unit) + b"\n"
    project = make_project()
    (project.root / "AGENTS.md").write_bytes(agents)
    pins = [_pin(block), _pin(unit, pid="P-002", role="unit", unit="U02")]
    _setup(project, pins=pins)
    assert _check(project).code == 0
    (project.root / "AGENTS.md").write_bytes(b"# P\n\n## Datos\nTodo dato es confidencial.\n")
    report = _check(project)
    assert report.code == 3
    assert _rules(report) == ["R5", "R5"]
    assert {f.path for f in report.failures} == {"AGENTS.md"}
    assert any("P-001" in f.detail for f in report.failures)
    assert any("P-002" in f.detail and "unit" in f.detail for f in report.failures)


def test_r5_released_by_unpin(make_project: MakeProject) -> None:
    block = [b"- nunca publiques la clave"]
    project = make_project({"AGENTS.md": "# P\n"})
    log = _baseline_log() + ratchet.log_line(
        "unpin", date="2026-11-05", reorg=RID, old=None, new=None, reason="ya no", pin="P-001"
    )
    _setup(project, pins=[_pin(block)], log=log)
    assert _check(project).code == 0
    # an unpin logged before the pin's baseline does not release it
    log = (
        ratchet.log_line(
            "unpin", date="2026-11-03", reorg=RID, old=None, new=None, reason="antes", pin="P-001"
        )
        + _baseline_log()
    )
    _setup(project, pins=[_pin(block)], log=log)
    assert _rules(_check(project)) == ["R5"]


def test_r5_repinned_sha_is_still_enforced(make_project: MakeProject) -> None:
    new = [b"- nunca publiques la clave nueva"]
    project = make_project({"AGENTS.md": "# P\n"})
    pin = _pin(new)
    log = _baseline_log() + ratchet.log_line(
        "repin",
        date="2026-11-05",
        reorg=RID,
        old={"sha256": "0" * 64, "lines": 1},
        new={"sha256": pin.sha256, "lines": 1},
        reason="editado",
        pin="P-001",
    )
    _setup(project, pins=[pin], log=log)
    assert _rules(_check(project)) == ["R5"]


# --------------------------------------------------------------------------- R6


def test_r6_trailing_space_trimmed_inside_src_block(make_project: MakeProject) -> None:
    body = [b"## Nota 2026-10-03", b"salto forzado  ", b"fin"]
    project = make_project()
    hist = project.root / "docs" / "historial"
    hist.mkdir(parents=True)
    (hist / "2026-10.md").write_bytes(_history({"U02": body}))
    _setup(project)
    assert _check(project).code == 0
    (hist / "2026-10.md").write_bytes(_history({"U02": body}).replace(b"forzado  ", b"forzado"))
    report = _check(project)
    assert report.code == 3
    assert [(f.rule, f.path) for f in report.failures] == [("R6", "docs/historial/2026-10.md")]


@pytest.mark.parametrize(
    "drop", [b"<!-- declutter:end U02 -->\n", b"<!-- declutter:src "], ids=["end", "src"]
)
def test_r6_orphan_marker(make_project: MakeProject, drop: bytes) -> None:
    body = [b"## Nota 2026-10-03", b"texto"]
    project = make_project()
    (project.root / "docs").mkdir()
    data = _history({"U02": body})
    if drop.startswith(b"<!-- declutter:src"):
        start = data.index(drop)
        data = data[:start] + data[data.index(b"\n", start) + 1 :]
    else:
        data = data.replace(drop, b"")
    (project.root / "docs" / "trampas.md").write_bytes(data)
    _setup(project)
    report = _check(project)
    assert report.code == 3
    assert [(f.rule, f.path) for f in report.failures] == [("R6", "docs/trampas.md")]


def test_r6_english_paths_and_map(make_project: MakeProject) -> None:
    body = [b"## Map", b"src/ code"]
    project = make_project()
    (project.root / "docs" / "history").mkdir(parents=True)
    bad = _history({"U03": body}).replace(b"src/ code", b"src/ cod")
    (project.root / "docs" / "history" / "2026-10.md").write_bytes(bad)
    (project.root / "docs" / "map.md").write_bytes(bad)
    _setup(project)
    report = _check(project)
    assert sorted((f.rule, f.path) for f in report.failures) == [
        ("R6", "docs/history/2026-10.md"),
        ("R6", "docs/map.md"),
    ]


# --------------------------------------------------------------------------- staged


def test_staged_reads_index_not_worktree(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "# P\n"}, git=True)
    _setup(project)
    commit_all(project.root)
    (project.root / "AGENTS.md").write_text(f"# P\n{DATED}\n", encoding="utf-8")
    assert _rules(_check(project)) == ["R3"]
    assert _check(project, staged=True).code == 0
    git(project.root, "add", "AGENTS.md")
    (project.root / "AGENTS.md").write_text("# P\n", encoding="utf-8")
    assert _check(project).code == 0
    assert _rules(_check(project, staged=True)) == ["R3"]


def test_staged_reads_control_files_from_index(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "# P\n"}, git=True)
    _setup(project)
    commit_all(project.root)
    (project.root / ".declutter" / "ratchet.json").write_bytes(b"{")
    assert _check(project).code == 2
    assert _check(project, staged=True).code == 0


def test_staged_without_git_exit_1(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "# P\n"})
    _setup(project)
    report = _check(project, staged=True)
    assert report.code == 1
    assert _rules(report) == ["no_git"]


# --------------------------------------------------------------------------- schema


@pytest.mark.parametrize(
    "name, data",
    [
        ("ratchet.json", b"{"),
        ("ratchet.json", b'{"schema": "declutter.ratchet/2"}'),
        ("pins.json", b'{"schema": "declutter.pins/1", "pins": [{"id": "X"}]}'),
        ("ratchet.log", b"not json\n"),
    ],
)
def test_invalid_schema_exit_2(make_project: MakeProject, name: str, data: bytes) -> None:
    project = make_project({"AGENTS.md": "# P\n"})
    _setup(project)
    (project.root / ".declutter" / name).write_bytes(data)
    report = _check(project)
    assert report.code == 2
    assert [(f.rule, f.path) for f in report.failures] == [("schema", f".declutter/{name}")]


def test_missing_baseline_exit_1(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "# P\n"})
    report = _check(project)
    assert report.code == 1
    assert _rules(report) == ["no_baseline"]


def test_missing_pins_with_baseline_exit_2(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "# P\n"})
    _setup(project)
    (project.root / ".declutter" / "pins.json").unlink()
    report = _check(project)
    assert report.code == 2
    assert [(f.rule, f.path) for f in report.failures] == [("schema", ".declutter/pins.json")]


def test_precedence_schema_over_threshold(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": f"# P\n{DATED}\n"})
    _setup(project)
    (project.root / ".declutter" / "pins.json").write_bytes(b"[]")
    report = _check(project)
    assert report.code == 2
    assert sorted(_rules(report)) == ["R3", "schema"]


# --------------------------------------------------------------------------- relax/unpin/repin


def _log(project: Project) -> list[dict[str, object]]:
    data = (project.root / ".declutter" / "ratchet.log").read_bytes()
    return [json.loads(x) for x in data.splitlines() if x]


def _pins(project: Project) -> tuple[Pin, ...]:
    return ratchet.pins_loads((project.root / ".declutter" / "pins.json").read_bytes())


def _state(project: Project) -> RatchetState:
    return ratchet.ratchet_loads((project.root / ".declutter" / "ratchet.json").read_bytes())


def _pinned_project(make_project: MakeProject) -> tuple[Project, Pin]:
    block = [b"- nunca publiques la clave"]
    project = make_project()
    (project.root / "AGENTS.md").write_bytes(b"# P\n\n" + block[0] + b"\n")
    pin = _pin(block)
    _setup(project, RatchetState(s1_max=10, s3_max_pct=40, reorg=RID), pins=[pin])
    return project, pin


def test_relax_unpin_repin_logged(make_project: MakeProject) -> None:
    project, pin = _pinned_project(make_project)

    p = FakePrompter(tty=True, answers=["confirmo"])
    code = ratchet.relax(
        project, s1_to=20, s3_to=None, reason="crece la doc", prompter=p, today=TODAY
    )
    assert code == 0
    assert _state(project).s1_max == 20 and _state(project).s3_max_pct == 40
    p = FakePrompter(tty=True, answers=["confirm"])
    code = ratchet.relax(project, s1_to=None, s3_to=55, reason="codex", prompter=p, today=TODAY)
    assert code == 0 and _state(project).s3_max_pct == 55

    (project.root / "AGENTS.md").write_bytes(b"# P\n\n- Nunca subas el password al repo.\n")
    p = FakePrompter(tty=True, answers=["confirmo"])
    code = ratchet.repin(
        project,
        "P-001",
        lines=(3, 3),
        reason="editado a mano",
        prompter=p,
        lexicon=LEX,
        today=TODAY,
    )
    assert code == 0
    new_pin = _pins(project)[0]
    assert new_pin.id == "P-001" and new_pin.lines == 1
    assert new_pin.sha256 == _sha([b"- Nunca subas el password al repo."]) != pin.sha256
    assert any("secrets" in s for s in p.said)  # current categories per the lexicon
    assert _check(project).code == 0

    p = FakePrompter(tty=True, answers=["confirmo"])
    code = ratchet.unpin(project, "P-001", reason="ya no aplica", prompter=p, today=TODAY)
    assert code == 0

    events = _log(project)
    assert [e["event"] for e in events] == ["baseline", "relax", "relax", "repin", "unpin"]
    relax1, relax2, repin, unpin = events[1:]
    assert relax1["old"] == {"S1_max": 10} and relax1["new"] == {"S1_max": 20}
    assert relax2["old"] == {"S3_max_pct": 40} and relax2["new"] == {"S3_max_pct": 55}
    assert relax1["reason"] == "crece la doc" and relax1["date"] == "2026-11-05"
    assert repin["pin"] == "P-001" and repin["reorg"] == RID
    assert repin["old"] == {"sha256": pin.sha256, "lines": 1}
    assert repin["new"] == {"sha256": new_pin.sha256, "lines": 1}
    assert unpin["pin"] == "P-001" and unpin["reason"] == "ya no aplica"
    (project.root / "AGENTS.md").write_bytes(b"# P\n")
    assert _check(project).code == 0  # released by the unpin


@pytest.mark.parametrize("cmd", ["relax", "unpin", "repin"])
def test_relax_unpin_repin_without_tty_exit_1(make_project: MakeProject, cmd: str) -> None:
    project, _ = _pinned_project(make_project)
    before = {
        n: (project.root / ".declutter" / n).read_bytes()
        for n in ("ratchet.json", "pins.json", "ratchet.log")
    }
    p = FakePrompter(tty=False, answers=["confirmo"])
    if cmd == "relax":
        code = ratchet.relax(
            project, s1_to=20, s3_to=None, reason="motivo", prompter=p, today=TODAY
        )
    elif cmd == "unpin":
        code = ratchet.unpin(project, "P-001", reason="motivo", prompter=p, today=TODAY)
    else:
        code = ratchet.repin(
            project, "P-001", lines=(1, 1), reason="motivo", prompter=p, lexicon=LEX, today=TODAY
        )
    assert code == 1
    after = {n: (project.root / ".declutter" / n).read_bytes() for n in before}
    assert after == before


@pytest.mark.parametrize("cmd", ["relax", "unpin", "repin"])
def test_wrong_confirmation_word_exit_1(make_project: MakeProject, cmd: str) -> None:
    project, _ = _pinned_project(make_project)
    before = (project.root / ".declutter" / "ratchet.log").read_bytes()
    p = FakePrompter(tty=True, answers=["si"])
    if cmd == "relax":
        code = ratchet.relax(
            project, s1_to=20, s3_to=None, reason="motivo", prompter=p, today=TODAY
        )
    elif cmd == "unpin":
        code = ratchet.unpin(project, "P-001", reason="motivo", prompter=p, today=TODAY)
    else:
        code = ratchet.repin(
            project, "P-001", lines=(1, 1), reason="motivo", prompter=p, lexicon=LEX, today=TODAY
        )
    assert code == 1
    assert (project.root / ".declutter" / "ratchet.log").read_bytes() == before


def test_command_usage_errors_exit_1(make_project: MakeProject) -> None:
    project, _ = _pinned_project(make_project)
    tty = FakePrompter(tty=True, answers=["confirmo"] * 10)
    # relax: exactly one maximum, it must go up, reason required
    assert (
        ratchet.relax(project, s1_to=None, s3_to=None, reason="m", prompter=tty, today=TODAY) == 1
    )
    assert ratchet.relax(project, s1_to=20, s3_to=50, reason="m", prompter=tty, today=TODAY) == 1
    assert ratchet.relax(project, s1_to=10, s3_to=None, reason="m", prompter=tty, today=TODAY) == 1
    assert ratchet.relax(project, s1_to=20, s3_to=None, reason="  ", prompter=tty, today=TODAY) == 1
    # unknown pin, bad range
    assert ratchet.unpin(project, "P-999", reason="m", prompter=tty, today=TODAY) == 1
    assert (
        ratchet.repin(
            project, "P-001", lines=(3, 9), reason="m", prompter=tty, lexicon=LEX, today=TODAY
        )
        == 1
    )
    assert tty.prompts == []
    assert [e["event"] for e in _log(project)] == ["baseline"]


def test_commands_invalid_schema_exit_2(make_project: MakeProject) -> None:
    project, _ = _pinned_project(make_project)
    (project.root / ".declutter" / "pins.json").write_bytes(b"{")
    tty = FakePrompter(tty=True, answers=["confirmo"])
    assert ratchet.unpin(project, "P-001", reason="m", prompter=tty, today=TODAY) == 2


def test_unpin_twice_exit_1(make_project: MakeProject) -> None:
    project, _ = _pinned_project(make_project)
    tty = FakePrompter(tty=True, answers=["confirmo", "confirmo"])
    assert ratchet.unpin(project, "P-001", reason="m", prompter=tty, today=TODAY) == 0
    assert ratchet.unpin(project, "P-001", reason="m", prompter=tty, today=TODAY) == 1


# --------------------------------------------------------------------------- integration


NOW2 = datetime(2026, 11, 6, 9, 0)


def test_maxima_do_not_increase_on_second_apply(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    first = _state(project)
    assert ratchet.check(project, Config(), staged=False).code == 0
    # two L1 lines are removed by hand and a new dated section is added; a second reorg
    # moves that section out again, so the measured S1 is below the first maximum
    agents = project.root / "AGENTS.md"
    data = agents.read_bytes()
    for line in (b"Intro del proyecto.\n", b"Ejecuta make test.\n"):
        assert line in data
        data = data.replace(line, b"", 1)
    agents.write_bytes(data + b"\n## Nota 2026-11-02\nOtra sesion.\n")
    commit_all(project.root, "edit")
    assert "R3" in _rules(ratchet.check(project, Config(), staged=False))
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=Config(), lang="es", now=NOW2))
    rid2 = out.manifest.id
    res = ap.apply(
        project,
        Config(),
        rid2,
        # with --commit: the unchanged final .gitignore no longer fails C8b
        ap.ApplyOptions(yes=True, commit=True),
        FakePrompter(tty=False),
        now=NOW2,
        lang="es",
    )
    assert res.code == 0, res.issues
    second = _state(project)
    assert second.reorg == rid2
    assert second.s1_max < first.s1_max  # only goes down
    assert second.s3_max_pct <= first.s3_max_pct
    assert second.zone_max_lines <= first.zone_max_lines
    assert ratchet.check(project, Config(), staged=False).code == 0
    assert [e["event"] for e in _log(project)] == ["baseline", "baseline"]


def test_verify_posterior_after_unpin_is_0(make_project: MakeProject) -> None:
    project = git_project(make_project)  # TEXT has the G2 unit "Datos de pacientes"
    assert "## Datos de pacientes" in TEXT
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    unit_pin = next(p for p in _pins(project) if p.role == "unit")
    agents = project.root / "AGENTS.md"
    agents.write_bytes(agents.read_bytes().replace(b"## Datos de pacientes\n", b""))
    res = undo.verify_run(project, Config(), RID, rev=None, strict=False)
    assert not res.ok
    assert _rules(ratchet.check(project, Config(), staged=False)) == ["R5"]
    p = FakePrompter(tty=True, answers=["confirmo"])
    code = ratchet.unpin(project, unit_pin.id, reason="titulo cambiado", prompter=p, today=TODAY)
    assert code == 0
    res = undo.verify_run(project, Config(), RID, rev=None, strict=False)
    assert res.ok, res.failures
    assert ratchet.check(project, Config(), staged=False).code == 0


def test_failures_have_no_content(make_project: MakeProject) -> None:
    seeded = f"- nunca publiques {SEED}".encode()
    project = make_project()
    (project.root / "AGENTS.md").write_bytes(
        b"# P\n" + (f"{SEED} cuerpo\n" * 30).encode() + f"{DATED}\n{SEED}\n".encode()
    )
    (project.root / "src").mkdir()
    (project.root / "src" / "AGENTS.md").write_bytes((f"{SEED}\n" * 30).encode())
    (project.root / "docs").mkdir()
    body = [b"## Nota 2026-10-03", f"{SEED} literal  ".encode()]
    (project.root / "docs" / "mapa.md").write_bytes(
        _history({"U02": body}).replace(b"literal  ", b"literal")
    )
    _setup(
        project,
        RatchetState(s1_max=1, s3_max_pct=0, zone_max_lines=20, reorg=RID),
        pins=[_pin([seeded])],
    )
    report = _check(project)
    assert report.code == 3
    assert sorted(set(_rules(report))) == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert SEED not in repr(report)
    p = FakePrompter(tty=True, answers=["confirmo"])
    (project.root / "AGENTS.md").write_bytes(seeded + b"\n")
    assert (
        ratchet.repin(
            project, "P-001", lines=(1, 1), reason="motivo", prompter=p, lexicon=LEX, today=TODAY
        )
        == 0
    )
    assert SEED not in "\n".join(p.prompts + p.said)


def test_check_does_not_write(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": f"# P\n{DATED}\n"})
    _setup(project)
    before = sorted(str(p) for p in project.root.rglob("*"))
    _check(project)
    assert sorted(str(p) for p in project.root.rglob("*")) == before


def test_repin_same_sha_is_noop(make_project: MakeProject) -> None:
    project, _ = _pinned_project(make_project)
    before = (project.root / ".declutter" / "pins.json").read_bytes()
    p = FakePrompter(tty=False)
    code = ratchet.repin(
        project, "P-001", lines=(3, 3), reason="motivo", prompter=p, lexicon=LEX, today=TODAY
    )
    assert code == 0 and p.prompts == []
    assert (project.root / ".declutter" / "pins.json").read_bytes() == before
    assert [e["event"] for e in _log(project)] == ["baseline"]


def test_messages_in_english(make_project: MakeProject) -> None:
    project, _ = _pinned_project(make_project)
    p = FakePrompter(tty=True, answers=["confirm"])
    code = ratchet.relax(
        project, s1_to=11, s3_to=None, reason="docs", prompter=p, today=TODAY, lang="en"
    )
    assert code == 0
    assert p.prompts == ["Type 'confirm' to continue: "]
