"""Typer application for the ``proof-ci`` command.

The Typer app object is named ``app`` so the ``[project.scripts]`` entry
``proof-ci = "proof_ci.cli:app"`` works (spec Section 5.3).

Every leaf command is decorated with :func:`common_options` so ``--json``,
``--repo``, and ``--config`` are accepted on every leaf command (spec Section
5.7). Exit codes follow spec Section 5.5.
"""

from __future__ import annotations

import inspect
import json
import sys
import typing
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from proof_ci import __version__
from proof_ci.config import ConfigError, InitIOError, find_repo_root, init_repo, load_config

__all__ = [
    "EXIT_RUNTIME",
    "EXIT_USAGE",
    "CommonOptions",
    "app",
    "common_options",
    "configure_utf8_streams",
    "emit_json",
]

EXIT_USAGE = 2
EXIT_RUNTIME = 3


def configure_utf8_streams() -> None:
    """Reconfigure ``sys.stdout`` and ``sys.stderr`` to UTF-8 (spec Section 5.20 rule 2).

    JSON output must not be mangled by a cp1252 console on Windows. Streams that
    cannot be reconfigured (replaced by a test harness, closed, or not text
    wrappers) are left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError, TypeError):
            continue


@dataclass(frozen=True)
class CommonOptions:
    """The three options accepted on every leaf command (spec Section 5.7)."""

    json_output: bool = False
    """``--json``: machine-readable stdout; human text goes to stderr."""

    repo: Path | None = None
    """``--repo PATH``: override the discovered repository root."""

    config: Path | None = None
    """``--config PATH``: override ``<repo>/proof-ci.toml``."""


_JsonOption = Annotated[
    bool,
    typer.Option(
        "--json/--no-json",
        help="Machine-readable stdout (human text goes to stderr).",
        show_default=False,
    ),
]
_RepoOption = Annotated[
    Path | None,
    typer.Option(
        "--repo",
        metavar="PATH",
        help="Override the repository root (default: discovered).",
        show_default=False,
    ),
]
_ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        metavar="PATH",
        help="Override the config path (default: <repo>/proof-ci.toml).",
        show_default=False,
    ),
]

COMMON_PARAMETER_NAME = "common"
"""Name of the parameter through which decorated commands receive :class:`CommonOptions`."""


def common_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Add ``--json/--no-json``, ``--repo``, and ``--config`` to a leaf command.

    Click parses root-level options only before the subcommand name, so the
    three common options MUST be declared on every leaf command (spec Section
    5.7). This decorator is the single shared helper that does so. The
    decorated function declares one parameter named ``common`` (of type
    :class:`CommonOptions`), which is hidden from Typer and populated from the
    three injected options::

        @app.command()
        @common_options
        def run(*, common: CommonOptions) -> None: ...

    Apply it *below* ``@app.command()`` so Typer sees the rewritten signature.
    """
    original = inspect.signature(func)
    if COMMON_PARAMETER_NAME not in original.parameters:
        raise TypeError(
            f"{func.__qualname__} must declare a parameter named "
            f"{COMMON_PARAMETER_NAME!r} to receive CommonOptions"
        )

    # Resolve string annotations (``from __future__ import annotations``) so Typer
    # sees the real ``Annotated[..., typer.Option(...)]`` metadata on the wrapper.
    hints = typing.get_type_hints(func, include_extras=True)
    kept = [
        p.replace(annotation=hints.get(p.name, p.annotation))
        for p in original.parameters.values()
        if p.name != COMMON_PARAMETER_NAME
    ]
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in kept):
        raise TypeError(f"{func.__qualname__} must not take **kwargs")
    injected = [
        inspect.Parameter(
            "json_output",
            inspect.Parameter.KEYWORD_ONLY,
            default=False,
            annotation=_JsonOption,
        ),
        inspect.Parameter(
            "repo",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=_RepoOption,
        ),
        inspect.Parameter(
            "config",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=_ConfigOption,
        ),
    ]
    for p in injected:
        if p.name in original.parameters:
            raise TypeError(
                f"{func.__qualname__} already declares {p.name!r}; it is injected by common_options"
            )

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        common = CommonOptions(
            json_output=kwargs.pop("json_output", False),
            repo=kwargs.pop("repo", None),
            config=kwargs.pop("config", None),
        )
        kwargs[COMMON_PARAMETER_NAME] = common
        return func(*args, **kwargs)

    params = [*kept, *injected]
    wrapper.__signature__ = original.replace(  # type: ignore[attr-defined]
        parameters=params, return_annotation=hints.get("return", original.return_annotation)
    )
    wrapper.__annotations__ = {p.name: p.annotation for p in params}
    if "return" in hints:
        wrapper.__annotations__["return"] = hints["return"]
    return wrapper


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"proof-ci {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="proof-ci",
    help="proof-ci: an agent-native CI layer. Fast, structured, local-first checks.",
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """proof-ci: an agent-native CI layer. Fast, structured, local-first checks."""
    configure_utf8_streams()


