"""M10: read-only CI config discovery (spec Section 5.13)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proof_ci.adapters.ci.common import classify_line, pytest_argv
from proof_ci.adapters.ci.github_actions import GitHubActionsSource
from proof_ci.adapters.ci.gitlab_ci import GitLabCISource
from proof_ci.cli import app
from proof_ci.config import DiscoveryConfig
from proof_ci.discover import discover, is_pytest_command

FIXTURES = Path(__file__).parent / "fixtures" / "ci"
ROOT = Path(__file__).resolve().parents[1]
cli = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(FIXTURES, target)
    return target


def by_job(found: list, job: str) -> list:  # type: ignore[type-arg]
    return [c for c in found if c.job_name == job]


def test_gitlab_extracts_pytest_and_skips_deploy(repo: Path) -> None:
    found = GitLabCISource().discover(repo)
    jobs = {c.job_name for c in found}
    assert "lint" not in jobs  # ruff is not a test command
    assert ".template" not in jobs and "stages" not in jobs and "default" not in jobs
    unit = by_job(found, "unit")
    assert [c.command for c in unit] == [
        "pytest -q tests --maxfail=1",  # YAML drops the trailing comment
        "pytest -q tests",
    ]
    assert all(c.confidence == "high" and c.skipped_reason is None for c in unit)
    assert by_job(found, "legacy")[0].confidence == "medium"
    assert "deploy" in (by_job(found, "deploy-prod")[0].skipped_reason or "")
    assert "production" in (by_job(found, "smoke")[0].skipped_reason or "")
    assert "helm" in (by_job(found, "helm-check")[0].skipped_reason or "")
    assert by_job(found, "pages") == []  # mkdocs is not a test command


def test_github_extracts_run_steps(repo: Path) -> None:
    found = GitHubActionsSource().discover(repo)
    test = by_job(found, "test")
    assert [(c.command, c.confidence) for c in test] == [
        ("python -m pytest -x --tb=short | tee out.txt", "high"),
        ("make check", "medium"),
    ]
    assert test[0].source_path == ".github/workflows/ci.yml"
    assert "release" in (by_job(found, "release")[0].skipped_reason or "")
    nightly = by_job(found, "nightly")
    assert [(c.command, c.confidence) for c in nightly] == [
        ("uv run pytest -q tests/slow", "high"),
        ("nox -s tests", "medium"),
    ]


def test_best_is_shortest_unique_high_confidence(repo: Path) -> None:
    result = discover(repo, DiscoveryConfig())
    assert result.best == ["pytest", "-q", "tests"]
    assert all(
        c.skipped_reason is None or c.command != "pytest -q smoke"
        for c in result.candidates
        if c.job_name == "unit"
    )


def test_nothing_found_without_ci_files(tmp_path: Path) -> None:
    result = discover(tmp_path, DiscoveryConfig())
    assert (result.candidates, result.best) == ([], None)


@pytest.mark.parametrize(
    ("line", "argv"),
    [
        ("pytest -q tests", ["pytest", "-q", "tests"]),
        ("uv run pytest -x", ["pytest", "-x"]),
        ("python3 -m pytest -q | tee log", ["python3", "-m", "pytest", "-q"]),
        ("cd sub && python -m pytest", ["python", "-m", "pytest"]),
        ("COVERAGE=1 pytest --cov", ["pytest", "--cov"]),
        ("tox -e py312", None),
        ("echo 'pytest is great'", None),
    ],
)
def test_pytest_argv(line: str, argv: list[str] | None) -> None:
    assert pytest_argv(line) == argv


@pytest.mark.parametrize(
    ("line", "confidence"),
    [
        ("pytest", "high"),
        ("python -m pytest -q", "high"),
        ("hatch test", "medium"),
        ("hatch run test:all", "medium"),
        ("make test", "medium"),
        ("make lint", None),
        ("tox", "medium"),
        ("ruff check .", None),
        ("pytest-benchmark compare", None),
    ],
)
def test_classify_line(line: str, confidence: str | None) -> None:
    assert classify_line(line) == confidence


@pytest.mark.parametrize(
    ("argv", "ok"),
    [
        (("pytest",), True),
        (("pytest", "-q"), True),
        (("python", "-m", "pytest"), True),
        (("python3.12", "-m", "pytest", "-x"), True),
        ((r"C:\venv\Scripts\python.exe", "-m", "pytest"), True),
        (("/opt/venv/bin/pytest",), True),
        (("tox", "-e", "py"), False),
        (("make", "test"), False),
        (("python", "script.py"), False),
        ((), False),
    ],
)
def test_is_pytest_command(argv: tuple[str, ...], ok: bool) -> None:
    assert is_pytest_command(argv) is ok


def test_discovery_never_spawns_processes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("discovery must not execute anything")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert discover(repo, DiscoveryConfig()).best == ["pytest", "-q", "tests"]


def test_init_discover_writes_command(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from proof_ci import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "user_config_dir", lambda: tmp_path / "keys")
    result = cli.invoke(app, ["init", "--discover", "--no-key", "--json", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["discovered_command"] == ["pytest", "-q", "tests"]
    raw = tomllib.loads((repo / "proof-ci.toml").read_text(encoding="utf-8"))
    assert raw["project"]["test_command"] == ["pytest", "-q", "tests"]


def test_init_discover_without_ci_keeps_default(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    (repo / ".git").mkdir(parents=True)
    result = cli.invoke(app, ["init", "--discover", "--no-key", "--json", "--repo", str(repo)])
    assert json.loads(result.stdout)["discovered_command"] is None
    raw = tomllib.loads((repo / "proof-ci.toml").read_text(encoding="utf-8"))
    assert raw["project"]["test_command"] == ["pytest"]


def test_discover_cli_json(repo: Path) -> None:
    (repo / ".git").mkdir()
    result = cli.invoke(app, ["discover", "--json", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["best"] == ["pytest", "-q", "tests"]
    assert {"command", "confidence", "source_path", "job_name", "skipped_reason"} <= set(
        data["candidates"][0]
    )


@pytest.mark.parametrize("command", [["tox", "-e", "py312"], ["make", "test"]])
def test_run_refuses_non_pytest_command(tmp_path: Path, command: list[str]) -> None:
    repo = tmp_path / "demo"
    shutil.copytree(
        ROOT / "examples" / "demo-project", repo, ignore=shutil.ignore_patterns(".proof-ci")
    )
    (repo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {json.dumps(command)}\n", encoding="utf-8"
    )
    result = cli.invoke(app, ["run", "--all", "--repo", str(repo)])
    assert result.exit_code == 2
    assert "not a pytest invocation" in result.stderr
    assert not (repo / ".proof-ci" / "runs").exists() or not any(
        (repo / ".proof-ci" / "runs").iterdir()
    )


def test_core_discover_does_not_parse_yaml() -> None:
    source = (ROOT / "src" / "proof_ci" / "discover.py").read_text(encoding="utf-8")
    assert "import yaml" not in source and "safe_load" not in source
    assert sys.modules.get("proof_ci.discover") is not None
