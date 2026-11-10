"""M1 smoke tests: the package installs and the CLI reports version and help.

These tests must pass on Linux, macOS, and native Windows (spec Section 5.20).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import typer
from typer.testing import CliRunner

import proof_ci
from proof_ci import cli
from proof_ci.cli import CommonOptions, app, common_options

runner = CliRunner()


def _run_module(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``python -m proof_ci`` as a subprocess with UTF-8 captured text."""
    return subprocess.run(
        [sys.executable, "-m", "proof_ci", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def test_version_constant() -> None:
    assert proof_ci.__version__ == "0.1.0"


def test_version_flag_prints_name_and_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout == f"proof-ci {proof_ci.__version__}\n"
    assert result.stdout == "proof-ci 0.1.0\n"


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--version" in result.stdout


def test_python_dash_m_version() -> None:
    proc = _run_module("--version")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "proof-ci 0.1.0"


def test_python_dash_m_help() -> None:
    proc = _run_module("--help")
    assert proc.returncode == 0, proc.stderr
    assert "Usage: proof-ci" in proc.stdout


def test_app_object_is_typer() -> None:
    """``[project.scripts] proof-ci = "proof_ci.cli:app"`` requires a Typer app named ``app``."""
    assert isinstance(app, typer.Typer)
    assert cli.app is app


def test_adapter_packages_exist() -> None:
    """The vendor-neutral core / adapter split from spec Section 3.5 is laid out in M1."""
    import proof_ci.adapters.agent
    import proof_ci.adapters.ci
    import proof_ci.adapters.forge
    import proof_ci.adapters.runners

    for pkg in (
        proof_ci.adapters.agent,
        proof_ci.adapters.forge,
        proof_ci.adapters.ci,
        proof_ci.adapters.runners,
    ):
        assert pkg.__file__ is not None
        assert Path(pkg.__file__).name == "__init__.py"


def test_streams_are_utf8_after_import() -> None:
    """Spec Section 5.20 rule 2: stdout/stderr are UTF-8 at CLI startup.

    Run in a subprocess with the UTF-8 mode switches off so the parent's
    encoding does not leak in. On Windows a pipe would otherwise be cp1252.
    """
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONIOENCODING", "PYTHONUTF8"}}
    env["PYTHONUTF8"] = "0"
    code = "import sys, proof_ci.cli; print(sys.stdout.encoding, sys.stderr.encoding)"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out, err = proc.stdout.split()
    assert out.lower().replace("_", "-") == "utf-8"
    assert err.lower().replace("_", "-") == "utf-8"


def test_common_options_helper_injects_three_options() -> None:
    """Spec Section 5.7: one shared helper adds --json/--repo/--config to a leaf command."""
    seen: dict[str, CommonOptions] = {}
    test_app = typer.Typer()

    @test_app.command()
    @common_options
    def leaf(name: str = "x", *, common: CommonOptions) -> None:
        seen["opts"] = common
        typer.echo(name)

    default = runner.invoke(test_app, [])
    assert default.exit_code == 0, default.output
    assert seen["opts"] == CommonOptions(json_output=False, repo=None, config=None)

    explicit = runner.invoke(
        test_app, ["--json", "--repo", "some/repo", "--config", "some/proof-ci.toml"]
    )
    assert explicit.exit_code == 0, explicit.output
    assert seen["opts"].json_output is True
    assert seen["opts"].repo == Path("some/repo")
    assert seen["opts"].config == Path("some/proof-ci.toml")

    negated = runner.invoke(test_app, ["--json", "--no-json"])
    assert negated.exit_code == 0, negated.output
    assert seen["opts"].json_output is False

    # CI terminals get rich formatting; force plain, wide output and strip any ANSI codes.
    help_result = runner.invoke(
        test_app, ["--help"], env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    )
    assert help_result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", help_result.stdout)
    for flag in ("--json", "--no-json", "--repo", "--config"):
        assert flag in plain


def test_common_options_requires_common_parameter() -> None:
    import pytest

    def no_common() -> None:  # pragma: no cover - never called
        pass

    with pytest.raises(TypeError, match="common"):
        common_options(no_common)
