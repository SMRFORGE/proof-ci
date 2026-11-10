"""Names and helpers shared by the pytest plugin and the runner. Standard library only.

The plugin process and the proof-ci process agree on these; keeping them here
means the runner does not import pytest and the plugin does not import proof-ci's
dependencies.
"""

from __future__ import annotations

__all__ = ["COLLECT_ENV", "EXC_PROPERTY", "RESULTS_ENV", "quote_node_id", "repro_command"]

COLLECT_ENV = "PROOF_CI_COLLECT_FILE"
RESULTS_ENV = "PROOF_CI_RESULTS_FILE"
EXC_PROPERTY = "proof_ci_exc"

_QUOTE_TRIGGERS = frozenset("[] \"'$`")


def quote_node_id(node_id: str) -> str:
    """Double-quote a node id when a shell would mangle it (spec Section 5.20 rule 6)."""
    if any(ch in _QUOTE_TRIGGERS for ch in node_id):
        return '"' + node_id.replace('"', '\\"') + '"'
    return node_id


def repro_command(node_id: str) -> str:
    """``pytest -q <node id>`` with shell-safe quoting (spec Section 4.2)."""
    return f"pytest -q {quote_node_id(node_id)}"
