"""Tests for declutter_context.scaffold."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from declutter_context.scaffold import apply_init, available_langs, plan_init, render_template

TODAY = date(2026, 11, 4)

EXPECTED = {
    "es": {
        "AGENTS.md",
        "CLAUDE.md",
        "ESTADO.md",
        "declutter.toml",
        "docs/decisiones/INDICE.md",
        "docs/decisiones/D-000-plantilla.md",
        "docs/historial/INDICE.md",
    },
    "en": {
        "AGENTS.md",
        "CLAUDE.md",
        "STATE.md",
        "declutter.toml",
        "docs/decisions/INDEX.md",
        "docs/decisions/D-000-template.md",
        "docs/history/INDEX.md",
    },
}


def _posix(paths: list[Path]) -> set[str]:
    return {p.as_posix() for p in paths}


def test_available_langs() -> None:
    assert available_langs() == ("en", "es")


@pytest.mark.parametrize("lang", ["es", "en"])
def test_creates_all_files(tmp_path: Path, lang: str) -> None:
    created = apply_init(tmp_path, lang, TODAY)
    assert _posix(created) == EXPECTED[lang]
    assert created == sorted(created)
    for rel in created:
        assert (tmp_path / rel).is_file()


@pytest.mark.parametrize("lang", ["es", "en"])
def test_idempotent(tmp_path: Path, lang: str) -> None:
    apply_init(tmp_path, lang, TODAY)
    assert plan_init(tmp_path, lang) == []
    assert apply_init(tmp_path, lang, TODAY) == []


def test_plan_matches_apply_and_writes_nothing(tmp_path: Path) -> None:
    planned = plan_init(tmp_path, "es")
    assert list(tmp_path.iterdir()) == []
    assert planned == sorted(planned)
    assert apply_init(tmp_path, "es", TODAY) == planned


def test_existing_claude_md_untouched(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    assert "CLAUDE.md" not in _posix(plan_init(tmp_path, "en"))
    created = apply_init(tmp_path, "en", TODAY)
    assert "CLAUDE.md" not in _posix(created)
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "mine\n"
    assert "AGENTS.md" in _posix(created)


def test_placeholders_replaced(tmp_path: Path) -> None:
    apply_init(tmp_path, "es", TODAY, project_name="Mi Proyecto")
    for f in tmp_path.rglob("*"):
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            assert "{{" not in text, f
    estado = (tmp_path / "ESTADO.md").read_text(encoding="utf-8")
    assert "# Estado — Mi Proyecto" in estado
    assert "Actualizado: 2026-11-04" in estado


def test_default_project_name_is_dir_name(tmp_path: Path) -> None:
    root = tmp_path / "demo-proj"
    root.mkdir()
    apply_init(root, "en", TODAY)
    assert "# State — demo-proj" in (root / "STATE.md").read_text(encoding="utf-8")
    assert "Updated: 2026-11-04" in (root / "STATE.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("lang", ["es", "en"])
def test_template_size_limits(tmp_path: Path, lang: str) -> None:
    apply_init(tmp_path, lang, TODAY)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8").splitlines()
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert len(agents) <= 80
    assert len(claude) <= 30
    assert claude[0] == "@AGENTS.md"
    state = "ESTADO.md" if lang == "es" else "STATE.md"
    assert state in agents[0]


def test_unknown_lang(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        apply_init(tmp_path, "fr", TODAY)
    with pytest.raises(ValueError):
        plan_init(tmp_path, "fr")


def test_root_not_a_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(ValueError):
        apply_init(missing, "en", TODAY)
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        plan_init(f, "en")


def test_symlink_dir_out_of_root_is_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "docs").symlink_to(outside, target_is_directory=True)
    created = apply_init(root, "en", TODAY)
    assert not any(p.parts[0] == "docs" for p in created)
    assert list(outside.iterdir()) == []


def test_symlink_file_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "AGENTS.md").symlink_to(outside)
    created = apply_init(root, "en", TODAY)
    assert "AGENTS.md" not in _posix(created)
    assert outside.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(("lang", "name"), [("es", "ESTADO.md"), ("en", "STATE.md")])
def test_created_estado_parses(tmp_path: Path, lang: str, name: str) -> None:
    if importlib.util.find_spec("declutter_context.estado") is None:
        pytest.skip("declutter_context.estado not available yet")
    from declutter_context.estado import parse_estado

    apply_init(tmp_path, lang, TODAY, project_name="demo")
    path = tmp_path / name
    estado = parse_estado(path.read_text(encoding="utf-8"), path)
    assert estado.updated == TODAY
    assert estado.errors == ()
    assert estado.phase
    assert len(estado.open_next_ai) == 1
    assert len(estado.open_human) == 1


# --- render_template (phase 2a, spec §3, §9.4, §9.8) ---

TEMPLATES_REORG_NAMES = {
    "agents_header.md",
    "claude.md",
    "historial_header.md",
    "card.md",
    "trampas_header.md",
    "mapa_header.md",
    "zone_header.md",
    "section_titles.json",
}


@pytest.mark.parametrize("lang", ["es", "en"])
def test_render_template_matches_apply_init(tmp_path: Path, lang: str) -> None:
    created = apply_init(tmp_path, lang, TODAY, project_name="Demo X")
    assert _posix(created) == EXPECTED[lang]
    for rel in created:
        expected = (tmp_path / rel).read_bytes()
        assert render_template(lang, rel.as_posix(), TODAY, "Demo X") == expected, rel


def test_render_template_substitutes_placeholders() -> None:
    data = render_template("es", "ESTADO.md", TODAY, "Mi Proyecto")
    text = data.decode("utf-8")
    assert "{{" not in text
    assert "# Estado — Mi Proyecto" in text
    assert "Actualizado: 2026-11-04" in text


def test_render_template_does_not_rewrite_placeholders_inside_values() -> None:
    # Single-pass substitution: a project name that looks like a placeholder stays literal.
    text = render_template("es", "ESTADO.md", TODAY, "{{date}}").decode("utf-8")
    assert "# Estado — {{date}}" in text
    assert "Actualizado: 2026-11-04" in text


@pytest.mark.parametrize(
    ("lang", "rel"),
    [
        ("fr", "AGENTS.md"),
        ("es", "NOPE.md"),
        ("es", "STATE.md"),
        ("es", "docs"),
        ("es", "../en/STATE.md"),
        ("es", "/AGENTS.md"),
        ("es", ""),
    ],
)
def test_render_template_unknown_lang_or_rel_raises(lang: str, rel: str) -> None:
    with pytest.raises(ValueError):
        render_template(lang, rel, TODAY, "demo")


def test_render_template_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for rel in sorted(EXPECTED["en"]):
        render_template("en", rel, TODAY, "demo")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("lang", ["es", "en"])
def test_init_does_not_create_templates_reorg(tmp_path: Path, lang: str) -> None:
    apply_init(tmp_path, lang, TODAY)
    names = {p.name for p in tmp_path.rglob("*")}
    assert names.isdisjoint(TEMPLATES_REORG_NAMES)
    assert not (tmp_path / "templates_reorg").exists()
