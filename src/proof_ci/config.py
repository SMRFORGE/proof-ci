"""Configuration loading and repository-root discovery (spec Section 5.4).

``proof-ci.toml`` lives at the repository root. Every key has a default, so a
missing file is tolerated: :func:`load_config` returns the defaults. Unknown
sections or keys and wrongly typed values are configuration errors (exit 2,
spec Section 5.5) so a typo does not silently fall back to a default.
"""

from __future__ import annotations

import json
import os
import shlex
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_CONFIG_TEXT",
    "PRICE_KEYS",
    "AffectedConfig",
    "CacheConfig",
    "Config",
    "ConfigError",
    "DiscoveryConfig",
    "FlakesConfig",
    "InitIOError",
    "InitResult",
    "ProjectConfig",
    "RecordConfig",
    "SavingsConfig",
    "find_repo_root",
    "init_repo",
    "load_config",
    "split_test_command",
]

CONFIG_FILENAME = "proof-ci.toml"


class ConfigError(Exception):
    """A usage or configuration error. The CLI maps it to exit code 2."""


# The file `proof-ci init` writes. Kept byte-for-byte aligned with the block in
# spec Section 5.4; tests parse it and compare against the dataclass defaults.
DEFAULT_CONFIG_TEXT = """\
[project]
name = ""                          # default: directory name
# Array form is canonical and needs no quoting rules (Windows paths included).
# A plain string is accepted and split with shlex (posix=False on Windows).
test_command = ["pytest"]
pytest_args = []                   # extra args always passed to pytest, e.g. ["-q"] or ["-n", "auto"]
source_roots = ["src"]             # used by the import graph; if a root does not exist, skip it
test_paths = ["tests"]

[affected]
# "auto" | "HEAD" | an explicit git ref. "auto" diffs against the merge-base
# with the default branch and never silently selects nothing on a clean tree
# (Section 5.8 Step 1). "HEAD" diffs only uncommitted work.
base = "auto"
fallback_full_run_globs = [
  "pyproject.toml",
  "setup.cfg",
  "setup.py",
  "tox.ini",
  "noxfile.py",
  "conftest.py",
  "requirements.txt",
  "requirements*.txt",
  "constraints*.txt",
  "uv.lock",
  "poetry.lock",
  "Pipfile.lock",
  "pdm.lock",
  ".gitlab-ci.yml",
  ".github/workflows/**",
  "proof-ci.toml",
]

[cache]
enabled = true
dir = ".proof-ci/cache"
collect_cache_dir = ".proof-ci/collect-cache"
collect_cache_env_recheck_minutes = 60     # re-run collection to refresh the environment fingerprint when every selected test would be a cache hit

[flakes]
db = ".proof-ci/flakes.sqlite"
min_runs = 3
window_days = 30
min_flips_confirmed = 2

[record]
# empty means the platformdirs default
key_path = ""
agent_id = ""
# Public PEM files (repo-relative or absolute) that `record verify` accepts as
# known signers. Empty means verify only proves integrity (Section 4.7).
trusted_pubkeys = []

[discovery]
gitlab_ci = ".gitlab-ci.yml"
github_workflows = ".github/workflows"

[savings]
# Prices below are DEFAULTS for local arithmetic. They are not market
# quotes and must not be cited as typical industry rates. Replace them
# with the rates you actually pay. A price key that is ABSENT from this
# file is listed in prices_used.defaults_used, and no dollar figure is
# shown in a headline until every applied price key is present
# (prices_configured). `proof-ci init` writes the keys commented out so
# that a fresh install shows time, not money.
db = ".proof-ci/savings.sqlite"
# ci_cost_per_minute = 0.01                            # USD per CI minute, used only for avoided CI runs; DEFAULT
# model_input_cost_per_million_tokens = 3.0            # USD; DEFAULT
# model_output_cost_per_million_tokens = 15.0          # USD; DEFAULT
# wasted_fix_cost = 25.0                               # USD per wasted fix attempt; DEFAULT
# price_local_minutes = false                          # true to price your own machine's test minutes; DEFAULT false
# local_compute_cost_per_minute = 0.0                  # USD per local test minute, applied only when price_local_minutes = true
# engineer_hourly_cost = 0.0                           # optional USD/hour; unused unless set > 0
# full_pipeline_baseline_ms = 0                        # optional measured CI pipeline wall time (queue + run)
calibration_max_age_days = 14
calibration_stale_after_runs = 50
refresh_calibration_on_all = true
"""

DEFAULT_FALLBACK_GLOBS: tuple[str, ...] = (
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "noxfile.py",
    "conftest.py",
    "requirements.txt",
    "requirements*.txt",
    "constraints*.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "pdm.lock",
    ".gitlab-ci.yml",
    ".github/workflows/**",
    "proof-ci.toml",
)

