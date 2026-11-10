"""Generic agent identity resolution (spec Section 3.5, AgentAdapter).

Reads only ``PROOF_CI_AGENT_*`` variables and the CLI/config agent id. It never
reads vendor account variables (``ANTHROPIC_*``, ``OPENAI_*``, ...) and never
special-cases a vendor name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["DEFAULT_AGENT_ID", "Identity", "resolve_identity"]

DEFAULT_AGENT_ID = "local"


@dataclass(frozen=True)
class Identity:
    id: str
    agent_name: str | None = None
    agent_version: str | None = None
    model: str | None = None
    session: str | None = None
    defaulted: bool = False
    """True when no id was supplied and :data:`DEFAULT_AGENT_ID` was used."""

    def as_payload(self) -> dict[str, str]:
        out = {"id": self.id}
        for name in ("agent_name", "agent_version", "model", "session"):
            value = getattr(self, name)
            if value:
                out[name] = value
        return out


def resolve_identity(
    agent_id: str | None, config_agent_id: str, environ: Mapping[str, str]
) -> Identity:
    """``--agent-id`` > ``PROOF_CI_AGENT_ID`` > ``[record].agent_id`` > ``"local"``."""
    chosen = agent_id or environ.get("PROOF_CI_AGENT_ID") or config_agent_id or ""
    return Identity(
        id=chosen or DEFAULT_AGENT_ID,
        agent_name=environ.get("PROOF_CI_AGENT_NAME") or None,
        agent_version=environ.get("PROOF_CI_AGENT_VERSION") or None,
        model=environ.get("PROOF_CI_AGENT_MODEL") or None,
        session=environ.get("PROOF_CI_AGENT_SESSION") or None,
        defaulted=not chosen,
    )
