"""Agent-facing docs stay identical and keep the guidance the spec requires (Section 5.18)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agents_and_claude_md_are_identical() -> None:
    assert (ROOT / "AGENTS.md").read_bytes() == (ROOT / "CLAUDE.md").read_bytes()


def test_agent_guide_covers_required_points() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for needle in (
        "run_affected_tests",
        "proof-ci run --json",
        "savings_headline",
        "explain_failure",
        "run --failed",
        "confirmed",
        "--sign",
        "tamper-evident",
        "private key",
        "PROOF_CI_AGENT_NAME",
        "data contracts",
    ):
        assert needle in text, needle


def test_readme_does_not_lead_with_money() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    intro = readme.split("## What this is", 1)[0].lower()
    assert "ci minutes" not in intro and "token" not in intro and "$" not in intro
    assert "not a security sandbox" in readme.lower()
    assert "proof the tests ran" not in readme.lower().replace("not prove", "")
