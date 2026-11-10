"""Content-addressed caches under ``.proof-ci/``: the collection cache (spec Section 5.8
Step 5) and the result cache (Section 5.9).
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rfc8785

from proof_ci.affected import IGNORED_DIRS, is_test_file

__all__ = [
    "CACHEABLE_OUTCOMES",
    "CollectCache",
    "CollectEntry",
    "ResultCache",
    "collect_cache_key",
    "conftest_chain",
    "deps_hash",
    "file_sha256",
    "list_repo_files",
    "pytest_config_file",
    "result_cache_key",
    "trim_caches",
]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(repo: Path, start: Path, want: Any) -> list[str]:
    found: list[str] = []
    if not start.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            rel = Path(dirpath, name).relative_to(repo).as_posix()
            if want(rel):
                found.append(rel)
    return sorted(found)


def list_repo_files(repo: Path) -> list[str]:
    """Tracked and untracked (non-ignored) files, repo-relative; falls back to a walk."""
    try:
        from proof_ci.util import GitError, run_git

        proc = run_git(repo, "ls-files", "-z", "-co", "--exclude-standard", check=False)
        if proc.returncode == 0:
            return sorted(
                p
                for p in proc.stdout.split("\0")
                if p and not any(part in IGNORED_DIRS for part in p.split("/")[:-1])
            )
    except GitError:
        pass
    return _walk(repo, repo, lambda _rel: True)


def pytest_config_file(repo: Path) -> Path | None:
    """The pytest configuration file pytest would pick at the repo root, if any.

    Order follows pytest: pytest.ini, .pytest.ini, pyproject.toml with
    [tool.pytest.ini_options], tox.ini with [pytest], setup.cfg with [tool:pytest].
    """
    for name in ("pytest.ini", ".pytest.ini"):
        if (repo / name).is_file():
            return repo / name
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        if "ini_options" in data.get("tool", {}).get("pytest", {}):
            return pyproject
    for name, section in (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        path = repo / name
        if path.is_file():
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(path, encoding="utf-8")
            except (OSError, configparser.Error):
                continue
            if parser.has_section(section):
                return path
    return None


_DOC_SUFFIXES = (".md", ".rst", ".txt")


def repo_content_digest(repo: Path, tree: str | None = None) -> str:
    """Digest of every file in the tested tree except docs and ignored directories.

    Collection depends on more than test files: helper modules and data files drive
    parametrization, and modules a test imports at collection time can fail (M13).
    ``tree`` is the working-tree hash from :func:`proof_ci.util.tree_hash` (content-hashed,
    so a same-size rewrite within one second is still seen); its blob ids are the digest
    input. Without a tree (outside git) the result is empty and the per-file parts apply.
    """
    if not tree:
        return ""
    from proof_ci.util import GitError, run_git

    try:
        proc = run_git(repo, "ls-tree", "-r", "-z", tree, check=False)
    except GitError:
        return ""
    if proc.returncode != 0:
        return ""
    entries: dict[str, str] = {}
    for record in proc.stdout.split("\0"):
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if (
            path
            and len(fields) >= 3
            and not any(part in IGNORED_DIRS for part in path.split("/")[:-1])
            and not path.lower().endswith(_DOC_SUFFIXES)
        ):
            entries[path] = fields[2]
    return hashlib.sha256(rfc8785.dumps(entries)).hexdigest()


def collect_cache_key(
    repo: Path,
    test_paths: tuple[str, ...],
    pytest_args: tuple[str, ...],
    test_command: tuple[str, ...],
    tree: str | None = None,
) -> str:
    test_files: dict[str, str] = {}
    for root in test_paths:
        if (repo / root).is_file():  # a single test file listed directly
            test_files[Path(root).as_posix()] = file_sha256(repo / root)
            continue
        for rel in _walk(repo, repo / root, is_test_file):
            test_files[rel] = file_sha256(repo / rel)
    conftests = {
        rel: file_sha256(repo / rel)
        for rel in _walk(repo, repo, lambda r: r.rsplit("/", 1)[-1] == "conftest.py")
    }
    config = pytest_config_file(repo)
    document: dict[str, Any] = {
        "test_files": test_files,
        "conftests": conftests,
        "pytest_config": file_sha256(config) if config is not None else "",
        "pytest_args": list(pytest_args),
        "test_command": list(test_command),
        "repo_content": repo_content_digest(repo, tree),
    }
    return hashlib.sha256(rfc8785.dumps(document)).hexdigest()


@dataclass(frozen=True)
class CollectEntry:
    key: str
    python: str
    platform: str
    inipath: str | None
    installed: list[str]
    items: list[dict[str, Any]]
    written_at: float

    def age_minutes(self) -> float:
        return (time.time() - self.written_at) / 60


def _valid_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not all(
        isinstance(i, dict)
        and isinstance(i.get("test_id"), str)
        and isinstance(i.get("file"), str)
        and isinstance(i.get("line"), int)
        for i in items
    ):
        raise ValueError("malformed collection entry")
    return items


class CollectCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> CollectEntry | None:
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
            return CollectEntry(
                key=key,
                python=str(data["python"]),
                platform=str(data["platform"]),
                inipath=data.get("inipath"),
                installed=list(data["installed"]),
                items=_valid_items(data["items"]),
                written_at=float(data["written_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, entry: CollectEntry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(entry.key)
        tmp = path.with_name(path.name + ".tmp")
        payload = {
            "python": entry.python,
            "platform": entry.platform,
            "inipath": entry.inipath,
            "installed": entry.installed,
            "items": entry.items,
            "written_at": entry.written_at,
        }
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


# --------------------------------------------------------------- result cache (M6)

CACHEABLE_OUTCOMES = frozenset({"passed", "skipped", "xfailed", "xpassed"})
LOCKFILES = ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock", "requirements.txt")

# LRU thresholds for the combined result + collection caches (spec Section 5.9).
TRIM_HIGH_BYTES = 500 * 1024 * 1024
TRIM_LOW_BYTES = 400 * 1024 * 1024


def deps_hash(repo: Path, installed: list[str]) -> str:
    """SHA-256 of existing lockfiles (path-prefixed) then the installed-distribution list."""
    h = hashlib.sha256()
    for name in LOCKFILES:
        path = repo / name
        if path.is_file():
            h.update(name.encode("utf-8") + b"\n")
            h.update(path.read_bytes())
    h.update(b"installed\n")
    for line in sorted(installed):
        h.update(line.encode("utf-8") + b"\n")
    return h.hexdigest()


def conftest_chain(repo: Path, test_file: str) -> list[str]:
    """Every conftest.py from the test file's directory up to the repo root, inclusive."""
    found: list[str] = []
    parts = test_file.split("/")[:-1]
    for i in range(len(parts), -1, -1):
        rel = "/".join([*parts[:i], "conftest.py"])
        if (repo / rel).is_file():
            found.append(rel)
    return found


