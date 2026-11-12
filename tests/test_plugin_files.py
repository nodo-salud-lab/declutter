"""Smoke tests for the Claude Code plugin and marketplace files."""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any

import pytest

import declutter_context
from declutter_context import cli

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
SKILLS = (
    "declutter",
    "declutter-check",
    "declutter-hygiene",
    "declutter-init",
    "declutter-reorg",
    "declutter-distill",
    "declutter-auto",
)
VERSION = "0.4.0"


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            fields[key.strip()] = value.strip()
    return fields


def test_all_json_parses() -> None:
    for path in (
        PLUGIN / ".claude-plugin" / "plugin.json",
        PLUGIN / "hooks" / "hooks.json",
        ROOT / ".claude-plugin" / "marketplace.json",
    ):
        assert isinstance(_load(path), dict), path


def test_plugin_manifest_keys() -> None:
    data = _load(PLUGIN / ".claude-plugin" / "plugin.json")
    for key in ("name", "version", "description", "author", "license", "keywords"):
        assert key in data, key
    assert data["name"] == "declutter"
    assert data["version"] == VERSION
    assert data["author"]["name"] == "declutter contributors"
    assert data["license"] == "MIT"


def test_skills_have_frontmatter() -> None:
    for name in SKILLS:
        fm = _frontmatter(PLUGIN / "skills" / name / "SKILL.md")
        assert fm.get("name") == name
        assert fm.get("description")


def test_skills_mention_install_line() -> None:
    for name in SKILLS:
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        # Not on PyPI yet: the install line must point at the repository.
        assert "pipx install git+https://github.com/" in text


def test_hook_session_start() -> None:
    data = _load(PLUGIN / "hooks" / "hooks.json")
    entries = data["hooks"]["SessionStart"]
    assert entries[0]["matcher"] == "startup"
    command = entries[0]["hooks"][0]["command"]
    assert "declutter hook session-start" in command
    assert command.endswith("|| true")


def test_marketplace_source_exists() -> None:
    data = _load(ROOT / ".claude-plugin" / "marketplace.json")
    for key in ("name", "owner", "plugins"):
        assert key in data, key
    for entry in data["plugins"]:
        assert entry["name"] and entry["source"]
        source = ROOT / entry["source"]
        assert source.is_dir(), source
        assert (source / ".claude-plugin" / "plugin.json").is_file()
        manifest = _load(source / ".claude-plugin" / "plugin.json")
        assert manifest["name"] == entry["name"]


# Minimal schemas: required keys and their types (from the Claude Code plugin reference).
_PLUGIN_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "name": str,
    "version": str,
    "description": str,
    "author": dict,
    "license": str,
    "keywords": list,
}
_MARKETPLACE_SCHEMA: dict[str, type] = {"name": str, "owner": dict, "plugins": list}
_MARKETPLACE_ENTRY_SCHEMA: dict[str, type] = {"name": str, "source": str, "description": str}


def _check(data: dict[str, Any], schema: dict[str, Any], where: str) -> None:
    for key, typ in schema.items():
        assert key in data, f"{where}: missing {key}"
        assert isinstance(data[key], typ), f"{where}: {key} must be {typ}"


