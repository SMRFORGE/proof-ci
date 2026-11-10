"""pytest as the first ``TestRunnerAdapter`` (spec Sections 3.5 and 5.10).

pytest always runs as a subprocess launched from ``[project].test_command``
with ``-p proof_ci.pytest_plugin``; results come back through the JSON file the
plugin writes, never by parsing pytest's stdout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from proof_ci._plugin_protocol import COLLECT_ENV, RESULTS_ENV, repro_command
from proof_ci._redact_patterns import redact

__all__ = ["CollectOutput", "PytestRunner", "RunOutput", "RunnerError"]

PLUGIN_ARGS = ("-p", "proof_ci.pytest_plugin")

# Above this many characters of test ids or paths, pass them through a pytest
# ``@argfile`` (pytest >= 8.2) instead of argv. Windows caps a command line at
# 32,767 characters; a suite of a few hundred parametrized ids exceeds it.
ARGFILE_THRESHOLD = 8000


def _maybe_argfile(items: list[str], work_dir: Path, name: str) -> list[str]:
    if sum(len(i) + 3 for i in items) <= ARGFILE_THRESHOLD:
        return list(items)
    if any("\n" in i or "\r" in i for i in items):
        return list(items)
    path = work_dir / name
    path.write_text("".join(f"{i}\n" for i in items), encoding="utf-8")
    return [f"@{path}"]


def resolve_command(command: tuple[str, ...]) -> list[str]:
    """Resolve ``command[0]`` through PATH before launching.

    On Windows, ``CreateProcess`` searches the running program's own directory
    before PATH. A venv's ``python.exe`` re-launches the base interpreter, so a
    bare ``python`` would otherwise find the global install, not the venv on
    PATH. Only ``.exe`` hits are substituted (``.cmd``/``.bat`` shims cannot be
    launched without a shell; spec Section 5.20 rule 7).
    """
    if not command:
        return []
    first = command[0]
    if os.path.dirname(first):
        return list(command)
    found = shutil.which(first)
    if found and (os.name != "nt" or found.lower().endswith(".exe")):
        return [found, *command[1:]]
    return list(command)


def _is_usage_error(proc: subprocess.CompletedProcess[str]) -> bool:
    """pytest exit 4 covers both bad arguments and a broken conftest; only the first is ours.

    Bad arguments (unknown option, a node id or path that does not exist) are a usage
    error (exit 2). A conftest that fails to import stays a runtime error (exit 3).
    """
    text = (proc.stdout or "") + (proc.stderr or "")
    markers = ("error: unrecognized arguments", "ERROR: not found:", "file or directory not found")
    return any(m in text for m in markers) and proc.returncode in (4, 2)


class RunnerError(Exception):
    """pytest or the plugin failed in a way that is a runtime error (exit 3)."""


class UsageError(RunnerError):
    """pytest rejected the requested tests or arguments (exit 2)."""


@dataclass(frozen=True)
class CollectOutput:
    python: str
    platform: str
    inipath: str | None
    installed: list[str]
    items: list[dict[str, Any]]
    stdout: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    """Collectors that failed (pytest exit 2): reported as error results, never a crash."""


@dataclass(frozen=True)
class RunOutput:
    python: str
    platform: str
    results: list[dict[str, Any]]
    returncode: int
    stdout: str
    stderr: str
    argv: list[str] = field(default_factory=list)
    installed: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PytestRunner:
    """Launches pytest for one repository.

    ``test_command`` is the argv prefix from config (array form). ``work_dir``
    is where the plugin's JSON files go (``.proof-ci/runs/<run_id>/``).
    """

    repo_root: Path
    test_command: tuple[str, ...]
    work_dir: Path
    name: str = "pytest"
    _preflighted: bool = False

    def supports(self, repo_root: Path) -> bool:
        return True

    def repro_command(self, test_id: str, extra_args: tuple[str, ...] = ()) -> str:
        return repro_command(test_id)

    # ------------------------------------------------------------------ internals

    def _spawn(
        self, args: list[str], env_var: str, out_file: Path
    ) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in (COLLECT_ENV, RESULTS_ENV)}
        env[env_var] = str(out_file)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        argv = [*resolve_command(self.test_command), *args]
        try:
            return subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,  # never share the caller's stdin (MCP transport)
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise RunnerError(f"cannot launch test_command {self.test_command!r}: {exc}") from exc

    def _plugin_missing(self, proc: subprocess.CompletedProcess[str]) -> RunnerError:
        detail = (proc.stderr or proc.stdout or "").strip()
        cmd = " ".join(self.test_command)
        if _is_usage_error(proc):
            # Usage error, e.g. an explicit node id that does not exist: exit 2, not 3.
            return UsageError(
                f"pytest rejected the requested tests or arguments for '{cmd}' (usage error)"
                + (f"\n{redact(detail[-2000:])}" if detail else "")
            )
        if "proof_ci" in detail or proc.returncode == 0:
            message = (
                f"proof-ci's pytest plugin is not importable by '{cmd}'; "
                "install proof-ci into the project's environment"
            )
        else:
            message = f"pytest collection produced no proof-ci output (exit {proc.returncode}) for '{cmd}'"
        return RunnerError(message + (f"\n{redact(detail[-2000:])}" if detail else ""))

    def preflight(self) -> None:
        if self._preflighted:
            return
        try:
            proc = subprocess.run(
                [*resolve_command(self.test_command), "--version"],
                stdin=subprocess.DEVNULL,  # never share the caller's stdin (MCP transport)
                cwd=self.repo_root,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise RunnerError(f"cannot launch test_command {self.test_command!r}: {exc}") from exc
        if proc.returncode != 0:
            raise RunnerError(
                f"'{' '.join(self.test_command)} --version' failed:\n{redact((proc.stderr or proc.stdout).strip()[-2000:])}"
            )
        self._preflighted = True

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    # --------------------------------------------------------------------- public

    def collect(self, paths: list[str], extra_args: tuple[str, ...] = ()) -> CollectOutput:
        self.preflight()
        out_file = self.work_dir / "collect.json"
        out_file.unlink(missing_ok=True)
        args = ["--collect-only", "-q", *PLUGIN_ARGS, "-p", "no:xdist", *extra_args]
        args += _maybe_argfile(paths, self.work_dir, "collect-args.txt")
        proc = self._spawn(args, COLLECT_ENV, out_file)
        data = self._read(out_file)
        if data is None:
            raise self._plugin_missing(proc)
        # pytest exit code 5 = no tests collected; 0 = ok. Others mean a collection error.
        if _is_usage_error(proc):
            raise UsageError(
                "pytest could not find the requested tests (usage error):\n"
                + redact((proc.stdout + proc.stderr).strip()[-2000:])
            )
        errors = [e for e in data.get("errors", []) if isinstance(e, dict)]
        if proc.returncode == 2 and errors:
            pass  # collection errors: reported as error results by the caller
        elif proc.returncode not in (0, 5):
            raise RunnerError(
                f"pytest collection failed (exit {proc.returncode}):\n{redact((proc.stdout + proc.stderr).strip()[-4000:])}"
            )
        return CollectOutput(
            python=str(data.get("python", "")),
            platform=str(data.get("platform", "")),
            inipath=data.get("inipath"),
            installed=list(data.get("installed", [])),
            items=list(data.get("items", [])),
            stdout=proc.stdout,
            errors=errors,
        )

    def run(self, test_ids: list[str], extra_args: tuple[str, ...] = ()) -> RunOutput:
        self.preflight()
        out_file = self.work_dir / "plugin-results.json"
        out_file.unlink(missing_ok=True)
        args = [*PLUGIN_ARGS, *extra_args, *_maybe_argfile(test_ids, self.work_dir, "run-args.txt")]
        proc = self._spawn(args, RESULTS_ENV, out_file)
        data = self._read(out_file)
        if data is None:
            raise RunnerError(
                "pytest did not produce proof-ci results (the plugin did not load or pytest "
                f"crashed before session finish; exit {proc.returncode}):\n"
                + redact((proc.stderr or proc.stdout).strip()[-4000:])
            )
        return RunOutput(
            python=str(data.get("python", "")),
            platform=str(data.get("platform", "")),
            installed=[str(x) for x in data.get("installed", [])],
            results=list(data.get("results", [])),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            argv=[*self.test_command, *args],
            errors=[e for e in data.get("errors", []) if isinstance(e, dict)],
        )
