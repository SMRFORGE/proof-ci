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
EXIT_VERIFY = 4


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
    discover: Annotated[
        bool,
        typer.Option("--discover", help="Read CI config and use its pytest command."),
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
        result = init_repo(root, common.config, force=force, no_key=no_key, discover=discover)
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
    if discover:
        if result.discovered_command:
            typer.echo(f"Discovered test command: {result.discovered_command}")
        else:
            typer.echo("No high-confidence pytest command found in CI config; using pytest")
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
    base: Annotated[
        str | None,
        typer.Option("--base", metavar="REF", help="Diff base: auto (default), HEAD, or a ref."),
    ] = None,
    all_tests: Annotated[bool, typer.Option("--all", help="Run every collected test.")] = False,
    failed: Annotated[
        bool, typer.Option("--failed", help="Re-run the previous run's failed and errored tests.")
    ] = False,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Bypass the result and collection caches.")
    ] = False,
    sign: Annotated[bool, typer.Option("--sign", help="Sign a RunRecord for this run.")] = False,
    allow_known_flakes: Annotated[
        bool,
        typer.Option(
            "--allow-known-flakes",
            help="Do not fail on confirmed or ignored flaky tests (unchanged code only).",
        ),
    ] = False,
    agent_id: Annotated[
        str | None, typer.Option("--agent-id", metavar="ID", help="Agent id for a signed record.")
    ] = None,
    record_out: Annotated[
        Path | None,
        typer.Option("--record-out", metavar="PATH", help="Where to write the signed record."),
    ] = None,
    *,
    common: CommonOptions,
) -> None:
    """Run the tests affected by the current change (or --all / --failed / TESTS)."""
    from proof_ci.runner import RunError, RunOptions, execute_run

    explicit = tuple(tests or ())
    if sum(bool(x) for x in (explicit, all_tests, failed)) > 1:
        raise _fail("--all, --failed, and TESTS are mutually exclusive", EXIT_USAGE)
    mode: Literal["affected", "all", "explicit", "failed"] = (
        "explicit" if explicit else "all" if all_tests else "failed" if failed else "affected"
    )
    try:
        cfg = load_config(common.repo, common.config)
        outcome = execute_run(
            cfg,
            RunOptions(
                mode=mode,
                tests=explicit,
                json_output=common.json_output,
                base=base,
                no_cache=no_cache,
                allow_known_flakes=allow_known_flakes,
                calibrate=all_tests and cfg.savings.refresh_calibration_on_all,
                agent_id=agent_id,
            ),
        )
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    except RunError as exc:
        raise _fail(str(exc), EXIT_RUNTIME) from exc

    for note in outcome.notes:
        typer.echo(note, err=True)
    summary = outcome.summary
    sign_refused: tuple[str, int] | None = None
    if sign:
        # A green record must mean at least one test was checked (Section 5.8 Step 7).
        if summary.selection.selected_count == 0:
            sign_refused = ("cannot sign a run that selected no tests", EXIT_USAGE)
        elif outcome.run_dir is not None:
            try:
                out = _record_output(cfg, outcome.run_dir, record_out)
                signed = _sign(cfg, outcome.run_dir, agent_id, None, out)
                summary = _attach_record(cfg, outcome.run_dir, summary, signed)
            except _SignFailure as exc:
                sign_refused = (exc.message, exc.code)

    if common.json_output:
        emit_json(summary.to_json())
    else:
        sel = summary.selection
        typer.echo(f"proof-ci run ({sel.mode}): {summary.summary}")
        typer.echo(f"Checked {sel.selected_count}/{sel.collected_count} tests.")
        if sel.fallback_full_run:
            typer.echo(sel.fallback_reason or "full run (fallback)")
        flake_status = {r.test_id: r.flaky.status for r in outcome.results if r.flaky}
        for f in summary.failures:
            where = f"{f.file}:{f.line}" if f.line else f.file
            status = flake_status.get(f.test_id, "none")
            label = (
                "FAILED (known flaky)"
                if status in ("confirmed", "ignored")
                else "FAILED (suspected flaky)"
                if status == "suspected"
                else "FAILED"
            )
            typer.echo(f"{label} {f.test_id} ({where})")
            first = f.error_message.splitlines()[0] if f.error_message else ""
            typer.echo(f"  {f.error_type}: {first}")
            typer.echo(f"  repro: {f.repro_command}")
        if summary.savings_headline:
            typer.echo(summary.savings_headline)
        _payoff_note(summary.savings)
        typer.echo(f"Results: {summary.results_path}")
        if summary.record_path:
            typer.echo(f"Signed record: {summary.record_path}")
    if sign_refused is not None:
        message, code = sign_refused
        if outcome.exit_code == 1:
            # Test failures take precedence: callers checking for 1 must not miss them.
            typer.echo(f"error: {message}", err=True)
            raise typer.Exit(1)
        raise _fail(message, code)
    raise typer.Exit(outcome.exit_code)


