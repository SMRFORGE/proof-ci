"""M11: MCP tool definitions and handlers (spec Section 5.14)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from proof_ci.adapters.agent.mcp_server import TOOLS, ToolError, call_tool, tool_definitions

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
VENDORS = ("claude", "anthropic", "cursor", "codex", "windsurf", "cline", "copilot", "openai")


def test_exactly_six_vendor_neutral_tools() -> None:
    names = [t["name"] for t in TOOLS]
    assert names == [
        "run_affected_tests",
        "get_failure_details",
        "list_flaky_tests",
        "verify_record",
        "explain_failure",
        "get_savings_report",
    ]
    for tool in TOOLS:
        assert not any(v in tool["description"].lower() for v in VENDORS)
        schema = tool["inputSchema"]
        assert schema["additionalProperties"] is False
        assert "repo" in schema["properties"]
    assert len(tool_definitions()) == 6


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    (repo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {json.dumps([sys.executable, '-m', 'pytest'])}\n",
        encoding="utf-8",
    )
    return repo


def run(demo: Path, **args: Any) -> dict[str, Any]:
    return call_tool("run_affected_tests", {"repo": str(demo), **args})


def test_explain_failure_rules(demo: Path) -> None:
    summary = run(demo, tests=["tests/test_math.py"])
    assert summary["counts"]["failed"] == 1
    out = call_tool(
        "explain_failure",
        {"repo": str(demo), "test_id": "tests/test_math.py::test_div_wrong_expectation"},
    )
    assert out["outcome"] == "failed" and out["is_flaky"] is False
    assert "AssertionError" in out["summary"]
    assert out["likely_cause"] is None
    assert any("did not intersect the current diff" in s for s in out["next_steps"])
    assert out["next_steps"][-1].startswith("Rerun with the repro command to confirm")

    from proof_ci.config import load_config
    from proof_ci.flakes import flake_store

    flake_store(load_config(demo)).mark(
        "tests/test_math.py::test_div_wrong_expectation", "confirmed", None
    )
    run(demo, tests=["tests/test_math.py"])
    flaky = call_tool(
        "explain_failure",
        {"repo": str(demo), "test_id": "tests/test_math.py::test_div_wrong_expectation"},
    )
    assert flaky["is_flaky"] is True and flaky["flake_status"] == "confirmed"
    assert "do not rewrite production code" in flaky["next_steps"][0]


def test_explain_passing_test(demo: Path) -> None:
    run(demo, tests=["tests/test_text.py"])
    out = call_tool(
        "explain_failure", {"repo": str(demo), "test_id": "tests/test_text.py::test_shout"}
    )
    assert out["outcome"] == "passed" and "no failure" in out["summary"]


def test_tool_errors(demo: Path) -> None:
    with pytest.raises(ToolError, match="no previous run"):
        call_tool("get_failure_details", {"repo": str(demo), "test_id": "x"})
    run(demo, tests=["tests/test_text.py"])
    with pytest.raises(ToolError, match="not in run"):
        call_tool("get_failure_details", {"repo": str(demo), "test_id": "x"})
    with pytest.raises(ToolError, match="not a run id"):
        call_tool("get_failure_details", {"repo": str(demo), "test_id": "x", "run_id": "../../etc"})
    with pytest.raises(ToolError, match="mutually exclusive"):
        run(demo, all=True, tests=["tests/test_text.py"])
    with pytest.raises(ToolError, match="unknown tool"):
        call_tool("delete_everything", {})


def test_verify_record_tool(demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    out = call_tool("verify_record", {"repo": str(demo), "path": str(bad)})
    assert out == {"valid": False, "reason": "missing payload"}


def test_explain_passing_test_has_no_failure_advice(demo: Path) -> None:
    run(demo, tests=["tests/test_text.py"])
    out = call_tool(
        "explain_failure", {"repo": str(demo), "test_id": "tests/test_text.py::test_words"}
    )
    assert out["likely_cause"] is None
    assert len(out["next_steps"]) == 1
    assert out["next_steps"][0].startswith("Rerun with the repro command")


def test_run_returns_notes(demo: Path) -> None:
    run(demo, tests=["tests/test_text.py"])
    out = run(demo, failed=True)
    assert out["selection"]["selected_count"] == 0
    assert "nothing to rerun" in out["summary"]


def test_sign_failure_still_returns_the_run(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(tmp_path / "missing.pem"))
    out = run(demo, tests=["tests/test_text.py"], sign=True)
    assert out["counts"]["passed"] == 2
    assert out["record_path"] is None
    assert "not signed:" in out["summary"]


def test_server_defaults_apply_without_repo_argument(demo: Path) -> None:
    from proof_ci.adapters.agent import mcp_server

    mcp_server.set_defaults(repo=demo, config=None)
    try:
        out = call_tool("run_affected_tests", {"tests": ["tests/test_text.py"]})
        assert out["counts"]["passed"] == 2
    finally:
        mcp_server.set_defaults(repo=None, config=None)
