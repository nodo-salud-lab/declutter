"""Tests for config loading. Only tmp_path is touched."""

from __future__ import annotations

from pathlib import Path

import pytest

from declutter_context.config import (
    config_dir,
    default_lang,
    last_warnings,
    load_config,
    registry_path,
)
from declutter_context.model import DEFAULT_THRESHOLDS, Config, ReorgConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("XDG_CONFIG_HOME", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "proj", home=tmp_path)
    assert cfg == Config(lang="en")
    assert dict(cfg.thresholds) == dict(DEFAULT_THRESHOLDS)
    assert last_warnings() == []


def test_none_project_root(tmp_path: Path) -> None:
    assert load_config(None, home=tmp_path).lang == "en"


def test_global_overlay(tmp_path: Path) -> None:
    g = _write(
        tmp_path / "g.toml",
        'lang = "es"\nbytes_per_token = 4\nimport_max_depth = 0\n[thresholds]\nS1 = [100, 300]\n',
    )
    cfg = load_config(None, global_path=g)
    assert cfg.lang == "es"
    assert cfg.bytes_per_token == 4.0
    assert cfg.import_max_depth == 0
    assert cfg.thresholds["S1"] == (100.0, 300.0)
    assert cfg.thresholds["S2"] == DEFAULT_THRESHOLDS["S2"]
    assert last_warnings() == []


def test_global_path_default_uses_config_dir(tmp_path: Path) -> None:
    _write(config_dir(tmp_path) / "config.toml", "memory_max_lines = 123\n")
    assert load_config(None, home=tmp_path).memory_max_lines == 123


def test_project_overrides_global(tmp_path: Path) -> None:
    g = _write(
        tmp_path / "g.toml",
        'lang = "es"\ncodex_budget_bytes = 1000\n[thresholds]\nS1 = [1, 2]\nS3 = [10, 20]\n',
    )
    root = tmp_path / "proj"
    _write(root / "declutter.toml", 'lang = "en"\n[thresholds]\nS1 = [5, 6]\n')
    cfg = load_config(root, global_path=g)
    assert cfg.lang == "en"
    assert cfg.codex_budget_bytes == 1000
    assert cfg.thresholds["S1"] == (5.0, 6.0)
    assert cfg.thresholds["S3"] == (10.0, 20.0)


def test_invalid_entries_ignored_with_warnings(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _write(
        root / "declutter.toml",
        'lang = "fr"\n'
        "bytes_per_token = 0\n"
        "codex_budget_bytes = true\n"
        "import_max_depth = -1\n"
        "memory_max_lines = 1.5\n"
        "memory_max_bytes = 500\n"
        "bogus = 1\n"
        "[thresholds]\n"
        "S1 = [300, 100]\n"
        "S2 = [1]\n"
        'S3 = ["a", 2]\n'
        "NOPE = [1, 2]\n"
        "S7 = [1, 2]\n",
    )
    cfg = load_config(root, global_path=tmp_path / "none.toml")
    default = Config(lang="en")
    assert cfg.lang == "en"
    assert cfg.bytes_per_token == default.bytes_per_token
    assert cfg.codex_budget_bytes == default.codex_budget_bytes
    assert cfg.import_max_depth == default.import_max_depth
    assert cfg.memory_max_lines == default.memory_max_lines
    assert cfg.memory_max_bytes == 500
    assert cfg.thresholds["S1"] == DEFAULT_THRESHOLDS["S1"]
    assert cfg.thresholds["S2"] == DEFAULT_THRESHOLDS["S2"]
    assert cfg.thresholds["S3"] == DEFAULT_THRESHOLDS["S3"]
    assert cfg.thresholds["S7"] == (1.0, 2.0)
    assert "NOPE" not in cfg.thresholds
    warnings = last_warnings()
    assert len(warnings) == 10
    assert all(w.startswith("project config:") for w in warnings)


def test_thresholds_not_a_table(tmp_path: Path) -> None:
    g = _write(tmp_path / "g.toml", "thresholds = 3\n")
    cfg = load_config(None, global_path=g)
    assert dict(cfg.thresholds) == dict(DEFAULT_THRESHOLDS)
    assert len(last_warnings()) == 1


def test_unparseable_file_ignored(tmp_path: Path) -> None:
    g = _write(tmp_path / "g.toml", "this is = = not toml [[[")
    cfg = load_config(None, global_path=g)
    assert cfg == Config(lang="en")
    assert len(last_warnings()) == 1


def test_warnings_reset_each_call(tmp_path: Path) -> None:
    g = _write(tmp_path / "g.toml", "bogus = 1\n")
    load_config(None, global_path=g)
    assert len(last_warnings()) == 1
    load_config(None, global_path=tmp_path / "missing.toml")
    assert last_warnings() == []


def test_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert config_dir(tmp_path / "home") == xdg / "declutter"
    assert registry_path(tmp_path / "home") == xdg / "declutter" / "projects.toml"


def test_config_dir_without_xdg(tmp_path: Path) -> None:
    assert config_dir(tmp_path) == tmp_path / ".config" / "declutter"
    assert registry_path(tmp_path) == tmp_path / ".config" / "declutter" / "projects.toml"


def test_default_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    assert default_lang() == "en"
    monkeypatch.setenv("LANG", "es_CL.UTF-8")
    assert default_lang() == "es"
    monkeypatch.setenv("LC_MESSAGES", "en_US.UTF-8")
    assert default_lang() == "en"
    monkeypatch.setenv("LC_ALL", "es_ES.UTF-8")
    assert default_lang() == "es"
    monkeypatch.setenv("LC_ALL", "")
    assert default_lang() == "en"


def test_default_lang_feeds_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "es_CL.UTF-8")
    assert load_config(None, home=tmp_path).lang == "es"