@app.command()
@common_options
def affected(
    base: Annotated[
        str | None,
        typer.Option("--base", metavar="REF", help="Diff base: auto (default), HEAD, or a ref."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            help="ids (node ids), files (selected test files), or json.",
        ),
    ] = "ids",
    *,
    common: CommonOptions,
) -> None:
    """List the tests affected by the current change. Never runs tests."""
    from proof_ci.runner import RunError, compute_affected

    if output_format not in ("ids", "files", "json"):
        raise _fail("--format must be ids, files, or json", EXIT_USAGE)
    try:
        cfg = load_config(common.repo, common.config)
        plan = compute_affected(cfg, base)
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    except RunError as exc:
        raise _fail(str(exc), EXIT_RUNTIME) from exc

    for note in plan.notes:
        typer.echo(note, err=True)
    sel = plan.selection
    if common.json_output or output_format == "json":
        emit_json(
            {
                "base_setting": sel.base_setting,
                "base_ref": sel.base_ref,
                "changed_files": plan.changed,
                "selected_tests": plan.selected,
                "fallback_full_run": bool(sel.fallback_full_run),
                "fallback_reason": sel.fallback_reason,
            }
        )
        return
    if output_format == "files":
        for path in dict.fromkeys(plan.item_file[t] for t in plan.selected):
            typer.echo(path)
        return
    for test_id in plan.selected:
        typer.echo(test_id)


flakes_app = typer.Typer(
    help="Inspect and manage the local flake store.",
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)
app.add_typer(flakes_app, name="flakes")

_FLAKE_STATUSES = ("suspected", "confirmed", "resolved", "ignored")


def _store_for(common: CommonOptions) -> Any:
    from proof_ci.runner import _flake_store

    try:
        cfg = load_config(common.repo, common.config)
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    return _flake_store(cfg)


def _print_flake(record: Any) -> None:
    typer.echo(
        f"{record.status:<10} pass={record.pass_count} fail={record.fail_count} "
        f"flips={record.flip_count}  {record.test_id}"
    )
    if record.reason:
        typer.echo(f"           {record.reason}")


@flakes_app.command("list")
@common_options
def flakes_list(
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            help="suspected|confirmed|resolved|ignored|all (default: suspected and confirmed).",
        ),
    ] = None,
    *,
    common: CommonOptions,
) -> None:
    """List flake records."""
    if status is None:
        statuses: tuple[str, ...] = ("suspected", "confirmed")
    elif status == "all":
        statuses = ("suspected", "confirmed", "ignored")
    elif status in _FLAKE_STATUSES:
        statuses = (status,)
    else:
        raise _fail(f"unknown --status {status!r}", EXIT_USAGE)
    records = _store_for(common).list_records(statuses)
    if common.json_output:
        emit_json({"flakes": [r.to_json() for r in records]})
        return
    if not records:
        typer.echo("No flaky tests recorded.")
    for record in records:
        _print_flake(record)


