"""Build every destination in memory, with its placements and the control files (spec §9).

:func:`build_content` turns a classified plan (units, decisions, literal copies) into the
final bytes of each file ``apply`` would write, plus one :class:`Placement` per span of
lines: ``primary`` and ``copy`` spans are literal source lines, ``generated`` and
``marker`` spans are text produced here from ``templates_reorg/<lang>/``. Every line of a
created or rewritten file belongs to exactly one placement; an existing file that is not a
source is only extended at its end (spec §4.3). :func:`add_controls` adds the control files
of spec §12.1 (``.declutter/.gitignore``, ``ratchet.json``, ``pins.json``, ``ratchet.log``).

Nothing is written to disk here. Errors carry paths, ids and line numbers, never content.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from functools import cache
from importlib import resources
from pathlib import Path

from .. import ratchet, scaffold
from ..model import Block, Config, Decision, Pin, Placement, SourceDoc, Unit
from ..ratchet import RatchetState
from ..signals_files import _CHANGELOG_RE
from . import Issue, ReorgError, classify, mdblocks
from .classify import CopySpec, Overrides, top_level_dirs
from .manifest import GITIGNORE_REL, PATHS, truncate60
from .projection import Projection

__all__ = [
    "AGENTS_ONLY_TITLES",
    "BuildInput",
    "BuildResult",
    "ExistingReader",
    "add_controls",
    "anchor",
    "build_content",
    "load_template",
    "next_card_number",
    "pitfall_prefix",
    "section_titles",
    "slug",
]

ExistingReader = Callable[[str], bytes | None]  # safe_read_bytes confined to root; None if absent

AGENTS = "AGENTS.md"
CLAUDE = "CLAUDE.md"
CONTROL_FILES: tuple[str, ...] = (
    GITIGNORE_REL,
    ratchet.RATCHET_REL,
    ratchet.PINS_REL,
    ratchet.LOG_REL,
)
# Section-title keys that only ever appear in the root AGENTS.md (the safety lexicon may
# match them there); every other title is written outside AGENTS.md.
AGENTS_ONLY_TITLES: frozenset[str] = frozenset(
    {"invariants", "dated_rules", "pitfall_index", "map", "where"}
)

_CARD_RE = re.compile(r"^D-(\d{3,})(?:-.*)?\.md$")
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_SUBST_RE = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- public types


@dataclass(frozen=True)
class BuildInput:
    root: Path
    reorg_id: str
    today: date
    lang: str  # messages and templates
    paths_lang: str  # destination path set (spec §9)
    project_name: str
    docs: Mapping[str, SourceDoc]
    units: tuple[Unit, ...]
    decisions: tuple[Decision, ...]
    copies: tuple[CopySpec, ...]
    overrides: Overrides
    existing: ExistingReader
    symlink_claude: bool = False


@dataclass(frozen=True)
class BuildResult:
    files: Mapping[str, bytes]  # every output path -> final full content
    modes: Mapping[str, str]  # path -> "created"|"extended"|"rewritten"|"control"
    placements: tuple[Placement, ...]
    pins: tuple[Pin, ...] = ()
    prev_sha: Mapping[str, str] = field(default_factory=dict)  # sha256 of current bytes or "absent"
    card_ids: Mapping[str, str] = field(default_factory=dict)  # unit id -> "D-NNN"


# --------------------------------------------------------------------------- small helpers


def next_card_number(existing_names: Iterable[str]) -> int:
    """Highest ``D-NNN`` (3+ digits) among ``existing_names`` plus one; 1 if there is none."""
    nums = [int(m.group(1)) for n in existing_names if (m := _CARD_RE.match(n))]
    return max(nums, default=0) + 1


def slug(title: str) -> str:
    """NFKD, combining marks dropped, lower case, runs of non ``[a-z0-9]`` -> ``-``, cut at 40."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return _SUBST_RE.sub("-", text).strip("-")[:40].rstrip("-")


def anchor(reorg_id: str, unit_id: str, block_id: str | None = None) -> str:
    """``r<id>-u10`` for a unit, ``r<id>-u13-b3`` for a block (``block_id`` "U13.b3")."""
    out = f"r{reorg_id}-{unit_id.lower()}"
    if block_id:
        out += "-" + block_id.rsplit(".", 1)[-1].lower()
    return out