# Price keys that are listed in `defaults_used` when absent (spec Section 5.12).
PRICE_KEYS: tuple[str, ...] = (
    "ci_cost_per_minute",
    "model_input_cost_per_million_tokens",
    "model_output_cost_per_million_tokens",
    "wasted_fix_cost",
)


@dataclass(frozen=True)
class ProjectConfig:
    name: str = ""
    test_command: tuple[str, ...] = ("pytest",)
    pytest_args: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ("src",)
    test_paths: tuple[str, ...] = ("tests",)


@dataclass(frozen=True)
class AffectedConfig:
    base: str = "auto"
    fallback_full_run_globs: tuple[str, ...] = DEFAULT_FALLBACK_GLOBS


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool = True
    dir: str = ".proof-ci/cache"
    collect_cache_dir: str = ".proof-ci/collect-cache"
    collect_cache_env_recheck_minutes: int = 60


@dataclass(frozen=True)
class FlakesConfig:
    db: str = ".proof-ci/flakes.sqlite"
    min_runs: int = 3
    window_days: int = 30
    min_flips_confirmed: int = 2


@dataclass(frozen=True)
class RecordConfig:
    key_path: str = ""
    agent_id: str = ""
    trusted_pubkeys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveryConfig:
    gitlab_ci: str = ".gitlab-ci.yml"
    github_workflows: str = ".github/workflows"


@dataclass(frozen=True)
class SavingsConfig:
    db: str = ".proof-ci/savings.sqlite"
    ci_cost_per_minute: float = 0.01
    model_input_cost_per_million_tokens: float = 3.0
    model_output_cost_per_million_tokens: float = 15.0
    wasted_fix_cost: float = 25.0
    price_local_minutes: bool = False
    local_compute_cost_per_minute: float = 0.0
    engineer_hourly_cost: float = 0.0
    full_pipeline_baseline_ms: int = 0
    calibration_max_age_days: int = 14
    calibration_stale_after_runs: int = 50
    refresh_calibration_on_all: bool = True
    present_keys: frozenset[str] = field(default_factory=frozenset)
    """Keys that were present in the TOML (not part of the file format)."""

    @property
    def defaults_used(self) -> list[str]:
        """Price keys absent from the TOML (spec Section 5.12), sorted."""
        applied = list(PRICE_KEYS)
        if self.price_local_minutes:
            applied.append("local_compute_cost_per_minute")
        return sorted(k for k in applied if k not in self.present_keys)

    @property
    def prices_configured(self) -> bool:
        return not self.defaults_used


@dataclass(frozen=True)
class Config:
    repo_root: Path
    config_path: Path
    file_exists: bool
    project: ProjectConfig
    affected: AffectedConfig
    cache: CacheConfig
    flakes: FlakesConfig
    record: RecordConfig
    discovery: DiscoveryConfig
    savings: SavingsConfig

    @property
    def project_name(self) -> str:
        return self.project.name or self.repo_root.name

    def resolve(self, path: str) -> Path:
        """Resolve a config path relative to the repository root unless absolute."""
        p = Path(path)
        return p if p.is_absolute() else self.repo_root / p


_SECTIONS: dict[str, type] = {
    "project": ProjectConfig,
    "affected": AffectedConfig,
    "cache": CacheConfig,
    "flakes": FlakesConfig,
    "record": RecordConfig,
    "discovery": DiscoveryConfig,
    "savings": SavingsConfig,
}

_INTERNAL_FIELDS = {"present_keys"}