@flakes_app.command("show")
@common_options
def flakes_show(
    test_id: Annotated[str, typer.Argument(metavar="TEST_ID")],
    *,
    common: CommonOptions,
) -> None:
    """Show one flake record."""
    record = _store_for(common).get(test_id)
    if record is None:
        raise _fail(f"no flake record for {test_id}", EXIT_USAGE)
    if common.json_output:
        emit_json(record.to_json())
    else:
        _print_flake(record)


@flakes_app.command("mark")
@common_options
def flakes_mark(
    test_id: Annotated[str, typer.Argument(metavar="TEST_ID")],
    status: Annotated[str, typer.Option("--status", help="confirmed or ignored.")] = "confirmed",
    reason: Annotated[str | None, typer.Option("--reason", metavar="TEXT")] = None,
    *,
    common: CommonOptions,
) -> None:
    """Mark a test as flaky by hand (confirmed or ignored)."""
    if status not in ("confirmed", "ignored"):
        raise _fail("--status must be confirmed or ignored", EXIT_USAGE)
    record = _store_for(common).mark(test_id, status, reason)
    if common.json_output:
        emit_json(record.to_json())
    else:
        _print_flake(record)


@flakes_app.command("unmark")
@common_options
def flakes_unmark(
    test_id: Annotated[str, typer.Argument(metavar="TEST_ID")],
    reason: Annotated[str | None, typer.Option("--reason", metavar="TEXT")] = None,
    *,
    common: CommonOptions,
) -> None:
    """Clear a flake flag (status becomes resolved; history is kept)."""
    record = _store_for(common).unmark(test_id, reason)
    if record is None:
        raise _fail(f"no flake record for {test_id}", EXIT_USAGE)
    if common.json_output:
        emit_json(record.to_json())
    else:
        _print_flake(record)


# ----------------------------------------------------------------- records


