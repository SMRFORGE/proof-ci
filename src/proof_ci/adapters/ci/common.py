"""Shared types and command classification for CICommandSource adapters (spec 3.5, 5.13).

Classification works on command *text* only. Nothing here executes a script.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "SKIP_JOB_RE",
    "DiscoveredCommand",
    "classify_line",
    "dangerous_reason",
    "pytest_argv",
    "script_lines",
]

Confidence = Literal["high", "medium"]

SKIP_JOB_RE = re.compile(r"^(pages|deploy|release|publish|notify|cleanup)\b", re.IGNORECASE)
_DANGEROUS = (
    ("deploy", re.compile(r"\bdeploy\b", re.IGNORECASE)),
    ("terraform apply", re.compile(r"\bterraform\s+apply\b")),
    ("kubectl", re.compile(r"\bkubectl\b")),
    ("helm", re.compile(r"\bhelm\b")),
    ("docker push", re.compile(r"\bdocker\s+push\b")),
)
_PYTEST = re.compile(r"(?<![\w.-])(?:py\.test|pytest)(?![\w.-])")
_PY_M_PYTEST = re.compile(r"\bpython3?(?:\.\d+)?\s+-m\s+pytest\b")
_TOX = re.compile(r"(?<![\w.-])tox(?![\w.-])")
_NOX_TESTS = re.compile(r"(?<![\w.-])nox\b.*\btests?\b")
_HATCH = re.compile(r"\bhatch\s+(?:test|run\s+test)\b")
_MAKE = re.compile(r"^make\s+(?:test|check)\s*$")


@dataclass(frozen=True)
class DiscoveredCommand:
    command: str
    confidence: Confidence
    source_path: str
    job_name: str
    skipped_reason: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "command": self.command,
            "confidence": self.confidence,
            "source_path": self.source_path,
            "job_name": self.job_name,
            "skipped_reason": self.skipped_reason,
        }


def script_lines(script: object) -> list[str]:
    """Command lines from a YAML ``script``/``run`` value (string or list of strings)."""
    items = script if isinstance(script, list) else [script]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        for raw in item.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if re.match(r"^set\s+-", line):
                continue
            lines.append(line)
    return lines


def classify_line(line: str) -> Confidence | None:
    if _PY_M_PYTEST.search(line) or _PYTEST.search(line):
        return "high"
    if _TOX.search(line) or _NOX_TESTS.search(line) or _HATCH.search(line) or _MAKE.match(line):
        return "medium"
    return None


def dangerous_reason(text: str, environment: object = None) -> str | None:
    for label, pattern in _DANGEROUS:
        if pattern.search(text):
            return f"script mentions {label}"
    env_name = environment.get("name") if isinstance(environment, dict) else environment
    if isinstance(env_name, str) and "prod" in env_name.lower():
        return f"job environment {env_name!r} looks like production"
    return None


def pytest_argv(line: str) -> list[str] | None:
    """The pytest invocation inside a shell line, as an argv, or None.

    ``uv run pytest -q tests`` -> ``["pytest", "-q", "tests"]``;
    ``python3 -m pytest -x`` -> ``["python3", "-m", "pytest", "-x"]``. Pipes,
    redirects, ``&&`` and ``;`` end the command.
    """
    segment = re.split(r"\s(?:\|\|?|&&|;|>>?|2>)\s?", " " + line + " ")
    for part in segment:
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            continue
        for i, tok in enumerate(tokens):
            base = tok.rsplit("/", 1)[-1]
            # Only the executable part: CI lines often carry paths and coverage flags
            # that belong in [project].test_paths / pytest_args, not in the prefix.
            if base in ("pytest", "py.test"):
                return ["pytest"]
            if re.fullmatch(r"python3?(?:\.\d+)?", base) and tokens[i + 1 : i + 3] == [
                "-m",
                "pytest",
            ]:
                return [base, "-m", "pytest"]
    return None
