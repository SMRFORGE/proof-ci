"""Content-addressed caches under ``.proof-ci/`` (spec Sections 5.8 Step 5 and 5.9).

M5 adds the collection cache. The result cache (M6) lands here too.
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
    "CollectCache",
    "CollectEntry",
    "collect_cache_key",
    "file_sha256",
    "pytest_config_file",
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


def collect_cache_key(
    repo: Path,
    test_paths: tuple[str, ...],
    pytest_args: tuple[str, ...],
    test_command: tuple[str, ...],
) -> str:
    test_files: dict[str, str] = {}
    for root in test_paths:
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
                items=list(data["items"]),
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