def pitfall_prefix(first_line: str) -> str:
    """Literal prefix of a pitfall index line (spec §9.2.7, plan reading L5).

    ``first_line`` is the decoded first line of the item or block. The bullet mark is
    removed; the text is cut after the first ``". "`` or at 100 characters (with ``…``),
    then before the first ``<!--``; a fence opener gives an empty prefix.
    """
    text = first_line
    m = mdblocks.ITEM_RE.match(text)
    if m:
        text = text[m.end() :]
    if text.lstrip(" ").startswith(("```", "~~~")):
        return ""
    cut = text.find(". ")
    if cut >= 0:
        text = text[: cut + 1]
    if len(text) > 100:
        text = text[:100] + "…"
    cut = text.find("<!--")
    if cut >= 0:
        text = text[:cut]
    return text.strip()


def _template_node(lang: str, name: str) -> bytes:
    node = resources.files("declutter_context") / "templates_reorg" / lang / name
    if not node.is_file():
        raise ValueError(f"unknown reorg template {name!r} for language {lang!r}")
    return node.read_bytes()


def _subst(text: str, subs: Mapping[str, str]) -> str:
    # Single pass: a value that contains a placeholder is never rewritten.
    return _PLACEHOLDER_RE.sub(lambda m: subs.get(m.group(1), m.group(0)), text)


def load_template(lang: str, name: str, subs: Mapping[str, str]) -> list[bytes]:
    """Lines (without ``\\n``) of ``templates_reorg/<lang>/<name>`` with ``{{key}}`` substituted."""
    text = _subst(_template_node(lang, name).decode("utf-8"), subs)
    return list(mdblocks.split_lines(text.encode("utf-8")).lines)