# --- [reorg] table (phase 2a, spec §3, §5.3, §6.4) ---


def _project_cfg(tmp_path: Path, text: str, global_text: str | None = None) -> Config:
    root = tmp_path / "proj"
    _write(root / "declutter.toml", text)
    g = tmp_path / "g.toml"
    if global_text is not None:
        _write(g, global_text)
    return load_config(root, global_path=g)


def test_reorg_table_parsed(tmp_path: Path) -> None:
    cfg = _project_cfg(
        tmp_path,
        "[reorg]\nl1_max_bytes = 2048\nl1_max_lines = 20\nzone_max_lines = 99\n"
        "codex_warn_pct = 99\n",
    )
    assert cfg.reorg == ReorgConfig(
        l1_max_bytes=2048, l1_max_lines=20, zone_max_lines=99, codex_warn_pct=99
    )
    assert last_warnings() == []


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("l1_max_bytes", "1023"),
        ("l1_max_lines", "19"),
        ("zone_max_lines", "19"),
        ("codex_warn_pct", "0"),
        ("codex_warn_pct", "100"),
        ("codex_warn_pct", "true"),
        ("codex_warn_pct", '"x"'),
        ("l1_max_bytes", "2048.0"),
    ],
)
def test_reorg_out_of_range_ignored_with_warning(tmp_path: Path, key: str, raw: str) -> None:
    cfg = _project_cfg(tmp_path, f"[reorg]\n{key} = {raw}\n")
    assert cfg.reorg == ReorgConfig()
    warnings = last_warnings()
    assert len(warnings) == 1
    assert f"'reorg.{key}'" in warnings[0]
    assert warnings[0].startswith("project config:")


def test_reorg_range_edges_accepted(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, "[reorg]\nl1_max_bytes = 1024\ncodex_warn_pct = 1\n")
    assert cfg.reorg.l1_max_bytes == 1024
    assert cfg.reorg.codex_warn_pct == 1
    assert last_warnings() == []


def test_reorg_unknown_subkey_warns(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, "[reorg]\nbogus = 1\nl1_max_lines = 30\n")
    assert cfg.reorg.l1_max_lines == 30
    assert last_warnings() == ["project config: unknown key 'reorg.bogus'; ignored"]


def test_reorg_not_a_table_warns(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, "reorg = 3\n")
    assert cfg.reorg == ReorgConfig()
    warnings = last_warnings()
    assert len(warnings) == 1
    assert "'reorg'" in warnings[0]


def test_reorg_lexicon_union_global_and_project(tmp_path: Path) -> None:
    cfg = _project_cfg(
        tmp_path,
        '[reorg.lexicon]\nclinical = ["b", "a"]\n',
        global_text='[reorg.lexicon]\nclinical = ["a"]\ndevice = ["x"]\n',
    )
    assert cfg.reorg.extra_lexicon == {"clinical": ("a", "b"), "device": ("x",)}
    assert last_warnings() == []


