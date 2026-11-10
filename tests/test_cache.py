"""M6: result cache (spec Section 5.9)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from proof_ci.cache import (
    ResultCache,
    conftest_chain,
    deps_hash,
    result_cache_key,
    trim_caches,
)
from proof_ci.cli import app
from proof_ci.config import load_config
from proof_ci.runner import CollectEnv, RunOptions, SelectionPlan, _KeyBuilder, execute_run

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
FAILING = "tests/test_math.py::test_div_wrong_expectation"
cli = CliRunner()


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    (repo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {json.dumps([sys.executable, '-m', 'pytest'])}\n"
        # run --all would otherwise refresh calibration, which never reads the cache.
        "[savings]\nrefresh_calibration_on_all = false\n",
        encoding="utf-8",
    )
    return repo


def run_all(repo: Path, *extra: str) -> dict[str, Any]:
    # Every test, by explicit path: explicit runs read the result cache, `--all` does not.
    result = cli.invoke(app, ["run", "tests", "--json", "--repo", str(repo), *extra])
    data: dict[str, Any] = json.loads(result.stdout)
    # Exit 1 exactly when tests failed or errored, 0 otherwise.
    failing = data["counts"]["failed"] + data["counts"]["error"]
    assert result.exit_code == (1 if failing else 0), result.output
    return data


def results_of(repo: Path, summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = json.loads((repo / summary["results_path"]).read_text(encoding="utf-8"))
    return {r["test_id"]: r for r in rows}


# ------------------------------------------------------------------ key material


ENV = CollectEnv("3.13.11", "Windows", ["pytest==9.2.0"], "pyproject.toml")


def keys_for(repo: Path, env: CollectEnv = ENV) -> dict[str, str]:
    cfg = load_config(repo=repo)
    plan = SelectionPlan(
        mode="all",
        collected_ids=[],
        invoke_id={},
        item_file={},
        selected=[],
        selection=None,  # type: ignore[arg-type]
        changed=[],
        base=None,
        hunks={},
        branch=None,
        collect_entry=None,
        env=env,
    )
    kb = _KeyBuilder(cfg, plan)
    return {
        name: kb.key(f"tests/{name}.py::t", f"tests/{name}.py", env)
        for name in ("test_math", "test_text", "test_data")
    }


def test_key_is_stable(demo: Path) -> None:
    assert keys_for(demo) == keys_for(demo)


def test_key_includes_import_closure(demo: Path) -> None:
    before = keys_for(demo)
    with (demo / "src/demo/mathops.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# edit\n")
    after = keys_for(demo)
    assert after["test_math"] != before["test_math"]
    assert after["test_text"] == before["test_text"]


def test_key_includes_conftest_chain(demo: Path) -> None:
    before = keys_for(demo)
    (demo / "tests/conftest.py").write_text("# fixtures\n", encoding="utf-8")
    after = keys_for(demo)
    assert all(after[k] != before[k] for k in before)
    assert conftest_chain(demo, "tests/test_math.py") == ["tests/conftest.py"]
    (demo / "conftest.py").write_text("", encoding="utf-8")
    assert conftest_chain(demo, "tests/test_math.py") == ["tests/conftest.py", "conftest.py"]


def test_key_includes_pytest_config(demo: Path) -> None:
    before = keys_for(demo)
    with (demo / "pyproject.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n# config edit\n")
    assert keys_for(demo)["test_text"] != before["test_text"]


def test_key_includes_referenced_data_file(demo: Path) -> None:
    before = keys_for(demo)
    (demo / "tests/data/sample.json").write_text('{"name": "demo", "x": 1}\n', encoding="utf-8")
    after = keys_for(demo)
    assert after["test_data"] != before["test_data"]
    assert after["test_text"] == before["test_text"]


def test_key_includes_installed_distributions_and_env(demo: Path) -> None:
    before = keys_for(demo)
    assert (
        keys_for(demo, CollectEnv("3.13.11", "Windows", ["pytest==9.3.0"], "pyproject.toml"))
        != before
    )
    assert (
        keys_for(demo, CollectEnv("3.12.1", "Windows", ["pytest==9.2.0"], "pyproject.toml"))
        != before
    )
    assert (
        keys_for(demo, CollectEnv("3.13.11", "Linux", ["pytest==9.2.0"], "pyproject.toml"))
        != before
    )


def test_deps_hash_includes_lockfiles(demo: Path) -> None:
    h1 = deps_hash(demo, ["a==1"])
    (demo / "uv.lock").write_text("lock\n", encoding="utf-8")
    h2 = deps_hash(demo, ["a==1"])
    assert h1 != h2
    assert deps_hash(demo, ["a==2"]) != h2


def test_result_cache_key_sensitivity() -> None:
    base = result_cache_key("t", {"a.py": "1"}, ("-q",), "3.13", "Linux", "d")
    assert base == result_cache_key("t", {"a.py": "1"}, ("-q",), "3.13", "Linux", "d")
    variants = [
        result_cache_key("u", {"a.py": "1"}, ("-q",), "3.13", "Linux", "d"),
        result_cache_key("t", {"a.py": "2"}, ("-q",), "3.13", "Linux", "d"),
        result_cache_key("t", {"a.py": "1"}, (), "3.13", "Linux", "d"),
        result_cache_key("t", {"a.py": "1"}, ("-q",), "3.12", "Linux", "d"),
        result_cache_key("t", {"a.py": "1"}, ("-q",), "3.13", "Darwin", "d"),
        result_cache_key("t", {"a.py": "1"}, ("-q",), "3.13", "Linux", "e"),
    ]
    assert base not in variants and len(set(variants)) == len(variants)


# ---------------------------------------------------------------- run behaviour


def test_second_run_hits_and_failures_are_never_cached(demo: Path) -> None:
    first = run_all(demo)
    assert first["cache"] == {"hits": 0, "misses": 7, "disabled": False}
    second = run_all(demo)
    assert second["cache"]["hits"] == 6
    assert second["cache"]["misses"] == 1
    rows = results_of(demo, second)
    assert rows["tests/test_math.py::test_add"]["cache_hit"] is True
    assert rows[FAILING]["cache_hit"] is False  # failures always re-execute
    assert second["counts"]["passed"] == 6 and second["counts"]["failed"] == 1
    stored = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (demo / ".proof-ci/cache").rglob("*.json")
    ]
    assert len(stored) == 6
    assert all(s["outcome"] not in ("failed", "error") for s in stored)


def test_run_all_never_replays_the_cache(demo: Path) -> None:
    run_all(demo)  # warm the cache
    result = cli.invoke(app, ["run", "--all", "--json", "--repo", str(demo)])
    data = json.loads(result.stdout)
    assert data["cache"]["hits"] == 0 and data["cache"]["misses"] == 7
    assert not any(r["cache_hit"] for r in results_of(demo, data).values())


def test_result_cache_refuses_failed_and_error(tmp_path: Path) -> None:
    rc = ResultCache(tmp_path)
    for outcome in ("failed", "error"):
        with pytest.raises(ValueError):
            rc.put("ab" * 32, {"outcome": outcome})


def test_no_cache_executes_again(demo: Path) -> None:
    run_all(demo)
    again = run_all(demo, "--no-cache")
    assert again["cache"] == {"hits": 0, "misses": 7, "disabled": True}
    assert not any(r["cache_hit"] for r in results_of(demo, again).values())


def test_cache_disabled_in_config(demo: Path) -> None:
    with (demo / "proof-ci.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n[cache]\nenabled = false\n")
    run_all(demo)
    assert run_all(demo)["cache"]["hits"] == 0
    assert not (demo / ".proof-ci/cache").exists() or not any(
        (demo / ".proof-ci/cache").rglob("*.json")
    )


def test_edit_invalidates_only_dependent_tests(demo: Path) -> None:
    run_all(demo)
    with (demo / "src/demo/textops.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# edit\n")
    rows = results_of(demo, run_all(demo))
    assert rows["tests/test_text.py::test_shout"]["cache_hit"] is False
    assert rows["tests/test_math.py::test_add"]["cache_hit"] is True


GIT = shutil.which("git")


@pytest.mark.skipif(GIT is None, reason="git is required")
def test_fired_fallback_reads_nothing(demo: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.invalid",
    }

    def git(*args: str) -> None:
        subprocess.run([GIT or "git", *args], cwd=demo, env=env, check=True, capture_output=True)

    # Only a green full run may suppress the fallback later: drop the deliberate failure.
    math = demo / "tests/test_math.py"
    math.write_text(
        math.read_text(encoding="utf-8").replace(
            "def test_div_wrong_expectation", "def _skip_div_wrong_expectation"
        ),
        encoding="utf-8",
    )
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    run_all(demo)  # warm the cache
    git("checkout", "-q", "-b", "feat")
    with (demo / "requirements.txt").open("w", encoding="utf-8") as fh:
        fh.write("pytest\n")
    with (demo / "src/demo/mathops.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# same commit\n")
    git("add", "-A")
    git("commit", "-q", "-m", "deps")
    result = cli.invoke(app, ["run", "--json", "--repo", str(demo)])
    summary = json.loads(result.stdout)
    assert summary["selection"]["fallback_full_run"] is True
    assert summary["cache"]["hits"] == 0
    # The suppressed fallback on the next run reads the cache normally.
    result2 = cli.invoke(app, ["run", "--json", "--repo", str(demo)])
    summary2 = json.loads(result2.stdout)
    assert summary2["selection"].get("fallback_suppressed_by_tree")
    assert summary2["selection"]["selected_count"] == 2  # the math tests
    assert summary2["cache"]["hits"] == 2


def test_flaky_blocked_tests_are_not_served(demo: Path) -> None:
    run_all(demo)
    result = cli.invoke(
        app, ["flakes", "mark", "tests/test_math.py::test_add", "--repo", str(demo)]
    )
    assert result.exit_code == 0, result.output
    rows = results_of(demo, run_all(demo))
    assert rows["tests/test_math.py::test_add"]["cache_hit"] is False
    assert rows["tests/test_math.py::test_add"]["flaky"]["status"] == "confirmed"
    assert rows["tests/test_math.py::test_div"]["cache_hit"] is True


def test_environment_mismatch_drops_the_collection_entry(demo: Path) -> None:
    cli.invoke(app, ["run", "--all", "--json", "--repo", str(demo)])  # warms collection cache
    # Tamper the collection-cache entry so it claims another interpreter: the run
    # reports the live env and the entry is dropped.
    entries = list((demo / ".proof-ci/collect-cache").glob("*.json"))
    assert entries
    for entry_path in entries:
        data = json.loads(entry_path.read_text(encoding="utf-8"))
        data["python"] = "0.0.1"
        entry_path.write_text(json.dumps(data), encoding="utf-8")
    cli.invoke(app, ["run", "--all", "--json", "--repo", str(demo)])
    assert not any(
        json.loads(p.read_text(encoding="utf-8"))["python"] == "0.0.1"
        for p in (demo / ".proof-ci/collect-cache").glob("*.json")
    )


def test_stale_entry_rechecks_env_before_serving_all_hits(demo: Path) -> None:
    cfg = load_config(repo=demo)
    execute_run(cfg, RunOptions(mode="explicit", tests=("tests/test_text.py",)))
    run_all(demo)
    for entry_path in (demo / ".proof-ci/collect-cache").glob("*.json"):
        data = json.loads(entry_path.read_text(encoding="utf-8"))
        data["written_at"] = 0
        entry_path.write_text(json.dumps(data), encoding="utf-8")
    # Every selected test is cached and the collection entry is ancient: proof-ci
    # re-collects to refresh the environment, then serves the hits.
    result = cli.invoke(app, ["run", "tests/test_text.py", "--json", "--repo", str(demo)])
    assert json.loads(result.stdout)["cache"]["hits"] == 2


# --------------------------------------------------------------------- LRU trim


def test_lru_trim_across_result_and_collect_caches(tmp_path: Path) -> None:
    results = tmp_path / "cache"
    collect = tmp_path / "collect-cache"
    files = []
    for i in range(6):
        d = results if i % 2 else collect
        p = d / f"{i:02d}" / f"{i:064d}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * 100, encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))
        files.append(p)
    assert trim_caches([results, collect], high=1000, low=500) == 0
    deleted = trim_caches([results, collect], high=500, low=300)
    assert deleted == 3
    assert [p.exists() for p in files] == [False, False, False, True, True, True]


def test_hit_updates_mtime(tmp_path: Path) -> None:
    rc = ResultCache(tmp_path)
    key = "cd" * 32
    valid = {
        "schema_version": "1.0.0",
        "test_id": "tests/t.py::t",
        "outcome": "passed",
        "duration_ms": 1,
        "location": {"file": "tests/t.py", "line": 1},
        "cache_hit": False,
    }
    rc.put(key, valid)
    path = tmp_path / key[:2] / f"{key}.json"
    os.utime(path, (1000, 1000))
    assert rc.get(key) is not None
    assert path.stat().st_mtime > 1000


def test_corrupt_or_failed_entries_are_misses(tmp_path: Path) -> None:
    rc = ResultCache(tmp_path)
    key = "ef" * 32
    path = tmp_path / key[:2] / f"{key}.json"
    path.parent.mkdir(parents=True)
    for content in (
        "not json",
        "[]",
        '{"outcome": "passed"}',
        json.dumps({"outcome": "failed", "test_id": "t"}),
    ):
        path.write_text(content, encoding="utf-8")
        assert rc.get(key) is None
