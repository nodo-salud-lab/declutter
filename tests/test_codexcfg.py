"""Project-scoped Codex budget: ``.codex/config.toml`` and ``declutter.toml`` edits
(``declutter reorg codex-budget``) and the reader used by S3. Synthetic temp dirs only."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from declutter_context import cli, codexcfg

# --------------------------------------------------------------------------- set_int_key


def test_set_int_key_appends_to_empty_text() -> None:
    assert codexcfg.set_int_key("", "project_doc_max_bytes", 65536) == (
        "project_doc_max_bytes = 65536\n"
    )


def test_set_int_key_replaces_value_preserving_everything_else() -> None:
    text = (
        "# my codex config\n"
        'model = "gpt-5"\n'
        "project_doc_max_bytes   =  32768   # old value\n"
        "\n"
        "[profiles.fast]\n"
        "project_doc_max_bytes = 1\n"
    )
    new = codexcfg.set_int_key(text, "project_doc_max_bytes", 65536)
    assert new == text.replace("=  32768   #", "=  65536   #")
    parsed = tomllib.loads(new)
    assert parsed["project_doc_max_bytes"] == 65536
    assert parsed["profiles"]["fast"]["project_doc_max_bytes"] == 1


def test_set_int_key_inserts_before_first_table() -> None:
    text = 'model = "gpt-5"\n\n[profiles.fast]\nmodel = "x"\n'
    new = codexcfg.set_int_key(text, "project_doc_max_bytes", 65536)
    assert new == 'model = "gpt-5"\n\nproject_doc_max_bytes = 65536\n[profiles.fast]\nmodel = "x"\n'
    parsed = tomllib.loads(new)
    assert parsed["project_doc_max_bytes"] == 65536
    assert "project_doc_max_bytes" not in parsed["profiles"]["fast"]


def test_set_int_key_only_table_key_present_adds_top_level() -> None:
    text = "[profiles.fast]\nproject_doc_max_bytes = 1\n"
    new = codexcfg.set_int_key(text, "project_doc_max_bytes", 4096)
    parsed = tomllib.loads(new)
    assert parsed["project_doc_max_bytes"] == 4096
    assert parsed["profiles"]["fast"]["project_doc_max_bytes"] == 1
    assert new.endswith(text)


def test_set_int_key_without_final_newline_and_crlf() -> None:
    assert codexcfg.set_int_key('a = "b"', "k", 7) == 'a = "b"\nk = 7\n'
    crlf = 'a = "b"\r\n[t]\r\nx = 1\r\n'
    assert codexcfg.set_int_key(crlf, "k", 7) == 'a = "b"\r\nk = 7\r\n[t]\r\nx = 1\r\n'


def test_set_int_key_ignores_lookalike_inside_multiline_string() -> None:
    text = 'note = """\nproject_doc_max_bytes = 5\n"""\n'
    new = codexcfg.set_int_key(text, "project_doc_max_bytes", 65536)
    parsed = tomllib.loads(new)
    assert parsed["note"] == "project_doc_max_bytes = 5\n"
    assert parsed["project_doc_max_bytes"] == 65536


def test_set_int_key_refuses_unparseable_toml() -> None:
    with pytest.raises(codexcfg.CodexCfgError) as err:
        codexcfg.set_int_key("not = = toml\n", "k", 1)
    assert err.value.key == "toml_invalid"


def test_suggest_bytes() -> None:
    assert codexcfg.suggest_bytes(10) == 65536
    assert codexcfg.suggest_bytes(60_000) == 81920  # 66 000 -> next multiple of 16 KiB
    assert codexcfg.suggest_bytes(65536) == 81920


# --------------------------------------------------------------------------- reader (S3)


def test_project_doc_max_bytes_reader(tmp_path: Path) -> None:
    assert codexcfg.project_doc_max_bytes(tmp_path) is None
    cfgfile = tmp_path / ".codex" / "config.toml"
    cfgfile.parent.mkdir()
    cfgfile.write_text("project_doc_max_bytes = 65536\n", encoding="utf-8")
    assert codexcfg.project_doc_max_bytes(tmp_path) == 65536
    for bad in ("project_doc_max_bytes = 0\n", 'project_doc_max_bytes = "x"\n', "= broken\n"):
        cfgfile.write_text(bad, encoding="utf-8")
        assert codexcfg.project_doc_max_bytes(tmp_path) is None


# --------------------------------------------------------------------------- CLI


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


def _proj(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Rules\n- be brief\n", encoding="utf-8")
    return root


def test_cli_creates_both_files_default_65536(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _proj(tmp_path)
    assert cli.main(["reorg", "codex-budget", "-C", str(root)]) == 0
    codex = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8"))
    declutter = tomllib.loads((root / "declutter.toml").read_text(encoding="utf-8"))
    assert codex["project_doc_max_bytes"] == 65536
    assert declutter["codex_budget_bytes"] == 65536
    out = capsys.readouterr().out
    assert "trust" in out.lower()
    assert 'trust_level = "trusted"' in out
    # never touches the real or fake ~/.codex
    assert not (tmp_path / "home" / ".codex").exists()


def test_cli_preserves_existing_bytes(tmp_path: Path) -> None:
    root = _proj(tmp_path)
    (root / ".codex").mkdir()
    codex_text = '# keep me\nmodel = "o3"\n\n[mcp_servers.x]\ncommand = "y"\n'
    (root / ".codex" / "config.toml").write_text(codex_text, encoding="utf-8")
    declutter_text = (
        'lang = "es"\ncodex_budget_bytes = 32768  # was default\n[reorg]\nl1_max_lines = 90\n'
    )
    (root / "declutter.toml").write_text(declutter_text, encoding="utf-8")
    assert cli.main(["reorg", "codex-budget", "-C", str(root), "--bytes", "98304"]) == 0
    new_codex = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert new_codex == (
        '# keep me\nmodel = "o3"\n\nproject_doc_max_bytes = 98304\n[mcp_servers.x]\ncommand = "y"\n'
    )
    new_declutter = (root / "declutter.toml").read_text(encoding="utf-8")
    assert new_declutter == declutter_text.replace("32768", "98304")


def test_cli_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _proj(tmp_path)
    assert cli.main(["reorg", "codex-budget", "-C", str(root), "--dry-run"]) == 0
    assert not (root / ".codex").exists()
    assert not (root / "declutter.toml").exists()
    out = capsys.readouterr().out
    assert ".codex/config.toml" in out
    assert "project_doc_max_bytes = 65536" in out
    assert "codex_budget_bytes = 65536" in out


def test_cli_refuses_unparseable_toml(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _proj(tmp_path)
    (root / ".codex").mkdir()
    (root / ".codex" / "config.toml").write_text("broken = = 1\n", encoding="utf-8")
    assert cli.main(["reorg", "codex-budget", "-C", str(root)]) == 1
    assert "toml_invalid" in capsys.readouterr().err
    assert (root / ".codex" / "config.toml").read_text(encoding="utf-8") == "broken = = 1\n"
    assert not (root / "declutter.toml").exists()


def test_cli_refuses_unparseable_declutter_toml_before_writing(tmp_path: Path) -> None:
    root = _proj(tmp_path)
    (root / "declutter.toml").write_text("[[[\n", encoding="utf-8")
    assert cli.main(["reorg", "codex-budget", "-C", str(root)]) == 1
    assert not (root / ".codex").exists()


def test_cli_refuses_symlink(tmp_path: Path) -> None:
    root = _proj(tmp_path)
    (root / ".codex").mkdir()
    target = tmp_path / "elsewhere.toml"
    target.write_text("project_doc_max_bytes = 1\n", encoding="utf-8")
    (root / ".codex" / "config.toml").symlink_to(target)
    assert cli.main(["reorg", "codex-budget", "-C", str(root)]) == 1
    assert target.read_text(encoding="utf-8") == "project_doc_max_bytes = 1\n"


def test_cli_refuses_symlinked_codex_dir(tmp_path: Path) -> None:
    root = _proj(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".codex").symlink_to(outside)
    assert cli.main(["reorg", "codex-budget", "-C", str(root)]) == 1
    assert list(outside.iterdir()) == []


def test_cli_bad_bytes_is_usage_error(tmp_path: Path) -> None:
    root = _proj(tmp_path)
    assert cli.main(["reorg", "codex-budget", "-C", str(root), "--bytes", "0"]) == 1
    assert cli.main(["reorg", "codex-budget", "-C", str(root), "--bytes", "x"]) == 1


def test_cli_warns_when_git_ignores_codex_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _proj(tmp_path)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    (root / ".gitignore").write_text(".codex/\n", encoding="utf-8")
    assert cli.main(["reorg", "codex-budget", "-C", str(root)]) == 0
    assert (root / ".codex" / "config.toml").exists()
    err = capsys.readouterr().err
    assert "codex_config_ignored" in err
    assert ".gitignore:1" in err


def test_cli_spanish_messages(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _proj(tmp_path)
    assert cli.main(["reorg", "codex-budget", "-C", str(root), "--lang", "es"]) == 0
    assert "confianza" in capsys.readouterr().out
