"""Affected-test selection (spec Section 5.8).

Steps: resolve the base (1), compute changed files (2), conservative full-run
fallback with the incremental rule (3), a static Python import graph (4), map
tests to code (5), non-Python heuristics (6). Nothing here executes user code:
files are read as text and parsed with :func:`ast.parse` only.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from proof_ci.util import GitError, run_git

__all__ = [
    "IGNORED_DIRS",
    "BaseResolution",
    "FallbackDecision",
    "ImportGraph",
    "Selection",
    "changed_files",
    "decide_fallback",
    "glob_match",
    "is_test_file",
    "last_tree_path",
    "read_last_tree",
    "resolve_base",
    "select",
    "write_last_tree",
]

IGNORED_DIRS = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".git",
        ".proof-ci",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
    }
)

DEFAULT_BRANCH_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")


# ---------------------------------------------------------------- Step 1: base


@dataclass(frozen=True)
class BaseResolution:
    base_setting: str
    base_ref: str
    """Resolved full sha of the diff base."""
    default_branch: str | None = None


def _verify(repo: Path, ref: str) -> str | None:
    proc = run_git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def resolve_base(repo: Path, setting: str, dirty: bool) -> BaseResolution:
    """Resolve ``--base`` / ``[affected].base`` to a commit sha (Step 1)."""
    head = _verify(repo, "HEAD")
    if head is None:
        raise GitError("the repository has no commits yet; commit once or use `run --all`")
    if setting != "auto":
        sha = _verify(repo, setting)
        if sha is None:
            raise GitError(f"base {setting!r} is not a valid git ref")
        return BaseResolution(base_setting=setting, base_ref=sha)

    def head_or_parent() -> str:
        if dirty:
            return head
        return _verify(repo, "HEAD~1") or head

    for candidate in DEFAULT_BRANCH_CANDIDATES:
        if _verify(repo, candidate) is None:
            continue
        merge_base = run_git(repo, "merge-base", "HEAD", candidate, check=False).stdout.strip()
        if merge_base and merge_base != head:
            return BaseResolution("auto", merge_base, candidate)
        return BaseResolution("auto", head_or_parent(), candidate)
    return BaseResolution("auto", head_or_parent(), None)


# --------------------------------------------------------- Step 2: changed set


def changed_files(repo: Path, base_ref: str) -> list[str]:
    """Files changed between ``base_ref`` and the working tree, plus untracked files."""
    diff = run_git(
        repo, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=ACMRD", base_ref
    )
    untracked = run_git(repo, "ls-files", "-z", "--others", "--exclude-standard")
    paths = {p for p in (diff.stdout + untracked.stdout).split("\0") if p}
    # .proof-ci/ itself (spec Step 2) and build/cache directories that are never
    # test inputs (__pycache__, .venv, ...; the Step 4 ignore list).
    return sorted(p for p in paths if not any(part in IGNORED_DIRS for part in p.split("/")[:-1]))


# ------------------------------------------------------------ Step 3: fallback


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(path: str, pattern: str) -> bool:
    """fnmatch semantics plus ``**`` against the full repo-relative path.

    ``*`` does not cross ``/``, so ``conftest.py`` matches only the root file
    and ``requirements*.txt`` only root requirement files.
    """
    return _glob_regex(pattern).match(path) is not None


def last_tree_path(repo: Path, branch: str) -> Path:
    """Stub used until the savings ledger exists (milestone M5 -> M9)."""
    digest = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:16]
    return repo / ".proof-ci" / f"last-tree-{digest}.txt"


def read_last_tree(repo: Path, branch: str) -> str | None:
    path = last_tree_path(repo, branch)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def write_last_tree(repo: Path, branch: str, tree: str) -> None:
    path = last_tree_path(repo, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tree + "\n", encoding="utf-8", newline="\n")


@dataclass(frozen=True)
class FallbackDecision:
    fired: bool
    reason: str | None
    suppressed_by_tree: str | None = None


def decide_fallback(
    repo: Path,
    changed: list[str],
    globs: Iterable[str],
    *,
    tree_now: str | None,
    tree_last: str | None,
) -> FallbackDecision:
    """Step 3 including the incremental rule."""
    matches = [(path, g) for path in changed for g in globs if glob_match(path, g)]
    if not matches:
        return FallbackDecision(False, None)
    described = ", ".join(f"{p} matched fallback glob '{g}'" for p, g in matches)
    if tree_now and tree_last and tree_now != tree_last:
        proc = run_git(
            repo, "diff-tree", "-r", "--name-only", "-z", tree_last, tree_now, check=False
        )
        if proc.returncode == 0:
            differing = {p for p in proc.stdout.split("\0") if p}
            if not {p for p, _ in matches} & differing:
                return FallbackDecision(
                    False, f"suppressed: {described}", suppressed_by_tree=tree_last
                )
        # diff-tree failed (e.g. the old tree object was pruned): fire, conservatively.
    elif tree_now and tree_last and tree_now == tree_last:
        return FallbackDecision(False, f"suppressed: {described}", suppressed_by_tree=tree_last)
    return FallbackDecision(True, f"full run because {described}")


# --------------------------------------------------------- Step 4: import graph


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _walk_py(repo: Path, start: Path) -> Iterable[str]:
    if not start.is_dir():
        return
    stack = [start]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in IGNORED_DIRS and not entry.name.endswith(".egg-info"):
                    stack.append(entry)
            elif entry.suffix == ".py":
                yield entry.relative_to(repo).as_posix()


def _is_dynamic(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("__import__", "import_module"):
                return True
            if isinstance(func, ast.Attribute) and func.attr == "import_module":
                return True
    return False


@dataclass
class _Parsed:
    absolute: list[str] = field(default_factory=list)
    relative: list[tuple[int, str | None, list[str]]] = field(default_factory=list)
    dynamic: bool = False


def _parse_imports(source: str) -> _Parsed | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    parsed = _Parsed(dynamic=_is_dynamic(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            parsed.absolute.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names if a.name != "*"]
            if node.level == 0 and node.module:
                parsed.absolute.append(node.module)
                parsed.absolute.extend(f"{node.module}.{n}" for n in names)
            elif node.level > 0:
                parsed.relative.append((node.level, node.module, names))
    return parsed


class ImportGraph:
    """Static, directed import graph over repository Python files."""

    def __init__(self, repo: Path, files: Iterable[str], roots: Iterable[str]) -> None:
        self.repo = repo
        self.files = sorted(set(files))
        self.roots = [r.strip("/") for r in roots if (repo / r).is_dir()]
        self.deps: dict[str, set[str]] = defaultdict(set)
        self.rdeps: dict[str, set[str]] = defaultdict(set)
        self.dynamic: set[str] = set()
        self.opaque: set[str] = set()
        self._file_set = set(self.files)
        self._modules = self._module_map()
        for f in self.files:
            self._add_file(f)

    @classmethod
    def build(
        cls,
        repo: Path,
        source_roots: Iterable[str],
        test_paths: Iterable[str],
        extra: Iterable[str] = (),
    ) -> ImportGraph:
        roots = [*source_roots, *test_paths]
        files: set[str] = set()
        for root in roots:
            files.update(_walk_py(repo, repo / root))
        for path in extra:
            parts = PurePosixPath(path).parts
            # Deleted files stay in the graph (opaque) so their importers are still found.
            if path.endswith(".py") and not any(p in IGNORED_DIRS for p in parts):
                files.add(path)
        return cls(repo, files, roots)

    def _module_names(self, path: str) -> list[str]:
        """Dotted names under which ``path`` can be imported (per root, and from the repo root)."""
        names = []
        p = PurePosixPath(path)
        for root in [*self.roots, ""]:
            if root:
                try:
                    rel = p.relative_to(root)
                except ValueError:
                    continue
            else:
                rel = p
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if parts and all(part.isidentifier() for part in parts):
                names.append(".".join(parts))
        return names

    def _module_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for f in self.files:
            for name in self._module_names(f):
                # Prefer a package's __init__.py and the first root that provides it.
                mapping.setdefault(name, f)
        return mapping

    def _resolve_relative(
        self, path: str, level: int, module: str | None, names: list[str]
    ) -> set[str]:
        base = PurePosixPath(path).parent
        for _ in range(level - 1):
            base = base.parent
        targets: set[str] = set()
        mod_parts = module.split(".") if module else []
        base_mod = base.joinpath(*mod_parts) if mod_parts else base
        for candidate in (
            base_mod.with_suffix(".py") if mod_parts else None,
            base_mod / "__init__.py",
        ):
            if candidate is not None and candidate.as_posix() in self._file_set:
                targets.add(candidate.as_posix())
        for n in names:
            for candidate in ((base_mod / n).with_suffix(".py"), base_mod / n / "__init__.py"):
                if candidate.as_posix() in self._file_set:
                    targets.add(candidate.as_posix())
        return targets

    def _resolve_absolute(self, dotted: str) -> set[str]:
        targets: set[str] = set()
        parts = dotted.split(".")
        # `import a.b.c` executes a, a.b and a.b.c: depend on each one that is ours.
        for i in range(1, len(parts) + 1):
            hit = self._modules.get(".".join(parts[:i]))
            if hit is not None:
                targets.add(hit)
        return targets

    def _add_file(self, path: str) -> None:
        try:
            source = (self.repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.opaque.add(path)
            return
        parsed = _parse_imports(source)
        if parsed is None:
            self.opaque.add(path)
            return
        if parsed.dynamic:
            self.dynamic.add(path)
        targets: set[str] = set()
        for dotted in parsed.absolute:
            targets |= self._resolve_absolute(dotted)
        for level, module, names in parsed.relative:
            targets |= self._resolve_relative(path, level, module, names)
        targets.discard(path)
        for t in targets:
            self.deps[path].add(t)
            self.rdeps[t].add(path)

    def _top_package(self, path: str) -> str:
        p = PurePosixPath(path)
        for root in self.roots:
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            return f"{root}/{rel.parts[0]}" if rel.parts else root
        return p.parts[0] if p.parts else ""

    def affected_files(self, changed: Iterable[str]) -> set[str]:
        """Changed files plus every transitive dependent, with dynamic-import conservatism."""
        seeds = {c for c in changed if c.endswith(".py")}
        extra: set[str] = set()
        for x in seeds:
            top = self._top_package(x)
            for d in self.dynamic:
                if is_test_file(d) or self._top_package(d) == top:
                    extra.add(d)
        result: set[str] = set()
        queue = deque(seeds | extra)
        while queue:
            f = queue.popleft()
            if f in result:
                continue
            result.add(f)
            queue.extend(self.rdeps.get(f, ()))
        return result


# ---------------------------------------------- Steps 5-6: tests and data files

_DOC_SUFFIXES = (".md", ".rst", ".txt")


def _is_skippable_doc(path: str) -> bool:
    p = PurePosixPath(path)
    if p.suffix.lower() not in _DOC_SUFFIXES and p.name.upper() not in (
        "LICENSE",
        "COPYING",
        "NOTICE",
    ):
        return False
    return len(p.parts) == 1 or p.parts[0] == "docs"


@dataclass(frozen=True)
class Selection:
    selected: list[str]
    affected_test_files: list[str]
    fallback: FallbackDecision
    unmatched_data_files: list[str]


def select(
    repo: Path,
    changed: list[str],
    collected: list[tuple[str, str]],
    graph: ImportGraph,
    fallback: FallbackDecision,
) -> Selection:
    """Select collected items. ``collected`` is ``(test_id, file)`` pairs in collection order."""
    if fallback.fired:
        files = sorted({f for _, f in collected})
        return Selection([t for t, _ in collected], files, fallback, [])

    affected = graph.affected_files(changed)
    test_files = {f for _, f in collected}

    # Nested conftest.py: select every test under that directory (Step 3 special case).
    for path in changed:
        p = PurePosixPath(path)
        if p.name == "conftest.py" and len(p.parts) > 1:
            prefix = p.parent.as_posix() + "/"
            affected |= {f for f in test_files if f.startswith(prefix)}

    # Step 6: non-Python files referenced by a test file's text.
    unmatched: list[str] = []
    texts: dict[str, str] = {}
    for path in changed:
        if (
            path.endswith(".py")
            or PurePosixPath(path).name == "conftest.py"
            or _is_skippable_doc(path)
        ):
            continue
        basename = PurePosixPath(path).name
        hit = False
        for tf in test_files:
            if tf not in texts:
                try:
                    texts[tf] = (repo / tf).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    texts[tf] = ""
            if path in texts[tf] or basename in texts[tf]:
                affected.add(tf)
                hit = True
        if not hit:
            unmatched.append(path)

    selected_files = sorted(f for f in test_files if f in affected)
    keep = set(selected_files)
    return Selection([t for t, f in collected if f in keep], selected_files, fallback, unmatched)


# ------------------------------------------------------------- likely cause


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_FRAME_RE = re.compile(r"^([^\s:<>\"][^:\n<>\"]*\.py):(\d+):", re.MULTILINE)


@dataclass(frozen=True)
class Hunk:
    start: int
    end: int
    header: str


def changed_hunks(repo: Path, base_ref: str, changed: list[str]) -> dict[str, list[Hunk]]:
    """New-side line ranges changed since ``base_ref``; untracked files count as wholly new."""
    hunks: dict[str, list[Hunk]] = defaultdict(list)
    py = [c for c in changed if c.endswith(".py")]
    if not py:
        return {}
    proc = run_git(
        repo, "diff", "-U0", "--no-renames", "--no-color", base_ref, "--", *py, check=False
    )
    current: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else None
        elif current is not None:
            m = _HUNK_RE.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                if count:
                    hunks[current].append(
                        Hunk(start, start + count - 1, line.split(" @@")[0] + " @@")
                    )
    tracked_diff = set(hunks)
    for path in py:
        if path not in tracked_diff and (repo / path).is_file():
            ls = run_git(repo, "ls-files", "--error-unmatch", "--", path, check=False)
            if ls.returncode != 0:  # untracked: the whole file is new
                hunks[path].append(Hunk(1, 10**9, "new file"))
    return dict(hunks)


def likely_cause(
    traceback: str, hunks: dict[str, list[Hunk]], base_ref: str
) -> dict[str, object] | None:
    """The innermost traceback frame that falls inside a changed hunk (spec R23)."""
    if not hunks or not traceback:
        return None
    found: dict[str, object] | None = None
    for m in _FRAME_RE.finditer(traceback):
        path = m.group(1).replace("\\", "/").removeprefix("./")
        line = int(m.group(2))
        for hunk in hunks.get(path, ()):
            if hunk.start <= line <= hunk.end:
                found = {
                    "file": path,
                    "line": line,
                    "diff_hunk": hunk.header,
                    "rationale": f"traceback frame {path}:{line} is inside a change since {base_ref[:12]}",
                }
    return found
