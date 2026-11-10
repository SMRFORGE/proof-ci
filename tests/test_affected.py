"""M5: affected-test selection (spec Section 5.8) and its wiring into run/affected."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from proof_ci.affected import (
    FallbackDecision,
    Hunk,
    ImportGraph,
    changed_files,
    decide_fallback,
    glob_match,
    likely_cause,
    resolve_base,
    select,
)
from proof_ci.cache import collect_cache_key
from proof_ci.cli import app
from proof_ci.util import tree_hash

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
runner = CliRunner()

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    proc = subprocess.run(
        [GIT or "git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def init_repo(repo: Path, branch: str = "main") -> None:
    git(repo, "init", "-q", "-b", branch)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")


# --------------------------------------------------------------- glob helper


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("conftest.py", "conftest.py", True),
        ("tests/conftest.py", "conftest.py", False),
        ("requirements-dev.txt", "requirements*.txt", True),
        ("docs/requirements.txt", "requirements*.txt", False),
        (".github/workflows/ci.yml", ".github/workflows/**", True),
        (".github/workflows/sub/x.yml", ".github/workflows/**", True),
        (".github/other.yml", ".github/workflows/**", False),
        ("a/b/c.lock", "**/*.lock", True),
        ("c.lock", "**/*.lock", True),
        ("pyproject.toml", "pyproject.toml", True),
        ("sub/pyproject.toml", "pyproject.toml", False),
    ],
)
def test_glob_match(path: str, pattern: str, expected: bool) -> None:
    assert glob_match(path, pattern) is expected


# ------------------------------------------------------- import graph + select


@pytest.fixture
def mini(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    write(repo, "src/pkg/__init__.py", "")
    write(repo, "src/pkg/a.py", "VALUE = 1\n")
    write(repo, "src/pkg/b.py", "from . import a\n\ndef f():\n    return a.VALUE\n")
    write(repo, "src/pkg/c.py", "def g():\n    return 2\n")
    write(repo, "src/pkg/sub/__init__.py", "")
    write(repo, "src/pkg/sub/d.py", "from ..a import VALUE\n")
    write(repo, "src/pkg/broken.py", "def oops(:\n")
    write(repo, "src/pkg/dyn.py", "import importlib\nm = importlib.import_module('pkg.c')\n")
    write(repo, "tests/test_b.py", "from pkg.b import f\n\ndef test_f():\n    assert f() == 1\n")
    write(repo, "tests/test_c.py", "import pkg.c\n\ndef test_g():\n    assert pkg.c.g() == 2\n")
    write(repo, "tests/test_d.py", "from pkg.sub import d\n\ndef test_d():\n    assert d.VALUE\n")
    write(repo, "tests/test_broken.py", "import pkg.broken\n")
    write(repo, "tests/test_data.py", "P = 'data/input.csv'\n\ndef test_x():\n    pass\n")
    write(repo, "tests/sub/conftest.py", "")
    write(repo, "tests/sub/test_s.py", "def test_s():\n    pass\n")
    write(repo, "data/input.csv", "a,b\n")
    write(repo, "README.md", "hi\n")
    return repo


COLLECTED = [
    ("tests/test_b.py::test_f", "tests/test_b.py"),
    ("tests/test_c.py::test_g", "tests/test_c.py"),
    ("tests/test_d.py::test_d", "tests/test_d.py"),
    ("tests/test_broken.py::test_nothing", "tests/test_broken.py"),
    ("tests/test_data.py::test_x", "tests/test_data.py"),
    ("tests/sub/test_s.py::test_s", "tests/sub/test_s.py"),
]
NO_FALLBACK = FallbackDecision(False, None)


def pick(repo: Path, changed: list[str], fallback: FallbackDecision = NO_FALLBACK) -> set[str]:
    graph = ImportGraph.build(repo, ["src"], ["tests"], changed)
    return set(select(repo, changed, COLLECTED, graph, fallback).selected)


def test_graph_relative_imports_and_syntax_error(mini: Path) -> None:
    graph = ImportGraph.build(mini, ["src"], ["tests"])
    assert "src/pkg/a.py" in graph.deps["src/pkg/b.py"]
    assert "src/pkg/a.py" in graph.deps["src/pkg/sub/d.py"]
    assert "src/pkg/broken.py" in graph.opaque
    assert graph.deps.get("src/pkg/broken.py", set()) == set()
    assert "src/pkg/dyn.py" in graph.dynamic


def test_source_change_selects_importing_tests_transitively(mini: Path) -> None:
    assert pick(mini, ["src/pkg/a.py"]) == {"tests/test_b.py::test_f", "tests/test_d.py::test_d"}


def test_unrelated_change_selects_nothing(mini: Path) -> None:
    write(mini, "src/other/x.py", "")
    assert pick(mini, ["src/other/x.py"]) == set()


def test_syntax_error_module_still_affects_its_importers(mini: Path) -> None:
    assert pick(mini, ["src/pkg/broken.py"]) == {"tests/test_broken.py::test_nothing"}


def test_changed_test_file_selects_itself(mini: Path) -> None:
    assert pick(mini, ["tests/test_c.py"]) == {"tests/test_c.py::test_g"}


def test_dynamic_import_is_conservative(mini: Path) -> None:
    # pkg.dyn imports pkg.c dynamically; a change to c also selects files depending on dyn.
    write(mini, "tests/test_dyn.py", "import pkg.dyn\n")
    collected = [*COLLECTED, ("tests/test_dyn.py::t", "tests/test_dyn.py")]
    graph = ImportGraph.build(mini, ["src"], ["tests"], ["src/pkg/c.py"])
    chosen = set(select(mini, ["src/pkg/c.py"], collected, graph, NO_FALLBACK).selected)
    assert "tests/test_dyn.py::t" in chosen
    assert "tests/test_c.py::test_g" in chosen


def test_deleted_module_selects_its_importers(mini: Path) -> None:
    (mini / "src/pkg/c.py").unlink()
    assert pick(mini, ["src/pkg/c.py"]) == {"tests/test_c.py::test_g"}


def test_nested_conftest_selects_subtree(mini: Path) -> None:
    assert pick(mini, ["tests/sub/conftest.py"]) == {"tests/sub/test_s.py::test_s"}


def test_readme_selects_nothing(mini: Path) -> None:
    assert pick(mini, ["README.md"]) == set()


def test_markdown_under_tests_is_not_skipped(mini: Path) -> None:
    write(mini, "tests/notes.md", "x")
    write(mini, "tests/test_c.py", "import pkg.c\n# reads notes.md\n")
    assert "tests/test_c.py::test_g" in pick(mini, ["tests/notes.md"])


def test_referenced_data_file_selects_that_test_file(mini: Path) -> None:
    assert pick(mini, ["data/input.csv"]) == {"tests/test_data.py::test_x"}


def test_unreferenced_data_file_selects_nothing(mini: Path) -> None:
    write(mini, "data/other.bin", "x")
    assert pick(mini, ["data/other.bin"]) == set()


def test_fired_fallback_selects_everything(mini: Path) -> None:
    fired = FallbackDecision(True, "full run because pyproject.toml matched")
    assert pick(mini, ["pyproject.toml"], fired) == {t for t, _ in COLLECTED}


def test_fallback_globs_root_conftest_and_pyproject(mini: Path) -> None:
    globs = ["pyproject.toml", "conftest.py"]
    for changed in (["pyproject.toml"], ["conftest.py"]):
        assert decide_fallback(mini, changed, globs, tree_now=None, tree_last=None).fired
    nested = decide_fallback(mini, ["tests/sub/conftest.py"], globs, tree_now=None, tree_last=None)
    assert not nested.fired


# -------------------------------------------------------------- base resolution


@pytest.fixture
def gitrepo(mini: Path) -> Path:
    write(mini, ".gitignore", "__pycache__/\n.proof-ci/\n")
    init_repo(mini)
    return mini


def test_auto_on_clean_feature_branch_uses_merge_base(gitrepo: Path) -> None:
    root = git(gitrepo, "rev-parse", "HEAD")
    git(gitrepo, "checkout", "-q", "-b", "feat")
    write(gitrepo, "src/pkg/a.py", "VALUE = 2\n")
    git(gitrepo, "commit", "-q", "-am", "change a")
    write(gitrepo, "src/pkg/c.py", "def g():\n    return 3\n")
    git(gitrepo, "commit", "-q", "-am", "change c")
    res = resolve_base(gitrepo, "auto", dirty=False)
    assert (res.base_setting, res.base_ref, res.default_branch) == ("auto", root, "main")
    assert changed_files(gitrepo, res.base_ref) == ["src/pkg/a.py", "src/pkg/c.py"]


def test_auto_on_clean_default_branch_uses_parent(gitrepo: Path) -> None:
    first = git(gitrepo, "rev-parse", "HEAD")
    write(gitrepo, "src/pkg/a.py", "VALUE = 2\n")
    git(gitrepo, "commit", "-q", "-am", "change a")
    res = resolve_base(gitrepo, "auto", dirty=False)
    assert res.base_ref == first
    assert changed_files(gitrepo, res.base_ref) == ["src/pkg/a.py"]


def test_auto_on_dirty_default_branch_uses_head(gitrepo: Path) -> None:
    write(gitrepo, "src/pkg/a.py", "VALUE = 2\n")
    git(gitrepo, "commit", "-q", "-am", "change a")
    head = git(gitrepo, "rev-parse", "HEAD")
    write(gitrepo, "src/pkg/c.py", "def g():\n    return 9\n")
    res = resolve_base(gitrepo, "auto", dirty=True)
    assert res.base_ref == head
    assert changed_files(gitrepo, res.base_ref) == ["src/pkg/c.py"]


def test_explicit_head_and_ref(gitrepo: Path) -> None:
    head = git(gitrepo, "rev-parse", "HEAD")
    res = resolve_base(gitrepo, "HEAD", dirty=False)
    assert (res.base_setting, res.base_ref) == ("HEAD", head)
    git(gitrepo, "tag", "v1")
    assert resolve_base(gitrepo, "v1", dirty=False).base_ref == head


def test_untracked_files_are_changed_and_proof_ci_is_not(gitrepo: Path) -> None:
    write(gitrepo, "src/pkg/new.py", "")
    write(gitrepo, ".proof-ci/runs/x.json", "{}")
    write(gitrepo, "src/pkg/__pycache__/a.cpython-313.pyc", "x")
    head = git(gitrepo, "rev-parse", "HEAD")
    assert changed_files(gitrepo, head) == ["src/pkg/new.py"]


# ----------------------------------------------------------- incremental fallback


def test_incremental_fallback(gitrepo: Path) -> None:
    globs = ["pyproject.toml"]
    git(gitrepo, "checkout", "-q", "-b", "feat")
    write(gitrepo, "pyproject.toml", "[project]\nname='x'\n")
    git(gitrepo, "add", "-A")
    git(gitrepo, "commit", "-q", "-m", "bump deps")
    base = resolve_base(gitrepo, "auto", dirty=False).base_ref

    # First run on the branch: no previous tree -> fire.
    t1 = tree_hash(gitrepo)
    changed = changed_files(gitrepo, base)
    first = decide_fallback(gitrepo, changed, globs, tree_now=t1, tree_last=None)
    assert first.fired

    # Second run after a source-only edit: suppressed, records the last tree.
    write(gitrepo, "src/pkg/a.py", "VALUE = 5\n")
    t2 = tree_hash(gitrepo)
    second = decide_fallback(
        gitrepo, changed_files(gitrepo, base), globs, tree_now=t2, tree_last=t1
    )
    assert not second.fired
    assert second.suppressed_by_tree == t1
    assert second.reason is not None and second.reason.startswith("suppressed:")

    # Editing pyproject.toml again fires again.
    write(gitrepo, "pyproject.toml", "[project]\nname='y'\n")
    t3 = tree_hash(gitrepo)
    third = decide_fallback(gitrepo, changed_files(gitrepo, base), globs, tree_now=t3, tree_last=t2)
    assert third.fired


# ------------------------------------------------------------------ likely cause


def test_likely_cause_points_at_changed_frame() -> None:
    hunks = {"src/demo/mathops.py": [Hunk(5, 5, "@@ -5 +5 @@")]}
    tb = "tests/test_math.py:5: in test_add\n    assert add(2, 3) == 5\nsrc/demo/mathops.py:5: in add\n"
    cause = likely_cause(tb, hunks, "a" * 40)
    assert cause is not None
    assert (cause["file"], cause["line"]) == ("src/demo/mathops.py", 5)
    assert likely_cause("src/demo/mathops.py:9: in div", hunks, "a" * 40) is None


# ---------------------------------------------------------- demo integration


def _toml_list(items: list[str]) -> str:
    return json.dumps(items)


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    (repo / "proof-ci.toml").write_text(
        f'[project]\nname = "demo"\ntest_command = {_toml_list([sys.executable, "-m", "pytest"])}\n',
        encoding="utf-8",
    )
    init_repo(repo)
    return repo


def run_json(*args: str) -> tuple[int, dict[str, Any], str]:
    result = runner.invoke(app, list(args))
    data = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result.exit_code, data, result.stderr


def edit_mathops(repo: Path) -> None:
    with (repo / "src/demo/mathops.py").open("a", encoding="utf-8") as fh:
        fh.write("\n\ndef mul(a, b):\n    return a * b\n")


MATH = {
    "tests/test_math.py::test_add",
    "tests/test_math.py::test_div",
    "tests/test_math.py::test_div_wrong_expectation",
}


def test_mathops_edit_selects_math_not_text(demo: Path) -> None:
    edit_mathops(demo)
    code, data, _ = run_json("affected", "--json", "--repo", str(demo))
    assert code == 0
    assert set(data["selected_tests"]) == MATH
    assert data["changed_files"] == ["src/demo/mathops.py"]
    assert data["base_setting"] == "auto"
    code, summary, _ = run_json("run", "--json", "--repo", str(demo))
    assert code == 1  # the deliberate failure is in the math tests
    sel = summary["selection"]
    assert sel["mode"] == "affected"
    assert (sel["selected_count"], sel["collected_count"]) == (3, 7)
    assert sel["base_ref"] == git(demo, "rev-parse", "HEAD")
    assert sel["fallback_full_run"] is False


def test_committed_change_on_clean_feature_branch_is_selected(demo: Path) -> None:
    git(demo, "checkout", "-q", "-b", "feat")
    edit_mathops(demo)
    git(demo, "commit", "-q", "-am", "mul")
    code, data, _ = run_json("affected", "--json", "--repo", str(demo))
    assert code == 0
    assert set(data["selected_tests"]) == MATH
    assert data["base_ref"] == git(demo, "rev-parse", "main")


def test_empty_selection_exits_0_and_explains(demo: Path) -> None:
    code, summary, stderr = run_json("run", "--json", "--repo", str(demo))
    assert code == 0
    assert summary["selection"]["selected_count"] == 0
    assert summary["summary"] == "0 selected, 0 failed (nothing affected)"
    assert "nothing affected" in stderr and "clean" in stderr


def test_sign_refuses_empty_selection(demo: Path) -> None:
    result = runner.invoke(app, ["run", "--sign", "--json", "--repo", str(demo)])
    assert result.exit_code == 2
    assert "cannot sign a run that selected no tests" in result.stderr
    summary = json.loads(result.stdout)
    assert summary["record_path"] is None
    assert not list((demo / ".proof-ci" / "runs").glob("*/record.json"))


def test_affected_requires_git(tmp_path: Path) -> None:
    repo = tmp_path / "nogit"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    assert runner.invoke(app, ["affected", "--repo", str(repo)]).exit_code == 2


def test_likely_cause_end_to_end(demo: Path) -> None:
    path = demo / "src/demo/mathops.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace("return a + b", "raise ValueError('changed')"),
        encoding="utf-8",
    )
    code, summary, _ = run_json("run", "--json", "--repo", str(demo))
    assert code == 1
    results = json.loads((demo / summary["results_path"]).read_text(encoding="utf-8"))
    add = next(r for r in results if r["test_id"] == "tests/test_math.py::test_add")
    cause = add["failure"]["likely_cause"]
    assert cause["file"] == "src/demo/mathops.py"
    assert cause["line"] == 5


def _results(repo: Path, summary: dict) -> list[dict]:
    return json.loads((repo / summary["results_path"]).read_text(encoding="utf-8"))


def _fix_failing_demo_test(repo: Path) -> None:
    path = repo / "tests/test_math.py"
    text = path.read_text(encoding="utf-8")
    fixed = text.replace("def test_div_wrong_expectation", "def _not_a_test_div_wrong_expectation")
    assert fixed != text
    path.write_text(fixed, encoding="utf-8")
    git(repo, "commit", "-q", "-am", "green")


def test_failing_full_run_anchors_but_its_failures_always_rerun(demo: Path) -> None:
    git(demo, "checkout", "-q", "-b", "feat")
    with (demo / "pyproject.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n# bump\n")
    git(demo, "commit", "-q", "-am", "bump")
    _, first, _ = run_json("run", "--json", "--repo", str(demo))
    assert first["selection"]["fallback_full_run"] is True and first["outcome"] == "failed"
    failing = "tests/test_math.py::test_div_wrong_expectation"
    # A textops edit does not select test_math.py by itself; the anchor's failure still
    # reruns, so the run stays red instead of quietly reporting less (M13 C4).
    text = demo / "src/demo/textops.py"
    text.write_text(text.read_text(encoding="utf-8") + "\n# touch\n", encoding="utf-8")
    _, second, _ = run_json("run", "--json", "--repo", str(demo))
    assert second["selection"]["fallback_full_run"] is False
    assert second["selection"]["fallback_suppressed_by_tree"]
    ids = {r["test_id"]: r for r in _results(demo, second)}
    assert ids[failing]["outcome"] == "failed"
    assert ids[failing]["cache_hit"] is False
    assert second["outcome"] == "failed"
    assert second["selection"]["selected_count"] < first["selection"]["selected_count"]


def test_file_reverted_after_the_anchor_is_still_selected(demo: Path) -> None:
    _fix_failing_demo_test(demo)
    git(demo, "checkout", "-q", "-b", "feat")
    edit_mathops(demo)
    git(demo, "commit", "-q", "-am", "edit")
    run_json("run", "--all", "--json", "--repo", str(demo))  # anchor: edited mathops
    # mathops now matches main again, so a diff against main misses it; the anchor ran
    # the other version, so its tests must run again.
    git(demo, "checkout", "-q", "main", "--", "src/demo/mathops.py")
    _, run, _ = run_json("run", "--json", "--repo", str(demo))
    assert any(r["test_id"].startswith("tests/test_math.py::") for r in _results(demo, run))


def test_run_records_suppressed_fallback(demo: Path) -> None:
    _fix_failing_demo_test(demo)
    git(demo, "checkout", "-q", "-b", "feat")
    with (demo / "pyproject.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n# bump\n")
    git(demo, "commit", "-q", "-am", "bump")
    _, first, _ = run_json("run", "--json", "--repo", str(demo))
    assert first["selection"]["fallback_full_run"] is True
    assert first["selection"]["selected_count"] == 6
    assert first["outcome"] == "passed"
    edit_mathops(demo)
    _, second, _ = run_json("run", "--json", "--repo", str(demo))
    sel = second["selection"]
    assert sel["fallback_full_run"] is False
    assert sel["fallback_suppressed_by_tree"] == first["commit_tree"]
    assert set(sel) >= {"fallback_reason"} and sel["fallback_reason"].startswith("suppressed:")
    assert sel["selected_count"] == 2


# ---------------------------------------------------------------- collect cache


FAKE = textwrap.dedent(
    """
    import json, os, sys
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")
    if sys.argv[1:] == ["--version"]:
        sys.exit(0)
    if "PROOF_CI_COLLECT_FILE" in os.environ:
        with open(os.environ["PROOF_CI_COLLECT_FILE"], "w", encoding="utf-8") as fh:
            json.dump({"python": "3", "platform": "X", "inipath": None, "installed": [],
                       "items": [{"test_id": "tests/test_math.py::test_add",
                                  "file": "tests/test_math.py", "line": 4}]}, fh)
    sys.exit(0)
    """
)


def test_collection_cache_skips_collect_and_invalidates(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dir = tmp_path / "fakepkg"
    fake_dir.mkdir()
    (fake_dir / "pytest.py").write_text(FAKE, encoding="utf-8")
    log = tmp_path / "log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("PYTHONPATH", str(fake_dir))
    (demo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {_toml_list([sys.executable, '-m', 'pytest'])}\n",
        encoding="utf-8",
    )

    def collects() -> int:
        if not log.exists():
            return 0
        return sum(
            "--collect-only" in line for line in log.read_text(encoding="utf-8").splitlines()
        )

    assert runner.invoke(app, ["affected", "--repo", str(demo)]).exit_code == 0
    assert collects() == 1
    assert runner.invoke(app, ["affected", "--repo", str(demo)]).exit_code == 0
    assert collects() == 1  # served from the collection cache

    with (demo / "tests/test_text.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# touched\n")
    runner.invoke(app, ["affected", "--repo", str(demo)])
    assert collects() == 2  # a test file change invalidates

    (demo / "tests/conftest.py").write_text("# new conftest\n", encoding="utf-8")
    runner.invoke(app, ["affected", "--repo", str(demo)])
    assert collects() == 3  # a conftest change invalidates

    runner.invoke(app, ["run", "--all", "--no-cache", "--repo", str(demo)])
    assert collects() == 4  # --no-cache bypasses it


def test_collect_cache_key_sensitivity(demo: Path) -> None:
    args: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] = (("tests",), (), ("pytest",))
    k1 = collect_cache_key(demo, *args)
    assert collect_cache_key(demo, *args) == k1
    assert collect_cache_key(demo, ("tests",), ("-q",), ("pytest",)) != k1
    assert collect_cache_key(demo, ("tests",), (), ("python", "-m", "pytest")) != k1
    with (demo / "pyproject.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n# changed\n")
    assert collect_cache_key(demo, *args) != k1


def test_collect_cache_key_follows_helpers_and_data_not_docs(demo: Path) -> None:
    # Helper modules and data files drive parametrization (M13): they must invalidate
    # the cached collection. Docs and pytest's own cache must not.
    args: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] = (("tests",), (), ("pytest",))

    def key() -> str:
        return collect_cache_key(demo, *args, tree_hash(demo))

    k1 = key()
    (demo / ".pytest_cache").mkdir(exist_ok=True)
    (demo / ".pytest_cache" / "lastfailed").write_text("{}", encoding="utf-8")
    _append(demo, "README.md", "\nmore docs\n")
    assert key() == k1
    _append(demo, "tests/data/sample.json", " ")
    k2 = key()
    assert k2 != k1
    _append(demo, "src/demo/textops.py", "\n# helper edit\n")
    assert key() != k2


# ------------------------------------------------------------ case canonical


def test_case_insensitive_invocation_uses_committed_case(demo: Path) -> None:
    probe = demo / "TESTS"
    if not probe.exists():
        pytest.skip("case-sensitive filesystem")
    code, summary, _ = run_json(
        "run", "Tests/Test_Math.py::test_add", "--json", "--repo", str(demo)
    )
    assert code == 0, summary
    results = json.loads((demo / summary["results_path"]).read_text(encoding="utf-8"))
    assert [r["test_id"] for r in results] == ["tests/test_math.py::test_add"]
    assert results[0]["location"]["file"] == "tests/test_math.py"


# ------------------------------------------------ untraced changes (M13 C1/C3)


def _commit_files(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixtures")


def _append(repo: Path, rel: str, text: str) -> None:
    with (repo / rel).open("a", encoding="utf-8") as fh:
        fh.write(text)


def _affected(repo: Path) -> dict[str, Any]:
    code, data, _ = run_json("affected", "--json", "--repo", str(repo))
    assert code == 0, data
    return data


def test_module_no_test_imports_forces_a_full_run(demo: Path) -> None:
    # Like a pytest plugin loaded through an entry point: no test imports it.
    _commit_files(demo, {"src/demo/plugin.py": "def pytest_configure(config):\n    pass\n"})
    _append(demo, "src/demo/plugin.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is True
    assert "src/demo/plugin.py is not imported by any collected test" in data["fallback_reason"]
    assert len(data["selected_tests"]) == 7


TEXT = {
    "tests/test_text.py::test_shout",
    "tests/test_text.py::test_words",
}

LAUNCHER = (
    "import subprocess, sys\n\n\n"
    "def test_serve():\n"
    "    subprocess.run([sys.executable, 'tools/serve.py'], check=True)\n"
)


def test_module_reached_through_a_launched_script_selects_the_launcher(demo: Path) -> None:
    # tools/serve.py imports demo.textops and a test runs it as a subprocess, so a
    # textops edit selects that test (uncached) on top of textops's importers.
    _commit_files(
        demo,
        {
            "tools/serve.py": "from demo import textops\nprint(textops)\n",
            "tests/test_serve.py": LAUNCHER,
        },
    )
    _append(demo, "src/demo/textops.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert set(data["selected_tests"]) == TEXT | {"tests/test_serve.py::test_serve"}


def test_module_reached_only_through_an_unlaunched_script_selects_narrowly(demo: Path) -> None:
    # Nothing tests reach names tools/bench.py, so tests cannot run it: a benchmark or
    # release script importing the package does not widen the selection.
    _commit_files(demo, {"tools/bench.py": "from demo import textops\nprint(textops)\n"})
    _append(demo, "src/demo/textops.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert set(data["selected_tests"]) == TEXT


def test_edited_launched_script_selects_its_launcher_only(demo: Path) -> None:
    _commit_files(
        demo,
        {"tools/serve.py": "print('serve')\n", "tests/test_serve.py": LAUNCHER},
    )
    _append(demo, "tools/serve.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert data["selected_tests"] == ["tests/test_serve.py::test_serve"]


def test_module_named_in_pytest_config_forces_a_full_run(demo: Path) -> None:
    _commit_files(demo, {"src/demo/plug.py": "def pytest_configure(config):\n    pass\n"})
    _append(demo, "pyproject.toml", '\n[project.entry-points.pytest11]\ndemo = "demo.plug"\n')
    git(demo, "commit", "-q", "-am", "register plugin")
    _append(demo, "src/demo/plug.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is True
    assert "registered as a pytest plugin" in data["fallback_reason"]


def test_main_module_run_with_dash_m_selects_its_launcher(demo: Path) -> None:
    _commit_files(
        demo,
        {
            "src/demo/__main__.py": "from demo import textops\nprint(textops)\n",
            "tests/test_cli.py": (
                "import subprocess, sys\n\n\ndef test_cli():\n"
                "    subprocess.run([sys.executable, '-m', 'demo'], check=True)\n"
            ),
        },
    )
    _append(demo, "src/demo/textops.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert set(data["selected_tests"]) == TEXT | {"tests/test_cli.py::test_cli"}


def test_broken_test_file_selects_only_itself(demo: Path) -> None:
    _fix_failing_demo_test(demo)
    run_json("run", "--all", "--json", "--repo", str(demo))
    _append(demo, "tests/test_text.py", "\nimport no_such_module_m13\n")
    code, summary, _ = run_json("run", "--json", "--repo", str(demo))
    assert code == 1
    assert summary["selection"]["fallback_full_run"] is False
    ids = [r["test_id"] for r in _results(demo, summary)]
    assert ids == ["tests/test_text.py"]


def test_traced_module_edit_still_selects_narrowly(demo: Path) -> None:
    _commit_files(demo, {"tools/serve.py": "print('no imports from the package')\n"})
    edit_mathops(demo)
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert set(data["selected_tests"]) == MATH


def test_package_data_forces_a_full_run(demo: Path) -> None:
    _commit_files(demo, {"src/demo/table.json": "{}\n"})
    (demo / "src/demo/table.json").write_text('{"a": 1}\n', encoding="utf-8")
    data = _affected(demo)
    assert data["fallback_full_run"] is True
    assert "package data" in data["fallback_reason"]


def test_data_named_by_non_test_code_forces_a_full_run(demo: Path) -> None:
    _commit_files(
        demo,
        {
            "tools/settings.json": "{}\n",
            "tools/load.py": "import json\nCFG = json.load(open('tools/settings.json'))\n",
        },
    )
    (demo / "tools/settings.json").write_text('{"a": 1}\n', encoding="utf-8")
    data = _affected(demo)
    assert data["fallback_full_run"] is True
    assert "named by non-test code tools/load.py" in data["fallback_reason"]


def test_root_doc_named_by_a_test_is_selected(demo: Path) -> None:
    _commit_files(
        demo,
        {
            "tests/test_readme.py": (
                "from pathlib import Path\n\n\ndef test_readme():\n"
                "    assert Path('README.md').read_text(encoding='utf-8')\n"
            )
        },
    )
    _append(demo, "README.md", "\nmore\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert data["selected_tests"] == ["tests/test_readme.py::test_readme"]


def test_unreferenced_doc_selects_nothing(demo: Path) -> None:
    _commit_files(demo, {"docs/notes.md": "notes\n"})
    _append(demo, "docs/notes.md", "\nmore\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert data["selected_tests"] == []


def test_import_through_sys_path_is_traced(demo: Path) -> None:
    # A test puts helpers/ on sys.path and imports helpmod by its bare name.
    _commit_files(
        demo,
        {
            "helpers/helpmod.py": "VALUE = 1\n",
            "tests/test_helper.py": (
                "import sys\nfrom pathlib import Path\n\n"
                "sys.path.insert(0, str(Path(__file__).parents[1] / 'helpers'))\n"
                "import helpmod  # noqa: E402\n\n\n"
                "def test_value():\n    assert helpmod.VALUE == 1\n"
            ),
        },
    )
    _append(demo, "helpers/helpmod.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert data["selected_tests"] == ["tests/test_helper.py::test_value"]


# ------------------------------------------- collection errors, crashed runs (M13 E2/C6)


def test_collection_error_is_a_structured_error_result(demo: Path) -> None:
    (demo / "tests/test_broken.py").write_text(
        "import does_not_exist_m13\n\n\ndef test_never():\n    pass\n", encoding="utf-8"
    )
    code, summary, _ = run_json("run", "--all", "--json", "--repo", str(demo))
    assert code == 1
    assert summary["outcome"] == "failed"  # the deliberate failure; the error counts too
    assert summary["counts"]["error"] == 1
    assert summary["counts"]["passed"] == 6
    broken = next(f for f in summary["failures"] if f["test_id"] == "tests/test_broken.py")
    assert broken["error_type"] in ("ModuleNotFoundError", "ImportError")
    assert "does_not_exist_m13" in broken["error_message"]
    # --failed reruns the file once it collects again.
    (demo / "tests/test_broken.py").write_text("def test_now():\n    pass\n", encoding="utf-8")
    code, again, err = run_json("run", "--failed", "--json", "--repo", str(demo))
    assert again, err
    ids = {r["test_id"] for r in json.loads((demo / again["results_path"]).read_text("utf-8"))}
    assert "tests/test_broken.py::test_now" in ids


def test_failed_refuses_after_an_unfinished_run(demo: Path) -> None:
    run_json("run", "--all", "--json", "--repo", str(demo))
    (demo / ".proof-ci" / "run-in-progress").write_text("x\n", encoding="utf-8")
    code, _, out = run_json("run", "--failed", "--json", "--repo", str(demo))
    assert code == 2
    assert "did not finish" in out


def test_module_named_by_lint_config_or_collect_ignore_is_not_a_plugin(demo: Path) -> None:
    # attrs: a test file excluded via collect_ignore and named in ruff's per-file config
    # imports the edited module. Neither mention runs it, so selection stays narrow.
    _commit_files(
        demo,
        {
            "tests/test_skipme.py": "from demo import textops\n\n\ndef test_x():\n    pass\n",
            "tests/conftest.py": 'collect_ignore = ["test_skipme.py"]\n',
        },
    )
    _append(
        demo,
        "pyproject.toml",
        '\n[tool.ruff.lint.per-file-ignores]\n"tests/test_skipme.py" = ["E1"]\n',
    )
    git(demo, "commit", "-q", "-am", "config")
    _append(demo, "src/demo/textops.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is False
    assert set(data["selected_tests"]) == TEXT


def test_dash_p_in_addopts_registers_a_plugin(demo: Path) -> None:
    _commit_files(demo, {"src/demo/plug.py": "def pytest_configure(config):\n    pass\n"})
    pp = demo / "pyproject.toml"
    text = pp.read_text(encoding="utf-8")
    assert 'testpaths = ["tests"]\n' in text
    pp.write_text(
        text.replace(
            'testpaths = ["tests"]\n', 'testpaths = ["tests"]\naddopts = "-p demo.plug"\n'
        ),
        encoding="utf-8",
    )
    git(demo, "commit", "-q", "-am", "register plugin")
    _append(demo, "src/demo/plug.py", "\n# edit\n")
    data = _affected(demo)
    assert data["fallback_full_run"] is True
    assert "registered as a pytest plugin" in data["fallback_reason"]
