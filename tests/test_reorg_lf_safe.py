"""Enmienda 05-11 a §11.1: normalizing attributes that are the identity on CR-free bytes.

``* text=auto eol=lf`` (or ``core.autocrlf=input``) on content without a ``\\r`` byte never
changes a byte in either direction, so ``plan``/``apply`` tolerate it with the warning
``git_normalizes_lf_safe``; every other normalizing setting still refuses with code 1.
Synthetic repositories only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import cli
from declutter_context.model import Project
from declutter_context.reorg import manifest as mf
from test_apply import TEXT, commit_all, git, tree_bytes

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"
LF_ATTRS = "* text=auto eol=lf\n"


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


def _repo(
    make_project: MakeProject,
    claude: bytes = TEXT.encode(),
    attrs: str | None = LF_ATTRS,
    autocrlf: str | None = None,
) -> Project:
    project = make_project(git=True)
    if autocrlf is not None:
        git(project.root, "config", "core.autocrlf", autocrlf)
    if attrs is not None:
        (project.root / ".gitattributes").write_bytes(attrs.encode())
    (project.root / "CLAUDE.md").write_bytes(claude)
    commit_all(project.root)
    return project


def _plan_json(project: Project, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    code = _run("reorg", "plan", "-C", str(project.root), "--json")
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _warnings(project: Project) -> tuple[str, ...]:
    m = mf.loads((project.root / mf.RUN_DIR / RID / mf.MANIFEST_NAME).read_bytes())
    return m.warnings


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_text_auto_eol_lf_plan_apply_verify_undo(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _repo(make_project)
    root = str(project.root)
    before = _sha(project.root / "CLAUDE.md")
    code, _out = _plan_json(project, capsys)
    assert code == 0
    warnings = _warnings(project)
    assert "git_normalizes_lf_safe:CLAUDE.md:text=auto" in warnings
    assert "git_normalizes_lf_safe:CLAUDE.md:eol=lf" in warnings
    assert all("Demo" not in w for w in warnings)  # path and attr=value only, no content
    assert _run("reorg", "apply", RID, "-C", root, "--yes", "--commit") == 0
    capsys.readouterr()
    assert _run("reorg", "verify", RID, "-C", root, "--json") == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] is True and res["code"] == 0
    assert _run("reorg", "verify", RID, "-C", root, "--rev", "HEAD", "--strict") == 0
    # git sees nothing to renormalize: the work tree is clean after the commit.
    assert git(project.root, "status", "--porcelain").strip() == ""
    assert _run("reorg", "undo", RID, "-C", root, "--yes") == 0
    assert _sha(project.root / "CLAUDE.md") == before


def test_autocrlf_input_with_lf_is_tolerated(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _repo(make_project, attrs=None, autocrlf="input")
    code, _out = _plan_json(project, capsys)
    assert code == 0
    assert "git_normalizes_lf_safe:<config>:core.autocrlf=input" in _warnings(project)
    assert _run("reorg", "apply", RID, "-C", str(project.root), "--yes", "--commit") == 0
    assert _run("reorg", "verify", RID, "-C", str(project.root)) == 0


@pytest.mark.parametrize(
    ("claude", "attrs", "autocrlf"),
    [
        (TEXT.replace("\n", "\r\n").encode(), LF_ATTRS, None),  # CRLF content
        (TEXT.encode(), "* text=auto eol=crlf\n", None),
        (TEXT.encode(), None, "true"),
        (TEXT.encode(), "* text=auto eol=lf filter=x\n", None),
        (TEXT.encode(), "* text=auto\n", None),  # depends on core.eol / platform
        (TEXT.encode(), "* eol=lf\n", None),  # eol without text
    ],
    ids=["crlf-content", "eol-crlf", "autocrlf-true", "filter", "text-auto-no-eol", "eol-only"],
)
def test_still_refused_exit_1(
    make_project: MakeProject,
    capsys: pytest.CaptureFixture[str],
    claude: bytes,
    attrs: str | None,
    autocrlf: str | None,
) -> None:
    project = _repo(make_project, claude=claude, attrs=attrs, autocrlf=autocrlf)
    before = tree_bytes(project.root)
    code, out = _plan_json(project, capsys)
    assert code == 1
    assert "git_normalizes" in out
    assert tree_bytes(project.root) == before
    assert not (project.root / ".declutter").exists()


def test_apply_refuses_when_attrs_become_unsafe_after_plan(make_project: MakeProject) -> None:
    project = _repo(make_project)
    root = str(project.root)
    assert _run("reorg", "plan", "-C", root) == 0
    (project.root / ".gitattributes").write_bytes(b"* text=auto eol=crlf\n")
    before = tree_bytes(project.root)
    assert _run("reorg", "apply", RID, "-C", root, "--yes", "--commit") == 1
    assert tree_bytes(project.root) == before


@pytest.mark.parametrize(
    ("index", "code"), [(b"# Historial\n\n- antes\n", 0), (b"# Historial\r\n\r\n- antes\r\n", 1)]
)
def test_extended_index_needs_cr_free_current_bytes(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str], index: bytes, code: int
) -> None:
    project = make_project(git=True)
    (project.root / ".gitattributes").write_bytes(LF_ATTRS.encode())
    (project.root / "CLAUDE.md").write_bytes(TEXT.encode())
    path = project.root / "docs" / "historial" / "INDICE.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(index)
    commit_all(project.root)
    path.write_bytes(index)  # keep the CRLF on disk even if git normalized the blob
    got, out = _plan_json(project, capsys)
    assert got == code
    if code == 0:
        warnings = _warnings(project)
        assert "git_normalizes_lf_safe:docs/historial/INDICE.md:eol=lf" in warnings
        modes = {
            o.path: o.mode
            for o in mf.loads(
                (project.root / mf.RUN_DIR / RID / mf.MANIFEST_NAME).read_bytes()
            ).outputs
        }
        assert modes["docs/historial/INDICE.md"] == "extended"
    else:
        assert "git_normalizes" in out and "docs/historial/INDICE.md" in out
