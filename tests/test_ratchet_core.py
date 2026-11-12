"""Ratchet core (spec §12.1, §12.3, §8 G8, §7 C9, §9.1). Synthetic data only."""

from __future__ import annotations

import json
import math

import pytest

from conftest import MakeProject
from declutter_context import ratchet
from declutter_context.model import Block, Pin, Placement
from declutter_context.reorg import mdblocks
from declutter_context.reorg.mdblocks import lines_sha

H64 = "a" * 64
RID = "20261104-1530"


def _block(bid: str, cats: frozenset[str] = frozenset({"publishable"})) -> Block:
    return Block(id=bid, kind="paragraph", start=1, end=1, key="k:000000000000", categories=cats)


def _agents() -> list[bytes]:
    return [
        b"# Project",
        b"",
        b"## Rules",
        b"- never publish the key",
        b"- keep CRLF\r",
        b"",
        b"### copy",
        b"do not push to main",
        b"",
    ]


# --------------------------------------------------------------------------- windows


def test_find_window() -> None:
    lines = [b"a", b"b", b"c", b"b", b"c", b"d"]
    assert ratchet.find_window(lines, lines_sha([b"a", b"b"]), 2) == 1
    assert ratchet.find_window(lines, lines_sha([b"c", b"d"]), 2) == 5
    assert ratchet.find_window(lines, lines_sha([b"b", b"c"]), 2) == 2  # first of duplicates
    assert ratchet.find_window(lines, lines_sha([b"x"]), 1) is None
    assert ratchet.find_window(lines, lines_sha([b"a", b"c"]), 2) is None  # not contiguous
    assert ratchet.find_window(lines, lines_sha(lines), 6) == 1
    assert ratchet.find_window(lines, lines_sha(lines), 7) is None
    assert ratchet.find_window(lines, lines_sha([]), 0) is None
    assert ratchet.find_window([], lines_sha([b"a"]), 1) is None
    # The window length matters: the same sha with the wrong n is not found.
    assert ratchet.find_window(lines, lines_sha([b"a", b"b"]), 3) is None


def test_title_key_matches_heading_key() -> None:
    assert ratchet.title_key("Nueva función (05-11-2026)") == mdblocks.heading_key(
        "Nueva función (05-11-2026)"
    )
    assert "~" not in ratchet.title_key("x")


# --------------------------------------------------------------------------- pins


def test_compute_pins_roles() -> None:
    agents = _agents()
    placements = [
        Placement(role="primary", dest="AGENTS.md", at=(1, 6), sha256=H64, unit="U00"),
        Placement(role="copy", dest="AGENTS.md", at=(8, 8), sha256=H64, unit="U13", block="U13.b3"),
        Placement(role="copy", dest="docs/trampas.md", at=(1, 1), sha256=H64, unit="U13"),
    ]
    l1 = [("U01", _block("U01.b2", frozenset({"device", "publishable"})), (4, 5))]
    protected = [("U01", (3, 6), frozenset({"publishable"}))]
    pins = ratchet.compute_pins(
        RID,
        agents,
        placements,
        l1,
        protected,
        prior=(),
        block_categories={"U13.b3": frozenset({"git"})},
    )
    assert [p.role for p in pins] == ["copy", "l1_block", "unit"]
    assert [p.id for p in pins] == ["P-001", "P-002", "P-003"]
    copy, blk, unit = pins
    assert copy == Pin(
        id="P-001",
        reorg=RID,
        unit="U13",
        block="U13.b3",
        file="AGENTS.md",
        role="copy",
        categories=("git",),
        lines=1,
        sha256=lines_sha(agents[7:8]),
    )
    assert blk.block == "U01.b2" and blk.unit == "U01"
    assert blk.categories == ("device", "publishable")
    assert blk.lines == 2 and blk.sha256 == lines_sha(agents[3:5])
    assert unit.block is None and unit.unit == "U01"
    assert unit.lines == 4 and unit.sha256 == lines_sha(agents[2:6])  # title included
    for p in pins:
        start = ratchet.find_window(agents, p.sha256, p.lines)
        assert start is not None


def test_compute_pins_skips_uncategorized_l1_blocks() -> None:
    l1 = [("U01", _block("U01.b1", frozenset()), (1, 1))]
    assert ratchet.compute_pins(RID, _agents(), (), l1, (), prior=()) == ()