class _SignFailure(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _record_output(cfg: Any, run_dir: Path, requested: Path | None) -> Path:
    """Default ``<run_dir>/record.json``; an explicit path must stay inside the repo."""
    if requested is None:
        return run_dir / "record.json"
    target = requested if requested.is_absolute() else Path.cwd() / requested
    target = target.resolve()
    root = cfg.repo_root.resolve()
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise _SignFailure(f"{requested} resolves outside the repository", EXIT_USAGE) from exc
    state = root / ".proof-ci"
    if (target == state or state in target.parents) and (state / "runs") not in target.parents:
        raise _SignFailure(
            f"{requested} would overwrite proof-ci's own state; use .proof-ci/runs/<run_id>/",
            EXIT_USAGE,
        )
    from proof_ci.util import GitError, run_git

    try:
        for flag in ("--git-dir", "--git-common-dir"):
            out = run_git(root, "rev-parse", flag, check=False)
            if out.returncode == 0 and out.stdout.strip():
                git_dir = Path(out.stdout.strip())
                git_dir = (git_dir if git_dir.is_absolute() else root / git_dir).resolve()
                if target == git_dir or git_dir in target.parents:
                    raise _SignFailure(f"{requested} is inside the git directory", EXIT_USAGE)
        tracked = run_git(root, "ls-files", "--error-unmatch", "--", rel.as_posix(), check=False)
        if tracked.returncode == 0:
            raise _SignFailure(
                f"{requested} is a tracked file; refusing to overwrite it", EXIT_USAGE
            )
    except GitError:
        pass
    return target


def _sign(
    cfg: Any,
    run_dir: Path,
    agent_id: str | None,
    key: Path | None,
    output: Path,
) -> Any:
    import os

    from proof_ci.adapters.agent.identity import resolve_identity
    from proof_ci.record import KeyError_, SignError, resolve_key_path, sign_run

    identity = resolve_identity(agent_id, cfg.record.agent_id, os.environ)
    if identity.defaulted:
        _warn("no agent id given; using 'local' (set --agent-id or PROOF_CI_AGENT_ID)")
    try:
        result = sign_run(
            cfg,
            run_dir,
            identity,
            resolve_key_path(cfg, key),
            output,
        )
    except SignError as exc:
        raise _SignFailure(str(exc), EXIT_RUNTIME if exc.runtime else EXIT_USAGE) from exc
    except KeyError_ as exc:
        raise _SignFailure(str(exc), EXIT_USAGE) from exc
    for message in result.warnings:
        _warn(message)
    return result


def _attach_record(cfg: Any, run_dir: Path, summary: Any, signed: Any) -> Any:
    from proof_ci.runner import _rel, _write_json

    try:
        record_path = _rel(cfg, signed.path)
    except ValueError:
        record_path = signed.path.as_posix()
    updated = summary.model_copy(update={"record_path": record_path})
    from proof_ci.savings import SavingsLedger

    SavingsLedger(cfg.resolve(cfg.savings.db)).set_record_path(str(summary.run_id), record_path)
    _write_json(run_dir / "summary.json", updated.to_json())
    last = cfg.repo_root / ".proof-ci" / "last-run.json"
    if last.is_file():
        try:
            if json.loads(last.read_text(encoding="utf-8")).get("run_id") == str(summary.run_id):
                _write_json(last, updated.to_json())
        except ValueError:
            pass
    return updated


record_app = typer.Typer(
    help="Sign and verify run records (tamper-evident statements of what proof-ci reported).",
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)
app.add_typer(record_app, name="record")


@record_app.command("keygen")
@common_options
def record_keygen(
    force: Annotated[bool, typer.Option("--force", help="Replace an existing key.")] = False,
    key: Annotated[Path | None, typer.Option("--key", metavar="PATH")] = None,
    *,
    common: CommonOptions,
) -> None:
    """Create an Ed25519 signing key (owner-only) and its .pub public key."""
    from proof_ci.record import KeyError_, generate_keypair, resolve_key_path

    cfg = None
    try:
        cfg = load_config(common.repo, common.config)
    except ConfigError:
        cfg = None
    path = resolve_key_path(cfg, key)
    try:
        pub = generate_keypair(path, force=force)
    except KeyError_ as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    except OSError as exc:
        raise _fail(f"cannot write {path}: {exc}", EXIT_RUNTIME) from exc
    if common.json_output:
        emit_json({"key_path": path.as_posix(), "public_key_path": pub.as_posix()})
        return
    typer.echo(f"Private key: {path}")
    typer.echo(f"Public key:  {pub}")
    typer.echo("To accept records from this key, add the .pub path to [record].trusted_pubkeys.")


@record_app.command("sign")
@common_options
def record_sign(
    run_id: Annotated[str | None, typer.Option("--run", metavar="RUN_ID")] = None,
    output: Annotated[Path | None, typer.Option("--output", metavar="PATH")] = None,
    key: Annotated[Path | None, typer.Option("--key", metavar="PATH")] = None,
    agent_id: Annotated[str | None, typer.Option("--agent-id", metavar="ID")] = None,
    *,
    common: CommonOptions,
) -> None:
    """Sign a run that `proof-ci run` produced (default: the last run)."""
    try:
        cfg = load_config(common.repo, common.config)
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    runs = cfg.repo_root / ".proof-ci" / "runs"
    if run_id is None:
        last = cfg.repo_root / ".proof-ci" / "last-run.json"
        try:
            run_id = str(json.loads(last.read_text(encoding="utf-8"))["run_id"])
        except (OSError, ValueError, KeyError) as exc:
            raise _fail("no last run found; run `proof-ci run` first", EXIT_USAGE) from exc
    import uuid as _uuid

    try:
        run_dir = runs / str(_uuid.UUID(run_id))
    except ValueError as exc:
        raise _fail(f"--run {run_id!r} is not a run id", EXIT_USAGE) from exc
    if not (run_dir / "summary.json").is_file():
        raise _fail(f"no run directory for {run_id}", EXIT_USAGE)
    try:
        out = _record_output(cfg, run_dir, output)
        signed = _sign(cfg, run_dir, agent_id, key, out)
    except _SignFailure as exc:
        raise _fail(exc.message, exc.code) from exc
    from proof_ci.models import RunSummary

    summary = RunSummary.model_validate(
        json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    )
    _attach_record(cfg, run_dir, summary, signed)
    if common.json_output:
        emit_json(
            {
                "record_path": signed.path.as_posix(),
                "run_id": run_id,
                "key_id": signed.record["signature"]["key_id"],
            }
        )
    else:
        typer.echo(f"Signed {signed.path} (key {signed.record['signature']['key_id']})")


@record_app.command("verify")
@common_options
def record_verify(
    path: Annotated[Path, typer.Argument(metavar="PATH")],
    pubkey: Annotated[
        Path | None, typer.Option("--pubkey", metavar="PATH", help="Trust only this public key.")
    ] = None,
    key: Annotated[
        Path | None,
        typer.Option("--key", metavar="PATH", help="Trust the public half of this private key."),
    ] = None,
    require_known_key: Annotated[
        bool,
        typer.Option("--require-known-key", help="Fail unless the signer is a trusted key."),
    ] = False,
    *,
    common: CommonOptions,
) -> None:
    """Verify a record: integrity always, identity only against trusted keys."""
    from proof_ci.record import (
        KeyError_,
        load_private_key,
        load_public_key,
        verify_record,
    )

    trusted: list[bytes] = []
    trust_source: str | None = None
    try:
        if pubkey is not None:
            trusted = [load_public_key(pubkey)]
            trust_source = "--pubkey"
        elif key is not None:
            private, _warnings = load_private_key(key)
            from cryptography.hazmat.primitives import serialization

            trusted = [
                private.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
            ]
            trust_source = "--key"
        else:
            try:
                cfg = load_config(common.repo, common.config)
                trusted = [load_public_key(cfg.resolve(p)) for p in cfg.record.trusted_pubkeys]
                if trusted:
                    trust_source = f"config:{cfg.config_path.as_posix()}"
            except ConfigError:
                trusted = []
    except KeyError_ as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    result = verify_record(path, trusted, require_known_key=require_known_key)
    if result.valid and result.signer == "unknown":
        _warn("verified against the embedded public key: this proves integrity, not identity")
    if result.valid and trust_source and trust_source.startswith("config:"):
        _warn(
            f"trusted keys came from {trust_source[7:]}, which the repository controls; "
            "reviewers should pass --pubkey obtained out of band"
        )
    if common.json_output:
        data = result.as_json()
        if result.valid:
            data["trust_source"] = trust_source
        emit_json(data)
    elif result.valid:
        typer.echo(f"valid: run {result.run_id}, key {result.key_id}, signer {result.signer}")
    else:
        typer.echo(f"invalid: {result.reason}")
    raise typer.Exit(0 if result.valid else EXIT_VERIFY)


def _payoff_note(savings: Any) -> None:
    """Repeat the calibration verdict once when plain pytest is faster here."""
    if savings is None or savings.calibration.payoff != "overhead_dominates":
        return
    from proof_ci.savings import payoff_sentence

    cal = savings.calibration
    typer.echo(
        payoff_sentence(cal.payoff, cal.full_suite_wall_ms or 0, cal.fixed_overhead_ms or 0),
        err=True,
    )


savings_app = typer.Typer(
    help="Report what proof-ci saved (time and counts first; money only when prices are set).",
    invoke_without_command=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)
app.add_typer(savings_app, name="savings")


@savings_app.callback(invoke_without_command=True)
@common_options
def savings_report(
    ctx: typer.Context,
    since: Annotated[str, typer.Option("--since", metavar="WHEN")] = "30d",
    until: Annotated[str | None, typer.Option("--until", metavar="WHEN")] = None,
    by: Annotated[str, typer.Option("--by", help="repo, agent, or branch.")] = "repo",
    weekly: Annotated[bool, typer.Option("--weekly", help="Also bucket by ISO week.")] = False,
    *,
    common: CommonOptions,
) -> None:
    """Summarise the local savings ledger as a SavingsReport."""
    if ctx.invoked_subcommand is not None:
        return
    from datetime import UTC, datetime

    from proof_ci.savings import SavingsLedger, build_report, parse_when

    if by not in ("repo", "agent", "branch"):
        raise _fail("--by must be repo, agent, or branch", EXIT_USAGE)
    try:
        start = parse_when(since)
        end = parse_when(until) if until else datetime.now(UTC)
    except ValueError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    try:
        cfg = load_config(common.repo, common.config)
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    fmt = "%Y-%m-%dT%H:%M:%S.%f"
    try:
        rows = SavingsLedger(cfg.resolve(cfg.savings.db)).rows(
            start.strftime(fmt)[:-3] + "Z", end.strftime(fmt)[:-3] + "Z"
        )
    except Exception as exc:  # sqlite errors: the ledger cannot be read
        raise _fail(f"cannot read the savings ledger: {exc}", EXIT_RUNTIME) from exc
    report = build_report(
        rows,
        since=start,
        until=end,
        group_by=by,  # type: ignore[arg-type]
        weekly=weekly,
        repo_name=cfg.project_name,
    )
    if common.json_output:
        emit_json(report.to_json())
        return
    typer.echo(report.headline)
    for group in report.groups:
        week = f" {group.week}" if group.week else ""
        g = group.totals
        typer.echo(
            f"  {group.key}{week}: {group.runs} run(s), flaky flagged {g.wrong_turns_avoided}, "
            f"CI runs avoided {g.ci_runs_avoided}, local test-min ~{g.test_ms_not_executed // 60000}"
        )
    if report.defaults_used:
        typer.echo(
            f"Default prices in use: {', '.join(report.defaults_used)} (no dollar figures shown)"
        )


@savings_app.command("calibrate")
@common_options
def savings_calibrate(*, common: CommonOptions) -> None:
    """Run the full suite to measure per-test durations and proof-ci's own overhead."""
    from proof_ci.runner import RunError, RunOptions, execute_run
    from proof_ci.savings import payoff_sentence

    try:
        cfg = load_config(common.repo, common.config)
        outcome = execute_run(
            cfg, RunOptions(mode="all", json_output=common.json_output, calibrate=True)
        )
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    except RunError as exc:
        raise _fail(str(exc), EXIT_RUNTIME) from exc
    savings = outcome.savings
    if common.json_output:
        emit_json(savings.to_json() if savings else {})
    elif savings is not None:
        cal = savings.calibration
        typer.echo(outcome.summary.summary)
        typer.echo(
            payoff_sentence(cal.payoff, cal.full_suite_wall_ms or 0, cal.fixed_overhead_ms or 0)
        )
    raise typer.Exit(outcome.exit_code)


@app.command("discover")
@common_options
def discover_cmd(*, common: CommonOptions) -> None:
    """List test commands found in CI config (read-only; nothing is executed)."""
    from proof_ci.discover import discover

    if common.repo is not None:
        root = common.repo
    else:
        try:
            root = find_repo_root()
        except ConfigError as exc:
            raise _fail(str(exc), EXIT_USAGE) from exc
    try:
        cfg = load_config(root, common.config)
        discovery_cfg = cfg.discovery
    except ConfigError:
        from proof_ci.config import DiscoveryConfig

        discovery_cfg = DiscoveryConfig()
    result = discover(root.resolve(), discovery_cfg)
    if common.json_output:
        emit_json(
            {
                "best": result.best,
                "candidates": [c.as_json() for c in result.candidates],
            }
        )
        return
    if not result.candidates:
        typer.echo("No test commands found in CI config.")
    for c in result.candidates:
        note = f"  (skipped: {c.skipped_reason})" if c.skipped_reason else ""
        typer.echo(f"[{c.confidence}] {c.source_path} :: {c.job_name}: {c.command}{note}")
    if result.best:
        typer.echo(f"Suggested test_command = {json.dumps(result.best)}")


@app.command("mcp")
def mcp_cmd() -> None:
    """Start the MCP server on stdio (no TCP port; stdout is the MCP transport)."""
    import anyio

    from proof_ci.adapters.agent.mcp_server import serve

    typer.echo("proof-ci MCP server starting on stdio", err=True)
    anyio.run(serve)
