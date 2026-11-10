"""Savings: calibration, per-run estimation, ledger, and reports (spec Section 5.12).

Honesty rules this module enforces:

* Figures that cannot be measured are 0 with ``estimated``/``unavailable`` and
  low confidence; nothing is invented from an assumed industry average.
* Local test minutes are the user's machine, never CI time, and are priced
  only when ``[savings].price_local_minutes`` is true.
* Tokens are measured from pytest's actual stdout; ``tokens_delta`` is signed
  and is never shown in the headline.
* Dollars appear in a headline only when every applied price key is present in
  ``proof-ci.toml`` (``prices_configured``), and always carry ``est.``.
* All money and time values are integers (micro-USD, milliseconds).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

import rfc8785

from proof_ci.config import SavingsConfig
from proof_ci.models import (
    Calibration,
    CiRunsMetric,
    LocalComputeMetric,
    PricesUsed,
    Savings,
    SavingsGroup,
    SavingsReport,
    SavingsTotals,
    TestResult,
    TokensMetric,
    WaitMetric,
    WrongTurnsMetric,
)
from proof_ci.util import debug, utc_now_iso

__all__ = [
    "CalibrationMeta",
    "RunFacts",
    "SavingsLedger",
    "build_report",
    "compute_savings",
    "human_duration",
    "parse_when",
    "payoff",
    "report_for",
    "to_micro",
    "token_count",
]

Confidence = Literal["high", "medium", "low"]
_RANK = {"low": 0, "medium": 1, "high": 2}

SCHEMA = """
CREATE TABLE IF NOT EXISTS calibration_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    calibrated_at TEXT NOT NULL,
    wall_ms INTEGER NOT NULL,
    fixed_overhead_ms INTEGER NOT NULL,
    payoff TEXT NOT NULL,
    test_count INTEGER NOT NULL,
    commit_sha TEXT,
    python TEXT,
    platform TEXT,
    run_id TEXT
);