def split_test_command(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Normalise ``[project].test_command`` to an argument list (spec Section 5.20 rule 7).

    The array form is used as-is. A string is split with
    ``shlex.split(value, posix=(os.name != "nt"))`` so Windows paths keep their
    backslashes.
    """
    if isinstance(value, str):
        posix = os.name != "nt"
        parts = shlex.split(value, posix=posix)
        if not posix:
            # Non-POSIX shlex keeps surrounding quotes on quoted tokens; strip them.
            parts = [_strip_quotes(p) for p in parts]
        result = tuple(parts)
    else:
        result = tuple(value)
    if not result or not all(isinstance(p, str) and p for p in result):
        raise ConfigError("[project].test_command must be a non-empty array of strings")
    return result


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _expect_type(section: str, key: str, value: Any, default: Any) -> Any:
    where = f"[{section}].{key}"
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{where} must be true or false")
        return value
    if isinstance(default, int) and not isinstance(default, bool):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where} must be an integer")
        if value < 0:
            raise ConfigError(f"{where} must not be negative")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where} must be a number")
        if value < 0:
            raise ConfigError(f"{where} must not be negative")
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError(f"{where} must be a string")
        return value
    if isinstance(default, tuple):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{where} must be an array of strings")
        return tuple(value)
    raise AssertionError(f"unhandled default type for {where}")  # pragma: no cover


def _build_section(name: str, cls: type, raw: Any) -> Any:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"[{name}] must be a table")
    defaults = cls()
    known = {f.name for f in fields(cls)} - _INTERNAL_FIELDS
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown key(s) in [{name}]: {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if name == "project" and key == "test_command":
            if not isinstance(value, (str, list)):
                raise ConfigError("[project].test_command must be an array of strings or a string")
            values[key] = split_test_command(value)
        else:
            values[key] = _expect_type(name, key, value, getattr(defaults, key))
    if cls is SavingsConfig:
        values["present_keys"] = frozenset(raw)
    return cls(**values)


def find_repo_root(start: Path | None = None) -> Path:
    """Discover the repository root (spec Section 5.4).

    1. The nearest ancestor of ``start`` containing ``proof-ci.toml``.
    2. Otherwise the nearest ancestor containing ``.git`` (directory or file).
    3. Otherwise raise :class:`ConfigError`.
    """
    here = (start or Path.cwd()).resolve()
    candidates = [here, *here.parents]
    for d in candidates:
        if (d / CONFIG_FILENAME).is_file():
            return d
    for d in candidates:
        if (d / ".git").exists():
            return d
    raise ConfigError(
        f"no {CONFIG_FILENAME} or git repository found from {here}; "
        "run `proof-ci init` or cd into the repository"
    )


def load_config(repo: Path | None = None, config: Path | None = None) -> Config:
    """Load ``proof-ci.toml`` with defaults for every missing key.

    ``repo`` overrides root discovery; ``config`` overrides the config path
    (relative paths are taken from the current directory, like any CLI path).
    """
    repo_root = repo.resolve() if repo is not None else find_repo_root()
    if repo is not None and not repo_root.is_dir():
        raise ConfigError(f"--repo {repo} is not a directory")
    config_path = config.resolve() if config is not None else repo_root / CONFIG_FILENAME

    raw: dict[str, Any] = {}
    exists = config_path.is_file()
    if config is not None and not exists:
        raise ConfigError(f"config file not found: {config_path}")
    if exists:
        try:
            with config_path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"cannot read {config_path}: {exc}") from exc

    unknown = sorted(set(raw) - set(_SECTIONS))
    if unknown:
        raise ConfigError(f"unknown section(s) in {config_path.name}: {', '.join(unknown)}")
    sections = {
        name: _build_section(name, cls, raw.get(name, {})) for name, cls in _SECTIONS.items()
    }
    result = Config(repo_root=repo_root, config_path=config_path, file_exists=exists, **sections)
    _confine_state_paths(result)
    return result


def _confine_state_paths(cfg: Config) -> None:
    """Cache and database paths must stay inside ``<repo>/.proof-ci/``.

    A repository's proof-ci.toml is untrusted input: pointing the cache at a tracked
    directory would let committed entries be served as results, and pointing a database
    at another checkout would mix their histories.
    """
    state = (cfg.repo_root / ".proof-ci").resolve()
    for name, value in (
        ("[cache].dir", cfg.cache.dir),
        ("[cache].collect_cache_dir", cfg.cache.collect_cache_dir),
        ("[flakes].db", cfg.flakes.db),
        ("[savings].db", cfg.savings.db),
    ):
        target = cfg.resolve(value).resolve()
        if target != state and state not in target.parents:
            raise ConfigError(
                f"{name} = {value!r} must be inside the repository's .proof-ci/ directory"
            )


# --------------------------------------------------------------------------- init

GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".proof-ci/cache/",
    ".proof-ci/collect-cache/",
    ".proof-ci/flakes.sqlite",
    ".proof-ci/savings.sqlite",
    ".proof-ci/last-run.json",
    ".proof-ci/runs/",
    ".proof-ci/logs/",
)
GITIGNORE_BEGIN = "# >>> proof-ci (managed by `proof-ci init`) >>>"
GITIGNORE_END = "# <<< proof-ci <<<"

STATE_SUBDIRS: tuple[str, ...] = ("cache", "collect-cache", "runs", "logs")

LONG_PATH_WARN_CHARS = 150


@dataclass(frozen=True)
class InitResult:
    """Outcome of :func:`init_repo`; ``as_json`` is the spec Section 5.7.1 shape."""

    repo: Path
    config_path: Path
    key_created: bool
    key_skipped_reason: str | None
    discovered_command: list[str] | None
    gitignore_added: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo.as_posix(),
            "config_path": self.config_path.as_posix(),
            "key_created": self.key_created,
            "key_skipped_reason": self.key_skipped_reason,
            "discovered_command": self.discovered_command,
        }


def user_config_dir() -> Path:
    """Default directory for the signing key (spec Section 5.20 rule 5)."""
    import platformdirs

    return Path(platformdirs.user_config_dir("proof-ci", appauthor=False))


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _ensure_gitignore(repo_root: Path) -> tuple[str, ...]:
    """Append missing entries in a marked block; never rewrite existing lines."""
    path = repo_root / ".gitignore"
    existing = ""
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as fh:
            existing = fh.read()
    present = {line.strip() for line in existing.splitlines()}
    missing = tuple(e for e in GITIGNORE_ENTRIES if e not in present)
    if not missing:
        return ()
    block = "\n".join([GITIGNORE_BEGIN, *missing, GITIGNORE_END]) + "\n"
    prefix = ""
    if existing and not existing.endswith(("\n", "\r")):
        prefix = "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(prefix + block)
    return missing


def _root_owner_warning(repo_root: Path) -> str | None:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        return None
    try:
        owner = repo_root.stat().st_uid
    except OSError:
        return None
    if owner == 0:
        return None
    return (
        "running as root against a repository owned by uid "
        f"{owner}: .proof-ci/ will be root-owned on the host; "
        "run the container with --user $(id -u):$(id -g)"
    )


def _key_step(no_key: bool, config_dir: Path | None) -> tuple[bool, str | None, str | None]:
    """Return (key_created, key_skipped_reason, warning).

    Generates the Ed25519 signing key at the default location when none exists
    (spec Section 5.7.1 step 5). Never fails init because of the key.
    """
    if no_key:
        return False, "--no-key", None
    target = config_dir if config_dir is not None else user_config_dir()
    # Signing reads PROOF_CI_SIGNING_KEY first, so create the key there when it is set.
    env_key = os.environ.get("PROOF_CI_SIGNING_KEY")
    key_path = Path(env_key) if env_key else target / "ed25519.pem"
    target = key_path.parent
    if key_path.exists():
        return False, f"a signing key already exists at {key_path.as_posix()}", None
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".proof-ci-write-probe"
        probe.write_bytes(b"")
        probe.unlink()
        from proof_ci.record import KeyError_, generate_keypair

        try:
            generate_keypair(key_path)
        except KeyError_ as exc:
            return False, str(exc), f"no signing key was created: {exc}"
    except OSError as exc:
        warning = (
            f"config directory {target} is not writable ({exc.strerror or exc}); "
            "no signing key was created. Point PROOF_CI_SIGNING_KEY at a persistent key to sign records."
        )
        return False, f"config directory not writable: {target.as_posix()}", warning
    return True, None, None


def init_repo(
    repo_root: Path,
    config_path: Path | None = None,
    *,
    force: bool = False,
    no_key: bool = False,
    discover: bool = False,
    config_dir: Path | None = None,
) -> InitResult:
    """Implement ``proof-ci init`` (spec Section 5.7.1).

    Raises :class:`ConfigError` if the config file exists and ``force`` is false.
    ``config_dir`` overrides the platformdirs key directory (tests use it).
    """
    repo_root = repo_root.resolve()
    if not repo_root.is_dir():
        raise ConfigError(f"repository root {repo_root} is not a directory")
    config_path = (config_path or repo_root / CONFIG_FILENAME).resolve()
    if config_path.exists() and not force:
        raise ConfigError(f"{config_path} already exists; use --force to overwrite")

    warnings: list[str] = []
    if os.name == "nt" and len(str(repo_root)) > LONG_PATH_WARN_CHARS:
        warnings.append(
            f"repository path is {len(str(repo_root))} characters long; Windows MAX_PATH is 260. "
            "Consider a shorter path and `git config core.longpaths true`."
        )
    owner_warning = _root_owner_warning(repo_root)
    if owner_warning:
        warnings.append(owner_warning)

    state = repo_root / ".proof-ci"
    try:
        for sub in STATE_SUBDIRS:
            (state / sub).mkdir(parents=True, exist_ok=True)
        added = _ensure_gitignore(repo_root)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        text = DEFAULT_CONFIG_TEXT
        discovered: list[str] | None = None
        if discover:
            from proof_ci.discover import discover as run_discovery

            discovered = run_discovery(repo_root, DiscoveryConfig()).best
            if discovered is not None:
                line = 'test_command = ["pytest"]\n'
                text = text.replace(line, f"test_command = {json.dumps(discovered)}\n", 1)
        _write_text(config_path, text)
    except OSError as exc:
        raise InitIOError(f"cannot initialise {repo_root}: {exc}") from exc

    key_created, skipped, key_warning = _key_step(no_key, config_dir)
    if key_warning:
        warnings.append(key_warning)

    return InitResult(
        repo=repo_root,
        config_path=config_path,
        key_created=key_created,
        key_skipped_reason=skipped,
        discovered_command=discovered,
        gitignore_added=added,
        warnings=tuple(warnings),
    )


class InitIOError(Exception):
    """An I/O failure during init. The CLI maps it to exit code 3 (runtime error)."""
