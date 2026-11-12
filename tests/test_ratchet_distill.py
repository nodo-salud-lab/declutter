"""Ratchet changes of fase 2b (spec §7.1, §7.3, §7.5, §7.6, §10.2): pin roles ``invariant``
and ``card``, the ``distill`` and ``tighten`` events, ``pin_released`` with ``pins`` and
the fail-closed reading of a 0.2.0 reader (item 20). Synthetic data only."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from declutter_context import ratchet
from declutter_context.model import Pin

RID = "20261104-1530"
H = "b" * 64


def _pin(pid: str, role: str = "copy", file: str = "AGENTS.md") -> Pin:
    return Pin(
        id=pid,
        reorg=RID,
        unit="U02",
        block="U02.b1" if role == "copy" else None,
        file=file,
        role=role,
        categories=("secrets",),
        lines=1,
        sha256=H,
    )


def _baseline() -> bytes:
    return ratchet.log_line("baseline", date="2026-11-04", reorg=RID, old=None, new={}, reason="r")


def _distill(old: list[str], new: list[str]) -> bytes:
    return ratchet.log_line(
        "distill",
        date="2026-11-05",
        reorg=RID,
        old={"pins": [{"id": x, "block": "U02.b1", "sha256": H, "lines": 1} for x in old]},
        new={"pins": new},
        reason="motivo",
        extra={"distill": f"{RID}/D-001", "card": "D-001", "unit": "U02", "placements": []},
    )


def test_new_roles_and_events_are_accepted() -> None:
    assert {"invariant", "card"} <= ratchet.PIN_ROLES
    assert {"distill", "tighten"} <= ratchet.LOG_EVENTS
    pins = (_pin("P-001"), _pin("P-002", "invariant"), _pin("P-003", "card", "docs/x.md"))
    assert ratchet.pins_loads(ratchet.pins_dumps(pins)) == pins
    log = _baseline() + _distill(["P-001"], ["P-002", "P-003"])
    assert [e["event"] for e in ratchet.log_events(log)] == ["baseline", "distill"]


def test_log_line_extra_only_for_distill_and_tighten() -> None:
    line = ratchet.log_line(
        "tighten",
        date="2026-11-05",
        reorg=RID,
        old={"S1_max": 5},
        new={"S1_max": 4},
        reason="r",
        extra={"distill": f"{RID}/D-001", "file_sha256": H},
    )
    obj = json.loads(line)
    assert obj["file_sha256"] == H and obj["distill"] == f"{RID}/D-001"
    assert line == (json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with pytest.raises(ValueError):
        ratchet.log_line(
            "unpin", date="d", reorg=RID, old=None, new=None, reason="r", extra={"x": 1}
        )
    with pytest.raises(ValueError):  # a base key cannot be replaced
        ratchet.log_line(
            "distill", date="d", reorg=RID, old=None, new=None, reason="r", extra={"reason": "x"}
        )


def test_distill_releases_only_with_its_new_pins() -> None:
    copy, inv, card = _good()
    events = _log(_ev([_old(copy)], ["P-002", "P-003"]))
    assert ratchet.pin_released(copy, events, (copy, inv, card))
    assert not ratchet.pin_released(copy, events, (copy, inv))  # card pin missing
    assert not ratchet.pin_released(copy, events, (copy,))
    assert not ratchet.pin_released(_pin("P-009"), events, (copy, inv, card))  # not named


def test_distill_before_the_last_baseline_does_not_release() -> None:
    copy, inv, card = _good()
    events = (*_log(_ev([_old(copy)], ["P-002", "P-003"])), *_log())
    assert not ratchet.pin_released(copy, events, (copy, inv, card))


def test_malformed_distill_new_pins_never_release() -> None:
    copy = _pin("P-001")
    line = ratchet.log_line(
        "distill",
        date="d",
        reorg=RID,
        old={"pins": [{"id": "P-001"}]},
        new={"pins": []},
        reason="r",
    )
    events = ratchet.log_events(_baseline() + line)
    assert not ratchet.pin_released(copy, events, (copy,))


def test_repin_rule_still_holds() -> None:
    pin = _pin("P-001")
    repin = ratchet.log_line(
        "repin",
        date="d",
        reorg=RID,
        old={"sha256": "c" * 64, "lines": 1},
        new={"sha256": H, "lines": 1},
        reason="r",
        pin="P-001",
    )
    events = ratchet.log_events(_baseline() + repin)
    assert not ratchet.pin_released(pin, events, (pin,))


# --------------------------------------------------------------------------- 0.2.0 reader


_V020_ROLES = frozenset({"copy", "l1_block", "unit"})
_V020_EVENTS = frozenset({"baseline", "relax", "unpin", "repin"})
_PIN_ID = re.compile(r"^P-(\d{3,})$")


def _v020_pins_loads(b: bytes) -> list[dict[str, Any]]:
    """A frozen copy of the 0.2.0 ``pins_loads`` checks that matter here: closed roles."""
    obj = json.loads(b.decode("utf-8"))
    if obj.get("schema") != "declutter.pins/1":
        raise ratchet.RatchetSchemaError("$.schema", "unknown schema")
    out: list[dict[str, Any]] = []
    for i, p in enumerate(obj["pins"]):
        if p["role"] not in _V020_ROLES:
            raise ratchet.RatchetSchemaError(f"$.pins[{i}].role", "value not allowed")
        if not _PIN_ID.match(p["id"]):
            raise ratchet.RatchetSchemaError(f"$.pins[{i}].id", "bad format")
        out.append(p)
    return out


def _v020_log_events(data: bytes) -> list[dict[str, Any]]:
    out = []
    for n, raw in enumerate(data.split(b"\n"), start=1):
        if raw:
            o = json.loads(raw)
            if o.get("event") not in _V020_EVENTS:
                raise ratchet.RatchetSchemaError(f"line {n}", "unknown event")
            out.append(o)
    return out


def test_a_020_reader_fails_closed_on_the_new_values() -> None:
    pins = (_pin("P-001"), _pin("P-002", "invariant"))
    with pytest.raises(ratchet.RatchetSchemaError) as exc:
        _v020_pins_loads(ratchet.pins_dumps(pins))
    assert exc.value.where == "$.pins[1].role"
    with pytest.raises(ratchet.RatchetSchemaError):
        _v020_log_events(_baseline() + _distill(["P-001"], ["P-002"]))
    assert _v020_pins_loads(ratchet.pins_dumps(pins[:1]))  # the old shape still reads


# --------------------------------------------------------------------------- forged releases


def _ev(
    old: list[dict[str, object]],
    new: list[str],
    *,
    unit: str = "U02",
    reorg: str = RID,
    entry: str = "e" * 64,
    card: str = "d" * 64,
) -> dict[str, object]:
    line = ratchet.log_line(
        "distill",
        date="d",
        reorg=reorg,
        old={"pins": old, "card_sha256": "0" * 64},
        new={
            "pins": new,
            "entry_sha256": entry,
            "entry_lines": 1,
            "card_sha256": card,
            "card_lines": 1,
        },
        reason="r",
        extra={"distill": f"{RID}/D-001", "card": "D-001", "unit": unit},
    )
    return dict(ratchet.log_events(line)[0])


def _good() -> tuple[Pin, Pin, Pin]:
    copy = _pin("P-001")
    inv = Pin("P-002", RID, "U02", None, "AGENTS.md", "invariant", ("secrets",), 1, "e" * 64)
    card = Pin("P-003", RID, "U02", None, "docs/c.md", "card", ("secrets",), 1, "d" * 64)
    return copy, inv, card


def _log(*events: dict[str, object]) -> tuple[dict[str, object], ...]:
    return (dict(ratchet.log_events(_baseline())[0]), *events)


def _old(p: Pin) -> dict[str, object]:
    return {"id": p.id, "block": p.block, "sha256": p.sha256, "lines": p.lines}


def test_valid_distill_releases() -> None:
    copy, _inv, _card = _good()
    assert ratchet.pin_released(copy, _log(_ev([_old(copy)], ["P-002", "P-003"])), _good())


def test_self_referencing_distill_does_not_release() -> None:
    copy, _inv, _card = _good()
    assert not ratchet.pin_released(copy, _log(_ev([_old(copy)], ["P-001"])), (copy,))


def test_distill_reusing_other_pins_or_other_unit_does_not_release() -> None:
    copy, _inv, _card = _good()
    other = Pin("P-009", RID, "U07", "U07.b1", "AGENTS.md", "copy", (), 1, H)
    pins = (*_good(), other)
    # Another unit's copy named with this distill's invariant/card pins.
    assert not ratchet.pin_released(other, _log(_ev([_old(other)], ["P-002", "P-003"])), pins)
    # Event of another unit.
    ev = _ev([_old(copy)], ["P-002", "P-003"], unit="U07")
    assert not ratchet.pin_released(copy, _log(ev), pins)


def test_distill_never_releases_unit_or_l1_block_pins() -> None:
    _copy, inv, card = _good()
    for role in ("unit", "l1_block"):
        p = Pin("P-001", RID, "U02", None, "AGENTS.md", role, (), 1, H)
        assert not ratchet.pin_released(p, _log(_ev([_old(p)], ["P-002", "P-003"])), (p, inv, card))


def test_distill_with_wrong_shas_or_roles_does_not_release() -> None:
    copy, _inv, _card = _good()
    pins = _good()
    bad_old = {**_old(copy), "sha256": "f" * 64}
    assert not ratchet.pin_released(copy, _log(_ev([bad_old], ["P-002", "P-003"])), pins)
    assert not ratchet.pin_released(
        copy, _log(_ev([_old(copy)], ["P-002", "P-003"], entry="1" * 64)), pins
    )
    assert not ratchet.pin_released(copy, _log(_ev([_old(copy)], ["P-002", "P-002"])), pins)
    other_reorg = _ev([_old(copy)], ["P-002", "P-003"], reorg="20261001-0900")
    assert not ratchet.pin_released(copy, _log(other_reorg), pins)


def test_released_by_id_ignores_distill() -> None:
    copy, _inv, _card = _good()
    assert not ratchet.released("P-001", _log(_ev([_old(copy)], ["P-002", "P-003"])), reorg=RID)
