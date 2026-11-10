"""MCP server over stdio (spec Section 5.14). Lives in the agent adapter package.

Six tools, vendor-neutral descriptions, one JSON text block per result (plus
``structuredContent``). Tool handlers are plain functions (:func:`call_tool`)
so they can be tested without a transport. Nothing is printed to stdout except
the MCP protocol itself.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["TOOLS", "ToolError", "call_tool", "serve", "tool_definitions"]

SERVER_NAME = "proof-ci"


class ToolError(Exception):
    """A readable error returned to the client as an MCP tool error."""


_REPO = {"type": "string", "description": "Override the repository root."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_affected_tests",
        "description": (
            "Select tests affected by the current change and run them. Returns a short RunSummary. "
            "Use get_failure_details or explain_failure for one test's full diagnosis. With all: true "
            "this can take as long as the full test suite."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "repo": _REPO,
                "base": {"type": "string", "description": "Git ref, HEAD, or auto."},
                "all": {"type": "boolean", "default": False},
                "failed": {
                    "type": "boolean",
                    "default": False,
                    "description": "Rerun only the tests that failed or errored in the previous run.",
                },
                "no_cache": {"type": "boolean", "default": False},
                "allow_known_flakes": {"type": "boolean", "default": False},
                "sign": {"type": "boolean", "default": False},
                "agent_id": {"type": "string"},
                "tests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit pytest node ids or paths. If set, selection mode is explicit.",
                },
            },
        },
    },
    {
        "name": "get_failure_details",
        "description": "Return the full TestResult for one test from a run (default: the last run).",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["test_id"],
            "properties": {
                "repo": _REPO,
                "test_id": {"type": "string"},
                "run_id": {"type": "string", "description": "Defaults to the last run."},
            },
        },
    },
    {
        "name": "list_flaky_tests",
        "description": "List locally known flaky tests.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "repo": _REPO,
                "status": {
                    "type": "string",
                    "enum": ["suspected", "confirmed", "resolved", "ignored", "all"],
                },
            },
        },
    },
    {
        "name": "verify_record",
        "description": (
            "Verify an Ed25519-signed RunRecord file. A valid record is a tamper-evident statement "
            "of what proof-ci reported; signer is known only for trusted keys."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path"],
            "properties": {
                "repo": _REPO,
                "path": {"type": "string"},
                "pubkey": {"type": "string", "description": "Optional path to a PEM public key."},
                "require_known_key": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "explain_failure",
        "description": (
            "Explain one failing test in short, agent-oriented language: what failed, where, the "
            "likely cause in the diff, whether it is flaky, the repro command, and suggested next "
            "steps. Prefer this over dumping logs into the context window."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["test_id"],
            "properties": {
                "repo": _REPO,
                "test_id": {"type": "string"},
                "run_id": {"type": "string", "description": "Defaults to the last run."},
            },
        },
        "outputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "test_id",
                "outcome",
                "summary",
                "repro_command",
                "is_flaky",
                "next_steps",
            ],
            "properties": {
                "test_id": {"type": "string"},
                "outcome": {"type": "string"},
                "summary": {"type": "string"},
                "likely_cause": {"type": ["object", "null"]},
                "repro_command": {"type": "string"},
                "is_flaky": {"type": "boolean"},
                "flake_status": {"type": "string"},
                "related_diff": {"type": ["string", "null"]},
                "next_steps": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "get_savings_report",
        "description": (
            "Return an aggregated savings report from the local ledger for a time window. Use this "
            "instead of inventing cost or token-savings figures."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "repo": _REPO,
                "since": {
                    "type": "string",
                    "description": "RFC 3339 timestamp or relative window such as 7d or 4w. Default 30d.",
                },
                "until": {"type": "string"},
                "by": {"type": "string", "enum": ["repo", "agent", "branch"]},
                "weekly": {"type": "boolean", "default": False},
            },
        },
    },
]


# ---------------------------------------------------------------- helpers


def _config(args: dict[str, Any]) -> Any:
    from proof_ci.config import ConfigError, load_config

    repo = args.get("repo")
    try:
        return load_config(Path(repo) if repo else None)
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc


def _run_dir(cfg: Any, run_id: str | None) -> Path:
    runs: Path = cfg.repo_root / ".proof-ci" / "runs"
    if run_id is None:
        last = cfg.repo_root / ".proof-ci" / "last-run.json"
        try:
            run_id = str(json.loads(last.read_text(encoding="utf-8"))["run_id"])
        except (OSError, ValueError, KeyError) as exc:
            raise ToolError("no previous run; call run_affected_tests first") from exc
    try:
        run_dir: Path = runs / str(uuid.UUID(run_id))
    except ValueError as exc:
        raise ToolError(f"run_id {run_id!r} is not a run id") from exc
    if not (run_dir / "results.json").is_file():
        raise ToolError(f"no results for run {run_id}")
    return run_dir


def _result(cfg: Any, args: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(cfg, args.get("run_id"))
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    for r in results:
        if r.get("test_id") == args["test_id"]:
            found: dict[str, Any] = r
            return found
    raise ToolError(f"test {args['test_id']!r} is not in run {run_dir.name}")


# ----------------------------------------------------------------- tools


def _run_affected_tests(args: dict[str, Any]) -> dict[str, Any]:
    from proof_ci.config import ConfigError
    from proof_ci.runner import RunError, RunOptions, execute_run

    cfg = _config(args)
    tests = tuple(args.get("tests") or ())
    flags = [bool(tests), bool(args.get("all")), bool(args.get("failed"))]
    if sum(flags) > 1:
        raise ToolError("all, failed, and tests are mutually exclusive")
    mode = (
        "explicit"
        if tests
        else "all"
        if args.get("all")
        else "failed"
        if args.get("failed")
        else "affected"
    )
    try:
        outcome = execute_run(
            cfg,
            RunOptions(
                mode=mode,  # type: ignore[arg-type]
                tests=tests,
                json_output=True,
                base=args.get("base"),
                no_cache=bool(args.get("no_cache")),
                allow_known_flakes=bool(args.get("allow_known_flakes")),
                calibrate=mode == "all" and cfg.savings.refresh_calibration_on_all,
                agent_id=args.get("agent_id"),
            ),
        )
    except (ConfigError, RunError) as exc:
        raise ToolError(str(exc)) from exc
    summary = outcome.summary
    if args.get("sign"):
        if summary.selection.selected_count == 0:
            raise ToolError(
                f"cannot sign run {summary.run_id}: it selected no tests (nothing was checked)"
            )
        from proof_ci.cli import _attach_record, _record_output, _sign, _SignFailure

        try:
            assert outcome.run_dir is not None
            out = _record_output(cfg, outcome.run_dir, None)
            signed = _sign(cfg, outcome.run_dir, args.get("agent_id"), None, out)
            summary = _attach_record(cfg, outcome.run_dir, summary, signed)
        except _SignFailure as exc:
            raise ToolError(
                f"run {summary.run_id} finished but was not signed: {exc.message}"
            ) from exc
    data: dict[str, Any] = summary.to_json()
    return data


def _get_failure_details(args: dict[str, Any]) -> dict[str, Any]:
    return _result(_config(args), args)


def _list_flaky_tests(args: dict[str, Any]) -> dict[str, Any]:
    from proof_ci.runner import _flake_store

    status = args.get("status")
    statuses: tuple[str, ...]
    if status is None:
        statuses = ("suspected", "confirmed")
    elif status == "all":
        statuses = ("suspected", "confirmed", "ignored")
    else:
        statuses = (status,)
    records = _flake_store(_config(args)).list_records(statuses)
    return {"flakes": [r.to_json() for r in records]}


def _verify_record(args: dict[str, Any]) -> dict[str, Any]:
    from proof_ci.config import ConfigError, load_config
    from proof_ci.record import KeyError_, load_public_key, verify_record

    trusted: list[bytes] = []
    source: str | None = None
    try:
        if args.get("pubkey"):
            trusted = [load_public_key(Path(args["pubkey"]))]
            source = "pubkey"
        else:
            try:
                cfg = load_config(Path(args["repo"]) if args.get("repo") else None)
                trusted = [load_public_key(cfg.resolve(p)) for p in cfg.record.trusted_pubkeys]
                if trusted:
                    source = f"config:{cfg.config_path.as_posix()}"
            except ConfigError:
                trusted = []
    except KeyError_ as exc:
        raise ToolError(str(exc)) from exc
    result = verify_record(
        Path(args["path"]), trusted, require_known_key=bool(args.get("require_known_key"))
    )
    data = result.as_json()
    if result.valid:
        data["trust_source"] = source
    return data


def _explain_failure(args: dict[str, Any]) -> dict[str, Any]:
    r = _result(_config(args), args)
    failure = r.get("failure") or {}
    flaky = r.get("flaky") or {}
    status = str(flaky.get("status", "none"))
    is_flaky = bool(flaky.get("is_flaky"))
    cause = failure.get("likely_cause")
    where = f"{failure.get('file') or r['location']['file']}:{failure.get('line') or r['location']['line']}"
    if r["outcome"] in ("failed", "error"):
        first = (failure.get("error_message") or "").splitlines()[:1]
        summary = (
            f"{r['test_id']} {r['outcome']} at {where} with {failure.get('error_type', 'an error')}"
        )
        if first and first[0]:
            summary += f": {first[0]}"
    else:
        summary = f"{r['test_id']} {r['outcome']}; there is no failure to explain."
    repro = failure.get("repro_command") or _repro(r["test_id"])
    steps: list[str] = []
    if is_flaky and status in ("confirmed", "ignored"):
        steps.append(
            "This test is a known flake: do not rewrite production code only to satisfy it. "
            "Rerun it once; if it passes, ignore it or mark it with `proof-ci flakes mark`."
        )
    if cause:
        steps.append(
            f"Inspect {cause.get('file')}:{cause.get('line')} first; the failure passes through code changed in this diff."
        )
    else:
        steps.append(
            "The failure did not intersect the current diff; consider an environmental issue or a "
            "dependency the import graph does not see."
        )
    steps.append(f"Rerun with the repro command to confirm: {repro}")
    return {
        "test_id": r["test_id"],
        "outcome": r["outcome"],
        "summary": summary,
        "likely_cause": cause,
        "repro_command": repro,
        "is_flaky": is_flaky,
        "flake_status": status,
        "related_diff": (cause or {}).get("diff_hunk"),
        "next_steps": steps,
    }


def _repro(test_id: str) -> str:
    from proof_ci._plugin_protocol import repro_command

    return repro_command(test_id)


def _get_savings_report(args: dict[str, Any]) -> dict[str, Any]:
    from proof_ci.savings import SavingsLedger, build_report, parse_when

    cfg = _config(args)
    by = args.get("by") or "repo"
    try:
        start = parse_when(args.get("since") or "30d")
        end = parse_when(args["until"]) if args.get("until") else datetime.now(UTC)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    fmt = "%Y-%m-%dT%H:%M:%S.%f"
    rows = SavingsLedger(cfg.resolve(cfg.savings.db)).rows(
        start.strftime(fmt)[:-3] + "Z", end.strftime(fmt)[:-3] + "Z"
    )
    report = build_report(
        rows,
        since=start,
        until=end,
        group_by=by,  # type: ignore[arg-type]
        weekly=bool(args.get("weekly")),
        repo_name=cfg.project_name,
    )
    data: dict[str, Any] = report.to_json()
    return data


_HANDLERS = {
    "run_affected_tests": _run_affected_tests,
    "get_failure_details": _get_failure_details,
    "list_flaky_tests": _list_flaky_tests,
    "verify_record": _verify_record,
    "explain_failure": _explain_failure,
    "get_savings_report": _get_savings_report,
}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool synchronously. Raises :class:`ToolError` for readable failures."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"unknown tool {name!r}")
    return handler(arguments)


def tool_definitions() -> list[Any]:
    from mcp import types

    return [types.Tool(**tool) for tool in TOOLS]


async def serve() -> None:
    """Serve the six tools over stdio until stdin closes."""
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    from proof_ci import __version__

    server: Server[Any, Any] = Server(SERVER_NAME, version=__version__)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def _list() -> list[types.Tool]:
        return tool_definitions()

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call(name: str, arguments: dict[str, Any]) -> Any:
        try:
            # Tests run in a subprocess; keep the server responsive meanwhile.
            data = await anyio.to_thread.run_sync(call_tool, name, arguments)
        except ToolError as exc:
            raise ValueError(str(exc)) from exc
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=text)], data

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
