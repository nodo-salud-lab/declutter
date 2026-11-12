"""Startup load sets: what Claude Code and Codex read at session start (spec section 6)."""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

from declutter_context import fsutil, gitutil
from declutter_context.model import Config, LoadedFile, LoadSet, Project

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_IMPORT_RE = re.compile(r"(?:^|(?<=[\s(\[\"']))@((?:~/|\.{1,2}/|/)\S*|\w\S*)")
_TRAILING_PUNCT = ".,;:!?)]}\"'"
_PATHS_KEY_RE = re.compile(r"^\s*paths\s*:", re.IGNORECASE)


# A load-set reader: the text of ``path``, or None when it does not exist or cannot be read
# safely. Injected to measure ``stage/`` or an in-memory map instead of the disk (spec §3).
Reader = Callable[[Path], str | None]

_SLUG_MAX = 200  # Claude Code shortens longer slugs and appends a hash suffix


def claude_project_slug(cwd: Path | str) -> str:
    """Folder name Claude Code uses under ``~/.claude/projects`` for a session cwd.

    Every character that is not an ASCII letter or digit becomes ``-``. Characters outside
    the Basic Multilingual Plane count twice, as in JavaScript's UTF-16 strings.
    """
    out: list[str] = []
    for ch in str(cwd):
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append("--" if ord(ch) > 0xFFFF else "-")
    return "".join(out)


def _session_cwd(project: Project) -> Path:
    return project.session_cwd if project.session_cwd is not None else project.cwd


def claude_project_dir(project: Project) -> Path:
    """Claude Code's per-project folder (memory and transcripts) for the session cwd.

    Tries the NFC, NFD and raw spellings of the cwd; for slugs longer than Claude's limit
    it also accepts a folder named ``<first 200 chars>-<hash>``. When nothing exists, the
    NFC candidate is returned.
    """
    base = project.claude_home / "projects"
    raw = str(_session_cwd(project))
    slugs: list[str] = []
    for variant in (unicodedata.normalize("NFC", raw), unicodedata.normalize("NFD", raw), raw):
        slug = claude_project_slug(variant)
        if slug not in slugs:
            slugs.append(slug)
    for slug in slugs:
        if fsutil.is_dir(base / slug):
            return base / slug
    for slug in slugs:
        if len(slug) > _SLUG_MAX:
            prefix = slug[:_SLUG_MAX] + "-"
            try:
                matches = sorted(
                    e.path for e in os.scandir(base) if e.name.startswith(prefix) and e.is_dir()
                )
            except OSError:
                matches = []
            if matches:
                return Path(matches[0])
    return base / slugs[0]


def memory_md_path(project: Project) -> Path:
    """Path of the auto-memory index for the project's (session) cwd."""
    return claude_project_dir(project) / "memory" / "MEMORY.md"


def repo_root(project: Project) -> Path:
    """Git top-level of the project's cwd, or ``project.root`` when not in a repo."""
    out = gitutil.run_git(project.cwd, "rev-parse", "--show-toplevel")
    if out and out.strip():
        return Path(out.strip()).resolve()
    return project.root


def _lexically_within(path: Path, base: Path) -> bool:
    try:
        Path(os.path.normpath(path)).relative_to(base)
    except ValueError:
        return False
    return True


def allowed_roots(path: Path, project: Project, *extra: Path) -> tuple[Path, ...]:
    """Where a load-set file may resolve to, given where it lexically lives.

    Files inside the project (or inside an ``extra`` root such as the repository top)
    must stay inside it, so a project symlink can never reach the rest of ``$HOME``.
    Files under ``~/.claude`` or ``$HOME`` (user CLAUDE.md, ancestors, memory, ``~/``
    imports) may resolve anywhere under ``$HOME``.
    """
    for base in (project.root, *extra):
        if _lexically_within(path, base):
            return (base,)
    if _lexically_within(path, project.claude_home) or _lexically_within(path, project.home):
        return (project.home, project.claude_home)
    return (project.root, project.home, project.claude_home)


