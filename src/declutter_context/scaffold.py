"""``declutter init``: create the 5-layer context scaffold from packaged templates.

Only missing files are created. Existing files are never overwritten and symlinks that
lead outside the project root are never followed.
"""

from __future__ import annotations

import os
import re
from datetime import date
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath

from declutter_context.fsutil import is_within


def _templates_root() -> Traversable:
    return resources.files("declutter_context") / "templates"


def available_langs() -> tuple[str, ...]:
    """Return the template languages shipped with the package, sorted (e.g. ``("en", "es")``)."""
    root = _templates_root()
    if not root.is_dir():
        return ()
    return tuple(sorted(p.name for p in root.iterdir() if p.is_dir()))


def _walk(node: Traversable, prefix: PurePosixPath) -> list[PurePosixPath]:
    found: list[PurePosixPath] = []
    for child in node.iterdir():
        rel = prefix / child.name
        if child.is_dir():
            found.extend(_walk(child, rel))
        elif child.is_file():
            found.append(rel)
    return found


def _template_files(lang: str) -> list[PurePosixPath]:
    """Relative paths of every template file for ``lang``, sorted. Raises ValueError."""
    if lang not in available_langs():
        raise ValueError(f"unknown language {lang!r}; available: {', '.join(available_langs())}")
    return sorted(_walk(_templates_root() / lang, PurePosixPath()))


def _check_root(root: Path) -> None:
    if not root.is_dir():
        raise ValueError(f"project root is not an existing directory: {root}")


def _nearest_existing(path: Path) -> Path:
    cur = path
    while not os.path.lexists(cur):
        cur = cur.parent
    return cur


def _is_creatable(root: Path, rel: Path) -> bool:
    """True if ``rel`` is missing (not even a broken symlink) and its parent stays inside root."""
    target = root / rel
    if os.path.lexists(target):
        return False
    return is_within(_nearest_existing(target.parent), root)


def plan_init(root: Path, lang: str) -> list[Path]:
    """Return the relative paths ``apply_init`` would create (only missing ones), sorted.

    Raises ValueError for an unknown ``lang`` or a ``root`` that is not an existing directory.
    """
    _check_root(root)
    rels = [Path(*p.parts) for p in _template_files(lang)]
    return sorted(r for r in rels if _is_creatable(root, r))


_PLACEHOLDER_RE = re.compile(rb"\{\{(?:project_name|date)\}\}")


def render_template(lang: str, rel: str, today: date, project_name: str) -> bytes:
    """Bytes of ``templates/<lang>/<rel>`` with ``{{project_name}}`` and ``{{date}}`` substituted.

    ``rel`` is a POSIX path relative to the language folder (e.g. ``"docs/historial/INDICE.md"``).
    Nothing is written to disk; the clock is injected through ``today``. Raises ValueError for
    an unknown ``lang`` or ``rel``.
    """
    rel_posix = PurePosixPath(rel)
    if rel_posix not in _template_files(lang):
        raise ValueError(f"unknown template {rel!r} for language {lang!r}")
    node: Traversable = _templates_root() / lang
    for part in rel_posix.parts:
        node = node / part
    subs = {
        b"{{project_name}}": project_name.encode("utf-8"),
        b"{{date}}": today.isoformat().encode("utf-8"),
    }
    # Single pass over the raw bytes: line endings are kept as shipped, and a value that
    # itself contains a placeholder (e.g. a project named "{{date}}") is not rewritten.
    return _PLACEHOLDER_RE.sub(lambda m: subs[m.group(0)], node.read_bytes())


def apply_init(root: Path, lang: str, today: date, project_name: str | None = None) -> list[Path]:
    """Create the missing scaffold files under ``root`` and return their relative paths.

    Placeholders ``{{project_name}}`` (default: the resolved folder name) and ``{{date}}`` (ISO) are
    substituted through :func:`render_template`. Existing files are left untouched; targets that
    would resolve outside ``root`` through a symlink are skipped. Raises ValueError for an
    unknown ``lang`` or a ``root`` that is not an existing directory.
    """
    _check_root(root)
    name = project_name or root.resolve().name or str(root.resolve())
    created: list[Path] = []
    for rel_posix in _template_files(lang):
        rel = Path(*rel_posix.parts)
        if not _is_creatable(root, rel):
            continue
        data = render_template(lang, rel_posix.as_posix(), today, name)
        target = root / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue  # e.g. a dangling symlink in the way: skip this file, keep going
        if not is_within(target.parent, root):
            continue
        try:
            with target.open("xb") as fh:  # "x": never overwrite
                fh.write(data)
        except FileExistsError:
            continue
        created.append(rel)
    return sorted(created)