def test_reorg_lexicon_duplicates_within_one_file_collapse(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, '[reorg.lexicon]\nsecrets = ["k", "k", "j"]\n')
    assert cfg.reorg.extra_lexicon == {"secrets": ("k", "j")}


def test_reorg_lexicon_unknown_category_warns(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, '[reorg.lexicon]\nweather = ["rain"]\n')
    assert cfg.reorg.extra_lexicon == {}
    warnings = last_warnings()
    assert len(warnings) == 1
    assert "'reorg.lexicon.weather'" in warnings[0]


@pytest.mark.parametrize("raw", ['"single"', "[1, 2]", '["ok", 3]', "{ a = 1 }"])
def test_reorg_lexicon_not_a_list_of_str_warns(tmp_path: Path, raw: str) -> None:
    cfg = _project_cfg(tmp_path, f"[reorg.lexicon]\npublishable = {raw}\n")
    assert cfg.reorg.extra_lexicon == {}
    warnings = last_warnings()
    assert len(warnings) == 1
    assert "'reorg.lexicon.publishable'" in warnings[0]


def test_reorg_lexicon_not_a_table_warns(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, '[reorg]\nlexicon = ["a"]\n')
    assert cfg.reorg.extra_lexicon == {}
    warnings = last_warnings()
    assert len(warnings) == 1
    assert "'reorg.lexicon'" in warnings[0]


def test_reorg_lexicon_bad_regex_ignored_with_warning(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, '[reorg.lexicon]\nirreversible = ["([", "ok"]\n')
    assert cfg.reorg.extra_lexicon == {"irreversible": ("ok",)}
    warnings = last_warnings()
    assert len(warnings) == 1
    assert "'reorg.lexicon.irreversible'" in warnings[0]


def test_reorg_scalars_project_overrides_global(tmp_path: Path) -> None:
    cfg = _project_cfg(
        tmp_path,
        "[reorg]\nl1_max_bytes = 4096\n",
        global_text="[reorg]\nl1_max_bytes = 2048\nzone_max_lines = 40\n",
    )
    assert cfg.reorg.l1_max_bytes == 4096
    assert cfg.reorg.zone_max_lines == 40
    assert cfg.reorg.l1_max_lines == ReorgConfig().l1_max_lines


def test_codex_budget_bytes_stays_top_level(tmp_path: Path) -> None:
    cfg = _project_cfg(tmp_path, "codex_budget_bytes = 1000\n[reorg]\ncodex_budget_bytes = 5\n")
    assert cfg.codex_budget_bytes == 1000
    assert last_warnings() == ["project config: unknown key 'reorg.codex_budget_bytes'; ignored"]


@pytest.mark.parametrize("bad", ["(?i)foo", "(?s)foo", "(?P<x>a)", "(a)\\1", "(?P=x)", "(?(1)a|b)"])
def test_reorg_lexicon_rejects_expr_unsafe_in_union(tmp_path: Path, bad: str) -> None:
    # Extensions are later joined with '|': global inline flags, named groups and group
    # references compile alone but break or change meaning inside the union.
    toml = f"[reorg.lexicon]\nclinical = ['{bad}', 'ok']\n"
    cfg = _project_cfg(tmp_path, toml)
    assert cfg.reorg.extra_lexicon == {"clinical": ("ok",)}
    warnings = last_warnings()
    assert len(warnings) == 1
    assert "'reorg.lexicon.clinical'" in warnings[0]


def test_reorg_lexicon_duplicate_named_group_rejected(tmp_path: Path) -> None:
    toml = "[reorg.lexicon]\nclinical = ['(?P<x>a)', '(?P<x>b)']\n"
    cfg = _project_cfg(tmp_path, toml)
    assert cfg.reorg.extra_lexicon == {}
    assert len(last_warnings()) == 2


@pytest.mark.parametrize("good", ["(?i:foo)", "(a|b)c", "a\\\\1", "[(]x", "ficha cl[ií]nica"])
def test_reorg_lexicon_accepts_union_safe_expr(tmp_path: Path, good: str) -> None:
    cfg = _project_cfg(tmp_path, f"[reorg.lexicon]\nclinical = ['{good}']\n")
    assert cfg.reorg.extra_lexicon == {"clinical": (good,)}
    assert last_warnings() == []