def test_compute_pins_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        ratchet.compute_pins(RID, _agents(), (), (), [("U01", (8, 99), frozenset({"x"}))], ())
    bad = [Placement(role="copy", dest="AGENTS.md", at=(0, 1), sha256=H64, unit="U1", block="b")]
    with pytest.raises(ValueError):
        ratchet.compute_pins(RID, _agents(), bad, (), (), ())


def test_compute_pins_no_duplicates_and_ids_continue() -> None:
    agents = _agents()
    prior = (
        Pin(
            "P-007",
            "20261001-0900",
            "U02",
            None,
            "AGENTS.md",
            "unit",
            ("publishable",),
            4,
            lines_sha(agents[2:6]),
        ),
        Pin("P-002", "20261001-0900", "U03", "U03.b1", "docs/x.md", "copy", ("git",), 1, H64),
    )
    placements = [
        Placement(role="copy", dest="AGENTS.md", at=(8, 8), sha256=H64, unit="U13", block="U13.b3"),
        # Same lines again: same file and sha -> one pin only.
        Placement(role="copy", dest="AGENTS.md", at=(8, 8), sha256=H64, unit="U14", block="U14.b1"),
    ]
    protected = [("U01", (3, 6), frozenset({"publishable"}))]  # same sha as prior P-007
    pins = ratchet.compute_pins(RID, agents, placements, (), protected, prior)
    assert pins[:2] == prior
    assert [p.id for p in pins[2:]] == ["P-008"]
    assert pins[2].unit == "U13" and pins[2].categories == ()
    # Pin ids keep three digits and then grow.
    big = (Pin("P-999", RID, "U1", None, "AGENTS.md", "unit", (), 1, H64),)
    more = ratchet.compute_pins(RID, agents, placements[:1], (), (), big)
    assert more[-1].id == "P-1000"


# --------------------------------------------------------------------------- baseline


def test_baseline_first_time_ceil() -> None:
    state, warnings = ratchet.baseline(318, 96.2, 150, ["bbbbbbbbbbbb", "aaaaaaaaaaaa"], RID, None)
    assert warnings == ()
    assert state == ratchet.RatchetState(
        s1_max=318,
        s3_max_pct=97,
        slack_lines=5,
        zone_max_lines=150,
        forbid_changelog_headings=True,
        allowed_dated_headings=("aaaaaaaaaaaa", "bbbbbbbbbbbb"),
        reorg=RID,
    )
    exact, _ = ratchet.baseline(10, 97.0, 150, [], RID, None)
    assert exact.s3_max_pct == 97 == math.ceil(97.0)


def test_baseline_never_increases_and_warns() -> None:
    prior = ratchet.RatchetState(
        s1_max=300,
        s3_max_pct=90,
        slack_lines=7,
        zone_max_lines=120,
        forbid_changelog_headings=False,
        allowed_dated_headings=("aaaaaaaaaaaa", "bbbbbbbbbbbb"),
        reorg="20261001-0900",
    )
    lower, w = ratchet.baseline(250, 80.5, 150, ["aaaaaaaaaaaa"], RID, prior)
    assert w == ()
    assert (lower.s1_max, lower.s3_max_pct, lower.zone_max_lines) == (250, 81, 120)
    assert lower.slack_lines == 7 and lower.forbid_changelog_headings is False
    assert lower.allowed_dated_headings == ("aaaaaaaaaaaa",)
    assert lower.reorg == RID

    higher, w = ratchet.baseline(301, 90.0, 100, ["aaaaaaaaaaaa"], RID, prior)
    assert w == ("ratchet_would_fail",)
    assert (higher.s1_max, higher.s3_max_pct, higher.zone_max_lines) == (300, 90, 100)

    _, w = ratchet.baseline(300, 90.01, 150, [], RID, prior)
    assert w == ("ratchet_would_fail",)

    # A dated heading that was not allowed before is growth: not added, warned.
    grown, w = ratchet.baseline(10, 1.0, 150, ["aaaaaaaaaaaa", "cccccccccccc"], RID, prior)
    assert w == ("ratchet_would_fail",)
    assert grown.allowed_dated_headings == ("aaaaaaaaaaaa",)


# --------------------------------------------------------------------------- files


