# Cross-agent release checklist

A manual check before each release, done once in Claude Code and once in at least one other agent
(Cursor or Codex). It is a release activity, not an automated test.

| Step | Agent A | Agent B |
| --- | --- | --- |
| Install proof-ci into a fresh virtualenv for `examples/demo-project` (`pip install -e <proof-ci checkout>`) | [ ] | [ ] |
| Configure MCP stdio with the generic snippet from the README (or use the CLI with `--json`) | [ ] | [ ] |
| Ask the agent to run the demo's tests through proof-ci; it calls `run_affected_tests` or `proof-ci run --json` | [ ] | [ ] |
| The `RunSummary` reports `tests/test_math.py::test_div_wrong_expectation` with a repro command | [ ] | [ ] |
| The agent calls `explain_failure` instead of pasting the pytest log | [ ] | [ ] |
| Run with `sign: true` on a passing selection; `verify_record` (or `proof-ci record verify --pubkey ...`) returns `valid: true` | [ ] | [ ] |
| The agent describes the record as a tamper-evident statement, not proof the tests ran | [ ] | [ ] |
| The agent calls `get_savings_report` and does not invent cost figures | [ ] | [ ] |
| Set `PROOF_CI_AGENT_NAME`; the signed record's `payload.agent.agent_name` matches | [ ] | [ ] |
| Nothing in the setup required agent-specific configuration beyond the MCP snippet | [ ] | [ ] |

Record the agent names and versions used, the proof-ci commit, and anything that behaved
differently between the two agents.
