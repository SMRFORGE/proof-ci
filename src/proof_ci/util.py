"""Git helpers, timestamps, and path canonicalisation shared by core modules.

Git rules come from spec Section 4.7 (tree hash, git >= 2.0 preflight) and
Section 5.20 rule 1 (repo-relative forward-slash paths in git's committed case).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "CaseIndex",
    "GitError",
    "GitInfo",
    "debug",
    "git_info",
    "git_preflight",
    "run_git",
    "tree_hash",
    "utc_now_iso",
    "write_json_atomic",
]


class GitError(Exception):
    """git is missing, too old, or failed. Callers map it to exit 3 when git is required."""


def debug(message: str) -> None:
    """Write a diagnostic line to stderr when ``PROOF_CI_DEBUG=1``."""
    if os.environ.get("PROOF_CI_DEBUG") == "1":
        sys.stderr.write(f"proof-ci debug: {message}\n")


def write_json_atomic(path: Path, data: object) -> None:
    """Write JSON (UTF-8, 2-space indent, trailing newline) via a temp file and rename,
    so a concurrent reader (the MCP server, another CLI) never sees a partial file."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def run_git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("git")
    if exe is None:
        raise GitError("git not found on PATH")
    try:
        proc = subprocess.run(
            [exe, *args],
            cwd=repo,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            input=stdin,
            # Never let git share the caller's stdin (it is the MCP transport).
            stdin=subprocess.DEVNULL if stdin is None else None,
        )
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


_VERSION_RE = re.compile(r"git version (\d+)\.(\d+)")


def git_preflight(repo: Path) -> str:
    """Return the git version string; raise :class:`GitError` if missing or < 2.0."""
    out = run_git(repo, "--version").stdout.strip()
    match = _VERSION_RE.search(out)
    if not match or int(match.group(1)) < 2:
        raise GitError(f"git >= 2.0 required; found {out or 'unknown'}")
    return out


def is_git_repo(repo: Path) -> bool:
    try:
        proc = run_git(repo, "rev-parse", "--is-inside-work-tree", check=False)
    except GitError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def tree_hash(repo: Path) -> str:
    """Hash of the working tree as tested, without touching the real index (Section 4.7)."""
    index = run_git(repo, "rev-parse", "--git-path", "index").stdout.strip()
    index_path = Path(index)
    if not index_path.is_absolute():
        index_path = repo / index_path
    fd, tmp_name = tempfile.mkstemp(prefix="proof-ci-index-")
    os.close(fd)
    tmp = Path(tmp_name).resolve()
    try:
        if index_path.exists():
            shutil.copyfile(index_path, tmp)
        else:
            tmp.unlink()  # git creates a fresh index when the file is absent
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp)}
        # proof-ci's own state is never part of the tested tree, ignored or not.
        run_git(repo, "add", "-A", env=env)
        run_git(repo, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", ".proof-ci", env=env)
        # `add -A` honours .git/info/exclude and core.excludesFile, which are local
        # to this machine. A file hidden that way could change test outcomes while
        # the record claims the committed tree, so untracked files are also added
        # by the in-tree .gitignore rules alone (proof-ci's own state excepted).
        extra = run_git(
            repo, "ls-files", "-z", "--others", "--exclude-per-directory=.gitignore", env=env
        ).stdout
        paths = [
            p
            for p in extra.split("\0")
            if p and not p.endswith("/") and not (p == ".proof-ci" or p.startswith(".proof-ci/"))
        ]
        if paths:
            run_git(repo, "update-index", "--add", "-z", "--stdin", env=env, stdin="\0".join(paths))
        return run_git(repo, "write-tree", env=env).stdout.strip()
    finally:
        tmp.unlink(missing_ok=True)


@dataclass(frozen=True)
class GitInfo:
    sha: str | None
    tree: str | None
    dirty: bool | None


def git_info(repo: Path) -> GitInfo:
    """Commit sha, working-tree hash, and dirty flag; all None when git is unavailable."""
    try:
        git_preflight(repo)
    except GitError:
        return GitInfo(None, None, None)
    if not is_git_repo(repo):
        return GitInfo(None, None, None)
    head = run_git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False)
    sha = head.stdout.strip() if head.returncode == 0 else None
    tree = tree_hash(repo)
    # Dirty means "the tree as tested differs from HEAD's tree", by the same rules
    # as the tree hash (not `git status`, which honours machine-local excludes).
    head_tree = run_git(repo, "rev-parse", "--verify", "-q", "HEAD^{tree}", check=False)
    dirty = head_tree.returncode != 0 or head_tree.stdout.strip() != tree
    return GitInfo(sha=sha, tree=tree, dirty=dirty)


class CaseIndex:
    """Maps repo-relative paths to git's committed spelling (Section 5.20 rule 1)."""

    def __init__(self, tracked: list[str]) -> None:
        self._exact = set(tracked)
        self._by_fold: dict[str, str] = {}
        for path in tracked:
            self._by_fold.setdefault(path.casefold(), path)
            # Directories too, so "Tests/test_x.py" maps through "tests/".
            parts = path.split("/")
            for i in range(1, len(parts)):
                prefix = "/".join(parts[:i])
                self._by_fold.setdefault(prefix.casefold(), prefix)

    @classmethod
    def from_repo(cls, repo: Path) -> CaseIndex:
        try:
            out = run_git(repo, "ls-files", "-z", check=False)
        except GitError:
            return cls([])
        if out.returncode != 0:
            return cls([])
        return cls([p for p in out.stdout.split("\0") if p])

    def canonical(self, path: str) -> str:
        if path in self._exact:
            return path
        return self._by_fold.get(path.casefold(), path)

    def canonical_node_id(self, node_id: str) -> str:
        file_part, sep, rest = node_id.partition("::")
        return self.canonical(file_part) + sep + rest
