"""reorg.gitops: byte-exact git reads, preconditions and writes (spec §3, §10.2, §11, §12.2).

Every repository is synthetic (``make_project(git=True)``). Content written to files carries
the unique marker ``ZQX-SEED-7731``; no :class:`GitError` text may contain it (G6).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context.reorg import gitops

SEED = "ZQX-SEED-7731"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


def _commit_all(root: Path, msg: str = "c") -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


def test_is_repo_and_head(make_project: MakeProject, tmp_path: Path) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    assert gitops.is_repo(p.root)
    assert gitops.head_branch(p.root) == "main"
    head = gitops.head_commit(p.root)
    assert head is not None and len(head) == 40
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not gitops.is_repo(plain)


def test_head_branch_detached_is_none(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    head = gitops.head_commit(p.root)
    assert head is not None
    _git(p.root, "switch", "-q", "--detach", head)
    assert gitops.head_branch(p.root) is None
    assert gitops.head_commit(p.root) == head


def test_head_branch_keeps_slashes(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    gitops.switch_create(p.root, "declutter/reorg-20261104-1530")
    assert gitops.head_branch(p.root) == "declutter/reorg-20261104-1530"
    gitops.switch(p.root, "main")
    assert gitops.head_branch(p.root) == "main"


def test_show_bytes_exact_crlf_and_index(make_project: MakeProject) -> None:
    p = make_project(git=True)
    raw = b"\xef\xbb\xbf# t\r\nline  \r\n\rno-eol"
    (p.root / "CLAUDE.md").write_bytes(raw)
    _commit_all(p.root)
    assert gitops.show_bytes(p.root, "HEAD", "CLAUDE.md") == raw
    staged = raw + b"\r\nmore\r\n"
    (p.root / "CLAUDE.md").write_bytes(staged)
    gitops.add(p.root, ["CLAUDE.md"])
    (p.root / "CLAUDE.md").write_bytes(b"worktree only\n")
    assert gitops.show_bytes(p.root, "", "CLAUDE.md") == staged
    assert gitops.show_bytes(p.root, "HEAD", "CLAUDE.md") == raw
    assert gitops.show_bytes(p.root, "HEAD", "missing.md") is None
    assert gitops.show_bytes(p.root, "", "missing.md") is None


def test_blob_matches_disk_and_mismatch_with_autocrlf(make_project: MakeProject) -> None:
    p = make_project(git=True)
    _git(p.root, "config", "core.autocrlf", "true")
    (p.root / "CLAUDE.md").write_bytes(b"# t\r\nbody\r\n")
    _commit_all(p.root)
    disk = (p.root / "CLAUDE.md").read_bytes()
    # git status is clean, yet the blob was normalized to LF: the bytes differ.
    assert gitops.dirty(p.root, ["CLAUDE.md"]) == ()
    assert gitops.show_bytes(p.root, "HEAD", "CLAUDE.md") == b"# t\nbody\n"
    assert not gitops.blob_matches_disk(p.root, "CLAUDE.md", disk)
    (p.root / "AGENTS.md").write_bytes(b"# a\n")
    _commit_all(p.root)
    assert gitops.blob_matches_disk(p.root, "AGENTS.md", b"# a\n")
    assert not gitops.blob_matches_disk(p.root, "untracked.md", b"x\n")


@pytest.mark.parametrize(
    ("config", "attrs", "expected"),
    [
        ("true", None, (("<config>", "core.autocrlf", "true"),)),
        ("input", None, (("<config>", "core.autocrlf", "input"),)),
        ("false", None, ()),
        (None, "*.md text\n", (("CLAUDE.md", "text", "set"),)),
        (None, "*.md text=auto\n", (("CLAUDE.md", "text", "auto"),)),
        (None, "*.md eol=lf\n", (("CLAUDE.md", "eol", "lf"),)),
        (None, "*.md filter=x\n", (("CLAUDE.md", "filter", "x"),)),
        (None, "*.md -text\n", ()),
        (None, "*.txt text\n", ()),
    ],
)
def test_normalizing_detects_autocrlf_text_eol_filter(
    make_project: MakeProject,
    config: str | None,
    attrs: str | None,
    expected: tuple[tuple[str, str, str], ...],
) -> None:
    files = {"CLAUDE.md": "# a\n"}
    if attrs is not None:
        files[".gitattributes"] = attrs
    p = make_project(files, git=True)
    if config is not None:
        _git(p.root, "config", "core.autocrlf", config)
    assert gitops.normalizing(p.root, ["CLAUDE.md"]) == expected


def test_normalizing_without_paths_still_checks_config(make_project: MakeProject) -> None:
    p = make_project({".gitattributes": "* text\n"}, git=True)
    assert gitops.normalizing(p.root, []) == ()
    _git(p.root, "config", "core.autocrlf", "true")
    assert gitops.normalizing(p.root, []) == (("<config>", "core.autocrlf", "true"),)


def test_operation_in_progress_merge(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\nbase\n"}, git=True)
    assert gitops.operation_in_progress(p.root) is None
    _git(p.root, "switch", "-q", "-c", "other")
    (p.root / "CLAUDE.md").write_text("# a\nother\n", encoding="utf-8")
    _commit_all(p.root)
    _git(p.root, "switch", "-q", "main")
    (p.root / "CLAUDE.md").write_text("# a\nmain\n", encoding="utf-8")
    _commit_all(p.root)
    proc = subprocess.run(
        ["git", "-C", str(p.root), "merge", "-q", "other"], capture_output=True, check=False
    )
    assert proc.returncode != 0
    assert gitops.operation_in_progress(p.root) == "merge"
    _git(p.root, "merge", "--abort")
    assert gitops.operation_in_progress(p.root) is None


def test_operation_in_progress_cherry_pick(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\nbase\n"}, git=True)
    _git(p.root, "switch", "-q", "-c", "other")
    (p.root / "CLAUDE.md").write_text("# a\nother\n", encoding="utf-8")
    _commit_all(p.root)
    other = gitops.head_commit(p.root)
    assert other is not None
    _git(p.root, "switch", "-q", "main")
    (p.root / "CLAUDE.md").write_text("# a\nmain\n", encoding="utf-8")
    _commit_all(p.root)
    proc = subprocess.run(
        ["git", "-C", str(p.root), "cherry-pick", other], capture_output=True, check=False
    )
    assert proc.returncode != 0
    assert gitops.operation_in_progress(p.root) == "cherry-pick"


def test_ref_exists_tag_and_branch(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    rid = "20261104-1530"
    assert not gitops.ref_exists(p.root, f"refs/tags/declutter/pre-reorg-{rid}")
    assert not gitops.ref_exists(p.root, f"refs/heads/declutter/reorg-{rid}")
    gitops.tag(p.root, f"declutter/pre-reorg-{rid}")
    gitops.switch_create(p.root, f"declutter/reorg-{rid}")
    assert gitops.ref_exists(p.root, f"refs/tags/declutter/pre-reorg-{rid}")
    assert gitops.ref_exists(p.root, f"refs/heads/declutter/reorg-{rid}")
    # The tag is lightweight: it points straight at the commit.
    out = _git(p.root, "cat-file", "-t", f"refs/tags/declutter/pre-reorg-{rid}").stdout
    assert out.strip() == b"commit"
    assert not gitops.ref_exists(p.root, "refs/heads/nope")


def test_dirty_and_status_paths(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n", "AGENTS.md": "# b\n", "x.txt": "x\n"}, git=True)
    assert gitops.dirty(p.root, ["CLAUDE.md", "AGENTS.md"]) == ()
    assert gitops.status_paths(p.root) == frozenset()
    (p.root / "CLAUDE.md").write_text("# a changed\n", encoding="utf-8")
    (p.root / "new.md").write_text("n\n", encoding="utf-8")
    (p.root / "x.txt").write_text("y\n", encoding="utf-8")
    assert gitops.dirty(p.root, ["CLAUDE.md", "AGENTS.md", "new.md"]) == ("CLAUDE.md", "new.md")
    assert gitops.dirty(p.root, []) == ()
    assert gitops.status_paths(p.root) == frozenset({"CLAUDE.md", "new.md", "x.txt"})


def test_dirty_reports_untracked_inside_new_dir(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    (p.root / "docs" / "historial").mkdir(parents=True)
    (p.root / "docs" / "historial" / "2026-10.md").write_text("h\n", encoding="utf-8")
    assert gitops.dirty(p.root, ["docs/historial/2026-10.md"]) == ("docs/historial/2026-10.md",)


def test_commit_only_does_not_include_foreign_change(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n", "foreign.txt": "f\n"}, git=True)
    base = gitops.head_commit(p.root)
    assert base is not None
    (p.root / "foreign.txt").write_text("foreign edit\n", encoding="utf-8")
    _git(p.root, "add", "foreign.txt")  # even a staged foreign change must not be committed
    (p.root / "CLAUDE.md").write_text("# a\n@AGENTS.md\n", encoding="utf-8")
    (p.root / "AGENTS.md").write_bytes(b"# new\r\n")
    gitops.add(p.root, ["AGENTS.md"])
    sha = gitops.commit_only(
        p.root,
        "declutter(reorg x): subject",
        "Declutter-Manifest: x sha256=00",
        ["CLAUDE.md", "AGENTS.md"],
    )
    assert sha == gitops.head_commit(p.root)
    assert gitops.diff_names(p.root, base, sha) == frozenset({"CLAUDE.md", "AGENTS.md"})
    assert "foreign.txt" in gitops.status_paths(p.root)
    assert gitops.show_bytes(p.root, "HEAD", "AGENTS.md") == b"# new\r\n"
    msg = _git(p.root, "log", "-1", "--format=%B").stdout.decode()
    assert msg.startswith("declutter(reorg x): subject\n\nDeclutter-Manifest: x sha256=00")


def test_commit_runs_hooks(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    witness = p.root / "hook-ran.txt"
    hook = p.root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f'#!/bin/sh\necho ran > "{witness}"\nexit 0\n', encoding="utf-8")
    hook.chmod(0o755)
    (p.root / "CLAUDE.md").write_text("# b\n", encoding="utf-8")
    gitops.commit_only(p.root, "s", "", ["CLAUDE.md"])
    assert witness.exists()


def test_rejecting_hook_raises_git_error(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    hook = p.root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho {SEED} >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    head = gitops.head_commit(p.root)
    (p.root / "CLAUDE.md").write_text("# b\n", encoding="utf-8")
    with pytest.raises(gitops.GitError) as ei:
        gitops.commit_only(p.root, "s", "b", ["CLAUDE.md"])
    assert ei.value.step == "commit"
    assert ei.value.returncode not in (None, 0)
    assert SEED not in str(ei.value)
    assert gitops.head_commit(p.root) == head


def test_branches_touching_sources(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n", "other.txt": "o\n"}, git=True)
    _git(p.root, "branch", "idle")
    _git(p.root, "switch", "-q", "-c", "feat/touch")
    (p.root / "CLAUDE.md").write_text("# a\nmore\n", encoding="utf-8")
    _commit_all(p.root)
    _git(p.root, "switch", "-q", "-c", "elsewhere", "main")
    (p.root / "other.txt").write_text("o2\n", encoding="utf-8")
    _commit_all(p.root)
    _git(p.root, "switch", "-q", "main")
    # main itself moves on: the three-dot diff only sees what the branch changed.
    (p.root / "CLAUDE.md").write_text("# a\nmain moved\n", encoding="utf-8")
    _commit_all(p.root)
    assert gitops.branches_touching(p.root, ["CLAUDE.md", "AGENTS.md"]) == ("feat/touch",)
    assert gitops.branches_touching(p.root, ["other.txt"]) == ("elsewhere",)
    assert gitops.branches_touching(p.root, []) == ()


def test_branches_touching_respects_limit(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    for name in ("b1", "b2", "b3"):
        _git(p.root, "switch", "-q", "-c", name, "main")
        (p.root / "CLAUDE.md").write_text(f"# {name}\n", encoding="utf-8")
        _commit_all(p.root)
    _git(p.root, "switch", "-q", "main")
    assert gitops.branches_touching(p.root, ["CLAUDE.md"]) == ("b1", "b2", "b3")
    assert gitops.branches_touching(p.root, ["CLAUDE.md"], limit=2) == ("b1", "b2")


def test_is_ancestor(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    first = gitops.head_commit(p.root)
    assert first is not None
    (p.root / "CLAUDE.md").write_text("# b\n", encoding="utf-8")
    _commit_all(p.root)
    second = gitops.head_commit(p.root)
    assert second is not None
    assert gitops.is_ancestor(p.root, first)
    assert not gitops.is_ancestor(p.root, second, first)


def test_revert_no_commit_and_abort(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    (p.root / "CLAUDE.md").write_text("# b\n", encoding="utf-8")
    _commit_all(p.root)
    target = gitops.head_commit(p.root)
    assert target is not None
    gitops.revert_no_commit(p.root, target)
    assert (p.root / "CLAUDE.md").read_text(encoding="utf-8") == "# a\n"
    assert gitops.head_commit(p.root) == target  # nothing committed yet
    sha = gitops.commit(p.root, "Revert reorg", "body")
    assert sha != target and gitops.show_bytes(p.root, "HEAD", "CLAUDE.md") == b"# a\n"

    # Conflicting revert: abort restores the tree.
    (p.root / "CLAUDE.md").write_text("# c\n", encoding="utf-8")
    _commit_all(p.root)
    (p.root / "CLAUDE.md").write_text("# d\n", encoding="utf-8")
    _commit_all(p.root)
    head = gitops.head_commit(p.root)
    with pytest.raises(gitops.GitError) as ei:
        gitops.revert_no_commit(p.root, target)
    assert ei.value.step == "revert"
    assert gitops.operation_in_progress(p.root) == "revert"
    gitops.revert_abort(p.root)
    assert gitops.operation_in_progress(p.root) is None
    assert gitops.head_commit(p.root) == head
    assert (p.root / "CLAUDE.md").read_text(encoding="utf-8") == "# d\n"


def test_git_error_has_no_content(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": f"# a\n{SEED}\n"}, git=True)
    with pytest.raises(gitops.GitError) as ei:
        gitops.run(p.root, "show", f"HEAD:CLAUDE.md{SEED}")
    err = ei.value
    assert err.step == "show"
    assert err.returncode not in (None, 0)
    assert SEED not in str(err) and SEED not in repr(err.args)
    # check=False never raises and returns stdout.
    assert gitops.run(p.root, "show", "HEAD:nope", check=False) == b""
    assert SEED.encode() in gitops.run(p.root, "show", "HEAD:CLAUDE.md")


def test_run_outside_repo_raises_and_reads_are_safe(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(gitops.GitError):
        gitops.run(plain, "status")
    assert gitops.head_branch(plain) is None
    assert gitops.head_commit(plain) is None
    assert gitops.operation_in_progress(plain) is None


def test_option_like_refs_are_rejected(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    with pytest.raises(ValueError):
        gitops.tag(p.root, "--force")
    with pytest.raises(ValueError):
        gitops.switch_create(p.root, "-x")
    with pytest.raises(ValueError):
        gitops.ref_exists(p.root, "--all")


def test_run_disables_terminal_prompt(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = make_project(git=True)
    seen: dict[str, object] = {}
    real = subprocess.run

    def spy(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        seen.update(kw)
        seen["cmd"] = cmd
        return real(cmd, **kw)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(gitops.subprocess, "run", spy)
    gitops.run(p.root, "status")
    env = seen["env"]
    assert isinstance(env, dict) and env["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["timeout"] == 30 and seen["capture_output"] is True
    assert "text" not in seen or seen["text"] is False
    assert seen["cmd"] == ["git", "--literal-pathspecs", "-C", str(p.root), "status"]


def test_blob_sha_matches_hashlib(make_project: MakeProject) -> None:
    p = make_project(git=True)
    raw = b"a\x00b\n"
    (p.root / "f.bin").write_bytes(raw)
    _commit_all(p.root)
    blob = gitops.show_bytes(p.root, "HEAD", "f.bin")
    assert blob is not None
    assert hashlib.sha256(blob).digest() == hashlib.sha256(raw).digest()


# --- review fixes: root in a subdirectory, literal pathspecs, env, extra attrs ---------


def test_root_in_subdirectory_resolves_paths_from_root(make_project: MakeProject) -> None:
    p = make_project({"a.md": "top\n", "sub/a.md": "sub\n"}, git=True)
    sub = p.root / "sub"
    assert gitops.is_repo(sub)
    assert gitops.show_bytes(sub, "HEAD", "a.md") == b"sub\n"
    assert gitops.show_bytes(sub, "", "a.md") == b"sub\n"
    assert gitops.blob_matches_disk(sub, "a.md", b"sub\n")
    assert not gitops.blob_matches_disk(sub, "a.md", b"top\n")
    base = gitops.head_commit(sub)
    assert base is not None
    (sub / "a.md").write_text("sub changed\n", encoding="utf-8")
    (p.root / "a.md").write_text("top changed\n", encoding="utf-8")
    (sub / "new.md").write_text("n\n", encoding="utf-8")
    assert gitops.dirty(sub, ["a.md", "new.md"]) == ("a.md", "new.md")
    # A change outside root stays visible, relative to root.
    assert gitops.status_paths(sub) == frozenset({"a.md", "new.md", "../a.md"})
    gitops.add(sub, ["new.md"])
    sha = gitops.commit_only(sub, "s", "", ["a.md", "new.md"])
    assert gitops.diff_names(sub, base, sha) == frozenset({"a.md", "new.md"})
    assert gitops.show_bytes(sub, "HEAD", "a.md") == b"sub changed\n"
    assert gitops.show_bytes(p.root, "HEAD", "a.md") == b"top\n"
    _commit_all(p.root)
    assert gitops.diff_names(sub, sha, "HEAD") == frozenset({"../a.md"})


def test_diff_names_ignores_diff_relative_config(make_project: MakeProject) -> None:
    p = make_project({"a.md": "top\n", "sub/a.md": "sub\n"}, git=True)
    base = gitops.head_commit(p.root)
    assert base is not None
    (p.root / "a.md").write_text("top 2\n", encoding="utf-8")
    _commit_all(p.root)
    _git(p.root, "config", "diff.relative", "true")
    assert gitops.diff_names(p.root / "sub", base, "HEAD") == frozenset({"../a.md"})


def test_operation_in_progress_from_subdirectory(make_project: MakeProject) -> None:
    p = make_project({"sub/a.md": "a\n"}, git=True)
    sub = p.root / "sub"
    assert gitops.operation_in_progress(sub) is None
    head = gitops.head_commit(p.root)
    assert head is not None
    (p.root / ".git" / "CHERRY_PICK_HEAD").write_text(head + "\n", encoding="ascii")
    assert gitops.operation_in_progress(sub) == "cherry-pick"


def test_glob_characters_are_literal_pathspecs(make_project: MakeProject) -> None:
    p = make_project({"a*.md": "star\n", "ab.md": "ab\n", "[x].md": "x\n", "x.md": "x\n"}, git=True)
    (p.root / "a*.md").write_text("star 2\n", encoding="utf-8")
    (p.root / "ab.md").write_text("ab 2\n", encoding="utf-8")
    (p.root / "x.md").write_text("x 2\n", encoding="utf-8")
    assert gitops.dirty(p.root, ["a*.md"]) == ("a*.md",)
    assert gitops.dirty(p.root, ["[x].md"]) == ()
    base = gitops.head_commit(p.root)
    assert base is not None
    sha = gitops.commit_only(p.root, "s", "", ["a*.md"])
    assert gitops.diff_names(p.root, base, sha) == frozenset({"a*.md"})
    assert gitops.status_paths(p.root) == frozenset({"ab.md", "x.md"})
    (p.root / ":(glob)q.md").write_text("q\n", encoding="utf-8")
    gitops.add(p.root, [":(glob)q.md"])
    assert gitops.show_bytes(p.root, "", ":(glob)q.md") == b"q\n"


def test_paths_with_spaces(make_project: MakeProject) -> None:
    p = make_project({"my notes.md": "a\n"}, git=True)
    (p.root / "my notes.md").write_text("b\n", encoding="utf-8")
    (p.root / "new file.md").write_text("n\n", encoding="utf-8")
    assert gitops.dirty(p.root, ["my notes.md", "new file.md"]) == ("my notes.md", "new file.md")
    gitops.add(p.root, ["new file.md"])
    gitops.commit_only(p.root, "s", "", ["my notes.md", "new file.md"])
    assert gitops.show_bytes(p.root, "HEAD", "my notes.md") == b"b\n"
    assert gitops.status_paths(p.root) == frozenset()


def test_paths_non_utf8_round_trip(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    name = b"caf\xe9.md".decode("utf-8", "surrogateescape")
    try:
        (p.root / name).write_bytes(b"latin\n")
    except OSError:
        pytest.skip("file system rejects non-UTF-8 names")
    assert gitops.dirty(p.root, [name]) == (name,)
    gitops.add(p.root, [name])
    gitops.commit_only(p.root, "s", "", [name])
    assert gitops.show_bytes(p.root, "HEAD", name) == b"latin\n"
    assert gitops.status_paths(p.root) == frozenset()


def test_status_paths_reports_both_sides_of_rename(make_project: MakeProject) -> None:
    p = make_project({"old.md": "same content for rename detection\n"}, git=True)
    _git(p.root, "mv", "old.md", "new.md")
    assert gitops.status_paths(p.root) == frozenset({"old.md", "new.md"})
    assert gitops.dirty(p.root, ["old.md"]) == ("old.md",)


def test_commit_only_with_deleted_file(make_project: MakeProject) -> None:
    p = make_project({"gone.md": "g\n", "keep.md": "k\n", "foreign.md": "f\n"}, git=True)
    base = gitops.head_commit(p.root)
    assert base is not None
    (p.root / "gone.md").unlink()
    (p.root / "foreign.md").write_text("f 2\n", encoding="utf-8")
    sha = gitops.commit_only(p.root, "s", "", ["gone.md"])
    assert gitops.diff_names(p.root, base, sha) == frozenset({"gone.md"})
    assert gitops.show_bytes(p.root, "HEAD", "gone.md") is None
    assert gitops.status_paths(p.root) == frozenset({"foreign.md"})


def test_inherited_repo_env_is_ignored(
    make_project: MakeProject, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = make_project({"a.md": "mine\n"}, git=True)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_GLOB_PATHSPECS", "1")
    assert gitops.show_bytes(p.root, "HEAD", "a.md") == b"mine\n"
    assert gitops.head_branch(p.root) == "main"


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        ("*.md ident\n", (("CLAUDE.md", "ident", "set"),)),
        (
            "*.md working-tree-encoding=UTF-16\n",
            (("CLAUDE.md", "working-tree-encoding", "UTF-16"),),
        ),
        ("*.md -ident\n", ()),
    ],
)
def test_normalizing_detects_ident_and_encoding(
    make_project: MakeProject, attrs: str, expected: tuple[tuple[str, str, str], ...]
) -> None:
    p = make_project({"CLAUDE.md": "# a\n"}, git=True)
    (p.root / ".gitattributes").write_text(attrs, encoding="utf-8")  # read from the work tree
    assert gitops.normalizing(p.root, ["CLAUDE.md"]) == expected


# --------------------------------------------------------------------------- lf-safe (05-11)


def _yes(_p: str) -> bool:
    return True


def _no(_p: str) -> bool:
    return False


@pytest.mark.parametrize(
    ("findings", "cr_free", "tolerated"),
    [
        # text=auto|set + eol=lf on CR-free bytes: identity both ways.
        ((("a", "text", "auto"), ("a", "eol", "lf")), _yes, True),
        ((("a", "text", "set"), ("a", "eol", "lf")), _yes, True),
        # same attributes, bytes with \r: refused.
        ((("a", "text", "auto"), ("a", "eol", "lf")), _no, False),
        # eol=crlf, text without eol, eol without text, filter, ident, encoding: refused.
        ((("a", "text", "auto"), ("a", "eol", "crlf")), _yes, False),
        ((("a", "text", "auto"),), _yes, False),
        ((("a", "text", "set"),), _yes, False),
        ((("a", "eol", "lf"),), _yes, False),
        ((("a", "text", "auto"), ("a", "eol", "lf"), ("a", "filter", "x")), _yes, False),
        ((("a", "text", "auto"), ("a", "eol", "lf"), ("a", "ident", "set")), _yes, False),
        (
            (("a", "text", "auto"), ("a", "eol", "lf"), ("a", "working-tree-encoding", "UTF-16")),
            _yes,
            False,
        ),
        ((("a", "filter", "x"),), _yes, False),
        # core.autocrlf=input on CR-free paths: tolerated; true: refused.
        ((("<config>", "core.autocrlf", "input"),), _yes, True),
        ((("<config>", "core.autocrlf", "input"),), _no, False),
        ((("<config>", "core.autocrlf", "true"),), _yes, False),
    ],
)
def test_split_lf_safe_decides_each_finding(
    findings: tuple[tuple[str, str, str], ...],
    cr_free: object,
    tolerated: bool,
) -> None:
    assert callable(cr_free)
    refused, ok = gitops.split_lf_safe(findings, ["a"], cr_free)
    if tolerated:
        assert ok == findings and refused == ()
    else:
        assert refused == findings and ok == ()


def test_split_lf_safe_autocrlf_input_needs_every_path_cr_free() -> None:
    findings = (("<config>", "core.autocrlf", "input"),)
    refused, ok = gitops.split_lf_safe(findings, ["a", "b"], lambda p: p == "a")
    assert refused == findings and ok == ()


def test_split_lf_safe_is_per_path() -> None:
    findings = (
        ("a", "text", "auto"),
        ("a", "eol", "lf"),
        ("b", "text", "auto"),
        ("b", "eol", "lf"),
    )
    refused, ok = gitops.split_lf_safe(findings, ["a", "b"], lambda p: p == "a")
    assert ok == findings[:2] and refused == findings[2:]


def test_split_lf_safe_on_real_check_attr(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# a\n", ".gitattributes": "* text=auto eol=lf\n"}, git=True)
    found = gitops.normalizing(p.root, ["CLAUDE.md"])
    assert found == (("CLAUDE.md", "text", "auto"), ("CLAUDE.md", "eol", "lf"))
    refused, ok = gitops.split_lf_safe(found, ["CLAUDE.md"], _yes)
    assert refused == () and ok == found


def test_lf_safe_warning_format() -> None:
    assert gitops.lf_safe_warning(("a/b.md", "eol", "lf")) == "git_normalizes_lf_safe:a/b.md:eol=lf"
