"""Mutations with code 4 on the disk and on a git revision (spec §15.1 item 7, §7, §11.4).

``test_proof.py`` applies the same mutations to an in-memory result (``stage/``). Here a
real run is planned, accepted and applied with ``--commit``; then each mutation is made:

- on the **disk**: the exact proof over the working tree (the one ``apply`` runs at step 7,
  C0-C9 with C8a) gives 4, and so does ``declutter reorg verify <id> --strict``;
- on a **git revision**: the mutation is committed on ``declutter/reorg-<id>`` and
  ``declutter reorg verify <id> --rev HEAD --strict`` gives 4.

Mutations of the manifest or of ``placement.json`` live in the local run folder, which git
never tracks: they are made there and checked the same way. A file outside the scope is
an untracked file on the disk (C8a against ``git status``) and a committed file on the
revision (C8b over ``git diff --name-only declutter/pre-reorg-<id> HEAD``, the comparison of
``apply`` step 10; ``verify --rev`` compares the tag with the *recorded* reorg commit, so a
later commit is out of its reach by design).

Every failure names its check, a unit or file and, for line-level mutations, a range; no
output contains the seed ``ZQX-SEED-7731`` planted in the mutated lines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import cli, ratchet
from declutter_context.model import Config, Placement, Project, ProofResult
from declutter_context.reorg import EXIT_PROOF, gitops, mdblocks, proof
from declutter_context.reorg import apply as ap
from declutter_context.reorg import manifest as mf
from declutter_context.reorg.manifest import Output
from reorg_synth import accept_pending
from test_apply import FakePrompter, commit_all, git
from test_proof import SEED, SOURCES

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"
BOM = b"\xef\xbb\xbf"
HIST = "docs/historial/2026-10.md"
MAPA = "docs/mapa.md"
ZONE = "src/AGENTS.md"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


# --------------------------------------------------------------------------- applied run


@dataclass
class Run:
    project: Project
    run: Path

    @property
    def root(self) -> Path:
        return self.project.root

    def read(self, rel: str) -> bytes:
        return (self.root / rel).read_bytes()

    def write(self, rel: str, data: bytes) -> None:
        (self.root / rel).write_bytes(data)

    def lines(self, rel: str) -> list[bytes]:
        return list(mdblocks.split_lines(self.read(rel)).lines)

    def set_lines(self, rel: str, lines: list[bytes]) -> None:
        self.write(rel, b"".join(x + b"\n" for x in lines))

    def line_no(self, rel: str, needle: str) -> int:
        for i, ln in enumerate(self.lines(rel), 1):
            if needle.encode() in ln:
                return i
        raise AssertionError(f"not found in {rel}")

    @property
    def manifest(self) -> mf.Manifest:
        return mf.loads((self.run / mf.MANIFEST_NAME).read_bytes())

    @manifest.setter
    def manifest(self, m: mf.Manifest) -> None:
        (self.run / mf.MANIFEST_NAME).write_bytes(mf.dumps(m))

    @property
    def placements(self) -> tuple[Placement, ...]:
        return mf.placements_loads((self.run / ap.PLACEMENT_NAME).read_bytes())

    @placements.setter
    def placements(self, ps: tuple[Placement, ...]) -> None:
        (self.run / ap.PLACEMENT_NAME).write_bytes(mf.placements_dumps(ps))

    def place_at(self, rel: str, line: int) -> Placement:
        return next(p for p in self.placements if p.dest == rel and p.at[0] <= line <= p.at[1])

    def swap_placement(self, old: Placement, new: Placement) -> None:
        self.placements = tuple(new if p == old else p for p in self.placements)


def applied(make_project: MakeProject) -> Run:
    """The ``test_proof`` sources, planned, accepted and applied with ``--commit``."""
    project = make_project(git=True)
    (project.root / "src").mkdir()
    (project.root / "src" / "keep.txt").write_bytes(b"x\n")
    for rel, data in SOURCES.items():
        (project.root / rel).write_bytes(data)
    commit_all(project.root)
    cfg = Config()
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=cfg, lang="es", now=NOW))
    assert out.code == 0, out.issues
    accept_pending(project, cfg, RID, NOW)
    res = ap.apply(
        project,
        cfg,
        RID,
        ap.ApplyOptions(yes=True, commit=True),
        FakePrompter(tty=False),
        now=NOW,
        lang="es",
    )
    assert res.code == 0, res.issues
    assert gitops.head_branch(project.root) == f"declutter/reorg-{RID}"
    return Run(project, mf.run_dir(project.root, RID))


# --------------------------------------------------------------------------- mutations

Mutation = Callable[[Run], None]


def _edit(rel: str, needle: str, fn: Callable[[bytes], bytes]) -> Mutation:
    def m(r: Run) -> None:
        lines = r.lines(rel)
        i = r.line_no(rel, needle) - 1
        lines[i] = fn(lines[i])
        r.set_lines(rel, lines)

    return m


def _delete_line(r: Run) -> None:
    lines = r.lines(HIST)
    del lines[r.line_no(HIST, SEED) - 1]
    r.set_lines(HIST, lines)


def _lf_to_crlf(r: Run) -> None:
    r.write(HIST, r.read(HIST).replace(b"\n", b"\r\n"))


def _drop_final_newline(r: Run) -> None:
    last = max((p for p in r.placements if p.dest == ZONE), key=lambda p: p.at)
    assert last.role == "primary"
    assert SEED.encode() in r.lines(ZONE)[-1]
    r.write(ZONE, r.read(ZONE)[:-1])


def _prepend_bom(r: Run) -> None:
    r.write(MAPA, BOM + r.read(MAPA))


def _reorder(r: Run) -> None:
    lines = r.lines(HIST)
    i = r.line_no(HIST, SEED) - 1
    assert lines[i] != lines[i + 1]  # two *distinct* lines of the same span
    assert r.place_at(HIST, i + 1) == r.place_at(HIST, i + 2)
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    r.set_lines(HIST, lines)


def _src_shift(d0: int) -> Mutation:
    def m(r: Run) -> None:
        target = r.place_at(HIST, r.line_no(HIST, SEED))
        assert target.src is not None
        r.swap_placement(target, replace(target, src=(target.src[0] + d0, target.src[1])))

    return m


def _manifest_sha(r: Run) -> None:
    m = r.manifest
    r.manifest = replace(m, sources=(replace(m.sources[0], sha256="0" * 64), *m.sources[1:]))


def _placement_sha(r: Run) -> None:
    target = r.place_at(HIST, r.line_no(HIST, SEED))
    r.swap_placement(target, replace(target, sha256="0" * 64))


def _inject(r: Run) -> None:
    r.set_lines("AGENTS.md", [*r.lines("AGENTS.md"), f"inyectada {SEED}".encode()])


def _gen_opens_fence(r: Run) -> None:
    blank = next(p for p in r.placements if p.dest == MAPA and p.kind == "blank")
    lines = r.lines(MAPA)
    lines[blank.at[0] - 1] = f"~~~{SEED}".encode()
    r.set_lines(MAPA, lines)
    # Same sha as the new bytes: only the balance rule of C5 can see it.
    span = lines[blank.at[0] - 1 : blank.at[1]]
    r.swap_placement(blank, replace(blank, sha256=mdblocks.lines_sha(span)))


def _gen_opens_comment(r: Run) -> None:
    blank = next(p for p in r.placements if p.dest == MAPA and p.kind == "blank")
    lines = r.lines(MAPA)
    lines[blank.at[0] - 1] = f"<!-- {SEED}".encode()
    r.set_lines(MAPA, lines)
    span = lines[blank.at[0] - 1 : blank.at[1]]
    r.swap_placement(blank, replace(blank, sha256=mdblocks.lines_sha(span)))


def _rewritten_non_source(r: Run) -> None:
    m = r.manifest
    outs = tuple(Output(o.path, "rewritten") if o.path == MAPA else o for o in m.outputs)
    r.manifest = replace(m, outputs=outs)


def _outside(r: Run) -> None:
    r.write("otro.md", f"fuera {SEED}\n".encode())


def _pin_deleted(r: Run) -> None:
    pins = ratchet.pins_loads(r.read(ratchet.PINS_REL))
    r.write(ratchet.PINS_REL, ratchet.pins_dumps([x for x in pins if x.role != "copy"]))
    control = next(p for p in r.placements if p.dest == ratchet.PINS_REL)
    sha = hashlib.sha256(r.read(ratchet.PINS_REL)).hexdigest()
    r.swap_placement(control, replace(control, sha256=sha))


MUTATIONS: dict[str, tuple[Mutation, str]] = {
    "delete_line": (_delete_line, "C2"),
    "change_byte": (_edit(HIST, "Solo registro", lambda b: b.replace(b"Solo", b"Sole")), "C2"),
    "trim_trailing_space": (_edit(HIST, "Solo registro", lambda b: b.rstrip(b" ")), "C2"),
    "lf_to_crlf": (_lf_to_crlf, "C2"),
    "drop_final_newline": (_drop_final_newline, "C5"),
    "prepend_bom": (_prepend_bom, "C5"),
    "reorder_lines": (_reorder, "C2"),
    "src_gap": (_src_shift(1), "C1"),
    "src_overlap": (_src_shift(-1), "C1"),
    "manifest_sha": (_manifest_sha, "C3"),
    "placement_sha": (_placement_sha, "C2"),
    "copy_altered": (_edit("AGENTS.md", SEED, lambda b: b.replace(b"Nunca", b"Nunka")), "C2"),
    "injected_line": (_inject, "C5"),
    "generated_opens_fence": (_gen_opens_fence, "C5"),
    "generated_opens_comment": (_gen_opens_comment, "C5"),
    "rewritten_non_source": (_rewritten_non_source, "C5"),
    "outside_scope": (_outside, "C8"),
    "pin_deleted": (_pin_deleted, "C9"),
    "g2_altered": (_edit("AGENTS.md", "Todo dato", lambda b: b.replace(b"Todo", b"Cada")), "C9"),
}
FILE_LEVEL = frozenset({"outside_scope", "rewritten_non_source", "manifest_sha"})
RUN_FOLDER_ONLY = frozenset(
    {"src_gap", "src_overlap", "manifest_sha", "placement_sha", "rewritten_non_source"}
)
# ``verify`` (posterior mode) reports an altered copy in AGENTS.md through its pin (C9).
CLI_CHECK = {"copy_altered": "C9"}


# --------------------------------------------------------------------------- assertions


def _reader(root: Path) -> proof.Read:
    def read(rel: str) -> bytes | None:
        path = root / rel
        return path.read_bytes() if path.is_file() else None

    return read


def exact_disk(r: Run) -> ProofResult:
    """The exact proof of ``apply`` step 7 over the working tree (C0-C9 with C8a)."""
    j = ap.read_journal(r.run)
    assert j is not None
    original = _reader(r.run / "original")
    return proof.verify(
        r.manifest,
        r.placements,
        _reader(r.root),
        lambda rel: original(rel) or b"",
        checks=proof.CHECKS_DISK,
        written=frozenset(e.path for e in j.entries),
        new_status=gitops.status_paths(r.root),
        backup=_reader(r.run / "backup"),
    )


def exact_rev(r: Run) -> ProofResult:
    """The exact proof of ``apply`` step 10 over ``HEAD`` (C8b over tag..HEAD)."""
    original = _reader(r.run / "original")
    return proof.verify(
        r.manifest,
        r.placements,
        lambda rel: gitops.show_bytes(r.root, "HEAD", rel),
        lambda rel: original(rel) or b"",
        checks=proof.CHECKS_REV,
        strict=True,
        diff_names=gitops.diff_names(r.root, f"declutter/pre-reorg-{RID}", "HEAD"),
        backup=_reader(r.run / "backup"),
    )


def assert_failure(res: ProofResult, check: str, name: str) -> None:
    assert not res.ok
    assert proof.exit_code(res) == EXIT_PROOF
    hits = [f for f in res.failures if f.check.startswith(check)]
    assert hits, res.failures
    assert any(f.unit or f.dest or f.source for f in hits), hits
    if name not in FILE_LEVEL:
        assert any(f.src or f.at for f in hits), hits
    text = repr(res) + "".join(proof.describe(f) for f in res.failures)
    assert SEED not in text
    for f in hits:
        assert proof.describe(f).startswith(f.check + " ")


def assert_cli_lines(out: str, check: str) -> None:
    """Every failure line of ``verify`` starts with its check id; ``check`` is among them."""
    lines = [x for x in out.splitlines() if x[:1] in ("C", "G")]
    assert lines, out
    assert any(x.split(" ")[0] == check for x in lines), out
    assert all(f"{x.split(' ')[0]} (" not in x for x in lines), out  # a label, not the id


def run_verify(r: Run, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    capsys.readouterr()
    code = cli.main(["reorg", "verify", RID, "-C", str(r.root), "--strict", *extra])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


# --------------------------------------------------------------------------- tests


def test_applied_run_is_clean(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    r = applied(make_project)
    assert exact_disk(r).ok, exact_disk(r).failures
    assert exact_rev(r).ok, exact_rev(r).failures
    assert run_verify(r, capsys)[0] == 0
    assert run_verify(r, capsys, "--rev", "HEAD")[0] == 0


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutations_on_disk_and_git_rev(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    mutate, check = MUTATIONS[name]
    r = applied(make_project)
    mutate(r)

    # Disk: the exact proof, then the command.
    assert_failure(exact_disk(r), "C8a" if check == "C8" else check, name)
    if name == "rewritten_non_source":  # spec §4.3 and §15.1.7: C5 *and* C8 fail
        assert_failure(exact_disk(r), "C8a", name)
    if name != "outside_scope":
        code, out = run_verify(r, capsys)
        assert code == EXIT_PROOF, out
        assert SEED not in out
        assert_cli_lines(out, CLI_CHECK.get(name, check))

    # Git revision: the mutation committed on the reorg branch.
    if name not in RUN_FOLDER_ONLY:
        commit_all(r.root, "mutación")
        assert git(r.root, "rev-parse", "--abbrev-ref", "HEAD").strip() == f"declutter/reorg-{RID}"
    if name == "outside_scope":
        assert_failure(exact_rev(r), "C8b", name)
        return
    assert_failure(exact_rev(r), check, name)
    code, out = run_verify(r, capsys, "--rev", "HEAD")
    assert code == EXIT_PROOF, out
    assert SEED not in out
    assert_cli_lines(out, CLI_CHECK.get(name, check))