@cache
def _titles(lang: str) -> dict[str, str]:
    data = json.loads(_template_node(lang, "section_titles.json").decode("utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("section_titles.json must map strings to strings")
    return dict(data)


def section_titles(lang: str) -> dict[str, str]:
    """Titles and labels of the generated sections for ``lang`` (a copy)."""
    return dict(_titles(lang))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_preamble(u: Unit) -> bool:
    return u.heading_key == u.id


def _enc(text: str) -> bytes:
    return text.encode("utf-8")


# --------------------------------------------------------------------------- file builder


class _File:
    """Lines of one destination and the placements that partition them."""

    def __init__(self, path: str, mode: str) -> None:
        self.path = path
        self.mode = mode
        self.lines: list[bytes] = []
        self.places: list[Placement] = []
        self.prefix = b""
        self.n_prefix = 0

    def add(
        self,
        role: str,
        lines: Sequence[bytes],
        *,
        unit: str | None = None,
        block: str | None = None,
        source: str | None = None,
        src: tuple[int, int] | None = None,
        kind: str | None = None,
    ) -> None:
        if not lines:
            return
        at = (len(self.lines) + 1, len(self.lines) + len(lines))
        self.lines.extend(lines)
        self.places.append(
            Placement(
                role=role,
                dest=self.path,
                at=at,
                sha256=mdblocks.lines_sha(lines),
                unit=unit,
                block=block,
                source=source,
                src=src,
                kind=kind,
            )
        )

    def gen(self, kind: str, lines: Sequence[bytes]) -> None:
        self.add("generated", lines, kind=kind)

    def blank(self) -> None:
        self.gen("blank", [b""])

    def sep(self) -> None:
        """A generated blank line unless the file is empty or already ends in a blank line."""
        if self.lines and self.lines[-1].strip(b" \t\r") != b"":
            self.blank()

    def extend_from(self, prev: bytes) -> None:
        """Keep ``prev`` as an exact byte prefix (spec §4.3), then one generated blank line."""
        added = not prev.endswith(b"\n")
        prefix = prev + (b"\n" if added else b"")
        lines = mdblocks.split_lines(prefix).lines
        self.prefix = prefix
        self.n_prefix = len(lines)
        self.lines.extend(lines)
        self.places.append(
            Placement(
                role="extended_prefix",
                dest=self.path,
                at=(1, len(lines)),
                sha256=mdblocks.lines_sha(lines),
                backup_sha256=_sha(prev),
                prefix_newline_added=added,
            )
        )
        self.blank()

    def data(self) -> bytes:
        return self.prefix + b"".join(ln + b"\n" for ln in self.lines[self.n_prefix :])


# --------------------------------------------------------------------------- builder


class _Builder:
    def __init__(self, inp: BuildInput) -> None:
        self.inp = inp
        self.rid = inp.reorg_id
        self.lang = inp.lang
        self.t = _titles(inp.lang)
        pl = "en" if inp.paths_lang == "en" else "es"
        self.paths = {role: cols[pl] for role, cols in PATHS.items()}
        self.dec: dict[str, Decision] = {d.unit: d for d in inp.decisions}
        self.units = [u for u in inp.units if u.id in self.dec]
        self.files: dict[str, _File] = {}
        self.card_ids: dict[str, str] = {}
        self.card_paths: dict[str, str] = {}
        self.subs = {
            "id": self.rid,
            "state": self.paths["state"],
            "decisions_index": self.paths["decisions_index"],
            "decisions_dir": self.paths["decisions_dir"],
            "history_dir": self.paths["history_dir"],
        }

    # ---------------------------------------------------------------- utilities

    def lit(self, source: str, a: int, b: int) -> list[bytes]:
        return list(self.inp.docs[source].lines[a - 1 : b])

    def tmpl(self, name: str, **extra: str) -> list[bytes]:
        return load_template(self.lang, name, {**self.subs, **extra})

    def title(self, key: str, **extra: str) -> str:
        return _subst(self.t[key], {**self.subs, **extra})

    def units_to(self, dest: str) -> list[Unit]:
        return [u for u in self.units if self.dec[u.id].dest == dest]

    def has_gen(self, kind: str) -> bool:
        """True if any source already holds a ``declutter:gen <kind>`` region (spec §9.1)."""
        pat = re.compile(rf"^<!-- declutter:gen {re.escape(kind)}(?:\s|$)")
        for doc in self.inp.docs.values():
            for line in doc.lines:
                if pat.match(mdblocks.decode_line(line)):
                    return True
        return False

    def exists_after(self, path: str) -> bool:
        return path in self.files or self.inp.existing(path) is not None

    def file(self, path: str) -> _File:
        """The destination ``path``: rewritten (source), created (absent) or extended."""
        if path in self.files:
            return self.files[path]
        if path in self.inp.docs or (path == CLAUDE and self.inp.symlink_claude):
            out = _File(path, "rewritten")
        else:
            prev = self.inp.existing(path)
            if prev is None:
                out = _File(path, "created")
            else:
                try:
                    still_open = mdblocks.open_structure_at_end(prev)
                except mdblocks.SourceError as exc:
                    raise ReorgError([Issue(code=2, key=exc.reason, dest=path)]) from None
                if still_open is not None:
                    kind, line = still_open
                    raise ReorgError(
                        [
                            Issue(
                                code=2,
                                key="extend_open_structure",
                                dest=path,
                                at=(line, line),
                                detail=kind,
                            )
                        ]
                    )
                out = _File(path, "extended")
                out.extend_from(prev)
        self.files[path] = out
        return out

    def region(self, out: _File, kind: str, title_key: str, body: Sequence[str]) -> None:
        """A generated ``##`` section: title, opening marker, body, closing marker."""
        out.sep()
        out.gen(
            kind,
            [
                _enc(f"## {self.title(title_key)}"),
                _enc(f"<!-- declutter:gen {kind} {self.rid} -->"),
                *(_enc(x) for x in body),
                _enc(f"<!-- declutter:gen-end {kind} -->"),
            ],
        )

    def primary(self, out: _File, u: Unit, a: int | None = None, kind: str | None = None) -> None:
        start = u.start if a is None else a
        out.add(
            "primary",
            self.lit(u.source, start, u.end),
            unit=u.id,
            source=u.source,
            src=(start, u.end),
            kind=kind,
        )

    def copy(self, out: _File, c: CopySpec) -> None:
        out.add(
            "copy",
            self.lit(c.source, c.src[0], c.src[1]),
            unit=c.unit,
            block=c.block,
            source=c.source,
            src=c.src,
        )

    def literal_unit(self, out: _File, u: Unit) -> None:
        """Anchor + ``declutter:src`` marker, the literal primary, ``declutter:end`` (spec §9.4)."""
        sha = mdblocks.lines_sha(self.lit(u.source, u.start, u.end))
        out.add(
            "marker",
            [
                _enc(f'<a id="{anchor(self.rid, u.id)}"></a>'),
                _enc(
                    f"<!-- declutter:src {u.source}:{u.start}-{u.end} sha256={sha} "
                    f"unit={u.id} plan={self.rid} -->"
                ),
            ],
            unit=u.id,
            kind="src",
        )
        self.primary(out, u)
        out.add("marker", [_enc(f"<!-- declutter:end {u.id} -->")], unit=u.id, kind="end")

    def literal_units(self, out: _File, units: Sequence[Unit]) -> None:
        for i, u in enumerate(units):
            if i:
                out.blank()
            self.literal_unit(out, u)

    def anchor_of(self, u: Unit) -> str:
        return f"{self.dec[u.id].file}#{anchor(self.rid, u.id)}"

    # ---------------------------------------------------------------- cards

    def assign_cards(self) -> None:
        folder = self.inp.root / self.paths["decisions_dir"]
        try:
            names = [c.name for c in folder.iterdir()] if folder.is_dir() else []
        except OSError:
            names = []
        nxt = next_card_number(names)
        for u in self.units:
            d = self.dec[u.id]
            if not (d.card and d.dest == "L4"):
                continue
            if d.card_id is not None:
                cid = d.card_id
            else:
                cid = f"D-{nxt:03d}"
                nxt += 1
            self.card_ids[u.id] = cid
            s = slug(u.heading)
            name = f"{cid}-{s}.md" if s else f"{cid}.md"
            self.card_paths[u.id] = f"{self.paths['decisions_dir']}/{name}"

    def pointer_line(self, u: Unit, base: str) -> str:
        """``- D-003 <title> → <card> (pending)`` or, without a card, a link to the literal."""
        t60 = truncate60(u.heading)
        if u.id in self.card_paths:
            link = posixpath.relpath(self.card_paths[u.id], base)
            return f"- {self.card_ids[u.id]} {t60} → {link} ({self.t['pending']})"
        link = posixpath.relpath(self.dec[u.id].file, base)
        return f"- {u.id} {t60} → {link}#{anchor(self.rid, u.id)}"

    # ---------------------------------------------------------------- non-root files

    def build_history(self) -> list[Unit]:
        hist = self.units_to("L4")
        by_file: dict[str, list[Unit]] = {}
        for u in hist:
            by_file.setdefault(self.dec[u.id].file, []).append(u)
        for path, units in by_file.items():
            out = self.file(path)
            if out.mode == "created":
                stem = posixpath.splitext(posixpath.basename(path))[0]
                is_undated = path == self.paths["history_undated"]
                label = self.t["history_undated"] if is_undated else stem
                out.gen("historial", self.tmpl("historial_header.md", title=label))
                out.blank()
            self.literal_units(out, units)
        return hist

    def build_cards(self) -> None:
        by_unit: dict[str, list[CopySpec]] = {}
        for c in self.inp.copies:
            if c.target == "card":
                by_unit.setdefault(c.unit, []).append(c)
        dec_dir = self.paths["decisions_dir"]
        ns = self.t["not_stated"]
        for u in self.units:
            if u.id not in self.card_paths:
                continue
            path = self.card_paths[u.id]
            if path in self.files or self.inp.existing(path) is not None:
                raise ReorgError(
                    [
                        Issue(
                            code=4,
                            key="check.10",
                            unit=u.id,
                            source=u.source,
                            src=(u.start, u.end),
                            dest=path,
                        )
                    ]
                )
            out = self.file(path)
            f = u.features
            hist = self.dec[u.id].file
            anc = anchor(self.rid, u.id)
            out.gen(
                "card",
                self.tmpl(
                    "card.md",
                    card_id=self.card_ids[u.id],
                    title=u.heading,
                    date=f.dated if f.dated not in (None, "undated") else ns,
                    branch=f"`{f.branch}`" if f.branch else ns,
                    zone=f.zone or ns,
                    literal=f"{hist}#{anc}",
                    literal_link=f"{posixpath.relpath(hist, dec_dir)}#{anc}",
                    range=f"{u.source}:{u.start}-{u.end}",
                    sha256=mdblocks.lines_sha(self.lit(u.source, u.start, u.end)),
                ),
            )
            for c in by_unit.get(u.id, []):
                self.copy(out, c)
                out.blank()

    def build_indices(self, hist: Sequence[Unit]) -> None:
        added = self.title("added_by")
        # History index: one row per L4 unit.
        if hist:
            path = self.paths["history_index"]
            out = self.file(path)
            if out.mode == "created":
                out.gen("template", self.rendered(path))
            base = self.paths["history_dir"]
            rows = [f"## {added}", "", self.t["history_table"], "|---|---|---|---|"]
            for u in hist:
                file = self.dec[u.id].file
                stem = posixpath.splitext(posixpath.basename(file))[0]
                link = f"{posixpath.relpath(file, base)}#{anchor(self.rid, u.id)}"
                rows.append(
                    f"| {stem} | {u.id} | {_cell(truncate60(u.heading))} "
                    f"| [{self.t['see']}]({link}) |"
                )
            out.sep()
            out.gen("index", [_enc(x) for x in rows])
        # Decisions index: always present (G7 sends the reader there), rows only if new.
        path = self.paths["decisions_index"]
        cards = [u for u in hist if u.id in self.card_paths]
        loose = [u for u in hist if u.id not in self.card_paths]
        if not (cards or loose) and self.inp.existing(path) is not None:
            return
        out = self.file(path)
        if out.mode == "created":
            out.gen("template", self.rendered(path))
        if not (cards or loose):
            return
        base = self.paths["decisions_dir"]
        body = [f"## {added}"]
        if cards:
            body += ["", self.t["decisions_table"], "|---|---|---|---|"]
            for u in cards:
                link = posixpath.relpath(self.card_paths[u.id], base)
                body.append(
                    f"| {self.card_ids[u.id]} | {_cell(truncate60(u.heading))} "
                    f"| {self.t['card_state']} | [{self.t['card_link']}]({link}) |"
                )
        if loose:
            body += ["", f"### {self.title('undated_log')}"]
            for u in loose:
                link = f"{posixpath.relpath(self.dec[u.id].file, base)}#{anchor(self.rid, u.id)}"
                body.append(
                    f"- {u.id} {truncate60(u.heading)} → [{self.t['history_link']}]({link})"
                )
        out.sep()
        out.gen("index", [_enc(x) for x in body])

    def rendered(self, rel: str) -> list[bytes]:
        data = scaffold.render_template(
            self.inp.paths_lang, rel, self.inp.today, self.inp.project_name
        )
        return list(mdblocks.split_lines(data).lines)

    def build_state(self) -> None:
        path = self.paths["state"]
        if self.inp.existing(path) is None:
            self.file(path).gen("state", self.rendered(path))

    def build_trampas(self) -> tuple[list[Unit], list[CopySpec]]:
        l5t = self.units_to("L5t")
        tcopies = [c for c in self.inp.copies if c.target == "trampas"]
        if not (l5t or tcopies):
            return l5t, tcopies
        out = self.file(self.paths["trampas"])
        if out.mode == "created":
            out.gen("trampas", self.tmpl("trampas_header.md"))
            out.blank()
        self.literal_units(out, l5t)
        if tcopies:
            out.sep()
            out.gen("trampas", [_enc(f"## {self.title('detected')}")])
            for c in tcopies:
                out.add(
                    "marker",
                    [_enc(f'<a id="{anchor(self.rid, c.unit, c.block)}"></a>')],
                    unit=c.unit,
                    block=c.block,
                    kind="anchor",
                )
                self.copy(out, c)
                out.blank()
        return l5t, tcopies

    def build_mapa(self) -> list[Unit]:
        l5m = self.units_to("L5m")
        if l5m:
            out = self.file(self.paths["mapa"])
            if out.mode == "created":
                out.gen("mapa", self.tmpl("mapa_header.md"))
                out.blank()
            self.literal_units(out, l5m)
        return l5m

    def build_zones(self) -> list[str]:
        prim: dict[str, list[Unit]] = {}
        ptrs: dict[str, list[Unit]] = {}
        for u in self.units:
            d = self.dec[u.id]
            if d.dest.startswith("L2:"):
                prim.setdefault(d.dest[3:], []).append(u)
            if d.dest == "L4" and d.pointer and "zone:" in d.pointer:
                ptrs.setdefault(d.pointer.split("zone:", 1)[1], []).append(u)
        zones = sorted(set(prim) | set(ptrs))
        for z in zones:
            out = self.file(f"{z}/AGENTS.md")
            if out.mode == "created":
                out.gen("zone", self.tmpl("zone_header.md", zone=z))
            if ptrs.get(z):
                out.sep()
                out.gen(
                    "zone",
                    [
                        _enc(f"## {self.title('zone_decisions')}"),
                        *(_enc(self.pointer_line(u, z)) for u in ptrs[z]),
                    ],
                )
            if prim.get(z):
                out.sep()
                for u in prim[z]:
                    self.primary(out, u)
            claude = f"{z}/CLAUDE.md"
            if self.inp.existing(claude) is None and claude not in self.files:
                self.file(claude).gen("zone", [b"@AGENTS.md"])
        return zones

    # ---------------------------------------------------------------- AGENTS.md

    def build_agents(
        self, hist: Sequence[Unit], l5t: Sequence[Unit], tcopies: Sequence[CopySpec]
    ) -> None:
        l5m_units = self.units_to("L5m")
        zones_touched = [p for p in self.files if p.endswith("/AGENTS.md")]
        out = self.file(AGENTS)
        l1 = self.units_to("L1")
        pre = [u for u in l1 if _is_preamble(u)]
        rest = [u for u in l1 if not _is_preamble(u)]

        # 1. front matter of the preamble that has one (plan reading L1).
        after_fm: dict[str, int] = {}
        for u in pre:
            if u.blocks and u.blocks[0].kind == "front_matter" and u.blocks[0].start == 1:
                fm: Block = u.blocks[0]
                self.primary_range(out, u, fm.start, fm.end, kind="front_matter")
                after_fm[u.id] = fm.end + 1
        # 2. header (G7), only once across runs.
        if not self.has_gen("agents-header"):
            out.gen("agents-header", self.tmpl("agents_header.md"))
        # 3. preamble primaries, in source order.
        for u in pre:
            start = after_fm.get(u.id, u.start)
            if start <= u.end:
                self.primary(out, u, start)
        # 4. safety invariants: G1 copies, each with its header and a blank line after.
        inv = [c for c in self.inp.copies if c.target == "invariants"]
        if inv:
            out.sep()
            out.gen(
                "invariants",
                [
                    _enc(f"## {self.title('invariants')}"),
                    _enc(f"<!-- declutter:gen invariants {self.rid} -->"),
                ],
            )
            by_id = {u.id: u for u in self.units}
            for c in inv:
                u = by_id[c.unit]
                d = self.dec[u.id]
                target = self.anchor_of(u) if d.dest in ("L4", "L5t", "L5m") else d.file
                cats = ", ".join(sorted(c.categories))
                out.gen("invariants", [_enc(f"### ⟨{c.block}⟩ → {target} · {cats}")])
                self.copy(out, c)
                out.blank()
            out.gen("invariants", [_enc("<!-- declutter:gen-end invariants -->")])
        # 5. remaining L1 primaries in (source, line) order.
        for u in rest:
            self.primary(out, u)
        # 6. dated rules: units moved to L4 with a pointer at the root.
        dated = [
            self.pointer_line(u, ".")
            for u in hist
            if (p := self.dec[u.id].pointer) is not None and p.startswith("root")
        ]
        if dated:
            self.region(out, "dated-rules", "dated_rules", dated)
        # 7. pitfall index.
        pits = self.pitfall_lines(l5t, tcopies)
        if pits:
            self.region(out, "pitfall-index", "pitfall_index", pits)
        # 8. map and zones.
        if l5m_units or zones_touched:
            self.region(out, "map", "map", self.map_lines())
        # 9. where each thing lives (once across runs).
        if not self.has_gen("where"):
            where = [
                f"- {self.t[key]}: {path}"
                for key, path in (
                    ("where_history", self.paths["history_index"]),
                    ("where_decisions", self.paths["decisions_index"]),
                    ("where_pitfalls", self.paths["trampas"]),
                    ("where_map", self.paths["mapa"]),
                )
                if self.exists_after(path)
            ]
            if where:
                self.region(out, "where", "where", where)

    def primary_range(self, out: _File, u: Unit, a: int, b: int, *, kind: str) -> None:
        out.add(
            "primary",
            self.lit(u.source, a, b),
            unit=u.id,
            source=u.source,
            src=(a, b),
            kind=kind,
        )

    def pitfall_lines(self, l5t: Sequence[Unit], tcopies: Sequence[CopySpec]) -> list[str]:
        trampas = self.paths["trampas"]
        lines: list[str] = []

        def line(first: bytes, link: str, label: str) -> str:
            prefix = pitfall_prefix(mdblocks.decode_line(first))
            head = f"- {prefix} → " if prefix else "- "
            return f"{head}{trampas}#{link} ({label})"

        for u in l5t:
            items = [b for b in u.blocks if b.kind == "item"]
            for n, blk in enumerate(items, 1):
                first = self.inp.docs[u.source].lines[blk.start - 1]
                lines.append(line(first, anchor(self.rid, u.id), self.title("item", n=str(n))))
        for c in tcopies:
            first = self.inp.docs[c.source].lines[c.src[0] - 1]
            lines.append(line(first, anchor(self.rid, c.unit, c.block), self.t["detected_ref"]))
        return lines

    def map_lines(self) -> list[str]:
        out: list[str] = []
        mapa = self.paths["mapa"]
        if self.exists_after(mapa):
            out.append(f"- {self.t['map_pointer']}: {mapa}")
        dirs = sorted(top_level_dirs(self.inp.root) | {p.split("/")[0] for p in self.zone_paths()})
        zoned = [d for d in dirs if self.exists_after(f"{d}/AGENTS.md")]
        others = [d for d in dirs if d not in zoned]
        out += [f"- `{d}/` → {d}/AGENTS.md" for d in zoned]
        if others:
            out.append(f"- {self.t['other_dirs']}: {', '.join(others)}")
        return out

    def zone_paths(self) -> list[str]:
        return [p for p in self.files if p.count("/") == 1 and p.endswith("/AGENTS.md")]

    # ---------------------------------------------------------------- CLAUDE.md

    def build_claude(self) -> None:
        out = self.file(CLAUDE)
        l1c = self.units_to("L1c")
        generated = [u for u in l1c if "generated" in self.dec[u.id].reasons]
        fresh = [u for u in l1c if "generated" not in self.dec[u.id].reasons]
        if not any(_is_preamble(u) and u.source == CLAUDE for u in generated):
            out.gen("claude", [b"@AGENTS.md", *self.tmpl("claude.md")])
        for u in generated:
            self.primary(out, u)
        if fresh:
            out.sep()
            out.gen(
                "claude-only",
                [
                    _enc(f"## {self.title('claude_only')}"),
                    _enc(f"<!-- declutter:gen claude-only {self.rid} -->"),
                ],
            )
            for u in fresh:
                self.primary(out, u)
            out.gen("claude-only", [_enc("<!-- declutter:gen-end claude-only -->")])

    # ---------------------------------------------------------------- run

    def run(self) -> BuildResult:
        unsupported = sorted(p for p in self.inp.docs if p not in (AGENTS, CLAUDE))
        if unsupported:
            raise ReorgError(
                [Issue(code=1, key="source_unsupported", source=p) for p in unsupported]
            )
        if AGENTS not in self.inp.docs and self.inp.existing(AGENTS) is not None:
            raise ReorgError([Issue(code=2, key="agents_not_source", dest=AGENTS)])
        self.assign_cards()
        hist = self.build_history()
        self.build_cards()
        l5t, tcopies = self.build_trampas()
        self.build_mapa()
        self.build_zones()
        self.build_indices(hist)
        self.build_state()
        self.build_agents(hist, l5t, tcopies)
        self.build_claude()

        files: dict[str, bytes] = {}
        modes: dict[str, str] = {}
        prev: dict[str, str] = {}
        places: list[Placement] = []
        for path in sorted(self.files):
            f = self.files[path]
            files[path] = f.data()
            modes[path] = f.mode
            places.extend(f.places)
            if path in self.inp.docs:
                prev[path] = self.inp.docs[path].sha256
            else:
                cur = self.inp.existing(path)
                prev[path] = "absent" if cur is None else _sha(cur)
        return BuildResult(
            files=files,
            modes=modes,
            placements=tuple(places),
            pins=(),
            prev_sha=prev,
            card_ids=dict(self.card_ids),
        )


def _cell(text: str) -> str:
    """A title inside a Markdown table cell: ``|`` is escaped so the row keeps its columns."""
    return text.replace("|", "\\|")


def build_content(inp: BuildInput) -> BuildResult:
    """Every content destination of the plan with its placements (no control files).

    Raises :class:`ReorgError`: code 2 for a file to extend that ends inside an open fence,
    comment or front matter (``extend_open_structure``) or that is not valid UTF-8; code 4
    when a card path already exists (``check.10``); code 1 for an unsupported source.
    """
    return _Builder(inp).run()


# --------------------------------------------------------------------------- controls


def _agents_index(res: BuildResult) -> list[Placement]:
    return [p for p in res.placements if p.dest == AGENTS and p.role == "primary"]


def _dated_keys(files: Mapping[str, bytes], inp: BuildInput) -> list[str]:
    """``title_key`` of every dated ``##`` heading in AGENTS.md, CLAUDE.md and zone AGENTS.md."""
    paths = [AGENTS, CLAUDE]
    zones = {p for p in files if p.count("/") == 1 and p.endswith("/AGENTS.md")}
    zones |= {f"{d}/AGENTS.md" for d in top_level_dirs(inp.root)}
    paths += sorted(zones)
    keys: list[str] = []
    for path in paths:
        data = files.get(path)
        if data is None:
            data = inp.existing(path)
        if data is None:
            continue
        for h in mdblocks.level2_headings(data.decode("utf-8", errors="replace")):
            if _CHANGELOG_RE.search(h):
                keys.append(ratchet.title_key(h.strip()))
    return keys


def _kept_dated_keys(inp: BuildInput) -> list[str]:
    """``title_key`` of the dated L1 units that no demotion can move (spec §5.4, §5.6.1):
    G2 units, units with imports and those the human kept with ``keep`` (or ``dest:
    "L1"``) and a reason. They are «in layer 1 when applying» (§12.1) by a rule or by a
    recorded decision, so a new one joins ``allowed_dated_headings``."""
    by_unit = {d.unit: d for d in inp.decisions}
    keys: list[str] = []
    for u in inp.units:
        d = by_unit.get(u.id)
        if d is None or d.dest != "L1" or not _CHANGELOG_RE.search(u.heading.strip()):
            continue
        if d.protected or u.features.imports >= 1 or classify.is_kept(u, inp.overrides):
            keys.append(ratchet.title_key(u.heading.strip()))
    return keys


def _state_obj(s: RatchetState | None) -> object:
    if s is None:
        return None
    return {"S1_max": s.s1_max, "S3_max_pct": s.s3_max_pct, "zone_max_lines": s.zone_max_lines}


def add_controls(
    res: BuildResult,
    inp: BuildInput,
    proj: Projection,
    cfg: Config,
    prior_ratchet: RatchetState | None,
    prior_pins: Sequence[Pin],
    prior_log: bytes | None,
    prior_gitignore: bytes | None,
) -> tuple[BuildResult, tuple[str, ...]]:
    """Add the control files of spec §12.1 to ``res`` and return the baseline warnings.

    ``proj`` must be the projection of ``res.files`` (the final content). ``ratchet.json``
    is the baseline over it (maxima only go down), ``pins.json`` the prior pins plus this
    run's (copies in AGENTS.md, blocks with categories of L1 primaries, G2 units),
    ``ratchet.log`` the prior log plus one ``baseline`` event, and ``.gitignore`` the final
    one that versions the control files. Raises :class:`ReorgError` (code 1) when a prior
    ``.declutter/.gitignore`` has no ``*`` line.
    """
    try:
        gitignore = ratchet.gitignore_final(prior_gitignore)
    except ValueError:
        raise ReorgError([Issue(code=1, key="gitignore_no_star", dest=GITIGNORE_REL)]) from None

    s1 = proj.after.get("S1")
    state, warnings = ratchet.baseline(
        int(s1) if s1 is not None else 0,
        proj.codex_max_pct,
        cfg.reorg.zone_max_lines,
        _dated_keys(res.files, inp),
        inp.reorg_id,
        prior_ratchet,
        kept=_kept_dated_keys(inp),
    )

    units = {u.id: u for u in inp.units}
    l1_blocks: list[tuple[str, Block, tuple[int, int]]] = []
    protected: list[tuple[str, tuple[int, int], frozenset[str]]] = []
    prot_ids = {d.unit for d in inp.decisions if d.protected and d.dest == "L1"}
    for p in _agents_index(res):
        if p.unit is None or p.src is None or p.unit not in units:
            continue
        u = units[p.unit]
        for blk in u.blocks:
            if blk.categories and p.src[0] <= blk.start and blk.end <= p.src[1]:
                off = p.at[0] - p.src[0]
                l1_blocks.append((u.id, blk, (blk.start + off, blk.end + off)))
        if u.id in prot_ids and p.src == (u.start, u.end):
            protected.append((u.id, p.at, u.features.title_guard))
    cats = {c.block: c.categories for c in inp.copies if c.target == "invariants"}
    agents_lines = mdblocks.split_lines(res.files.get(AGENTS, b"")).lines
    pins = ratchet.compute_pins(
        inp.reorg_id,
        agents_lines,
        res.placements,
        l1_blocks,
        protected,
        prior_pins,
        block_categories=cats,
    )

    log = prior_log or b""
    if log and not log.endswith(b"\n"):
        log += b"\n"
    log += ratchet.log_line(
        "baseline",
        date=inp.today.isoformat(),
        reorg=inp.reorg_id,
        old=_state_obj(prior_ratchet),
        new=_state_obj(state),
        reason=f"declutter reorg {inp.reorg_id}",
    )

    controls = {
        GITIGNORE_REL: gitignore,
        ratchet.RATCHET_REL: ratchet.ratchet_dumps(state),
        ratchet.PINS_REL: ratchet.pins_dumps(pins),
        ratchet.LOG_REL: log,
    }
    files = dict(res.files)
    modes = dict(res.modes)
    prev = dict(res.prev_sha)
    places = list(res.placements)
    for path in CONTROL_FILES:
        data = controls[path]
        files[path] = data
        modes[path] = "control"
        cur = inp.existing(path)
        prev[path] = "absent" if cur is None else _sha(cur)
        n = len(mdblocks.split_lines(data).lines)
        places.append(Placement(role="control", dest=path, at=(1, max(n, 1)), sha256=_sha(data)))
    out = replace(res, files=files, modes=modes, placements=tuple(places), pins=pins, prev_sha=prev)
    return out, warnings