def test_ratchet_and_pins_roundtrip(make_project: MakeProject) -> None:
    state = ratchet.RatchetState(
        s1_max=318, s3_max_pct=97, allowed_dated_headings=("4be1aaaaaaaa",), reorg=RID
    )
    data = ratchet.ratchet_dumps(state)
    assert data.endswith(b"\n")
    obj = json.loads(data)
    assert obj == {
        "schema": "declutter.ratchet/1",
        "S1_max": 318,
        "S3_max_pct": 97,
        "slack_lines": 5,
        "zone_max_lines": 150,
        "forbid_changelog_headings": True,
        "allowed_dated_headings": ["4be1aaaaaaaa"],
        "reorg": RID,
    }
    assert data == (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()

    pins = (
        Pin("P-001", RID, "U13", "U13.b3", "AGENTS.md", "copy", ("publishable",), 1, H64),
        Pin("P-023", RID, "U08", None, "AGENTS.md", "unit", ("publishable",), 25, "b" * 64),
    )
    pdata = ratchet.pins_dumps(pins)
    pobj = json.loads(pdata)
    assert pobj["schema"] == "declutter.pins/1"
    assert pobj["pins"][1]["block"] is None
    assert pobj["pins"][0]["categories"] == ["publishable"]

    project = make_project({})
    ctl = project.root / ".declutter"
    ctl.mkdir()
    (project.root / ratchet.RATCHET_REL).write_bytes(data)
    (project.root / ratchet.PINS_REL).write_bytes(pdata)
    assert ratchet.ratchet_loads((project.root / ratchet.RATCHET_REL).read_bytes()) == state
    assert ratchet.pins_loads((project.root / ratchet.PINS_REL).read_bytes()) == pins
    assert ratchet.ratchet_dumps(ratchet.ratchet_loads(data)) == data
    assert ratchet.pins_dumps(ratchet.pins_loads(pdata)) == pdata


def _ratchet_obj() -> dict[str, object]:
    return json.loads(ratchet.ratchet_dumps(ratchet.RatchetState(s1_max=1, s3_max_pct=2)))  # type: ignore[no-any-return]


def _pins_obj() -> dict[str, object]:
    pin = Pin("P-001", RID, "U1", None, "AGENTS.md", "unit", ("git",), 1, H64)
    return json.loads(ratchet.pins_dumps([pin]))  # type: ignore[no-any-return]


def _enc(obj: object) -> bytes:
    return json.dumps(obj).encode()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.update(schema="declutter.ratchet/2"),
        lambda o: o.pop("S1_max"),
        lambda o: o.update(extra=1),
        lambda o: o.update(S1_max=-1),
        lambda o: o.update(S1_max=True),
        lambda o: o.update(S3_max_pct=1.5),
        lambda o: o.update(forbid_changelog_headings="yes"),
        lambda o: o.update(allowed_dated_headings="x"),
        lambda o: o.update(allowed_dated_headings=["NOTHEX"]),
        lambda o: o.update(reorg=5),
        lambda o: o.update(reorg="yesterday"),
    ],
)
def test_invalid_ratchet_schema_raises(mutate: object) -> None:
    obj = _ratchet_obj()
    mutate(obj)  # type: ignore[operator]
    with pytest.raises(ratchet.RatchetSchemaError):
        ratchet.ratchet_loads(_enc(obj))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.update(schema="declutter.reorg/1"),
        lambda o: o.update(pins={}),
        lambda o: o["pins"][0].update(role="other"),
        lambda o: o["pins"][0].update(id="P1"),
        lambda o: o["pins"][0].update(sha256="xyz"),
        lambda o: o["pins"][0].update(lines=0),
        lambda o: o["pins"][0].update(block=3),
        lambda o: o["pins"][0].update(file=""),
        lambda o: o["pins"][0].update(categories=[1]),
        lambda o: o["pins"][0].pop("reorg"),
        lambda o: o["pins"][0].update(more=True),
        lambda o: o["pins"].append(dict(o["pins"][0])),  # duplicate id
    ],
)
def test_invalid_pins_schema_raises(mutate: object) -> None:
    obj = _pins_obj()
    mutate(obj)  # type: ignore[operator]
    with pytest.raises(ratchet.RatchetSchemaError):
        ratchet.pins_loads(_enc(obj))