def result_cache_key(
    test_id: str,
    input_hashes: dict[str, str],
    pytest_args: tuple[str, ...],
    python: str,
    platform: str,
    deps: str,
) -> str:
    material: dict[str, Any] = {
        "test_id": test_id,
        "input_hashes": dict(sorted(input_hashes.items())),
        "pytest_args": list(pytest_args),
        "python": python,
        "platform": platform,
        "deps_hash": deps,
    }
    return hashlib.sha256(rfc8785.dumps(material)).hexdigest()


class ResultCache:
    """``<dir>/<key[:2]>/<key>.json`` holding one serialised TestResult."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("outcome") not in CACHEABLE_OUTCOMES:
            return None  # never replay a failure, whatever is on disk
        try:
            from proof_ci.models import TestResult

            TestResult.model_validate(data)
        except ValueError:
            return None  # corrupt or hand-edited entry: treat as a miss
        try:
            os.utime(path)  # LRU: a hit counts as a use
        except OSError:
            pass
        return data

    def put(self, key: str, result: dict[str, Any]) -> None:
        if result.get("outcome") not in CACHEABLE_OUTCOMES:
            raise ValueError(f"outcome {result.get('outcome')!r} must never be cached")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)


def trim_caches(directories: list[Path], high: int | None = None, low: int | None = None) -> int:
    """Delete least-recently-used entries across ``directories`` once over ``high`` bytes.

    Returns the number of files deleted.
    """
    high = TRIM_HIGH_BYTES if high is None else high
    low = TRIM_LOW_BYTES if low is None else low
    entries: list[tuple[float, int, Path]] = []
    for d in directories:
        if not d.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(d):
            for name in files:
                if not name.endswith(".json"):
                    continue
                path = Path(dirpath, name)
                try:
                    st = path.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, path))
    total = sum(size for _, size, _ in entries)
    if total <= high:
        return 0
    deleted = 0
    for _mtime, size, path in sorted(entries):
        if total <= low:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        deleted += 1
    return deleted
