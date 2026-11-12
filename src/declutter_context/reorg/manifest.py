"""Manifest ``declutter.reorg/1``, the run folder, ``plan.md`` and ``report.json`` (spec §6).

Part 1 (T06): the schema as frozen dataclasses with a deterministic, strictly validated
JSON form (spec §6.2, §5.8 for overrides), the run folder ``.declutter/reorg/<id>/``
with its ``.gitignore`` guard and byte-exact snapshot (spec §6.1), the ``placement.json``
form (spec §6.3), the choice of the destination path set (spec §9) and the two
metadata-only reports (spec §6.1, §8 G6).

Part 2 (T12): the orchestrator of ``declutter reorg plan``, ``check`` and ``set``
(:func:`plan_project`, :func:`check_run`, :func:`set_override`), the validity rules of
spec §6.4 (:func:`validate`) and the Codex levers of spec §5.6.5 (:func:`levers`).

G6: ``plan.md`` and ``report.json`` carry ids, ranges, counts, bytes, categories, keys
and headings cut to 60 characters, never other source text. Validation errors name the
offending field path, never its value.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from .. import __version__, codexcfg, fsutil, loadset, ratchet
from ..model import Config, Decision, Pin, Placement, Project, SourceDoc, Unit
from . import Issue, ReorgError, classify, gitops, mdblocks, projection, resolve_code
from .classify import BlockOverride, ClassifyContext, CopySpec, Overrides, UnitOverride
from .lexicon import build_lexicon, scan_secrets

if TYPE_CHECKING:  # build imports this module: it is imported lazily at run time
    from .build import BuildInput, BuildResult

__all__ = [
    "DESTS_FIXED",
    "GITIGNORE_REL",
    "PATHS",
    "REPORT_SCHEMA",
    "RUN_DIR",
    "SCHEMA",
    "BlockEntry",
    "BlockOverride",
    "Budgets",
    "CheckContext",
    "GitInfo",
    "Manifest",
    "ManifestError",
    "Output",
    "PlanOutcome",
    "PlanRequest",
    "SourceEntry",
    "UnitEntry",
    "UnitOverride",
    "check_run",
    "choose_paths_lang",
    "dumps",
    "ensure_gitignore",
    "levers",
    "load_run",
    "loads",
    "outcome_report",
    "pending_units",
    "placements_dumps",
    "placements_loads",
    "plan_md",
    "plan_project",
    "report_dict",
    "report_dumps",
    "run_dir",
    "run_id",
    "set_exit",
    "set_override",
    "truncate60",
    "validate",
    "write_snapshot",
]

SCHEMA = "declutter.reorg/1"
REPORT_SCHEMA = "declutter.reorg-report/1"
DESTS_FIXED: frozenset[str] = frozenset({"L1", "L1c", "L4", "L5t", "L5m"})  # plus "L2:<dir>"
RUN_DIR = ".declutter/reorg"
CONTROL_DIR = ".declutter"
GITIGNORE_REL = ".declutter/.gitignore"

LANGS = frozenset({"es", "en"})
EOLS = frozenset({"lf", "crlf", "mixed", "none"})
CONFIDENCES = frozenset({"high", "medium", "low"})
PINS = frozenset({"block", "subsection", "list_group"})
COPY_TARGETS = frozenset({"trampas", "card"})
OUTPUT_MODES = frozenset({"created", "extended", "rewritten", "control"})
UNIT_FIELDS = frozenset({"dest", "card", "keep", "accept"})
PLACEMENT_ROLES = frozenset(
    {"primary", "copy", "generated", "marker", "extended_prefix", "control"}
)

_ID_RE = re.compile(r"^\d{8}-\d{4}(?:-[1-9]\d*)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ZONE_RE = re.compile(r"^[^/\\\s.][^/\\]*$")
_POINTER_RE = re.compile(r"^(?:root|zone:(?P<z1>.+)|root\+zone:(?P<z2>.+))$")

# Destination path sets (spec §9 table). Directory roles end without "/".
PATHS: Mapping[str, Mapping[str, str]] = {
    "state": {"es": "ESTADO.md", "en": "STATE.md"},
    "history_dir": {"es": "docs/historial", "en": "docs/history"},
    "history_undated": {"es": "docs/historial/sin-fecha.md", "en": "docs/history/undated.md"},
    "history_index": {"es": "docs/historial/INDICE.md", "en": "docs/history/INDEX.md"},
    "decisions_dir": {"es": "docs/decisiones", "en": "docs/decisions"},
    "decisions_index": {"es": "docs/decisiones/INDICE.md", "en": "docs/decisions/INDEX.md"},
    "trampas": {"es": "docs/trampas.md", "en": "docs/pitfalls.md"},
    "mapa": {"es": "docs/mapa.md", "en": "docs/map.md"},
}

# The paths whose existence decides the column (spec §9, "Regla de elección").
_DETECT: Mapping[str, tuple[str, ...]] = {
    lang: (
        PATHS["state"][lang],
        PATHS["history_dir"][lang],
        PATHS["decisions_dir"][lang],
        PATHS["trampas"][lang],
        PATHS["mapa"][lang],
    )
    for lang in ("es", "en")
}


# --------------------------------------------------------------------------- schema types


@dataclass(frozen=True)
class Budgets:
    l1_max_bytes: int
    l1_max_lines: int
    zone_max_lines: int
    codex_budget_bytes: int
    codex_warn_pct: int


@dataclass(frozen=True)
class GitInfo:
    enabled: bool
    base_branch: str | None
    base_commit: str | None


@dataclass(frozen=True)
class SourceEntry:
    path: str
    sha256: str
    bytes: int
    lines: int
    bom: bool
    eol: str
    final_newline: bool


@dataclass(frozen=True)
class BlockEntry:
    id: str
    key: str
    kind: str
    lines: tuple[int, int]
    sha256: str
    guard: tuple[str, ...]  # sorted categories
    pin: str | None  # "block" | "subsection" | "list_group" | None
    pin_lines: tuple[int, int] | None  # serialized as [] when None
    copy_to: tuple[str, ...]  # subset of ("card", "trampas"), sorted
    guard_ack: str  # "" when none


@dataclass(frozen=True)
class UnitEntry:
    id: str
    source: str
    heading: str
    heading_key: str
    lines: tuple[int, int]
    sha256: str
    dest: str
    file: str
    card: bool
    card_id: str | None
    pointer: str | None
    confidence: str
    protected: bool
    needs_confirmation: bool
    imports: int
    reasons: tuple[str, ...]
    blocks: tuple[BlockEntry, ...]


@dataclass(frozen=True)
class Output:
    path: str
    mode: str  # "created" | "extended" | "rewritten" | "control"


@dataclass(frozen=True)
class Manifest:
    schema: str
    id: str
    created: str
    tool_version: str
    lang: str
    paths_lang: str
    budgets: Budgets
    git: GitInfo
    sources: tuple[SourceEntry, ...]
    units: tuple[UnitEntry, ...]
    outputs: tuple[Output, ...]
    overrides: tuple[UnitOverride | BlockOverride, ...]
    projection: Mapping[str, object]
    warnings: tuple[str, ...]
    symlink_claude: bool = False  # conservative addition (plan, reading L7)


class ManifestError(Exception):
    """Schema or shape error. Carries the field path only, never a value."""

    def __init__(self, where: str, what: str) -> None:
        super().__init__(f"{where}: {what}")
        self.where = where
        self.what = what

    @property
    def issue(self) -> Issue:
        return Issue(code=4, key="manifest_invalid", detail=f"{self.where}: {self.what}")


def valid_dest(dest: str) -> bool:
    """Shape of ``dest`` (spec §6.2). Whether an ``L2:<dir>`` exists is checked by ``check``."""
    if dest in DESTS_FIXED:
        return True
    return dest.startswith("L2:") and _ZONE_RE.match(dest[3:]) is not None


def _valid_pointer(p: str) -> bool:
    m = _POINTER_RE.match(p)
    if m is None:
        return False
    zone = m.group("z1") or m.group("z2")
    return zone is None or _ZONE_RE.match(zone) is not None


# --------------------------------------------------------------------------- serialization


def _json_bytes(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _range(r: tuple[int, int] | None) -> list[int]:
    return [] if r is None else [r[0], r[1]]


def _block_obj(b: BlockEntry) -> dict[str, object]:
    return {
        "id": b.id,
        "key": b.key,
        "kind": b.kind,
        "lines": _range(b.lines),
        "sha256": b.sha256,
        "guard": list(b.guard),
        "pin": b.pin,
        "pin_lines": _range(b.pin_lines),
        "copy_to": list(b.copy_to),
        "guard_ack": b.guard_ack,
    }


def _unit_obj(u: UnitEntry) -> dict[str, object]:
    return {
        "id": u.id,
        "source": u.source,
        "heading": u.heading,
        "heading_key": u.heading_key,
        "lines": _range(u.lines),
        "sha256": u.sha256,
        "dest": u.dest,
        "file": u.file,
        "card": u.card,
        "card_id": u.card_id,
        "pointer": u.pointer,
        "confidence": u.confidence,
        "protected": u.protected,
        "needs_confirmation": u.needs_confirmation,
        "imports": u.imports,
        "reasons": list(u.reasons),
        "blocks": [_block_obj(b) for b in u.blocks],
    }


def _override_obj(o: UnitOverride | BlockOverride) -> dict[str, object]:
    if isinstance(o, BlockOverride):
        return {"block_key": o.block_key, "guard_ack": o.guard_ack}
    return {"heading_key": o.heading_key, "set": {o.field: o.value}, "reason": o.reason}


def _source_obj(s: SourceEntry) -> dict[str, object]:
    return {
        "path": s.path,
        "sha256": s.sha256,
        "bytes": s.bytes,
        "lines": s.lines,
        "bom": s.bom,
        "eol": s.eol,
        "final_newline": s.final_newline,
    }


def to_obj(m: Manifest) -> dict[str, object]:
    return {
        "schema": m.schema,
        "id": m.id,
        "created": m.created,
        "tool_version": m.tool_version,
        "lang": m.lang,
        "paths_lang": m.paths_lang,
        "budgets": {
            "l1_max_bytes": m.budgets.l1_max_bytes,
            "l1_max_lines": m.budgets.l1_max_lines,
            "zone_max_lines": m.budgets.zone_max_lines,
            "codex_budget_bytes": m.budgets.codex_budget_bytes,
            "codex_warn_pct": m.budgets.codex_warn_pct,
        },
        "git": {
            "enabled": m.git.enabled,
            "base_branch": m.git.base_branch,
            "base_commit": m.git.base_commit,
        },
        "sources": [_source_obj(s) for s in m.sources],
        "units": [_unit_obj(u) for u in m.units],
        "outputs": [{"path": o.path, "mode": o.mode} for o in m.outputs],
        "overrides": [_override_obj(o) for o in m.overrides],
        "projection": dict(m.projection),
        "warnings": list(m.warnings),
        "symlink_claude": m.symlink_claude,
    }


def dumps(m: Manifest) -> bytes:
    """Deterministic JSON (spec §6.2): sorted keys, indent 2, UTF-8, trailing newline."""
    return _json_bytes(to_obj(m))


# --------------------------------------------------------------------------- strict reader


def _no_dup_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ManifestError("$", "duplicate key")
        out[k] = v
    return out


def _parse_json(data: bytes) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError("$", "not UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_no_dup_keys)
    except ManifestError:
        raise
    except (ValueError, RecursionError):
        raise ManifestError("$", "not JSON") from None


def _obj(
    v: object, where: str, required: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise ManifestError(where, "not an object")
    keys = set(v)
    missing = required - keys
    if missing:
        raise ManifestError(where, "missing " + ",".join(sorted(missing)))
    extra = keys - required - optional
    if extra:
        raise ManifestError(where, "unknown key")
    return v


def _str(v: object, where: str) -> str:
    if not isinstance(v, str):
        raise ManifestError(where, "not a string")
    return v


def _opt_str(v: object, where: str) -> str | None:
    return None if v is None else _str(v, where)


def _int(v: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ManifestError(where, "not an integer")
    if v < minimum:
        raise ManifestError(where, "out of range")
    return v


def _bool(v: object, where: str) -> bool:
    if not isinstance(v, bool):
        raise ManifestError(where, "not a boolean")
    return v


def _enum(v: object, where: str, allowed: frozenset[str]) -> str:
    s = _str(v, where)
    if s not in allowed:
        raise ManifestError(where, "value not allowed")
    return s


def _sha(v: object, where: str) -> str:
    s = _str(v, where)
    if not _SHA_RE.match(s):
        raise ManifestError(where, "not a sha256")
    return s


def _list(v: object, where: str) -> list[Any]:
    if not isinstance(v, list):
        raise ManifestError(where, "not a list")
    return v


def _strs(v: object, where: str) -> tuple[str, ...]:
    return tuple(_str(x, f"{where}[{i}]") for i, x in enumerate(_list(v, where)))


def _range_of(v: object, where: str) -> tuple[int, int]:
    items = _list(v, where)
    if len(items) != 2:
        raise ManifestError(where, "not a range")
    a = _int(items[0], where, minimum=1)
    b = _int(items[1], where, minimum=1)
    if b < a:
        raise ManifestError(where, "reversed range")
    return (a, b)


def _opt_range(v: object, where: str) -> tuple[int, int] | None:
    if isinstance(v, list) and not v:
        return None
    return _range_of(v, where)


_BLOCK_KEYS = frozenset(
    {"id", "key", "kind", "lines", "sha256", "guard", "pin", "pin_lines", "copy_to", "guard_ack"}
)
_UNIT_KEYS = frozenset(
    {
        "id",
        "source",
        "heading",
        "heading_key",
        "lines",
        "sha256",
        "dest",
        "file",
        "card",
        "card_id",
        "pointer",
        "confidence",
        "protected",
        "needs_confirmation",
        "imports",
        "reasons",
        "blocks",
    }
)
_SOURCE_KEYS = frozenset({"path", "sha256", "bytes", "lines", "bom", "eol", "final_newline"})
_BUDGET_KEYS = frozenset(
    {"l1_max_bytes", "l1_max_lines", "zone_max_lines", "codex_budget_bytes", "codex_warn_pct"}
)
_TOP_KEYS = frozenset(
    {
        "schema",
        "id",
        "created",
        "tool_version",
        "lang",
        "paths_lang",
        "budgets",
        "git",
        "sources",
        "units",
        "outputs",
        "overrides",
        "projection",
        "warnings",
    }
)


def _block_of(v: object, w: str) -> BlockEntry:
    o = _obj(v, w, _BLOCK_KEYS)
    pin = None if o["pin"] is None else _enum(o["pin"], f"{w}.pin", PINS)
    pin_lines = _opt_range(o["pin_lines"], f"{w}.pin_lines")
    if (pin is None) != (pin_lines is None):
        raise ManifestError(f"{w}.pin_lines", "inconsistent with pin")
    copy_to = _strs(o["copy_to"], f"{w}.copy_to")
    if any(c not in COPY_TARGETS for c in copy_to) or len(set(copy_to)) != len(copy_to):
        raise ManifestError(f"{w}.copy_to", "value not allowed")
    return BlockEntry(
        id=_str(o["id"], f"{w}.id"),
        key=_str(o["key"], f"{w}.key"),
        kind=_str(o["kind"], f"{w}.kind"),
        lines=_range_of(o["lines"], f"{w}.lines"),
        sha256=_sha(o["sha256"], f"{w}.sha256"),
        guard=_strs(o["guard"], f"{w}.guard"),
        pin=pin,
        pin_lines=pin_lines,
        copy_to=copy_to,
        guard_ack=_str(o["guard_ack"], f"{w}.guard_ack"),
    )


def _unit_of(v: object, w: str) -> UnitEntry:
    o = _obj(v, w, _UNIT_KEYS)
    dest = _str(o["dest"], f"{w}.dest")
    if not valid_dest(dest):
        raise ManifestError(f"{w}.dest", "value not allowed")
    pointer = _opt_str(o["pointer"], f"{w}.pointer")
    if pointer is not None and not _valid_pointer(pointer):
        raise ManifestError(f"{w}.pointer", "value not allowed")
    return UnitEntry(
        id=_str(o["id"], f"{w}.id"),
        source=_str(o["source"], f"{w}.source"),
        heading=_str(o["heading"], f"{w}.heading"),
        heading_key=_str(o["heading_key"], f"{w}.heading_key"),
        lines=_range_of(o["lines"], f"{w}.lines"),
        sha256=_sha(o["sha256"], f"{w}.sha256"),
        dest=dest,
        file=_str(o["file"], f"{w}.file"),
        card=_bool(o["card"], f"{w}.card"),
        card_id=_opt_str(o["card_id"], f"{w}.card_id"),
        pointer=pointer,
        confidence=_enum(o["confidence"], f"{w}.confidence", CONFIDENCES),
        protected=_bool(o["protected"], f"{w}.protected"),
        needs_confirmation=_bool(o["needs_confirmation"], f"{w}.needs_confirmation"),
        imports=_int(o["imports"], f"{w}.imports"),
        reasons=_strs(o["reasons"], f"{w}.reasons"),
        blocks=tuple(
            _block_of(b, f"{w}.blocks[{i}]")
            for i, b in enumerate(_list(o["blocks"], f"{w}.blocks"))
        ),
    )


def _source_of(v: object, w: str) -> SourceEntry:
    o = _obj(v, w, _SOURCE_KEYS)
    return SourceEntry(
        path=_str(o["path"], f"{w}.path"),
        sha256=_sha(o["sha256"], f"{w}.sha256"),
        bytes=_int(o["bytes"], f"{w}.bytes"),
        lines=_int(o["lines"], f"{w}.lines"),
        bom=_bool(o["bom"], f"{w}.bom"),
        eol=_enum(o["eol"], f"{w}.eol", EOLS),
        final_newline=_bool(o["final_newline"], f"{w}.final_newline"),
    )


def _override_of(v: object, w: str) -> UnitOverride | BlockOverride:
    if isinstance(v, dict) and "block_key" in v:
        o = _obj(v, w, frozenset({"block_key", "guard_ack"}))
        return BlockOverride(
            block_key=_str(o["block_key"], f"{w}.block_key"),
            guard_ack=_str(o["guard_ack"], f"{w}.guard_ack"),
        )
    o = _obj(v, w, frozenset({"heading_key", "set", "reason"}))
    s = o["set"]
    if not isinstance(s, dict) or len(s) != 1:
        raise ManifestError(f"{w}.set", "needs exactly one field")
    ((name, value),) = s.items()
    if name not in UNIT_FIELDS:
        raise ManifestError(f"{w}.set", "unknown field")
    if name == "dest":
        if not isinstance(value, str) or not valid_dest(value):
            raise ManifestError(f"{w}.set.dest", "value not allowed")
    elif name == "card":
        _bool(value, f"{w}.set.card")
    elif value is not True:  # keep / accept
        raise ManifestError(f"{w}.set.{name}", "must be true")
    return UnitOverride(
        heading_key=_str(o["heading_key"], f"{w}.heading_key"),
        field=name,
        value=value,
        reason=_str(o["reason"], f"{w}.reason"),
    )


def _created(v: object) -> str:
    s = _str(v, "created")
    if not _DATE_RE.match(s):
        raise ManifestError("created", "not an ISO date")
    try:
        _dt.date.fromisoformat(s)
    except ValueError:
        raise ManifestError("created", "not an ISO date") from None
    return s


def from_obj(v: object) -> Manifest:
    o = _obj(v, "$", _TOP_KEYS, frozenset({"symlink_claude"}))
    if o["schema"] != SCHEMA:
        raise ManifestError("schema", "unsupported schema")
    reorg_id = _str(o["id"], "id")
    if not _ID_RE.match(reorg_id):
        raise ManifestError("id", "not a run id")
    b = _obj(o["budgets"], "budgets", _BUDGET_KEYS)
    g = _obj(o["git"], "git", frozenset({"enabled", "base_branch", "base_commit"}))
    projection = o["projection"]
    if not isinstance(projection, dict):
        raise ManifestError("projection", "not an object")
    return Manifest(
        schema=SCHEMA,
        id=reorg_id,
        created=_created(o["created"]),
        tool_version=_str(o["tool_version"], "tool_version"),
        lang=_enum(o["lang"], "lang", LANGS),
        paths_lang=_enum(o["paths_lang"], "paths_lang", LANGS),
        budgets=Budgets(**{k: _int(b[k], f"budgets.{k}") for k in sorted(_BUDGET_KEYS)}),
        git=GitInfo(
            enabled=_bool(g["enabled"], "git.enabled"),
            base_branch=_opt_str(g["base_branch"], "git.base_branch"),
            base_commit=_opt_str(g["base_commit"], "git.base_commit"),
        ),
        sources=tuple(
            _source_of(x, f"sources[{i}]") for i, x in enumerate(_list(o["sources"], "sources"))
        ),
        units=tuple(_unit_of(x, f"units[{i}]") for i, x in enumerate(_list(o["units"], "units"))),
        outputs=tuple(
            Output(
                path=_str(
                    _obj(x, f"outputs[{i}]", frozenset({"path", "mode"}))["path"],
                    f"outputs[{i}].path",
                ),
                mode=_enum(x["mode"], f"outputs[{i}].mode", OUTPUT_MODES),
            )
            for i, x in enumerate(_list(o["outputs"], "outputs"))
        ),
        overrides=tuple(
            _override_of(x, f"overrides[{i}]")
            for i, x in enumerate(_list(o["overrides"], "overrides"))
        ),
        projection=projection,
        warnings=_strs(o["warnings"], "warnings"),
        symlink_claude=_bool(o.get("symlink_claude", False), "symlink_claude"),
    )


def loads(data: bytes) -> Manifest:
    """Parse and validate a manifest; any schema or shape error is a :class:`ManifestError`."""
    return from_obj(_parse_json(data))


# --------------------------------------------------------------------------- placements


_PLACEMENT_REQUIRED = frozenset({"role", "dest", "at", "sha256"})
_PLACEMENT_OPTIONAL = frozenset(
    {"unit", "block", "source", "src", "kind", "backup_sha256", "prefix_newline_added"}
)


def _placement_obj(p: Placement) -> dict[str, object]:
    d: dict[str, object] = {"role": p.role, "dest": p.dest, "at": _range(p.at), "sha256": p.sha256}
    for name in ("unit", "block", "source", "kind", "backup_sha256"):
        value = getattr(p, name)
        if value is not None:
            d[name] = value
    if p.src is not None:
        d["src"] = _range(p.src)
    if p.role == "extended_prefix" or p.prefix_newline_added:
        d["prefix_newline_added"] = p.prefix_newline_added
    return d


def placements_dumps(ps: Sequence[Placement]) -> bytes:
    """``placement.json`` (spec §6.3): a list with only the fields that are set."""
    return _json_bytes([_placement_obj(p) for p in ps])


def _placement_of(v: object, w: str) -> Placement:
    o = _obj(v, w, _PLACEMENT_REQUIRED, _PLACEMENT_OPTIONAL)

    def opt(name: str, conv: Callable[[object, str], str]) -> str | None:
        return conv(o[name], f"{w}.{name}") if name in o else None

    return Placement(
        role=_enum(o["role"], f"{w}.role", PLACEMENT_ROLES),
        dest=_str(o["dest"], f"{w}.dest"),
        at=_range_of(o["at"], f"{w}.at"),
        sha256=_sha(o["sha256"], f"{w}.sha256"),
        unit=opt("unit", _str),
        block=opt("block", _str),
        source=opt("source", _str),
        src=_range_of(o["src"], f"{w}.src") if "src" in o else None,
        kind=opt("kind", _str),
        backup_sha256=opt("backup_sha256", _sha),
        prefix_newline_added=(
            _bool(o["prefix_newline_added"], f"{w}.prefix_newline_added")
            if "prefix_newline_added" in o
            else False
        ),
    )


def placements_loads(data: bytes) -> tuple[Placement, ...]:
    items = _list(_parse_json(data), "$")
    return tuple(_placement_of(x, f"[{i}]") for i, x in enumerate(items))


# --------------------------------------------------------------------------- run folder


def run_dir(root: Path, reorg_id: str) -> Path:
    """``<root>/.declutter/reorg/<id>``; an id that is not ``AAAAMMDD-HHMM[-N]`` is refused."""
    if not _ID_RE.match(reorg_id):
        raise ReorgError([Issue(code=1, key="run_id_invalid")])
    return root / RUN_DIR / reorg_id


def run_id(now: datetime, root: Path) -> str:
    """``now`` as ``AAAAMMDD-HHMM``; ``-2``, ``-3``... when that run folder already exists."""
    base = now.strftime("%Y%m%d-%H%M")
    candidate = base
    n = 1
    while os.path.lexists(run_dir(root, candidate)):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def ensure_gitignore(root: Path) -> Issue | None:
    """Create ``.declutter/.gitignore`` = ``*`` if missing (spec §6.1).

    An existing one must contain a line exactly equal to ``*`` (bytes, no trimming);
    otherwise, or if it cannot be read safely, the result is an issue with code 1 and
    nothing is written.
    """
    path = root / GITIGNORE_REL
    if not os.path.lexists(path):
        try:
            fsutil.atomic_write_bytes(path, b"*\n", root)
        except fsutil.UnsafeWriteError as exc:
            return Issue(code=2, key="unsafe_write", dest=GITIGNORE_REL, detail=exc.reason)
        return None
    try:
        data = fsutil.safe_read_bytes(path, root)
    except fsutil.UnsafeReadError as exc:
        return Issue(code=1, key="gitignore_no_star", dest=GITIGNORE_REL, detail=exc.reason)
    if b"*" in data.split(b"\n"):
        return None
    return Issue(code=1, key="gitignore_no_star", dest=GITIGNORE_REL)


def _safe_rel(rel: str) -> bool:
    p = PurePosixPath(rel)
    return bool(rel) and not p.is_absolute() and "\\" not in rel and ".." not in p.parts


def write_snapshot(run: Path, docs: Sequence[tuple[str, bytes]]) -> None:
    """Write ``original/<path>`` byte for byte for every source (spec §6.1, decision 6).

    ``run`` is ``run_dir(root, id)``; writes are confined to the project root above it
    (``run`` itself may not exist yet) and relative paths may not climb out of ``original/``.
    """
    root = run.parent.parent.parent
    for rel, data in docs:
        if not _safe_rel(rel):
            raise ReorgError([Issue(code=2, key="unsafe_write", source=rel, detail="outside_root")])
        try:
            fsutil.atomic_write_bytes(run / "original" / rel, data, root)
        except fsutil.UnsafeWriteError as exc:
            raise ReorgError(
                [Issue(code=2, key="unsafe_write", source=rel, detail=exc.reason)]
            ) from None


def choose_paths_lang(root: Path, lang: str) -> str:
    """Destination path set (spec §9): the only column with existing paths, else ``lang``."""
    if lang not in LANGS:
        raise ReorgError([Issue(code=1, key="lang_invalid")])
    found = {c for c, rels in _DETECT.items() if any(os.path.lexists(root / r) for r in rels)}
    if len(found) == 2:
        raise ReorgError([Issue(code=1, key="paths_both_langs")])
    return found.pop() if found else lang


# --------------------------------------------------------------------------- reports (G6)


def truncate60(s: str) -> str:
    """At most 60 characters; a cut string ends in "…", which counts inside the 60."""
    return s if len(s) <= 60 else s[:59] + "…"


def _has_reason(o: UnitOverride) -> bool:
    return bool(o.reason.strip())


def pending_units(m: Manifest) -> tuple[str, ...]:
    """Units still waiting for a human decision (spec §5.8, §6.4.15-16), manifest order.

    L1c needs a ``dest: "L1c"`` override with a reason; any other unit that needs
    confirmation needs an ``accept`` or an explicit ``dest``, with a reason.
    """
    out: list[str] = []
    for u in m.units:
        if not u.needs_confirmation or "generated" in u.reasons:
            continue
        ovs = [
            o
            for o in m.overrides
            if isinstance(o, UnitOverride) and o.heading_key == u.heading_key and _has_reason(o)
        ]
        if u.dest == "L1c":
            ok = any(o.field == "dest" and o.value == "L1c" for o in ovs)
        else:
            ok = any(
                (o.field == "dest" and isinstance(o.value, str))
                or (o.field == "accept" and o.value is True)
                for o in ovs
            )
        if not ok:
            out.append(u.id)
    return tuple(out)


def _copies(u: UnitEntry) -> list[dict[str, object]]:
    return [
        {
            "block": b.id,
            "lines": _range(b.pin_lines if b.pin is not None else b.lines),
            "pin": b.pin,
            "categories": list(b.guard),
        }
        for b in u.blocks
        if b.pin is not None or b.copy_to
    ]


def _issue_obj(i: Issue) -> dict[str, object]:
    d: dict[str, object] = {"code": i.code, "key": i.key}
    for name in ("unit", "block", "source", "dest"):
        value = getattr(i, name)
        if value is not None:
            d[name] = value
    for name in ("src", "at"):
        r = getattr(i, name)
        if r is not None:
            d[name] = [r[0], r[1]]
    d["detail"] = i.detail
    return d


def report_dict(
    m: Manifest,
    issues: Sequence[Issue],
    code: int,
    levers: Mapping[str, int],
    *,
    unit_bytes: Mapping[str, int] | None = None,
) -> dict[str, object]:
    """``report.json`` and the ``--json`` output: metadata only (spec §6.1, G6).

    ``unit_bytes`` (unit id -> bytes) comes from the orchestrator, because the manifest
    schema has no per-unit byte count; a unit without it reports ``null``.
    """
    pending = set(pending_units(m))
    sizes = unit_bytes or {}
    units: list[dict[str, object]] = [
        {
            "id": u.id,
            "source": u.source,
            "heading": truncate60(u.heading),
            "lines": _range(u.lines),
            "bytes": sizes.get(u.id),
            "dest": u.dest,
            "file": u.file,
            "card": u.card,
            "card_id": u.card_id,
            "pointer": u.pointer,
            "confidence": u.confidence,
            "protected": u.protected,
            "needs_confirmation": u.needs_confirmation,
            "pending": u.id in pending,
            "imports": u.imports,
            "reasons": list(u.reasons),
            "copies": _copies(u),
            "guard_acks": [b.id for b in u.blocks if b.guard_ack],
        }
        for u in m.units
    ]
    d: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "id": m.id,
        "code": code,
        "lang": m.lang,
        "paths_lang": m.paths_lang,
        "sources": [_source_obj(s) for s in m.sources],
        "units": units,
        "outputs": [{"path": o.path, "mode": o.mode} for o in m.outputs],
        "projection": dict(m.projection),
        "warnings": list(m.warnings),
        "pending_confirmation": [u.id for u in m.units if u.id in pending],
        "issues": [_issue_obj(i) for i in issues],
    }
    if code == 3:
        d["levers"] = dict(levers)
        d["lever_commands"] = {
            "lever_codex_budget": codex_budget_command(
                m.budgets.codex_budget_bytes, levers.get("lever_codex_budget", 0)
            )
        }
    return d


def codex_budget_command(budget: int, excess: int, root: str | None = None) -> str:
    """The concrete action of ``lever_codex_budget``: ``declutter reorg codex-budget`` with a
    ``--bytes`` that fits the largest projected chain (``budget + excess``) plus 10 %."""
    n = codexcfg.suggest_bytes(budget + max(0, excess))
    where = f" -C {shlex.quote(root)}" if root is not None else ""
    return f"declutter reorg codex-budget{where} --bytes {n}"


def report_dumps(d: Mapping[str, object]) -> bytes:
    return _json_bytes(dict(d))


_LABELS: Mapping[str, Mapping[str, str]] = {
    "es": {
        "title": "Plan de reorganización",
        "card": "ficha",
        "copies": "copias",
        "confidence": "confianza",
        "reasons": "razones",
        "pending": "pendiente de aceptar",
        "protected": "protegida",
        "preamble": "(preámbulo)",
    },
    "en": {
        "title": "Reorganization plan",
        "card": "card",
        "copies": "copies",
        "confidence": "confidence",
        "reasons": "reasons",
        "pending": "pending acceptance",
        "protected": "protected",
        "preamble": "(preamble)",
    },
}


def _md_safe(s: str) -> str:
    """Keep a heading on one line inside the checklist item."""
    return s.replace("\r", " ").replace("\n", " ")


def plan_md(m: Manifest, lang: str) -> str:
    """``plan.md``: one checkbox per unit, in manifest order, metadata only (spec §6.1, G6)."""
    lb = _LABELS["en" if lang == "en" else "es"]
    pending = set(pending_units(m))
    out = [f"# {lb['title']} {m.id}", ""]
    for u in m.units:
        heading = _md_safe(truncate60(u.heading)) if u.heading else lb["preamble"]
        parts = [
            f"{u.id} · «{heading}»",
            f"{u.source}:{u.lines[0]}-{u.lines[1]}",
            f"{u.dest} → {u.file}",
        ]
        if u.card:
            parts.append(f"{lb['card']} {u.card_id or '-'}")
        copies = []
        for b in u.blocks:
            if b.pin is None and not b.copy_to:
                continue
            a, z = b.pin_lines if b.pin_lines is not None else b.lines
            copies.append(f"{b.id} ({b.pin or '-'} {a}-{z})")
        if copies:
            parts.append(f"{lb['copies']}: " + ", ".join(copies))
        parts.append(f"{lb['confidence']} {u.confidence}")
        if u.reasons:
            parts.append(f"{lb['reasons']}: " + ", ".join(u.reasons))
        if u.protected:
            parts.append(lb["protected"])
        if u.id in pending:
            parts.append(lb["pending"])
        out.append("- [ ] " + " · ".join(parts))
    out.append("")
    return "\n".join(out)


# =========================================================================== part 2 (T12)
# Orchestration of ``plan`` / ``check`` / ``set`` (spec §6.4, §10.1, §5.8, §13).

AGENTS = "AGENTS.md"
CLAUDE = "CLAUDE.md"
MANIFEST_NAME = "manifest.json"
PLAN_MD_NAME = "plan.md"
REPORT_NAME = "report.json"
LEVER_KEYS: tuple[str, ...] = (
    "lever_review_low",
    "lever_guard_ack",
    "lever_codex_budget",
    "lever_distill",
    "lever_revisit_g2",
)
_FATAL = frozenset({1, 2, 5})
_G3_CATEGORIES = frozenset({"publishable", "device"})
_SUFFIX_RE = re.compile(r"~\d+")


@dataclass(frozen=True)
class CheckContext:
    """What :func:`validate` needs beyond the manifest: the project root, the classifier
    context (top-level dirs, paths), the current sources and the overrides in effect."""

    root: Path
    classify: ClassifyContext
    docs: Mapping[str, SourceDoc]
    overrides: Overrides
    cfg: Config


@dataclass(frozen=True)
class PlanRequest:
    project: Project
    cfg: Config
    lang: str
    now: datetime
    dry_run: bool = False
    extra_sources: tuple[str, ...] = ()
    from_id: str | None = None


@dataclass(frozen=True)
class PlanOutcome:
    manifest: Manifest
    build: BuildResult
    issues: tuple[Issue, ...]
    code: int
    run_dir: Path | None
    levers: Mapping[str, int]
    # Added in T12 (defaults keep the shared contract): what ``validate`` and the reports
    # need to be recomputed by callers and tests.
    units: tuple[Unit, ...] = ()
    decisions: tuple[Decision, ...] = ()
    context: CheckContext | None = None
    unit_bytes: Mapping[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- small helpers


def _sha_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dedupe(issues: Iterable[Issue]) -> tuple[Issue, ...]:
    return tuple(dict.fromkeys(issues))


def _raise_if_fatal(issues: Sequence[Issue]) -> None:
    if any(i.code in _FATAL for i in issues):
        raise ReorgError(_dedupe(issues))


def _reader(root: Path) -> Callable[[str], bytes | None]:
    """Existing files inside ``root``: bytes, ``None`` if absent, code 2 if unsafe."""

    def read(rel: str) -> bytes | None:
        try:
            return fsutil.safe_read_bytes(root / rel, root)
        except fsutil.UnsafeReadError as exc:
            if exc.reason == "missing":
                return None
            raise ReorgError(
                [Issue(code=2, key="unsafe_read", dest=rel, detail=exc.reason)]
            ) from None

    return read


def _source_error_issue(exc: mdblocks.SourceError) -> Issue:
    detail = ""
    if exc.offset is not None:
        detail = f"offset={exc.offset}"
    elif exc.paths:
        detail = ",".join(exc.paths)
    return Issue(
        code=2,
        key=exc.reason,
        source=exc.path or None,
        at=(exc.line, exc.line) if exc.line is not None else None,
        detail=detail,
    )


def _names(folder: Path) -> list[str]:
    try:
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
    except OSError:
        return []


def _card_taken(card_id: str, names: Iterable[str]) -> bool:
    pat = re.compile(rf"^{re.escape(card_id)}(?:-.*)?\.md$")
    return any(pat.match(n) for n in names)


def _explicit_dest(overrides: Iterable[UnitOverride | BlockOverride], key: str) -> bool:
    return any(
        isinstance(o, UnitOverride)
        and o.heading_key == key
        and o.field == "dest"
        and isinstance(o.value, str)
        and o.reason.strip() != ""
        for o in overrides
    )


def _split_overrides(
    ovs: Iterable[UnitOverride | BlockOverride],
) -> Overrides:
    items = list(ovs)
    return Overrides(
        units=tuple(o for o in items if isinstance(o, UnitOverride)),
        blocks=tuple(o for o in items if isinstance(o, BlockOverride)),
    )


def _unit_issue(code: int, key: str, u: UnitEntry, detail: str = "") -> Issue:
    return Issue(code=code, key=key, unit=u.id, source=u.source, src=u.lines, detail=detail)


def _block_issue(key: str, u: UnitEntry, b: BlockEntry, detail: str = "") -> Issue:
    return Issue(
        code=4, key=key, unit=u.id, block=b.id, source=u.source, src=b.lines, detail=detail
    )


# --------------------------------------------------------------------------- §6.4 validity


def _source_rules(m: Manifest, docs: Mapping[str, SourceDoc]) -> list[Issue]:
    """Rules 1 and 2 of spec §6.4: stale sources, unit coverage and unit/block sha."""
    out: list[Issue] = []
    for s in m.sources:
        doc = docs.get(s.path)
        if doc is None or doc.sha256 != s.sha256 or doc.bytes != s.bytes:
            out.append(Issue(code=4, key="check.1", source=s.path, detail=f"plan --from {m.id}"))
            continue
        n = len(doc.lines)
        units = sorted((u for u in m.units if u.source == s.path), key=lambda u: u.lines)
        expected = 1
        for u in units:
            a, b = u.lines
            if a != expected or b > n:
                out.append(_unit_issue(4, "check.2", u, "coverage"))
                expected = max(expected, b + 1)
                continue
            expected = b + 1
            if mdblocks.lines_sha(doc.lines[a - 1 : b]) != u.sha256:
                out.append(_unit_issue(4, "check.2", u, "unit sha256"))
            for blk in u.blocks:
                ba, bb = blk.lines
                if ba < a or bb > b or mdblocks.lines_sha(doc.lines[ba - 1 : bb]) != blk.sha256:
                    out.append(_block_issue("check.2", u, blk, "block sha256"))
        if expected != n + 1:
            out.append(Issue(code=4, key="check.2", source=s.path, detail="coverage"))
    return out


def _contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _zone_imports_agents(root: Path, zone: str) -> bool | None:
    """``None`` if ``<zone>/CLAUDE.md`` does not exist; else whether it imports AGENTS.md."""
    path = root / zone / CLAUDE
    if not os.path.lexists(path):
        return None
    try:
        text = fsutil.safe_read_bytes(path, root).decode("utf-8", errors="replace")
    except fsutil.UnsafeReadError:
        return False
    return any(
        os.path.normpath(imp) == AGENTS
        for imp in loadset.extract_imports(text.replace("\r\n", "\n"))
    )


def _codex_chains(proj: Mapping[str, object]) -> list[tuple[str, int, float]]:
    chains = proj.get("codex")
    out: list[tuple[str, int, float]] = []
    if not isinstance(chains, list):
        return out
    for c in chains:
        if not isinstance(c, dict):
            continue
        name, size, pct = c.get("name"), c.get("bytes"), c.get("pct")
        if isinstance(name, str) and isinstance(size, int) and isinstance(pct, int | float):
            out.append((name, size, float(pct)))
    return out


def validate(
    m: Manifest,
    res: BuildResult,
    units: Sequence[Unit],
    decisions: Sequence[Decision],
    ctx: CheckContext,
    *,
    for_apply: bool,
) -> tuple[Issue, ...]:
    """The validity rules of spec §6.4 over the manifest ``m`` (code 4; rule 9 is code 3).

    Rules 15 and 16 (pending confirmations) are issues only ``for_apply``; ``plan`` reports
    them as the ``pending_confirmation`` warning instead. Issues carry ids, ranges, keys
    and paths, never content.
    """
    del res, units, decisions  # part of the contract; the manifest carries what is checked
    out: list[Issue] = _source_rules(m, ctx.docs)
    top_dirs = ctx.classify.top_dirs
    zones: dict[str, list[UnitEntry]] = {}
    for u in m.units:
        explicit = _explicit_dest(m.overrides, u.heading_key)
        zone_dest = u.dest.startswith("L2:")
        if not valid_dest(u.dest) or (zone_dest and u.dest[3:] not in top_dirs):
            out.append(_unit_issue(4, "check.3", u, "dest"))
        if u.protected and u.dest != "L1":
            out.append(_unit_issue(4, "check.4", u, "protected"))
        enlarged = [
            b.pin_lines
            for b in u.blocks
            if b.pin in ("subsection", "list_group") and b.pin_lines is not None
        ]
        for b in u.blocks:
            if (
                b.guard
                and u.dest != "L1"
                and b.pin is None
                and not any(_contains(r, b.lines) for r in enlarged)
                and not b.guard_ack.strip()
            ):
                out.append(_block_issue("check.5", u, b, ",".join(b.guard)))
            if _G3_CATEGORIES & set(b.guard) and (u.dest == "L1c" or zone_dest):
                out.append(
                    _block_issue("check.6", u, b, ",".join(sorted(_G3_CATEGORIES & set(b.guard))))
                )
        if u.dest == "L1c" and any(b.guard for b in u.blocks):
            out.append(_unit_issue(4, "check.7", u, "S>=1"))
        if u.imports >= 1 and u.dest not in ("L1", "L1c") and not explicit:
            out.append(_unit_issue(4, "check.13", u, f"imports={u.imports}"))
        if u.source == AGENTS and (u.dest == "L1c" or zone_dest) and not explicit:
            out.append(_unit_issue(4, "check.14", u, u.dest))
        if zone_dest:
            zones.setdefault(u.dest[3:], []).append(u)
        if u.dest == "L4" and u.pointer and "zone:" in u.pointer:
            zones.setdefault(u.pointer.split("zone:", 1)[1], []).append(u)

    # 8: reasons (located on the unit or block when the key still names one)
    unit_of = {u.heading_key: u for u in m.units}
    block_of = {b.key: (u, b) for u in m.units for b in u.blocks}
    for o in m.overrides:
        if isinstance(o, UnitOverride) and not o.reason.strip():
            if o.heading_key in unit_of:
                out.append(_unit_issue(4, "check.8", unit_of[o.heading_key], o.field))
            else:
                out.append(Issue(code=4, key="check.8", detail=f"heading_key={o.heading_key}"))
        if isinstance(o, BlockOverride) and len(o.guard_ack.strip()) < classify.GUARD_ACK_MIN:
            if o.block_key in block_of:
                out.append(_block_issue("check.8", *block_of[o.block_key], "guard_ack"))
            else:
                out.append(Issue(code=4, key="check.8", detail=f"block_key={o.block_key}"))

    # 9: Codex budget per chain
    for name, size, pct in _codex_chains(m.projection):
        if pct > 100:
            out.append(
                Issue(
                    code=3,
                    key="check.9",
                    dest=AGENTS if name == "root" else f"{name}/{AGENTS}",
                    detail=(
                        f"chain={name} bytes={size} budget={m.budgets.codex_budget_bytes} "
                        f"pct={pct:.1f}"
                    ),
                )
            )

    # 10: card ids still free
    names = _names(ctx.root / PATHS["decisions_dir"][m.paths_lang])
    for u in m.units:
        if u.card_id is not None and _card_taken(u.card_id, names):
            out.append(_unit_issue(4, "check.10", u, u.card_id))

    # 11: zones whose CLAUDE.md does not import AGENTS.md
    for z in sorted(zones):
        if _zone_imports_agents(ctx.root, z) is False:
            for u in zones[z]:
                out.append(
                    Issue(
                        code=4,
                        key="check.11",
                        unit=u.id,
                        source=u.source,
                        src=u.lines,
                        dest=f"{z}/{CLAUDE}",
                    )
                )

    # 12: ambiguous overrides; keep together with a dest other than L1
    unit_keys = [u.heading_key for u in m.units]
    block_keys = [b.key for u in m.units for b in u.blocks]
    by_key: dict[str, list[UnitOverride]] = {}
    for o in m.overrides:
        if isinstance(o, UnitOverride):
            by_key.setdefault(o.heading_key, []).append(o)
            if unit_keys.count(o.heading_key) > 1:
                out.append(Issue(code=4, key="check.12", detail=f"heading_key={o.heading_key}"))
        elif block_keys.count(o.block_key) > 1:
            out.append(Issue(code=4, key="check.12", detail=f"block_key={o.block_key}"))
    for u in m.units:
        ovs = by_key.get(u.heading_key, [])
        if any(o.field == "keep" and o.value is True for o in ovs) and any(
            o.field == "dest" and o.value != "L1" for o in ovs
        ):
            out.append(_unit_issue(4, "check.12", u, "keep+dest"))

    # 15, 16: pending confirmations
    if for_apply:
        by_id = {u.id: u for u in m.units}
        for uid in pending_units(m):
            u = by_id[uid]
            out.append(_unit_issue(4, "check.15" if u.dest == "L1c" else "check.16", u, u.dest))
    return _dedupe(out)


# --------------------------------------------------------------------------- levers


def levers(m: Manifest, res: BuildResult, proj: projection.Projection) -> dict[str, int]:
    """Bytes of each human lever of spec §5.6.5 for this plan. Never a recommendation.

    ``lever_review_low``: L1 primaries of ``low`` units in AGENTS.md; ``lever_guard_ack``
    and ``lever_distill``: literal G1 copies in AGENTS.md; ``lever_codex_budget``: bytes
    the largest Codex chain exceeds the budget by; ``lever_revisit_g2``: G2 primaries.
    """
    lines = mdblocks.split_lines(res.files.get(AGENTS, b"")).lines
    by_unit = {u.id: u for u in m.units}

    def span(p: Placement) -> int:
        return sum(len(x) + 1 for x in lines[p.at[0] - 1 : p.at[1]])

    low = copies = g2 = 0
    for p in res.placements:
        if p.dest != AGENTS:
            continue
        if p.role == "copy":
            copies += span(p)
        elif p.role == "primary" and p.unit in by_unit:
            u = by_unit[p.unit]
            if u.confidence == "low":
                low += span(p)
            if u.protected:
                g2 += span(p)
    largest = max((c.bytes for c in proj.codex), default=0)
    return {
        "lever_review_low": low,
        "lever_guard_ack": copies,
        "lever_codex_budget": max(0, largest - m.budgets.codex_budget_bytes),
        "lever_distill": copies,
        "lever_revisit_g2": g2,
    }


# --------------------------------------------------------------------------- pipeline


@dataclass(frozen=True)
class _Inputs:
    project: Project
    cfg: Config
    lang: str
    paths_lang: str
    reorg_id: str
    created: str
    today: date
    docs: tuple[SourceDoc, ...]  # source order
    units: tuple[Unit, ...]  # parsed (no features yet)
    symlink_claude: bool
    overrides: tuple[UnitOverride | BlockOverride, ...]
    git: GitInfo
    carried_cards: Mapping[str, str]  # unit id -> card id fixed by an earlier plan
    for_apply: bool
    extra_warnings: tuple[str, ...] = ()
    check_git_outputs: bool = False


def _assign_cards(
    decisions: Sequence[Decision],
    carried: Mapping[str, str],
    names: Sequence[str],
    units: Mapping[str, Unit],
) -> tuple[tuple[Decision, ...], list[Issue]]:
    """Fix ``card_id`` for every L4 unit with a card (spec §9.5).

    Carried ids stay; one that is taken in the decisions folder gives ``check.10`` and a
    fresh one. New ids continue after both the folder's and the carried maximum, so two
    cards never share a number.
    """
    from .build import next_card_number

    nums = [int(c[2:]) for c in carried.values() if re.match(r"^D-\d{3,}$", c)]
    nxt = max(next_card_number(names), max(nums, default=0) + 1)
    out: list[Decision] = []
    issues: list[Issue] = []
    for d in decisions:
        if not (d.card and d.dest == "L4"):
            out.append(replace(d, card_id=None))
            continue
        cid = carried.get(d.unit)
        if cid is not None and _card_taken(cid, names):
            u = units[d.unit]
            issues.append(
                Issue(
                    code=4,
                    key="check.10",
                    unit=u.id,
                    source=u.source,
                    src=(u.start, u.end),
                    detail=cid,
                )
            )
            cid = None
        if cid is None:
            cid = f"D-{nxt:03d}"
            nxt += 1
        out.append(replace(d, card_id=cid))
    return tuple(out), issues


def _block_entries(
    u: Unit, doc: SourceDoc, cps: Sequence[CopySpec], ov: Overrides
) -> tuple[BlockEntry, ...]:
    """Manifest blocks: the G1 pin (and its contiguous range) and the other copy targets."""
    inv = {c.block: c for c in cps if c.unit == u.id and c.target == "invariants"}
    targets: dict[str, set[str]] = {}
    for cp in cps:
        if cp.unit == u.id and cp.target in ("trampas", "card"):
            targets.setdefault(cp.block, set()).add(cp.target)
    out: list[BlockEntry] = []
    for b in u.blocks:
        c = inv.get(b.id)
        out.append(
            BlockEntry(
                id=b.id,
                key=b.key,
                kind=b.kind,
                lines=(b.start, b.end),
                sha256=mdblocks.lines_sha(doc.lines[b.start - 1 : b.end]),
                guard=tuple(sorted(b.categories)),
                pin=c.pin if c is not None else None,
                pin_lines=c.src if c is not None else None,
                copy_to=tuple(sorted(targets.get(b.id, set()))),
                guard_ack=ov.ack(b.key),
            )
        )
    return tuple(out)


def _warnings(
    x: _Inputs,
    units: Sequence[Unit],
    decisions: Sequence[Decision],
    res: BuildResult,
    proj: projection.Projection,
    ratchet_warnings: Sequence[str],
) -> list[str]:
    cfg = x.cfg
    out = list(x.extra_warnings)
    agents = res.files.get(AGENTS, b"")
    n_agents = len(mdblocks.split_lines(agents).lines)
    if len(agents) > cfg.reorg.l1_max_bytes or n_agents > cfg.reorg.l1_max_lines:
        out.append(f"l1_over_budget:bytes={len(agents)},lines={n_agents}")
    for i in projection.codex_warnings(proj, cfg):
        if i.key == "codex_warn":
            out.append(f"codex_warn:{i.dest}")
    for path in sorted(res.files):
        if path.count("/") == 1 and path.endswith("/" + AGENTS):
            n = len(mdblocks.split_lines(res.files[path]).lines)
            if n > cfg.reorg.zone_max_lines:
                out.append(f"zone_over_limit:{path.split('/')[0]}")
    out.extend(projection.import_warnings(units, decisions, cfg))
    docs = {d.path: d for d in x.docs}
    by_unit = {d.unit: d for d in decisions}
    for u in units:
        d = by_unit[u.id]
        if d.file == u.source:
            continue
        lines = docs[u.source].lines[u.start - 1 : u.end]
        n = projection.relative_links_moved(lines, u.source, d.file, x.project.root)
        if n:
            out.append(f"relative_link_moved:{u.id}:{d.file}:{n}")
    out.extend(ratchet_warnings)
    return out


def _read_controls(
    read: Callable[[str], bytes | None],
) -> tuple[ratchet.RatchetState | None, tuple[Pin, ...], bytes | None, bytes | None]:
    issues: list[Issue] = []
    prior_ratchet: ratchet.RatchetState | None = None
    prior_pins: tuple[Pin, ...] = ()
    raw = read(ratchet.RATCHET_REL)
    if raw is not None:
        try:
            prior_ratchet = ratchet.ratchet_loads(raw)
        except ratchet.RatchetSchemaError:
            issues.append(Issue(code=2, key="ratchet_invalid", dest=ratchet.RATCHET_REL))
    raw = read(ratchet.PINS_REL)
    if raw is not None:
        try:
            prior_pins = ratchet.pins_loads(raw)
        except ratchet.RatchetSchemaError:
            issues.append(Issue(code=2, key="ratchet_invalid", dest=ratchet.PINS_REL))
    _raise_if_fatal(issues)
    return prior_ratchet, prior_pins, read(ratchet.LOG_REL), read(GITIGNORE_REL)


def _pipeline(x: _Inputs) -> PlanOutcome:
    """Classify, build, project and validate (steps 8-12 and 14 of the plan order)."""
    from .build import BuildInput, add_controls, build_content

    project, cfg = x.project, x.cfg
    root = project.root
    docs = {d.path: d for d in x.docs}
    read = _reader(root)
    ctx = ClassifyContext(
        lexicon=build_lexicon(cfg.reorg.extra_lexicon),
        top_dirs=classify.top_level_dirs(root),
        paths_lang=x.paths_lang,
        agents_source=AGENTS if AGENTS in docs else None,
        claude_source=CLAUDE if CLAUDE in docs else None,
    )
    ov = _split_overrides(x.overrides)
    units = tuple(classify.features(u, docs[u.source], ctx) for u in x.units)
    decisions = tuple(classify.decide(u, ctx) for u in units)
    decisions, ov_issues = classify.apply_overrides(units, decisions, ov, ctx)
    prior_ratchet, prior_pins, prior_log, prior_gitignore = _read_controls(read)
    allowed = None if prior_ratchet is None else frozenset(prior_ratchet.allowed_dated_headings)
    decisions = classify.demote_new_dated(units, decisions, ov, ctx=ctx, allowed=allowed)

    def build_input(ds: Sequence[Decision]) -> BuildInput:
        return BuildInput(
            root=root,
            reorg_id=x.reorg_id,
            today=x.today,
            lang=x.lang,
            paths_lang=x.paths_lang,
            project_name=root.name,
            docs=docs,
            units=units,
            decisions=tuple(ds),
            copies=classify.copies(units, docs, ds, ov),
            overrides=ov,
            existing=read,
            symlink_claude=x.symlink_claude,
        )

    decisions = classify.apply_budget(
        units,
        docs,
        decisions,
        ov,
        ctx=ctx,
        l1_max_bytes=cfg.reorg.l1_max_bytes,
        agents_size=lambda ds: len(build_content(build_input(ds)).files.get(AGENTS, b"")),
    )
    decisions, card_ov_issues = classify.apply_card_overrides(units, decisions, ov, ctx)
    ov_issues = (*ov_issues, *card_ov_issues)
    decisions = classify.finalize(units, decisions, ov)
    names = _names(root / PATHS["decisions_dir"][x.paths_lang])
    by_id = {u.id: u for u in units}
    decisions, card_issues = _assign_cards(decisions, x.carried_cards, names, by_id)
    inp = build_input(decisions)
    res = build_content(inp)

    zones = sorted(
        classify.top_level_dirs(root)
        | {p.split("/")[0] for p in res.files if p.count("/") == 1 and p.endswith("/" + AGENTS)}
    )
    proj = projection.project_after(project, cfg, res.files, zones)
    res, ratchet_warnings = add_controls(
        res, inp, proj, cfg, prior_ratchet, prior_pins, prior_log, prior_gitignore
    )

    # G5 and git attributes on every existing file this plan would extend or back up.
    fatal: list[Issue] = []
    existing = [p for p in sorted(res.modes) if p not in docs and res.prev_sha.get(p) != "absent"]
    for path in existing:
        data = read(path)
        if data is None:
            continue
        for hit in scan_secrets(path, data):
            fatal.append(
                Issue(code=5, key="secret", dest=path, at=(hit.line, hit.line), detail=hit.category)
            )
    lf_safe: list[str] = []
    if x.check_git_outputs and existing:

        def cr_free(path: str) -> bool:
            # Enmienda 05-11: the current bytes and the new ones, both without \r.
            now, new = read(path), res.files.get(path)
            return now is not None and new is not None and b"\r" not in now + new

        try:
            found = gitops.normalizing(root, existing)
            refused, tolerated = gitops.split_lf_safe(found, existing, cr_free)
            for path, attr, value in refused:
                fatal.append(
                    Issue(code=1, key="git_normalizes", dest=path, detail=f"{attr}={value}")
                )
            lf_safe = [gitops.lf_safe_warning(f) for f in tolerated]
        except gitops.GitError as exc:
            fatal.append(Issue(code=2, key="git_error", detail=str(exc)))
    if x.check_git_outputs:
        # §12.1 and §11.2 step 8: every output must be addable. An ignore rule outside
        # .declutter/.gitignore (for example ``.declutter/`` in the root .gitignore)
        # would make the commit impossible after writing; detect it before anything.
        # The control files are governed by the final .declutter/.gitignore (its ``!``
        # lines), so for them only an exclusion of the folder itself counts.
        try:
            ctl = CONTROL_DIR + "/"
            plain = [p for p in sorted(res.files) if not p.startswith(ctl)]
            for path, where in gitops.ignored(root, plain):
                fatal.append(Issue(code=1, key="output_ignored", dest=path, detail=where))
            if any(p.startswith(ctl) for p in res.files):
                where_dir = gitops.ignored_dir(root, CONTROL_DIR)
                if where_dir is not None:
                    fatal.append(Issue(code=1, key="output_ignored", dest=ctl, detail=where_dir))
        except gitops.GitError as exc:
            fatal.append(Issue(code=2, key="git_error", detail=str(exc)))
    _raise_if_fatal(fatal)

    unit_bytes = {u.id: classify.unit_bytes(docs[u.source], u.start, u.end) for u in units}
    dec = {d.unit: d for d in decisions}
    entries = tuple(
        UnitEntry(
            id=u.id,
            source=u.source,
            heading=u.heading,
            heading_key=u.heading_key,
            lines=(u.start, u.end),
            sha256=mdblocks.lines_sha(docs[u.source].lines[u.start - 1 : u.end]),
            dest=dec[u.id].dest,
            file=dec[u.id].file,
            card=dec[u.id].card,
            card_id=res.card_ids.get(u.id),
            pointer=dec[u.id].pointer,
            confidence=dec[u.id].confidence,
            protected=dec[u.id].protected,
            needs_confirmation=dec[u.id].needs_confirmation,
            imports=u.features.imports,
            reasons=dec[u.id].reasons,
            blocks=_block_entries(u, docs[u.source], inp.copies, ov),
        )
        for u in units
    )
    m = Manifest(
        schema=SCHEMA,
        id=x.reorg_id,
        created=x.created,
        tool_version=__version__,
        lang=x.lang,
        paths_lang=x.paths_lang,
        budgets=Budgets(
            l1_max_bytes=cfg.reorg.l1_max_bytes,
            l1_max_lines=cfg.reorg.l1_max_lines,
            zone_max_lines=cfg.reorg.zone_max_lines,
            codex_budget_bytes=cfg.codex_budget_bytes,
            codex_warn_pct=cfg.reorg.codex_warn_pct,
        ),
        git=x.git,
        sources=tuple(
            SourceEntry(
                path=d.path,
                sha256=d.sha256,
                bytes=d.bytes,
                lines=len(d.lines),
                bom=d.bom,
                eol=d.eol,
                final_newline=d.final_newline,
            )
            for d in x.docs
        ),
        units=entries,
        outputs=tuple(Output(path=p, mode=res.modes[p]) for p in sorted(res.files)),
        overrides=x.overrides,
        projection=proj.to_json(),
        warnings=(),
        symlink_claude=x.symlink_claude,
    )
    warnings = _warnings(x, units, decisions, res, proj, ratchet_warnings)
    warnings += [w for w in dict.fromkeys(lf_safe) if w not in warnings]
    if not x.for_apply:
        pending = pending_units(m)
        if pending:
            warnings.append("pending_confirmation:" + ",".join(pending))
    m = replace(m, warnings=tuple(warnings))

    check_ctx = CheckContext(root=root, classify=ctx, docs=docs, overrides=ov, cfg=cfg)
    # validate re-reports reasons (8) and keep+dest (12) for every override, matched or not.
    from_overrides = [i for i in ov_issues if i.key not in ("check.8", "check.12")]
    issues = _dedupe(
        [
            *from_overrides,
            *card_issues,
            *validate(m, res, units, decisions, check_ctx, for_apply=x.for_apply),
        ]
    )
    code = resolve_code(i.code for i in issues)
    lv = levers(m, res, proj) if code == 3 else {}
    return PlanOutcome(
        manifest=m,
        build=res,
        issues=issues,
        code=code,
        run_dir=None,
        levers=lv,
        units=units,
        decisions=decisions,
        context=check_ctx,
        unit_bytes=unit_bytes,
    )


# --------------------------------------------------------------------------- reports


def outcome_report(out: PlanOutcome) -> dict[str, object]:
    """``report.json`` / ``--json`` of an outcome (metadata only, G6)."""
    return report_dict(out.manifest, out.issues, out.code, out.levers, unit_bytes=out.unit_bytes)


def _write(root: Path, path: Path, data: bytes) -> None:
    try:
        fsutil.atomic_write_bytes(path, data, root)
    except fsutil.UnsafeWriteError as exc:
        raise ReorgError(
            [Issue(code=2, key="unsafe_write", dest=path.name, detail=exc.reason)]
        ) from None


def _write_reports(root: Path, run: Path, out: PlanOutcome) -> None:
    _write(root, run / PLAN_MD_NAME, plan_md(out.manifest, out.manifest.lang).encode("utf-8"))
    _write(root, run / REPORT_NAME, report_dumps(outcome_report(out)))


# --------------------------------------------------------------------------- sources


def _rel_source(root: Path, given: str) -> str | None:
    """A ``--source`` as a POSIX path relative to ``root``: ``*.md``, inside the root."""
    p = Path(given)
    full = p if p.is_absolute() else root / p
    norm = Path(os.path.normpath(full))
    try:
        rel = norm.relative_to(root)
    except ValueError:
        return None
    rel_s = rel.as_posix()
    if not rel_s.endswith(".md") or rel_s in (".", "") or not fsutil.is_within(norm, root):
        return None
    return rel_s


def _discover(root: Path, extra: Sequence[str]) -> tuple[list[str], bool, list[Issue]]:
    """Source paths in order (CLAUDE.md, AGENTS.md, ``--source``…) and the symlink case."""
    issues: list[Issue] = []
    paths: list[str] = []
    symlink_claude = False
    claude, agents = root / CLAUDE, root / AGENTS
    if os.path.lexists(claude):
        if (
            claude.is_symlink()
            and os.path.lexists(agents)
            and os.path.realpath(claude) == os.path.realpath(agents)
        ):
            symlink_claude = True
        else:
            paths.append(CLAUDE)
    if os.path.lexists(agents):
        paths.append(AGENTS)
    for given in extra:
        rel = _rel_source(root, given)
        if rel is None:
            issues.append(Issue(code=1, key="source_invalid", source=given))
        elif rel not in paths:
            paths.append(rel)
    if not paths and not issues:
        issues.append(Issue(code=1, key="no_sources"))
    return paths, symlink_claude, issues


def _agents_overrides(root: Path) -> list[Issue]:
    out: list[Issue] = []
    if os.path.lexists(root / "AGENTS.override.md"):
        out.append(Issue(code=1, key="agents_override", dest="AGENTS.override.md"))
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            continue
        if os.path.lexists(entry / "AGENTS.override.md"):
            out.append(
                Issue(code=1, key="agents_override", dest=f"{entry.name}/AGENTS.override.md")
            )
    return out


def _gitignore_issue(root: Path) -> Issue | None:
    """Step 6 without writing: an existing ``.declutter/.gitignore`` needs a ``*`` line."""
    path = root / GITIGNORE_REL
    if not os.path.lexists(path):
        return None
    try:
        data = fsutil.safe_read_bytes(path, root)
    except fsutil.UnsafeReadError as exc:
        return Issue(code=1, key="gitignore_no_star", dest=GITIGNORE_REL, detail=exc.reason)
    if b"*" in data.split(b"\n"):
        return None
    return Issue(code=1, key="gitignore_no_star", dest=GITIGNORE_REL)


def _git_checks(
    root: Path, raws: Mapping[str, bytes]
) -> tuple[GitInfo, list[Issue], tuple[str, ...]]:
    """Step 7: HEAD on a branch, no normalizing attributes, HEAD blobs equal to the disk."""
    issues: list[Issue] = []
    warnings: list[str] = []
    if not gitops.is_repo(root):
        return GitInfo(enabled=False, base_branch=None, base_commit=None), issues, ()
    try:
        branch = gitops.head_branch(root)
        if branch is None:
            issues.append(Issue(code=1, key="detached_head"))
        info = GitInfo(enabled=True, base_branch=branch, base_commit=gitops.head_commit(root))
        paths = list(raws)
        found = gitops.normalizing(root, paths)
        # Enmienda 05-11: tolerated only on CR-free disk bytes; blob_differs still applies.
        refused, tolerated = gitops.split_lf_safe(
            found, paths, lambda p: p in raws and b"\r" not in raws[p]
        )
        warnings = [gitops.lf_safe_warning(f) for f in tolerated]
        for path, attr, value in refused:
            issues.append(
                Issue(
                    code=1,
                    key="git_normalizes",
                    source=path if path in raws else None,
                    detail=f"{attr}={value}",
                )
            )
        for path, raw in raws.items():
            blob = gitops.show_bytes(root, "HEAD", path)
            if blob is not None and _sha_hex(blob) != _sha_hex(raw):
                issues.append(Issue(code=1, key="blob_differs", source=path))
        warnings += [
            f"open_branch_touches_source:{b}" for b in gitops.branches_touching(root, paths)
        ]
    except gitops.GitError as exc:
        issues.append(Issue(code=2, key="git_error", detail=str(exc)))
        info = GitInfo(enabled=True, base_branch=None, base_commit=None)
    return info, issues, tuple(warnings)


def _key_family(key: str) -> str:
    return _SUFFIX_RE.sub("", key)


def _import_overrides(
    old: Manifest, units: Sequence[Unit]
) -> tuple[tuple[UnitOverride | BlockOverride, ...], tuple[str, ...]]:
    """``plan --from`` (spec §5.8): keep overrides whose key still names one unit or block;
    list orphans and ambiguous ones (duplicate titles or repeated identical blocks, i.e. a
    ``~N`` key in either plan) as warnings, without applying them."""
    unit_keys = [u.heading_key for u in units]
    block_keys = [b.key for u in units for b in u.blocks]
    kept: list[UnitOverride | BlockOverride] = []
    warnings: list[str] = []
    for o in old.overrides:
        key = o.heading_key if isinstance(o, UnitOverride) else o.block_key
        pool = unit_keys if isinstance(o, UnitOverride) else block_keys
        family = _key_family(key)
        ambiguous = "~" in key or any(k != key and _key_family(k) == family for k in pool)
        if ambiguous:
            warnings.append(f"override_ambiguous:{key}")
        elif key not in pool:
            warnings.append(f"override_orphan:{key}")
        else:
            kept.append(o)
    return tuple(kept), tuple(dict.fromkeys(warnings))


# --------------------------------------------------------------------------- plan


def load_run(root: Path, reorg_id: str) -> tuple[Manifest, Path]:
    """The manifest of run ``reorg_id`` and its folder. Missing -> code 1 (``run_missing``);
    invalid -> code 4 (``manifest_invalid``)."""
    run = run_dir(root, reorg_id)
    try:
        data = fsutil.safe_read_bytes(run / MANIFEST_NAME, root)
    except fsutil.UnsafeReadError as exc:
        if exc.reason == "missing":
            raise ReorgError([Issue(code=1, key="run_missing", detail=reorg_id)]) from None
        raise ReorgError(
            [Issue(code=2, key="unsafe_read", dest=MANIFEST_NAME, detail=exc.reason)]
        ) from None
    try:
        m = loads(data)
    except ManifestError as exc:
        raise ReorgError([exc.issue]) from None
    if m.id != reorg_id:
        raise ReorgError([Issue(code=4, key="manifest_invalid", detail="id: mismatch")])
    return m, run


def _parse_docs(
    raws: Mapping[str, bytes],
) -> tuple[tuple[SourceDoc, ...], tuple[Unit, ...], list[Issue]]:
    issues: list[Issue] = []
    docs: list[SourceDoc] = []
    for path, raw in raws.items():
        try:
            docs.append(mdblocks.parse(raw, path))
        except mdblocks.SourceError as exc:
            issues.append(_source_error_issue(exc))
    if issues:
        return tuple(docs), (), issues
    try:
        units = mdblocks.parse_sources(docs)
    except mdblocks.SourceError as exc:
        return tuple(docs), (), [_source_error_issue(exc)]
    return tuple(docs), units, issues


def plan_project(req: PlanRequest) -> PlanOutcome:
    """``declutter reorg plan`` (spec §10.1), in the order of the plan (steps 1-14).

    Fatal issues (codes 5, 2, 1) raise :class:`ReorgError` before anything is written;
    with ``dry_run`` nothing is written at all, not even ``.declutter/``. Otherwise the
    run folder gets ``original/``, ``manifest.json``, ``plan.md`` and ``report.json``.
    """
    project, cfg = req.project, req.cfg
    root = project.root
    issues: list[Issue] = []

    # 1. sources, read byte-exact and confined to the root
    paths, symlink_claude, found = _discover(root, req.extra_sources)
    issues += found
    raws: dict[str, bytes] = {}
    for path in paths:
        try:
            raws[path] = fsutil.safe_read_bytes(root / path, root)
        except fsutil.UnsafeReadError as exc:
            issues.append(Issue(code=2, key="unsafe_read", source=path, detail=exc.reason))
    # 2. AGENTS.override.md hides AGENTS.md from Codex
    issues += _agents_overrides(root)
    # 3. G5 on every source, before parsing
    for path, raw in raws.items():
        for hit in scan_secrets(path, raw):
            issues.append(
                Issue(
                    code=5, key="secret", source=path, at=(hit.line, hit.line), detail=hit.category
                )
            )
    # 4. parse
    docs: tuple[SourceDoc, ...] = ()
    units0: tuple[Unit, ...] = ()
    if not any(i.code == 2 for i in issues):
        docs, units0, parse_issues = _parse_docs(raws)
        issues += parse_issues
    # 5. destination path set
    paths_lang = req.lang if req.lang in LANGS else "es"
    try:
        paths_lang = choose_paths_lang(root, req.lang)
    except ReorgError as exc:
        issues += exc.issues
    # 6. the existing .declutter/.gitignore (created at step 13, never before)
    gi = _gitignore_issue(root)
    if gi is not None:
        issues.append(gi)
    # 7. git
    git, git_issues, branch_warnings = _git_checks(root, raws)
    issues += git_issues
    old: Manifest | None = None
    if req.from_id is not None:
        try:
            old, _ = load_run(root, req.from_id)
        except ReorgError as exc:
            issues += exc.issues
    _raise_if_fatal(issues)

    # 8. overrides from --from
    overrides: tuple[UnitOverride | BlockOverride, ...] = ()
    from_warnings: tuple[str, ...] = ()
    if old is not None:
        overrides, from_warnings = _import_overrides(old, units0)

    rid = run_id(req.now, root)
    out = _pipeline(
        _Inputs(
            project=project,
            cfg=cfg,
            lang=req.lang,
            paths_lang=paths_lang,
            reorg_id=rid,
            created=req.now.date().isoformat(),
            today=req.now.date(),
            docs=docs,
            units=units0,
            symlink_claude=symlink_claude,
            overrides=overrides,
            git=git,
            carried_cards={},
            for_apply=False,
            extra_warnings=(*from_warnings, *branch_warnings),
            check_git_outputs=git.enabled,
        )
    )
    if req.dry_run:
        return out

    # 13. the run folder
    run = run_dir(root, rid)
    gi = ensure_gitignore(root)
    if gi is not None:
        raise ReorgError([gi])
    write_snapshot(run, [(d.path, raws[d.path]) for d in docs])
    _write(root, run / MANIFEST_NAME, dumps(out.manifest))
    out = replace(out, run_dir=run)
    _write_reports(root, run, out)
    return out


# --------------------------------------------------------------------------- check


def _empty_build() -> BuildResult:
    from .build import BuildResult

    return BuildResult(files={}, modes={}, placements=())


def check_run(project: Project, cfg: Config, reorg_id: str, *, now: datetime) -> PlanOutcome:
    """``declutter reorg check`` (spec §6.4): re-read the sources, rebuild the plan with the
    manifest's overrides, validate with rules 1-16 and rewrite ``plan.md`` and
    ``report.json``. A stale plan (rule 1) is reported without rebuilding."""
    root = project.root
    m, run = load_run(root, reorg_id)
    stale: list[Issue] = []
    raws: dict[str, bytes] = {}
    for s in m.sources:
        try:
            raws[s.path] = fsutil.safe_read_bytes(root / s.path, root)
        except fsutil.UnsafeReadError as exc:
            if exc.reason != "missing":
                raise ReorgError(
                    [Issue(code=2, key="unsafe_read", source=s.path, detail=exc.reason)]
                ) from None
    known = {s.path for s in m.sources}
    paths, symlink_claude, _ = _discover(root, ())
    for path in paths:
        if path not in known:
            stale.append(Issue(code=4, key="check.1", source=path, detail=f"plan --from {m.id}"))
    docs: dict[str, SourceDoc] = {}
    for path, raw in raws.items():
        try:
            docs[path] = mdblocks.load_source(path, raw)
        except mdblocks.SourceError as exc:
            raise ReorgError([_source_error_issue(exc)]) from None
    source_issues = _source_rules(m, docs)
    stale += [i for i in source_issues if i.key == "check.1"]
    if stale:
        issues = _dedupe(stale)
        out = PlanOutcome(
            manifest=m,
            build=_empty_build(),
            issues=issues,
            code=resolve_code(i.code for i in issues),
            run_dir=run,
            levers={},
        )
        _write_reports(root, run, out)
        return out

    ordered, units0, parse_issues = _parse_docs(raws)
    _raise_if_fatal(parse_issues)
    out = _pipeline(
        _Inputs(
            project=project,
            cfg=cfg,
            lang=m.lang,
            paths_lang=m.paths_lang,
            reorg_id=m.id,
            created=m.created,
            today=now.date(),
            docs=ordered,
            units=units0,
            symlink_claude=m.symlink_claude or symlink_claude,
            overrides=m.overrides,
            git=m.git,
            carried_cards={u.id: u.card_id for u in m.units if u.card_id is not None},
            for_apply=True,
            check_git_outputs=m.git.enabled and gitops.is_repo(root),
        )
    )
    issues = _dedupe([*source_issues, *out.issues])
    code = resolve_code(i.code for i in issues)
    out = replace(out, issues=issues, code=code, run_dir=run)
    if code != 3:
        out = replace(out, levers={})
    _write_reports(root, run, out)
    return out


# --------------------------------------------------------------------------- set


def _resolve_unit(m: Manifest, ref: str) -> UnitEntry:
    """``UNIT`` by id or ``heading_key``. A key of a duplicated title family (``k`` while
    ``k~2`` exists) is ambiguous (code 4); nothing found is code 1."""
    found = [u for u in m.units if u.id == ref or u.heading_key == ref]
    if "~" not in ref and any(u.heading_key == ref for u in found):
        family = [u for u in m.units if _key_family(u.heading_key) == ref]
        if len(family) > 1:
            found = family
    found = list({u.id: u for u in found}.values())
    if len(found) > 1:
        raise ReorgError([Issue(code=4, key="ambiguous_override", detail=f"units={len(found)}")])
    if not found:
        raise ReorgError([Issue(code=1, key="unit_unknown", detail=ref)])
    return found[0]


def _resolve_block(m: Manifest, u: UnitEntry, ref: str) -> BlockEntry:
    found = [b for b in u.blocks if b.id == ref or b.key == ref]
    if "~" not in ref and any(b.key == ref for b in found):
        family = [b for x in m.units for b in x.blocks if _key_family(b.key) == ref]
        if len(family) > 1:
            found = family
    found = list({b.id: b for b in found}.values())
    if len(found) > 1:
        raise ReorgError([Issue(code=4, key="ambiguous_override", detail=f"blocks={len(found)}")])
    if not found:
        raise ReorgError([Issue(code=1, key="block_unknown", unit=u.id, detail=ref)])
    return found[0]


def set_override(
    project: Project,
    cfg: Config,
    reorg_id: str,
    unit_ref: str,
    *,
    dest: str | None = None,
    card: bool | None = None,
    keep: bool = False,
    accept: bool = False,
    reason: str | None = None,
    not_safety: str | None = None,
    ack_reason: str | None = None,
    now: datetime,
) -> PlanOutcome:
    """``declutter reorg set`` (spec §5.8, §10.1): record an override in ``manifest.json`` and
    run :func:`check_run`.

    Rejected without writing: an unknown unit or block (code 1), no change requested
    (code 1), an ambiguous reference, an empty ``--reason``, an ``--ack-reason`` under 10
    characters (``check.8``), an invalid ``dest`` (``check.3``) and a ``dest`` that moves a
    G2 unit (``check.4``), all code 4. A new override replaces the earlier one of the same
    unit and field (or of the same block).
    """
    root = project.root
    m, run = load_run(root, reorg_id)
    u = _resolve_unit(m, unit_ref)
    unit_fields: list[tuple[str, str | bool]] = []
    if dest is not None:
        unit_fields.append(("dest", dest))
    if card is not None:
        unit_fields.append(("card", card))
    if keep:
        unit_fields.append(("keep", True))
    if accept:
        unit_fields.append(("accept", True))
    if not unit_fields and not_safety is None:
        raise ReorgError([Issue(code=1, key="set_nothing", unit=u.id)])
    if ack_reason is not None and not_safety is None:
        raise ReorgError([Issue(code=1, key="ack_without_block", unit=u.id)])

    issues: list[Issue] = []
    new: list[UnitOverride | BlockOverride] = []
    if unit_fields:
        why = (reason or "").strip()
        if not why:
            issues.append(_unit_issue(4, "check.8", u, "reason"))
        if dest is not None:
            top = classify.top_level_dirs(root)
            if not valid_dest(dest) or (dest.startswith("L2:") and dest[3:] not in top):
                issues.append(_unit_issue(4, "check.3", u, "dest"))
            elif u.protected and dest != "L1":
                issues.append(_unit_issue(4, "check.4", u, "protected"))
        new += [
            UnitOverride(u.heading_key, name, value, reason or "") for name, value in unit_fields
        ]
    block: BlockEntry | None = None
    if not_safety is not None:
        block = _resolve_block(m, u, not_safety)
        if not block.guard:
            raise ReorgError([Issue(code=1, key="block_not_guarded", unit=u.id, block=block.id)])
        ack = ack_reason or ""
        if len(ack.strip()) < classify.GUARD_ACK_MIN:
            issues.append(_block_issue("check.8", u, block, "guard_ack"))
        new.append(BlockOverride(block.key, ack))
    if issues:
        raise ReorgError(_dedupe(issues))

    replaced_unit = {o.field for o in new if isinstance(o, UnitOverride)}
    kept = [
        o
        for o in m.overrides
        if not (
            (
                isinstance(o, UnitOverride)
                and o.heading_key == u.heading_key
                and o.field in replaced_unit
            )
            or (isinstance(o, BlockOverride) and block is not None and o.block_key == block.key)
        )
    ]
    _write(root, run / MANIFEST_NAME, dumps(replace(m, overrides=(*kept, *new))))
    return check_run(project, cfg, reorg_id, now=now)


_PENDING_KEYS = frozenset({"check.15", "check.16"})


def set_exit(out: PlanOutcome, unit_ref: str) -> tuple[int, tuple[Issue, ...], tuple[str, ...]]:
    """Exit code of ``declutter reorg set`` (spec §10.5, Enmienda 05-11), the issues to print
    and the ids of OTHER units still pending confirmation.

    The override was saved when :func:`set_override` returned. Pending confirmations of
    other units (``check.15``/``check.16``) are reported as a count, not as failures, so
    they alone give 0; any other issue keeps its code (4 for the override's own unit or a
    proof error, 3 for ``codex_budget``) under the usual precedence.
    """
    target = {u.id for u in out.manifest.units if unit_ref in (u.id, u.heading_key)}
    others = [i for i in out.issues if i.key in _PENDING_KEYS and i.unit not in target]
    rest = tuple(i for i in out.issues if i not in others)
    pending = tuple(dict.fromkeys(i.unit for i in others if i.unit is not None))
    return resolve_code(i.code for i in rest), rest, pending
