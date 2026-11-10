"""Signing a finished run and attaching the record (shared by the CLI and the MCP server).

Kept out of ``cli.py`` so frontends depend on the core, not on each other. Messages
that a frontend should show the user are returned as warnings rather than printed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from proof_ci.adapters.agent.identity import resolve_identity
from proof_ci.config import Config
from proof_ci.models import RunSummary
from proof_ci.record import KeyError_, SignError, resolve_key_path, sign_run
from proof_ci.savings import SavingsLedger
from proof_ci.util import GitError, run_git, write_json_atomic

__all__ = ["SignFailure", "Signed", "record_output", "sign_and_attach"]

EXIT_USAGE = 2
EXIT_RUNTIME = 3


class SignFailure(Exception):
    """Signing was refused (``code`` 2) or impossible (``code`` 3)."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class Signed:
    summary: RunSummary
    record_path: Path
    key_id: str
    warnings: list[str] = field(default_factory=list)


def record_output(cfg: Config, run_dir: Path, requested: Path | None) -> Path:
    """Default ``<run_dir>/record.json``. An explicit path must stay inside the repository,
    outside the git directory and proof-ci's own state, and must not be a tracked file."""
    if requested is None:
        return run_dir / "record.json"
    target = requested if requested.is_absolute() else Path.cwd() / requested
    target = target.resolve()
    root = cfg.repo_root.resolve()
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise SignFailure(f"{requested} resolves outside the repository", EXIT_USAGE) from exc
    state = root / ".proof-ci"
    if (target == state or state in target.parents) and (state / "runs") not in target.parents:
        raise SignFailure(
            f"{requested} would overwrite proof-ci's own state; use .proof-ci/runs/<run_id>/",
            EXIT_USAGE,
        )
    try:
        for flag in ("--git-dir", "--git-common-dir"):
            out = run_git(root, "rev-parse", flag, check=False)
            if out.returncode == 0 and out.stdout.strip():
                git_dir = Path(out.stdout.strip())
                git_dir = (git_dir if git_dir.is_absolute() else root / git_dir).resolve()
                if target == git_dir or git_dir in target.parents:
                    raise SignFailure(f"{requested} is inside the git directory", EXIT_USAGE)
        tracked = run_git(root, "ls-files", "--error-unmatch", "--", rel.as_posix(), check=False)
        if tracked.returncode == 0:
            raise SignFailure(
                f"{requested} is a tracked file; refusing to overwrite it", EXIT_USAGE
            )
    except GitError:
        pass
    return target


def _relative(cfg: Config, path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def sign_and_attach(
    cfg: Config,
    run_dir: Path,
    summary: RunSummary,
    *,
    agent_id: str | None,
    key: Path | None = None,
    record_out: Path | None = None,
) -> Signed:
    """Sign the run in ``run_dir`` and record the record's path everywhere it is reported:
    the run's summary.json, last-run.json (when it is this run), and the savings ledger."""
    if summary.selection.selected_count == 0:
        raise SignFailure("cannot sign a run that selected no tests", EXIT_USAGE)
    output = record_output(cfg, run_dir, record_out)
    identity = resolve_identity(agent_id, cfg.record.agent_id, os.environ)
    warnings: list[str] = []
    if identity.defaulted:
        warnings.append("no agent id given; using 'local' (set --agent-id or PROOF_CI_AGENT_ID)")
    try:
        result = sign_run(cfg, run_dir, identity, resolve_key_path(cfg, key), output)
    except SignError as exc:
        raise SignFailure(str(exc), EXIT_RUNTIME if exc.runtime else EXIT_USAGE) from exc
    except KeyError_ as exc:
        raise SignFailure(str(exc), EXIT_USAGE) from exc
    warnings.extend(result.warnings)

    record_path = _relative(cfg, result.path)
    updated = summary.model_copy(update={"record_path": record_path})
    SavingsLedger(cfg.resolve(cfg.savings.db)).set_record_path(str(summary.run_id), record_path)
    write_json_atomic(run_dir / "summary.json", updated.to_json())
    last = cfg.repo_root / ".proof-ci" / "last-run.json"
    try:
        if json.loads(last.read_text(encoding="utf-8")).get("run_id") == str(summary.run_id):
            write_json_atomic(last, updated.to_json())
    except (OSError, ValueError):
        pass
    return Signed(updated, result.path, str(result.record["signature"]["key_id"]), warnings)
