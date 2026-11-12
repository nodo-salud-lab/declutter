"""Shared builders for the proof tests: a synthetic plan built in memory (no real data)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from declutter_context import fsutil
from declutter_context.model import Config, Decision, Placement, Project, SourceDoc
from declutter_context.reorg import build, classify, mdblocks, projection
from declutter_context.reorg.build import BuildInput, BuildResult
from declutter_context.reorg.classify import ClassifyContext, Overrides
from declutter_context.reorg.lexicon import build_lexicon
from declutter_context.reorg.manifest import (
    SCHEMA,
    BlockEntry,
    Budgets,
    GitInfo,
    Manifest,
    Output,
    SourceEntry,
    UnitEntry,
)

LEX = build_lexicon({})
RID = "20261104-1530"
TODAY = date(2026, 11, 4)


@dataclass
class Plan:
    project: Project
    inp: BuildInput
    res: BuildResult
    manifest: Manifest
    raw: dict[str, bytes]
    files: dict[str, bytes] = field(default_factory=dict)

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)

    def original(self, path: str) -> bytes:
        return self.raw[path]

    def lines(self, path: str) -> list[bytes]:
        return list(mdblocks.split_lines(self.files[path]).lines)

    def set_lines(self, path: str, lines: Sequence[bytes]) -> None:
        self.files[path] = b"".join(x + b"\n" for x in lines)

    def places(self, path: str) -> list[Placement]:
        return sorted((p for p in self.res.placements if p.dest == path), key=lambda p: p.at)

    def unit_id(self, heading: str) -> str:
        return next(u.id for u in self.inp.units if u.heading == heading)


def _existing(root: Path):  # type: ignore[no-untyped-def]
    def read(rel: str) -> bytes | None:
        try:
            return fsutil.safe_read_bytes(root / rel, root)
        except fsutil.UnsafeReadError:
            return None

    return read


def manifest_of(inp: BuildInput, res: BuildResult, order: Sequence[str]) -> Manifest:
    dec = {d.unit: d for d in inp.decisions}
    sources = tuple(
        SourceEntry(
            path=d.path,
            sha256=d.sha256,
            bytes=d.bytes,
            lines=len(d.lines),
            bom=d.bom,
            eol=d.eol,
            final_newline=d.final_newline,
        )
        for d in (inp.docs[s] for s in order)
    )
    units = []
    for u in inp.units:
        d = dec[u.id]
        doc = inp.docs[u.source]
        blocks = tuple(
            BlockEntry(
                id=b.id,
                key=b.key,
                kind=b.kind,
                lines=(b.start, b.end),
                sha256=mdblocks.lines_sha(doc.lines[b.start - 1 : b.end]),
                guard=tuple(sorted(b.categories)),
                pin=None,
                pin_lines=None,
                copy_to=(),
                guard_ack=inp.overrides.ack(b.key),
            )
            for b in u.blocks
        )
        units.append(
            UnitEntry(
                id=u.id,
                source=u.source,
                heading=u.heading,
                heading_key=u.heading_key,
                lines=(u.start, u.end),
                sha256=mdblocks.lines_sha(doc.lines[u.start - 1 : u.end]),
                dest=d.dest,
                file=d.file,
                card=d.card,
                card_id=res.card_ids.get(u.id),
                pointer=d.pointer,
                confidence=d.confidence,
                protected=d.protected,
                needs_confirmation=d.needs_confirmation,
                imports=u.features.imports,
                reasons=d.reasons,
                blocks=blocks,
            )
        )
    return Manifest(
        schema=SCHEMA,
        id=inp.reorg_id,
        created=inp.today.isoformat(),
        tool_version="0.2.0",
        lang=inp.lang,
        paths_lang=inp.paths_lang,
        budgets=Budgets(12_288, 200, 150, 32_768, 60),
        git=GitInfo(enabled=False, base_branch=None, base_commit=None),
        sources=sources,
        units=tuple(units),
        outputs=tuple(Output(path=p, mode=res.modes[p]) for p in sorted(res.files)),
        overrides=(*inp.overrides.units, *inp.overrides.blocks),
        projection={},
        warnings=(),
        symlink_claude=inp.symlink_claude,
    )


def make_plan(
    project: Project,
    sources: Mapping[str, bytes],
    disk: Mapping[str, bytes] | None = None,
    *,
    ov: Overrides | None = None,
    dirs: Sequence[str] = (),
    controls: bool = True,
    force: Mapping[str, Callable[[Decision], Decision]] | None = None,
    drop_invariants: bool = False,
) -> Plan:
    """Build a plan; ``force`` rewrites the decision of a unit (by heading) after the
    overrides, and ``drop_invariants`` removes the G1 copies (both only to fake bad plans)."""
    root = project.root
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for rel, data in {**(disk or {}), **sources}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    overrides = ov or Overrides()
    order = sorted(sources, key=lambda s: (s != "CLAUDE.md", s != "AGENTS.md", s))
    docs: dict[str, SourceDoc] = {s: mdblocks.parse(sources[s], s) for s in order}
    ctx = ClassifyContext(
        lexicon=LEX,
        top_dirs=classify.top_level_dirs(root),
        paths_lang="es",
        agents_source="AGENTS.md" if "AGENTS.md" in docs else None,
        claude_source="CLAUDE.md" if "CLAUDE.md" in docs else None,
    )
    units = tuple(
        classify.features(u, docs[u.source], ctx)
        for u in mdblocks.parse_sources([docs[s] for s in order])
    )
    decs = tuple(classify.decide(u, ctx) for u in units)
    decs, issues = classify.apply_overrides(units, decs, overrides, ctx)
    assert not issues, issues
    if force:
        heads = {u.id: u.heading for u in units}
        decs = tuple(force[heads[d.unit]](d) if heads[d.unit] in force else d for d in decs)
    cps = classify.copies(units, docs, decs, overrides)
    if drop_invariants:
        cps = tuple(c for c in cps if c.target != "invariants")
    inp = BuildInput(
        root=root,
        reorg_id=RID,
        today=TODAY,
        lang="es",
        paths_lang="es",
        project_name="demo",
        docs=docs,
        units=units,
        decisions=decs,
        copies=cps,
        overrides=overrides,
        existing=_existing(root),
    )
    res = build.build_content(inp)
    if controls:
        cfg = Config()
        proj = projection.project_after(project, cfg, res.files, ())
        res, _ = build.add_controls(res, inp, proj, cfg, None, (), None, None)
    m = manifest_of(inp, res, order)
    return Plan(project, inp, res, m, dict(sources), dict(res.files))


def with_places(plan: Plan, placements: Sequence[Placement]) -> Plan:
    return replace(plan, res=replace(plan.res, placements=tuple(placements)))
