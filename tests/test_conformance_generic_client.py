"""Generic-client conformance: drive the CLI (--json) and the MCP server over stdio
with no agent-specific headers, tools, or prompts, and check the documented shapes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import jsonschema
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from referencing import Registry, Resource

from proof_ci import models as m

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
GIT = shutil.which("git")
TOOL_NAMES = {
    "run_affected_tests",
    "get_failure_details",
    "list_flaky_tests",
    "verify_record",
    "explain_failure",
    "get_savings_report",
}
VENDORS = ("claude", "anthropic", "cursor", "codex", "windsurf", "cline", "copilot", "openai")


def validator(name: str) -> jsonschema.Draft202012Validator:
    schemas = [m.load_schema(n) for n in m.SCHEMA_FILES]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    return jsonschema.Draft202012Validator(m.load_schema(name), registry=registry)


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    if GIT is None:
        pytest.skip("git is required")
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    (repo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {json.dumps([sys.executable, '-m', 'pytest'])}\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.invalid",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run([GIT or "git", *args], cwd=repo, env=env, check=True, capture_output=True)
    return repo


def cli_json(repo: Path, *args: str) -> tuple[int, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "proof_ci", *args, "--json", "--repo", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return proc.returncode, json.loads(proc.stdout) if proc.stdout.strip() else None


def test_cli_json_shapes(demo: Path) -> None:
    code, summary = cli_json(demo, "run", "--all")
    assert code == 1
    assert not list(validator("run_summary").iter_errors(summary))
    code, report = cli_json(demo, "savings")
    assert code == 0 and not list(validator("savings_report").iter_errors(report))
    code, flakes = cli_json(demo, "flakes", "list")
    assert code == 0 and flakes == {"flakes": []}
    code, affected = cli_json(demo, "affected")
    assert code == 0
    assert set(affected) == {
        "base_setting",
        "base_ref",
        "changed_files",
        "selected_tests",
        "fallback_full_run",
        "fallback_reason",
    }


async def _mcp_session(repo: Path) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proof_ci", "mcp"],
        cwd=str(repo),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out: dict[str, Any] = {}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        out["server_name"] = init.serverInfo.name
        tools = await session.list_tools()
        out["tools"] = {t.name: t for t in tools.tools}

        run = await session.call_tool("run_affected_tests", {"tests": ["tests/test_math.py"]})
        out["run"] = run
        explain = await session.call_tool(
            "explain_failure", {"test_id": "tests/test_math.py::test_div_wrong_expectation"}
        )
        out["explain"] = explain
        out["details"] = await session.call_tool(
            "get_failure_details", {"test_id": "tests/test_math.py::test_add"}
        )
        out["flaky"] = await session.call_tool("list_flaky_tests", {"status": "all"})
        out["savings"] = await session.call_tool("get_savings_report", {"since": "7d"})
        out["unknown_test"] = await session.call_tool("get_failure_details", {"test_id": "nope"})
        out["bad_args"] = await session.call_tool(
            "run_affected_tests", {"all": True, "failed": True}
        )
        out["zero_sign"] = await session.call_tool("run_affected_tests", {"sign": True})
    return out


def single_json(result: Any) -> Any:
    assert not result.isError, result.content
    assert len(result.content) == 1 and result.content[0].type == "text"
    data = json.loads(result.content[0].text)
    assert result.structuredContent == data
    return data


def test_mcp_stdio_generic_client(demo: Path) -> None:
    out = anyio.run(_mcp_session, demo)
    assert out["server_name"] == "proof-ci"
    assert set(out["tools"]) == TOOL_NAMES
    for tool in out["tools"].values():
        text = (tool.description or "").lower()
        assert not any(v in text for v in VENDORS), tool.name

    summary = single_json(out["run"])
    assert not list(validator("run_summary").iter_errors(summary))
    assert summary["counts"]["failed"] == 1 and summary["savings_headline"]

    explain = single_json(out["explain"])
    assert explain["outcome"] == "failed" and explain["repro_command"]
    assert any("repro command" in s for s in explain["next_steps"])

    details = single_json(out["details"])
    assert not list(validator("test_result").iter_errors(details))
    assert details["outcome"] == "passed"

    assert single_json(out["flaky"]) == {"flakes": []}
    assert not list(validator("savings_report").iter_errors(single_json(out["savings"])))

    for key in ("unknown_test", "bad_args", "zero_sign"):
        assert out[key].isError, key
    assert "selected no tests" in out["zero_sign"].content[0].text