def test_manifests_match_minimal_schema() -> None:
    plugin = _load(PLUGIN / ".claude-plugin" / "plugin.json")
    _check(plugin, _PLUGIN_SCHEMA, "plugin.json")
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin["name"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"])
    assert isinstance(plugin["author"].get("name"), str)
    assert all(isinstance(k, str) for k in plugin["keywords"])

    market = _load(ROOT / ".claude-plugin" / "marketplace.json")
    _check(market, _MARKETPLACE_SCHEMA, "marketplace.json")
    assert isinstance(market["owner"].get("name"), str)
    for entry in market["plugins"]:
        _check(entry, _MARKETPLACE_ENTRY_SCHEMA, "marketplace plugin entry")
        # The version lives only in plugin.json (it overrides the entry's anyway).
        assert "version" not in entry

    hooks = _load(PLUGIN / "hooks" / "hooks.json")
    assert isinstance(hooks.get("hooks"), dict)
    for event, groups in hooks["hooks"].items():
        assert isinstance(groups, list), event
        for group in groups:
            assert isinstance(group.get("hooks"), list), event
            for h in group["hooks"]:
                assert h.get("type") == "command"
                assert isinstance(h.get("command"), str)
                assert isinstance(h.get("timeout", 1), int)


def _cli_argv(command: str) -> list[str]:
    """The ``declutter`` arguments of a shell hook command, up to the first redirect/operator."""
    argv: list[str] = []
    for tok in shlex.split(command):
        redirect = tok.startswith(("<", ">")) or (tok[:1].isdigit() and ">" in tok)
        if tok in ("||", "&&", "|", ";") or redirect:
            break
        argv.append(tok)
    return argv


def test_hook_commands_parse_with_the_cli() -> None:
    hooks = _load(PLUGIN / "hooks" / "hooks.json")
    parser = cli._build_parser()
    seen = 0
    for groups in hooks["hooks"].values():
        for group in groups:
            for h in group["hooks"]:
                argv = _cli_argv(h["command"])
                assert argv[0] == "declutter", h["command"]
                ns = parser.parse_args(argv[1:])
                assert ns.command in cli._COMMANDS
                seen += 1
    assert seen >= 1
    assert parser.parse_args(["hook", "session-start"]).hook_name == "session-start"


def test_plugin_readme_exists() -> None:
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert len(text.split()) >= 40


def test_version_is_0_4_0() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = _load(PLUGIN / ".claude-plugin" / "plugin.json")
    assert declutter_context.__version__ == VERSION
    assert pyproject["project"]["version"] == VERSION
    assert plugin["version"] == VERSION


_REORG_SKILL = PLUGIN / "skills" / "declutter-reorg" / "SKILL.md"
_DISTILL_SKILL = PLUGIN / "skills" / "declutter-distill" / "SKILL.md"
_AUTO_SKILL = PLUGIN / "skills" / "declutter-auto" / "SKILL.md"
_ENTRY_SKILL = PLUGIN / "skills" / "declutter" / "SKILL.md"
_PLACEHOLDER = re.compile(r"<[^<>]+>")


def _skill_commands(path: Path) -> list[str]:
    """Every inline-code span of a skill that is a ``declutter`` command (``! `` prefix allowed)."""
    text = path.read_text(encoding="utf-8")
    spans = re.findall(r"`([^`\n]+)`", text)
    return [s.removeprefix("! ") for s in spans if s.removeprefix("! ").startswith("declutter ")]


def _assert_commands_parse(commands: list[str]) -> None:
    parser = cli._build_parser()
    for command in commands:
        argv = shlex.split(
            _PLACEHOLDER.sub("X", command.replace("--auto <n>", "--auto 1")).replace(
                "D-NNN", "D-001"
            )
        )
        assert argv[0] == "declutter", command
        try:
            ns = parser.parse_args(argv[1:])
        except SystemExit as exc:  # argparse reports errors by exiting
            pytest.fail(f"{command!r} does not parse: exit {exc.code}")
        assert ns.command in cli._COMMANDS, command


def test_reorg_skill_commands_parse_with_the_cli() -> None:
    commands = _skill_commands(_REORG_SKILL)
    _assert_commands_parse(commands)
    # The literal steps of spec §14 are all present.
    joined = "\n".join(commands)
    for needle in (
        "declutter reorg plan --dry-run --json",
        "declutter reorg plan --json",
        "declutter reorg set",
        "--accept",
        "--dest L1c --reason",
        "declutter reorg check",
        "--yes",
        "--commit --yes",
        "declutter ratchet check",
    ):
        assert needle in joined, needle


def test_reorg_skill_rules() -> None:
    text = _REORG_SKILL.read_text(encoding="utf-8")
    assert "GUÍA" in text
    assert "pending_confirmation" in text
    assert "lever_" in text
    assert "guard_ack" in text
    assert "! declutter reorg apply" in text
    assert "merge" in text and "push" in text
    # It must not read the sources: only the JSON (G6).
    assert "CLAUDE.md" in text


def test_distill_skill_commands_parse_with_the_cli() -> None:
    commands = _skill_commands(_DISTILL_SKILL)
    _assert_commands_parse(commands)
    joined = "\n".join(commands)
    for needle in (
        "declutter reorg status",
        "declutter reorg distill-check <id> --json",
        "declutter reorg distill-check <id> --card D-NNN --json",
        "declutter reorg distill-apply <id> --card D-NNN --commit --yes",
        "declutter reorg verify <id> --strict",
        "declutter ratchet check",
        "declutter reorg distill-undo <id> --card D-NNN",
    ):
        assert needle in joined, needle
    # The apply itself is the human's, in a TTY: never with --yes, never without --commit.
    for command in commands:
        if command.startswith("declutter reorg distill-apply") and "--dry-run" not in command:
            assert "--commit" in command or "--yes" not in command, command


def test_auto_skill() -> None:
    text = _AUTO_SKILL.read_text(encoding="utf-8")
    assert "## Modo auto" in text
    commands = _skill_commands(_AUTO_SKILL)
    joined = "\n".join(commands)
    assert "declutter reorg distill-auto <id> --json" in joined
    assert "declutter reorg distill-undo <id> --auto <n>" in joined
    _assert_commands_parse(commands)
    section = text.split("## Modo auto", 1)[1].split("\n## ", 1)[0]
    for needle in (
        "3 rondas",
        "sin pedir confirmación",
        "«para»",
        "no fusiona",
        "solo la ficha y su bloque del historial",
        "tal cual",
    ):
        assert needle in section, needle
    assert "confirmo" not in section
    for needle in ("merge", "push", "ratchet relax", "/declutter-reorg"):
        assert needle in text, needle
    # The one-card skill no longer carries the auto mode: it points to /declutter-auto.
    distill = _DISTILL_SKILL.read_text(encoding="utf-8")
    assert "## Modo auto" not in distill
    assert "/declutter-auto" in distill
    assert "declutter reorg distill-auto" not in "\n".join(_skill_commands(_DISTILL_SKILL))


def test_entry_skill_guides_the_whole_process() -> None:
    text = _ENTRY_SKILL.read_text(encoding="utf-8")
    commands = _skill_commands(_ENTRY_SKILL)
    _assert_commands_parse(commands)
    joined = "\n".join(commands)
    for needle in ("declutter measure", "declutter reorg status", "declutter ratchet check"):
        assert needle in joined, needle
    # Order: measure, then reorg when none is done, then auto, then the savings.
    steps = text.split("## Steps", 1)[1].split("\n## ", 1)[0]
    i_measure = steps.index("declutter measure")
    i_reorg = steps.index("/declutter-reorg")
    i_auto = steps.index("/declutter-auto")
    i_saved = steps.index("What was saved")
    assert i_measure < i_reorg < i_auto < i_saved
    # The reorg keeps the human's TTY confirmation; the entry skill never relaxes it.
    assert "TTY" in steps
    rules = text.split("## Rules", 1)[1]
    for needle in ("Never merge", "push", "relax the ratchet", "No made-up savings"):
        assert needle in rules, needle
    assert "never" in steps and "invent" in steps
    assert "--yes" not in joined
    assert "ratchet relax" not in joined


def test_distill_skill_rules() -> None:
    text = _DISTILL_SKILL.read_text(encoding="utf-8")
    for needle in (
        "GUÍA",
        "BORRADOR",
        "«no consta»",
        "«not stated»",
        "`confirmo`",
        "! declutter reorg distill-apply <id> --card D-NNN",
        "exits 1",
        "LIFO",
        "`ratchet relax`",
        "S1_max",
        "<id> D-NNN <state>",
        "distill.<key>",
        "merge",
        "push",
    ):
        assert needle in text, needle
    reorg = _REORG_SKILL.read_text(encoding="utf-8")
    assert "/declutter-distill" in reorg


def test_hygiene_suggests_declutter_reorg() -> None:
    text = (PLUGIN / "skills" / "declutter-hygiene" / "SKILL.md").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if "/declutter-reorg" in ln]
    assert len(lines) == 1
    assert "S1" in lines[0] and "S4" in lines[0]