CREATE TABLE IF NOT EXISTS calibration_tests (
    test_id TEXT PRIMARY KEY,
    duration_ms INTEGER NOT NULL,
    measured_at TEXT NOT NULL,
    run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS savings_ledger (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    commit_sha TEXT,
    commit_tree TEXT,
    branch TEXT,
    agent_id TEXT,
    agent_name TEXT,
    headline TEXT NOT NULL,
    overall_confidence TEXT NOT NULL,
    prices_configured INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    selected_count INTEGER NOT NULL,
    collected_count INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    wrong_turns_avoided INTEGER NOT NULL,
    ci_runs_avoided INTEGER NOT NULL,
    ci_runs_dedupe_key TEXT,
    test_ms_not_executed INTEGER NOT NULL,
    wait_ms_saved INTEGER NOT NULL,
    tokens_delta INTEGER NOT NULL,
    cost_micro_usd INTEGER NOT NULL,
    defaults_used TEXT NOT NULL,
    savings_json TEXT NOT NULL,
    record_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_savings_time ON savings_ledger (recorded_at);
CREATE INDEX IF NOT EXISTS idx_savings_agent ON savings_ledger (agent_id);
CREATE INDEX IF NOT EXISTS idx_savings_branch ON savings_ledger (branch, recorded_at);
CREATE INDEX IF NOT EXISTS idx_savings_dedupe ON savings_ledger (ci_runs_dedupe_key);
"""


# --------------------------------------------------------------------- helpers


def to_micro(dollars: float) -> int:
    """Dollars to integer micro-USD, half away from zero (spec Section 5.12)."""
    return int((Decimal(str(dollars)) * 1_000_000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def token_count(n_bytes: int) -> int:
    """The frozen Phase 1 tokenizer ``utf8_bytes_div_4``."""
    return n_bytes // 4


def human_duration(ms: int) -> str:
    seconds = max(0, round(ms / 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def payoff(wall_ms: int | None, fixed_overhead_ms: int | None) -> str:
    if wall_ms is None or fixed_overhead_ms is None:
        return "unknown"
    if wall_ms < 3 * fixed_overhead_ms:
        return "overhead_dominates"
    if wall_ms < 10 * fixed_overhead_ms:
        return "marginal"
    return "selection_saves_time"


def payoff_sentence(verdict: str, wall_ms: int, overhead_ms: int) -> str:
    suite, overhead = human_duration(wall_ms), f"{overhead_ms / 1000:.1f}s"
    head = f"Full suite {suite}; proof-ci fixed overhead {overhead}."
    return {
        "selection_saves_time": f"{head} Affected-test selection will save time here.",
        "marginal": (
            f"{head} Selection saves little; the flake flags, structured failures, and signed "
            "records are the reason to keep proof-ci on this repo."
        ),
        "overhead_dominates": (
            f"{head} Plain pytest is faster here; keep proof-ci for structure and records only, "
            "or run it less often."
        ),
    }.get(verdict, head)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_when(value: str, now: datetime | None = None) -> datetime:
    """RFC 3339 timestamp or a relative window ``Nd`` / ``Nw`` back from now (UTC)."""
    now = now or datetime.now(UTC)
    m = re.fullmatch(r"(\d+)([dw])", value.strip())
    if m:
        n = int(m.group(1))
        return now - (timedelta(days=n) if m.group(2) == "d" else timedelta(weeks=n))
    try:
        parsed = _parse_iso(value)
    except ValueError as exc:
        raise ValueError(f"cannot parse {value!r}; use an RFC 3339 time or Nd / Nw") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _min_conf(*values: Confidence) -> Confidence:
    return min(values, key=lambda v: _RANK[v])


# ---------------------------------------------------------------------- ledger


@dataclass(frozen=True)
class CalibrationMeta:
    calibrated_at: str
    wall_ms: int
    fixed_overhead_ms: int
    payoff: str
    test_count: int


class SavingsLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5.0)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            if not mode or str(mode[0]).lower() != "wal":
                conn.execute("PRAGMA journal_mode = DELETE")
                debug(f"{self.path}: WAL unavailable, using journal_mode=DELETE")
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(savings_ledger)")}
            if "mode" not in columns:
                # Which selection mode produced the row: only affected/all runs honour the
                # full-run fallback, so only they may suppress it later (Section 5.8 Step 3).
                conn.execute("ALTER TABLE savings_ledger ADD COLUMN mode TEXT")
            if "full_failed" not in columns:
                # Set only on a run that executed every collected test fresh to completion:
                # the JSON list of test ids that failed or errored in it ("[]" when green).
                # Such a run anchors the full-run fallback; see last_full (M13 C4).
                conn.execute("ALTER TABLE savings_ledger ADD COLUMN full_failed TEXT")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def calibration(self) -> CalibrationMeta | None:
        if not self.path.exists():
            return None
        with self._connect() as conn:
            r = conn.execute(
                "SELECT calibrated_at, wall_ms, fixed_overhead_ms, payoff, test_count "
                "FROM calibration_meta WHERE id = 1"
            ).fetchone()
        return CalibrationMeta(*r) if r else None

    def calibrated_durations(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        with self._connect() as conn:
            return dict(
                conn.execute("SELECT test_id, duration_ms FROM calibration_tests").fetchall()
            )

    def runs_since(self, iso: str) -> int:
        if not self.path.exists():
            return 0
        with self._connect() as conn:
            r = conn.execute(
                "SELECT COUNT(*) FROM savings_ledger WHERE recorded_at > ? "
                "AND json_extract(savings_json, '$.is_calibration_run') = 0",
                (iso,),
            ).fetchone()
        return int(r[0]) if r else 0

    def last_full(self, branch: str) -> tuple[str, list[str]] | None:
        """Tree and failing test ids of the last run on ``branch`` that executed every
        collected test fresh to completion, green or not.

        The full-run fallback is suppressed only relative to such a run. A red one still
        anchors, but the caller must rerun its failures, so a failing full run never lets
        the next run quietly report less (M13 C4) and a fix loop is not a full run per try.
        """
        if not self.path.exists():
            return None
        with self._connect() as conn:
            r = conn.execute(
                "SELECT commit_tree, full_failed FROM savings_ledger WHERE branch = ? "
                "AND commit_tree IS NOT NULL AND mode IN ('affected', 'all') "
                "AND full_failed IS NOT NULL ORDER BY recorded_at DESC, id DESC LIMIT 1",
                (branch,),
            ).fetchone()
        if not r:
            return None
        try:
            failed = json.loads(r[1])
        except ValueError:
            return None
        if not isinstance(failed, list):
            return None
        return str(r[0]), [str(x) for x in failed]

    def dedupe_seen(self, key: str) -> bool:
        if not self.path.exists():
            return False
        with self._connect() as conn:
            r = conn.execute(
                "SELECT 1 FROM savings_ledger WHERE ci_runs_dedupe_key = ? LIMIT 1", (key,)
            ).fetchone()
        return r is not None

    def write_calibration(
        self,
        *,
        wall_ms: int,
        fixed_overhead_ms: int,
        durations: dict[str, int],
        commit_sha: str | None,
        python: str,
        platform: str,
        run_id: str,
    ) -> CalibrationMeta:
        now = utc_now_iso()
        verdict = payoff(wall_ms, fixed_overhead_ms)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO calibration_meta (id, calibrated_at, wall_ms, fixed_overhead_ms, payoff, "
                "test_count, commit_sha, python, platform, run_id) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET calibrated_at=excluded.calibrated_at, "
                "wall_ms=excluded.wall_ms, fixed_overhead_ms=excluded.fixed_overhead_ms, "
                "payoff=excluded.payoff, test_count=excluded.test_count, "
                "commit_sha=excluded.commit_sha, python=excluded.python, "
                "platform=excluded.platform, run_id=excluded.run_id",
                (
                    now,
                    wall_ms,
                    fixed_overhead_ms,
                    verdict,
                    len(durations),
                    commit_sha,
                    python,
                    platform,
                    run_id,
                ),
            )
            conn.executemany(
                "INSERT INTO calibration_tests (test_id, duration_ms, measured_at, run_id) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(test_id) DO UPDATE SET "
                "duration_ms=excluded.duration_ms, measured_at=excluded.measured_at, "
                "run_id=excluded.run_id",
                [(t, d, now, run_id) for t, d in durations.items()],
            )
        return CalibrationMeta(now, wall_ms, fixed_overhead_ms, verdict, len(durations))

    def append(self, row: dict[str, Any]) -> None:
        cols = list(row)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO savings_ledger ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [row[c] for c in cols],
            )

    def set_record_path(self, run_id: str, record_path: str) -> None:
        if not self.path.exists():
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE savings_ledger SET record_path = ? WHERE run_id = ?", (record_path, run_id)
            )

    def rows(self, since: str, until: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            found = conn.execute(
                "SELECT * FROM savings_ledger WHERE recorded_at >= ? AND recorded_at <= ? "
                "ORDER BY recorded_at, id",
                (since, until),
            ).fetchall()
        return [dict(r) for r in found]


# ------------------------------------------------------------------ estimation


@dataclass(frozen=True)
class RunFacts:
    """What one run did, as the estimator needs it."""

    is_calibration_run: bool
    results: list[TestResult]
    selected_count: int
    collected_count: int
    deselected_ids: list[str]
    cache_hit_ids: list[str]
    outcome: str
    failed_count: int
    flaky_failures: int
    actual_ms: int
    pytest_stdout: str
    pytest_flags: str
    branch: str | None
    commit_tree: str | None


def _prices(cfg: SavingsConfig) -> PricesUsed:
    return PricesUsed(
        ci_cost_per_minute_micro_usd=to_micro(cfg.ci_cost_per_minute),
        price_local_minutes=cfg.price_local_minutes,
        local_compute_cost_per_minute_micro_usd=to_micro(cfg.local_compute_cost_per_minute),
        model_input_cost_per_million_tokens_micro_usd=to_micro(
            cfg.model_input_cost_per_million_tokens
        ),
        model_output_cost_per_million_tokens_micro_usd=to_micro(
            cfg.model_output_cost_per_million_tokens
        ),
        wasted_fix_cost_micro_usd=to_micro(cfg.wasted_fix_cost),
        engineer_hourly_cost_micro_usd=0,
        defaults_used=cfg.defaults_used,
    )


def _freshness(
    meta: CalibrationMeta | None, runs_since: int, cfg: SavingsConfig
) -> Literal["missing", "stale", "current"]:
    if meta is None:
        return "missing"
    age = datetime.now(UTC) - _parse_iso(meta.calibrated_at)
    if (
        age > timedelta(days=cfg.calibration_max_age_days)
        or runs_since > cfg.calibration_stale_after_runs
    ):
        return "stale"
    return "current"


def dedupe_key(branch: str | None, commit_tree: str | None, failing: list[str]) -> str:
    doc: dict[str, Any] = {
        "branch": branch or "",
        "commit_tree": commit_tree or "",
        "failing": sorted(failing),
    }
    return hashlib.sha256(rfc8785.dumps(doc)).hexdigest()


def compute_savings(
    facts: RunFacts,
    cfg: SavingsConfig,
    ledger: SavingsLedger,
    *,
    structured_bytes_for: Any,
) -> Savings:
    """Build the Savings object. ``structured_bytes_for(headline)`` returns the
    byte length of the RunSummary JSON (without savings) carrying that headline."""
    prices = _prices(cfg)
    meta = ledger.calibration()
    status = _freshness(meta, ledger.runs_since(meta.calibrated_at) if meta else 0, cfg)
    durations = ledger.calibrated_durations() if meta else {}
    cal = facts.is_calibration_run

    # (a) wrong turns avoided
    flagged = sum(
        1
        for r in facts.results
        if r.outcome in ("failed", "error")
        and r.flaky is not None
        and r.flaky.status in ("confirmed", "ignored")
    )
    wrong = WrongTurnsMetric(
        status="measured",
        confidence="high",
        flagged_known_flake_failures=flagged,
        cost_micro_usd=flagged * prices.wasted_fix_cost_micro_usd,
        rationale=(
            f"{flagged} failure(s) carried a confirmed or ignored flake flag, so the agent was told "
            "not to chase them."
            if flagged
            else "No failures in this run had flaky.status of confirmed or ignored."
        ),
    )

    # baseline shared by (b) and (d)
    if cfg.full_pipeline_baseline_ms > 0:
        baseline_kind: Literal["none", "full_suite_local", "full_pipeline_user"] = (
            "full_pipeline_user"
        )
        baseline_ms: int | None = cfg.full_pipeline_baseline_ms
    elif meta is not None:
        baseline_kind, baseline_ms = "full_suite_local", meta.wall_ms
    else:
        baseline_kind, baseline_ms = "none", None

    # (b) CI runs avoided, deduplicated
    non_flaky_failing = sorted(
        r.test_id
        for r in facts.results
        if r.outcome == "failed" and not (r.flaky is not None and r.flaky.is_flaky)
    )
    qualifies = facts.outcome == "failed" and facts.failed_count - facts.flaky_failures > 0
    if qualifies:
        new_key = dedupe_key(facts.branch, facts.commit_tree, non_flaky_failing)
        seen = ledger.dedupe_seen(new_key)
        key: str | None = new_key
        count = 0 if seen else 1
        ci_cost = (
            (baseline_ms * prices.ci_cost_per_minute_micro_usd) // 60_000
            if count and baseline_ms is not None
            else 0
        )
        if seen:
            why = "This failing set on this branch and tree was already recorded; the agent is iterating on a known failure, so no new CI run is counted."
        elif baseline_ms is None:
            why = "The run had a non-flaky failure not previously recorded for this branch and tree, so a push would likely have produced a failing CI run. No pipeline baseline is stored, so the avoided-run cost is 0 rather than invented."
        else:
            why = f"The run had a non-flaky failure not previously recorded for this branch and tree; costed at the {baseline_kind} baseline of {baseline_ms} ms."
        ci = CiRunsMetric(
            status="estimated" if count else "measured",
            confidence="medium" if count else "high",
            count=count,
            dedupe_key=key,
            deduplicated=seen,
            cost_micro_usd=ci_cost,
            rationale=why,
        )
    else:
        ci = CiRunsMetric(
            status="measured",
            confidence="high",
            count=0,
            dedupe_key=None,
            deduplicated=False,
            cost_micro_usd=0,
            rationale="The run had no non-flaky failures, so no failing CI run was avoided.",
        )

    # (c) local compute not executed (user's machine, never CI)
    uncalibrated = 0
    if cal:
        local_ms, lc_status, lc_conf = 0, "measured", "high"
        lc_why = "This is a calibration run: it is the baseline, so it claims no local time saved against itself."
    elif status == "missing":
        uncalibrated = len(facts.deselected_ids)
        local_ms, lc_status, lc_conf = 0, "estimated", "low"
        lc_why = "No calibration exists yet, so deselected tests have no measured duration and local test time not executed is reported as 0 rather than invented."
    else:
        local_ms = 0
        for t in facts.deselected_ids:
            if t in durations:
                local_ms += durations[t]
            else:
                uncalibrated += 1
        by_id = {r.test_id: r for r in facts.results}
        for t in facts.cache_hit_ids:
            local_ms += durations.get(t, by_id[t].duration_ms if t in by_id else 0)
        if status == "current" and uncalibrated == 0:
            lc_status, lc_conf = "measured", "high"
        else:
            lc_status, lc_conf = "estimated", "medium"
        lc_why = (
            f"Sum of calibrated durations for {len(facts.deselected_ids)} deselected test(s) and "
            f"{len(facts.cache_hit_ids)} cache hit(s): time on this machine, not CI time"
            + (
                f"; {uncalibrated} deselected test(s) had no calibrated duration and were skipped"
                if uncalibrated
                else ""
            )
            + (" (calibration is stale)." if status == "stale" else ".")
        )
    priced = cfg.price_local_minutes
    local_cost = (
        (local_ms * prices.local_compute_cost_per_minute_micro_usd) // 60_000 if priced else 0
    )
    lc_why += (
        " Local minutes are priced (price_local_minutes is true)."
        if priced
        else " Local minutes are not priced (price_local_minutes is false)."
    )
    local = LocalComputeMetric(
        status=lc_status,  # type: ignore[arg-type]
        confidence=lc_conf,  # type: ignore[arg-type]
        deselected_count=len(facts.deselected_ids),
        cache_hits=len(facts.cache_hit_ids),
        test_ms_not_executed=local_ms,
        priced=priced,
        cost_micro_usd=local_cost,
        rationale=lc_why,
    )

    # (d) wait versus the full local suite (or the user's pipeline)
    if cal:
        wait = WaitMetric(
            status="measured",
            confidence="high",
            baseline_kind=baseline_kind,
            baseline_ms=baseline_ms,
            actual_ms=facts.actual_ms,
            wait_ms_saved=0,
            rationale="This is a calibration run: it is the baseline, so it claims no wait saved against itself.",
        )
    elif baseline_kind == "none":
        wait = WaitMetric(
            status="unavailable",
            confidence="low",
            baseline_kind="none",
            baseline_ms=None,
            actual_ms=facts.actual_ms,
            wait_ms_saved=0,
            rationale="No full-suite calibration or user-supplied pipeline baseline is stored.",
        )
    else:
        assert baseline_ms is not None
        saved = max(0, baseline_ms - facts.actual_ms)
        w_status: Literal["measured", "estimated", "unavailable"]
        w_conf: Confidence
        if baseline_kind == "full_pipeline_user":
            w_status, w_conf = "measured", "high"
            w_why = f"Versus the user-measured CI pipeline time of {baseline_ms} ms ([savings].full_pipeline_baseline_ms)."
        elif status == "current":
            w_status, w_conf = "measured", "medium"
            w_why = f"Versus running the full suite locally ({baseline_ms} ms at calibration); a proxy, not CI queue time."
        else:
            w_status, w_conf = "estimated", "low"
            w_why = (
                f"Versus running the full suite locally ({baseline_ms} ms at a stale calibration)."
            )
        wait = WaitMetric(
            status=w_status,
            confidence=w_conf,
            baseline_kind=baseline_kind,
            baseline_ms=baseline_ms,
            actual_ms=facts.actual_ms,
            wait_ms_saved=saved,
            rationale=w_why,
        )

    # totals (tokens filled after the headline exists)
    def money(tokens_cost: int) -> int:
        return wrong.cost_micro_usd + ci.cost_micro_usd + local.cost_micro_usd + tokens_cost

    # headline: time and counts first; money only when prices are configured
    hits = len(facts.cache_hit_ids)
    if local_ms <= 0:
        minutes = "0"
    elif local_ms < 60_000:
        minutes = "<1"
    else:
        minutes = f"{local_ms // 60_000:,}"
    note = {"missing": " (uncalibrated)", "stale": " (stale calibration)"}.get(status, "")

    def headline(cost: int) -> str:
        base = (
            f"Checked {facts.selected_count:,}/{facts.collected_count:,} ({hits:,} cached) in "
            f"{human_duration(facts.actual_ms)}; flaky flagged {flagged:,}; CI runs avoided "
            f"{ci.count:,}; local test-min not run ~{minutes}{note}"
        )
        if prices.defaults_used == [] and cost > 0:
            base += f"; ~${cost / 1_000_000:,.2f} est."
        return base

    # (e) tokens versus pytest's own output; structured size uses a headline
    # without money first, then is recomputed once with the final headline.
    raw_bytes = len(facts.pytest_stdout.encode("utf-8"))
    raw_tokens = token_count(raw_bytes)

    def tokens_for(head: str) -> TokensMetric:
        structured = structured_bytes_for(head)
        delta = raw_tokens - token_count(structured)
        cost = (max(0, delta) * prices.model_input_cost_per_million_tokens_micro_usd) // 1_000_000
        return TokensMetric(
            status="measured",
            confidence="medium",
            raw_source="pytest_stdout",
            raw_bytes=raw_bytes,
            structured_bytes=structured,
            raw_tokens=raw_tokens,
            structured_tokens=token_count(structured),
            tokens_delta=delta,
            cost_micro_usd=cost,
            tokenizer="utf8_bytes_div_4",
            rationale=(
                f"Measured the pytest subprocess stdout ({raw_bytes} bytes at {facts.pytest_flags or 'default verbosity'}) "
                f"against the RunSummary JSON without the savings object ({structured} bytes); "
                + (
                    "the structured form is larger for this run, so no token saving is claimed."
                    if delta <= 0
                    else f"{delta} fewer tokens, priced at the input-token rate."
                )
            ),
        )

    tokens = tokens_for(headline(money(0)))
    final_head = headline(money(tokens.cost_micro_usd))
    tokens = tokens_for(final_head)
    final_head = headline(money(tokens.cost_micro_usd))

    overall = _min_conf(
        wrong.confidence,
        ci.confidence,
        local.confidence,
        *([wait.confidence] if wait.status != "unavailable" else []),
    )
    if status == "missing":
        overall = "low"
    elif status == "stale":
        overall = _min_conf(overall, "medium")

    cal_model = Calibration(
        status=status,
        calibrated_at=meta.calibrated_at if meta else None,
        test_count=meta.test_count if meta else 0,
        full_suite_wall_ms=meta.wall_ms if meta else None,
        fixed_overhead_ms=meta.fixed_overhead_ms if meta else None,
        payoff=meta.payoff if meta else "unknown",  # type: ignore[arg-type]
        uncalibrated_skipped_count=uncalibrated,
    )
    return Savings(
        schema_version="1.0.0",
        headline=final_head,
        overall_confidence=overall,
        currency="USD",
        is_calibration_run=cal,
        prices_configured=prices.defaults_used == [],
        prices_used=prices,
        calibration=cal_model,
        wrong_turns=wrong,
        ci_runs_avoided=ci,
        local_compute=local,
        wait=wait,
        tokens=tokens,
        totals=SavingsTotals(
            cost_micro_usd=money(tokens.cost_micro_usd),
            wrong_turns_avoided=flagged,
            ci_runs_avoided=ci.count,
            test_ms_not_executed=local_ms,
            wait_ms_saved=wait.wait_ms_saved,
            tokens_delta=tokens.tokens_delta,
        ),
    )


def ledger_row(
    savings: Savings,
    *,
    run_id: str,
    commit_sha: str | None,
    commit_tree: str | None,
    branch: str | None,
    agent_id: str | None,
    agent_name: str | None,
    outcome: str,
    selected_count: int,
    collected_count: int,
    duration_ms: int,
    mode: str | None = None,
    full_failed: list[str] | None = None,
) -> dict[str, Any]:
    t = savings.totals
    return {
        "run_id": run_id,
        "recorded_at": utc_now_iso(),
        "commit_sha": commit_sha,
        "commit_tree": commit_tree,
        "branch": branch,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "headline": savings.headline,
        "overall_confidence": savings.overall_confidence,
        "prices_configured": int(savings.prices_configured),
        "outcome": outcome,
        "selected_count": selected_count,
        "collected_count": collected_count,
        "duration_ms": duration_ms,
        "wrong_turns_avoided": t.wrong_turns_avoided,
        "ci_runs_avoided": t.ci_runs_avoided,
        "ci_runs_dedupe_key": savings.ci_runs_avoided.dedupe_key,
        "test_ms_not_executed": t.test_ms_not_executed,
        "wait_ms_saved": t.wait_ms_saved,
        "tokens_delta": t.tokens_delta,
        "cost_micro_usd": t.cost_micro_usd,
        "defaults_used": json.dumps(savings.prices_used.defaults_used),
        "savings_json": json.dumps(savings.to_json(), ensure_ascii=False),
        "record_path": None,
        "mode": mode,
        "full_failed": None if full_failed is None else json.dumps(sorted(full_failed)),
    }


# --------------------------------------------------------------------- reports


def _totals(rows: list[dict[str, Any]]) -> SavingsTotals:
    return SavingsTotals(
        cost_micro_usd=sum(r["cost_micro_usd"] for r in rows),
        wrong_turns_avoided=sum(r["wrong_turns_avoided"] for r in rows),
        ci_runs_avoided=sum(r["ci_runs_avoided"] for r in rows),
        test_ms_not_executed=sum(r["test_ms_not_executed"] for r in rows),
        wait_ms_saved=sum(r["wait_ms_saved"] for r in rows),
        tokens_delta=sum(r["tokens_delta"] for r in rows),
    )


def _overall(rows: list[dict[str, Any]]) -> Confidence:
    if not rows:
        return "low"
    return _min_conf(*(r["overall_confidence"] for r in rows))


def _week(iso: str) -> str:
    year, week, _ = _parse_iso(iso).isocalendar()
    return f"{year}-W{week:02d}"


def build_report(
    rows: list[dict[str, Any]],
    *,
    since: datetime,
    until: datetime,
    group_by: Literal["repo", "agent", "branch"],
    weekly: bool,
    repo_name: str,
) -> SavingsReport:
    defaults = sorted({d for r in rows for d in json.loads(r["defaults_used"])})
    configured = bool(rows) and all(r["prices_configured"] for r in rows)
    totals = _totals(rows)

    def key_of(r: dict[str, Any]) -> str:
        if group_by == "agent":
            return str(r["agent_id"] or "unknown")
        if group_by == "branch":
            return str(r["branch"] or "unknown")
        return repo_name

    buckets: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault((key_of(r), _week(r["recorded_at"]) if weekly else None), []).append(r)
    groups = [
        SavingsGroup(
            key=k,
            **({"week": w} if w is not None else {}),
            runs=len(rs),
            totals=_totals(rs),
            overall_confidence=_overall(rs),
        )
        for (k, w), rs in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]
    minutes = totals.test_ms_not_executed // 60_000
    head = (
        f"{len(rows):,} run(s); flaky flagged {totals.wrong_turns_avoided:,}; CI runs avoided "
        f"{totals.ci_runs_avoided:,}; local test-min not run ~{minutes:,}; wait saved "
        f"{human_duration(totals.wait_ms_saved)}"
    )
    if configured and totals.cost_micro_usd > 0:
        head += f"; ~${totals.cost_micro_usd / 1_000_000:,.2f} est."
    return SavingsReport(
        schema_version="1.0.0",
        since=_iso(since),
        until=_iso(until),
        group_by=group_by,
        weekly=weekly,
        runs=len(rows),
        overall_confidence=_overall(rows),
        defaults_used=defaults,
        prices_configured=configured,
        headline=head,
        totals=totals,
        groups=groups,
    )


def report_for(
    cfg: Any, *, since: str | None, until: str | None, by: str, weekly: bool
) -> SavingsReport:
    """The SavingsReport for a window (``since``/``until`` as RFC 3339 or ``Nd``/``Nw``).

    Raises ValueError for unparsable windows or an unknown grouping.
    """
    if by not in ("repo", "agent", "branch"):
        raise ValueError("by must be repo, agent, or branch")
    start = parse_when(since or "30d")
    end = parse_when(until) if until else datetime.now(UTC)
    rows = SavingsLedger(cfg.resolve(cfg.savings.db)).rows(_iso(start), _iso(end))
    return build_report(
        rows,
        since=start,
        until=end,
        group_by=by,  # type: ignore[arg-type]
        weekly=weekly,
        repo_name=cfg.project_name,
    )
