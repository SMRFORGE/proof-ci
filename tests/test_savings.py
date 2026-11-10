"""M9: savings estimation, calibration, ledger, and ``proof-ci savings`` (spec Section 5.12)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from typer.testing import CliRunner

from proof_ci import models as m
from proof_ci.cli import app
from proof_ci.config import SavingsConfig
from proof_ci.models import FlakeInfo, Location, TestResult
from proof_ci.savings import (
    RunFacts,
    SavingsLedger,
    compute_savings,
    human_duration,
    parse_when,
    payoff,
    to_micro,
    token_count,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
GIT = shutil.which("git")
cli = CliRunner()

ALL_PRICES = frozenset(
    {
        "ci_cost_per_minute",
        "model_input_cost_per_million_tokens",
        "model_output_cost_per_million_tokens",
        "wasted_fix_cost",
    }
)


def result(
    test_id: str, outcome: str = "passed", flaky: str | None = None, ms: int = 10
) -> TestResult:
    data: dict[str, Any] = {
        "schema_version": "1.0.0",
        "test_id": test_id,
        "outcome": outcome,
        "duration_ms": ms,
        "location": Location(file="tests/t.py", line=1).to_json(),
        "cache_hit": False,
    }
    if outcome in ("failed", "error"):
        data["failure"] = {
            "error_type": "AssertionError",
            "error_message": "x",
            "repro_command": "pytest -q t",
        }
    if flaky:
        data["flaky"] = FlakeInfo(is_flaky=flaky != "resolved", status=flaky).to_json()  # type: ignore[arg-type]
    return TestResult.model_validate(data)


def facts(**overrides: Any) -> RunFacts:
    base: dict[str, Any] = {
        "is_calibration_run": False,
        "results": [result("t::a"), result("t::b")],
        "selected_count": 2,
        "collected_count": 6,
        "deselected_ids": ["t::c", "t::d", "t::e", "t::f"],
        "cache_hit_ids": [],
        "outcome": "passed",
        "failed_count": 0,
        "flaky_failures": 0,
        "actual_ms": 1_000,
        "pytest_stdout": "..  [100%]\n2 passed in 0.01s\n",
        "pytest_flags": "-q --tb=short",
        "branch": "feat",
        "commit_tree": "a" * 40,
    }
    base.update(overrides)
    return RunFacts(**base)


def structured(n: int = 1_600) -> Any:
    return lambda _headline: n


@pytest.fixture
def ledger(tmp_path: Path) -> SavingsLedger:
    return SavingsLedger(tmp_path / "savings.sqlite")


def calibrate(
    ledger: SavingsLedger, durations: dict[str, int], wall: int = 60_000, overhead: int = 2_000
) -> None:
    ledger.write_calibration(
        wall_ms=wall,
        fixed_overhead_ms=overhead,
        durations=durations,
        commit_sha=None,
        python="3.13",
        platform="X",
        run_id="cal",
    )


def walk_no_floats(value: Any) -> None:
    assert not isinstance(value, float), value
    if isinstance(value, dict):
        for v in value.values():
            walk_no_floats(v)
    elif isinstance(value, list):
        for v in value:
            walk_no_floats(v)


# ------------------------------------------------------------------- helpers


def test_to_micro_rounds_half_up() -> None:
    assert to_micro(0.01) == 10_000
    assert to_micro(3.0) == 3_000_000
    assert to_micro(0.0000005) == 1  # half away from zero, not banker's rounding
    assert to_micro(0.0000025) == 3


def test_token_count_is_stable() -> None:
    assert token_count(612) == 153
    assert token_count(0) == 0
    assert token_count(7) == 1


def test_human_duration() -> None:
    assert human_duration(22_000) == "22s"
    assert human_duration(64_000) == "1m 04s"
    assert human_duration(3_725_000) == "1h 02m"


def test_payoff_thresholds() -> None:
    assert payoff(None, None) == "unknown"
    assert payoff(5_999, 2_000) == "overhead_dominates"
    assert payoff(6_000, 2_000) == "marginal"
    assert payoff(19_999, 2_000) == "marginal"
    assert payoff(20_000, 2_000) == "selection_saves_time"


def test_parse_when() -> None:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    assert parse_when("30d", now) == now - timedelta(days=30)
    assert parse_when("2w", now) == now - timedelta(weeks=2)
    assert parse_when("2026-11-01T00:00:00Z", now) == datetime(2026, 11, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_when("yesterday", now)


# ---------------------------------------------------------------- estimation


def test_uncalibrated_run_is_low_confidence(ledger: SavingsLedger) -> None:
    s = compute_savings(facts(), SavingsConfig(), ledger, structured_bytes_for=structured())
    assert s.calibration.status == "missing"
    assert s.calibration.uncalibrated_skipped_count == 4
    assert s.local_compute.test_ms_not_executed == 0
    assert (s.local_compute.status, s.local_compute.confidence) == ("estimated", "low")
    assert s.overall_confidence == "low"
    assert s.headline.endswith("(uncalibrated)")
    assert s.wait.baseline_kind == "none" and s.wait.status == "unavailable"


def test_calibrated_deselected_durations(ledger: SavingsLedger) -> None:
    calibrate(ledger, {"t::c": 1_000, "t::d": 2_000, "t::e": 3_000, "t::f": 4_000})
    s = compute_savings(facts(), SavingsConfig(), ledger, structured_bytes_for=structured())
    assert s.local_compute.test_ms_not_executed == 10_000
    assert (s.local_compute.status, s.local_compute.confidence) == ("measured", "high")
    assert s.local_compute.priced is False and s.local_compute.cost_micro_usd == 0
    assert "not CI time" in s.local_compute.rationale


def test_partial_calibration_is_estimated(ledger: SavingsLedger) -> None:
    calibrate(ledger, {"t::c": 1_000})
    s = compute_savings(facts(), SavingsConfig(), ledger, structured_bytes_for=structured())
    assert s.local_compute.test_ms_not_executed == 1_000
    assert s.calibration.uncalibrated_skipped_count == 3
    assert (s.local_compute.status, s.local_compute.confidence) == ("estimated", "medium")


def test_cache_hits_count_as_not_executed(ledger: SavingsLedger) -> None:
    calibrate(ledger, {"t::a": 500})
    s = compute_savings(
        facts(deselected_ids=[], cache_hit_ids=["t::a", "t::b"]),
        SavingsConfig(),
        ledger,
        structured_bytes_for=structured(),
    )
    # t::a from calibration, t::b falls back to the cached result's duration (10 ms).
    assert s.local_compute.test_ms_not_executed == 510


def test_local_minutes_priced_only_when_enabled(ledger: SavingsLedger) -> None:
    calibrate(ledger, {"t::c": 120_000, "t::d": 0, "t::e": 0, "t::f": 0})
    cfg = SavingsConfig(price_local_minutes=True, local_compute_cost_per_minute=0.5)
    s = compute_savings(facts(), cfg, ledger, structured_bytes_for=structured())
    assert s.local_compute.priced is True
    assert s.local_compute.cost_micro_usd == 1_000_000  # 2 min * $0.50


def test_tokens_measured_from_stdout_and_negative_on_small_green_run(ledger: SavingsLedger) -> None:
    stdout = "..  [100%]\n2 passed in 0.01s\n"
    s = compute_savings(
        facts(pytest_stdout=stdout), SavingsConfig(), ledger, structured_bytes_for=structured()
    )
    assert s.tokens.raw_bytes == len(stdout.encode("utf-8"))
    assert s.tokens.raw_source == "pytest_stdout"
    assert s.tokens.tokens_delta < 0
    assert s.tokens.cost_micro_usd == 0
    assert "tokens" not in s.headline


def test_wrong_turns_counts_confirmed_and_ignored_only(ledger: SavingsLedger) -> None:
    results = [
        result("t::a", "failed", "confirmed"),
        result("t::b", "failed", "ignored"),
        result("t::c", "failed", "suspected"),
        result("t::d", "failed"),
    ]
    s = compute_savings(
        facts(results=results, outcome="failed", failed_count=4, flaky_failures=3),
        SavingsConfig(),
        ledger,
        structured_bytes_for=structured(),
    )
    assert s.wrong_turns.flagged_known_flake_failures == 2
    assert s.wrong_turns.cost_micro_usd == 2 * 25_000_000


def test_ci_runs_avoided_once_per_dedupe_key(ledger: SavingsLedger) -> None:
    from proof_ci.savings import ledger_row

    failing = facts(results=[result("t::a", "failed")], outcome="failed", failed_count=1)
    first = compute_savings(failing, SavingsConfig(), ledger, structured_bytes_for=structured())
    assert (first.ci_runs_avoided.count, first.ci_runs_avoided.deduplicated) == (1, False)
    assert first.ci_runs_avoided.dedupe_key
    assert (first.ci_runs_avoided.status, first.ci_runs_avoided.confidence) == (
        "estimated",
        "medium",
    )
    ledger.append(
        ledger_row(
            first,
            run_id="r1",
            commit_sha=None,
            commit_tree="a" * 40,
            branch="feat",
            agent_id="x",
            agent_name=None,
            outcome="failed",
            selected_count=1,
            collected_count=1,
            duration_ms=1,
        )
    )
    second = compute_savings(failing, SavingsConfig(), ledger, structured_bytes_for=structured())
    assert (second.ci_runs_avoided.count, second.ci_runs_avoided.deduplicated) == (0, True)
    other_tree = compute_savings(
        facts(
            results=[result("t::a", "failed")],
            outcome="failed",
            failed_count=1,
            commit_tree="b" * 40,
        ),
        SavingsConfig(),
        ledger,
        structured_bytes_for=structured(),
    )
    assert other_tree.ci_runs_avoided.count == 1


def test_passing_run_avoids_no_ci_run(ledger: SavingsLedger) -> None:
    s = compute_savings(facts(), SavingsConfig(), ledger, structured_bytes_for=structured())
    assert (s.ci_runs_avoided.count, s.ci_runs_avoided.dedupe_key) == (0, None)
    assert (s.ci_runs_avoided.status, s.ci_runs_avoided.confidence) == ("measured", "high")


def test_wait_full_suite_local_is_at_most_medium(ledger: SavingsLedger) -> None:
    calibrate(ledger, {}, wall=30_000)
    s = compute_savings(
        facts(actual_ms=5_000), SavingsConfig(), ledger, structured_bytes_for=structured()
    )
    assert s.wait.baseline_kind == "full_suite_local"
    assert s.wait.wait_ms_saved == 25_000
    assert s.wait.confidence == "medium"
    assert "full suite locally" in s.wait.rationale


def test_wait_user_pipeline_baseline(ledger: SavingsLedger) -> None:
    cfg = SavingsConfig(full_pipeline_baseline_ms=600_000)
    s = compute_savings(facts(), cfg, ledger, structured_bytes_for=structured())
    assert (s.wait.baseline_kind, s.wait.confidence, s.wait.wait_ms_saved) == (
        "full_pipeline_user",
        "high",
        599_000,
    )


def test_stale_calibration(ledger: SavingsLedger) -> None:
    calibrate(ledger, {"t::c": 1_000, "t::d": 1, "t::e": 1, "t::f": 1}, wall=30_000)
    cfg = SavingsConfig(calibration_max_age_days=0)
    s = compute_savings(facts(), cfg, ledger, structured_bytes_for=structured())
    assert s.calibration.status == "stale"
    assert s.wait.confidence == "low"
    assert s.overall_confidence in ("low", "medium")
    assert "(stale calibration)" in s.headline


def test_defaults_used_and_money_in_headline(ledger: SavingsLedger) -> None:
    failing = facts(
        results=[result("t::a", "failed", "confirmed")],
        outcome="failed",
        failed_count=1,
        flaky_failures=1,
    )
    fresh = compute_savings(failing, SavingsConfig(), ledger, structured_bytes_for=structured())
    assert fresh.prices_used.defaults_used == sorted(ALL_PRICES)
    assert fresh.prices_configured is False
    assert "$" not in fresh.headline
    configured = SavingsConfig(present_keys=ALL_PRICES)
    priced = compute_savings(failing, configured, ledger, structured_bytes_for=structured())
    assert priced.prices_configured is True
    assert priced.headline.endswith("; ~$25.00 est.")


def test_calibration_run_claims_nothing_against_itself(ledger: SavingsLedger) -> None:
    calibrate(ledger, {"t::a": 10, "t::b": 10}, wall=15_000)
    s = compute_savings(
        facts(is_calibration_run=True, deselected_ids=[]),
        SavingsConfig(),
        ledger,
        structured_bytes_for=structured(),
    )
    assert s.is_calibration_run is True
    assert s.local_compute.test_ms_not_executed == 0
    assert s.wait.wait_ms_saved == 0
    assert s.calibration.payoff == "marginal"  # 15 s suite, 2 s overhead


def test_savings_object_has_no_floats_and_validates(ledger: SavingsLedger) -> None:
    calibrate(ledger, {"t::c": 1_000})
    s = compute_savings(
        facts(), SavingsConfig(present_keys=ALL_PRICES), ledger, structured_bytes_for=structured()
    )
    doc = s.to_json()
    walk_no_floats(doc)
    jsonschema.Draft202012Validator(m.load_schema("savings")).validate(doc)


# --------------------------------------------------------------- integration


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    if GIT is None:
        pytest.skip("git is required")
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    (repo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {json.dumps([sys.executable, '-m', 'pytest'])}\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.invalid",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run([GIT or "git", *args], cwd=repo, env=env, check=True, capture_output=True)
    return repo


def invoke_json(*args: str) -> tuple[int, dict[str, Any]]:
    r = cli.invoke(app, [*args, "--json"])
    return r.exit_code, json.loads(r.stdout)


def report_validator() -> jsonschema.Draft202012Validator:
    from referencing import Registry, Resource

    schemas = [m.load_schema(n) for n in m.SCHEMA_FILES]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    return jsonschema.Draft202012Validator(m.load_schema("savings_report"), registry=registry)


def test_run_writes_headline_and_ledger_row(demo: Path) -> None:
    code, summary = invoke_json("run", "tests/test_text.py", "--repo", str(demo))
    assert code == 0
    assert summary["savings_headline"] == summary["savings"]["headline"]
    assert "$" not in summary["savings_headline"]  # fresh install: time, not money
    assert summary["savings"]["tokens"]["raw_bytes"] == len(
        (demo / Path(summary["results_path"]).parent / "pytest-stdout.txt").read_bytes()
    )
    ledger = SavingsLedger(demo / ".proof-ci" / "savings.sqlite")
    rows = ledger.rows("2000-01-01T00:00:00.000Z", "2100-01-01T00:00:00.000Z")
    assert [r["run_id"] for r in rows] == [summary["run_id"]]
    assert rows[0]["commit_tree"] == summary["commit_tree"]


def test_calibrate_cli(demo: Path) -> None:
    r = cli.invoke(app, ["savings", "calibrate", "--json", "--repo", str(demo)])
    assert r.exit_code == 1  # the demo has a deliberate failure; durations are still recorded
    savings = json.loads(r.stdout)
    assert savings["is_calibration_run"] is True
    assert savings["calibration"]["status"] == "current"
    assert savings["calibration"]["payoff"] in (
        "overhead_dominates",
        "marginal",
        "selection_saves_time",
    )
    assert savings["calibration"]["test_count"] == 7
    human = cli.invoke(app, ["savings", "calibrate", "--repo", str(demo)])
    assert "Full suite" in human.stdout and "fixed overhead" in human.stdout


def test_savings_report_cli(demo: Path) -> None:
    code, empty = invoke_json("savings", "--repo", str(demo))
    assert code == 0 and empty["runs"] == 0
    for _ in range(2):
        cli.invoke(app, ["run", "tests/test_text.py", "--repo", str(demo)])
    code, report = invoke_json("savings", "--repo", str(demo))
    assert code == 0
    assert not list(report_validator().iter_errors(report))
    assert report["runs"] == 2 and report["group_by"] == "repo"
    assert report["prices_configured"] is False and "$" not in report["headline"]
    code, weekly = invoke_json("savings", "--by", "branch", "--weekly", "--repo", str(demo))
    assert not list(report_validator().iter_errors(weekly))
    assert [g["key"] for g in weekly["groups"]] == ["main"]
    assert weekly["groups"][0]["week"].startswith("20")
    code, agent = invoke_json("savings", "--by", "agent", "--repo", str(demo))
    assert [g["key"] for g in agent["groups"]] == ["local"]
    code, future = invoke_json("savings", "--since", "2099-01-01T00:00:00Z", "--repo", str(demo))
    assert future["runs"] == 0
    assert cli.invoke(app, ["savings", "--since", "yesterday", "--repo", str(demo)]).exit_code == 2
    assert cli.invoke(app, ["savings", "--by", "planet", "--repo", str(demo)]).exit_code == 2


def test_tampered_payload_savings_fails_verify(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from proof_ci.record import generate_keypair

    key = tmp_path / "k" / "ed25519.pem"
    generate_keypair(key)
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(key))
    code, summary = invoke_json("run", "tests/test_text.py", "--sign", "--repo", str(demo))
    assert code == 0
    record_path = demo / summary["record_path"]
    doc = json.loads(record_path.read_text(encoding="utf-8"))
    assert doc["payload"]["savings"]["headline"] == summary["savings_headline"]
    doc["payload"]["savings"]["totals"]["ci_runs_avoided"] = 50
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    r = cli.invoke(app, ["record", "verify", str(bad), "--json"])
    assert r.exit_code == 4


def test_ledger_anchors_on_full_fresh_runs_with_their_failures(demo: Path) -> None:
    ledger = SavingsLedger(demo / ".proof-ci" / "savings.sqlite")
    cli.invoke(app, ["run", "tests/test_text.py", "--repo", str(demo)])  # explicit
    assert ledger.last_full("main") is None
    cli.invoke(app, ["run", "--all", "--repo", str(demo)])  # full, but one test fails
    red = ledger.last_full("main")
    assert red is not None
    assert red[1] == ["tests/test_math.py::test_div_wrong_expectation"]
    math = demo / "tests/test_math.py"
    math.write_text(
        math.read_text(encoding="utf-8").replace(
            "def test_div_wrong_expectation", "def _skip_div_wrong_expectation"
        ),
        encoding="utf-8",
    )
    cli.invoke(app, ["run", "--all", "--repo", str(demo)])  # full and green
    green = ledger.last_full("main")
    assert green is not None
    assert green[1] == []
    assert green[0] != red[0]
