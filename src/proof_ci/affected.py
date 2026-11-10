"""Affected-test selection (spec Section 5.8).

Steps: resolve the base (1), compute changed files (2), conservative full-run
fallback with the incremental rule (3), a static Python import graph (4), map
tests to code (5), non-Python heuristics (6). Nothing here executes user code:
files are read as text and parsed with :func:`ast.parse` only.
"""

from __future__ import annotations

import ast
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
    "Hunk",
    "ImportGraph",
    "Selection",
    "Untraced",
    "changed_files",
    "changed_hunks",
    "decide_fallback",
    "glob_match",
    "is_test_file",
    "likely_cause",
    "loaded_conftests",
    "references_data_file",
    "resolve_base",
    "select",
    "since_tree",
    "untraced_changes",
]

IGNORED_DIRS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".hypothesis",
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
    extra_triggers: Iterable[tuple[str, str]] = (),
) -> FallbackDecision:
    """Step 3 including the incremental rule.

    ``extra_triggers`` are ``(path, why)`` pairs for changes selection cannot trace
    (see :func:`untraced_changes`); they fire the full run exactly like a glob match.
    ``tree_last`` must be the tree of the last full fresh run on the branch
    (:meth:`SavingsLedger.last_full`): the fallback is suppressed only when none of the
    triggering files changed since then, and the caller reruns that run's failures.
    """
    matches = [
        (path, f"{path} matched fallback glob '{g}'")
        for path in changed
        for g in globs
        if glob_match(path, g)
    ]
    matches.extend(extra_triggers)
    if not matches:
        return FallbackDecision(False, None)
    described = ", ".join(why for _, why in matches[:5]) + (
        f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
    )
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


def since_tree(repo: Path, tree_from: str, tree_to: str) -> set[str]:
    """Paths that differ between two tree objects (empty if git cannot diff them)."""
    proc = run_git(repo, "diff-tree", "-r", "--name-only", "-z", tree_from, tree_to, check=False)
    return {p for p in proc.stdout.split("\0") if p} if proc.returncode == 0 else set()


# --------------------------------------------------------- Step 4: import graph


def references_data_file(text: str, path: str) -> bool:
    """Whether a test file's text names a data file by repo-relative path or basename.

    Shared by selection (Step 6) and the result-cache key so the two cannot drift.
    """
    return path in text or PurePosixPath(path).name in text


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


