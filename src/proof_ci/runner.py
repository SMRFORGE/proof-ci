"""Orchestrates one ``proof-ci run`` (spec Section 5.7.3).

Modes: ``affected`` (default, Section 5.8), ``all``, ``explicit``, ``failed``.
Collection and execution go through the pytest adapter; the run consults the
result cache, records flake history, computes savings, and writes RunSummary,
results.json, and last-run.json.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from proof_ci._plugin_protocol import repro_command
from proof_ci.adapters.agent.identity import resolve_identity
from proof_ci.adapters.runners.pytest_runner import (
    PytestRunner,
    RunnerError,
    RunOutput,
    UsageError,
)
from proof_ci.affected import (
    BaseResolution,
    FallbackDecision,
    Hunk,
    ImportGraph,
    changed_files,
    changed_hunks,
    decide_fallback,
    likely_cause,
    references_data_file,
    resolve_base,
    select,
    since_tree,
    untraced_changes,
)
from proof_ci.cache import (
    CACHEABLE_OUTCOMES,
    CollectCache,
    CollectEntry,
    ResultCache,
    collect_cache_key,
    conftest_chain,
    deps_hash,
    file_sha256,
    list_repo_files,
    result_cache_key,
    trim_caches,
)
from proof_ci.config import Config, ConfigError
from proof_ci.flakes import OutcomeRow, code_hash, flake_store
from proof_ci.models import (
    CacheStats,
    Counts,
    FailureSummary,
    RunSummary,
    Savings,
    Selection,
    TestResult,
)
from proof_ci.redact import redact
from proof_ci.savings import RunFacts, SavingsLedger, compute_savings, ledger_row
from proof_ci.util import (
    CaseIndex,
    GitError,
    GitInfo,
    debug,
    git_info,
    run_git,
    utc_now_iso,
    write_json_atomic,
)

__all__ = [
    "RunError",
    "RunOptions",
    "RunOutcome",
    "SelectionPlan",
    "compute_affected",
    "execute_run",
    "plan_selection",
]

Mode = Literal["affected", "all", "explicit", "failed"]

STATE_DIR = ".proof-ci"


class RunError(Exception):
    """Runtime failure (exit 3)."""


@dataclass(frozen=True)
class RunOptions:
    mode: Mode
    tests: tuple[str, ...] = ()
    json_output: bool = False
    base: str | None = None
    no_cache: bool = False
    allow_known_flakes: bool = False
    calibrate: bool = False
    agent_id: str | None = None


@dataclass(frozen=True)
class RunOutcome:
    summary: RunSummary
    results: list[TestResult]
    exit_code: int
    pytest_stdout: str
    run_dir: Path
    savings: Savings
    notes: list[str] = field(default_factory=list)


def _require_pytest_command(cfg: Config) -> None:
    """Phase 1 runs pytest only, so the plugin always loads (spec Section 5.13)."""
    from proof_ci.discover import is_pytest_command

    if not is_pytest_command(cfg.project.test_command):
        shown = " ".join(cfg.project.test_command)
        raise ConfigError(
            f"[project].test_command = {shown!r} is not a pytest invocation; Phase 1 runs pytest "
            'directly so its plugin loads. Set test_command = ["pytest"] or '
            '["python", "-m", "pytest"] (extra arguments are fine).'
        )
    # Paths in the prefix would make pytest collect and run tests outside the selection.
    root = cfg.repo_root.resolve()
    start = (
        1 if Path(cfg.project.test_command[0]).name.lower().startswith(("pytest", "py.test")) else 3
    )
    for arg in cfg.project.test_command[start:]:
        if arg.startswith("-"):
            continue
        candidate = Path(arg.split("::", 1)[0])
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.exists():
            raise ConfigError(
                f"[project].test_command names the path {arg!r}; put test locations in "
                "[project].test_paths (or pass TESTS) so selection controls what runs"
            )


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


INCOMPLETE_MARKER = "run-in-progress"


def _previous_failed(cfg: Config, *, check_marker: bool = True) -> list[str]:
    marker = cfg.repo_root / STATE_DIR / INCOMPLETE_MARKER
    if check_marker and marker.is_file():
        raise ConfigError(
            "the last run did not finish (it crashed or was interrupted), so its failures "
            "are unknown; run `proof-ci run` or `proof-ci run --all` first"
        )
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


def _collect_error_result(err: dict[str, Any], case: CaseIndex) -> TestResult:
    """A collector pytest could not collect (e.g. ImportError in a test file), as an error
    result: plain pytest reports it as an error, so proof-ci must too (M13 E2)."""
    nodeid = case.canonical_node_id(str(err.get("nodeid") or "."))
    file = case.canonical(str(err.get("file") or nodeid.split("::", 1)[0] or "."))
    longrepr = str(err.get("longrepr") or "")
    last = next(
        (ln.strip() for ln in reversed(longrepr.splitlines()) if ln.strip()), "collection failed"
    )
    last = last[1:].strip() if last.startswith("E ") else last
    error_type, sep, message = last.partition(": ")
    if not sep or not error_type.replace(".", "").replace("_", "").isalnum():
        error_type, message = "CollectionError", last
    return TestResult.model_validate(
        {
            "schema_version": "1.0.0",
            "test_id": nodeid,
            "outcome": "error",
            "duration_ms": 0,
            "location": {"file": file, "line": 1},
            "message": f"collection error: {message}"[:500],
            "cache_hit": False,
            "failure": {
                "error_type": error_type.rsplit(".", 1)[-1],
                "error_message": message[:2000],
                "file": file,
                "traceback": longrepr,
                "repro_command": repro_command(nodeid),
            },
        }
    )


def _counts(results: list[TestResult]) -> Counts:
    tally = {k: 0 for k in ("passed", "failed", "skipped", "error", "xfailed", "xpassed")}
    for r in results:
        tally[r.outcome] += 1
    flaky = sum(
        1
        for r in results
        if r.outcome in ("failed", "error") and r.flaky is not None and r.flaky.is_flaky
    )
    return Counts(**tally, flaky_failures=flaky, total_selected=len(results))


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
    write_json_atomic(path, data)


# ------------------------------------------------------------------ selection


@dataclass
class SelectionPlan:
    """Everything selection decided before any test executes."""

    mode: Mode
    collected_ids: list[str]
    invoke_id: dict[str, str]
    item_file: dict[str, str]
    selected: list[str]
    selection: Selection
    changed: list[str]
    base: BaseResolution | None
    hunks: dict[str, list[Hunk]]
    branch: str | None
    collect_entry: CollectEntry | None
    env: CollectEnv
    graph: ImportGraph | None = None
    no_cache_files: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    collect_errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CollectEnv:
    """The environment of the interpreter that runs pytest (from collect mode)."""

    python: str
    platform: str
    installed: list[str]
    inipath: str | None


def _branch(repo: Path) -> str | None:
    try:
        proc = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    except GitError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _collect(
    cfg: Config,
    runner: PytestRunner,
    paths: list[str],
    use_cache: bool,
    tree: str | None = None,
) -> tuple[list[dict[str, Any]], CollectEntry | None, CollectEnv, list[dict[str, Any]]]:
    """Collect through the plugin, reusing the collection cache when its key matches.

    Returns the items, the cache entry when they came from the cache, and the
    pytest interpreter's environment.
    """
    if not use_cache:
        out = runner.collect(paths)
        env = CollectEnv(out.python, out.platform, out.installed, out.inipath)
        return out.items, None, env, out.errors
    cache = CollectCache(cfg.resolve(cfg.cache.collect_cache_dir))
    key = collect_cache_key(
        cfg.repo_root,
        cfg.project.test_paths,
        cfg.project.pytest_args,
        cfg.project.test_command,
        tree,
    )
    entry = cache.get(key)
    if entry is not None:
        debug("collection: cached")
        env = CollectEnv(entry.python, entry.platform, entry.installed, entry.inipath)
        return entry.items, entry, env, []
    debug("collection: fresh")
    out = runner.collect(paths)
    env = CollectEnv(out.python, out.platform, out.installed, out.inipath)
    if out.errors:
        return out.items, None, env, out.errors  # never cache a broken collection
    cache.put(
        CollectEntry(
            key=key,
            python=out.python,
            platform=out.platform,
            inipath=out.inipath,
            installed=out.installed,
            items=out.items,
            written_at=time.time(),
        )
    )
    return out.items, None, env, []


def plan_selection(
    cfg: Config,
    options: RunOptions,
    runner: PytestRunner,
    info: GitInfo,
    case: CaseIndex,
) -> SelectionPlan:
    """Spec Section 5.8 Steps 1-6 (and the other modes). Raises ConfigError/GitError/RunnerError."""
    in_git = info.sha is not None
    if options.mode == "affected" and not in_git:
        raise ConfigError(
            "affected selection needs a git repository with at least one commit; "
            "use `proof-ci run --all` or pass TESTS"
        )

    base: BaseResolution | None = None
    changed: list[str] = []
    hunks: dict[str, list[Hunk]] = {}
    if in_git:
        setting = options.base or cfg.affected.base
        try:
            base = resolve_base(cfg.repo_root, setting, bool(info.dirty))
        except GitError as exc:
            if options.mode == "affected" or options.base:
                raise ConfigError(str(exc)) from exc
        if base is not None:
            changed = changed_files(cfg.repo_root, base.base_ref)
            hunks = changed_hunks(cfg.repo_root, base.base_ref, changed)

    use_cache = cfg.cache.enabled and not options.no_cache and options.mode != "explicit"
    # Explicit arguments are mapped to git's committed spelling first: pytest matches node
    # ids case-sensitively even on case-insensitive filesystems (macOS APFS).
    paths = (
        [case.canonical_node_id(t) for t in options.tests]
        if options.mode == "explicit"
        else list(cfg.project.test_paths)
    )
    items, entry, env, collect_errors = _collect(cfg, runner, paths, use_cache, info.tree)
    collected_ids = [case.canonical_node_id(i["test_id"]) for i in items]
    invoke_id = {case.canonical_node_id(i["test_id"]): i["test_id"] for i in items}
    item_file = {case.canonical_node_id(i["test_id"]): case.canonical(i["file"]) for i in items}

    branch = _branch(cfg.repo_root) if in_git else None
    fallback = FallbackDecision(False, None)
    notes: list[str] = []
    graph: ImportGraph | None = None
    no_cache_files: set[str] = set()
    if options.mode == "affected":
        anchor = SavingsLedger(cfg.resolve(cfg.savings.db)).last_full(branch) if branch else None
        tree_last = anchor[0] if anchor else None
        # Files that differ from the anchor run's tree are selected too, even when they now
        # match the base again: the anchor ran them in their other state.
        considered = list(changed)
        if tree_last and info.tree and tree_last != info.tree:
            considered += sorted(since_tree(cfg.repo_root, tree_last, info.tree) - set(changed))
        graph = ImportGraph.build(
            cfg.repo_root, cfg.project.source_roots, cfg.project.test_paths, considered
        )
        # A test file that failed to collect is still a test file: its own edit selects it
        # (as a collection error), not the whole suite (M13 recheck).
        test_files = set(item_file.values()) | {
            case.canonical(str(e.get("nodeid") or "").partition("::")[0])
            for e in collect_errors
            if str(e.get("nodeid") or "").partition("::")[0].endswith(".py")
        }
        untraced = untraced_changes(graph, considered, test_files, cfg.project.source_roots)
        fallback = decide_fallback(
            cfg.repo_root,
            considered,
            cfg.affected.fallback_full_run_globs,
            tree_now=info.tree,
            tree_last=tree_last,
            extra_triggers=untraced.triggers,
        )
        pairs = [(t, item_file[t]) for t in collected_ids]
        chosen = select(cfg.repo_root, considered, pairs, graph, fallback)
        selected = chosen.selected
        no_cache_files = set(chosen.no_cache_files)
        if not fallback.fired:
            # Tests that launch untraced code, and the anchor run's failures: rerun fresh.
            failed_before = set(anchor[1]) if anchor and fallback.suppressed_by_tree else set()
            extra_files = set(untraced.extra_test_files)
            extra_files |= {f for f in failed_before if f in test_files}
            have = set(selected)
            add = {
                t for t, f in pairs if t not in have and (f in extra_files or t in failed_before)
            }
            if add:
                selected = [t for t in collected_ids if t in have or t in add]
            no_cache_files |= extra_files | {
                item_file[t] for t in collected_ids if t in failed_before
            }
        for path in chosen.unmatched_data_files:
            debug(f"changed non-Python file {path} is not referenced by any test file")
    elif options.mode == "failed":
        previous = set(_previous_failed(cfg, check_marker=False))  # checked before the run
        # A collection error is reported under the file's node id; once the file collects
        # again, rerun every test in it.
        selected = [t for t in collected_ids if t in previous or item_file[t] in previous]
        if not selected:
            gone = sorted(previous - set(collected_ids))
            notes.append(
                "nothing to rerun: the previous run had no failures"
                if not previous
                else f"nothing to rerun: {len(gone)} previously failing test(s) no longer collect"
                + (f" (e.g. {gone[0]})" if gone else "")
            )
    else:
        selected = collected_ids

    sel_kwargs: dict[str, Any] = {}
    if base is not None:
        sel_kwargs.update(
            base_setting=base.base_setting,
            base_ref=base.base_ref,
            changed_files=changed,
            fallback_full_run=fallback.fired,
            fallback_reason=fallback.reason,
        )
        if fallback.suppressed_by_tree:
            sel_kwargs["fallback_suppressed_by_tree"] = fallback.suppressed_by_tree
    selection = Selection(
        mode=options.mode,
        selected_count=len(selected),
        collected_count=len(collected_ids),
        deselected_count=len(collected_ids) - len(selected),
        **sel_kwargs,
    )
    if options.mode == "affected" and not selected and base is not None:
        state = "dirty" if info.dirty else "clean"
        notes.append(
            f"nothing affected: diffed against {base.base_setting} -> {base.base_ref[:12]} "
            f"({len(changed)} changed file(s)); working tree is {state}"
        )
    return SelectionPlan(
        mode=options.mode,
        collected_ids=collected_ids,
        invoke_id=invoke_id,
        item_file=item_file,
        selected=selected,
        selection=selection,
        changed=changed,
        base=base,
        hunks=hunks,
        branch=branch,
        collect_entry=entry,
        env=env,
        graph=graph,
        no_cache_files=no_cache_files,
        notes=notes,
        collect_errors=collect_errors,
    )


def _git_state(cfg: Config) -> tuple[GitInfo, CaseIndex]:
    try:
        info = git_info(cfg.repo_root)
    except GitError as exc:
        raise RunError(str(exc)) from exc
    case = CaseIndex.from_repo(cfg.repo_root) if info.sha is not None else CaseIndex([])
    return info, case


def compute_affected(cfg: Config, base: str | None) -> SelectionPlan:
    """``proof-ci affected``: selection only, never runs tests."""
    _require_pytest_command(cfg)
    info, case = _git_state(cfg)
    work = cfg.repo_root / STATE_DIR / "runs" / "affected"
    work.mkdir(parents=True, exist_ok=True)
    runner = PytestRunner(cfg.repo_root, cfg.project.test_command, work)
    try:
        return plan_selection(cfg, RunOptions(mode="affected", base=base), runner, info, case)
    except UsageError as exc:
        raise ConfigError(str(exc)) from exc
    except (GitError, RunnerError) as exc:
        raise RunError(str(exc)) from exc


def _check_run(out: RunOutput, ids: list[str], case: CaseIndex) -> None:
    # pytest exit codes 2 (interrupted), 3 (internal error), 4 (usage error):
    # whatever results exist are partial, so do not present them as a run.
    if out.returncode in (2, 3, 4):
        raise RunError(
            f"pytest exited {out.returncode} (interrupted, internal or usage error):\n"
            + redact((out.stdout + out.stderr)[-4000:])
        )
    # Compare canonical ids: on case-insensitive filesystems pytest may report a node
    # id in the file's on-disk case rather than the spelling it was invoked with.
    reported = {case.canonical_node_id(raw["test_id"]) for raw in out.results}
    missing = [t for t in ids if t not in reported]
    if missing:
        raise RunError(
            f"pytest did not report {len(missing)} selected test(s), e.g. {missing[0]}; "
            "check pytest_args for options that deselect tests"
        )


class _KeyBuilder:
    """Builds result-cache keys (spec Section 5.9), memoising file hashes per run."""

    def __init__(self, cfg: Config, plan: SelectionPlan) -> None:
        self.cfg = cfg
        self.repo = cfg.repo_root
        self.graph = plan.graph or ImportGraph.build(
            self.repo, cfg.project.source_roots, cfg.project.test_paths
        )
        self._hashes: dict[str, str | None] = {}
        self._inputs: dict[str, dict[str, str]] = {}
        self._data_files = [p for p in list_repo_files(self.repo) if not p.endswith(".py")]
        self._deps: dict[tuple[str, ...], str] = {}

    def _hash(self, rel: str) -> str | None:
        if rel not in self._hashes:
            path = self.repo / rel
            self._hashes[rel] = file_sha256(path) if path.is_file() else None
        return self._hashes[rel]

    def inputs(self, test_file: str, inipath: str | None) -> dict[str, str]:
        cached = self._inputs.get(test_file)
        if cached is None:
            files = {
                test_file,
                *self.graph.closure(test_file),
                *conftest_chain(self.repo, test_file),
                *(
                    dep
                    for conf in conftest_chain(self.repo, test_file)
                    for dep in self.graph.closure(conf)
                ),
            }
            try:
                text = (self.repo / test_file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for data in self._data_files:
                if references_data_file(text, data):
                    files.add(data)
            cached = {}
            for rel in files:
                digest = self._hash(rel)
                if digest is not None:
                    cached[rel] = digest
            self._inputs[test_file] = cached
        if inipath and not Path(inipath).is_absolute():
            digest = self._hash(inipath)
            if digest is not None:
                return {**cached, inipath: digest}
        return cached

    def key(self, test_id: str, test_file: str, env: CollectEnv) -> str:
        deps_key = tuple(env.installed)
        if deps_key not in self._deps:
            self._deps[deps_key] = deps_hash(self.repo, env.installed)
        return result_cache_key(
            test_id,
            self.inputs(test_file, env.inipath),
            self.cfg.project.pytest_args,
            env.python,
            env.platform,
            self._deps[deps_key],
        )


def _savings(
    cfg: Config,
    options: RunOptions,
    plan: SelectionPlan,
    summary: RunSummary,
    results: list[TestResult],
    *,
    hits: list[str],
    executed: dict[str, TestResult],
    pytest_ms: int,
    pytest_stdout: str,
    extra: tuple[str, ...],
    env: CollectEnv,
    info: GitInfo,
) -> Savings:
    """Calibrate when asked, compute the Savings object, and append the ledger row."""
    ledger = SavingsLedger(cfg.resolve(cfg.savings.db))
    if options.calibrate and plan.selected:
        durations = {
            t: r.duration_ms
            for t, r in executed.items()
            if r.outcome in ("passed", "failed", "skipped", "xfailed", "xpassed")
        }
        ledger.write_calibration(
            wall_ms=pytest_ms,
            fixed_overhead_ms=max(0, summary.duration_ms - pytest_ms),
            durations=durations,
            commit_sha=info.sha,
            python=env.python,
            platform=env.platform,
            run_id=str(summary.run_id),
        )
    selected_set = set(plan.selected)
    deselected = (
        [t for t in plan.collected_ids if t not in selected_set]
        if plan.mode == "affected" and not plan.selection.fallback_full_run
        else []
    )
    facts = RunFacts(
        is_calibration_run=options.calibrate,
        results=results,
        selected_count=len(plan.selected),
        collected_count=len(plan.collected_ids),
        deselected_ids=deselected,
        cache_hit_ids=hits,
        outcome=summary.outcome,
        failed_count=summary.counts.failed,
        flaky_failures=summary.counts.flaky_failures,
        actual_ms=summary.duration_ms,
        pytest_stdout=pytest_stdout,
        pytest_flags=" ".join(extra),
        branch=plan.branch,
        commit_tree=info.tree,
    )

    def structured_bytes(headline: str) -> int:
        doc = summary.model_copy(update={"savings_headline": headline}).to_json()
        doc.pop("savings", None)
        return len((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    savings = compute_savings(facts, cfg.savings, ledger, structured_bytes_for=structured_bytes)
    identity = resolve_identity(options.agent_id, cfg.record.agent_id, os.environ)
    ledger.append(
        ledger_row(
            savings,
            run_id=str(summary.run_id),
            commit_sha=info.sha,
            commit_tree=info.tree,
            branch=plan.branch,
            agent_id=identity.id,
            agent_name=identity.agent_name,
            outcome=summary.outcome,
            selected_count=len(plan.selected),
            collected_count=len(plan.collected_ids),
            duration_ms=summary.duration_ms,
            mode=plan.mode,
            full_failed=(
                [r.test_id for r in results if r.outcome in ("failed", "error")]
                if plan.mode in ("affected", "all")
                and bool(plan.selected)
                and len(plan.selected) == len(plan.collected_ids)
                and not hits
                and summary.outcome in ("passed", "failed")
                else None
            ),
        )
    )
    return savings


# ------------------------------------------------------------------------ run


def execute_run(cfg: Config, options: RunOptions) -> RunOutcome:
    """Run the selected tests and write the run artifacts. Raises ConfigError (2) or RunError (3)."""
    _require_pytest_command(cfg)
    if options.mode == "explicit":
        _validate_test_args(cfg, options.tests)
    if options.mode == "failed":
        _previous_failed(cfg)  # exit 2 early when there is no previous run

    started = utc_now_iso()
    t0 = time.monotonic()
    run_id = uuid.uuid4()
    run_dir = cfg.repo_root / STATE_DIR / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Removed once last-run.json describes this run; while it exists `--failed` refuses
    # to trust the previous run's failures (M13 C6).
    marker = cfg.repo_root / STATE_DIR / INCOMPLETE_MARKER
    marker.write_text(str(run_id) + "\n", encoding="utf-8")

    info, case = _git_state(cfg)
    runner = PytestRunner(cfg.repo_root, cfg.project.test_command, run_dir)
    extra = _pytest_args(cfg, options.json_output)
    try:
        plan = plan_selection(cfg, options, runner, info, case)
    except UsageError as exc:
        raise ConfigError(str(exc)) from exc
    except (GitError, RunnerError) as exc:
        raise RunError(str(exc)) from exc

    cache_on = cfg.cache.enabled and not options.no_cache
    # Reads are skipped when the fallback fired (a change selection cannot trace, so the
    # cache keys cannot capture it either) and under --all, which promises a fresh run of
    # every test (M13: replaying passes under --all hid real failures).
    read_cache = (
        cache_on
        and not plan.selection.fallback_full_run
        and not options.calibrate
        and plan.mode != "all"
    )
    result_cache = ResultCache(cfg.resolve(cfg.cache.dir))
    # Keys also give each test its code_hash for the flake store.
    keys = _KeyBuilder(cfg, plan) if plan.selected else None
    store = flake_store(cfg)
    flakes_before = store.snapshot()
    env = plan.env
    base_ref = plan.base.base_ref if plan.base else ""
    hits: dict[str, TestResult] = {}
    executed: dict[str, TestResult] = {}
    stdout_parts: list[str] = []

    def lookup(current: CollectEnv) -> dict[str, TestResult]:
        found: dict[str, TestResult] = {}
        if keys is None or not read_cache:
            return found
        wanted: dict[str, str] = {}
        for test_id in plan.selected:
            if flakes_before.blocks_cache(test_id):
                continue
            if plan.item_file[test_id] in plan.no_cache_files:
                continue  # selected for a reason its cache key does not capture
            wanted[test_id] = keys.key(test_id, plan.item_file[test_id], current)
        stored_by_key = result_cache.get_many(wanted.values())
        for test_id, key in wanted.items():
            stored = stored_by_key.get(key)
            if stored is not None and stored.get("test_id") == test_id:
                found[test_id] = TestResult.model_validate({**stored, "cache_hit": True})
        return found

    pytest_ms = 0

    collect_errors: dict[str, dict[str, Any]] = {
        str(e.get("nodeid")): e for e in plan.collect_errors
    }

    def execute(ids: list[str], retry: bool = True) -> RunOutput:
        nonlocal pytest_ms
        started_pytest = time.monotonic()
        # Running every collected test: hand pytest the collection paths, not thousands
        # of node ids (pytest matches each id against its file's items, which costs more
        # than the tests on large suites). Same paths and args as the collect pass, so the
        # same tests run; _check_run still verifies every id came back.
        whole = (
            plan.mode != "explicit"
            and not collect_errors
            and len(ids) == len(plan.collected_ids)
            and set(ids) == set(plan.collected_ids)
        )
        args = list(cfg.project.test_paths) if whole else [plan.invoke_id[t] for t in ids]
        out = runner.run(args, extra)
        pytest_ms += round((time.monotonic() - started_pytest) * 1000)
        if out.returncode == 2 and out.errors:
            # A selected file no longer collects (pytest then runs nothing at all): report
            # the error, drop the stale collection, and run the rest once more.
            for err in out.errors:
                collect_errors[str(err.get("nodeid"))] = err
            if plan.collect_entry is not None:
                CollectCache(cfg.resolve(cfg.cache.collect_cache_dir)).delete(
                    plan.collect_entry.key
                )
            bad = {case.canonical(str(e.get("file") or "")) for e in out.errors}
            rest = [t for t in ids if plan.item_file[t] not in bad]
            if len(rest) < len(ids):
                stdout_parts.append(out.stdout)
                return execute(rest, retry=False) if rest and retry else out
        _check_run(out, ids, case)
        wanted = set(ids)
        stdout_parts.append(out.stdout)
        for raw in out.results:
            data = _canonicalise(raw, case)
            data["cache_hit"] = False
            failure = data.get("failure")
            if failure is not None and plan.hunks:
                cause = likely_cause(failure.get("traceback", ""), plan.hunks, base_ref)
                if cause is not None:
                    data["failure"] = {**failure, "likely_cause": cause}
            if data["test_id"] not in wanted:
                continue  # e.g. paths in test_command made pytest run extra tests
            result = TestResult.model_validate(data)
            executed[result.test_id] = result
        return out

    def fresh_env() -> CollectEnv:
        out = runner.collect(list(cfg.project.test_paths))
        return CollectEnv(out.python, out.platform, out.installed, out.inipath)

    try:
        hits = lookup(env)
        entry = plan.collect_entry
        recheck = cfg.cache.collect_cache_env_recheck_minutes
        if (
            hits
            and len(hits) == len(plan.selected)
            and entry is not None
            and entry.age_minutes() > recheck
        ):
            # Nothing would execute and the cached environment is old: refresh it
            # before serving hits (Section 5.8 Step 5).
            env = fresh_env()
            hits = lookup(env)
        to_run = [t for t in plan.selected if t not in hits]
        if to_run:
            out = execute(to_run)
            live_installed = sorted(out.installed) if out.installed else sorted(env.installed)
            if (env.python, env.platform, sorted(env.installed)) != (
                out.python,
                out.platform,
                live_installed,
            ):
                # Environment consistency: the cached collection described another
                # interpreter. Drop it, re-key with the live environment, and
                # execute any hits that were served under the stale one.
                if entry is not None:
                    CollectCache(cfg.resolve(cfg.cache.collect_cache_dir)).delete(entry.key)
                env = fresh_env()
                stale = [t for t in plan.selected if t in hits]
                hits = {}
                if stale:
                    execute(stale)
    except UsageError as exc:
        raise ConfigError(str(exc)) from exc
    except (GitError, RunnerError) as exc:
        raise RunError(str(exc)) from exc

    # Flake history: freshly executed outcomes only, never cache replays (Section 5.9).
    # A test is flaky only if it passes and fails on the same code, and the import graph
    # cannot see every input (plugins, subprocesses, data read by code). So outcomes are
    # grouped by the whole working tree as well as the test's own inputs: an edit and its
    # revert never count as a flip (M13 C5).
    code_hashes: dict[str, str] = {}
    if keys is not None:
        tree = {":tree": info.tree} if info.tree else {}
        for test_id in plan.selected:
            inputs = keys.inputs(plan.item_file[test_id], env.inipath)
            code_hashes[test_id] = code_hash(test_id, {**inputs, **tree})
        now = utc_now_iso()
        store.record_many(
            [
                OutcomeRow(
                    test_id,
                    code_hashes[test_id],
                    result.outcome,
                    now,
                    result.duration_ms,
                    str(run_id),
                    info.sha,
                    result.failure.error_message[:500] if result.failure else None,
                )
                for test_id, result in executed.items()
            ]
        )
    flakes_after = store.snapshot()
    results = [
        (hits[t] if t in hits else executed[t]).model_copy(update={"flaky": flakes_after.info(t)})
        for t in plan.selected
        if t in hits or t in executed  # tests in a file that stopped collecting are not
    ]
    results.extend(_collect_error_result(err, case) for err in collect_errors.values())
    pytest_stdout = "".join(stdout_parts)
    if stdout_parts:
        (run_dir / "pytest-stdout.txt").write_text(
            redact(pytest_stdout), encoding="utf-8", newline="\n"
        )

    if cache_on and keys is not None:
        # Replayed hits are never re-written and never enter flake history
        # (Section 5.9): only fresh executions feed both.
        result_cache.put_many(
            (keys.key(test_id, plan.item_file[test_id], env), result.to_json())
            for test_id, result in executed.items()
            if result.outcome in CACHEABLE_OUTCOMES and not flakes_after.blocks_cache(test_id)
        )
        trim_caches([result_cache.directory, cfg.resolve(cfg.cache.collect_cache_dir)])

    duration_ms = round((time.monotonic() - t0) * 1000)
    counts = _counts(results)
    # --allow-known-flakes excuses confirmed/ignored flakes on unchanged code only.
    blocking = [
        r
        for r in results
        if r.outcome in ("failed", "error")
        and not (
            options.allow_known_flakes
            and flakes_after.allowed_failure(r.test_id, code_hashes.get(r.test_id))
        )
    ]
    outcome: Literal["passed", "failed", "error"] = (
        "failed"
        if any(r.outcome == "failed" for r in blocking)
        else "error"
        if blocking
        else "passed"
    )
    failures = [
        FailureSummary(
            test_id=r.test_id,
            error_type=r.failure.error_type,
            error_message=r.failure.error_message,
            file=r.failure.file or r.location.file,
            line=r.failure.line if r.failure.file else r.location.line,
            is_flaky=bool(r.flaky and r.flaky.is_flaky),
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

    nothing_affected = plan.mode == "affected" and not plan.selected
    summary = RunSummary(
        schema_version="1.0.0",
        run_id=run_id,
        started_at=started,
        finished_at=utc_now_iso(),
        duration_ms=duration_ms,
        selection=plan.selection,
        cache=CacheStats(
            hits=len(hits), misses=len(plan.selected) - len(hits), disabled=not cache_on
        ),
        counts=counts,
        outcome=outcome,
        summary=(
            "0 selected, 0 failed (nothing affected)"
            if nothing_affected
            else _summary_text(counts, duration_ms, len(plan.selected))
            + (
                f"; {allowed_flaky} known-flaky failure(s) allowed"
                if (allowed_flaky := counts.failed + counts.error - len(blocking))
                else ""
            )
        ),
        failures=failures,
        results_path=_rel(cfg, results_path),
        record_path=None,
        **summary_kwargs,
    )
    savings = _savings(
        cfg,
        options,
        plan,
        summary,
        results,
        hits=list(hits),
        executed=executed,
        pytest_ms=pytest_ms,
        pytest_stdout=pytest_stdout,
        extra=extra,
        env=env,
        info=info,
    )
    summary = summary.model_copy(update={"savings": savings, "savings_headline": savings.headline})
    _write_json(results_path, [r.to_json() for r in results])
    _write_json(run_dir / "summary.json", summary.to_json())
    # The interpreter that ran pytest, for RunRecord.payload.environment (M8).
    _write_json(run_dir / "environment.json", {"python": env.python, "platform": env.platform})
    _write_json(cfg.repo_root / STATE_DIR / "last-run.json", summary.to_json())
    marker.unlink(missing_ok=True)

    exit_code = 1 if blocking else 0
    return RunOutcome(
        summary=summary,
        results=results,
        exit_code=exit_code,
        pytest_stdout=pytest_stdout,
        notes=plan.notes,
        run_dir=run_dir,
        savings=savings,
    )
