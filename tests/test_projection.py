"""projection: S1-S4, Codex chains, load-set diff, link and import warnings (spec §10.2.3, §13).

Synthetic projects only (``make_project``); the "after" content is a hand-built map.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import measure, signals_files
from declutter_context.model import Config, Decision, Features, Measurement, Unit
from declutter_context.reorg import projection
from declutter_context.reorg.projection import CodexChain, Projection

CFG = Config()


def _sig(m: Measurement, sid: str) -> float | None:
    return next(s.value for s in m.signals if s.id == sid)


def _unit(uid: str, *, imports: int = 0, source: str = "CLAUDE.md") -> Unit:
    return Unit(
        id=uid,
        source=source,
        heading=f"Synthetic {uid}",
        heading_key=f"synthetic {uid.lower()}",
        start=1,
        end=3,
        blocks=(),
        features=Features(imports=imports),
    )


def _decision(uid: str, dest: str) -> Decision:
    return Decision(
        unit=uid,
        dest=dest,
        file="AGENTS.md",
        card=False,
        pointer=None,
        confidence="high",
        reasons=(),
        protected=False,
        needs_confirmation=False,
    )


# --------------------------------------------------------------------------- overlay


def test_overlay_reader_prefers_map_then_disk(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "disk agents\n", "docs/x.md": "disk x\n"})
    read = projection.overlay_reader(p, {"AGENTS.md": b"map agents\n", "new.md": b"n\n"})
    assert read(p.root / "AGENTS.md") == "map agents\n"
    assert read(p.root / "new.md") == "n\n"
    assert read(p.root / "docs" / "x.md") == "disk x\n"
    assert read(p.root / "docs" / ".." / "AGENTS.md") == "map agents\n"
    assert read(p.root / "missing.md") is None


def test_overlay_reader_never_reads_secret_files(make_project: MakeProject) -> None:
    p = make_project({".env": "TOKEN=x\n"})
    read = projection.overlay_reader(p, {})
    assert read(p.root / ".env") is None


# --------------------------------------------------------------------------- signals


def test_s1_s4_over_map(make_project: MakeProject) -> None:
    claude = "".join(f"## 2026-10-{d:02d} entry\nbody\n" for d in range(1, 10))
    p = make_project({"CLAUDE.md": claude})
    agents = b"# T\n\n## Rules\nr\n\n## Map\nm\n\n## 2026-10-01 old\nx\n"
    files = {"AGENTS.md": agents, "CLAUDE.md": b"@AGENTS.md\n"}
    proj = projection.project_after(p, CFG, files, ())
    assert proj.before["S1"] == 18.0
    assert proj.before["S4"] == 100.0
    assert proj.after["S1"] == float(len(agents.decode().splitlines()) + 1)
    assert proj.after["S4"] == pytest.approx(100 / 3)
    total = len(agents) + len(b"@AGENTS.md\n")
    assert proj.after["S2"] == float(math.ceil(total / CFG.bytes_per_token))
    assert dict((f, (b, n)) for f, b, n in proj.files) == {
        "AGENTS.md": (len(agents), 10),
        "CLAUDE.md": (11, 1),
    }


def test_s4_needs_three_headings(make_project: MakeProject) -> None:
    p = make_project({})
    proj = projection.project_after(p, CFG, {"AGENTS.md": b"## a\n## 2026-01-01 b\n"}, ())
    assert proj.after["S4"] is None
    assert proj.after["S1"] == 2.0
    assert proj.before["S1"] is None


def test_s3_from_na_to_value(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "# only claude\n"})
    agents = b"x" * 3277 + b"\n"
    proj = projection.project_after(p, CFG, {"AGENTS.md": agents}, ())
    assert proj.before["S3"] is None
    assert proj.after["S3"] == pytest.approx(100 * len(agents) / CFG.codex_budget_bytes)


# --------------------------------------------------------------------------- codex chains


def test_codex_chain_uses_map_not_disk(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "y" * 30_000 + "\n"})
    small = b"z" * 999 + b"\n"
    proj = projection.project_after(p, CFG, {"AGENTS.md": small}, ())
    (root,) = proj.codex
    assert root == CodexChain(
        name="root",
        files=("AGENTS.md",),
        bytes=1000,
        pct=pytest.approx(100 * 1000 / CFG.codex_budget_bytes),
    )
    assert proj.before["S3"] == pytest.approx(100 * 30_001 / CFG.codex_budget_bytes)


def test_codex_zone_chain_root_plus_zone(make_project: MakeProject) -> None:
    p = make_project({"lib/AGENTS.md": "d" * 99 + "\n", "lib/x.py": "", "web/y.js": ""})
    files = {"AGENTS.md": b"a" * 199 + b"\n", "src/AGENTS.md": b"s" * 49 + b"\n"}
    proj = projection.project_after(p, CFG, files, ("src", "lib", "web"))
    by_name = {c.name: c for c in proj.codex}
    assert set(by_name) == {"root", "src", "lib"}
    assert by_name["root"].files == ("AGENTS.md",)
    assert by_name["root"].bytes == 200
    assert by_name["src"].files == ("AGENTS.md", "src/AGENTS.md")
    assert by_name["src"].bytes == 250
    assert by_name["lib"].files == ("AGENTS.md", "lib/AGENTS.md")
    assert by_name["lib"].bytes == 300
    assert proj.codex_max_pct == pytest.approx(100 * 300 / CFG.codex_budget_bytes)
    paths = [f for f, _, _ in proj.files]
    assert paths == ["AGENTS.md", "lib/AGENTS.md", "src/AGENTS.md"]


def test_codex_empty_chain_is_zero(make_project: MakeProject) -> None:
    p = make_project({})
    proj = projection.project_after(p, CFG, {}, ())
    assert proj.codex == (CodexChain(name="root", files=(), bytes=0, pct=0.0),)
    assert proj.codex_max_pct == 0.0
    assert proj.after["S3"] is None


def _pct_bytes(pct: int, budget: int) -> int:
    return budget * pct // 100 + 1


@pytest.mark.parametrize(
    ("pct", "keys"),
    [(60, []), (61, ["codex_warn"]), (100, ["codex_warn"]), (101, ["codex_budget"])],
)
def test_codex_warn_and_over_100(make_project: MakeProject, pct: int, keys: list[str]) -> None:
    budget = CFG.codex_budget_bytes
    size = budget * pct // 100 if pct in (60, 100) else _pct_bytes(pct, budget)
    p = make_project({})
    proj = projection.project_after(p, CFG, {"AGENTS.md": b"q" * (size - 1) + b"\n"}, ())
    issues = projection.codex_warnings(proj, CFG)
    assert [i.key for i in issues] == keys
    for i in issues:
        assert i.code == (3 if i.key == "codex_budget" else 0)
        assert i.dest == "AGENTS.md"
        assert "chain=root" in i.detail


def test_codex_warn_pct_from_config(make_project: MakeProject) -> None:
    cfg = replace(CFG, reorg=replace(CFG.reorg, codex_warn_pct=10))
    p = make_project({})
    proj = projection.project_after(p, cfg, {"AGENTS.md": b"q" * 4000 + b"\n"}, ())
    assert [i.key for i in projection.codex_warnings(proj, cfg)] == ["codex_warn"]


def test_codex_budget_per_zone_chain(make_project: MakeProject) -> None:
    budget = CFG.codex_budget_bytes
    files = {"AGENTS.md": b"a" * (budget * 7 // 10), "src/AGENTS.md": b"s" * budget + b"\n"}
    p = make_project({})
    proj = projection.project_after(p, CFG, files, ("src",))
    issues = projection.codex_warnings(proj, CFG)
    assert [(i.key, i.dest) for i in issues] == [
        ("codex_warn", "AGENTS.md"),
        ("codex_budget", "src/AGENTS.md"),
    ]


# --------------------------------------------------------------------------- load set


def test_loadset_diff_includes_import_target(make_project: MakeProject) -> None:
    claude = "# P\n\n## 2026-10-01 rules\n@docs/reglas.md\n"
    p = make_project({"CLAUDE.md": claude, "docs/reglas.md": "rules\n", "docs/old.md": "o\n"})
    # The unit stays in L1: it moves to AGENTS.md, still reachable via @AGENTS.md.
    kept = {"CLAUDE.md": b"@AGENTS.md\n", "AGENTS.md": b"## 2026-10-01 rules\n@docs/reglas.md\n"}
    proj = projection.project_after(p, CFG, kept, ())
    assert "docs/reglas.md" in proj.loadset_before
    assert "docs/reglas.md" in proj.loadset_after
    assert proj.loadset_added == ("AGENTS.md",)
    assert proj.loadset_removed == ()
    # Moved to L4: the import is no longer loaded at startup.
    moved = {
        "CLAUDE.md": b"@AGENTS.md\n",
        "AGENTS.md": b"# P\n",
        "docs/historial/2026-10.md": b"## 2026-10-01 rules\n@docs/reglas.md\n",
    }
    proj2 = projection.project_after(p, CFG, moved, ())
    assert proj2.loadset_removed == ("docs/reglas.md",)
    assert "docs/historial/2026-10.md" not in proj2.loadset_after


# --------------------------------------------------------------------------- vs measure


def test_projection_matches_measure_after_writing_map(make_project: MakeProject) -> None:
    claude = "# P\n\n" + "".join(f"## 2026-09-{d:02d} x\n- a\n" for d in range(1, 6))
    p = make_project({"CLAUDE.md": claude, "docs/reglas.md": "r\n", "src/a.py": ""})
    files = {
        "CLAUDE.md": b"@AGENTS.md\n\n## Claude only\nnote\n",
        "AGENTS.md": b"# P\n\n## Rules\n@docs/reglas.md\n\n## Map\nm\n\n## 2026-09-01 x\n- a\n",
        "src/AGENTS.md": b"# Zone src\n",
        "src/CLAUDE.md": b"@AGENTS.md\n",
        "docs/historial/2026-09.md": b"## 2026-09-02 x\n- a\n",
    }
    proj = projection.project_after(p, CFG, files, ("src",))
    before = measure.measure(p, CFG)
    for sid in ("S1", "S2", "S3", "S4"):
        assert proj.before[sid] == _sig(before, sid), sid
    for rel, data in files.items():
        target = p.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    after = measure.measure(p, CFG)
    for sid in ("S1", "S2", "S3", "S4"):
        assert proj.after[sid] == _sig(after, sid), sid
    assert proj.after["S3"] == signals_files.s3_codex_budget(p, CFG).value


# --------------------------------------------------------------------------- json


def test_to_json_shape_and_determinism(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "## a\n"})
    files = {"AGENTS.md": b"## a\n## b\n## c\n", "CLAUDE.md": b"@AGENTS.md\n"}
    proj = projection.project_after(p, CFG, files, ())
    data = proj.to_json()
    assert set(data) == {"files", "before", "after", "codex", "codex_max_pct", "loadset"}
    assert data["files"] == [
        {"path": "AGENTS.md", "bytes": 15, "lines": 3},
        {"path": "CLAUDE.md", "bytes": 11, "lines": 1},
    ]
    assert data["loadset"] == {
        "added": ["AGENTS.md"],
        "removed": [],
        "before": ["CLAUDE.md"],
        "after": ["AGENTS.md", "CLAUDE.md"],
    }
    text = json.dumps(data, sort_keys=True)
    again = projection.project_after(p, CFG, files, ()).to_json()
    assert json.dumps(again, sort_keys=True) == text
    assert isinstance(proj, Projection)


# --------------------------------------------------------------------------- links


def test_relative_links_moved_count(tmp_path: Path) -> None:
    lines = [
        b"## 2026-10-01 x\r",
        b'See [a](docs/a.md) and [b](./b.md#sec) and ![img](img/p.png "t").',
        b"[web](https://example.com) [mail](mailto:x@example.com) [frag](#top)",
        b"[abs](/etc/hosts) [ang](<docs/c d.md>) [empty]() [q](?x=1)",
        b"Inline `[no](docs/n.md)` is code.",
        b"```",
        b"[fenced](docs/f.md)",
        b"```",
        b"<!-- [commented](docs/c.md) -->",
    ]
    n = projection.relative_links_moved(lines, "CLAUDE.md", "docs/historial/2026-10.md", tmp_path)
    assert n == 4
    assert projection.relative_links_moved(lines, "CLAUDE.md", "AGENTS.md", tmp_path) == 0
    # A link that resolves to the same file from both folders does not count.
    same = [b"[up](../../AGENTS.md)"]
    assert projection.relative_links_moved(same, "a/b/X.md", "a/b/Y.md", tmp_path) == 0
    assert projection.relative_links_moved(same, "a/b/X.md", "a/Y.md", tmp_path) == 1


# --------------------------------------------------------------------------- imports


def test_import_warnings() -> None:
    units = [_unit("U01", imports=2), _unit("U02"), _unit("U03", imports=1)]
    keep = [_decision("U01", "L1"), _decision("U02", "L4"), _decision("U03", "L1c")]
    assert projection.import_warnings(units, keep, CFG) == (
        "import_codex:U01",
        "import_codex:U03",
    )
    moved = [_decision("U01", "L4"), _decision("U02", "L4"), _decision("U03", "L2:src")]
    assert projection.import_warnings(units, moved, CFG) == (
        "import_codex:U01",
        "import_moved:U01",
        "import_codex:U03",
        "import_moved:U03",
    )
    shallow = replace(CFG, import_max_depth=1)
    assert projection.import_warnings(units, keep, shallow) == (
        "import_codex:U01",
        "import_depth:U01",
        "import_codex:U03",
    )
    assert projection.import_warnings([_unit("U09")], [_decision("U09", "L1")], CFG) == ()