_HOOK = "#!/bin/sh\nexec declutter ratchet check --staged\n"


@pytest.mark.parametrize(
    ("name", "reorg_title", "ratchet_title", "manual_title"),
    [
        (
            "guide.es.md",
            "## Reorganizar sin pérdida (`declutter reorg`)",
            "## Trinquete",
            "## Migrar a mano un CLAUDE.md inflado (antes de la fase 2)",
        ),
        (
            "guide.en.md",
            "## Lossless reorganization (`declutter reorg`)",
            "## Ratchet",
            "## Migrating a bloated CLAUDE.md by hand (before phase 2)",
        ),
    ],
)
def test_guides_document_reorg_and_ratchet(
    name: str, reorg_title: str, ratchet_title: str, manual_title: str
) -> None:
    text = (ROOT / "docs" / name).read_text(encoding="utf-8")
    lines = text.splitlines()
    assert reorg_title in lines
    assert ratchet_title in lines
    assert _HOOK in text
    assert "declutter ratchet check" in text
    assert "docs/historial/**" in text
    assert "-text" in text and ".gitattributes" in text
    # The manual-migration section points at the command.
    start = lines.index(manual_title)
    section = "\n".join(lines[start : start + 6])
    assert "declutter reorg" in section


def test_changelog_and_readmes_mention_0_4_0() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.2.0] - 2026-11-04" in changelog.splitlines()
    assert "## [0.3.0] - 2026-11-05" in changelog.splitlines()
    assert "## [0.4.0] - 2026-11-12" in changelog.splitlines()
    for name in ("README.md", "README.es.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "declutter reorg" in text and "declutter ratchet" in text, name
        assert "/declutter:declutter-reorg" in text, name
        assert "/declutter:declutter-distill" in text, name
        assert "distill-apply" in text, name
        assert "0.1.0" not in text, name


def test_ci_wheel_smoke_covers_reorg() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "templates_reorg" in text
    assert "reorg plan --dry-run --json" in text
