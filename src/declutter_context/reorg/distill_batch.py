"""The distill-auto batch (fase 3A §4, §6.3): number, ``batch.json`` and ``auto.lock``.

Only metadata is stored: card ids, result keys, shas, byte counts and dates; never a byte
of a card, a literal or an invariant (G6).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import fsutil
from . import EXIT_INTERNAL, Issue, ReorgError

DISTILL_DIR = ".declutter/distill"  # same as distill_apply.DISTILL_DIR (no import cycle)
SCHEMA = "declutter.distill-batch/1"
BATCH_NAME = "batch.json"
LOCK_NAME = "auto.lock"
STATES = ("running", "stopped", "done", "interrupted", "undone")
RESULTS = ("applied", "skipped", "stopped", "failed")
_AUTO = re.compile(r"auto-([1-9][0-9]*)")


def batch_branch(reorg_id: str, n: int) -> str:
    return f"declutter/distill-auto-{reorg_id}-{n}"


def batch_tag(reorg_id: str, n: int) -> str:
    return f"declutter/pre-distill-auto-{reorg_id}-{n}"


def batch_dir(root: Path, reorg_id: str, n: int) -> Path:
    return root / DISTILL_DIR / reorg_id / f"auto-{n}"


def numbers(root: Path, reorg_id: str) -> list[int]:
    base = root / DISTILL_DIR / reorg_id
    if not base.is_dir() or base.is_symlink():
        return []
    out = []
    for child in base.iterdir():
        m = _AUTO.fullmatch(child.name)
        if m and child.is_dir() and not child.is_symlink():
            out.append(int(m.group(1)))
    return sorted(out)


def next_number(root: Path, reorg_id: str) -> int:
    return max(numbers(root, reorg_id), default=0) + 1


@dataclass(frozen=True)
class CardResult:
    card: str
    result: str  # applied | skipped | stopped | failed
    commit: str | None = None
    bytes_removed: int | None = None
    bytes_added: int | None = None
    copies: int | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Batch:
    reorg: str
    batch: int
    state: str
    base_branch: str
    base_commit: str
    branch: str
    tag: str
    planned: tuple[str, ...]
    skipped_by_user: tuple[str, ...]
    cards: tuple[CardResult, ...]
    started: str
    ended: str | None
    stop_key: str | None = None  # the issue key that halted the batch (§7.1)


def _invalid(detail: str) -> ReorgError:
    return ReorgError([Issue(code=EXIT_INTERNAL, key="batch_invalid", detail=detail)])


def _card_obj(c: CardResult) -> dict[str, object]:
    obj: dict[str, object] = {"card": c.card, "result": c.result}
    for name in ("commit", "bytes_removed", "bytes_added", "copies"):
        value = getattr(c, name)
        if value is not None:
            obj[name] = value
    if c.reasons:
        obj["reasons"] = list(c.reasons)
    return obj


def batch_obj(b: Batch) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "reorg": b.reorg,
        "batch": b.batch,
        "state": b.state,
        "base_branch": b.base_branch,
        "base_commit": b.base_commit,
        "branch": b.branch,
        "tag": b.tag,
        "planned": list(b.planned),
        "skipped_by_user": list(b.skipped_by_user),
        "cards": [_card_obj(c) for c in b.cards],
        "started": b.started,
        "ended": b.ended,
        "stop_key": b.stop_key,
    }


def _str(v: object, where: str) -> str:
    if isinstance(v, str):
        return v
    raise _invalid(where)


def _opt_int(v: object, where: str) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise _invalid(where)
    return v


def _strs(v: object, where: str) -> tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise _invalid(where)
    return tuple(v)


def _card_of(v: object, k: int) -> CardResult:
    if not isinstance(v, dict):
        raise _invalid(f"cards[{k}]")
    result = _str(v.get("result"), f"cards[{k}].result")
    if result not in RESULTS:
        raise _invalid(f"cards[{k}].result")
    commit = v.get("commit")
    if commit is not None and not isinstance(commit, str):
        raise _invalid(f"cards[{k}].commit")
    return CardResult(
        card=_str(v.get("card"), f"cards[{k}].card"),
        result=result,
        commit=commit,
        bytes_removed=_opt_int(v.get("bytes_removed"), f"cards[{k}].bytes_removed"),
        bytes_added=_opt_int(v.get("bytes_added"), f"cards[{k}].bytes_added"),
        copies=_opt_int(v.get("copies"), f"cards[{k}].copies"),
        reasons=_strs(v.get("reasons", []), f"cards[{k}].reasons"),
    )


def batch_of(v: object) -> Batch:
    if not isinstance(v, dict) or v.get("schema") != SCHEMA:
        raise _invalid("schema")
    n = v.get("batch")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise _invalid("batch")
    state = _str(v.get("state"), "state")
    if state not in STATES:
        raise _invalid("state")
    cards = v.get("cards")
    if not isinstance(cards, list):
        raise _invalid("cards")
    ended = v.get("ended")
    stop_key = v.get("stop_key")
    if ended is not None and not isinstance(ended, str):
        raise _invalid("ended")
    if stop_key is not None and not isinstance(stop_key, str):
        raise _invalid("stop_key")
    return Batch(
        reorg=_str(v.get("reorg"), "reorg"),
        batch=n,
        state=state,
        base_branch=_str(v.get("base_branch"), "base_branch"),
        base_commit=_str(v.get("base_commit"), "base_commit"),
        branch=_str(v.get("branch"), "branch"),
        tag=_str(v.get("tag"), "tag"),
        planned=_strs(v.get("planned"), "planned"),
        skipped_by_user=_strs(v.get("skipped_by_user"), "skipped_by_user"),
        cards=tuple(_card_of(c, k) for k, c in enumerate(cards)),
        started=_str(v.get("started"), "started"),
        ended=ended,
        stop_key=stop_key,
    )


def read_batch(root: Path, reorg_id: str, n: int) -> Batch | None:
    path = batch_dir(root, reorg_id, n) / BATCH_NAME
    try:
        raw = fsutil.safe_read_bytes(path, root)
    except fsutil.UnsafeReadError as exc:
        if exc.reason == "missing":
            return None
        raise _invalid(exc.reason) from None
    try:
        obj: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _invalid("json") from None
    return batch_of(obj)


def write_batch(root: Path, b: Batch) -> None:
    data = (json.dumps(batch_obj(b), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fsutil.atomic_write_bytes(batch_dir(root, b.reorg, b.batch) / BATCH_NAME, data, root)


# --------------------------------------------------------------------------- lock (§6.3)


@dataclass(frozen=True)
class Lock:
    pid: int
    started: str
    batch: int


def lock_path(root: Path, reorg_id: str) -> Path:
    return root / DISTILL_DIR / reorg_id / LOCK_NAME


def acquire(root: Path, reorg_id: str, n: int, started: str) -> None:
    """Create ``auto.lock`` with ``O_EXCL``; ``FileExistsError`` when it already exists."""
    path = lock_path(root, reorg_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started": started, "batch": n}, f)
        f.flush()
        os.fsync(f.fileno())


def read_lock(root: Path, reorg_id: str) -> Lock | None:
    """The lock, or ``None``; an unreadable lock counts as a dead one (pid 0)."""
    path = lock_path(root, reorg_id)
    if not path.exists() and not path.is_symlink():
        return None
    try:
        obj = json.loads(fsutil.safe_read_bytes(path, root).decode("utf-8"))
        pid, started, n = obj["pid"], obj["started"], obj["batch"]
        if (
            isinstance(pid, int)
            and not isinstance(pid, bool)
            and isinstance(started, str)
            and isinstance(n, int)
            and not isinstance(n, bool)
        ):
            return Lock(pid=pid, started=started, batch=n)
    except (fsutil.UnsafeReadError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        pass
    return Lock(pid=0, started="", batch=0)


def release(root: Path, reorg_id: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        lock_path(root, reorg_id).unlink()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
