"""Flake store and detection (spec Section 5.11).

SQLite via the standard library. Connections are opened per operation and
closed before returning (spec Section 5.20 rule 8), so the MCP server and a
concurrent CLI do not hold the file open across each other's writes.

Only freshly executed results are recorded. Cache replays are never inserted:
a replayed pass cannot pair with a fresh fail, and inserting replays would
freeze detection (Section 5.9).

Two deliberate readings of the transition table, both conservative:

* Resolution and re-flagging of a resolved test look at the test's *latest*
  ``code_hash`` group. Read literally ("not a candidate anywhere in the
  window"), a test whose flaky group is still inside the 30-day window could
  never resolve after its code changed, and a resolved test would be flagged
  again on its very next run.
* :meth:`FlakeSnapshot.allowed_failure` only excuses a failure when the
  current ``code_hash`` equals the record's ``last_code_hash`` (or the status
  was set by hand), so a "known flaky" label cannot mask a real regression in
  code the agent just edited.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import rfc8785

from proof_ci.models import FlakeInfo, FlakeRecord, FlakeWindow
from proof_ci.util import debug, utc_now_iso

__all__ = [
    "FAIL_LIKE",
    "PASS_LIKE",
    "RECORDED_OUTCOMES",
    "FlakeSnapshot",
    "FlakeStore",
    "code_hash",
]

PASS_LIKE = frozenset({"passed", "xpassed"})
FAIL_LIKE = frozenset({"failed", "xfailed"})
RECORDED_OUTCOMES = PASS_LIKE | FAIL_LIKE

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_outcomes (
    id INTEGER PRIMARY KEY,
    test_id TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    outcome TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    run_id TEXT NOT NULL,
    commit_sha TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_outcomes_test_code
    ON test_outcomes (test_id, code_hash, started_at);

CREATE TABLE IF NOT EXISTS flake_records (
    test_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_code_hash TEXT NOT NULL,
    pass_count INTEGER NOT NULL,
    fail_count INTEGER NOT NULL,
    flip_count INTEGER NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0
);
"""


def code_hash(test_id: str, input_hashes: dict[str, str]) -> str:
    """SHA-256 of RFC 8785 ``{test_id, input_hashes}`` (no interpreter fields)."""
    doc: dict[str, Any] = {"test_id": test_id, "input_hashes": dict(sorted(input_hashes.items()))}
    return hashlib.sha256(rfc8785.dumps(doc)).hexdigest()


@dataclass(frozen=True)
class _Group:
    code_hash: str
    pass_count: int
    fail_count: int
    flip_count: int
    last_used: str
    recent: tuple[str, ...]
    """Categories ("p"/"f") in time order."""

    @property
    def candidate_counts(self) -> tuple[int, int]:
        return self.pass_count, self.fail_count


@dataclass(frozen=True)
class _Row:
    status: str
    first_seen: str
    last_seen: str
    last_code_hash: str
    pass_count: int
    fail_count: int
    flip_count: int
    reason: str | None
    updated_at: str
    manual: bool


@dataclass(frozen=True)
class FlakeSnapshot:
    """Statuses loaded once per run."""

    rows: dict[str, _Row]

    def status(self, test_id: str) -> str:
        row = self.rows.get(test_id)
        return row.status if row else "none"

    def blocks_cache(self, test_id: str) -> bool:
        return self.status(test_id) in ("suspected", "confirmed")

    def info(self, test_id: str) -> FlakeInfo:
        row = self.rows.get(test_id)
        if row is None:
            return FlakeInfo(is_flaky=False, status="none")
        return FlakeInfo(
            is_flaky=row.status in ("suspected", "confirmed", "ignored"),
            status=row.status,  # type: ignore[arg-type]
            flip_count=row.flip_count,
            reason=row.reason or None,
        )

    def allowed_failure(self, test_id: str, current_code_hash: str | None) -> bool:
        """``--allow-known-flakes``: confirmed/ignored only, and only on unchanged code.

        Manual marks are pinned to a code hash too (at mark time, or on the test's next
        run), so marking a test ``ignored`` cannot excuse a later regression.
        """
        row = self.rows.get(test_id)
        if row is None or row.status not in ("confirmed", "ignored"):
            return False
        return bool(row.last_code_hash) and current_code_hash == row.last_code_hash