@pytest.mark.parametrize(
    "data", [b"", b"\xff\xfe", b"[1]", b"not json", b'{"schema": 1, "schema": 2}']
)
def test_invalid_schema_raises(data: bytes) -> None:
    with pytest.raises(ratchet.RatchetSchemaError):
        ratchet.ratchet_loads(data)
    with pytest.raises(ratchet.RatchetSchemaError):
        ratchet.pins_loads(data)


# --------------------------------------------------------------------------- log


def test_log_line_format_and_events() -> None:
    a = ratchet.log_line(
        "baseline", date="2026-11-04", reorg=RID, old=None, new={"S1_max": 318}, reason="apply"
    )
    b = ratchet.log_line(
        "unpin",
        date="2026-11-05",
        reorg=RID,
        old="a" * 64,
        new=None,
        reason="moved\nout",
        pin="P-003",
    )
    assert a.endswith(b"\n") and a.count(b"\n") == 1
    assert b.count(b"\n") == 1  # an embedded newline is escaped
    assert json.loads(a) == {
        "event": "baseline",
        "date": "2026-11-04",
        "reorg": RID,
        "old": None,
        "new": {"S1_max": 318},
        "reason": "apply",
    }
    assert (
        a
        == (
            json.dumps(json.loads(a), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
    )
    events = ratchet.log_events(a + b)
    assert [e["event"] for e in events] == ["baseline", "unpin"]
    assert events[1]["pin"] == "P-003" and events[1]["reason"] == "moved\nout"
    assert ratchet.log_events(b"") == ()
    assert len(ratchet.log_events(a + b.rstrip(b"\n"))) == 2  # no final newline
    with pytest.raises(ValueError):
        ratchet.log_line("delete", date="2026-11-04", reorg=RID, old=None, new=None, reason="")
    for bad in (b"not json\n", b"[1]\n", b'{"event": "other"}\n', b"\xff\n"):
        with pytest.raises(ratchet.RatchetSchemaError):
            ratchet.log_events(a + bad)


def test_released_by_unpin_or_repin() -> None:
    def ev(event: str, reorg: str, pin: str | None = None) -> bytes:
        return ratchet.log_line(
            event, date="2026-11-04", reorg=reorg, old=None, new=None, reason="r", pin=pin
        )

    log = ratchet.log_events(
        ev("baseline", "20261001-0900")
        + ev("unpin", "20261001-0900", "P-002")
        + ev("baseline", RID)
        + ev("repin", RID, "P-004")
        + ev("relax", RID)
    )
    assert ratchet.released("P-002", log)
    assert ratchet.released("P-004", log)
    assert not ratchet.released("P-001", log)
    # With the pin's reorg: only events after that reorg's baseline count.
    assert not ratchet.released("P-002", log, reorg=RID)  # unpinned before a later reorg
    assert ratchet.released("P-002", log, reorg="20261001-0900")
    assert ratchet.released("P-004", log, reorg=RID)
    assert not ratchet.released("P-004", log, reorg="20251231-2359")  # no such baseline
    assert not ratchet.released("P-002", ())


# --------------------------------------------------------------------------- .gitignore


def test_gitignore_final_create_and_extend() -> None:
    assert ratchet.GITIGNORE_FINAL == b"*\n!.gitignore\n!ratchet.json\n!pins.json\n!ratchet.log\n"
    assert ratchet.gitignore_final(None) == ratchet.GITIGNORE_FINAL
    assert ratchet.gitignore_final(b"*\n") == ratchet.GITIGNORE_FINAL
    assert ratchet.gitignore_final(b"*") == ratchet.GITIGNORE_FINAL
    assert ratchet.gitignore_final(ratchet.GITIGNORE_FINAL) == ratchet.GITIGNORE_FINAL
    prev = b"# mine\n*\n!pins.json\n"
    out = ratchet.gitignore_final(prev)
    assert out.startswith(prev)
    assert out == prev + b"!.gitignore\n!ratchet.json\n!ratchet.log\n"
    # A negation before the last "*" does not count: "*" re-ignores it.
    early = b"!pins.json\n*\n"
    assert (
        ratchet.gitignore_final(early)
        == early + b"!.gitignore\n!ratchet.json\n!pins.json\n!ratchet.log\n"
    )
    with pytest.raises(ValueError):
        ratchet.gitignore_final(b"*.log\n")
