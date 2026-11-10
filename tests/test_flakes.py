"""M7: flake store, detection algorithm, and ``proof-ci flakes`` (spec Section 5.11)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from proof_ci.cli import app
from proof_ci.flakes import FlakeStore, code_hash

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
FLAKY = "tests/test_flaky.py::test_sometimes"
cli = CliRunner()

T = "tests/test_x.py::test_y"
A = "a" * 64
B = "b" * 64


class Clock:
    """Strictly increasing timestamps inside the detection window."""

    def __init__(self, start_days_ago: float = 1.0) -> None:
        self.t = datetime.now(UTC) - timedelta(days=start_days_ago)

    def __call__(self) -> str:
        self.t += timedelta(seconds=1)
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@pytest.fixture
def store(tmp_path: Path) -> FlakeStore:
    return FlakeStore(tmp_path / "flakes.sqlite", min_runs=3, window_days=30, min_flips_confirmed=2)


def feed(
    store: FlakeStore, outcomes: str, h: str = A, clock: Clock | None = None, test: str = T
) -> None:
    clock = clock or Clock()
    names = {
        "p": "passed",
        "f": "failed",
        "s": "skipped",
        "e": "error",
        "X": "xpassed",
        "x": "xfailed",
    }
    for ch in outcomes:
        store.record(
            test_id=test,
            code_hash=h,
            outcome=names[ch],
            started_at=clock(),
            duration_ms=1,
            run_id="r",
            commit_sha=None,
            error_message=None,
        )


def status(store: FlakeStore, test: str = T) -> str:
    record = store.get(test)
    return record.status if record else "none"


# ----------------------------------------------------------- transition table


def test_two_opposite_results_do_not_flag(store: FlakeStore) -> None:
    feed(store, "pf")
    assert status(store) == "none"


def test_three_mixed_with_one_flip_is_suspected(store: FlakeStore) -> None:
    feed(store, "ppf")
    record = store.get(T)
    assert record is not None
    assert (record.status, record.pass_count, record.fail_count, record.flip_count) == (
        "suspected",
        2,
        1,
        1,
    )
    assert record.last_code_hash == A
    assert "passed 2 and failed 1 times" in (record.reason or "")


def test_enough_flips_on_first_detection_is_confirmed(store: FlakeStore) -> None:
    feed(store, "pfp")
    assert status(store) == "confirmed"


def test_suspected_becomes_confirmed(store: FlakeStore) -> None:
    clock = Clock()
    feed(store, "ppf", clock=clock)
    assert status(store) == "suspected"
    feed(store, "p", clock=clock)
    assert status(store) == "confirmed"


def test_resolved_after_stable_streak_on_new_hash(store: FlakeStore) -> None:
    clock = Clock()
    feed(store, "pfp", A, clock)
    assert status(store) == "confirmed"
    feed(store, "pp", B, clock)
    assert status(store) == "confirmed"  # streak not long enough yet
    feed(store, "p", B, clock)
    record = store.get(T)
    assert record is not None and record.status == "resolved"
    assert record.last_code_hash == B


def test_suspected_resolves_when_latest_hash_is_stable(store: FlakeStore) -> None:
    clock = Clock()
    feed(store, "ppf", A, clock)
    feed(store, "fff", B, clock)
    assert status(store) == "resolved"  # consistently failing is a real failure, not a flake


def test_resolved_becomes_suspected_again(store: FlakeStore) -> None:
    clock = Clock()
    feed(store, "pfp", A, clock)
    feed(store, "ppp", B, clock)
    assert status(store) == "resolved"
    feed(store, "f", B, clock)
    assert status(store) == "suspected"


def test_resolved_stays_resolved_on_stable_runs(store: FlakeStore) -> None:
    clock = Clock()
    feed(store, "pfp", A, clock)
    feed(store, "pppppp", B, clock)
    assert status(store) == "resolved"


def test_ignored_is_never_auto_changed(store: FlakeStore) -> None:
    store.mark(T, "ignored", "known infra noise")
    feed(store, "pfpfp")
    record = store.get(T)
    assert record is not None and record.status == "ignored"
    assert record.reason == "known infra noise"


def test_manual_mark_and_unmark(store: FlakeStore) -> None:
    clock = Clock()
    store.mark(T, "confirmed", None)
    feed(store, "ppp", clock=clock)
    assert status(store) == "confirmed"  # manual: not auto-resolved
    record = store.unmark(T)
    assert record is not None
    assert (record.status, record.reason) == ("resolved", "manually unmarked")
    feed(store, "f", clock=clock)  # manual cleared: automatic transitions resume
    assert status(store) == "suspected"


def test_mark_rejects_other_statuses(store: FlakeStore) -> None:
    with pytest.raises(ValueError):
        store.mark(T, "suspected", None)


def test_skipped_and_error_are_not_recorded(store: FlakeStore) -> None:
    feed(store, "pses")
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute("SELECT outcome FROM test_outcomes").fetchall()
    assert [r[0] for r in rows] == ["passed"]


def test_xpassed_and_xfailed_count_as_pass_and_fail(store: FlakeStore) -> None:
    feed(store, "XxX")
    record = store.get(T)
    assert record is not None and record.status == "confirmed"
    with sqlite3.connect(store.path) as conn:
        assert {r[0] for r in conn.execute("SELECT outcome FROM test_outcomes")} == {
            "xpassed",
            "xfailed",
        }


def test_different_code_hashes_are_not_flips(store: FlakeStore) -> None:
    clock = Clock()
    feed(store, "pp", A, clock)
    feed(store, "f", B, clock)
    feed(store, "p", A, clock)
    assert status(store) == "none"


def test_rows_outside_window_are_ignored(store: FlakeStore) -> None:
    feed(store, "pfp", clock=Clock(start_days_ago=60))
    assert status(store) == "none"


def test_code_hash_ignores_dict_order() -> None:
    assert code_hash(T, {"a.py": "1", "b.py": "2"}) == code_hash(T, {"b.py": "2", "a.py": "1"})
    assert code_hash(T, {"a.py": "1"}) != code_hash(T, {"a.py": "2"})


def test_snapshot_semantics(store: FlakeStore) -> None:
    feed(store, "pfp")
    snap = store.snapshot()
    info = snap.info(T)
    assert (info.is_flaky, info.status, info.flip_count) == (True, "confirmed", 2)
    assert snap.blocks_cache(T)
    assert snap.allowed_failure(T, A)
    assert not snap.allowed_failure(T, B)  # code changed: no excuse
    assert snap.info("other").status == "none"
    assert not snap.blocks_cache("other")


# -------------------------------------------------------------- integration


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    (repo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {json.dumps([sys.executable, '-m', 'pytest'])}\n",
        encoding="utf-8",
    )
    return repo


def flaky_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch, fail: bool, *extra: str
) -> tuple[int, dict[str, Any]]:
    monkeypatch.setenv("PROOF_CI_DEMO_FLAKE", "1" if fail else "0")
    result = cli.invoke(app, ["run", FLAKY, "--no-cache", "--json", "--repo", str(repo), *extra])
    return result.exit_code, json.loads(result.stdout)


def flake_list(repo: Path, *extra: str) -> list[dict[str, Any]]:
    result = cli.invoke(app, ["flakes", "list", "--json", "--repo", str(repo), *extra])
    assert result.exit_code == 0, result.output
    flakes: list[dict[str, Any]] = json.loads(result.stdout)["flakes"]
    return flakes


def test_demo_flaky_becomes_suspected_then_confirmed(
    demo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for fail in (False, False, True):
        flaky_run(demo, monkeypatch, fail)
    (record,) = flake_list(demo)
    assert (record["test_id"], record["status"]) == (FLAKY, "suspected")

    code, summary = flaky_run(demo, monkeypatch, False)
    assert code == 0
    (record,) = flake_list(demo)
    assert record["status"] == "confirmed"
    results = json.loads((demo / summary["results_path"]).read_text(encoding="utf-8"))
    assert results[0]["flaky"]["is_flaky"] is True
    assert results[0]["flaky"]["status"] == "confirmed"

    # --allow-known-flakes: a confirmed flake on unchanged code does not fail the run.
    code, summary = flaky_run(demo, monkeypatch, True)
    assert code == 1
    assert summary["failures"][0]["is_flaky"] is True
    code, summary = flaky_run(demo, monkeypatch, True, "--allow-known-flakes")
    assert code == 0
    assert summary["outcome"] == "passed"
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["flaky_failures"] == 1

    # After the test's code changes, the label no longer excuses a failure.
    with (demo / "tests/test_flaky.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# edited\n")
    code, _ = flaky_run(demo, monkeypatch, True, "--allow-known-flakes")
    assert code == 1


def test_suspected_flake_is_not_allowed(demo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for fail in (False, False, True):
        flaky_run(demo, monkeypatch, fail)
    assert flake_list(demo)[0]["status"] == "suspected"
    code, _ = flaky_run(demo, monkeypatch, True, "--allow-known-flakes")
    assert code == 1


def test_cache_replays_do_not_insert_outcomes(demo: Path) -> None:
    for _ in range(2):
        cli.invoke(app, ["run", "tests/test_text.py", "--json", "--repo", str(demo)])
    with sqlite3.connect(demo / ".proof-ci" / "flakes.sqlite") as conn:
        count = conn.execute("SELECT COUNT(*) FROM test_outcomes").fetchone()[0]
    assert count == 2  # two tests executed once; the second run was all cache hits


def test_flakes_cli_show_mark_unmark(demo: Path) -> None:
    show = cli.invoke(app, ["flakes", "show", "tests/test_math.py::test_add", "--repo", str(demo)])
    assert show.exit_code == 2
    assert cli.invoke(app, ["flakes", "unmark", "nope", "--repo", str(demo)]).exit_code == 2
    mark = cli.invoke(
        app,
        [
            "flakes",
            "mark",
            FLAKY,
            "--status",
            "ignored",
            "--reason",
            "infra",
            "--json",
            "--repo",
            str(demo),
        ],
    )
    assert mark.exit_code == 0, mark.output
    assert json.loads(mark.stdout)["status"] == "ignored"
    assert [r["status"] for r in flake_list(demo, "--status", "all")] == ["ignored"]
    assert flake_list(demo) == []  # default lists suspected + confirmed only
    bad = cli.invoke(app, ["flakes", "mark", FLAKY, "--status", "suspected", "--repo", str(demo)])
    assert bad.exit_code == 2
    unmark = cli.invoke(app, ["flakes", "unmark", FLAKY, "--json", "--repo", str(demo)])
    assert json.loads(unmark.stdout)["status"] == "resolved"
    assert [r["test_id"] for r in flake_list(demo, "--status", "resolved")] == [FLAKY]
    show = cli.invoke(app, ["flakes", "show", FLAKY, "--json", "--repo", str(demo)])
    assert json.loads(show.stdout)["reason"] == "manually unmarked"
