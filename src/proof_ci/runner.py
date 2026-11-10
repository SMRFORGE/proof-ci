"""Orchestrates one ``proof-ci run`` (spec Section 5.7.3).

M4 scope: modes ``all``, ``explicit``, and ``failed``; collection and execution
through the pytest adapter; RunSummary, results.json, and last-run.json.
Affected selection (M5), cache (M6), flakes (M7), signing (M8), and savings
(M9) plug in here later.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from proof_ci.adapters.runners.pytest_runner import PytestRunner, RunnerError, RunOutput
from proof_ci.config import Config, ConfigError
from proof_ci.models import (
    CacheStats,
    Counts,
    FailureSummary,
    RunSummary,
    Selection,
    TestResult,
)
from proof_ci.util import CaseIndex, GitError, git_info, utc_now_iso

__all__ = ["RunError", "RunOptions", "RunOutcome", "execute_run"]

Mode = Literal["all", "explicit", "failed"]

STATE_DIR = ".proof-ci"


class RunError(Exception):
    """Runtime failure (exit 3)."""


@dataclass(frozen=True)
class RunOptions:
    mode: Mode
    tests: tuple[str, ...] = ()
    json_output: bool = False


@dataclass(frozen=True)
class RunOutcome:
    summary: RunSummary
    results: list[TestResult]
    exit_code: int
    pytest_stdout: str


def _validate_test_args(cfg: Config, tests: tuple[str, ...]) -> None:
    """Spec Section 5.15 item 5: no option injection, no paths outside the repo."""
    root = cfg.repo_root.resolve()
    for arg in tests:
        if arg.startswith("-"):
            raise ConfigError(
                f"test argument {arg!r} looks like an option; pass node ids or paths only"
            )
        file_part = arg.split("::", 1)[0]
        target = (root / file_part).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ConfigError(f"test argument {arg!r} resolves outside the repository") from exc


def _previous_failed(cfg: Config) -> list[str]:
    last = cfg.repo_root / STATE_DIR / "last-run.json"
    if not last.is_file():
        raise ConfigError(
            "no previous run found (.proof-ci/last-run.json); run `proof-ci run --all` first"
        )
    try:
        summary = json.loads(last.read_text(encoding="utf-8"))
        results_path = cfg.resolve(summary["results_path"])
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"previous run is unreadable: {exc}") from exc
    return [r["test_id"] for r in results if r.get("outcome") in ("failed", "error")]


def _pytest_args(cfg: Config, json_output: bool) -> tuple[str, ...]:
    args = list(cfg.project.pytest_args)
    if json_output:
        if not any(a in ("-q", "--quiet", "-v", "--verbose") or a.startswith("-q") for a in args):
            args.append("-q")
        if not any(a.startswith("--tb") for a in args):
            args.append("--tb=short")
    return tuple(args)


def _canonicalise(raw: dict[str, Any], case: CaseIndex) -> dict[str, Any]:
    raw = dict(raw)
    raw["test_id"] = case.canonical_node_id(raw["test_id"])
    loc = dict(raw["location"])
    loc["file"] = case.canonical(loc["file"])
    raw["location"] = loc
    return raw


def _counts(results: list[TestResult]) -> Counts:
    tally = {k: 0 for k in ("passed", "failed", "skipped", "error", "xfailed", "xpassed")}
    for r in results:
        tally[r.outcome] += 1
    return Counts(**tally, flaky_failures=0, total_selected=len(results))


def _summary_text(counts: Counts, duration_ms: int, selected: int) -> str:
    if selected == 0:
        return "No tests selected."
    parts = [
        f"{getattr(counts, k)} {k}"
        for k in ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
        if getattr(counts, k)
    ]
    return f"{', '.join(parts) or 'no results'} in {duration_ms / 1000:.2f}s"


def _rel(cfg: Config, path: Path) -> str:
    return PurePosixPath(path.resolve().relative_to(cfg.repo_root.resolve()).as_posix()).as_posix()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def execute_run(cfg: Config, options: RunOptions) -> RunOutcome:
    """Run the selected tests and write the run artifacts. Raises ConfigError (2) or RunError (3)."""
    if options.mode == "explicit":
        _validate_test_args(cfg, options.tests)
    previous_failed = set(_previous_failed(cfg)) if options.mode == "failed" else set()

    started = utc_now_iso()
    t0 = time.monotonic()
    run_id = uuid.uuid4()
    run_dir = cfg.repo_root / STATE_DIR / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        info = git_info(cfg.repo_root)
    except GitError as exc:
        raise RunError(str(exc)) from exc
    case = (
        CaseIndex.from_repo(cfg.repo_root) if info.sha is not None or info.tree else CaseIndex([])
    )

    runner = PytestRunner(cfg.repo_root, cfg.project.test_command, run_dir)
    extra = _pytest_args(cfg, options.json_output)
    try:
        collect_paths = (
            list(options.tests) if options.mode == "explicit" else list(cfg.project.test_paths)
        )
        # Collection is serial and does not take pytest_args (spec Section 5.7.3 step 2).
        collected = runner.collect(collect_paths)
        collected_ids = [case.canonical_node_id(item["test_id"]) for item in collected.items]
        # Keep pytest's own spelling for invocation; canonical ids for contracts.
        invoke_id = {case.canonical_node_id(i["test_id"]): i["test_id"] for i in collected.items}

        if options.mode == "failed":
            selected = [t for t in collected_ids if t in previous_failed]
        else:
            selected = collected_ids

        run_out: RunOutput | None = None
        if selected:
            run_out = runner.run([invoke_id[t] for t in selected], extra)
    except RunnerError as exc:
        raise RunError(str(exc)) from exc

    results: list[TestResult] = []
    pytest_stdout = ""
    if run_out is not None:
        pytest_stdout = run_out.stdout
        for raw in run_out.results:
            data = _canonicalise(raw, case)
            data["cache_hit"] = False
            results.append(TestResult.model_validate(data))
        # pytest exit codes 2 (interrupted), 3 (internal error), 4 (usage error):
        # whatever results exist are partial, so do not present them as a run.
        if run_out.returncode in (2, 3, 4):
            raise RunError(
                f"pytest exited {run_out.returncode} (interrupted, internal or usage error):\n"
                + (run_out.stdout + run_out.stderr)[-4000:]
            )
        reported = {raw["test_id"] for raw in run_out.results}
        missing = [t for t in selected if invoke_id[t] not in reported]
        if missing:
            raise RunError(
                f"pytest did not report {len(missing)} selected test(s), e.g. {missing[0]}; "
                "check pytest_args for options that deselect tests"
            )
        if not results and run_out.returncode not in (0, 5):
            raise RunError(
                f"pytest exited {run_out.returncode} without results:\n{(run_out.stderr or run_out.stdout)[-4000:]}"
            )
        (run_dir / "pytest-stdout.txt").write_text(pytest_stdout, encoding="utf-8", newline="\n")

    duration_ms = round((time.monotonic() - t0) * 1000)
    counts = _counts(results)
    outcome: Literal["passed", "failed", "error"] = (
        "failed" if counts.failed else "error" if counts.error else "passed"
    )
    failures = [
        FailureSummary(
            test_id=r.test_id,
            error_type=r.failure.error_type,
            error_message=r.failure.error_message,
            file=r.failure.file or r.location.file,
            line=r.failure.line if r.failure.file else r.location.line,
            is_flaky=False,
            repro_command=r.failure.repro_command,
        )
        for r in results
        if r.failure is not None
    ]
    results_path = run_dir / "results.json"
    summary_kwargs: dict[str, Any] = {}
    if info.sha is not None:
        summary_kwargs["commit_sha"] = info.sha
    if info.tree is not None:
        summary_kwargs["commit_tree"] = info.tree
    if info.dirty is not None:
        summary_kwargs["working_tree_dirty"] = info.dirty

    summary = RunSummary(
        schema_version="1.0.0",
        run_id=run_id,
        started_at=started,
        finished_at=utc_now_iso(),
        duration_ms=duration_ms,
        selection=Selection(
            mode=options.mode,
            selected_count=len(selected),
            collected_count=len(collected_ids),
            deselected_count=len(collected_ids) - len(selected),
        ),
        # TODO(M6): the result cache. Until then every selected test is a miss.
        cache=CacheStats(hits=0, misses=len(selected), disabled=True),
        counts=counts,
        outcome=outcome,
        summary=_summary_text(counts, duration_ms, len(selected)),
        failures=failures,
        results_path=_rel(cfg, results_path),
        record_path=None,
        **summary_kwargs,
    )
    _write_json(results_path, [r.to_json() for r in results])
    _write_json(run_dir / "summary.json", summary.to_json())
    _write_json(cfg.repo_root / STATE_DIR / "last-run.json", summary.to_json())

    exit_code = 1 if (counts.failed or counts.error) else 0
    return RunOutcome(
        summary=summary, results=results, exit_code=exit_code, pytest_stdout=pytest_stdout
    )
