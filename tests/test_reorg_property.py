"""Coverage by property (spec §15.1 item 6): 500 random synthetic documents with a fixed
seed; on every one ``plan`` -> ``set`` (every pending confirmation) -> ``apply --no-git``
-> ``verify`` passes and the C3 reconstruction has the original sha256.

The documents come from ``reorg_synth.random_document``: duplicates, blank lines, trailing
spaces, CRLF, lone CR, BOM, no final newline, fences, tables, items and headings.
"""

from __future__ import annotations

import hashlib
import random
import shutil
from datetime import datetime
from pathlib import Path

from conftest import MakeProject
from declutter_context.model import Config, Project
from declutter_context.reorg import apply as ap
from declutter_context.reorg import manifest as mf
from declutter_context.reorg import mdblocks, undo
from reorg_synth import accept_pending, random_document
from test_apply import FakePrompter

SEED = 20261104
N_DOCS = 500
NOW = datetime(2026, 11, 4, 15, 30)


def rebuild_source(root: Path, run: Path, m: mf.Manifest, source: str) -> bytes:
    """C3 done independently of ``proof``: the primaries of ``source`` in ``src`` order,
    read back from the destinations on disk."""
    placements = mf.placements_loads((run / ap.PLACEMENT_NAME).read_bytes())
    entry = next(s for s in m.sources if s.path == source)
    prim = sorted(
        (p for p in placements if p.role == "primary" and p.source == source),
        key=lambda p: p.src or (0, 0),
    )
    lines: list[bytes] = []
    for p in prim:
        dest = mdblocks.split_lines((root / p.dest).read_bytes()).lines
        lines += dest[p.at[0] - 1 : p.at[1]]
    out = (b"\xef\xbb\xbf" if entry.bom else b"") + b"\n".join(lines)
    return out + (b"\n" if entry.final_newline else b"")


def plan_apply_verify(project: Project, raw: bytes, label: str) -> None:
    cfg = Config()
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=cfg, lang="es", now=NOW))
    assert out.code == 0, (label, out.issues)
    rid = out.manifest.id
    accept_pending(project, cfg, rid, NOW)
    checked = mf.check_run(project, cfg, rid, now=NOW)
    assert checked.code == 0, (label, checked.issues)
    res = ap.apply(
        project,
        cfg,
        rid,
        ap.ApplyOptions(yes=True, no_git=True),
        FakePrompter(tty=True, answers=["s", "s"]),
        now=NOW,
        lang="es",
    )
    assert res.code == 0, (label, res.issues)
    result = undo.verify_run(project, cfg, rid, rev=None, strict=True)
    assert result.ok and result.mode == "exact", (label, result.mode, result.failures)
    m, run = mf.load_run(project.root, rid)
    for s in m.sources:
        original = raw if s.path == "CLAUDE.md" else b""
        rebuilt = rebuild_source(project.root, run, m, s.path)
        assert hashlib.sha256(rebuilt).hexdigest() == hashlib.sha256(original).hexdigest(), label


def test_property_plan_apply_verify_500_docs(make_project: MakeProject, tmp_path: Path) -> None:
    base = make_project()
    rng = random.Random(SEED)
    for i in range(N_DOCS):
        raw = random_document(rng)
        mdblocks.parse(raw, "CLAUDE.md")  # the generator only yields balanced documents
        root = tmp_path / "docs" / f"d{i:03d}"
        root.mkdir(parents=True)
        (root / "CLAUDE.md").write_bytes(raw)
        project = Project.at(root, home=base.home, claude_home=base.claude_home)
        plan_apply_verify(project, raw, f"doc {i}")
        shutil.rmtree(root)
