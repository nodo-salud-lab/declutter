"""Byte-exact git operations for ``declutter reorg``: reads, preconditions and writes.

Spec §3 (gitops), §10.2 (open branches), §11.1 (preconditions and exact bytes), §11.2
steps 4, 8 and 9, §11.5 (revert) and §12.2 (``--staged`` reads the index).

Every call is ``git --literal-pathspecs -C <root> ...`` with captured output in bytes, a
30 s timeout, ``GIT_TERMINAL_PROMPT=0`` and no inherited repository-selecting variables
(``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE``...). Paths given to and returned by this
module are relative to ``root``, which may be a subdirectory of the work tree; a path
outside ``root`` but inside the repository is returned as ``../...``. This module never
passes ``--no-verify`` (hooks always run), never pushes and never touches remotes.
:class:`GitError` carries the git sub-command and its exit code only: never stdout,
stderr, arguments or file content (G6).
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import subprocess
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

TIMEOUT_S = 30
CONFIG_PATH = "<config>"
# Attributes that make git rewrite bytes between the blob and the work tree (§11.1 lists
# text/eol/filter; ident and working-tree-encoding also rewrite bytes, so they block too).
_ATTRS = ("text", "eol", "filter", "ident", "working-tree-encoding")
# Environment variables that would make ``-C root`` act on another repository or index.
_REPO_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
)
# (git-path, operation name), checked in this order.
_IN_PROGRESS: tuple[tuple[str, str], ...] = (
    ("MERGE_HEAD", "merge"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
)
_TRUTHY = frozenset({"true", "yes", "on", "1"})


class GitError(Exception):
    """A git command failed. Carries metadata only; never file content."""

    def __init__(self, step: str, returncode: int | None) -> None:
        self.step = step
        self.returncode = returncode
        detail = "did not run" if returncode is None else f"exit {returncode}"
        super().__init__(f"git {step} failed ({detail})")


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REPO_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _step(args: Sequence[str]) -> str:
    """The git sub-command in ``args`` (skipping ``-c key=value`` and other options)."""
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == "-c":
            skip = True
        elif not arg.startswith("-"):
            return arg
    return "git"


def _exec(
    root: Path, args: Sequence[str], *, stdin: bytes | None = None, literal: bool = True
) -> tuple[int, bytes]:
    """Run git; raise GitError(step, None) if it cannot run or times out.

    ``literal=False`` drops ``--literal-pathspecs`` for the few commands that reject it
    (``check-ignore``); those callers pass paths on ``stdin``, never as pathspecs.
    """
    step = _step(args)
    head = ["git", "--literal-pathspecs"] if literal else ["git"]
    try:
        proc = subprocess.run(
            [*head, "-C", str(root), *args],
            input=stdin,
            capture_output=True,
            timeout=TIMEOUT_S,
            env=_env(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise GitError(step, None) from None
    return proc.returncode, proc.stdout


def run(root: Path, *args: str, check: bool = True) -> bytes:
    """Run ``git -C root <args>`` and return stdout as bytes.

    With ``check`` a non-zero exit raises :class:`GitError`; without it, stdout is
    returned whatever the exit code. Failing to start git, or a timeout, always raises.
    """
    rc, out = _exec(root, args)
    if check and rc != 0:
        raise GitError(_step(args), rc)
    return out


def _try(root: Path, *args: str) -> tuple[int, bytes] | None:
    """Like ``_exec`` but None when git cannot run (for best-effort reads)."""
    try:
        return _exec(root, args)
    except GitError:
        return None


def _arg(value: str) -> str:
    """Reject empty or option-like refs, revisions and names."""
    if not value or value.startswith("-"):
        raise ValueError("invalid git ref or name")
    return value


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _nul_fields(raw: bytes) -> list[str]:
    return [_decode(f) for f in raw.split(b"\0")[:-1]] if raw else []


def _prefix(root: Path) -> str:
    """Path of ``root`` below the top of the work tree (``"sub/"``), ``""`` at the top."""
    return _decode(run(root, "rev-parse", "--show-prefix").rstrip(b"\n"))


def _from_top(prefix: str, path: str) -> str:
    """Turn a path relative to the work-tree top into one relative to ``root``."""
    if not prefix:
        return path
    if path.startswith(prefix):
        return path[len(prefix) :]
    return posixpath.relpath(path, prefix.rstrip("/"))


# --- reads ---------------------------------------------------------------------------


def is_repo(root: Path) -> bool:
    """True if ``root`` is inside a git work tree."""
    res = _try(root, "rev-parse", "--is-inside-work-tree")
    return res is not None and res[0] == 0 and res[1].strip() == b"true"


def head_branch(root: Path) -> str | None:
    """Branch HEAD points to (full name below ``refs/heads/``), or None if detached."""
    res = _try(root, "symbolic-ref", "--quiet", "HEAD")
    if res is None or res[0] != 0:
        return None
    ref = _decode(res[1].strip())
    prefix = "refs/heads/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


def head_commit(root: Path) -> str | None:
    """Full sha of HEAD, or None (no repository or no commit yet)."""
    res = _try(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if res is None or res[0] != 0:
        return None
    sha = _decode(res[1].strip())
    return sha or None


def operation_in_progress(root: Path) -> str | None:
    """ "merge", "rebase", "cherry-pick" or "revert" if one is in progress, else None.

    "revert" goes beyond the spec's list (§11.1: merge, rebase, cherry-pick); callers
    treat any non-None value as blocking.
    """
    args: list[str] = []
    for name, _op in _IN_PROGRESS:
        args += ["--git-path", name]
    res = _try(root, "rev-parse", *args)
    if res is None or res[0] != 0:
        return None
    lines = _decode(res[1]).splitlines()
    for line, (_name, op) in zip(lines, _IN_PROGRESS, strict=False):
        if line and (root / line).exists():
            return op
    return None


def ref_exists(root: Path, ref: str) -> bool:
    """True if ``ref`` (e.g. ``refs/tags/declutter/pre-reorg-<id>``) resolves."""
    res = _exec(root, ("rev-parse", "--verify", "--quiet", _arg(ref)))
    return res[0] == 0


def _porcelain(root: Path, paths: Sequence[str] | None) -> frozenset[str]:
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
    if paths is not None:
        args += ["--", *paths]
    fields = _nul_fields(run(root, *args))
    # Porcelain paths are relative to the top of the work tree, not to ``root``.
    prefix = _prefix(root) if fields else ""
    found: set[str] = set()
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        found.add(_from_top(prefix, path))
        if "R" in xy or "C" in xy:  # rename/copy: the next field is the original path
            if i < len(fields):
                found.add(_from_top(prefix, fields[i]))
            i += 1
    return frozenset(found)


def dirty(root: Path, paths: Sequence[str]) -> tuple[str, ...]:
    """Paths among ``paths`` with changes or untracked (``status --porcelain -- paths``)."""
    if not paths:
        return ()
    return tuple(sorted(_porcelain(root, paths)))


def status_paths(root: Path) -> frozenset[str]:
    """Every path ``git status --porcelain`` reports (changed, staged or untracked)."""
    return _porcelain(root, None)


def normalizing(root: Path, paths: Sequence[str]) -> tuple[tuple[str, str, str], ...]:
    """Attributes and config that make git rewrite bytes (spec §11.1).

    One ``(path, attr, value)`` per path whose ``text`` is ``set``/``auto``, whose
    ``ident`` is ``set``, or whose ``eol``, ``filter`` or ``working-tree-encoding`` is
    specified (``ident`` and ``working-tree-encoding`` go beyond the spec's list, as a
    conservative extension: they rewrite bytes too); plus ``("<config>", "core.autocrlf", v)`` when
    ``core.autocrlf`` is ``true`` or ``input``.
    """
    found: list[tuple[str, str, str]] = []
    if paths:
        fields = _nul_fields(run(root, "check-attr", "-z", *_ATTRS, "--", *paths))
        for k in range(0, len(fields) - 2, 3):
            path, attr, value = fields[k], fields[k + 1], fields[k + 2]
            if (
                (attr == "text" and value in ("set", "auto"))
                or (attr == "ident" and value == "set")
                or (attr in ("eol", "filter", "working-tree-encoding") and value != "unspecified")
            ):
                found.append((path, attr, value))
    raw = _decode(run(root, "config", "--get", "core.autocrlf", check=False)).strip().lower()
    if raw == "input":
        found.append((CONFIG_PATH, "core.autocrlf", "input"))
    elif raw in _TRUTHY:
        found.append((CONFIG_PATH, "core.autocrlf", "true"))
    return tuple(found)


Finding = tuple[str, str, str]
LF_SAFE_KEY = "git_normalizes_lf_safe"


def _lf_safe_shape(attrs: dict[str, str]) -> bool:
    """True when a path's normalizing attributes are the identity on CR-free bytes.

    Only ``text`` and ``eol`` may appear; then either ``text`` is ``set``/``auto`` with
    ``eol=lf`` (clean turns CRLF into LF, smudge writes LF), or neither appears.
    ``text`` without ``eol`` depends on ``core.eol`` and the platform, so it does not pass.
    """
    if set(attrs) - {"text", "eol"}:
        return False
    text, eol = attrs.get("text"), attrs.get("eol")
    if text is None and eol is None:
        return True
    return text in ("set", "auto") and eol == "lf"


def split_lf_safe(
    findings: Sequence[Finding],
    paths: Iterable[str],
    cr_free: Callable[[str], bool],
) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    """Split :func:`normalizing` findings into ``(refused, tolerated)`` (Enmienda 05-11).

    A path's findings are tolerated only if its attributes pass :func:`_lf_safe_shape`
    and ``cr_free(path)`` (its relevant bytes hold no ``\\r``). ``core.autocrlf=input``
    is tolerated only if ``cr_free`` holds for every path in ``paths``; ``true`` never
    is. Pure: ``cr_free`` is the only input besides the findings.
    """
    involved = tuple(paths)
    by_path: dict[str, dict[str, str]] = {}
    for path, attr, value in findings:
        if path != CONFIG_PATH:
            by_path.setdefault(path, {})[attr] = value
    ok_path = {p: _lf_safe_shape(a) and cr_free(p) for p, a in by_path.items()}
    refused: list[Finding] = []
    tolerated: list[Finding] = []
    for f in findings:
        path, attr, value = f
        if path == CONFIG_PATH:
            ok = attr == "core.autocrlf" and value == "input" and all(map(cr_free, involved))
        else:
            ok = ok_path[path]
        (tolerated if ok else refused).append(f)
    return tuple(refused), tuple(tolerated)


def lf_safe_warning(f: Finding) -> str:
    """``git_normalizes_lf_safe:<path>:<attr>=<value>``: no content, only the setting."""
    return f"{LF_SAFE_KEY}:{f[0]}:{f[1]}={f[2]}"


def show_bytes(root: Path, rev: str, path: str) -> bytes | None:
    """Exact blob bytes of ``<rev>:<path>``; ``rev=""`` reads the index. None if absent.

    Uses ``git cat-file blob`` (no textconv, no filters) so the bytes are the stored ones.
    ``path`` is relative to ``root``: the ``./`` prefix makes git resolve it from ``-C
    root`` rather than from the top of the work tree.
    """
    if rev:
        _arg(rev)
    rc, out = _exec(root, ("cat-file", "blob", f"{rev}:./{path}"))
    return out if rc == 0 else None


def blob_matches_disk(root: Path, path: str, disk: bytes) -> bool:
    """``sha256(HEAD:path) == sha256(disk)``; False if the path is not in HEAD."""
    blob = show_bytes(root, "HEAD", path)
    if blob is None:
        return False
    return hashlib.sha256(blob).digest() == hashlib.sha256(disk).digest()


def diff_names(root: Path, a: str, b: str) -> frozenset[str]:
    """Paths that differ between revisions ``a`` and ``b`` (renames split in two).

    The whole repository is compared (``--no-relative``); paths are made relative to
    ``root``, so a change outside ``root`` shows up as ``../...`` instead of being hidden.
    """
    out = run(
        root,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        "--no-relative",
        _arg(a),
        _arg(b),
    )
    fields = _nul_fields(out)
    prefix = _prefix(root) if fields else ""
    return frozenset(_from_top(prefix, f) for f in fields)


def rev_commit(root: Path, rev: str) -> str | None:
    """Full sha of the commit ``rev`` points to (a tag is peeled), or None."""
    res = _try(root, "rev-parse", "--verify", "--quiet", f"{_arg(rev)}^{{commit}}")
    if res is None or res[0] != 0:
        return None
    return _decode(res[1].strip()) or None


def _check_ignore(root: Path, paths: Sequence[str]) -> list[tuple[str, str, str, str]]:
    """``(ignore file, line, pattern, path)`` of every non-negated match of ``paths``.

    The ignore file is relative to ``root``. Tracked paths never match.
    """
    if not paths:
        return []
    stdin = b"".join(p.encode("utf-8", "surrogateescape") + b"\0" for p in paths)
    rc, out = _exec(root, ("check-ignore", "-v", "-z", "--stdin"), stdin=stdin, literal=False)
    if rc not in (0, 1):
        raise GitError("check-ignore", rc)
    fields = _nul_fields(out)
    prefix = _prefix(root) if fields else ""
    found: list[tuple[str, str, str, str]] = []
    for k in range(0, len(fields) - 3, 4):
        source, line, pattern, path = fields[k : k + 4]
        if source and not pattern.startswith("!"):
            found.append((_from_top(prefix, source), line, pattern, path))
    return found


def ignored(root: Path, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """``(path, "<ignore file>:<line>")`` for each of ``paths`` that git would refuse to
    ``git add`` because an ignore rule excludes it or one of its folders.

    Tracked paths are never reported (``git add`` accepts them), nor paths matched only by
    a negated pattern. The ignore file is relative to ``root``; the pattern itself is
    never returned (G6).
    """
    return tuple((path, f"{src}:{line}") for src, line, _pat, path in _check_ignore(root, paths))


def ignored_dir(root: Path, rel: str) -> str | None:
    """``"<ignore file>:<line>"`` if the folder ``rel`` itself is excluded, so that no rule
    inside it (such as a negation in ``rel/.gitignore``) can include a file again.

    The folder may not exist yet: ``rel/`` is also asked about, keeping only patterns
    that name a folder (ending in ``/``); a pattern such as ``rel/*`` matches ``rel/``
    with an empty ``*`` but leaves the folder itself included.
    """
    rel = rel.rstrip("/")
    for src, line, pattern, path in _check_ignore(root, [rel, rel + "/"]):
        if path.rstrip("/") != rel:
            continue
        if not path.endswith("/") or pattern.endswith("/"):
            return f"{src}:{line}"
    return None


def is_ancestor(root: Path, sha: str, of: str = "HEAD") -> bool:
    """True if ``sha`` is an ancestor of (or equal to) ``of``."""
    rc, _out = _exec(root, ("merge-base", "--is-ancestor", _arg(sha), _arg(of)))
    if rc in (0, 1):
        return rc == 0
    raise GitError("merge-base", rc)


def branches_touching(root: Path, paths: Sequence[str], limit: int = 200) -> tuple[str, ...]:
    """Local branches (at most ``limit``, by name) whose changes since the merge base
    with HEAD touch ``paths`` (``git diff --quiet HEAD...<b> -- paths`` exits 1).

    Informational only (spec §10.2): a branch git cannot compare (for example, with no
    merge base) is skipped.
    """
    if not paths or limit <= 0 or head_commit(root) is None:
        return ()
    refs = _decode(
        run(
            root,
            "for-each-ref",
            "--sort=refname",
            f"--count={limit}",
            "--format=%(refname)",
            "refs/heads",
        )
    ).splitlines()
    prefix = "refs/heads/"
    touching: list[str] = []
    for ref in refs:
        if not ref.startswith(prefix):
            continue
        rc, _out = _exec(root, ("diff", "--quiet", "--no-ext-diff", f"HEAD...{ref}", "--", *paths))
        if rc == 1:
            touching.append(ref[len(prefix) :])
    return tuple(touching)


# --- writes --------------------------------------------------------------------------


def tag(root: Path, name: str) -> None:
    """Create the lightweight tag ``name`` on HEAD; fails if it already exists."""
    run(root, "-c", "tag.gpgSign=false", "tag", "--", _arg(name))


def switch_create(root: Path, branch: str) -> None:
    """``git switch -c <branch>`` from HEAD."""
    run(root, "switch", "--quiet", "-c", _arg(branch))


def switch(root: Path, branch: str) -> None:
    """``git switch <branch>`` (local branches only: never guesses a remote one)."""
    run(root, "switch", "--quiet", "--no-guess", _arg(branch))


def add(root: Path, paths: Sequence[str]) -> None:
    """``git add -- <paths>``; nothing to do for an empty list."""
    if paths:
        run(root, "add", "--", *paths)


def unstage(root: Path, paths: Sequence[str]) -> None:
    """``git reset -q -- <paths>``: the index entries of ``paths`` go back to HEAD (a path
    absent from HEAD leaves the index); the work tree is not touched."""
    if paths:
        run(root, "reset", "-q", "--", *paths)


def _message(subject: str, body: str) -> list[str]:
    msg = ["-m", subject]
    if body:
        msg += ["-m", body]
    return msg


def _new_head(root: Path) -> str:
    sha = head_commit(root)
    if sha is None:
        raise GitError("rev-parse", None)
    return sha


def commit_only(root: Path, subject: str, body: str, paths: Sequence[str]) -> str:
    """``git commit --only -m <subject> -m <body> -- <paths>``; returns the new HEAD sha.

    ``--only`` commits exactly ``paths`` (new files must be added first), leaving any
    foreign change, staged or not, out of the commit. Hooks run.
    """
    if not paths:
        raise ValueError("commit_only needs at least one path")
    run(root, "commit", "--quiet", "--only", *_message(subject, body), "--", *paths)
    return _new_head(root)


def commit(root: Path, subject: str, body: str = "") -> str:
    """``git commit`` of the index (used after a revert); returns the new HEAD sha."""
    run(root, "commit", "--quiet", *_message(subject, body))
    return _new_head(root)


def revert_no_commit(root: Path, sha: str) -> None:
    """``git revert --no-commit <sha>``; a conflict raises GitError("revert", rc)."""
    run(root, "revert", "--no-commit", _arg(sha))


def revert_abort(root: Path) -> None:
    """``git revert --abort``."""
    run(root, "revert", "--abort")
