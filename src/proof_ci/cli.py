"""Typer application for the ``proof-ci`` command.

The Typer app object is named ``app`` so the ``[project.scripts]`` entry
``proof-ci = "proof_ci.cli:app"`` works (spec Section 5.3).

Milestone M1: the CLI reports its version and help only. Leaf commands arrive
in later milestones; each of them is decorated with :func:`common_options` so
``--json``, ``--repo``, and ``--config`` are accepted on every leaf command
(spec Section 5.7).
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer

from proof_ci import __version__

__all__ = ["CommonOptions", "app", "common_options", "configure_utf8_streams"]


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

    kept = [p for p in original.parameters.values() if p.name != COMMON_PARAMETER_NAME]
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

    wrapper.__signature__ = original.replace(parameters=[*kept, *injected])  # type: ignore[attr-defined]
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
