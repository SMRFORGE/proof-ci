"""CICommandSource for GitHub Actions workflows (read-only, YAML safe_load only)."""

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

__all__ = ["GitHubActionsSource"]


class GitHubActionsSource:
    name = "github_actions"

    def __init__(self, workflows_dir: str = ".github/workflows") -> None:
        self.workflows_dir = workflows_dir

    def _files(self, repo_root: Path) -> list[Path]:
        d = repo_root / self.workflows_dir
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())

    def detect(self, repo_root: Path) -> bool:
        return bool(self._files(repo_root))

    def discover(self, repo_root: Path) -> list[DiscoveredCommand]:
        found: list[DiscoveredCommand] = []
        for path in self._files(repo_root):
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            jobs = doc.get("jobs") if isinstance(doc, dict) else None
            if not isinstance(jobs, dict):
                continue
            rel = path.relative_to(repo_root).as_posix()
            for job_name, job in jobs.items():
                if not isinstance(job, dict):
                    continue
                steps = job.get("steps")
                if not isinstance(steps, list):
                    continue
                runs = [s.get("run") for s in steps if isinstance(s, dict) and s.get("run")]
                lines = [line for run in runs for line in script_lines(run)]
                skip = (
                    f"job name {job_name!r} looks like deploy/release"
                    if SKIP_JOB_RE.match(str(job_name))
                    else dangerous_reason("\n".join(lines), job.get("environment"))
                )
                for line in lines:
                    confidence = classify_line(line)
                    if confidence is not None:
                        found.append(DiscoveredCommand(line, confidence, rel, str(job_name), skip))
        return found
