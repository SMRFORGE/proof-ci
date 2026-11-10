"""M4: pytest plugin, redaction, runner, and ``proof-ci run --all | --failed | TESTS``."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from typer.testing import CliRunner

from proof_ci import models as m
from proof_ci._plugin_protocol import quote_node_id, repro_command
from proof_ci.adapters.runners.pytest_runner import resolve_command
from proof_ci.cli import app
from proof_ci.config import ConfigError, load_config
from proof_ci.redact import redact, truncate_and_redact
from proof_ci.runner import RunOptions, execute_run

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
FAILING = "tests/test_math.py::test_div_wrong_expectation"
runner = CliRunner()


def _toml_list(items: list[str]) -> str:
    return json.dumps(items)  # JSON string escapes are valid TOML basic strings


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    """A private copy of the demo project whose test_command is this interpreter."""
    target = tmp_path / "demo"
    shutil.copytree(DEMO, target, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    write_config(target, [sys.executable, "-m", "pytest"])
    return target


def write_config(repo: Path, command: list[str], pytest_args: list[str] | None = None) -> None:
    body = f'[project]\nname = "demo"\ntest_command = {_toml_list(command)}\n'
    if pytest_args:
        body += f"pytest_args = {_toml_list(pytest_args)}\n"
    (repo / "proof-ci.toml").write_text(body, encoding="utf-8")


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def schema_validator(name: str) -> jsonschema.Draft202012Validator:
    from referencing import Registry, Resource

    schemas = [m.load_schema(n) for n in m.SCHEMA_FILES]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    return jsonschema.Draft202012Validator(m.load_schema(name), registry=registry)


# ------------------------------------------------------------------ integration


def test_run_all_reports_deliberate_failure(demo: Path) -> None:
    result = invoke("run", "--all", "--json", "--repo", str(demo))
    assert result.exit_code == 1, result.output
    summary = json.loads(result.stdout)
    assert not list(schema_validator("run_summary").iter_errors(summary))
    m.RunSummary.model_validate(summary)
    assert summary["selection"]["mode"] == "all"
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["passed"] == 6
    assert summary["outcome"] == "failed"
    (failure,) = summary["failures"]
    assert failure["test_id"] == FAILING
    assert failure["error_type"] == "AssertionError"
    assert failure["repro_command"] == f"pytest -q {FAILING}"
    assert failure["file"] == "tests/test_math.py"

    results = json.loads((demo / summary["results_path"]).read_text(encoding="utf-8"))
    validator = schema_validator("test_result")
    for r in results:
        assert not list(validator.iter_errors(r))
        assert "\\" not in r["location"]["file"]
        assert r["location"]["line"] >= 1
        assert r["cache_hit"] is False
    by_id = {r["test_id"]: r for r in results}
    # 1-based line of the def (the file starts with an import and two blank lines).
    assert by_id["tests/test_math.py::test_add"]["location"] == {
        "file": "tests/test_math.py",
        "line": 4,
        "function": "test_add",
    }
    run_dir = demo / Path(summary["results_path"]).parent
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "pytest-stdout.txt").read_text(encoding="utf-8").strip()
    last = json.loads((demo / ".proof-ci" / "last-run.json").read_text(encoding="utf-8"))
    assert last["run_id"] == summary["run_id"]


def test_explicit_passing_node_exits_0(demo: Path) -> None:
    result = invoke("run", "tests/test_math.py::test_add", "--repo", str(demo))
    assert result.exit_code == 0, result.output
    assert "1 passed" in result.stdout


def test_failed_mode_selects_previous_failures(demo: Path) -> None:
    assert invoke("run", "--all", "--repo", str(demo)).exit_code == 1
    result = invoke("run", "--failed", "--json", "--repo", str(demo))
    assert result.exit_code == 1, result.output
    summary = json.loads(result.stdout)
    assert summary["selection"]["mode"] == "failed"
    assert summary["selection"]["selected_count"] == 1
    assert [f["test_id"] for f in summary["failures"]] == [FAILING]


def test_failed_mode_without_previous_run_exits_2(demo: Path) -> None:
    result = invoke("run", "--failed", "--repo", str(demo))
    assert result.exit_code == 2
    assert "no previous run" in result.stderr


@pytest.mark.parametrize(
    "args",
    [["--failed", "--all"], ["--all", "tests/test_math.py"], ["--failed", "tests/test_math.py"]],
)
def test_modes_are_mutually_exclusive(demo: Path, args: list[str]) -> None:
    assert invoke("run", *args, "--repo", str(demo)).exit_code == 2


def test_affected_mode_outside_git_exits_2(demo: Path) -> None:
    result = invoke("run", "--repo", str(demo))
    assert result.exit_code == 2
    assert "git repository" in result.stderr


def test_zero_selected_does_not_invoke_pytest(demo: Path) -> None:
    # Previous run with no failures -> --failed selects nothing -> exit 0, no run pass.
    assert invoke("run", "tests/test_math.py::test_add", "--repo", str(demo)).exit_code == 0
    result = invoke("run", "--failed", "--json", "--repo", str(demo))
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["selection"]["selected_count"] == 0
    run_dir = demo / Path(summary["results_path"]).parent
    assert not (run_dir / "plugin-results.json").exists()


def test_xdist_passthrough_keeps_every_result(demo: Path) -> None:
    pytest.importorskip("xdist")
    write_config(demo, [sys.executable, "-m", "pytest"], ["-n", "2"])
    result = invoke("run", "--all", "--json", "--repo", str(demo))
    assert result.exit_code == 1, result.output
    summary = json.loads(result.stdout)
    results = json.loads((demo / summary["results_path"]).read_text(encoding="utf-8"))
    assert len(results) == 7
    failing = next(r for r in results if r["test_id"] == FAILING)
    assert failing["failure"]["error_type"] == "AssertionError"
    assert "2.5" in failing["failure"]["error_message"]


def test_git_metadata_and_tree_hash(demo: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run([git, *cmd], cwd=demo, check=True, env=env, capture_output=True)
    head_tree = subprocess.run(
        [git, "rev-parse", "HEAD^{tree}"], cwd=demo, capture_output=True, text=True, check=True
    ).stdout.strip()
    result = invoke("run", "tests/test_math.py::test_add", "--json", "--repo", str(demo))
    summary = json.loads(result.stdout)
    assert summary["commit_tree"] == head_tree
    assert summary["working_tree_dirty"] is False
    # An untracked (non-ignored) file changes the tree hash; the real index is untouched.
    (demo / "new_file.txt").write_text("x", encoding="utf-8")
    summary2 = json.loads(
        invoke("run", "tests/test_math.py::test_add", "--json", "--repo", str(demo)).stdout
    )
    assert summary2["commit_tree"] != head_tree
    assert summary2["working_tree_dirty"] is True
    status = subprocess.run(
        [git, "status", "--porcelain"], cwd=demo, capture_output=True, text=True, check=True
    ).stdout
    assert "?? new_file.txt" in status


# ------------------------------------------------------- fake test_command checks


FAKE = textwrap.dedent(
    """
    import json, os, sys
    log = os.environ["FAKE_LOG"]
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "env": sorted(k for k in os.environ if k.startswith("PROOF_CI_"))}) + "\\n")
    if sys.argv[1:] == ["--version"]:
        sys.exit(0)
    mode = os.environ.get("FAKE_MODE", "ok")
    if "PROOF_CI_COLLECT_FILE" in os.environ and mode != "no-collect":
        with open(os.environ["PROOF_CI_COLLECT_FILE"], "w", encoding="utf-8") as fh:
            json.dump({"python": "3", "platform": "X", "inipath": None, "installed": [],
                       "items": [{"test_id": "tests/test_math.py::test_add", "file": "tests/test_math.py", "line": 4}]}, fh)
    # Run mode never writes PROOF_CI_RESULTS_FILE: simulates a plugin that did not load.
    sys.exit(0)
    """
)


def fake_repo(demo: Path, tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake_pytest.py"
    script.write_text(FAKE, encoding="utf-8")
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_MODE", mode)
    write_config(demo, [sys.executable, str(script)])
    return log


def test_subprocess_args_and_env(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = fake_repo(demo, tmp_path, "ok", monkeypatch)
    result = invoke("run", "--all", "--repo", str(demo))
    assert result.exit_code == 3  # no results file -> runtime error
    assert "did not produce proof-ci results" in result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    version, collect, run = calls
    assert version["argv"] == ["--version"]
    assert collect["argv"][:2] == ["--collect-only", "-q"]
    assert ["-p", "proof_ci.pytest_plugin"] == collect["argv"][2:4]
    assert "no:xdist" in collect["argv"]
    assert "PROOF_CI_COLLECT_FILE" in collect["env"]
    assert "PROOF_CI_RESULTS_FILE" not in collect["env"]
    assert run["argv"][:2] == ["-p", "proof_ci.pytest_plugin"]
    assert run["argv"][-1] == "tests/test_math.py::test_add"
    assert "PROOF_CI_RESULTS_FILE" in run["env"]
    assert "PROOF_CI_COLLECT_FILE" not in run["env"]


def test_plugin_not_importable_exits_3(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_repo(demo, tmp_path, "no-collect", monkeypatch)
    result = invoke("run", "--all", "--repo", str(demo))
    assert result.exit_code == 3
    assert "not importable" in result.stderr


def test_missing_interpreter_exits_3(demo: Path) -> None:
    write_config(demo, ["definitely-not-a-real-python-xyz", "-m", "pytest"])
    assert invoke("run", "--all", "--repo", str(demo)).exit_code == 3


# ------------------------------------------------------------ argument safety


@pytest.mark.parametrize("arg", ["-pevil", "--rootdir=/", "../outside.py", "../../x.py::t"])
def test_unsafe_test_arguments_rejected(demo: Path, arg: str) -> None:
    cfg = load_config(repo=demo)
    with pytest.raises(ConfigError):
        execute_run(cfg, RunOptions(mode="explicit", tests=(arg,)))


# ---------------------------------------------------------------- unit pieces


def test_repro_command_quoting() -> None:
    assert quote_node_id("tests/test_x.py::test_y") == "tests/test_x.py::test_y"
    assert quote_node_id("tests/test_x.py::test_y[a-b]") == '"tests/test_x.py::test_y[a-b]"'
    assert quote_node_id("t.py::test[a b]") == '"t.py::test[a b]"'
    assert quote_node_id('t.py::test["q"]') == '"t.py::test[\\"q\\"]"'
    assert quote_node_id("t.py::test[$HOME]") == '"t.py::test[$HOME]"'
    assert repro_command("t.py::test[1]") == 'pytest -q "t.py::test[1]"'


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("api_key=abcdefgh12345", "abcdefgh12345"),
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ('password: "hunter22hunter22"', "hunter22hunter22"),
        ("key AKIAABCDEFGHIJKLMNOP here", "AKIAABCDEFGHIJKLMNOP"),
        ("ghp_" + "a" * 30, "ghp_" + "a" * 30),
        ("glpat-" + "b" * 22, "glpat-" + "b" * 22),
        ("tok eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc_DEF-123 end", "eyJhbGciOiJIUzI1NiJ9"),
    ],
)
def test_redaction(text: str, secret: str) -> None:
    out = redact(text)
    assert secret not in out
    assert "***" in out


def test_redaction_keeps_ordinary_text() -> None:
    assert redact("assert 2.5 == 2") == "assert 2.5 == 2"
    assert redact("token: short") == "token: short"


def test_truncate_then_redact() -> None:
    out = truncate_and_redact("x" * 9000)
    assert len(out) == 8192


def test_plugin_imports_only_stdlib_and_pytest() -> None:
    allowed_first_party = {"proof_ci._plugin_protocol", "proof_ci._redact_patterns"}
    for module in ("pytest_plugin", "_plugin_protocol", "_redact_patterns"):
        tree = ast.parse((ROOT / "src" / "proof_ci" / f"{module}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                ok = (
                    top in sys.stdlib_module_names
                    or top == "__future__"
                    or (
                        module == "pytest_plugin"
                        and (top == "pytest" or name in allowed_first_party)
                    )
                )
                assert ok, f"{module} imports {name}"


def test_resolve_command_uses_path() -> None:
    resolved = resolve_command(("python", "-m", "pytest"))
    assert Path(resolved[0]).is_absolute()
    assert resolved[1:] == ["-m", "pytest"]
    explicit = resolve_command((sys.executable, "-m", "pytest"))
    assert explicit[0] == sys.executable
