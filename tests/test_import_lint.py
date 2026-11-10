"""Vendor neutrality: core modules must not import vendor SDKs (spec R35/R36, Section 3.5).

An AST scan (so a test can inject a violation into a copy of the tree) backs up
the import-linter contract in pyproject.toml.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "proof_ci"
FORBIDDEN = {"github", "gitlab", "atlassian", "msrest", "azure", "anthropic", "openai", "mcp"}


def violations(package_root: Path) -> list[str]:
    """``file: module`` for every direct import of a forbidden top-level module in core."""
    found: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        rel = path.relative_to(package_root).as_posix()
        if rel.startswith("adapters/"):
            continue  # adapters may import vendor SDKs; agent may import mcp
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            found.extend(f"{rel}: {n}" for n in names if n.split(".")[0] in FORBIDDEN)
    return found


def test_core_has_no_vendor_imports() -> None:
    assert violations(SRC) == []


def test_injected_core_import_is_caught(tmp_path: Path) -> None:
    copy = tmp_path / "proof_ci"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    with (copy / "runner.py").open("a", encoding="utf-8") as fh:
        fh.write("\nimport anthropic  # injected\n")
    with (copy / "cache.py").open("a", encoding="utf-8") as fh:
        fh.write("\nfrom github import Github  # injected\n")
    assert violations(copy) == ["cache.py: github", "runner.py: anthropic"]


def test_adapters_may_import_vendor_sdks(tmp_path: Path) -> None:
    copy = tmp_path / "proof_ci"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    with (copy / "adapters" / "forge" / "__init__.py").open("a", encoding="utf-8") as fh:
        fh.write("\nimport github  # allowed in an adapter\n")
    assert violations(copy) == []


def test_mcp_only_in_agent_adapter() -> None:
    users = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "from mcp" in p.read_text(encoding="utf-8")
        or "import mcp" in p.read_text(encoding="utf-8")
    )
    assert users == ["adapters/agent/mcp_server.py"]
