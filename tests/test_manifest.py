"""manifest: schema declutter.reorg/1, run folder, plan.md and report.json (spec §6.1-§6.3).

Synthetic manifests built in memory; project trees via ``make_project``. No real content.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import MakeProject
from declutter_context.model import Placement
from declutter_context.reorg import Issue, ReorgError
from declutter_context.reorg import manifest as mf
from declutter_context.reorg.manifest import (
    BlockEntry,
    BlockOverride,
    Budgets,
    GitInfo,
    Manifest,
    ManifestError,
    Output,
    SourceEntry,
    UnitEntry,
    UnitOverride,
)

SEED = "ZQX-SEED-7731"
H64 = "a" * 64
B64 = "b" * 64
C64 = "c" * 64
LONG_HEADING = "Encabezado sintético " + "x" * 59  # 80 characters


def _block(**kw: Any) -> BlockEntry:
    base: dict[str, Any] = {
        "id": "U01.b1",
        "key": "3e91a07c55d2:7f30e2a19b44",
        "kind": "paragraph",
        "lines": (3, 4),
        "sha256": C64,
        "guard": ("secrets",),
        "pin": "list_group",
        "pin_lines": (3, 6),
        "copy_to": ("card",),
        "guard_ack": "",
    }
    base.update(kw)
    return BlockEntry(**base)


def _unit(**kw: Any) -> UnitEntry:
    base: dict[str, Any] = {
        "id": "U01",
        "source": "CLAUDE.md",
        "heading": LONG_HEADING,
        "heading_key": "3e91a07c55d2",
        "lines": (2, 7),
        "sha256": B64,
        "dest": "L4",
        "file": "docs/historial/2026-10.md",
        "card": True,
        "card_id": "D-001",
        "pointer": "root",
        "confidence": "medium",
        "protected": False,
        "needs_confirmation": True,
        "imports": 0,
        "reasons": ("D:date=2026-10-01", "R:title", "K=1", "S=1", "rule:6", "budget:demoted"),
        "blocks": (
            _block(),
            _block(
                id="U01.b2",
                key="3e91a07c55d2:000000000000",
                lines=(5, 7),
                guard=(),
                pin=None,
                pin_lines=None,
                copy_to=(),
            ),
        ),
    }
    base.update(kw)
    return UnitEntry(**base)


def sample(**kw: Any) -> Manifest:
    base: dict[str, Any] = {
        "schema": mf.SCHEMA,
        "id": "20261104-1530",
        "created": "2026-11-04",
        "tool_version": "0.2.0",
        "lang": "es",
        "paths_lang": "es",
        "budgets": Budgets(
            l1_max_bytes=12288,
            l1_max_lines=200,
            zone_max_lines=150,
            codex_budget_bytes=32768,
            codex_warn_pct=60,
        ),
        "git": GitInfo(enabled=True, base_branch="main", base_commit="9" * 40),
        "sources": (
            SourceEntry(
                path="CLAUDE.md",
                sha256=H64,
                bytes=120,
                lines=7,
                bom=False,
                eol="lf",
                final_newline=True,
            ),
        ),
        "units": (
            UnitEntry(
                id="U00",
                source="CLAUDE.md",
                heading="",
                heading_key="U00",
                lines=(1, 1),
                sha256=H64,
                dest="L1",
                file="AGENTS.md",
                card=False,
                card_id=None,
                pointer=None,
                confidence="high",
                protected=False,
                needs_confirmation=False,
                imports=0,
                reasons=("rule:2", "K=0", "S=0"),
                blocks=(),
            ),
            _unit(),
        ),
        "outputs": (
            Output(path="AGENTS.md", mode="created"),
            Output(path="CLAUDE.md", mode="rewritten"),
            Output(path=".declutter/pins.json", mode="control"),
        ),
        "overrides": (),
        "projection": {"AGENTS.md": {"bytes": 100, "lines": 9}, "codex": {"root": 1}},
        "warnings": ("pending_confirmation",),
    }
    base.update(kw)
    return Manifest(**base)


# --------------------------------------------------------------------------- dumps / loads


def test_manifest_roundtrip_is_byte_identical() -> None:
    m = sample(
        overrides=(
            UnitOverride(heading_key="3e91a07c55d2", field="accept", value=True, reason="ok"),
            BlockOverride(block_key="3e91a07c55d2:7f30e2a19b44", guard_ack="motivo largo ok"),
        ),
        symlink_claude=True,
    )
    data = mf.dumps(m)
    back = mf.loads(data)
    assert back == m
    assert mf.dumps(back) == data


def test_dumps_format() -> None:
    m = sample()
    data = mf.dumps(m)
    assert data.endswith(b"\n")
    obj = json.loads(data)
    expected = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    assert data == expected.encode("utf-8")
    # ensure_ascii=False: non-ASCII headings are written as UTF-8, not \u escapes
    assert "sintético".encode() in data
    blocks = obj["units"][1]["blocks"]
    assert blocks[0]["pin_lines"] == [3, 6]
    assert blocks[1]["pin"] is None
    assert blocks[1]["pin_lines"] == []
    assert obj["units"][1]["lines"] == [2, 7]
    assert obj["symlink_claude"] is False
    assert obj["schema"] == "declutter.reorg/1"


def test_overrides_serialization_shape() -> None:
    m = sample(
        overrides=(
            UnitOverride(heading_key="hk1", field="dest", value="L4", reason="r1"),
            UnitOverride(heading_key="hk2", field="card", value=False, reason="r2"),
            UnitOverride(heading_key="hk3", field="keep", value=True, reason="r3"),
            BlockOverride(block_key="hk1:abc", guard_ack="un motivo suficiente"),
        )
    )
    ov = json.loads(mf.dumps(m))["overrides"]
    assert ov[0] == {"heading_key": "hk1", "set": {"dest": "L4"}, "reason": "r1"}
    assert ov[1] == {"heading_key": "hk2", "set": {"card": False}, "reason": "r2"}
    assert ov[2] == {"heading_key": "hk3", "set": {"keep": True}, "reason": "r3"}
    assert ov[3] == {"block_key": "hk1:abc", "guard_ack": "un motivo suficiente"}


def test_dests_and_override_types_are_shared_with_classify() -> None:
    from declutter_context.reorg import classify

    assert mf.UnitOverride is classify.UnitOverride
    assert mf.BlockOverride is classify.BlockOverride
    assert classify.FIXED_DESTS == mf.DESTS_FIXED


def _mutate(fn: Any) -> bytes:
    obj = json.loads(mf.dumps(sample()))
    fn(obj)
    return json.dumps(obj).encode("utf-8")


def _set(path: list[Any], value: Any) -> Any:
    def fn(obj: Any) -> None:
        cur = obj
        for p in path[:-1]:
            cur = cur[p]
        cur[path[-1]] = value

    return fn


def _del(path: list[Any]) -> Any:
    def fn(obj: Any) -> None:
        cur = obj
        for p in path[:-1]:
            cur = cur[p]
        del cur[path[-1]]

    return fn


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"not json", id="not-json"),
        pytest.param(b"[]", id="not-object"),
        pytest.param(b"\xff\xfe", id="not-utf8"),
        pytest.param(_mutate(_set(["schema"], "declutter.reorg/2")), id="schema"),
        pytest.param(_mutate(_del(["units", 1, "dest"])), id="missing-dest"),
        pytest.param(_mutate(_del(["budgets", "l1_max_bytes"])), id="missing-budget"),
        pytest.param(_mutate(_set(["units", 1, "dest"], "L3")), id="dest-bad"),
        pytest.param(_mutate(_set(["units", 1, "dest"], "L2:")), id="dest-l2-empty"),
        pytest.param(_mutate(_set(["units", 1, "dest"], "L2:a/b")), id="dest-l2-nested"),
        pytest.param(_mutate(_set(["units", 1, "dest"], "L2:..")), id="dest-l2-dots"),
        pytest.param(_mutate(_set(["lang"], "fr")), id="lang"),
        pytest.param(_mutate(_set(["paths_lang"], "pt")), id="paths-lang"),
        pytest.param(_mutate(_set(["id"], "../x")), id="id-traversal"),
        pytest.param(_mutate(_set(["created"], "2026-13-01")), id="created"),
        pytest.param(_mutate(_set(["units", 1, "lines"], [7, 2])), id="lines-reversed"),
        pytest.param(_mutate(_set(["units", 1, "lines"], [True, 2])), id="lines-bool"),
        pytest.param(_mutate(_set(["units", 1, "sha256"], "abc")), id="sha-short"),
        pytest.param(_mutate(_set(["units", 1, "confidence"], "huge")), id="confidence"),
        pytest.param(_mutate(_set(["units", 1, "imports"], "0")), id="imports-str"),
        pytest.param(_mutate(_set(["units", 1, "blocks", 0, "pin"], "zone")), id="pin"),
        pytest.param(_mutate(_set(["units", 1, "blocks", 0, "pin_lines"], [])), id="pin-no-lines"),
        pytest.param(
            _mutate(_set(["units", 1, "blocks", 1, "pin_lines"], [5, 7])), id="lines-no-pin"
        ),
        pytest.param(_mutate(_set(["units", 1, "blocks", 0, "copy_to"], ["x"])), id="copy-to"),
        pytest.param(_mutate(_set(["units", 1, "pointer"], "elsewhere")), id="pointer"),
        pytest.param(_mutate(_set(["outputs", 0, "mode"], "deleted")), id="output-mode"),
        pytest.param(_mutate(_set(["sources", 0, "eol"], "cr")), id="eol"),
        pytest.param(_mutate(_set(["sources", 0, "bom"], 0)), id="bom-int"),
        pytest.param(_mutate(_set(["extra"], 1)), id="unknown-key"),
        pytest.param(
            _mutate(_set(["overrides"], [{"heading_key": "h", "set": {}, "reason": "r"}])),
            id="override-empty-set",
        ),
        pytest.param(
            _mutate(
                _set(["overrides"], [{"heading_key": "h", "set": {"keep": False}, "reason": "r"}])
            ),
            id="override-keep-false",
        ),
        pytest.param(
            _mutate(
                _set(["overrides"], [{"heading_key": "h", "set": {"dest": "X"}, "reason": "r"}])
            ),
            id="override-dest-bad",
        ),
        pytest.param(
            _mutate(
                _set(
                    ["overrides"],
                    [{"heading_key": "h", "set": {"dest": "L4", "card": True}, "reason": "r"}],
                )
            ),
            id="override-two-fields",
        ),
        pytest.param(_mutate(_set(["overrides"], [{"block_key": "k"}])), id="override-no-ack"),
        pytest.param(_mutate(_set(["projection"], [])), id="projection"),
        pytest.param(_mutate(_set(["warnings"], [1])), id="warnings"),
    ],
)
def test_loads_rejects_bad_schema_or_shape(data: bytes) -> None:
    with pytest.raises(ManifestError) as ei:
        mf.loads(data)
    issue = ei.value.issue
    assert issue.code == 4
    assert issue.key == "manifest_invalid"


def test_loads_error_carries_no_values() -> None:
    data = _mutate(_set(["units", 1, "dest"], SEED))
    with pytest.raises(ManifestError) as ei:
        mf.loads(data)
    assert SEED not in str(ei.value)
    assert SEED not in ei.value.issue.detail


def test_loads_symlink_claude_defaults_false() -> None:
    data = _mutate(_del(["symlink_claude"]))
    assert mf.loads(data).symlink_claude is False


def test_loads_accepts_l2_and_zone_pointer() -> None:
    m = sample(units=(_unit(dest="L2:app", file="app/AGENTS.md", pointer="root+zone:app"),))
    assert mf.loads(mf.dumps(m)) == m


# --------------------------------------------------------------------------- run folder


NOW = datetime(2026, 11, 4, 15, 30)


def test_run_id_suffixes(make_project: MakeProject) -> None:
    root = make_project().root
    first = mf.run_id(NOW, root)
    assert first == "20261104-1530"
    mf.run_dir(root, first).mkdir(parents=True)
    second = mf.run_id(NOW, root)
    assert second == "20261104-1530-2"
    mf.run_dir(root, second).mkdir(parents=True)
    assert mf.run_id(NOW, root) == "20261104-1530-3"


def test_run_dir_location_and_rejects_bad_id(make_project: MakeProject) -> None:
    root = make_project().root
    assert mf.run_dir(root, "20261104-1530") == root / ".declutter" / "reorg" / "20261104-1530"
    for bad in ("../x", "x", "20261104-1530/..", ""):
        with pytest.raises(ReorgError) as ei:
            mf.run_dir(root, bad)
        assert ei.value.code == 1


def test_ensure_gitignore_creates_star(make_project: MakeProject) -> None:
    root = make_project().root
    assert mf.ensure_gitignore(root) is None
    assert (root / ".declutter" / ".gitignore").read_bytes() == b"*\n"
    # idempotent
    assert mf.ensure_gitignore(root) is None


def test_ensure_gitignore_existing_without_star_is_issue_1(make_project: MakeProject) -> None:
    root = make_project({".declutter/.gitignore": "*.json\n# *\n"}).root
    issue = mf.ensure_gitignore(root)
    assert issue == Issue(code=1, key="gitignore_no_star", dest=".declutter/.gitignore")
    assert (root / ".declutter" / ".gitignore").read_bytes() == b"*.json\n# *\n"


@pytest.mark.parametrize("content", [b"*\n", b"foo\n*\n", b"*", b"a\n*\nb"])
def test_ensure_gitignore_existing_with_star_ok(make_project: MakeProject, content: bytes) -> None:
    root = make_project().root
    (root / ".declutter").mkdir()
    (root / ".declutter" / ".gitignore").write_bytes(content)
    assert mf.ensure_gitignore(root) is None
    assert (root / ".declutter" / ".gitignore").read_bytes() == content


def test_ensure_gitignore_star_must_be_exact(make_project: MakeProject) -> None:
    root = make_project().root
    (root / ".declutter").mkdir()
    (root / ".declutter" / ".gitignore").write_bytes(b" *\n* \n")
    issue = mf.ensure_gitignore(root)
    assert issue is not None
    assert issue.code == 1


def test_snapshot_is_byte_exact(make_project: MakeProject) -> None:
    root = make_project().root
    run = mf.run_dir(root, "20261104-1530")
    docs = [
        ("CLAUDE.md", b"\xef\xbb\xbf# T\r\nline\r\nno newline"),
        ("sub/notes.md", b"a\nb"),
    ]
    mf.write_snapshot(run, docs)
    assert (run / "original" / "CLAUDE.md").read_bytes() == docs[0][1]
    assert (run / "original" / "sub" / "notes.md").read_bytes() == docs[1][1]
    assert not list(run.rglob("*.declutter-tmp-*"))


@pytest.mark.parametrize("bad", ["../x.md", "/abs.md", "a/../../x.md"])
def test_snapshot_rejects_paths_outside(make_project: MakeProject, bad: str) -> None:
    root = make_project().root
    run = mf.run_dir(root, "20261104-1530")
    with pytest.raises(ReorgError):
        mf.write_snapshot(run, [(bad, b"x")])
    assert not (root.parent / "x.md").exists()


# --------------------------------------------------------------------------- paths_lang


@pytest.mark.parametrize(
    ("files", "lang", "expected"),
    [
        ({"docs/history/2026-01.md": "x"}, "es", "en"),
        ({"STATE.md": "x"}, "es", "en"),
        ({"ESTADO.md": "x"}, "en", "es"),
        ({"docs/decisiones/D-001-a.md": "x"}, "en", "es"),
        ({"docs/trampas.md": "x"}, "en", "es"),
        ({"docs/map.md": "x", "docs/pitfalls.md": "x"}, "es", "en"),
        ({}, "es", "es"),
        ({}, "en", "en"),
        ({"docs/otro.md": "x"}, "en", "en"),
    ],
)
def test_choose_paths_lang(
    make_project: MakeProject, files: dict[str, str], lang: str, expected: str
) -> None:
    root = make_project(files).root
    assert mf.choose_paths_lang(root, lang) == expected


def test_choose_paths_lang_both_columns_is_error_1(make_project: MakeProject) -> None:
    root = make_project({"ESTADO.md": "x", "docs/history/a.md": "x"}).root
    with pytest.raises(ReorgError) as ei:
        mf.choose_paths_lang(root, "es")
    assert ei.value.code == 1
    assert [i.key for i in ei.value.issues] == ["paths_both_langs"]


def test_paths_table() -> None:
    assert mf.PATHS["state"] == {"es": "ESTADO.md", "en": "STATE.md"}
    assert mf.PATHS["history_index"] == {
        "es": "docs/historial/INDICE.md",
        "en": "docs/history/INDEX.md",
    }
    assert mf.PATHS["decisions_index"]["en"] == "docs/decisions/INDEX.md"
    assert mf.PATHS["trampas"] == {"es": "docs/trampas.md", "en": "docs/pitfalls.md"}
    assert mf.PATHS["mapa"] == {"es": "docs/mapa.md", "en": "docs/map.md"}


# --------------------------------------------------------------------------- placements


def test_placements_roundtrip() -> None:
    ps = (
        Placement(role="generated", dest="AGENTS.md", at=(1, 6), sha256=H64, kind="agents-header"),
        Placement(
            role="primary",
            dest="AGENTS.md",
            at=(7, 16),
            sha256=B64,
            unit="U00",
            source="CLAUDE.md",
            src=(1, 10),
        ),
        Placement(
            role="copy",
            dest="AGENTS.md",
            at=(31, 45),
            sha256=C64,
            unit="U10",
            block="U10.b1",
            source="CLAUDE.md",
            src=(169, 183),
        ),
        Placement(
            role="extended_prefix",
            dest="docs/trampas.md",
            at=(1, 3),
            sha256=H64,
            backup_sha256=B64,
            prefix_newline_added=False,
        ),
        Placement(role="control", dest=".declutter/pins.json", at=(1, 4), sha256=C64),
    )
    data = mf.placements_dumps(ps)
    assert mf.placements_loads(data) == ps
    assert mf.placements_dumps(mf.placements_loads(data)) == data
    obj = json.loads(data)
    assert obj[0] == {
        "role": "generated",
        "dest": "AGENTS.md",
        "at": [1, 6],
        "kind": "agents-header",
        "sha256": H64,
    }
    assert "prefix_newline_added" not in obj[1]
    assert obj[3]["prefix_newline_added"] is False
    assert data == (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


@pytest.mark.parametrize(
    "data",
    [
        b"{}",
        b'[{"role": "x", "dest": "A", "at": [1, 1], "sha256": "' + H64.encode() + b'"}]',
        b'[{"role": "primary", "dest": "A", "at": [2, 1], "sha256": "' + H64.encode() + b'"}]',
        b'[{"role": "primary", "dest": "A", "at": [1, 1]}]',
        b'[{"role": "primary", "dest": "A", "at": [1, 1], "sha256": "'
        + H64.encode()
        + b'", "zzz": 1}]',
    ],
)
def test_placements_loads_rejects_bad_shape(data: bytes) -> None:
    with pytest.raises(ManifestError):
        mf.placements_loads(data)


# --------------------------------------------------------------------------- reports (G6)


def test_truncate60() -> None:
    assert mf.truncate60("abc") == "abc"
    assert mf.truncate60("x" * 60) == "x" * 60
    cut = mf.truncate60("x" * 61)
    assert len(cut) == 60
    assert cut.endswith("…")
    assert mf.truncate60(LONG_HEADING) == LONG_HEADING[:59] + "…"


def _seeded() -> Manifest:
    # A seeded heading longer than 60 chars: the seed sits past the cut, so it never appears.
    heading = "Sección " + "y" * 60 + " " + SEED
    return sample(units=(_unit(heading=heading),))


def test_plan_md_and_report_have_no_content() -> None:
    m = _seeded()
    for lang in ("es", "en"):
        text = mf.plan_md(m, lang)
        assert SEED not in text
        assert "- [ ] U01" in text
    rep = mf.report_dumps(mf.report_dict(m, (), 0, {}))
    assert SEED.encode() not in rep
    obj = json.loads(rep)
    assert len(obj["units"][0]["heading"]) == 60


def test_plan_md_lists_metadata() -> None:
    m = sample()
    text = mf.plan_md(m, "es")
    line = next(ln for ln in text.splitlines() if ln.startswith("- [ ] U01"))
    assert LONG_HEADING[:59] + "…" in line
    assert LONG_HEADING not in text
    assert "CLAUDE.md:2-7" in line
    assert "L4" in line and "docs/historial/2026-10.md" in line
    assert "D-001" in line
    assert "U01.b1" in line
    assert "medium" in line
    assert "budget:demoted" in line
    assert "20261104-1530" in text
    en = mf.plan_md(m, "en")
    assert "pending" in en
    assert "pendiente" in text


def test_report_dict_shape() -> None:
    m = sample(
        overrides=(
            BlockOverride(block_key="3e91a07c55d2:7f30e2a19b44", guard_ack="un motivo suficiente"),
        ),
        units=(_unit(blocks=(_block(guard_ack="un motivo suficiente"),)),),
    )
    issues = (Issue(code=3, key="check.9", dest="AGENTS.md"),)
    d = mf.report_dict(m, issues, 3, {"lever_l4": 1200}, unit_bytes={"U01": 345})
    assert d["schema"] == "declutter.reorg-report/1"
    assert d["id"] == "20261104-1530"
    assert d["code"] == 3
    assert d["sources"] == [
        {
            "path": "CLAUDE.md",
            "sha256": H64,
            "bytes": 120,
            "lines": 7,
            "bom": False,
            "eol": "lf",
            "final_newline": True,
        }
    ]
    u = d["units"][0]  # type: ignore[index]
    assert u["bytes"] == 345
    assert u["lines"] == [2, 7]
    assert u["pending"] is True
    assert u["copies"] == [
        {"block": "U01.b1", "lines": [3, 6], "pin": "list_group", "categories": ["secrets"]}
    ]
    assert u["guard_acks"] == ["U01.b1"]
    assert d["pending_confirmation"] == ["U01"]
    assert d["levers"] == {"lever_l4": 1200}
    assert d["issues"] == [{"code": 3, "key": "check.9", "dest": "AGENTS.md", "detail": ""}]
    assert d["outputs"][0] == {"path": "AGENTS.md", "mode": "created"}  # type: ignore[index]
    assert d["warnings"] == ["pending_confirmation"]
    json.loads(mf.report_dumps(d))


def test_report_levers_only_with_code_3() -> None:
    d = mf.report_dict(sample(), (), 4, {"lever_l4": 1})
    assert "levers" not in d


@pytest.mark.parametrize(
    ("override", "pending"),
    [
        (None, True),
        (UnitOverride(heading_key="3e91a07c55d2", field="accept", value=True, reason="ok"), False),
        (UnitOverride(heading_key="3e91a07c55d2", field="accept", value=True, reason=" "), True),
        (UnitOverride(heading_key="3e91a07c55d2", field="dest", value="L4", reason="ok"), False),
        (UnitOverride(heading_key="otro", field="accept", value=True, reason="ok"), True),
    ],
)
def test_pending_units(override: UnitOverride | None, pending: bool) -> None:
    m = sample(overrides=(override,) if override else ())
    assert (mf.pending_units(m) == ("U01",)) is pending


def test_pending_l1c_requires_dest_l1c() -> None:
    u = _unit(dest="L1c", file="CLAUDE.md", card=False, card_id=None, pointer=None)
    acc = UnitOverride(heading_key="3e91a07c55d2", field="accept", value=True, reason="ok")
    assert mf.pending_units(sample(units=(u,), overrides=(acc,))) == ("U01",)
    dst = UnitOverride(heading_key="3e91a07c55d2", field="dest", value="L1c", reason="ok")
    assert mf.pending_units(sample(units=(u,), overrides=(dst,))) == ()
    gen = replace(u, needs_confirmation=False, reasons=("generated", "rule:1"))
    assert mf.pending_units(sample(units=(gen,))) == ()


def test_report_bytes_identical_with_same_inputs() -> None:
    a = mf.report_dumps(mf.report_dict(sample(), (), 0, {}))
    b = mf.report_dumps(mf.report_dict(sample(), (), 0, {}))
    assert a == b
    assert mf.dumps(sample()) == mf.dumps(sample())


def test_manifest_is_jsonable_projection_preserved(tmp_path: Path) -> None:
    m = sample(projection={"S1": {"before": 1, "after": 2}, "nested": [1, {"a": None}]})
    assert mf.loads(mf.dumps(m)).projection == m.projection
