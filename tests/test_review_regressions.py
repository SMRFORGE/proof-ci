"""Regression tests for the full adversarial review at ca8001c (each reproduces a finding)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from proof_ci.adapters.agent.mcp_server import ToolError, call_tool
from proof_ci.affected import ImportGraph
from proof_ci.cli import app
from proof_ci.redact import redact

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
GIT = shutil.which("git")
cli = CliRunner()
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required")

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x.invalid",
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        [GIT or "git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        env={**os.environ, **ENV},
        check=True,
        capture_output=True,
    )


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    write(
        repo,
        "proof-ci.toml",
        f"[project]\ntest_command = {json.dumps([sys.executable, '-m', 'pytest'])}\n"
        "[savings]\nrefresh_calibration_on_all = false\n",
    )
    write(repo, ".gitignore", "__pycache__/\n.proof-ci/\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def run(repo: Path, *args: str) -> tuple[int, dict[str, Any], str]:
    result = cli.invoke(app, ["run", *args, "--json", "--repo", str(repo)])
    data = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result.exit_code, data, result.stderr


def results(repo: Path, summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = json.loads((repo / summary["results_path"]).read_text(encoding="utf-8"))
    return {r["test_id"]: r for r in rows}


# H1: code reached only through conftest.py
def test_module_imported_by_conftest_selects_and_misses_cache(demo: Path) -> None:
    write(demo, "src/demo/fixhelp.py", "def make_value():\n    return 42\n")
    write(
        demo,
        "tests/conftest.py",
        "import pytest\nfrom demo.fixhelp import make_value\n\n\n"
        "@pytest.fixture\ndef value():\n    return make_value()\n",
    )
    write(demo, "tests/test_fixture.py", "def test_value(value):\n    assert value == 42\n")
    git(demo, "add", "-A")
    git(demo, "commit", "-q", "-m", "fixture")
    assert run(demo, "tests/test_fixture.py")[0] == 0  # warm the cache
    write(demo, "src/demo/fixhelp.py", "def make_value():\n    return 41\n")

    code, summary, _ = run(demo)
    assert code == 1
    assert "tests/test_fixture.py::test_value" in results(demo, summary)
    code, summary, _ = run(demo, "tests/test_fixture.py")
    assert code == 1
    assert results(demo, summary)["tests/test_fixture.py::test_value"]["cache_hit"] is False


# H2: an explicit/--failed run must not suppress the config-change full run
@pytest.mark.parametrize("between", [["tests/test_text.py"], ["--failed"]])
def test_explicit_run_does_not_suppress_fallback(demo: Path, between: list[str]) -> None:
    run(demo, "--all")
    with (demo / "pyproject.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n# changed\n")
    run(demo, *between)
    _, summary, _ = run(demo)
    assert summary["selection"]["fallback_full_run"] is True
    assert summary["selection"]["selected_count"] == 7


# H3: dynamic-import selection must not be served from cache
def test_dynamic_selection_skips_cache(demo: Path) -> None:
    write(demo, "src/demo/plug.py", "VALUE = 1\n")
    write(
        demo,
        "tests/test_dyn.py",
        "import importlib\n\n\ndef test_dynamic():\n"
        "    assert importlib.import_module('demo.plug').VALUE == 1\n",
    )
    git(demo, "add", "-A")
    git(demo, "commit", "-q", "-m", "dyn")
    assert run(demo, "tests/test_dyn.py")[0] == 0
    write(demo, "src/demo/plug.py", "VALUE = 2\n")
    code, summary, _ = run(demo)
    assert code == 1
    assert results(demo, summary)["tests/test_dyn.py::test_dynamic"]["cache_hit"] is False


# M1: the installed-package list reported by the run pass invalidates the collection cache
def test_installed_list_mismatch_drops_collection_entry(demo: Path) -> None:
    run(demo, "--all")
    entries = list((demo / ".proof-ci" / "collect-cache").glob("*.json"))
    assert entries
    for entry in entries:
        data = json.loads(entry.read_text(encoding="utf-8"))
        data["installed"] = [*data["installed"], "fakedep==9.9"]
        entry.write_text(json.dumps(data), encoding="utf-8")
    run(demo, "--all")
    for entry in (demo / ".proof-ci" / "collect-cache").glob("*.json"):
        assert "fakedep==9.9" not in json.loads(entry.read_text(encoding="utf-8"))["installed"]


# M2: pytest_plugins is an import edge
def test_pytest_plugins_is_an_import(tmp_path: Path) -> None:
    write(tmp_path, "tests/plugins/fx.py", "VALUE = 1\n")
    write(tmp_path, "tests/conftest.py", 'pytest_plugins = ["tests.plugins.fx"]\n')
    graph = ImportGraph.build(tmp_path, ["src"], ["tests"])
    assert "tests/plugins/fx.py" in graph.deps["tests/conftest.py"]


# M3: paths inside test_command must not crash a run
def test_paths_in_test_command_are_refused(demo: Path) -> None:
    command = [sys.executable, "-m", "pytest", "tests/test_text.py", "-q"]
    write(demo, "proof-ci.toml", f"[project]\ntest_command = {json.dumps(command)}\n")
    code, _, stderr = run(demo, "tests/test_math.py::test_add")
    assert code == 2
    assert "test_paths" in stderr


# M4: a manual ignore mark is pinned to the code it was made on
def test_manual_ignore_does_not_excuse_changed_code(demo: Path) -> None:
    run(demo, "tests/test_flaky.py")
    assert (
        cli.invoke(
            app,
            [
                "flakes",
                "mark",
                "tests/test_flaky.py::test_sometimes",
                "--status",
                "ignored",
                "--repo",
                str(demo),
            ],
        ).exit_code
        == 0
    )
    write(demo, "tests/test_flaky.py", "def test_sometimes():\n    assert 1 == 3\n")
    code, summary, _ = run(demo, "tests/test_flaky.py", "--allow-known-flakes")
    assert code == 1
    assert summary["outcome"] == "failed"


def test_allowed_flake_is_visible_in_summary(demo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run(demo, "tests/test_flaky.py")
    cli.invoke(
        app,
        [
            "flakes",
            "mark",
            "tests/test_flaky.py::test_sometimes",
            "--status",
            "ignored",
            "--repo",
            str(demo),
        ],
    )
    monkeypatch.setenv("PROOF_CI_DEMO_FLAKE", "1")
    code, summary, _ = run(demo, "tests/test_flaky.py", "--allow-known-flakes", "--no-cache")
    assert code == 0
    assert "known-flaky failure(s) allowed" in summary["summary"]


# M5: stored stdout and error messages are redacted
def test_pytest_stdout_file_is_redacted(demo: Path) -> None:
    write(
        demo,
        "tests/test_secret.py",
        "def test_s():\n    print('token=SUPERSECRETVALUE123')\n    assert False\n",
    )
    code, summary, _ = run(demo, "tests/test_secret.py")
    assert code == 1  # a failing test makes pytest print the captured stdout
    run_dir = demo / Path(summary["results_path"]).parent
    text = (run_dir / "pytest-stdout.txt").read_text(encoding="utf-8")
    assert "SUPERSECRETVALUE123" not in text


def test_pem_blocks_are_redacted() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIA\n-----END PRIVATE KEY-----"
    assert "MC4CAQAw" not in redact(f"before\n{pem}\nafter")


def test_collection_error_message_is_redacted(demo: Path) -> None:
    write(
        demo,
        "tests/conftest.py",
        "import sys\nsys.stderr.write('AKIAABCDEFGHIJKLMNOP secret=hunter2hunter2\\n')\nraise RuntimeError('boom')\n",
    )
    result = cli.invoke(app, ["run", "--all", "--repo", str(demo)])
    assert result.exit_code == 3
    assert "AKIAABCDEFGHIJKLMNOP" not in result.stderr
    assert "hunter2hunter2" not in result.stderr


# M6 / L6: state paths stay inside .proof-ci/
@pytest.mark.parametrize(
    "line", ['[cache]\ndir = "cache_committed"', '[flakes]\ndb = "/tmp/elsewhere.sqlite"']
)
def test_state_paths_confined(demo: Path, line: str) -> None:
    with (demo / "proof-ci.toml").open("a", encoding="utf-8") as fh:
        fh.write(f"\n{line}\n")
    result = cli.invoke(app, ["run", "--all", "--repo", str(demo)])
    assert result.exit_code == 2
    assert ".proof-ci/" in result.stderr


# L2: hostile MCP arguments are tool errors
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("run_affected_tests", {"tests": 5}),
        ("run_affected_tests", {"tests": [5]}),
        ("get_savings_report", {"until": 5}),
        ("get_savings_report", {"by": "DROP TABLE"}),
        ("run_affected_tests", {"unknown": True}),
    ],
)
def test_mcp_rejects_bad_arguments(demo: Path, tool: str, args: dict[str, Any]) -> None:
    with pytest.raises(ToolError):
        call_tool(tool, {"repo": str(demo), **args})


# L3: --failed with nothing to rerun says so
def test_failed_with_nothing_to_rerun_explains(demo: Path) -> None:
    run(demo, "tests/test_text.py")
    code, _, stderr = run(demo, "--failed")
    assert code == 0
    assert "nothing to rerun" in stderr


# L4: a record must not overwrite proof-ci's own state
def test_record_out_cannot_overwrite_state(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from proof_ci.record import generate_keypair

    key = tmp_path / "k" / "ed25519.pem"
    generate_keypair(key)
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(key))
    run(demo, "tests/test_text.py")
    target = demo / ".proof-ci" / "flakes.sqlite"
    result = cli.invoke(
        app,
        ["run", "tests/test_text.py", "--sign", "--record-out", str(target), "--repo", str(demo)],
    )
    assert result.exit_code == 2
    assert "own state" in result.stderr


# L5: a suspected flake is not labelled "known flaky"
def test_suspected_label(demo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in ("0", "0", "1"):
        monkeypatch.setenv("PROOF_CI_DEMO_FLAKE", flag)
        cli.invoke(app, ["run", "tests/test_flaky.py", "--no-cache", "--repo", str(demo)])
    monkeypatch.setenv("PROOF_CI_DEMO_FLAKE", "1")
    result = cli.invoke(app, ["run", "tests/test_flaky.py", "--no-cache", "--repo", str(demo)])
    assert "FAILED (suspected flaky)" in result.stdout
    assert "known flaky" not in result.stdout


# --- code review (b42b1f4) regressions ---------------------------------------------


def test_single_file_test_path_invalidates_collection_cache(demo: Path) -> None:
    command = json.dumps([sys.executable, "-m", "pytest"])
    write(
        demo,
        "proof-ci.toml",
        f"[project]\ntest_command = {command}\n"
        'test_paths = ["tests/test_text.py"]\n'
        "[savings]\nrefresh_calibration_on_all = false\n",
    )
    _, first, _ = run(demo, "--all")
    assert first["selection"]["collected_count"] == 2
    with (demo / "tests/test_text.py").open("a", encoding="utf-8") as fh:
        fh.write("\n\ndef test_new():\n    assert False\n")
    code, second, _ = run(demo, "--all")
    assert second["selection"]["collected_count"] == 3
    assert code == 1


def test_corrupt_database_is_a_runtime_error(demo: Path) -> None:
    run(demo, "tests/test_text.py")
    (demo / ".proof-ci" / "flakes.sqlite").write_bytes(b"this is not a database" * 100)
    for args in (["flakes", "list"], ["run", "--all"]):
        result = cli.invoke(app, [*args, "--repo", str(demo)])
        assert result.exit_code == 3, (args, result.output)


def test_unknown_explicit_node_id_is_a_usage_error(demo: Path) -> None:
    result = cli.invoke(
        app, ["run", "tests/test_math.py::test_does_not_exist", "--repo", str(demo)]
    )
    assert result.exit_code == 2, result.output


def test_init_creates_key_where_signing_looks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "plain"
    (repo / ".git").mkdir(parents=True)
    key = tmp_path / "custom" / "signing.pem"
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(key))
    result = cli.invoke(app, ["init", "--json", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["key_created"] is True
    assert key.is_file()


def test_keygen_explicit_key_beats_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_key = tmp_path / "env.pem"
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(env_key))
    explicit = tmp_path / "explicit.pem"
    assert cli.invoke(app, ["record", "keygen", "--key", str(explicit)]).exit_code == 0
    assert explicit.is_file() and not env_key.exists()
