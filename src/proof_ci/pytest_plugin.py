"""pytest plugin loaded by the runner with ``-p proof_ci.pytest_plugin`` (spec Section 5.10).

Imports only the standard library, pytest, and the stdlib-only
:mod:`proof_ci._redact_patterns`. Inert unless ``PROOF_CI_COLLECT_FILE`` or
``PROOF_CI_RESULTS_FILE`` is set. Writes its JSON file at session finish; the
runner never parses pytest's stdout for results.

Exception details travel in ``report.user_properties`` (set on the process
that executes the test), so the controller sees them with or without
pytest-xdist.
"""

from __future__ import annotations

import json
import os
import platform
import re
from collections.abc import Generator
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from proof_ci._plugin_protocol import COLLECT_ENV, EXC_PROPERTY, RESULTS_ENV, repro_command
from proof_ci._redact_patterns import truncate_and_redact


def _is_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _repo_relative(config: pytest.Config, rootdir_relative: str) -> str:
    """Forward-slash path relative to the cwd (the repository root)."""
    native = rootdir_relative.replace("\\", "/")
    try:
        absolute = (config.rootpath / native).resolve()
        return absolute.relative_to(Path.cwd().resolve()).as_posix()
    except (ValueError, OSError):
        return native


def _installed() -> list[str]:
    seen: set[str] = set()
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            seen.add(f"{re.sub(r'[-_.]+', '-', name).lower()}=={dist.version}")
    return sorted(seen)


def _write_json(path: str, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, target)


class _Collector:
    def __init__(self, config: pytest.Config, path: str) -> None:
        self.config = config
        self.path = path
        self.items: list[dict[str, Any]] = []

    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        for item in session.items:
            file, line, _name = item.location
            self.items.append(
                {
                    "test_id": item.nodeid,
                    "file": _repo_relative(self.config, file),
                    "line": (line + 1) if isinstance(line, int) else 1,
                }
            )

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        inipath = self.config.inipath
        ini: str | None = None
        if inipath is not None:
            try:
                ini = Path(inipath).resolve().relative_to(Path.cwd().resolve()).as_posix()
            except ValueError:
                ini = Path(inipath).as_posix()
        _write_json(
            self.path,
            {
                "python": platform.python_version(),
                "platform": platform.system(),
                "inipath": ini,
                "installed": _installed(),
                "items": self.items,
            },
        )


class _Phase:
    def __init__(self, report: pytest.TestReport) -> None:
        self.outcome: str = report.outcome
        self.duration: float = float(report.duration or 0.0)
        self.longrepr: str = report.longreprtext or ""
        self.capstdout: str = report.capstdout or ""
        self.capstderr: str = report.capstderr or ""
        self.wasxfail: bool = hasattr(report, "wasxfail")
        self.exc: dict[str, str] | None = None
        for name, value in report.user_properties:
            if name == EXC_PROPERTY and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    self.exc = {str(k): str(v) for k, v in parsed.items()}
        crash = getattr(report.longrepr, "reprcrash", None)
        self.crash_path: str | None = getattr(crash, "path", None)
        self.crash_line: int | None = getattr(crash, "lineno", None)
        self.crash_message: str | None = getattr(crash, "message", None)


