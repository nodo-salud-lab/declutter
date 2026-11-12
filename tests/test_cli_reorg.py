"""``declutter reorg …`` and ``declutter ratchet …`` on the command line: argument shape
(``-C DIR``,
no positional root), exit codes 0-5 and 130 and their precedence (spec §3, §10.1, §10.4,
§10.5, §11.2; §15.1 items 9, 12 and 18).

Synthetic projects only (``make_project``); exact bytes written with ``write_bytes``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import cli, ratchet
from declutter_context import reorg as reorg_pkg
from declutter_context.model import Project
from declutter_context.ratchet import RatchetState
from declutter_context.reorg import manifest as mf
from test_apply import BASE, G2, K_L4, NOTE_L4, TEXT, commit_all, tree_bytes

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"
SEED = "ZQX-SEED-7731"
AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"
ZONE = "\n## Componentes\nVer src/a src/b src/c src/d src/e.\n"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setattr(cli, "_now", lambda: NOW)


def _run(*argv: str) -> int:
    return cli.main(list(argv))


def _root(project: Project) -> str:
    return str(project.root)


def _plan(project: Project, *extra: str) -> int:
    return _run("reorg", "plan", "-C", _root(project), *extra)


def _manifest(project: Project) -> mf.Manifest:
    return mf.loads((project.root / mf.RUN_DIR / RID / mf.MANIFEST_NAME).read_bytes())


def _unit_id(project: Project, heading: str) -> str:
    return next(u.id for u in _manifest(project).units if u.heading == heading)


# --------------------------------------------------------------------------- shape


def test_reorg_without_subcommand_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run("reorg") == 1
    captured = capsys.readouterr()
    assert "usage" in (captured.out + captured.err).lower()


def test_ratchet_without_subcommand_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run("ratchet") == 1
    captured = capsys.readouterr()
    assert "usage" in (captured.out + captured.err).lower()


def test_no_positional_root(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    assert _run("reorg", "plan", str(project.root), "--dry-run") == 1
    assert _run("ratchet", "check", str(project.root)) == 1


def test_C_must_be_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run("reorg", "plan", "-C", str(tmp_path / "nope"), "--dry-run") == 1
    assert "nope" in capsys.readouterr().err


def test_plan_without_C_uses_cwd(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project({"CLAUDE.md": TEXT})
    monkeypatch.chdir(project.root)
    assert _run("reorg", "plan") == 0
    assert (project.root / mf.RUN_DIR / RID / mf.MANIFEST_NAME).is_file()


def test_folder_named_status_with_C(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"status/CLAUDE.md": TEXT})
    monkeypatch.chdir(project.root)
    before = tree_bytes(project.root)
    assert _run("reorg", "plan", "-C", "status", "--dry-run", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert [s["path"] for s in report["sources"]] == ["CLAUDE.md"]
    assert tree_bytes(project.root) == before  # dry-run: nothing anywhere


def test_plan_writes_run_and_prints_id(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": TEXT})
    assert _plan(project, "--lang", "en") == 0
    out = capsys.readouterr().out
    assert RID in out
    assert _manifest(project).lang == "en"


def test_plan_dry_run_writes_nothing(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": TEXT})
    before = tree_bytes(project.root)
    assert _plan(project, "--dry-run") == 0
    assert tree_bytes(project.root) == before
    assert not (project.root / ".declutter").exists()


def test_plan_source_and_lang_choices(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": TEXT, "notes.txt": "x\n"})
    assert _plan(project, "--dry-run", "--lang", "fr") == 1
    capsys.readouterr()
    assert _plan(project, "--dry-run", "--source", "notes.txt") == 1
    assert "source_invalid" in capsys.readouterr().err
    assert _plan(project, "--source", "CLAUDE.md") == 0


# --------------------------------------------------------------------------- set / check


def test_set_reason_and_ack_reason_cli(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4})
    assert _plan(project) == 0
    uid = _unit_id(project, "Sesión 2026-10-05")
    root = _root(project)
    # no change requested -> 1
    assert _run("reorg", "set", RID, uid, "-C", root) == 1
    # --ack-reason without --not-safety -> 1
    assert _run("reorg", "set", RID, uid, "-C", root, "--ack-reason", "x" * 12) == 1
    # empty reason -> 4
    assert _run("reorg", "set", RID, uid, "-C", root, "--accept", "--reason", " ") == 4
    assert _run("reorg", "set", RID, uid, "-C", root, "--accept", "--reason", "revisada") == 0
    stored = [
        (getattr(o, "field", None), getattr(o, "reason", None))
        for o in _manifest(project).overrides
    ]
    assert ("accept", "revisada") in stored
    # --card takes yes|no only
    assert _run("reorg", "set", RID, uid, "-C", root, "--card", "maybe", "--reason", "r") == 1
    assert _run("reorg", "check", RID, "-C", root) == 0


def test_set_not_safety_with_short_ack_reason_4(make_project: MakeProject) -> None:
    text = BASE + "\n## Sesión 2026-10-09\n- Nunca subas el password al repo.\n"
    project = make_project({"CLAUDE.md": text})
    _plan(project)
    m = _manifest(project)
    u = next(x for x in m.units if x.heading == "Sesión 2026-10-09")
    block = next(b.id for b in u.blocks if b.guard)
    root = _root(project)
    code = _run(
        "reorg", "set", RID, u.id, "-C", root, "--not-safety", block, "--ack-reason", "corto"
    )
    assert code == 4
    code = _run(
        "reorg",
        "set",
        RID,
        u.id,
        "-C",
        root,
        "--not-safety",
        block,
        "--ack-reason",
        "no es una regla de seguridad",
    )
    assert code == 4  # recorded; check still asks to accept the L4 unit (check.16)
    keys = {getattr(o, "guard_ack", None) for o in _manifest(project).overrides}
    assert "no es una regla de seguridad" in keys


def test_check_json_and_missing_run(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": TEXT})
    assert _run("reorg", "check", RID, "-C", _root(project)) == 1
    assert "run_missing" in capsys.readouterr().err
    _plan(project)
    capsys.readouterr()
    assert _run("reorg", "check", RID, "-C", _root(project), "--json") == 0
    assert json.loads(capsys.readouterr().out)["code"] == 0


# --------------------------------------------------------------------------- apply / guide


def test_apply_commit_without_tty_or_yes_exit_1(make_project: MakeProject) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(TEXT.encode())
    commit_all(project.root)
    assert _plan(project) == 0
    before = tree_bytes(project.root)
    assert _run("reorg", "apply", RID, "-C", _root(project), "--commit") == 1
    assert tree_bytes(project.root) == before


def test_apply_yes_commit_then_verify_status_undo(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(TEXT.encode())
    commit_all(project.root)
    root = _root(project)
    assert _plan(project) == 0
    assert _run("reorg", "apply", RID, "-C", root, "--yes", "--commit") == 0
    capsys.readouterr()
    assert _run("reorg", "verify", RID, "-C", root, "--json") == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] is True and res["code"] == 0
    assert _run("reorg", "verify", RID, "-C", root, "--rev", "HEAD", "--strict") == 0
    capsys.readouterr()
    assert _run("reorg", "status", "-C", root, "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["id"] == RID and rows[0]["state"] == "committed"
    assert _run("ratchet", "check", "-C", root) == 0


def test_apply_no_git_dry_run(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": TEXT})
    root = _root(project)
    assert _plan(project) == 0
    before = tree_bytes(project.root)
    assert _run("reorg", "apply", RID, "-C", root, "--no-git", "--dry-run") == 0
    assert tree_bytes(project.root) == before
    # inside no repository, without --no-git -> 1
    assert _run("reorg", "apply", RID, "-C", root, "--yes") == 1


def test_guide_without_tty_runs_dry_run_and_writes_nothing(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": TEXT + f"\n## Otra\n{SEED}\n"})
    before = tree_bytes(project.root)
    assert _run("reorg", "guide", "-C", _root(project), "--lang", "en") == 0
    captured = capsys.readouterr()
    assert "plan --dry-run" in captured.out
    assert SEED not in captured.out + captured.err
    assert tree_bytes(project.root) == before
    assert not (project.root / ".declutter").exists()


def test_verify_without_run_folder_exit_1(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": TEXT})
    assert _run("reorg", "verify", RID, "-C", _root(project)) == 1
    assert "run_missing" in capsys.readouterr().err


def test_undo_and_prune_on_planned_run(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": TEXT})
    root = _root(project)
    _plan(project)
    assert _run("reorg", "undo", RID, "-C", root, "--yes") == 0
    assert _run("reorg", "status", RID, "-C", root) == 0
    assert _run("reorg", "prune", RID, "-C", root, "--yes") == 0
    assert not (project.root / mf.RUN_DIR / RID / "original").exists()
    assert _run("reorg", "status", "20991231-0000", "-C", root) == 1


# --------------------------------------------------------------------------- codes


def test_exit_code_constants_and_docstring() -> None:
    assert (cli.EXIT_OK, cli.EXIT_USAGE, cli.EXIT_INTERNAL, cli.EXIT_THRESHOLD) == (0, 1, 2, 3)
    assert cli.EXIT_PROOF == reorg_pkg.EXIT_PROOF == 4
    assert cli.EXIT_SECRET == reorg_pkg.EXIT_SECRET == 5
    assert cli.EXIT_INTERRUPTED == 130
    doc = cli.__doc__ or ""
    for code in ("0", "1", "2", "3", "4", "5", "130"):
        assert f"{code} " in doc
    assert "5 > 2 > 1 > 4 > 3" in doc
    assert "reorg" in doc and "ratchet" in doc


def _secret_and_non_utf8(project: Project) -> None:
    (project.root / "CLAUDE.md").write_bytes(b"# Demo\n\xff\xfe roto\n")
    (project.root / "AGENTS.md").write_bytes(f"# Ag\nclave {AKIA}\n".encode())


def _non_utf8_and_override(project: Project) -> None:
    (project.root / "CLAUDE.md").write_bytes(b"# Demo\n\xff\xfe roto\n")
    (project.root / "AGENTS.override.md").write_bytes(b"# override\n")


def _override_and_check_4(project: Project) -> None:
    (project.root / "CLAUDE.md").write_bytes((BASE + ZONE).encode())
    (project.root / "src").mkdir()
    (project.root / "src" / "CLAUDE.md").write_bytes(b"# zona\n")  # check.11 -> 4
    (project.root / "AGENTS.override.md").write_bytes(b"# override\n")


def _check_4_and_codex_3(project: Project) -> None:
    (project.root / "CLAUDE.md").write_bytes((BASE + ZONE).encode())
    (project.root / "src").mkdir()
    (project.root / "src" / "CLAUDE.md").write_bytes(b"# zona\n")  # check.11 -> 4
    (project.root / "declutter.toml").write_bytes(b"codex_budget_bytes = 50\n")


@pytest.mark.parametrize(
    ("setup", "expected", "keys"),
    [
        (_secret_and_non_utf8, 5, {"secret"}),
        (_non_utf8_and_override, 2, {"not_utf8"}),
        (_override_and_check_4, 1, {"agents_override"}),
        (_check_4_and_codex_3, 4, {"check.11", "check.9"}),
    ],
)
def test_precedence_combined(
    make_project: MakeProject,
    capsys: pytest.CaptureFixture[str],
    setup: object,
    expected: int,
    keys: set[str],
) -> None:
    project = make_project()
    assert callable(setup)
    setup(project)
    code = _plan(project)
    captured = capsys.readouterr()
    assert code == expected, captured.err
    for key in keys:
        assert key in captured.err
    assert AKIA not in captured.out + captured.err


def test_plan_json_is_metadata_only(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    body = f"\n## Otra 2026-10-04\n{SEED} texto sembrado.\n"
    project = make_project({"CLAUDE.md": BASE + NOTE_L4 + G2 + body})
    assert _plan(project, "--json") == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["code"] == 0
    assert SEED not in captured.out + captured.err
    run = project.root / mf.RUN_DIR / RID
    assert SEED not in (run / mf.PLAN_MD_NAME).read_text(encoding="utf-8")
    assert SEED not in (run / mf.REPORT_NAME).read_text(encoding="utf-8")
    assert _plan(project, "--dry-run") in (0, 1)  # text output, also without the seed
    captured = capsys.readouterr()
    assert SEED not in captured.out + captured.err


# --------------------------------------------------------------------------- ratchet


def _ratchet_setup(project: Project, s1_max: int) -> None:
    ctl = project.root / ".declutter"
    ctl.mkdir(exist_ok=True)
    state = RatchetState(s1_max=s1_max, s3_max_pct=100, reorg=RID)
    (ctl / "ratchet.json").write_bytes(ratchet.ratchet_dumps(state))
    (ctl / "pins.json").write_bytes(ratchet.pins_dumps(()))
    log = ratchet.log_line("baseline", date="2026-11-04", reorg=RID, old=None, new={}, reason="x")
    (ctl / "ratchet.log").write_bytes(log)


def test_ratchet_check_exit_3_on_failure(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = "".join(f"line {i}\n" for i in range(12))
    project = make_project({"AGENTS.md": lines, "CLAUDE.md": "@AGENTS.md\n"})
    _ratchet_setup(project, s1_max=6)
    assert _run("ratchet", "check", "-C", _root(project)) == 3
    assert "R1" in capsys.readouterr().err
    assert _run("ratchet", "check", "-C", _root(project), "--json") == 3
    report = json.loads(capsys.readouterr().out)
    assert report["code"] == 3 and report["failures"][0]["rule"] == "R1"
    # --staged outside git -> 1
    assert _run("ratchet", "check", "-C", _root(project), "--staged") == 1


def test_ratchet_relax_args(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "x\n", "CLAUDE.md": "@AGENTS.md\n"})
    _ratchet_setup(project, s1_max=6)
    root = _root(project)
    before = (project.root / ".declutter" / "ratchet.json").read_bytes()
    # mutually exclusive and required
    assert _run("ratchet", "relax", "-C", root, "--reason", "r") == 1
    assert (
        _run("ratchet", "relax", "-C", root, "--s1-to", "9", "--s3-to", "50", "--reason", "r") == 1
    )
    # --reason is required
    assert _run("ratchet", "relax", "-C", root, "--s1-to", "9") == 1
    # no TTY: G9 refuses -> 1, nothing written
    assert _run("ratchet", "relax", "-C", root, "--s1-to", "9", "--reason", "crece") == 1
    assert (project.root / ".declutter" / "ratchet.json").read_bytes() == before


def test_ratchet_unpin_repin_args(make_project: MakeProject) -> None:
    project = make_project({"AGENTS.md": "x\n", "CLAUDE.md": "@AGENTS.md\n"})
    _ratchet_setup(project, s1_max=6)
    root = _root(project)
    assert _run("ratchet", "unpin", "P-001", "-C", root) == 1  # --reason required
    assert _run("ratchet", "unpin", "P-001", "-C", root, "--reason", "r") == 1  # unknown pin
    assert _run("ratchet", "repin", "P-001", "-C", root, "--reason", "r") == 1  # --lines
    assert _run("ratchet", "repin", "P-001", "-C", root, "--lines", "x", "--reason", "r") == 1
    assert _run("ratchet", "repin", "P-001", "-C", root, "--lines", "3-1", "--reason", "r") == 1
    assert _run("ratchet", "repin", "P-001", "-C", root, "--lines", "1-1", "--reason", "r") == 1


# --------------------------------------------------------------------------- 130 and phase 1


def test_keyboard_interrupt_returns_130(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project({"CLAUDE.md": TEXT})

    def boom(_req: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(mf, "plan_project", boom)
    assert _plan(project, "--dry-run") == 130


def test_internal_error_exit_2(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": TEXT})

    def boom(_req: object) -> object:
        raise RuntimeError("kaput")

    monkeypatch.setattr(mf, "plan_project", boom)
    assert _plan(project, "--dry-run") == 2
    assert "internal error" in capsys.readouterr().err


def test_phase1_commands_unchanged(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": "# Rules\n- one\n", "AGENTS.md": "# Agents\n"})
    assert _run("measure", str(project.root), "--json") == 0
    assert json.loads(capsys.readouterr().out)
    assert _run("init", str(project.root), "--dry-run") == 0
    assert project.root.is_dir()


# --------------------------------------------------------------------------- mejoras


def test_codex_3_lists_codex_budget_command(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": BASE})
    (project.root / "declutter.toml").write_bytes(b"codex_budget_bytes = 50\n")
    assert _plan(project, "--dry-run") == 3
    out = capsys.readouterr().out
    assert "lever_codex_budget" in out
    assert "declutter reorg codex-budget -C" in out
    assert "--bytes 65536" in out
    assert _plan(project, "--dry-run", "--json") == 3
    report = json.loads(capsys.readouterr().out)
    cmd = report["lever_commands"]["lever_codex_budget"]
    assert cmd.startswith("declutter reorg codex-budget") and cmd.endswith("--bytes 65536")


K2_L4 = "\n## Sesión 2026-10-06\n- Nunca borres la tabla de prueba.\n"


def test_set_saved_with_other_units_pending_exits_0(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4 + K2_L4})
    assert _plan(project) == 0
    first = _unit_id(project, "Sesión 2026-10-05")
    second = _unit_id(project, "Sesión 2026-10-06")
    root = _root(project)
    capsys.readouterr()
    assert _run("reorg", "set", RID, first, "-C", root, "--accept", "--reason", "revisada") == 0
    captured = capsys.readouterr()
    assert f"1 pending confirmation(s) left: {second}" in captured.out
    assert "check.16" not in captured.err
    assert _run("reorg", "check", RID, "-C", root) == 4  # check still requires every one
    # the override's own validation error is still 4
    assert _run("reorg", "set", RID, second, "-C", root, "--dest", "L9", "--reason", "x") == 4
    capsys.readouterr()
    assert _run("reorg", "set", RID, second, "-C", root, "--accept", "--reason", "ok") == 0
    assert "confirmation(s) left" not in capsys.readouterr().out
    assert _run("reorg", "check", RID, "-C", root) == 0


def test_set_pending_others_and_codex_budget_exits_3(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4 + K2_L4})
    (project.root / "declutter.toml").write_bytes(b"codex_budget_bytes = 50\n")
    assert _plan(project) == 3
    first = _unit_id(project, "Sesión 2026-10-05")
    code = _run("reorg", "set", RID, first, "-C", _root(project), "--accept", "--reason", "r")
    assert code == 3


def test_set_pending_message_in_spanish(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": BASE + K_L4 + K2_L4})
    (project.root / "declutter.toml").write_bytes(b'lang = "es"\n')
    assert _plan(project) == 0
    first = _unit_id(project, "Sesión 2026-10-05")
    capsys.readouterr()
    assert _run("reorg", "set", RID, first, "-C", _root(project), "--accept", "--reason", "r") == 0
    assert "quedan 1 confirmaciones pendientes:" in capsys.readouterr().out


def test_report_json_has_lang_and_paths_lang(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project({"CLAUDE.md": BASE})
    assert _plan(project, "--dry-run", "--json", "--lang", "es") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "declutter.reorg-report/1"
    assert report["lang"] == "es" and report["paths_lang"] == "es"