class FlakeStore:
    def __init__(
        self,
        path: Path,
        *,
        min_runs: int = 3,
        window_days: int = 30,
        min_flips_confirmed: int = 2,
    ) -> None:
        self.path = path
        self.min_runs = min_runs
        self.window_days = window_days
        self.min_flips_confirmed = min_flips_confirmed

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
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                (utc_now_iso(),),
            )
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- reading

    @staticmethod
    def _row(r: tuple[Any, ...]) -> _Row:
        return _Row(
            status=r[0],
            first_seen=r[1],
            last_seen=r[2],
            last_code_hash=r[3],
            pass_count=r[4],
            fail_count=r[5],
            flip_count=r[6],
            reason=r[7],
            updated_at=r[8],
            manual=bool(r[9]),
        )

    _COLS = (
        "status, first_seen, last_seen, last_code_hash, pass_count, fail_count, "
        "flip_count, reason, updated_at, manual"
    )

    def snapshot(self) -> FlakeSnapshot:
        if not self.path.exists():
            return FlakeSnapshot({})
        with self._connect() as conn:
            rows = conn.execute(f"SELECT test_id, {self._COLS} FROM flake_records").fetchall()
        return FlakeSnapshot({r[0]: self._row(r[1:]) for r in rows})

    def _window_runs(self, conn: sqlite3.Connection, test_id: str) -> int:
        since = self._window_start()
        row = conn.execute(
            "SELECT COUNT(*) FROM test_outcomes WHERE test_id = ? AND started_at >= ?",
            (test_id, since),
        ).fetchone()
        return int(row[0]) if row else 0

    def _to_model(self, conn: sqlite3.Connection, test_id: str, row: _Row) -> FlakeRecord:
        return FlakeRecord(
            schema_version="1.0.0",
            test_id=test_id,
            status=row.status,  # type: ignore[arg-type]
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            last_code_hash=row.last_code_hash,
            pass_count=row.pass_count,
            fail_count=row.fail_count,
            flip_count=row.flip_count,
            window=FlakeWindow(days=self.window_days, runs=self._window_runs(conn, test_id)),
            reason=row.reason or None,
            updated_at=row.updated_at,
        )

    def get(self, test_id: str) -> FlakeRecord | None:
        if not self.path.exists():
            return None
        with self._connect() as conn:
            r = conn.execute(
                f"SELECT {self._COLS} FROM flake_records WHERE test_id = ?", (test_id,)
            ).fetchone()
            return self._to_model(conn, test_id, self._row(r)) if r else None

    def list_records(self, statuses: tuple[str, ...]) -> list[FlakeRecord]:
        if not self.path.exists():
            return []
        marks = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT test_id, {self._COLS} FROM flake_records WHERE status IN ({marks}) "
                "ORDER BY test_id",
                statuses,
            ).fetchall()
            return [self._to_model(conn, r[0], self._row(r[1:])) for r in rows]

    # ---------------------------------------------------------------- writing

    def _window_start(self) -> str:
        start = datetime.now(UTC) - timedelta(days=self.window_days)
        return start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def record(
        self,
        *,
        test_id: str,
        code_hash: str,
        outcome: str,
        started_at: str,
        duration_ms: int | None,
        run_id: str,
        commit_sha: str | None,
        error_message: str | None,
    ) -> None:
        """Insert one freshly executed outcome and recompute that test's record."""
        if outcome not in RECORDED_OUTCOMES:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO test_outcomes (test_id, code_hash, outcome, started_at, duration_ms, "
                "run_id, commit_sha, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    test_id,
                    code_hash,
                    outcome,
                    started_at,
                    duration_ms,
                    run_id,
                    commit_sha,
                    error_message,
                ),
            )
            self._recompute(conn, test_id, code_hash)

    def _groups(self, conn: sqlite3.Connection, test_id: str) -> list[_Group]:
        rows = conn.execute(
            "SELECT code_hash, outcome, started_at FROM test_outcomes "
            "WHERE test_id = ? AND started_at >= ? ORDER BY started_at, id",
            (test_id, self._window_start()),
        ).fetchall()
        by_hash: dict[str, list[tuple[str, str]]] = {}
        for h, outcome, started in rows:
            if outcome in PASS_LIKE:
                by_hash.setdefault(h, []).append(("p", started))
            elif outcome in FAIL_LIKE:
                by_hash.setdefault(h, []).append(("f", started))
        groups = []
        for h, seq in by_hash.items():
            cats = tuple(c for c, _ in seq)
            flips = sum(1 for a, b in pairwise(cats) if a != b)
            groups.append(
                _Group(
                    code_hash=h,
                    pass_count=cats.count("p"),
                    fail_count=cats.count("f"),
                    flip_count=flips,
                    last_used=seq[-1][1],
                    recent=cats,
                )
            )
        return groups

    def _is_candidate(self, g: _Group) -> bool:
        return (
            g.pass_count >= 1 and g.fail_count >= 1 and g.pass_count + g.fail_count >= self.min_runs
        )

    def _recompute(self, conn: sqlite3.Connection, test_id: str, latest_hash: str) -> None:
        r = conn.execute(
            f"SELECT {self._COLS} FROM flake_records WHERE test_id = ?", (test_id,)
        ).fetchone()
        current = self._row(r) if r else None
        if current is not None and (current.manual or current.status == "ignored"):
            if not current.last_code_hash:
                # Pin a manual mark made before the test ever ran to the code it next runs on.
                conn.execute(
                    "UPDATE flake_records SET last_code_hash = ? WHERE test_id = ?",
                    (latest_hash, test_id),
                )
            return
        groups = self._groups(conn, test_id)
        if not groups:
            return
        best = max(groups, key=lambda g: (g.flip_count, g.last_used))
        latest = next((g for g in groups if g.code_hash == latest_hash), best)
        any_candidate = any(self._is_candidate(g) for g in groups)
        latest_candidate = self._is_candidate(latest)
        flagged = "confirmed" if best.flip_count >= self.min_flips_confirmed else "suspected"
        tail = latest.recent[-self.min_runs :]
        stable_latest = len(tail) >= self.min_runs and len(set(tail)) == 1

        if current is None:
            if not any_candidate:
                return
            status = flagged
        elif current.status == "suspected":
            if any_candidate and best.flip_count >= self.min_flips_confirmed:
                status = "confirmed"
            elif not latest_candidate and stable_latest:
                status = "resolved"
            else:
                status = "suspected"
        elif current.status == "confirmed":
            status = "resolved" if (not latest_candidate and stable_latest) else "confirmed"
        elif current.status == "resolved":
            if latest_candidate:
                status = (
                    "confirmed" if latest.flip_count >= self.min_flips_confirmed else "suspected"
                )
            else:
                status = "resolved"
        else:
            return

        # Stored counts come from the group with the most flips, except when the
        # decision was made on the latest group (resolving, or re-flagging after
        # a resolution).
        resolving_path = status == "resolved" or (
            current is not None and current.status == "resolved"
        )
        stats = latest if resolving_path else best
        now = utc_now_iso()
        reason = (
            f"passed {stats.pass_count} and failed {stats.fail_count} times on unchanged code hash "
            f"{stats.code_hash[:8]}… ({stats.flip_count} flips)"
        )
        if status == "resolved":
            reason = f"last {self.min_runs} runs on code hash {stats.code_hash[:8]}… were stable"
        conn.execute(
            "INSERT INTO flake_records (test_id, status, first_seen, last_seen, last_code_hash, "
            "pass_count, fail_count, flip_count, reason, updated_at, manual) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0) "
            "ON CONFLICT(test_id) DO UPDATE SET status=excluded.status, last_seen=excluded.last_seen, "
            "last_code_hash=excluded.last_code_hash, pass_count=excluded.pass_count, "
            "fail_count=excluded.fail_count, flip_count=excluded.flip_count, reason=excluded.reason, "
            "updated_at=excluded.updated_at",
            (
                test_id,
                status,
                current.first_seen if current else now,
                now,
                stats.code_hash,
                stats.pass_count,
                stats.fail_count,
                stats.flip_count,
                reason,
                now,
            ),
        )

    def mark(self, test_id: str, status: str, reason: str | None) -> FlakeRecord:
        if status not in ("confirmed", "ignored"):
            raise ValueError("status must be confirmed or ignored")
        now = utc_now_iso()
        with self._connect() as conn:
            latest = conn.execute(
                "SELECT code_hash FROM test_outcomes WHERE test_id = ? "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (test_id,),
            ).fetchone()
            pinned = str(latest[0]) if latest else ""
            conn.execute(
                "INSERT INTO flake_records (test_id, status, first_seen, last_seen, last_code_hash, "
                "pass_count, fail_count, flip_count, reason, updated_at, manual) "
                "VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, 1) "
                "ON CONFLICT(test_id) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
                "updated_at=excluded.updated_at, last_code_hash=excluded.last_code_hash, manual=1",
                (test_id, status, now, now, pinned, reason or f"manually marked {status}", now),
            )
            r = conn.execute(
                f"SELECT {self._COLS} FROM flake_records WHERE test_id = ?", (test_id,)
            ).fetchone()
            return self._to_model(conn, test_id, self._row(r))

    def unmark(self, test_id: str, reason: str | None = None) -> FlakeRecord | None:
        if not self.path.exists():
            return None
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE flake_records SET status='resolved', manual=0, reason=?, updated_at=? "
                "WHERE test_id = ?",
                (reason or "manually unmarked", utc_now_iso(), test_id),
            )
            if cur.rowcount == 0:
                return None
            r = conn.execute(
                f"SELECT {self._COLS} FROM flake_records WHERE test_id = ?", (test_id,)
            ).fetchone()
            return self._to_model(conn, test_id, self._row(r))