# Reconfigure at import so the console-script entry point (which calls ``app()``
# directly) emits UTF-8 from the first byte, including eager ``--version`` output.
configure_utf8_streams()


def emit_json(obj: Any) -> None:
    """Print the primary JSON object on stdout (spec Section 5.6)."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def _warn(message: str) -> None:
    typer.echo(f"warning: {message}", err=True)


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code)


@app.command()
@common_options
def init(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing proof-ci.toml.")
    ] = False,
    no_key: Annotated[
        bool, typer.Option("--no-key", help="Do not create a signing key (containers, CI).")
    ] = False,
    *,
    common: CommonOptions,
) -> None:
    """Write proof-ci.toml, create .proof-ci/, and update .gitignore."""
    if common.repo is not None:
        root = common.repo
    else:
        try:
            root = find_repo_root()
        except ConfigError:
            root = Path.cwd()
    try:
        result = init_repo(root, common.config, force=force, no_key=no_key)
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    except InitIOError as exc:
        raise _fail(str(exc), EXIT_RUNTIME) from exc

    for message in result.warnings:
        _warn(message)
    if common.json_output:
        emit_json(result.as_json())
        return
    typer.echo(f"Wrote {result.config_path}")
    typer.echo(f"Created {result.repo / '.proof-ci'}")
    if result.gitignore_added:
        typer.echo(f"Added {len(result.gitignore_added)} entries to .gitignore")
    if result.key_created:
        typer.echo("Created a signing key")
    else:
        typer.echo(f"No signing key created: {result.key_skipped_reason}")


@app.command()
@common_options
def run(
    tests: Annotated[
        list[str] | None,
        typer.Argument(metavar="TESTS", help="pytest node ids or file paths (explicit mode)."),
    ] = None,
    all_tests: Annotated[bool, typer.Option("--all", help="Run every collected test.")] = False,
    failed: Annotated[
        bool, typer.Option("--failed", help="Re-run the previous run's failed and errored tests.")
    ] = False,
    *,
    common: CommonOptions,
) -> None:
    """Run tests through pytest and write a structured RunSummary."""
    from proof_ci.runner import RunError, RunOptions, execute_run

    explicit = tuple(tests or ())
    chosen = sum(bool(x) for x in (explicit, all_tests, failed))
    if chosen > 1:
        raise _fail("--all, --failed, and TESTS are mutually exclusive", EXIT_USAGE)
    if chosen == 0:
        raise _fail(
            "affected-test selection arrives in milestone M5; use --all, --failed, or TESTS for now",
            EXIT_USAGE,
        )
    mode: Literal["all", "explicit", "failed"] = (
        "explicit" if explicit else "all" if all_tests else "failed"
    )
    try:
        cfg = load_config(common.repo, common.config)
        outcome = execute_run(
            cfg, RunOptions(mode=mode, tests=explicit, json_output=common.json_output)
        )
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    except RunError as exc:
        raise _fail(str(exc), EXIT_RUNTIME) from exc

    summary = outcome.summary
    if common.json_output:
        emit_json(summary.to_json())
    else:
        sel = summary.selection
        typer.echo(f"proof-ci run ({sel.mode}): {summary.summary}")
        typer.echo(f"Checked {sel.selected_count}/{sel.collected_count} tests.")
        for f in summary.failures:
            where = f"{f.file}:{f.line}" if f.line else f.file
            typer.echo(f"FAILED {f.test_id} ({where})")
            typer.echo(
                f"  {f.error_type}: {f.error_message.splitlines()[0] if f.error_message else ''}"
            )
            typer.echo(f"  repro: {f.repro_command}")
        typer.echo(f"Results: {summary.results_path}")
    raise typer.Exit(outcome.exit_code)
