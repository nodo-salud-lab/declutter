"""In-memory projection of a reorg plan (spec §10.2.3, §13, §5.6.4, §11.3, §5.4 rule 3b).

The "after" side never reads the built files from disk: every file the plan would write
comes from a map ``POSIX path -> bytes`` laid over the disk (:func:`overlay_reader`). The
formulas and thresholds are those of phase 1 (S1-S4 in ``signals_files``, the load sets
in ``loadset``). Output carries metadata only (paths, sizes, percentages), never content.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from declutter_context import loadset, signals_files
from declutter_context.model import Config, Decision, LoadSet, Project, Unit
from declutter_context.reorg import EXIT_OK, EXIT_THRESHOLD, Issue, mdblocks

ROOT_FILES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")
_SIGNALS: tuple[str, ...] = ("S1", "S2", "S3", "S4")

# ``](target)`` or ``](<target>)``; the title after a space is ignored.
_LINK_RE = re.compile(r"\]\(\s*(?:<([^>\n]*)>|([^)\s]*))")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_TEXT_KINDS = frozenset({"text", "h2", "blank"})

# Hops from a startup CLAUDE.md to an ``@path`` written in a unit, by destination: L1 is
# CLAUDE.md -> @AGENTS.md -> @path; L1c is CLAUDE.md -> @path; a zone has the same shape as
# L1 (<dir>/CLAUDE.md -> @AGENTS.md). Other destinations are not loaded at startup.
_IMPORT_HOPS: dict[str, int] = {"L1": 2, "L1c": 1}
_ZONE_HOPS = 2
_STARTUP_DESTS = frozenset({"L1", "L1c"})


@dataclass(frozen=True)
class CodexChain:
    """One Codex load chain: ``root`` (the root folder) or ``<dir>`` (root -> zone)."""

    name: str
    files: tuple[str, ...]  # POSIX paths, relative to the root, in load order
    bytes: int  # requested bytes of the chain (before Codex truncates)
    pct: float  # 100 * bytes / codex_budget_bytes


@dataclass(frozen=True)
class Projection:
    """S1-S4 before and after, Codex chains and the Claude load-set diff."""

    files: tuple[tuple[str, int, int], ...]  # (path, bytes, lines) of the instruction files
    before: Mapping[str, float | None]
    after: Mapping[str, float | None]
    codex: tuple[CodexChain, ...]
    loadset_added: tuple[str, ...]
    loadset_removed: tuple[str, ...]
    loadset_before: tuple[str, ...] = field(default=())  # added in T08 (rendering, tests)
    loadset_after: tuple[str, ...] = field(default=())

    @property
    def codex_max_pct(self) -> float:
        return max((c.pct for c in self.codex), default=0.0)

    def to_json(self) -> dict[str, object]:
        return {
            "files": [{"path": p, "bytes": b, "lines": n} for p, b, n in self.files],
            "before": {k: self.before.get(k) for k in _SIGNALS},
            "after": {k: self.after.get(k) for k in _SIGNALS},
            "codex": [
                {"name": c.name, "files": list(c.files), "bytes": c.bytes, "pct": c.pct}
                for c in self.codex
            ],
            "codex_max_pct": self.codex_max_pct,
            "loadset": {
                "added": list(self.loadset_added),
                "removed": list(self.loadset_removed),
                "before": list(self.loadset_before),
                "after": list(self.loadset_after),
            },
        }


# --------------------------------------------------------------------------- overlay


def _rel_in_root(path: Path, root: Path) -> str | None:
    """POSIX path of ``path`` relative to ``root`` (lexically normalized), or None."""
    try:
        rel = Path(os.path.normpath(path)).relative_to(root)
    except ValueError:
        return None
    return rel.as_posix()


def overlay_reader(project: Project, files: Mapping[str, bytes]) -> loadset.Reader:
    """A load-set reader: paths in ``files`` from the map, the rest from the disk (phase 1).

    Map keys are POSIX paths relative to ``project.root``. Map bytes are decoded as UTF-8
    (undecodable bytes replaced, like the phase-1 disk reader).
    """

    def read(path: Path) -> str | None:
        rel = _rel_in_root(path, project.root)
        if rel is not None and rel in files:
            return files[rel].decode("utf-8", errors="replace")
        return loadset._read(path, project)

    return read


# --------------------------------------------------------------------------- signals


def _label(path: Path, project: Project) -> str:
    rel = _rel_in_root(path, project.root)
    if rel is not None:
        return rel
    try:
        return "~/" + Path(os.path.normpath(path)).relative_to(project.home).as_posix()
    except ValueError:
        return str(path)


def _labels(ls: LoadSet, project: Project) -> tuple[str, ...]:
    return tuple(sorted({_label(f.path, project) for f in ls.files}))


def _s1(texts: Mapping[str, str | None]) -> float | None:
    if all(t is None for t in texts.values()):
        return None
    return float(sum(len(t.splitlines()) for t in texts.values() if t is not None))


def _s4(texts: Mapping[str, str | None]) -> float | None:
    headings: list[str] = []
    for name in ROOT_FILES[::-1]:  # phase 1 reads CLAUDE.md first, then AGENTS.md
        text = texts[name]
        if text is not None:
            headings.extend(mdblocks.level2_headings(text))
    if len(headings) < signals_files._MIN_HEADINGS:
        return None
    dated = sum(1 for h in headings if signals_files._CHANGELOG_RE.search(h))
    return 100 * dated / len(headings)


def _before(project: Project, cfg: Config) -> dict[str, float | None]:
    """S1-S4 of the current disk, exactly as phase 1 measures them."""
    return {
        "S1": signals_files.s1_instruction_lines(project, cfg).value,
        "S2": signals_files.s2_startup_tokens(project, cfg).value,
        "S3": signals_files.s3_codex_budget(project, cfg).value,
        "S4": signals_files.s4_changelog_ratio(project, cfg).value,
    }


def _chain(project: Project, cfg: Config, read: loadset.Reader, name: str, cwd: Path) -> CodexChain:
    ls = loadset.codex_loadset(replace(project, cwd=cwd), cfg, reader=read, top=project.root)
    return CodexChain(
        name=name,
        files=tuple(_label(f.path, project) for f in ls.files),
        bytes=ls.requested_bytes,
        pct=100 * ls.requested_bytes / cfg.codex_budget_bytes,
    )


def project_after(
    project: Project, cfg: Config, files: Mapping[str, bytes], zones: Sequence[str]
) -> Projection:
    """Project S1-S4, the Codex chains and the Claude load set over ``files`` (spec §10.2.3).

    ``zones`` are top-level folder names; a zone gets its own chain (root -> zone) when its
    ``AGENTS.md`` is in the map or on disk. The root chain is always reported.
    """
    read = overlay_reader(project, files)
    root = project.root
    texts = {name: read(root / name) for name in ROOT_FILES}

    zone_names = sorted({z.strip("/") for z in zones if z.strip("/")})
    zone_files = [
        z for z in zone_names if f"{z}/AGENTS.md" in files or read(root / z / "AGENTS.md")
    ]

    sizes: list[tuple[str, int, int]] = []
    for rel in (*ROOT_FILES, *(f"{z}/AGENTS.md" for z in zone_files)):
        text = texts[rel] if rel in texts else read(root / rel)
        if text is None:
            continue
        size = len(files[rel]) if rel in files else len(text.encode("utf-8"))
        sizes.append((rel, size, len(text.splitlines())))

    chains = [_chain(project, cfg, read, "root", root)]
    chains += [_chain(project, cfg, read, z, root / z) for z in zone_files]

    ls_before = loadset.claude_loadset(project, cfg)
    ls_after = loadset.claude_loadset(project, cfg, reader=read)
    before_set = _labels(ls_before, project)
    after_set = _labels(ls_after, project)

    root_chain = chains[0]
    after: dict[str, float | None] = {
        "S1": _s1(texts),
        "S2": float(math.ceil(ls_after.total_bytes / cfg.bytes_per_token)),
        "S3": root_chain.pct if root_chain.files else None,
        "S4": _s4(texts),
    }
    return Projection(
        files=tuple(sizes),
        before=_before(project, cfg),
        after=after,
        codex=tuple(chains),
        loadset_added=tuple(p for p in after_set if p not in before_set),
        loadset_removed=tuple(p for p in before_set if p not in after_set),
        loadset_before=before_set,
        loadset_after=after_set,
    )


def codex_warnings(p: Projection, cfg: Config) -> tuple[Issue, ...]:
    """Per chain: over ``codex_warn_pct`` -> ``codex_warn`` (code 0, a warning); over 100 % ->
    ``Issue(3, "codex_budget")`` (spec §5.6.4). Both thresholds are strict ("supera")."""
    out: list[Issue] = []
    for c in p.codex:
        if c.pct > 100:
            code, key = EXIT_THRESHOLD, "codex_budget"
        elif c.pct > cfg.reorg.codex_warn_pct:
            code, key = EXIT_OK, "codex_warn"
        else:
            continue
        out.append(
            Issue(
                code=code,
                key=key,
                dest="AGENTS.md" if c.name == "root" else f"{c.name}/AGENTS.md",
                detail=(
                    f"chain={c.name} bytes={c.bytes} budget={cfg.codex_budget_bytes} "
                    f"pct={c.pct:.1f}"
                ),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- links


def _link_targets(unit_lines: Sequence[bytes]) -> list[str]:
    """Relative Markdown link targets outside fences, comments, front matter and code spans."""
    texts = [mdblocks.decode_line(line) for line in unit_lines]
    kinds = mdblocks.scan(texts, strict=False)
    out: list[str] = []
    for text, kind in zip(texts, kinds, strict=True):
        if kind not in _TEXT_KINDS:
            continue
        for m in _LINK_RE.finditer(_INLINE_CODE_RE.sub("", text)):
            target = m.group(1) if m.group(1) is not None else m.group(2)
            target = re.split(r"[#?]", target, maxsplit=1)[0].strip()
            if not target or target.startswith("/") or _SCHEME_RE.match(target):
                continue
            out.append(target)
    return out


def relative_links_moved(
    unit_lines: Sequence[bytes], src_file: str, dest_file: str, root: Path
) -> int:
    """Relative ``](path)`` links whose resolved target changes when the literal moves from
    ``src_file`` to ``dest_file`` (spec §11.3). ``@path`` imports are not counted here."""
    src_dir = root / Path(src_file).parent
    dest_dir = root / Path(dest_file).parent
    return sum(
        1
        for t in _link_targets(unit_lines)
        if os.path.normpath(src_dir / t) != os.path.normpath(dest_dir / t)
    )


# --------------------------------------------------------------------------- imports


def _hops(dest: str) -> int | None:
    if dest in _IMPORT_HOPS:
        return _IMPORT_HOPS[dest]
    if dest.startswith("L2:"):
        return _ZONE_HOPS
    return None


def import_warnings(
    units: Sequence[Unit], decisions: Sequence[Decision], cfg: Config
) -> tuple[str, ...]:
    """Warnings ``<key>:<unit id>`` for units with ``@path`` imports (spec §5.4 rule 3b).

    ``import_codex`` always (Codex does not expand ``@``); ``import_moved`` when the unit
    leaves L1/L1c; ``import_depth`` when the hop count from CLAUDE.md to the import
    (CLAUDE.md -> @AGENTS.md -> @path) exceeds ``import_max_depth``.
    """
    by_unit = {d.unit: d for d in decisions}
    out: list[str] = []
    for u in units:
        d = by_unit.get(u.id)
        if u.features.imports < 1 or d is None:
            continue
        out.append(f"import_codex:{u.id}")
        if d.dest not in _STARTUP_DESTS:
            out.append(f"import_moved:{u.id}")
        hops = _hops(d.dest)
        if hops is not None and hops > cfg.import_max_depth:
            out.append(f"import_depth:{u.id}")
    return tuple(out)