def _read(path: Path, project: Project) -> str | None:
    """Read an existing regular file through the safe reader, else None."""
    if not fsutil.is_file(path):
        return None
    return fsutil.safe_read_text(path, *allowed_roots(path, project))


def _loaded(
    path: Path, text: str, kind: str, *, truncated: bool = False, on_disk: bool = True
) -> LoadedFile:
    data = text.encode("utf-8")
    if on_disk and not truncated and len(data) >= fsutil.MAX_READ_BYTES:
        # The reader stopped at its cap: take the true size by streaming the file.
        stats = fsutil.safe_text_stats(path)
        if stats is not None:
            return LoadedFile(path=path, kind=kind, bytes=stats.bytes, lines=stats.lines)
    return LoadedFile(
        path=path,
        kind=kind,
        bytes=len(data),
        lines=len(text.splitlines()),
        truncated=truncated,
    )


def _has_paths_frontmatter(text: str) -> bool:
    """True if the leading ``---`` frontmatter block contains a ``paths:`` key."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for ln in lines[1:]:
        if ln.strip() == "---":
            return False
        if _PATHS_KEY_RE.match(ln):
            return True
    return False


def _extract_imports(text: str) -> list[str]:
    """Raw ``@path`` tokens outside fenced code blocks and inline code spans."""
    found: list[str] = []
    fence: str | None = None
    for ln in text.splitlines():
        m = _FENCE_RE.match(ln)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence is not None:
            continue
        for tok in _IMPORT_RE.finditer(_INLINE_CODE_RE.sub("", ln)):
            raw = tok.group(1).rstrip(_TRAILING_PUNCT)
            if raw:
                found.append(raw)
    return found


def extract_imports(text: str) -> list[str]:
    """Public form of the ``@import`` scanner (fences and inline code are ignored)."""
    return _extract_imports(text)


def _resolve_import(raw: str, base_dir: Path, home: Path) -> Path:
    if raw.startswith("~/"):
        return home / raw[2:]
    if Path(raw).is_absolute():
        return Path(raw)
    return Path(os.path.normpath(base_dir / raw))


def _key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _truncate_memory(text: str, cfg: Config) -> tuple[str, bool]:
    """Keep the first ``memory_max_lines`` lines or ``memory_max_bytes`` bytes."""
    cut = False
    lines = text.splitlines(keepends=True)
    if len(lines) > cfg.memory_max_lines:
        lines = lines[: cfg.memory_max_lines]
        cut = True
    out = "".join(lines)
    data = out.encode("utf-8")
    if len(data) > cfg.memory_max_bytes:
        out = data[: cfg.memory_max_bytes].decode("utf-8", errors="ignore")
        cut = True
    return out, cut


def ancestor_dirs(project: Project) -> list[Path]:
    """cwd and its parents, stopping after ``home`` when cwd lives under it."""
    cwd = project.cwd
    under_home = cwd == project.home or project.home in cwd.parents
    dirs: list[Path] = []
    for d in (cwd, *cwd.parents):
        dirs.append(d)
        if under_home and d == project.home:
            break
    return dirs


def claude_loadset(project: Project, cfg: Config, *, reader: Reader | None = None) -> LoadSet:
    """Files Claude Code loads at startup, including truncated memory and @imports.

    With ``reader``, every file (CLAUDE.md files, rules, memory, imports) is read through it
    instead of the disk; folders of rules are still listed from the disk.
    """
    on_disk = reader is None

    def read(path: Path) -> str | None:
        return _read(path, project) if reader is None else reader(path)

    files: list[LoadedFile] = []
    texts: list[tuple[Path, str]] = []  # markdown files whose imports are followed
    seen: set[Path] = set()
    requested = 0

    def add(path: Path, kind: str, follow: bool = True) -> str | None:
        nonlocal requested
        key = _key(path)
        if key in seen:
            return None
        text = read(path)
        if text is None:
            return None
        seen.add(key)
        loaded = _loaded(path, text, kind, on_disk=on_disk)
        requested += loaded.bytes
        files.append(loaded)
        if follow:
            texts.append((path, text))
        return text

    add(project.claude_home / "CLAUDE.md", "user_claude_md")
    for d in ancestor_dirs(project):
        add(d / "CLAUDE.md", "claude_md")
        add(d / "CLAUDE.local.md", "claude_local_md")

    rule_dirs = (project.root / ".claude" / "rules", project.claude_home / "rules")
    for rd in rule_dirs:
        if not fsutil.is_dir(rd):
            continue
        for p in sorted(fsutil.iter_files(rd, "*.md")):
            text = read(p)
            if text is None or _has_paths_frontmatter(text):
                continue
            add(p, "rule")

    mem = memory_md_path(project)
    mem_text = read(mem)
    if mem_text is not None:
        kept, cut = _truncate_memory(mem_text, cfg)
        mem_bytes = len(mem_text.encode("utf-8"))
        requested += max(mem_bytes, fsutil.file_size(mem)) if on_disk else mem_bytes
        files.append(_loaded(mem, kept, "memory", truncated=cut, on_disk=on_disk))

    # Breadth-first import expansion; ``depth`` counts hops from a non-import file.
    queue: list[tuple[Path, str, int]] = [(p, t, 0) for p, t in texts]
    i = 0
    while i < len(queue):
        parent, text, depth = queue[i]
        i += 1
        if depth >= cfg.import_max_depth:
            continue
        for raw in _extract_imports(text):
            target = _resolve_import(raw, parent.parent, project.home)
            body = add(target, "import")
            if body is not None:
                queue.append((target, body, depth + 1))

    return LoadSet(agent="claude", files=tuple(files), requested_bytes=requested)


def codex_chain(top: Path, cwd: Path) -> list[Path]:
    """Folders Codex reads AGENTS.md from: ``top`` down to ``cwd`` (just ``cwd`` if outside)."""
    chain = [top]
    if cwd != top and top in cwd.parents:
        for part in cwd.relative_to(top).parts:
            chain.append(chain[-1] / part)
    elif cwd != top:
        chain = [cwd]
    return chain


def codex_loadset(
    project: Project, cfg: Config, *, reader: Reader | None = None, top: Path | None = None
) -> LoadSet:
    """Codex project docs from the repo root down to cwd, sharing one byte budget.

    With ``reader``, each ``AGENTS.override.md``/``AGENTS.md`` is read through it instead of
    the disk. ``top`` (when given) is used as the repository root without asking git.
    """
    if top is None:
        top = repo_root(project)
    chain = codex_chain(top, project.cwd)

    files: list[LoadedFile] = []
    requested = 0
    remaining = cfg.codex_budget_bytes
    for d in chain:
        for name in ("AGENTS.override.md", "AGENTS.md"):
            p = d / name
            roots: tuple[Path, ...] = ()
            if reader is not None:
                text = reader(p)
            else:
                roots = allowed_roots(p, project, top)
                text = fsutil.safe_read_text(p, *roots) if fsutil.is_file(p) else None
            if text is None or not text.strip():
                continue
            data = text.encode("utf-8")
            if reader is None and len(data) >= fsutil.MAX_READ_BYTES:
                stats = fsutil.safe_text_stats(p, *roots)
                requested += stats.bytes if stats is not None else len(data)
            else:
                requested += len(data)
            if remaining > 0:
                if len(data) > remaining:
                    kept = data[:remaining].decode("utf-8", errors="ignore")
                    files.append(
                        _loaded(p, kept, "agents_md", truncated=True, on_disk=reader is None)
                    )
                    remaining = 0
                else:
                    files.append(_loaded(p, text, "agents_md", on_disk=reader is None))
                    remaining -= len(data)
            break
    return LoadSet(agent="codex", files=tuple(files), requested_bytes=requested)
