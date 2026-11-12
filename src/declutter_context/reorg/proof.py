"""Lossless proof C0-C9 with its exact and posterior modes (spec §7, §8 G2-G4, G8, §11.4).

:func:`verify` is a pure function over a manifest, its placements and two readers:
``read(path)`` returns the current bytes of a destination (from ``stage/``, the disk or a
git revision) or ``None``; ``original(path)`` returns the original bytes of a source.
Every comparison is made over bytes under the line model of spec §4.2.

Exact mode runs the requested checks. Posterior mode (``posterior=True``) first tests
whether every placement still matches its sha at its position; if so it runs the exact
checks, otherwise it evaluates each class by content (spec §11.4) and reports a state per
unit. Failures and states carry ids, paths, line ranges, sizes, hashes and categories;
never a byte of file content (G6).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from functools import cache

from .. import ratchet
from ..model import Pin, Placement, ProofFailure, ProofResult
from . import EXIT_OK, EXIT_PROOF, Issue, mdblocks
from . import overlay as ov
from .lexicon import Lexicon, build_lexicon
from .manifest import PATHS, Manifest, UnitEntry

__all__ = [
    "CHECKS_DISK",
    "CHECKS_REV",
    "CHECKS_STAGE",
    "POSTERIOR_STATES",
    "Read",
    "describe",
    "exit_code",
    "to_issues",
    "verify",
]

Read = Callable[[str], bytes | None]

CHECKS_STAGE: frozenset[str] = frozenset({"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C9"})
CHECKS_DISK: frozenset[str] = CHECKS_STAGE | {"C0", "C8a"}
CHECKS_REV: frozenset[str] = (CHECKS_DISK - {"C8a"}) | {"C8b"}

AGENTS = "AGENTS.md"
CLAUDE = "CLAUDE.md"
_BOM = b"\xef\xbb\xbf"
_TEXT_ROLES = frozenset({"primary", "copy", "generated", "marker"})
_LITERAL_DESTS = frozenset({"L4", "L5t", "L5m"})
_G3_CATEGORIES = frozenset({"publishable", "device"})
_FENCE_KINDS = frozenset({"fence_open", "fence_body", "fence_close"})
_SRC_MARKER_RE = re.compile(
    r"^<!-- declutter:src (?P<source>\S+):(?P<a>\d+)-(?P<b>\d+) sha256=(?P<sha>[0-9a-f]{64}) "
    r"unit=(?P<unit>\S+) plan=(?P<plan>\S+) -->$"
)

# Posterior states (spec §11.4) and the code each one gives (strict-only ones apart).
_STATE_CODE_4 = frozenset(
    {"history_altered", "marker_missing", "excerpt_altered", "pin_missing", "protected_altered"}
    | {"distill_invalid"}
)
# ``file_edited`` (lines added outside every placement) is informational: no literal or
# generated span changed. Under ``--strict`` it is a line without class (C5) and gives code
# 4 like any other edit after ``apply`` (spec §11.4, §15.1.7), except in a file holding
# L4/L5t/L5m literals: those are found by their markers, and lines added around them leave
# the literal intact (spec §15.1.12).
_STATE_STRICT_4 = frozenset({"edited_after", "generated_edited", "control_edited", "file_edited"})
# Every state the posterior mode can report (each has an i18n label ``reorg.<state>``).
POSTERIOR_STATES: frozenset[str] = frozenset(
    {"intact", "edited_after", "literal_intact", "history_altered", "marker_missing"}
    | {"excerpt_intact", "excerpt_altered", "pin_intact", "pin_missing", "released"}
    | {"protected_intact", "protected_altered", "generated_edited", "control_edited"}
    | {"file_edited", "distilled", "card_distilled", "control_appended", "distill_invalid"}
)
_STATE_CHECK: Mapping[str, str] = {
    "history_altered": "C2",
    "marker_missing": "C2",
    "excerpt_altered": "C2",
    "edited_after": "C2",
    "pin_missing": "C9",
    "protected_altered": "C9",
    "generated_edited": "C5",
    "control_edited": "C5",
    "distill_invalid": "C9",
    "file_edited": "C5",
}


@cache
def _default_lexicon() -> Lexicon:
    return build_lexicon({})


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rng(a: int, b: int) -> tuple[int, int]:
    return (a, b)


def _decode(line: bytes) -> str | None:
    try:
        return mdblocks.decode_line(line)
    except UnicodeDecodeError:
        return None


def _is_preamble(u: UnitEntry) -> bool:
    return u.heading_key == u.id


# --------------------------------------------------------------------------- public helpers


def describe(f: ProofFailure) -> str:
    """One line per failure: ``C2 U13 CLAUDE.md:202-260 → docs/x.md:41-99: <reason>``."""
    parts = [f.check]
    if f.unit:
        parts.append(f.unit)
    where = ""
    if f.source:
        where = f.source + (f":{f.src[0]}-{f.src[1]}" if f.src else "")
    if f.dest:
        to = f.dest + (f":{f.at[0]}-{f.at[1]}" if f.at else "")
        where = f"{where} → {to}" if where else to
    if where:
        parts.append(where)
    return " ".join(parts) + f": {f.reason}"


def exit_code(result: ProofResult) -> int:
    """0 when the proof holds, 4 otherwise (spec §10.5)."""
    return EXIT_OK if result.ok else EXIT_PROOF


def to_issues(result: ProofResult) -> tuple[Issue, ...]:
    """The failures as :class:`Issue` records (code 4, key = check)."""
    return tuple(
        Issue(
            code=EXIT_PROOF,
            key=f.check,
            unit=f.unit,
            source=f.source,
            src=f.src,
            dest=f.dest,
            at=f.at,
            detail=f.reason,
        )
        for f in result.failures
    )


# --------------------------------------------------------------------------- context


class _Proof:
    def __init__(
        self,
        manifest: Manifest,
        placements: Sequence[Placement],
        read: Read,
        original: Callable[[str], bytes],
        lexicon: Lexicon,
        backup: Read | None = None,
    ) -> None:
        self.m = manifest
        self._backup = backup
        self.places = tuple(placements)
        self._read = read
        self._original = original
        self.lex = lexicon
        self.failures: list[ProofFailure] = []
        self._raw: dict[str, bytes | None] = {}
        self._lines: dict[str, tuple[bytes, ...] | None] = {}
        self._src: dict[str, tuple[bytes, ...]] = {}
        self.units = {u.id: u for u in manifest.units}
        self.source_index = {s.path: i for i, s in enumerate(manifest.sources)}
        self.outputs = {o.path: o.mode for o in manifest.outputs}

    # ---------------------------------------------------------------- readers

    def raw(self, path: str) -> bytes | None:
        if path not in self._raw:
            self._raw[path] = self._read(path)
        return self._raw[path]

    def lines(self, path: str) -> tuple[bytes, ...] | None:
        if path not in self._lines:
            data = self.raw(path)
            self._lines[path] = None if data is None else mdblocks.split_lines(data).lines
        return self._lines[path]

    def src_lines(self, path: str) -> tuple[bytes, ...]:
        if path not in self._src:
            self._src[path] = mdblocks.split_lines(self._original(path)).lines
        return self._src[path]

    def span(self, p: Placement) -> tuple[bytes, ...] | None:
        lines = self.lines(p.dest)
        a, b = p.at
        if lines is None or a < 1 or b < a or b > len(lines):
            return None
        return lines[a - 1 : b]

    def fail(
        self,
        check: str,
        reason: str,
        *,
        unit: str | None = None,
        source: str | None = None,
        src: tuple[int, int] | None = None,
        dest: str | None = None,
        at: tuple[int, int] | None = None,
    ) -> None:
        self.failures.append(ProofFailure(check, unit, source, src, dest, at, reason))

    def fail_at(self, check: str, p: Placement, reason: str) -> None:
        self.fail(check, reason, unit=p.unit, source=p.source, src=p.src, dest=p.dest, at=p.at)

    # ---------------------------------------------------------------- lookups

    def primaries(self, source: str | None = None) -> list[Placement]:
        ps = [p for p in self.places if p.role == "primary"]
        if source is not None:
            ps = [p for p in ps if p.source == source]
        return ps

    def unit_of_line(self, source: str, i: int) -> UnitEntry | None:
        for u in self.m.units:
            if u.source == source and u.lines[0] <= i <= u.lines[1]:
                return u
        return None

    def trampas_path(self) -> str:
        return PATHS["trampas"]["en" if self.m.paths_lang == "en" else "es"]

    # ---------------------------------------------------------------- C0

    def c0(self) -> None:
        for s in self.m.sources:
            data = self._original(s.path)
            if _sha(data) != s.sha256 or len(data) != s.bytes:
                self.fail(
                    "C0",
                    f"source changed since plan ({len(data)} B vs {s.bytes} B, "
                    f"sha256 {_sha(data)[:12]} vs {s.sha256[:12]})",
                    source=s.path,
                    src=_rng(1, max(len(self.src_lines(s.path)), 1)),
                )

    # ---------------------------------------------------------------- C1

    def c1(self) -> None:
        for p in self.primaries():
            if p.source not in self.source_index:
                self.fail_at("C1", p, "primary of an unknown source")
        for s in self.m.sources:
            n = len(self.src_lines(s.path))
            ps = sorted(
                (p for p in self.primaries(s.path) if p.src is not None),
                key=lambda p: p.src or (0, 0),
            )
            for p in self.primaries(s.path):
                if p.src is None:
                    self.fail_at("C1", p, "primary without a source range")
            if n == 0:
                for p in ps:
                    self.fail_at("C1", p, "primary of an empty source")
                continue
            expected = 1
            for p in ps:
                assert p.src is not None
                a, b = p.src
                if a > expected:
                    self.fail_at("C1", p, f"source lines {expected}-{a - 1} have no primary")
                elif a < expected:
                    self.fail_at("C1", p, f"source lines {a}-{expected - 1} have two primaries")
                if b < a:
                    self.fail_at("C1", p, "empty source range")
                expected = max(expected, b + 1)
            if expected <= n:
                self.fail(
                    "C1",
                    f"source lines {expected}-{n} have no primary",
                    source=s.path,
                    src=_rng(expected, n),
                )
            elif expected > n + 1:
                self.fail(
                    "C1",
                    f"primaries reach line {expected - 1} of a {n}-line source",
                    source=s.path,
                    src=_rng(n + 1, expected - 1),
                )

    # ---------------------------------------------------------------- C2

    def c2(self) -> None:
        for p in self.places:
            if p.role not in ("primary", "copy"):
                continue
            if p.source is None or p.src is None or p.source not in self.source_index:
                self.fail_at("C2", p, "literal span without a known source range")
                continue
            src = self.src_lines(p.source)
            a, b = p.src
            if a < 1 or b < a or b > len(src):
                self.fail_at("C2", p, f"source range outside the {len(src)}-line source")
                continue
            lines = self.lines(p.dest)
            if lines is None:
                self.fail_at("C2", p, "destination missing")
                continue
            span = self.span(p)
            want = src[a - 1 : b]
            if span is None:
                self.fail_at("C2", p, f"range outside the {len(lines)}-line destination")
                continue
            if len(span) != len(want):
                self.fail_at("C2", p, f"{len(span)} destination lines vs {len(want)} source lines")
                continue
            for j, (got, exp) in enumerate(zip(span, want, strict=True)):
                if got != exp:
                    self.fail_at(
                        "C2",
                        p,
                        f"destination line {p.at[0] + j} differs from source line {a + j} "
                        f"({len(got)} B vs {len(exp)} B)",
                    )
                    break
            if mdblocks.lines_sha(span) != p.sha256:
                self.fail_at("C2", p, "span sha256 differs from the placement")

    # ---------------------------------------------------------------- C3 and C4

    def _primary_lines(self, source: str) -> list[bytes] | None:
        out: list[bytes] = []
        ok = True
        ps = [p for p in self.primaries(source) if p.src is not None]
        for p in sorted(ps, key=lambda p: p.src or (0, 0)):
            span = self.span(p)
            if span is None:
                ok = False
                continue
            out.extend(span)
        return out if ok else None

    def c3(self) -> None:
        for s in self.m.sources:
            lines = self._primary_lines(s.path)
            if lines is None:
                self.fail("C3", "a primary span cannot be read", source=s.path)
                continue
            rebuilt = (_BOM if s.bom else b"") + b"\n".join(lines)
            if s.final_newline:
                rebuilt += b"\n"
            if _sha(rebuilt) != s.sha256:
                self.fail(
                    "C3",
                    f"reconstruction differs ({len(rebuilt)} B vs {s.bytes} B)",
                    source=s.path,
                    src=_rng(1, max(len(lines), 1)),
                )

    def c4(self) -> None:
        for s in self.m.sources:
            lines = self._primary_lines(s.path) or []
            want = Counter(_sha(x) for x in self.src_lines(s.path))
            got = Counter(_sha(x) for x in lines)
            if want == got:
                continue
            missing = sorted((want - got).elements())
            extra = sorted((got - want).elements())
            sample = ", ".join(h[:12] for h in (missing + extra)[:3])
            self.fail(
                "C4",
                f"{len(missing)} line hashes missing, {len(extra)} extra ({sample})",
                source=s.path,
                src=_rng(1, max(len(self.src_lines(s.path)), 1)),
            )

    # ---------------------------------------------------------------- C5

    def _rewritable(self, path: str) -> bool:
        return path in self.source_index or (path == CLAUDE and self.m.symlink_claude)

    def c5(self, written: frozenset[str] | None) -> None:
        dests = {p.dest for p in self.places}
        expected = written if written is not None else frozenset(dests)
        for path in sorted(set(self.outputs) ^ expected):
            why = "not in outputs" if path in expected else "in outputs but not written"
            self.fail("C5", f"file {why}", dest=path)
        for path in sorted(dests - set(self.outputs)):
            self.fail("C5", "placement in a file outside outputs", dest=path)
        for s in self.m.sources:
            if self.outputs.get(s.path) != "rewritten":
                self.fail("C5", "source not rewritten", source=s.path, dest=s.path)
        for path, mode in sorted(self.outputs.items()):
            if mode == "rewritten" and not self._rewritable(path):
                self.fail("C5", "rewritten file is not a source", dest=path)
            ps = sorted((p for p in self.places if p.dest == path), key=lambda p: p.at)
            if not ps:
                self.fail("C5", "output without placements", dest=path)
                continue
            if mode == "control":
                self._c5_control(path, ps)
            elif any(p.role == "control" for p in ps):
                self.fail("C5", f"control placement in a {mode} file", dest=path)
            else:
                self._c5_file(path, mode, ps)
        self._c5_trampas_index()

    def _c5_control(self, path: str, ps: Sequence[Placement]) -> None:
        if len(ps) != 1 or ps[0].role != "control":
            self.fail("C5", "control file needs exactly one control placement", dest=path)
            return
        data = self.raw(path)
        if data is None:
            self.fail("C5", "control file missing", dest=path)
        elif _sha(data) != ps[0].sha256:
            self.fail("C5", f"control file sha256 differs ({len(data)} B)", dest=path)

    def _c5_file(self, path: str, mode: str, ps: Sequence[Placement]) -> None:
        data = self.raw(path)
        lines = self.lines(path)
        if data is None or lines is None:
            self.fail("C5", "file missing", dest=path)
            return
        m = len(lines)
        first = 1
        prefixes = [p for p in ps if p.role == "extended_prefix"]
        if mode == "extended":
            if len(prefixes) != 1 or prefixes[0].at[0] != 1:
                self.fail("C5", "extended file needs one prefix placement at line 1", dest=path)
                return
            pre = prefixes[0]
            if not self._c5_prefix(path, data, lines, pre):
                return
            first = pre.at[1] + 1
            blank = next((p for p in ps if p.at[0] == first), None)
            if (
                blank is None
                or blank.role != "generated"
                or blank.at != (first, first)
                or first > m
                or lines[first - 1] != b""
            ):
                self.fail(
                    "C5",
                    "no generated blank line after the prefix",
                    dest=path,
                    at=_rng(first, first),
                )
        else:
            if prefixes:
                self.fail("C5", f"prefix placement in a {mode} file", dest=path)
            if data.startswith(_BOM):
                self.fail("C5", "file starts with a BOM", dest=path, at=_rng(1, 1))
        if not data.endswith(b"\n"):
            self.fail("C5", "file does not end with a newline", dest=path, at=_rng(m, m))
        nxt = first
        for p in ps:
            if p.role == "extended_prefix":
                continue
            if p.role not in _TEXT_ROLES:
                self.fail_at("C5", p, f"unexpected placement role {p.role}")
                continue
            if p.at[0] != nxt:
                why = "lines without placement" if p.at[0] > nxt else "overlapping placements"
                self.fail_at("C5", p, f"{why} before line {p.at[0]}")
            nxt = max(nxt, p.at[1] + 1)
            span = self.span(p)
            if span is None:
                self.fail_at("C5", p, f"range outside the {m}-line file")
                continue
            if p.role in ("generated", "marker") and mdblocks.lines_sha(span) != p.sha256:
                self.fail_at("C5", p, f"{p.role} span sha256 differs")
            if p.role in ("copy", "generated") and not mdblocks.is_balanced(
                span, front_matter=p.at[0] == 1
            ):
                self.fail_at("C5", p, f"{p.role} span not balanced")
        if nxt - 1 != m:
            self.fail(
                "C5",
                f"placements cover lines 1-{nxt - 1} of a {m}-line file",
                dest=path,
                at=_rng(min(nxt, m), m) if nxt <= m else _rng(m, nxt - 1),
            )
        texts = [_decode(x) for x in lines]
        if any(t is None for t in texts):
            self.fail("C5", "destination is not valid UTF-8", dest=path)
            return
        try:
            mdblocks.scan([t for t in texts if t is not None], strict=True)
        except mdblocks.SourceError as exc:
            line = exc.line or 1
            self.fail("C5", "destination has an open structure", dest=path, at=_rng(line, line))

    def _c5_prefix(self, path: str, data: bytes, lines: Sequence[bytes], pre: Placement) -> bool:
        n = pre.at[1]
        if n > len(lines) or mdblocks.lines_sha(lines[:n]) != pre.sha256:
            self.fail("C5", "prefix of the extended file differs", dest=path, at=pre.at)
            return False
        if self._backup is None:
            return True
        prev = self._backup(path)
        if prev is None:
            self.fail("C5", "backup missing", dest=path, at=pre.at)
            return False
        if _sha(prev) != pre.backup_sha256:
            self.fail("C5", f"backup sha256 differs ({len(prev)} B)", dest=path, at=pre.at)
            return False
        if pre.prefix_newline_added == prev.endswith(b"\n") and prev:
            self.fail("C5", "prefix newline flag inconsistent with backup", dest=path, at=pre.at)
            return False
        expected = prev + (b"\n" if pre.prefix_newline_added else b"")
        if not data.startswith(expected):
            self.fail("C5", "backup is not a byte prefix of the file", dest=path, at=pre.at)
            return False
        return True

    def _c5_trampas_index(self) -> None:
        trampas = self.trampas_path()
        index: list[str] = []
        for p in self.places:
            if p.dest == AGENTS and p.role == "generated" and p.kind == "pitfall-index":
                for line in self.span(p) or ():
                    text = _decode(line)
                    if text is not None:
                        index.append(text)
        for p in self.places:
            if p.role != "copy" or p.dest != trampas or p.unit is None:
                continue
            anchor = f"r{self.m.id}-{p.unit.lower()}"
            if p.block:
                anchor += "-" + p.block.rsplit(".", 1)[-1].lower()
            link = f"{trampas}#{anchor}"
            if not any(t.endswith(link) or f"{link} (" in t for t in index):
                self.fail_at("C5", p, "pitfall copy without its index line in AGENTS.md")

    # ---------------------------------------------------------------- C6 (G2, G3, G4)

    def c6(self) -> None:
        copies_in_agents: dict[str, list[tuple[int, int]]] = {}
        for p in self.places:
            if p.role == "copy" and p.dest == AGENTS and p.source and p.src:
                copies_in_agents.setdefault(p.source, []).append(p.src)
        for s in self.m.sources:
            src = self.src_lines(s.path)
            texts = [_decode(x) for x in src]
            if any(t is None for t in texts):
                self.fail("C6", "source is not valid UTF-8", source=s.path)
                continue
            decoded = [t for t in texts if t is not None]
            kinds = mdblocks.scan(decoded, strict=False)
            primary_at: dict[int, Placement] = {}
            for p in self.primaries(s.path):
                if p.src is not None:
                    for i in range(p.src[0], p.src[1] + 1):
                        primary_at.setdefault(i, p)
            units = [u for u in self.m.units if u.source == s.path]
            titles = {u.lines[0]: u for u in units if not _is_preamble(u)}
            acks: list[tuple[int, int, str]] = [
                (b.lines[0], b.lines[1], b.guard_ack) for u in units for b in u.blocks
            ]
            for i, text in enumerate(decoded, 1):
                cats = self.lex.safety(text)
                if not cats:
                    continue
                prim = primary_at.get(i)
                dest = prim.dest if prim is not None else None
                u = self.unit_of_line(s.path, i)
                uid = u.id if u is not None else None
                label = ",".join(sorted(cats))
                copied = any(a <= i <= b for a, b in copies_in_agents.get(s.path, ()))
                acked = any(a <= i <= b and ack.strip() for a, b, ack in acks)
                if dest != AGENTS and not copied and not acked:
                    self.fail(
                        "C6",
                        f"line with safety categories ({label}) not in AGENTS.md",
                        unit=uid,
                        source=s.path,
                        src=_rng(i, i),
                        dest=dest,
                        at=None if prim is None else self._at_of(prim, i),
                    )
                g3 = cats & _G3_CATEGORIES
                in_l2_l1c = dest is not None and (dest == CLAUDE or dest.endswith("/AGENTS.md"))
                if u is not None and (u.dest == "L1c" or u.dest.startswith("L2:")):
                    in_l2_l1c = True
                if g3 and in_l2_l1c:
                    self.fail(
                        "G3",
                        f"line with {','.join(sorted(g3))} has its primary in L2 or L1c",
                        unit=uid,
                        source=s.path,
                        src=_rng(i, i),
                        dest=dest,
                        at=None if prim is None else self._at_of(prim, i),
                    )
                is_title_in_l1 = i in titles and dest == AGENTS
                if (
                    mdblocks.H2_RE.match(text)
                    and kinds[i - 1] not in _FENCE_KINDS
                    and not is_title_in_l1
                ):
                    self.fail(
                        "C6",
                        f"'##' line with safety categories ({label}) is not a unit title in L1",
                        unit=uid,
                        source=s.path,
                        src=_rng(i, i),
                        dest=dest,
                        at=None if prim is None else self._at_of(prim, i),
                    )
            for u in units:
                if _is_preamble(u):
                    continue
                i = u.lines[0]
                text = decoded[i - 1] if 0 < i <= len(decoded) else ""
                cats = self.lex.safety(text)
                prim = primary_at.get(i)
                dest = prim.dest if prim is not None else None
                if (cats and dest != AGENTS) or (u.protected and u.dest != "L1"):
                    self.fail(
                        "G2",
                        "unit with safety categories in its title has its primary outside L1",
                        unit=u.id,
                        source=s.path,
                        src=_rng(i, i),
                        dest=dest,
                    )

    @staticmethod
    def _at_of(p: Placement, i: int) -> tuple[int, int]:
        assert p.src is not None
        line = p.at[0] + i - p.src[0]
        return (line, line)

    # ---------------------------------------------------------------- C7

    def c7(self) -> None:
        ps = sorted(
            (
                p
                for p in self.primaries()
                if p.dest == AGENTS and p.kind != "front_matter" and p.src is not None
            ),
            key=lambda p: p.at,
        )
        prev: tuple[int, int, int] | None = None
        for p in ps:
            assert p.src is not None
            u = self.units.get(p.unit or "")
            key = (
                0 if u is not None and _is_preamble(u) else 1,
                self.source_index.get(p.source or "", len(self.source_index)),
                p.src[0],
            )
            if prev is not None and key <= prev:
                self.fail_at("C7", p, "L1 primary out of (source, line) order in AGENTS.md")
            prev = key

    # ---------------------------------------------------------------- C8

    def c8a(self, written: frozenset[str] | None, new_status: frozenset[str] | None) -> None:
        outs = frozenset(self.outputs)
        if written is None:
            self.fail("C8a", "set of written files not provided")
        else:
            for path in sorted(written - outs):
                self.fail("C8a", "file written outside outputs", dest=path)
            for path in sorted(outs - written):
                self.fail("C8a", "output not written", dest=path)
        for path, mode in sorted(self.outputs.items()):
            if mode == "rewritten" and not self._rewritable(path):
                self.fail("C8a", "rewritten file is not a source", dest=path)
        if new_status is not None:
            for path in sorted(new_status - outs):
                self.fail("C8a", "new working-tree change outside outputs", dest=path)

    def c8b(self, diff_names: frozenset[str] | None, base: Read | None = None) -> None:
        outs = frozenset(self.outputs)
        if diff_names is None:
            self.fail("C8b", "names changed by the commit not provided")
            return
        for path in sorted(diff_names ^ outs):
            if path not in diff_names and base is not None:
                # An output already at its final bytes before the commit (for example
                # the final .declutter/.gitignore on a second run) cannot change; C5
                # proves its content, so only a real difference is required here.
                before, now = base(path), self.raw(path)
                if before is not None and now is not None and before == now:
                    continue
            why = (
                "changed by the commit but not in outputs"
                if path in diff_names
                else ("in outputs but not changed by the commit")
            )
            self.fail("C8b", why, dest=path)

    # ---------------------------------------------------------------- C9 (G8)

    def pins_and_events(
        self, check: str
    ) -> tuple[tuple[Pin, ...], tuple[Mapping[str, object], ...]] | None:
        data = self.raw(ratchet.PINS_REL)
        if data is None:
            self.fail(check, "pins.json missing", dest=ratchet.PINS_REL)
            return None
        try:
            pins = ratchet.pins_loads(data)
        except ratchet.RatchetSchemaError as exc:
            self.fail(
                check, f"pins.json invalid at {exc.where}: {exc.reason}", dest=ratchet.PINS_REL
            )
            return None
        log = self.raw(ratchet.LOG_REL)
        try:
            events = ratchet.log_events(log or b"")
        except ratchet.RatchetSchemaError as exc:
            self.fail(
                check, f"ratchet.log invalid at {exc.where}: {exc.reason}", dest=ratchet.LOG_REL
            )
            return None
        return pins, events

    def _block_categories(self, source: str, a: int, b: int, guard: Iterable[str]) -> set[str]:
        src = self.src_lines(source)
        texts = [_decode(x) for x in src[a - 1 : b]]
        cats = set(guard)
        if all(t is not None for t in texts):
            cats |= self.lex.safety("\n".join(t for t in texts if t is not None))
        return cats

    def c9(self) -> None:
        got = self.pins_and_events("C9")
        if got is None:
            return
        pins, events = got
        have = {(p.file, p.sha256) for p in pins}
        unit_pins = {(p.file, p.sha256) for p in pins if p.role == "unit"}
        for p in self.places:
            if p.role == "copy" and p.dest == AGENTS and (AGENTS, p.sha256) not in have:
                self.fail_at("C9", p, "copy in AGENTS.md without a pin")
        for p in self.primaries():
            if p.dest != AGENTS or p.src is None or p.source not in self.source_index:
                continue
            u = self.units.get(p.unit or "")
            if u is None:
                continue
            src = self.src_lines(p.source)
            for blk in u.blocks:
                a, b = blk.lines
                if not (p.src[0] <= a and b <= p.src[1]) or b > len(src):
                    continue
                if not self._block_categories(p.source, a, b, blk.guard):
                    continue
                if (AGENTS, mdblocks.lines_sha(src[a - 1 : b])) not in have:
                    off = p.at[0] - p.src[0]
                    self.fail(
                        "C9",
                        f"L1 block {blk.id} with safety categories has no pin",
                        unit=u.id,
                        source=p.source,
                        src=blk.lines,
                        dest=AGENTS,
                        at=_rng(a + off, b + off),
                    )
        for u in self.m.units:
            if not u.protected or u.source not in self.source_index:
                continue
            a, b = u.lines
            sha = mdblocks.lines_sha(self.src_lines(u.source)[a - 1 : b])
            if (AGENTS, sha) not in unit_pins:
                self.fail(
                    "C9", "protected unit has no unit pin", unit=u.id, source=u.source, src=u.lines
                )
        for pin in pins:
            lines = self.lines(pin.file)
            if lines is not None and ratchet.find_window(lines, pin.sha256, pin.lines) is not None:
                continue
            if ratchet.pin_released(pin, events, pins):
                continue
            self.fail(
                "C9",
                f"pin {pin.id} ({pin.role}, {pin.lines} lines) not found in its file",
                unit=pin.unit,
                dest=pin.file,
                at=_rng(1, pin.lines),
            )

    # ---------------------------------------------------------------- posterior

    def positional_match(self) -> bool:
        """True if every placement matches its sha at its position and no file grew."""
        ends: dict[str, int] = {}
        for p in self.places:
            if p.role == "control":
                data = self.raw(p.dest)
                if data is None or _sha(data) != p.sha256:
                    return False
                continue
            span = self.span(p)
            if span is None or mdblocks.lines_sha(span) != p.sha256:
                return False
            ends[p.dest] = max(ends.get(p.dest, 0), p.at[1])
        for path, end in ends.items():
            lines = self.lines(path)
            if lines is None or len(lines) != end:
                return False
        return True

    def posterior(self, strict: bool) -> list[tuple[str, str]]:
        states: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(
            key: str,
            state: str,
            p: Placement | None = None,
            detail: str = "",
            *,
            informative: bool = False,
        ) -> None:
            if (key, state) in seen:
                return
            seen.add((key, state))
            states.append((key, state))
            if informative:
                return
            if state in _STATE_CODE_4 or (strict and state in _STATE_STRICT_4):
                reason = state + (f" ({detail})" if detail else "")
                check = _STATE_CHECK[state]
                if p is not None:
                    self.fail_at(check, p, reason)
                else:
                    self.fail(
                        check,
                        reason,
                        unit=key if key in self.units else None,
                        dest=(None if key in self.units else key),
                    )

        got = self.pins_and_events("C9")
        pins: tuple[Pin, ...] = ()
        events: tuple[Mapping[str, object], ...] = ()
        if got is not None:
            pins, events = got
        # Fase 2b §10.1: the expected sha of each placement after the distill events.
        over = ov.build(self.m, self.places, events, pins)

        ends: dict[str, int] = {}
        literal_files: set[str] = set()
        places = self.places
        order = sorted(range(len(places)), key=lambda k: (places[k].dest, places[k].at))
        for k in order:
            p = self.places[k]
            if p.role != "control":
                ends[p.dest] = max(ends.get(p.dest, 0), p.at[1])
            u = self.units.get(p.unit or "")
            if k in over.removed:
                if p.role != "copy":  # a copy is evaluated through its pin
                    add(p.dest, "distilled")
                continue
            if k in over.changed:
                exp = over.expected[k]
                assert exp is not None
                # Position-checked (fail closed): the card from line 1, the rest once.
                starts = self._starts(p.dest, exp[0], exp[1])
                placed = starts == [1] if p.kind == "card" else len(starts) == 1
                if placed:
                    add(p.dest, "card_distilled" if p.kind == "card" else "distilled")
                else:
                    add(p.dest, "generated_edited", p)
                continue
            if p.role == "primary":
                if u is not None and u.dest in _LITERAL_DESTS:
                    literal_files.add(p.dest)
                    add(u.id, self._literal_state(p), p)
                elif u is not None and u.protected:
                    continue  # evaluated through its unit pin
                else:
                    found = self._window(p)
                    add(p.unit or p.dest, "intact" if found else "edited_after", p)
            elif p.role == "copy":
                if p.dest == AGENTS:
                    continue  # evaluated through its pin
                found = self._window(p)
                add(p.unit or p.dest, "excerpt_intact" if found else "excerpt_altered", p)
            elif p.role == "marker" and p.kind in ("src", "end"):
                continue  # evaluated with the literal class
            elif p.role == "control":
                data = self.raw(p.dest)
                if data is None or _sha(data) != p.sha256:
                    state = self._control_state(p, data, pins, events)
                    add(p.dest, state, p, informative=state == "control_appended")
            elif not self._window(p):
                add(p.dest, "generated_edited", p)
        for span in over.generated:
            starts = self._starts(span.dest, span.sha256, span.lines)
            once = len(starts) == 1 if span.kind != "blank" else bool(starts)
            if once:
                add(span.dest, "distilled")
            else:
                add(span.dest, "generated_edited", None, f"{span.kind}, {span.lines} lines")
        for name in over.invalid:
            add(ratchet.LOG_REL, "distill_invalid", None, name)
        for path, end in sorted(ends.items()):
            lines = self.lines(path)
            want = end + over.deltas.get(path, 0)
            if lines is not None and len(lines) != want:
                detail = f"{len(lines)} lines vs {want}"
                add(path, "file_edited", None, detail, informative=path in literal_files)

        for pin in pins:
            lines = self.lines(pin.file)
            found = lines is not None and (
                ratchet.find_window(lines, pin.sha256, pin.lines) is not None
            )
            unit_role = pin.role == "unit"
            if found:
                add(pin.unit, "protected_intact" if unit_role else "pin_intact")
            elif ratchet.pin_released(pin, events, pins):
                last = ratchet.releasing_event(pin, events)
                by_distill = last is not None and last.get("event") == "distill"
                add(pin.unit, "distilled" if by_distill else "released")
            else:
                add(
                    pin.unit,
                    "protected_altered" if unit_role else "pin_missing",
                    None,
                    f"pin {pin.id} ({pin.role}), {pin.lines} lines",
                )
        pinned_units = {p.unit for p in pins if p.role == "unit" and p.reorg == self.m.id}
        for p in self.primaries():
            u = self.units.get(p.unit or "")
            if u is None or not u.protected or u.id in pinned_units:
                continue
            add(u.id, "protected_intact" if self._window(p) else "protected_altered", p)
        return states

    def _starts(self, dest: str, sha256: str, n: int) -> list[int]:
        lines = self.lines(dest)
        if lines is None or n < 1 or n > len(lines):
            return []
        return [
            s + 1
            for s in range(len(lines) - n + 1)
            if mdblocks.lines_sha(lines[s : s + n]) == sha256
        ]

    def _control_state(
        self,
        p: Placement,
        data: bytes | None,
        pins: Sequence[Pin],
        events: Sequence[Mapping[str, object]],
    ) -> str:
        """``control_appended`` (fase 2b §10.1) when the only change since the run is
        audited events appended to ``ratchet.log``, pins appended to ``pins.json`` (with the
        entries of the run identical, save those changed by a logged ``repin``) or a
        ``ratchet.json`` written by the last ``tighten``; else ``control_edited``."""
        if data is None:
            return "control_edited"
        if p.dest == ratchet.LOG_REL:
            n = p.at[1] - p.at[0] + 1
            lines = mdblocks.split_lines(data).lines
            head = b"".join(x + b"\n" for x in lines[:n])
            ok = len(lines) >= n and data.startswith(head) and _sha(head) == p.sha256
            return "control_appended" if ok else "control_edited"
        if p.dest == ratchet.PINS_REL:
            first: dict[str, Mapping[str, object]] = {}
            for e in events:
                pid, old = e.get("pin"), e.get("old")
                if e.get("event") == "repin" and isinstance(pid, str) and isinstance(old, Mapping):
                    first.setdefault(pid, old)
            restored: list[Pin] = []
            for x in pins:
                orig = first.get(x.id) or {}
                sha, nl = orig.get("sha256"), orig.get("lines")
                if isinstance(sha, str) and isinstance(nl, int) and not isinstance(nl, bool):
                    x = replace(x, sha256=sha, lines=nl)
                restored.append(x)
            for k in range(len(restored) + 1):
                if _sha(ratchet.pins_dumps(restored[:k])) == p.sha256:
                    return "control_appended"
            return "control_edited"
        if p.dest == ratchet.RATCHET_REL:
            return "control_appended" if self._tighten_chain(p, data, events) else "control_edited"
        return "control_edited"

    def _tighten_chain(
        self, p: Placement, data: bytes, events: Sequence[Mapping[str, object]]
    ) -> bool:
        """``ratchet.json`` reached from the run's file only through ``tighten`` events
        (fase 2b §7.5): each one only lowers ``S1_max``/``S3_max_pct``, starts from the
        previous state (the run's sha, then the previous ``file_sha256``), leaves every
        other field as it is and records the sha of the file it wrote."""
        try:
            cur = ratchet.ratchet_loads(data)
        except ratchet.RatchetSchemaError:
            return False
        state_sha = p.sha256
        marks = [
            i
            for i, e in enumerate(events)
            if e.get("event") == "baseline" and e.get("reorg") == self.m.id
        ]
        scope = events[marks[-1] + 1 :] if marks else ()
        tightens = [e for e in scope if e.get("event") == "tighten"]
        if not tightens:
            return False
        for e in tightens:
            old, new = e.get("old"), e.get("new")
            if not isinstance(old, Mapping) or not isinstance(new, Mapping):
                return False
            vals = [old.get("S1_max"), old.get("S3_max_pct"), new.get("S1_max")]
            vals.append(new.get("S3_max_pct"))
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
                return False
            o1, o3, n1, n3 = (int(str(v)) for v in vals)
            if n1 > o1 or n3 > o3:
                return False
            before = ratchet.ratchet_dumps(replace(cur, s1_max=o1, s3_max_pct=o3))
            after = ratchet.ratchet_dumps(replace(cur, s1_max=n1, s3_max_pct=n3))
            if _sha(before) != state_sha or e.get("file_sha256") != _sha(after):
                return False
            state_sha = _sha(after)
        return state_sha == _sha(data)

    def _window(self, p: Placement) -> bool:
        lines = self.lines(p.dest)
        n = p.at[1] - p.at[0] + 1
        return lines is not None and ratchet.find_window(lines, p.sha256, n) is not None

    def _literal_state(self, p: Placement) -> str:
        lines = self.lines(p.dest)
        if lines is None or p.unit is None:
            return "marker_missing"
        start: int | None = None
        found: re.Match[str] | None = None
        for i, line in enumerate(lines):
            text = _decode(line)
            m = _SRC_MARKER_RE.match(text) if text is not None else None
            if m and m["unit"] == p.unit and m["plan"] == self.m.id:
                start, found = i, m
                break
        if start is None or found is None:
            return "marker_missing"
        end_line = f"<!-- declutter:end {p.unit} -->".encode()
        end = next((j for j in range(start + 1, len(lines)) if lines[j] == end_line), None)
        if end is None:
            return "marker_missing"
        if (
            found["sha"] != p.sha256
            or found["source"] != p.source
            or p.src != (int(found["a"]), int(found["b"]))
        ):
            return "history_altered"
        if mdblocks.lines_sha(lines[start + 1 : end]) != p.sha256:
            return "history_altered"
        return "literal_intact"


# --------------------------------------------------------------------------- verify


def verify(
    manifest: Manifest,
    placements: Sequence[Placement],
    read: Read,
    original: Callable[[str], bytes],
    *,
    checks: frozenset[str],
    posterior: bool = False,
    strict: bool = False,
    written: frozenset[str] | None = None,
    new_status: frozenset[str] | None = None,
    diff_names: frozenset[str] | None = None,
    backup: Read | None = None,
    lexicon: Lexicon | None = None,
    base: Read | None = None,
) -> ProofResult:
    """Run the lossless proof (spec §7) over ``placements`` read through ``read``.

    ``checks`` selects among C0-C9 (``CHECKS_STAGE``, ``CHECKS_DISK``, ``CHECKS_REV``);
    C6 always includes G2-G4. ``written`` and ``new_status`` feed C8a, ``diff_names`` C8b,
    and ``backup`` the backup comparison of C5 (c). ``base`` reads the revision before the
    commit (the tag): an output whose bytes there already equal the ones read is not
    required to appear in ``diff_names`` (C8b on a re-run). With ``posterior``, a result whose
    placements no longer match at their positions is evaluated by content (spec §11.4):
    ``states`` lists ``(unit or path, state)`` and ``ok`` is false only for states that
    give code 4 (with ``strict``, also ``edited_after`` and the generated/edited ones).
    """
    pr = _Proof(manifest, placements, read, original, lexicon or _default_lexicon(), backup)
    if posterior and not pr.positional_match():
        if "C0" in checks:
            pr.c0()
        states = pr.posterior(strict)
        return ProofResult(
            ok=not pr.failures,
            mode="posterior",
            failures=tuple(pr.failures),
            states=tuple(states),
        )
    if "C0" in checks:
        pr.c0()
    if "C1" in checks:
        pr.c1()
    if "C2" in checks:
        pr.c2()
    if "C3" in checks:
        pr.c3()
    if "C4" in checks:
        pr.c4()
    if "C5" in checks:
        pr.c5(written)
    if "C6" in checks:
        pr.c6()
    if "C7" in checks:
        pr.c7()
    if "C8a" in checks:
        pr.c8a(written, new_status)
    if "C8b" in checks:
        pr.c8b(diff_names, base)
    if "C9" in checks:
        pr.c9()
    return ProofResult(ok=not pr.failures, mode="exact", failures=tuple(pr.failures))
