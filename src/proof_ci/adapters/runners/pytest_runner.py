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


class RunnerError(Exception):
    """pytest or the plugin failed in a way that is a runtime error (exit 3)."""


@dataclass(frozen=True)
class CollectOutput:
    python: str
    platform: str
    inipath: str | None
    installed: list[str]
    items: list[dict[str, Any]]
    stdout: str


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
        if proc.returncode == 4 or "error: unrecognized arguments" in detail:
            message = f"pytest rejected the collect command (usage error) for '{cmd}'"
        elif "proof_ci" in detail or proc.returncode == 0:
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
        args = ["--collect-only", "-q", *PLUGIN_ARGS, "-p", "no:xdist", *extra_args, *paths]
        proc = self._spawn(args, COLLECT_ENV, out_file)
        data = self._read(out_file)
        if data is None:
            raise self._plugin_missing(proc)
        # pytest exit code 5 = no tests collected; 0 = ok. Others mean a collection error.
        if proc.returncode not in (0, 5):
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
        )

    def run(self, test_ids: list[str], extra_args: tuple[str, ...] = ()) -> RunOutput:
        self.preflight()
        out_file = self.work_dir / "plugin-results.json"
        out_file.unlink(missing_ok=True)
        args = [*PLUGIN_ARGS, *extra_args, *test_ids]
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
        )