def _repo_py_files(repo: Path) -> list[str]:
    """Tracked and untracked (not ignored) ``.py`` files; empty outside git."""
    try:
        proc = run_git(
            repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", check=False
        )
    except GitError:
        return []
    if proc.returncode != 0:
        return []
    out = []
    for path in proc.stdout.split("\0"):
        parts = path.split("/")
        if (
            path.endswith(".py")
            and not any(p in IGNORED_DIRS or p.endswith(".egg-info") for p in parts[:-1])
            and (repo / path).is_file()
        ):
            out.append(path)
    return out


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
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in node.targets
        ):
            # pytest loads these modules like imports (usually from conftest.py).
            values = (
                node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            )
            parsed.absolute.extend(
                v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
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
        self.texts: dict[str, str] = {}
        self._file_set = set(self.files)
        self._modules = self._module_map()
        self._tails = self._tail_map()
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
        # Every other Python file in the repository too: scripts, examples, tools and
        # servers that tests launch or put on sys.path. They are how code is reached
        # without an import from a test, which selection must notice (untraced_changes).
        files.update(_repo_py_files(repo))
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

    def _tail_map(self) -> dict[str, set[str]]:
        """Dotted tails of every file's repo path, for imports that only resolve through a
        ``sys.path`` entry we cannot see (``examples/x`` inserted by a test, say)."""
        tails: dict[str, set[str]] = defaultdict(set)
        for f in self.files:
            parts = list(PurePosixPath(f).with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            for k in range(1, len(parts) + 1):
                tail = parts[-k:]
                if all(part.isidentifier() for part in tail):
                    tails[".".join(tail)].add(f)
        return tails

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
        if not targets:
            # Not importable from a source root or the repo root: link every file that could
            # provide it from some other directory. Over-linking only selects more tests.
            for i in range(1, len(parts) + 1):
                targets |= self._tails.get(".".join(parts[:i]), set())
        return targets

    def _add_file(self, path: str) -> None:
        try:
            source = (self.repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.opaque.add(path)
            return
        self.texts[path] = source
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

    def closure(self, path: str) -> set[str]:
        """Every repository file ``path`` imports, directly or transitively."""
        seen: set[str] = set()
        queue = deque(self.deps.get(path, ()))
        while queue:
            f = queue.popleft()
            if f in seen:
                continue
            seen.add(f)
            queue.extend(self.deps.get(f, ()))
        seen.discard(path)
        return seen

    def reached_from(self, roots: Iterable[str]) -> set[str]:
        """``roots`` plus everything they import, transitively."""
        seen: set[str] = set()
        queue = deque(roots)
        while queue:
            f = queue.popleft()
            if f in seen:
                continue
            seen.add(f)
            queue.extend(self.deps.get(f, ()))
        return seen

    def _dependents(self, seeds: Iterable[str]) -> set[str]:
        result: set[str] = set()
        queue = deque(seeds)
        while queue:
            f = queue.popleft()
            if f in result:
                continue
            result.add(f)
            queue.extend(self.rdeps.get(f, ()))
        return result

    def affected_split(self, changed: Iterable[str]) -> tuple[set[str], set[str]]:
        """(statically affected files, files affected only through the dynamic-import rule).

        The second set matters to the result cache: those files do not have the changed
        file in their import closure, so their cache key would not change.
        """
        seeds = {c for c in changed if c.endswith(".py")}
        extra: set[str] = set()
        for x in seeds:
            top = self._top_package(x)
            for d in self.dynamic:
                if is_test_file(d) or self._top_package(d) == top:
                    extra.add(d)
        static = self._dependents(seeds)
        return static, self._dependents(extra) - static


# ---------------------------------------------- Steps 5-6: tests and data files


def loaded_conftests(graph: ImportGraph, test_files: Iterable[str]) -> set[str]:
    """conftest.py files pytest loads for these test files (their directory ancestors)."""
    dirs: set[str] = set()
    for tf in test_files:
        parent = PurePosixPath(tf).parent
        while True:
            dirs.add(parent.as_posix())
            if parent == parent.parent:
                break
            parent = parent.parent
    out = set()
    for f in graph.files:
        p = PurePosixPath(f)
        if p.name == "conftest.py" and p.parent.as_posix() in dirs:
            out.add(f)
    return out


# Files whose text can register a module as a pytest plugin or name it on the command line.
_INI_FILES = ("setup.cfg", "pytest.ini", "tox.ini")
# Directory names too generic to count as naming the files below them.
_GENERIC_DIRS = frozenset({"src", "lib", "test", "tests", "testing"})


@dataclass(frozen=True)
class Untraced:
    """What :func:`untraced_changes` found.

    ``triggers`` force the full run. ``extra_test_files`` are test files that launch
    code the import graph cannot follow (a subprocess, a file path, a dynamic loader);
    they are selected in addition to the import-graph selection and never read the cache.
    """

    triggers: list[tuple[str, str]]
    extra_test_files: frozenset[str] = frozenset()


def _name_patterns(graph: ImportGraph, path: str) -> list[re.Pattern[str]]:
    """Ways a file that launches ``path`` would have to spell it."""
    p = PurePosixPath(path)
    pats = [re.escape(path)]
    for dotted in graph._module_names(path):
        if p.name == "__main__.py":
            pkg = dotted.removesuffix(".__main__")
            if pkg and pkg != "__main__":
                esc = re.escape(pkg)
                pats.append(rf"""-m["',\s]+{esc}\b""")
                pats.append(rf"""run_module\(\s*["']{esc}\b""")
        elif "." in dotted:
            pats.append(re.escape(dotted))
    if p.stem not in ("__init__", "__main__"):
        pats.append(rf"\b{re.escape(p.stem)}\b")
    # Code below a plain directory (no __init__.py) is often loaded by directory: a tools/
    # folder scanned by path, an example project copied into a tmp dir.
    parent = p.parent
    roots = set(graph.roots)
    while parent.as_posix() not in ("", "."):
        d = parent.as_posix()
        if d in roots or (graph.repo / d / "__init__.py").is_file():
            break
        if parent.name not in _GENERIC_DIRS:
            esc = re.escape(parent.name)
            pats.append(rf"""["'/\\]{esc}["'/\\]""")
        parent = parent.parent
    return [re.compile(x) for x in pats]


def _dash_p(args: Iterable[str]) -> set[str]:
    """Module names given to pytest as ``-p NAME`` or ``-pNAME`` (``no:`` disables)."""
    out: set[str] = set()
    items = list(args)
    for i, arg in enumerate(items):
        name = items[i + 1] if arg == "-p" and i + 1 < len(items) else ""
        if arg.startswith("-p") and arg != "-p":
            name = arg[2:]
        if name and not name.startswith("no:"):
            out.add(name)
    return out


def _split_opts(value: object) -> list[str]:
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def plugin_modules(repo: Path) -> set[str]:
    """Modules pytest loads as plugins because the repository says so: ``pytest11``
    entry points and ``-p`` in pytest options (pyproject, setup.cfg, pytest.ini,
    tox.ini, proof-ci.toml). Other mentions of a module in config (lint or type-checker
    settings) do not make it a plugin."""
    import configparser
    import tomllib

    names: set[str] = set()

    def entry(value: object) -> None:
        if isinstance(value, str) and value.strip():
            names.add(value.split(":", 1)[0].strip())

    def load_toml(name: str) -> dict[str, object]:
        try:
            data = tomllib.loads((repo / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    pyproject = load_toml("pyproject.toml")
    project = pyproject.get("project")
    if isinstance(project, dict):
        eps = project.get("entry-points")
        if isinstance(eps, dict) and isinstance(eps.get("pytest11"), dict):
            for v in eps["pytest11"].values():
                entry(v)
    tool = pyproject.get("tool")
    if isinstance(tool, dict):
        pt = tool.get("pytest")
        if isinstance(pt, dict):
            for section in (pt, pt.get("ini_options")):
                if isinstance(section, dict):
                    names |= _dash_p(_split_opts(section.get("addopts")))
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            plugins = poetry.get("plugins")
            if isinstance(plugins, dict) and isinstance(plugins.get("pytest11"), dict):
                for v in plugins["pytest11"].values():
                    entry(v)
    ours = load_toml("proof-ci.toml").get("project")
    if isinstance(ours, dict):
        names |= _dash_p(_split_opts(ours.get("pytest_args")))
    for ini in _INI_FILES:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string((repo / ini).read_text(encoding="utf-8"))
        except (OSError, configparser.Error):
            continue
        for section in ("pytest", "tool:pytest"):
            if parser.has_option(section, "addopts"):
                names |= _dash_p(parser.get(section, "addopts").split())
        if parser.has_option("options.entry_points", "pytest11"):
            for line in parser.get("options.entry_points", "pytest11").splitlines():
                if "=" in line:
                    entry(line.split("=", 1)[1])
    return names


def _plugin_like(graph: ImportGraph, path: str, plugins: set[str]) -> bool:
    return any(n in plugins for n in graph._module_names(path))


def _names_it(text: str, pats: list[re.Pattern[str]]) -> bool:
    # A collect_ignore entry names a file precisely so pytest does NOT run it.
    return any(
        pt.search(line) for line in text.splitlines() if "collect_ignore" not in line for pt in pats
    )


def _tests_reaching(graph: ImportGraph, files: Iterable[str], tests: set[str]) -> set[str]:
    out: set[str] = set()
    for f in graph._dependents(files):
        if f in tests:
            out.add(f)
        elif PurePosixPath(f).name == "conftest.py":
            parent = PurePosixPath(f).parent.as_posix()
            out |= (
                tests if parent in ("", ".") else {t for t in tests if t.startswith(parent + "/")}
            )
    return out


def untraced_changes(
    graph: ImportGraph,
    changed: list[str],
    test_files: Iterable[str],
    source_roots: Iterable[str],
) -> Untraced:
    """Changed files whose effect on tests the import graph cannot see.

    proof-ci's rule is to run the full suite when unsure. An *orphan* is a Python file
    no collected test file (or conftest pytest loads for one) reaches by import. Code
    reaches tests through an orphan only if something runs the orphan, so for a changed
    Python file every orphan that is, or imports, it is checked:

    - registered as a plugin or named in pytest config (``pyproject.toml`` and friends):
      full run;
    - named by a file tests do reach (a subprocess command, a path, a ``-m`` module, a
      directory a loader scans), or loadable by a dynamic loader in its package: the
      tests reaching that launcher are added to the selection, uncached;
    - named by nothing tests reach: if the orphan *is* the changed file, full run (it may
      load in a way proof-ci cannot see); otherwise it is ignored, since tests cannot run
      it (benchmarks, release scripts, examples nobody launches) and the changed file's
      own importers are selected as usual.

    A changed non-Python file is a full-run trigger when it lives under a source root
    (package data) or when a non-test Python file names it (data read by code). Data
    files only a test file names are traced (Step 6 selects those tests).
    """
    tests = set(test_files)
    roots = tests | loaded_conftests(graph, tests)
    reached = graph.reached_from(roots)
    orphans = {f for f in graph.files if f not in reached}
    src = [r.strip("/") for r in source_roots if r.strip("/")]
    plugins = plugin_modules(graph.repo)
    triggers: list[tuple[str, str]] = []
    extra: set[str] = set()
    for path in changed:
        if path.endswith(".py"):
            if path in tests:
                continue
            reason: str | None = None
            for orphan in sorted(graph._dependents([path]) & orphans):
                via = "" if orphan == path else f" (imported by {orphan})"
                if _plugin_like(graph, orphan, plugins):
                    reason = f"{path}{via} is registered as a pytest plugin"
                    break
                pats = _name_patterns(graph, orphan)
                launchers = {
                    f for f in reached if f != orphan and _names_it(graph.texts.get(f, ""), pats)
                }
                top = graph._top_package(orphan)
                launchers |= {d for d in graph.dynamic & reached if graph._top_package(d) == top}
                if launchers:
                    extra |= _tests_reaching(graph, launchers, tests)
                elif orphan == path:
                    reason = (
                        f"{path} is not imported by any collected test and nothing tests "
                        "reach names it (it may load as a plugin, subprocess or by path)"
                    )
                    break
            if reason:
                triggers.append((path, reason))
            continue
        if any(path == r or path.startswith(r + "/") for r in src):
            triggers.append((path, f"{path} is package data under a source root"))
            continue
        readers = sorted(
            f
            for f in graph.files
            if f not in tests and references_data_file(graph.texts.get(f, ""), path)
        )
        if readers:
            triggers.append((path, f"{path} is named by non-test code {readers[0]}"))
    return Untraced(triggers, frozenset(extra))


@dataclass(frozen=True)
class Selection:
    selected: list[str]
    fallback: FallbackDecision
    unmatched_data_files: list[str]
    no_cache_files: frozenset[str] = frozenset()
    """Test files selected for a reason their cache key does not capture."""


def select(
    repo: Path,
    changed: list[str],
    collected: list[tuple[str, str]],
    graph: ImportGraph,
    fallback: FallbackDecision,
) -> Selection:
    """Select collected items. ``collected`` is ``(test_id, file)`` pairs in collection order."""
    if fallback.fired:
        return Selection([t for t, _ in collected], fallback, [])

    static, dynamic_only = graph.affected_split(changed)
    affected = static | dynamic_only
    test_files = {f for _, f in collected}

    # A conftest.py is loaded for every test below it, so a conftest that is changed, or
    # that imports something that changed, affects that whole subtree. A changed root
    # conftest already fires the full-run fallback; an affected one selects everything.
    conftests = {c for c in affected if PurePosixPath(c).name == "conftest.py"}
    conftests |= {c for c in changed if PurePosixPath(c).name == "conftest.py"}
    for conf in conftests:
        parent = PurePosixPath(conf).parent.as_posix()
        if parent in ("", "."):
            affected |= test_files
        else:
            affected |= {f for f in test_files if f.startswith(parent + "/")}

    # Step 6: non-Python files referenced by a test file's text.
    unmatched: list[str] = []
    texts: dict[str, str] = {}
    for path in changed:
        if path.endswith(".py"):
            continue
        hit = False
        for tf in test_files:
            if tf not in texts:
                try:
                    texts[tf] = (repo / tf).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    texts[tf] = ""
            if references_data_file(texts[tf], path):
                affected.add(tf)
                hit = True
        if not hit:
            unmatched.append(path)

    selected_files = sorted(f for f in test_files if f in affected)
    keep = set(selected_files)
    no_cache = frozenset(f for f in selected_files if f in dynamic_only and f not in static)
    return Selection([t for t, f in collected if f in keep], fallback, unmatched, no_cache)


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