class _Results:
    def __init__(self, config: pytest.Config, path: str) -> None:
        self.config = config
        self.path = path
        self.order: list[str] = []
        self.location: dict[str, tuple[str, int | None, str]] = {}
        self.phases: dict[str, dict[str, _Phase]] = {}

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, Any, None]:
        outcome = yield
        report: pytest.TestReport = outcome.get_result()
        excinfo = call.excinfo
        if excinfo is not None and not isinstance(excinfo.value, pytest.skip.Exception):
            details = {
                "type": excinfo.type.__name__,
                "message": str(excinfo.value),
            }
            report.user_properties.append((EXC_PROPERTY, json.dumps(details, ensure_ascii=False)))

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if _is_worker(self.config):
            return
        nodeid = report.nodeid
        if nodeid not in self.phases:
            self.order.append(nodeid)
            self.phases[nodeid] = {}
            loc = report.location
            self.location[nodeid] = (loc[0], loc[1], loc[2])
        self.phases[nodeid][report.when] = _Phase(report)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if _is_worker(self.config):
            return
        results = [self._result(nodeid) for nodeid in self.order]
        _write_json(
            self.path,
            {
                "python": platform.python_version(),
                "platform": platform.system(),
                "installed": _installed(),
                "results": results,
            },
        )

    def _result(self, nodeid: str) -> dict[str, Any]:
        phases = self.phases[nodeid]
        setup, call, teardown = phases.get("setup"), phases.get("call"), phases.get("teardown")
        message: str | None = None
        failing: _Phase | None = None

        if setup is not None and setup.outcome == "failed":
            outcome, failing = "error", setup
        elif setup is not None and setup.outcome == "skipped":
            outcome = "xfailed" if setup.wasxfail else "skipped"
            message = _skip_reason(setup.longrepr)
        elif call is not None and call.outcome == "failed":
            outcome, failing = "failed", call
        elif call is not None and call.outcome == "skipped":
            outcome = "xfailed" if call.wasxfail else "skipped"
            message = _skip_reason(call.longrepr)
        elif teardown is not None and teardown.outcome == "failed":
            outcome, failing = "error", teardown
            message = "test passed but teardown failed"
        else:
            outcome = "xpassed" if (call is not None and call.wasxfail) else "passed"

        file, line, function = self.location[nodeid]
        result: dict[str, Any] = {
            "schema_version": "1.0.0",
            "test_id": nodeid,
            "outcome": outcome,
            "duration_ms": round(sum(p.duration for p in phases.values()) * 1000),
            "location": {
                "file": _repo_relative(self.config, file),
                "line": (line + 1) if isinstance(line, int) else 1,
                "function": function,
            },
        }
        stdout = "".join(p.capstdout for p in phases.values() if p.capstdout)
        stderr = "".join(p.capstderr for p in phases.values() if p.capstderr)
        if failing is not None:
            failure = self._failure(nodeid, failing)
            if message is None:
                message = (
                    failure["error_message"].splitlines()[0] if failure["error_message"] else None
                )
            result["failure"] = failure
        if message:
            result["message"] = truncate_and_redact(message, 500)
        if stdout:
            result["stdout"] = truncate_and_redact(stdout)
        if stderr:
            result["stderr"] = truncate_and_redact(stderr)
        return result

    def _failure(self, nodeid: str, phase: _Phase) -> dict[str, Any]:
        if phase.exc is not None:
            error_type, error_message = phase.exc.get("type", ""), phase.exc.get("message", "")
        else:
            error_type, error_message = _parse_crash(phase.crash_message or "")
        failure: dict[str, Any] = {
            "error_type": error_type or "Error",
            "error_message": truncate_and_redact(error_message, 2000),
            "repro_command": repro_command(nodeid),
        }
        if phase.crash_path:
            failure["file"] = _repo_relative(
                self.config, _rootdir_relative(self.config, phase.crash_path)
            )
        if isinstance(phase.crash_line, int) and phase.crash_line >= 1:
            failure["line"] = phase.crash_line
        if phase.longrepr:
            failure["traceback"] = truncate_and_redact(phase.longrepr)
        return failure


def _rootdir_relative(config: pytest.Config, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(config.rootpath.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _parse_crash(message: str) -> tuple[str, str]:
    """Split pytest's crash line ``"pkg.Error: text"`` into type and message."""
    first, _, rest = message.partition("\n")
    match = re.match(
        r"^(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception|Exit|Failed|Interrupt|Warning)?):\s?(.*)$",
        first,
    )
    if match:
        type_name = match.group(1).rsplit(".", 1)[-1]
        return type_name, (match.group(2) + ("\n" + rest if rest else "")).strip()
    return "", message.strip()


def _skip_reason(longrepr: str) -> str | None:
    # Skip longrepr text looks like "('path', line, 'Skipped: reason')".
    match = re.search(r"Skipped: (.*?)['\")]*$", longrepr)
    return match.group(1) if match else None


def pytest_configure(config: pytest.Config) -> None:
    collect_path = os.environ.get(COLLECT_ENV)
    results_path = os.environ.get(RESULTS_ENV)
    if collect_path:
        config.pluginmanager.register(_Collector(config, collect_path), "proof_ci_collector")
    elif results_path:
        config.pluginmanager.register(_Results(config, results_path), "proof_ci_results")
