"""CICommandSource for GitLab CI (read-only, YAML safe_load only)."""

from __future__ import annotations

from pathlib import Path

import yaml

from proof_ci.adapters.ci.common import (
    SKIP_JOB_RE,
    DiscoveredCommand,
    classify_line,
    dangerous_reason,
    script_lines,
)

__all__ = ["GitLabCISource"]

RESERVED = frozenset(
    {
        "stages",
        "variables",
        "include",
        "default",
        "workflow",
        "image",
        "services",
        "before_script",
        "after_script",
        "cache",
    }
)


class GitLabCISource:
    name = "gitlab_ci"

    def __init__(self, path: str = ".gitlab-ci.yml") -> None:
        self.path = path

    def detect(self, repo_root: Path) -> bool:
        return (repo_root / self.path).is_file()

    def discover(self, repo_root: Path) -> list[DiscoveredCommand]:
        path = repo_root / self.path
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if not isinstance(doc, dict):
            return []
        rel = Path(self.path).as_posix()
        found: list[DiscoveredCommand] = []
        for job_name, job in doc.items():
            name = str(job_name)
            if name in RESERVED or name.startswith(".") or not isinstance(job, dict):
                continue
            lines = script_lines(job.get("script"))
            skip = (
                f"job name {name!r} looks like deploy/release"
                if SKIP_JOB_RE.match(name)
                else dangerous_reason("\n".join(lines), job.get("environment"))
            )
            if skip and name.lower().startswith("pages") and any(classify_line(x) for x in lines):
                # `pages` is still a job; keep it when its script runs tests, unless the
                # script itself does something dangerous.
                skip = dangerous_reason("\n".join(lines), job.get("environment"))
            for line in lines:
                confidence = classify_line(line)
                if confidence is not None:
                    found.append(DiscoveredCommand(line, confidence, rel, name, skip))
        return found
