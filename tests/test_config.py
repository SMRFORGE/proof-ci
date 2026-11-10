"""M2: configuration loading and ``proof-ci init`` (spec Sections 5.4, 5.7.1)."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import fields
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proof_ci import config as cfg
from proof_ci.cli import app
from proof_ci.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_TEXT,
    GITIGNORE_ENTRIES,
    PRICE_KEYS,
    ConfigError,
    find_repo_root,
    init_repo,
    load_config,
    split_test_command,
)

runner = CliRunner()


@pytest.fixture
def key_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep init's config-directory probe out of the real user profile."""
    d = tmp_path / "userconfig"
    monkeypatch.setattr(cfg, "user_config_dir", lambda: d)
    return d


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".git").mkdir(parents=True)
    return r


def _invoke(args: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.chdir(cwd)
    return runner.invoke(app, args)


# ----------------------------------------------------------------- defaults text


def test_default_text_parses_to_dataclass_defaults(tmp_path: Path) -> None:
    raw = tomllib.loads(DEFAULT_CONFIG_TEXT)
    for section, cls in cfg._SECTIONS.items():
        defaults = cls()
        for f in fields(cls):
            if f.name in cfg._INTERNAL_FIELDS or f.name not in raw[section]:
                continue
            value = raw[section][f.name]
            expected = getattr(defaults, f.name)
            if isinstance(expected, tuple):
                value = tuple(value)
            assert value == expected, f"[{section}].{f.name}"


def test_default_text_spec_values() -> None:
    raw = tomllib.loads(DEFAULT_CONFIG_TEXT)
    assert raw["affected"]["base"] == "auto"
    assert raw["record"]["trusted_pubkeys"] == []
    assert raw["project"]["test_command"] == ["pytest"]
    for key in (*PRICE_KEYS, "price_local_minutes", "local_compute_cost_per_minute"):
        assert key not in raw["savings"], f"{key} must be written commented out"
        assert f"# {key} = " in DEFAULT_CONFIG_TEXT


def test_default_text_matches_spec_block() -> None:
    spec = Path(__file__).resolve().parents[1] / "docs" / "spec" / "proof-ci_build_spec.md"
    if not spec.exists():
        pytest.skip("in-repo spec copy not present")
    text = spec.read_text(encoding="utf-8")
    start = text.index('```toml\n[project]\nname = ""')
    block = text[start + len("```toml\n") : text.index("```", start + 7)]
    assert block == DEFAULT_CONFIG_TEXT


# -------------------------------------------------------------- loading defaults


def test_missing_file_gives_defaults(repo: Path) -> None:
    c = load_config(repo=repo)
    assert not c.file_exists
    assert c.project.test_command == ("pytest",)
    assert c.affected.base == "auto"
    assert c.record.trusted_pubkeys == ()
    assert c.project_name == "repo"
    assert c.resolve(".proof-ci/cache") == repo.resolve() / ".proof-ci" / "cache"


def test_missing_keys_take_defaults(repo: Path) -> None:
    (repo / CONFIG_FILENAME).write_text(
        '[project]\nname = "demo"\n[flakes]\nmin_runs = 5\n', encoding="utf-8"
    )
    c = load_config(repo=repo)
    assert c.file_exists
    assert c.project_name == "demo"
    assert c.flakes.min_runs == 5
    assert c.flakes.window_days == 30
    assert c.cache.enabled is True
    assert c.savings.calibration_max_age_days == 14


def test_fresh_init_lists_all_price_defaults(repo: Path, key_dir: Path) -> None:
    init_repo(repo)
    s = load_config(repo=repo).savings
    assert s.defaults_used == sorted(PRICE_KEYS)
    assert s.prices_configured is False


def test_price_key_present_with_default_value_is_not_a_default(repo: Path) -> None:
    body = "\n".join(f"{k} = 1.0" for k in PRICE_KEYS)
    (repo / CONFIG_FILENAME).write_text(f"[savings]\n{body}\n", encoding="utf-8")
    s = load_config(repo=repo).savings
    assert s.defaults_used == []
    assert s.prices_configured is True


def test_local_compute_price_applies_only_when_enabled(repo: Path) -> None:
    body = "\n".join(f"{k} = 1.0" for k in PRICE_KEYS)
    (repo / CONFIG_FILENAME).write_text(
        f"[savings]\n{body}\nprice_local_minutes = true\n", encoding="utf-8"
    )
    s = load_config(repo=repo).savings
    assert s.defaults_used == ["local_compute_cost_per_minute"]
    assert s.prices_configured is False


@pytest.mark.parametrize(
    "body",
    [
        "[project\n",  # invalid TOML
        "[nope]\nx = 1\n",  # unknown section
        "[project]\ntest_cmd = ['pytest']\n",  # unknown key
        "[flakes]\nmin_runs = 'three'\n",  # wrong type
        "[cache]\nenabled = 1\n",  # int is not bool
        "[project]\ntest_command = []\n",  # empty command
        "[project]\ntest_command = 7\n",
        "[savings]\nwasted_fix_cost = -1\n",
    ],
)
def test_invalid_config_is_config_error(repo: Path, body: str) -> None:
    (repo / CONFIG_FILENAME).write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(repo=repo)


def test_explicit_missing_config_is_error(repo: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(repo=repo, config=repo / "other.toml")


# ------------------------------------------------------------ test_command split


def test_array_command_used_as_is() -> None:
    assert split_test_command(["python", "-m", "pytest"]) == ("python", "-m", "pytest")


def test_string_command_is_split() -> None:
    assert split_test_command("python -m pytest -q") == ("python", "-m", "pytest", "-q")


@pytest.mark.skipif(os.name != "nt", reason="posix=False splitting is Windows-only")
def test_windows_path_survives_string_split() -> None:
    cmd = r"C:\venv\Scripts\python.exe -m pytest"
    assert split_test_command(cmd) == (r"C:\venv\Scripts\python.exe", "-m", "pytest")


@pytest.mark.skipif(os.name != "nt", reason="posix=False splitting is Windows-only")
def test_windows_quoted_path_with_space_survives() -> None:
    cmd = r'"C:\Program Files\py\python.exe" -m pytest'
    assert split_test_command(cmd) == (r"C:\Program Files\py\python.exe", "-m", "pytest")


def test_windows_path_survives_array_form_everywhere(repo: Path) -> None:
    (repo / CONFIG_FILENAME).write_text(
        "[project]\ntest_command = ['C:\\venv\\Scripts\\python.exe', '-m', 'pytest']\n",
        encoding="utf-8",
    )
    assert load_config(repo=repo).project.test_command[0] == "C:\\venv\\Scripts\\python.exe"


# ---------------------------------------------------------------- root discovery


def test_root_discovery_from_subdirectory(repo: Path) -> None:
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert find_repo_root(sub) == repo.resolve()


def test_toml_ancestor_wins_over_git(repo: Path) -> None:
    inner = repo / "pkg"
    (inner / "deep").mkdir(parents=True)
    (inner / CONFIG_FILENAME).write_text("", encoding="utf-8")
    assert find_repo_root(inner / "deep") == inner.resolve()


def test_git_file_counts_for_worktrees(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert find_repo_root(wt) == wt.resolve()


def test_no_root_is_config_error(tmp_path: Path) -> None:
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    if any((d / ".git").exists() or (d / CONFIG_FILENAME).exists() for d in lonely.parents):
        pytest.skip("temp dir sits inside a repository")
    with pytest.raises(ConfigError):
        find_repo_root(lonely)


def test_cli_missing_repo_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _invoke(["init", "--repo", str(tmp_path / "missing")], tmp_path, monkeypatch)
    assert result.exit_code == 2


# --------------------------------------------------------------------------- init


def test_init_writes_files(repo: Path, key_dir: Path) -> None:
    result = init_repo(repo)
    assert result.config_path == repo.resolve() / CONFIG_FILENAME
    raw_bytes = result.config_path.read_bytes()
    assert b"\r\n" not in raw_bytes
    assert raw_bytes.decode("utf-8") == DEFAULT_CONFIG_TEXT
    for sub in ("cache", "collect-cache", "runs", "logs"):
        assert (repo / ".proof-ci" / sub).is_dir()
    gi = (repo / ".gitignore").read_text(encoding="utf-8")
    for entry in GITIGNORE_ENTRIES:
        assert entry in gi.splitlines()
    assert result.key_created is True
    assert result.key_skipped_reason is None
    assert (key_dir / "ed25519.pem").is_file()
    assert (key_dir / "ed25519.pem.pub").is_file()
    # A second (forced) init keeps the existing key.
    again = init_repo(repo, force=True)
    assert again.key_created is False
    assert "already exists" in (again.key_skipped_reason or "")


def test_init_appends_to_existing_gitignore(repo: Path, key_dir: Path) -> None:
    (repo / ".gitignore").write_text("node_modules/\n.proof-ci/runs/", encoding="utf-8")
    result = init_repo(repo)
    text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("node_modules/\n.proof-ci/runs/\n")
    assert ".proof-ci/runs/" not in result.gitignore_added
    assert text.count(".proof-ci/runs/") == 1
    # Idempotent: a forced re-init adds nothing.
    assert init_repo(repo, force=True).gitignore_added == ()
    assert (repo / ".gitignore").read_text(encoding="utf-8") == text


def test_second_init_refuses_without_force(repo: Path, key_dir: Path) -> None:
    init_repo(repo)
    with pytest.raises(ConfigError):
        init_repo(repo)


def test_force_overwrites(repo: Path, key_dir: Path) -> None:
    init_repo(repo)
    path = repo / CONFIG_FILENAME
    path.write_text("[project]\nname = 'changed'\n", encoding="utf-8")
    init_repo(repo, force=True)
    assert path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEXT


def test_no_key(repo: Path, key_dir: Path) -> None:
    result = init_repo(repo, no_key=True)
    assert result.key_created is False
    assert result.key_skipped_reason == "--no-key"
    assert not key_dir.exists()


def test_unwritable_config_dir_is_tolerated(repo: Path, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    result = init_repo(repo, config_dir=blocker / "proof-ci")
    assert result.key_created is False
    assert result.key_skipped_reason is not None
    assert "not writable" in result.key_skipped_reason
    assert any("PROOF_CI_SIGNING_KEY" in w for w in result.warnings)
    assert (repo / CONFIG_FILENAME).exists()


def test_root_owner_warning(repo: Path, monkeypatch: pytest.MonkeyPatch, key_dir: Path) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    real_stat = Path.stat
    # Resolve before patching: on Python 3.11/3.12 Path.resolve() calls stat() itself.
    target = repo.resolve()

    class FakeStat:
        def __init__(self, st: os.stat_result) -> None:
            self._st = st
            self.st_uid = 1000

        def __getattr__(self, name: str) -> object:
            return getattr(self._st, name)

    def fake_stat(self: Path, *a: object, **k: object) -> object:
        st = real_stat(self, *a, **k)  # type: ignore[arg-type]
        return FakeStat(st) if self == target else st

    monkeypatch.setattr(Path, "stat", fake_stat)
    result = init_repo(repo)
    assert any("--user $(id -u):$(id -g)" in w for w in result.warnings)


def test_no_root_owner_warning_when_not_root(
    repo: Path, monkeypatch: pytest.MonkeyPatch, key_dir: Path
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    assert not any("root" in w for w in init_repo(repo).warnings)


# ------------------------------------------------------------------- CLI surface


def test_cli_init_json(repo: Path, key_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _invoke(["init", "--json"], repo, monkeypatch)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data) == {
        "repo",
        "config_path",
        "key_created",
        "key_skipped_reason",
        "discovered_command",
    }
    assert data["key_created"] is True
    assert data["discovered_command"] is None
    assert "\\" not in data["repo"]


def test_cli_init_from_subdirectory_uses_repo_root(
    repo: Path, key_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    result = _invoke(["init"], sub, monkeypatch)
    assert result.exit_code == 0, result.output
    assert (repo / CONFIG_FILENAME).exists()
    assert not (sub / CONFIG_FILENAME).exists()


def test_cli_second_init_exits_2_and_force_overwrites(
    repo: Path, key_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _invoke(["init"], repo, monkeypatch).exit_code == 0
    second = _invoke(["init"], repo, monkeypatch)
    assert second.exit_code == 2
    assert "--force" in second.stderr
    assert _invoke(["init", "--force", "--no-key"], repo, monkeypatch).exit_code == 0


def test_cli_init_outside_any_repo_uses_cwd(
    tmp_path: Path, key_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    if any((d / ".git").exists() or (d / CONFIG_FILENAME).exists() for d in plain.parents):
        pytest.skip("temp dir sits inside a repository")
    assert _invoke(["init", "--no-key"], plain, monkeypatch).exit_code == 0
    assert (plain / CONFIG_FILENAME).exists()


def test_cli_init_config_override(
    repo: Path, key_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = repo / "conf" / "custom.toml"
    result = _invoke(["init", "--config", str(target), "--no-key"], repo, monkeypatch)
    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEXT
