"""CI config discovery facade (spec Section 5.13).

Loads every detected CICommandSource adapter and ranks their candidates. It
does not parse vendor YAML itself and never executes a discovered command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from proof_ci.adapters.ci.common import DiscoveredCommand, pytest_argv
from proof_ci.adapters.ci.github_actions import GitHubActionsSource
from proof_ci.adapters.ci.gitlab_ci import GitLabCISource
from proof_ci.config import DiscoveryConfig

__all__ = ["CICommandSource", "Discovery", "discover", "is_pytest_command"]


class CICommandSource(Protocol):
    name: str

    def detect(self, repo_root: Path) -> bool: ...

    def discover(self, repo_root: Path) -> list[DiscoveredCommand]: ...


def sources(cfg: DiscoveryConfig) -> list[CICommandSource]:
    return [GitLabCISource(cfg.gitlab_ci), GitHubActionsSource(cfg.github_workflows)]


@dataclass(frozen=True)
class Discovery:
    candidates: list[DiscoveredCommand]
    best: list[str] | None
    """Highest-confidence pytest argv, or None."""


def discover(repo_root: Path, cfg: DiscoveryConfig) -> Discovery:
    candidates: list[DiscoveredCommand] = []
    for source in sources(cfg):
        if source.detect(repo_root):
            candidates.extend(source.discover(repo_root))
    usable = [c for c in candidates if c.skipped_reason is None and c.confidence == "high"]
    argvs = [a for a in (pytest_argv(c.command) for c in usable) if a is not None]
    unique = list(dict.fromkeys(tuple(a) for a in argvs))
    # Several high-confidence commands that differ only by flags: prefer the shortest.
    best = list(min(unique, key=lambda a: (len(" ".join(a)), a))) if unique else None
    return Discovery(candidates, best)


def is_pytest_command(argv: tuple[str, ...]) -> bool:
    """Phase 1 executes only ``pytest ...`` or ``<python> -m pytest ...``."""
    if not argv:
        return False
    first = Path(argv[0].replace("\\", "/")).name.lower()
    first = first.removesuffix(".exe")
    if first in ("pytest", "py.test"):
        return True
    is_python = first in ("python", "python3", "py") or (
        first.startswith("python") and first[6:].replace(".", "").isdigit()
    )
    return is_python and list(argv[1:3]) == ["-m", "pytest"]
