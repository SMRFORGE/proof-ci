# proof-ci Build Specification

**An agent-native CI layer**

| Field | Value |
| --- | --- |
| Document version | 1.2.0 |
| Date | 9 November 2026 (America/New_York) |
| Status | Ready for implementation |
| Audience | Claude Code (an AI coding assistant) and Chris Whalen |
| Working name | `proof-ci` |
| Software license | MIT |
| Primary language (Phase 1) | Python 3.11+ (pytest first) |
| Phase 1 platforms | Linux (x86-64; aarch64 expected), macOS (Intel and Apple silicon), Windows 10/11 x64 (Section 5.20) |

This document is a **build specification**, not an implementation and not marketing copy. It exists so Claude Code can implement `proof-ci` in small, ordered milestones without guessing. Example JSON, schemas, commands, and file trees in this document are the contracts to implement, not descriptions of software that already exists.

If a later implementation choice would change a data contract in [Section 4](#4-data-contracts), stop and ask Chris before changing it.

## Changes in 1.2.0

Revision 1.2.0 applies the economics and operating-system review Chris approved on 9 November 2026. `schema_version` stays `"1.0.0"` for the same reason as in 1.1.0 (nothing has shipped). Contract changes in this revision are confined to `Savings`, `SavingsReport`, the `selection` object, and the savings ledger.

1. **Positioning leads with agent turns, not CI minutes or token dollars** (Sections 1.2, 1.3 P13/R29–R34, 5.18, 10.4). The value proposition is: dependents the agent did not know about, a flake-aware verdict, a signed record of what ran, and fewer wasted turns. CI-minute and token-dollar figures are reported but never lead. Section 1.2 gains a short alternatives paragraph (`pytest --lf`, pytest-testmon, pytest-picked, pytest-xdist, Trunk, Datadog, Buildkite, Nx).
2. **Savings contract made honest** (Sections 4.5, 4.8, 5.4, 5.12, 5.16, 5.19, 8 M9, 10.1). `compute` is now `local_compute` with `test_ms_not_executed`, priced only when `[savings].price_local_minutes = true` (default false) at a separate `local_compute_cost_per_minute`; `ci_runs_avoided` is counted once per distinct failing set + tree per branch (`dedupe_key`); `tokens` is measured from pytest's actual stdout at the configured verbosity and may be negative (`tokens_delta`); `wait` carries `baseline_kind` and is capped at `medium` for the local-suite proxy; `defaults_used` means keys absent from the TOML; `prices_configured` gates every dollar figure; the headline separates time from money and shows dollars only when all prices are configured; `calibration.payoff` tells the user whether selection can pay off for this repository.
3. **Incremental full-run fallback and cache bypass** (Sections 4.3, 4.6, 5.8 Step 3, 5.9, 5.12 ledger, 8 M5/M6). A fallback glob fires only when a matching file differs between the current tree and the tree recorded for the most recent run on this branch; the cache bypass follows the same rule. `selection.fallback_suppressed_by_tree` records a suppression; the ledger stores `commit_tree`.
4. **Collection cache** (Sections 3.4, 5.7.1, 5.8 Step 5, 5.10, 8 M5). Collected node ids are cached under `.proof-ci/collect-cache/` keyed by test-file, `conftest.py`, pytest-config, `pytest_args`, and `test_command` hashes, with an environment-consistency rule.
5. **`run --failed`** (Sections 4.3, 4.6, 5.7.3, 5.14, 8 M4): rerun the tests that failed or errored in the last run; `selection.mode` gains `failed`.
6. **pytest-xdist passthrough** (Sections 5.10, 8 M4): documented and tested; the controller-side `logreport` assumption is marked to-verify in M4.
7. **Calibrate payoff preflight** (Sections 5.7.8, 5.12): `savings calibrate` measures the fixed overhead and prints whether selection is worth it for this repository.
8. **Operating-system fixes** (Sections 4.7, 5.4, 5.7.1, 5.8, 5.10, 5.17, 5.20). `test_command` is a TOML array (string form split with `posix=(os.name != "nt")`); tree hash uses `git rev-parse --git-path index` and an absolute `GIT_INDEX_FILE`; git >= 2.0 preflight; key-mode check has an owner-plus-filesystem-type exception for mode-less filesystems; repo paths are canonicalised to git's committed case before entering contracts; container rules (`init --no-key`, tolerant config dir, root-ownership warning, `--user` guidance, WAL fallback); explicit support matrix (distros, Alpine, aarch64, Apple silicon, Windows 10/11, Windows ARM, BSD unsupported, Python 3.13 required leg, optional `ubuntu-24.04-arm` leg); WSL2 paragraph; Windows long paths and `.exe` resolution; `USERNAME` before `os.getlogin()`.
9. **Phase 2 pricing** (Sections 1.3 R33, 1.4, 3.2, 6.1, 6.12, 6.13, 8 P2-M9): share-of-verified-savings is replaced by flat per-committer pricing that leads with team flake history and the record store / status checks / signer registry; the shared cache is an included extra. No other Phase 2 or Phase 3 scope changed.

The v1.1.0 files are archived unchanged under `v1.1.0/`, and v1.0.0 under `v1.0.0/`.

## Changes in 1.1.0

Revision 1.1.0 applies the review decisions Chris approved on 9 November 2026. Data contracts changed in this revision keep `schema_version` `"1.0.0"` because no implementation has shipped yet; the first shipped release freezes them.

1. **What a signature proves** (Sections 1.2, 1.3 R28/R32, 2.2, 3.3, 4.7, 5.7.5, 5.7.6, 6.7, 10.1). A signed `RunRecord` is now described as tamper-evident and attributable, not as proof that tests executed. `proof-ci record sign` only signs artifacts produced by `proof-ci run` (no `--input` of arbitrary files). Verification checks the embedded key against `[record].trusted_pubkeys` or `--pubkey` and reports `signer: "known" | "unknown"`; `--require-known-key` fails on `unknown`.
2. **Records bind to the tested tree and the selection** (Sections 4.3, 4.6, 4.7, 5.7.3). `payload.commit.tree` is required and defined as the git tree hash of the working tree as tested; `RunSummary` gains the optional `commit_tree` it is copied from, and `selection` gains `base_setting`. `payload.selection` (same shape as `RunSummary.selection`) is required. `payload.summary` gains required `executed` and `replayed` counts.
3. **Default base is `auto`, and zero-test runs are never signed** (Sections 5.4, 5.7.3, 5.8 Step 1 and Step 7, 5.14, 8 M5). `[affected].base` defaults to `auto` with a defined fallback for clean trees and for being on the default branch. A run that selected zero tests cannot be signed (`--sign` exits 2; `record sign` refuses).
4. **Cache keys cover every input the selector treats as significant, and failures are not cached** (Sections 3.2, 5.9, 5.10, 5.11, 5.16, 8 M6). `input_hashes` now include the `conftest.py` chain, the pytest configuration file, and non-Python files referenced by the test file; `deps_hash` includes the installed-distribution list of the interpreter that runs pytest; cache reads are bypassed when the full-run fallback fires; only `passed`, `skipped`, `xfailed`, and `xpassed` are cached.
5. **pytest runs as a subprocess only** (Sections 3.4, 5.7.3, 5.8 Step 5, 5.10, 8 M4). Collection and execution go through `[project].test_command` in a subprocess; the plugin writes JSON to the file named by `PROOF_CI_COLLECT_FILE` / `PROOF_CI_RESULTS_FILE`. `proof-ci` must be installed in the project's own environment.
6. **Plugin loading is `-p` only** (Sections 5.2, 5.3, 5.10). The `pytest11` entry point is removed; the runner always passes `-p proof_ci.pytest_plugin`.
7. **`--json`, `--repo`, `--config` are accepted on every leaf command** (Section 5.7).
8. **Native Windows 11 is a supported Phase 1 platform** (Sections 2.1, 4, 4.7, 5.10, 5.17, 5.18, 5.19, 5.20, 8 M1/M8/M12, 10.1). New Section 5.20 lists the normative platform rules: UTF-8 text I/O, forward-slash repo paths, owner-only ACL for the key on Windows and what the readable-key check does there, `platformdirs` with `appauthor=False`, `report.location` normalisation, `repro_command` quoting, `windows-latest` in the project's CI matrix, and the MCP configuration example for Windows.
9. **Dependency pin and MCP output form** (Sections 5.3, 5.14). `mcp>=1.2,<2` with the reason stated; every tool returns its JSON object as a single text content block (plus `structuredContent` when the SDK supports it).
10. **Open question added** (Section 10.2): whether a later revision should wrap records in an in-toto test-result predicate / DSSE envelope.

The v1.0.0 markdown and Word files are archived unchanged under `v1.0.0/` next to this file.

---

## Table of contents

- [Changes in 1.2.0](#changes-in-120)
- [Changes in 1.1.0](#changes-in-110)

1. [Purpose and first principles](#1-purpose-and-first-principles)
2. [Non-goals and design principles](#2-non-goals-and-design-principles)
3. [System architecture](#3-system-architecture)
4. [Data contracts](#4-data-contracts)
5. [Phase 1 — local CLI and MCP server](#5-phase-1--local-cli-and-mcp-server)
6. [Phase 2 — hosted service](#6-phase-2--hosted-service)
7. [Phase 3 — enterprise](#7-phase-3--enterprise)
8. [Milestone plan](#8-milestone-plan)
9. [Ready-to-paste Claude Code prompts](#9-ready-to-paste-claude-code-prompts)
10. [Risks, open questions, and success metrics](#10-risks-open-questions-and-success-metrics)

---

## 1. Purpose and first principles

### 1.1 The problem, stated from first principles

Continuous integration was designed around a human workflow:

1. A trusted person writes code for hours or days.
2. They push a small number of commits.
3. A shared runner eventually executes a pipeline.
4. The person reads red/green output and a wall of logs.
5. They decide what to do next.

That design embeds assumptions that were reasonable for humans and are false for coding agents.

| Assumption baked into classical CI | Agent reality |
| --- | --- |
| Commits are infrequent (a few per author per day). | An agent may make tens or hundreds of small attempts while solving one task. |
| Waiting several minutes per attempt is acceptable. | An agent that waits minutes per attempt wastes wall-clock time and context. Feedback needs to be on the order of seconds. |
| Logs are written for a human to skim. | An agent that ingests unstructured logs burns tokens, misses the causal line, and may “fix” the wrong thing. |
| The actor pushing code is a person with judgment about secrets and production. | An agent can be steered by untrusted repository content (prompt injection) and must not be handed long-lived secrets. |
| A binary pass/fail is enough of a verdict. | An agent needs an actionable diagnosis: which test, what error, where, what changed, how to reproduce, and whether the test is simply flaky. |
| Flaky tests are an annoyance the human learns to ignore. | An agent treats a red test as a defect in *its* change. It will rewrite working code or loop. |
| “It passed in CI” is trusted because a human kicked it off in a known environment. | At agent volume, “it passed somewhere” is not evidence. Downstream systems need a verifiable record of *what* was tested, *where*, and *with what result*. |
| YAML pipelines edited by humans are an acceptable interface. | Agents edit YAML poorly, and pipeline files that both run code and hold secrets are a dangerous blend. |
| Identity is the human’s git author or the repo’s bot account. | Bot commits blur accountability: who prompted the change, in which session, against which tree? |
| Merge queues are sized for human throughput. | Concurrent agent branches collide more often; naive “merge when green” thrashes. |

`proof-ci` exists because those assumptions no longer hold. It does not exist to replace GitHub Actions or GitLab CI. Those systems remain the slow, trusted pipeline of record. `proof-ci` sits **in front of** them: agents check work here, in seconds, with structured results; only work that passes is worth promoting to the expensive trusted pipeline. Existing CI still does the final run.

### 1.2 Why a layer, not a replacement

Replacing hosted CI is a non-starter:

- Organizations already trust GitHub Actions and GitLab CI for billing, compliance, required checks, and deployment.
- Those vendors sell runner minutes. They have a structural conflict of interest in making the inner loop dramatically faster and cheaper for agents.
- Agents do not need a new place to deploy from. They need a fast, structured, least-privilege **check layer** they can call locally (and later, remotely) through a stable interface.

The product precedent is the same pattern as Turborepo, Nx, and Docker: a free local tool that is useful alone, with paid value in shared history and team/enterprise controls. An MCP server by itself is easy to copy and hard to monetize. Shared data (team flake history first, remote cache as an extra) and signed, tamper-evident, attributable records of what each check reported (Section 4.7, "What a signature proves") are the moat.

**What the user actually gets, in order of value.** (1) Tests the agent did not know to run: transitive dependents of the change, selected conservatively. (2) A flake-aware verdict, so the agent stops rewriting good code to satisfy an unreliable test. (3) One structured failure object with a repro command instead of a log to scrape. (4) A signed record of exactly what was checked (tree hash and selection). (5) Fewer wasted agent turns, which is where the real time and money go. Wall-clock savings matter only when the full suite takes longer than about a minute; CI-minute and token-dollar savings are small and are reported, not led with (Section 5.12).

**Alternatives a user already has, and why this is different.** `pytest --lf` reruns last failures (sub-second, free) but misses regressions in tests that passed last time. Running "the relevant file" relies on the agent guessing the dependents. `pytest-picked` runs changed test files only. `pytest-testmon` selects by recorded coverage (more precise than an import graph, catches dynamic imports) but gives no structured output, no flake model, no record, and slows the measuring run. `pytest-xdist` parallelises and is complementary (Section 5.10 passes it through). Hosted flake and test-analytics products (Trunk Flaky Tests, Datadog Test Optimization, Buildkite Test Engine) classify flakes from CI history and are priced per committer or per test execution; Nx Cloud sells a remote build cache. None of them give a coding agent a local, offline, structured, flake-aware verdict in one tool call with a signed record. `proof-ci` is worth installing where the suite is big enough for selection to matter (roughly a full suite over a minute) or where flakes and structured failures are the pain; for a ten-second suite the honest advice is "run pytest".

### 1.3 Pain points, derived requirements

Each requirement below is derived from a pain point. Do not add scope that does not trace to one of these.

**P1. Slow feedback.** Classical CI queues and cold runners commonly take minutes. An agent inner loop that depends on that queue is unusable.

- **R1.** Select and run only the tests affected by the current change.
- **R2.** Cache test results by a content hash of their inputs so unchanged work is not rerun.
- **R3.** Prefer a warm execution environment over a cold VM per attempt.
- **R4.** Target: for a typical single-file Python change on a warm local machine, `proof-ci run` should return structured results in under 30 seconds. This is a design target, not a claimed benchmark.

**P2. Unstructured, human-oriented logs.** Agents that scrape pytest or CI logs waste context and guess at causes.

- **R5.** Every failure is a structured object: test id, error, file and line, likely cause in the current diff, a copy-pasteable reproduction command, and a flaky flag.
- **R6.** Every run produces a short summary suitable for an agent context window, with a way to drill into one failure.

**P3. Flaky tests.** A test that fails on unchanged code causes agents to “fix” innocent code or retry forever.

- **R7.** Persist a history of results keyed by test identity and the content hash of the code that test depends on.
- **R8.** Automatically flag tests that both pass and fail under the same code hash.
- **R9.** Surface that flag on subsequent failures so the agent can distinguish “your change broke this” from “this test is unreliable.”

**P4. Local and CI drift.** Agents cannot cheaply run “exactly what CI would run” before pushing.

- **R10.** Define the check environment and command once.
- **R11.** Discover candidate test commands by reading existing `.gitlab-ci.yml` and GitHub workflow files, without executing deploy or production jobs.
- **R12.** Phase 1 guarantees the same command and a recorded environment fingerprint. Phase 2 guarantees the same provisioned environment (warm sandboxes / snapshots).

**P5. Cost and concurrency designed for human volume.** Per-minute pricing and low concurrency assume a few pipelines per developer per day.

- **R13.** The default path is local and free: no account, no network, no per-minute billing.
- **R14.** Remote execution and shared cache (Phase 2) exist to speed teams, not to be required for a single agent on a laptop.

**P6. Coarse secrets and prompt-injection risk.** A confused or injected agent with long-lived cloud credentials is unacceptable.

- **R15.** Phase 1 ships no secrets broker and never prints signing private keys.
- **R16.** Phase 3 issues short-lived, least-privilege credentials scoped to one agent task.
- **R17.** Destructive actions (deploys, production data, pipeline definition changes) require a human approval gate.
- **R18.** Every privileged action is attributable to an agent, a task, and the prompt/session that requested it.

**P7. Fragile YAML as the only interface.** Agents should not have to synthesize CI YAML to get a signal.

- **R19.** The primary interface is a CLI and an MCP server with typed tools.
- **R20.** Repository configuration is a small TOML file, not a second pipeline language.

**P8. Blurred identity for bot commits.** “github-actions[bot] committed” is not accountability.

- **R21.** Each signed run record names a generic agent identity (`id`, `agent_name`, optional `agent_version`, optional `model`, optional `session`) alongside the git commit. It MUST NOT use vendor-specific field names.

**P9. Merge collisions at agent volume.** Many small green branches still conflict.

- **R22.** Phase 3 provides a high-volume merge queue with batching and automatic bisect on batch failure. Phase 1 does not implement a merge queue.

**P10. Pass/fail is not a diagnosis.** A red check without a cause forces the agent to re-read logs.

- **R23.** Failures include a best-effort `likely_cause` pointing at a file and line in the current diff when a traceback frame intersects that diff.
- **R24.** An `explain_failure` MCP tool returns a short, agent-oriented explanation and suggested next steps — not a novel-length log dump.

**P11. Slow environment provisioning.** Cold containers and service startup dominate short test runs.

- **R25.** Phase 2 maintains a pool of warm sandboxes matching the defined environment, including service snapshots where configured.

**P12. No verifiable proof a change was tested.** “The agent said it ran the tests” is not evidence a merge rule can trust.

- **R26.** Produce an Ed25519-signed JSON run record covering the commit and the tree as tested, the selection, checks run, results, environment, agent id, and savings.
- **R27.** Provide a local (Phase 1) and public (Phase 2) verify command/endpoint.
- **R28.** Branch protection / merge rules can require a record that verifies against a trusted signer key (Phase 2 status checks; Phase 3 policy). A valid signature proves the record was produced by the holder of that key and has not been altered since. It is not proof that tests executed; see Section 4.7.

**P13. Invisible cost.** Teams cannot see what agent work costs in wasted turns, waiting, model tokens, and flake-chasing, and tools that claim savings rarely show how the number was made.

- **R29.** Every run reports what `proof-ci` did and did not have to do, in this order of importance: wrong turns avoided (known-flaky failures flagged and not chased), full CI runs avoided (a defect caught locally before a push, counted once per distinct failure), local test time not executed (deselected and cached tests, as minutes of the user's machine, not CI minutes), wait versus the full local suite, and tokens versus pytest's own output at the configured verbosity (which may be negative on a green run).
- **R30.** Each metric is labeled `measured` or `estimated` and carries a `confidence` of `high`, `medium`, or `low`. Baselines come from periodic full-suite calibration that stores per-test durations. Until a calibration exists, mark the affected metrics as estimated with low confidence. Never invent a baseline.
- **R31.** Prices live in `proof-ci.toml` under `[savings]` (`ci_cost_per_minute` for avoided CI runs, model input/output `$` per million tokens, `wasted_fix_cost`, optional `engineer_hourly_cost`, and an opt-in `local_compute_cost_per_minute` that is 0 unless the user prices their own machine's minutes). Dollar figures appear only when the user has configured every price that was applied; documented defaults are clearly marked as defaults, not market quotes.
- **R32.** The savings object is embedded in the signed `RunRecord` payload so it cannot be altered after signing without detection. Altering it MUST fail `proof-ci record verify`. Records are signed only from artifacts that `proof-ci run` produced (Section 5.7.5).
- **R33.** Surface savings as a one-line summary after every run (time first; money only when prices are configured), a `proof-ci savings` report (with `--since`, `--by`, `--json`, and a weekly roll-up), an MCP tool `get_savings_report`, and a local SQLite ledger. Phase 2 adds a team dashboard, a status-check summary line, and team-wide aggregation. Phase 2 pricing is flat per committer (Section 6.12), not a share of these figures.
- **R34.** Never claim aggregate or public savings figures except those computed from signed run records (or a ledger derived from them). Marketing copy and dashboards follow the same rule.

**P14. Vendor lock-in.** A layer that only works with one coding agent, one git host, one CI product, or one model provider cannot serve teams that mix tools, and it makes the project hostage to that vendor. This document itself is handed to Claude Code; that must not leak into the software’s design.

- **R35.** The core is vendor-free. Vendor-specific behavior lives in adapters behind `AgentAdapter` surfaces, `ForgeAdapter`, `CICommandSource`, and `TestRunnerAdapter` (Section 3.5). Adding a new agent, forge, CI config format, or test runner MUST NOT require editing selection, caching, flakes, savings, or signing logic.
- **R36.** Core modules MUST NOT import vendor SDKs. An import-lint test (import-linter or an equivalent AST check) runs in CI and fails the build if they do.
- **R37.** Agent-facing docs ship both `AGENTS.md` (generic) and `CLAUDE.md` with identical guidance. MCP tool descriptions MUST NOT name or assume a specific agent.
- **R38.** Conformance tests drive the CLI and MCP server as a generic client. Acceptance includes a manual checklist covering Claude Code and at least one other agent (for example Cursor or Codex). Phase 2 accepts signed records from any agent or forge and posts status to GitHub and GitLab equally through `ForgeAdapter`.

### 1.4 Traceability

| Pain point | Requirements | Component(s) | Phase |
| --- | --- | --- | --- |
| P1 Slow feedback | R1, R2, R3, R4 | Affected-test selector; result cache; warm sandboxes | R1–R2, R4 in Phase 1; R3 in Phase 2 |
| P2 Unstructured logs | R5, R6 | pytest plugin; `TestResult` / `RunSummary`; MCP `get_failure_details` | Phase 1 |
| P3 Flaky tests | R7, R8, R9 | Flake store; detection algorithm; `proof-ci flakes` | Phase 1 local; Phase 2 shared |
| P4 Local/CI drift | R10, R11, R12 | Config; CI discovery; environment fingerprint; sandboxes | Discovery + fingerprint in Phase 1; identical sandboxes in Phase 2 |
| P5 Cost / concurrency | R13, R14 | Local CLI (default); hosted cache/runners | Phase 1 local; Phase 2 hosted |
| P6 Secrets / injection | R15–R18 | Key handling; credentials broker; approval gates; audit log | R15 in Phase 1; R16–R18 in Phase 3 |
| P7 Fragile YAML | R19, R20 | CLI; MCP server; `proof-ci.toml` | Phase 1 |
| P8 Blurred identity | R21 | `RunRecord.payload.agent` | Phase 1 (field); Phase 2 (storage) |
| P9 Merge collisions | R22 | Merge queue | Phase 3 |
| P10 Pass/fail not a diagnosis | R23, R24 | `Failure.likely_cause`; `explain_failure` | Phase 1 |
| P11 Slow provisioning | R25 | Warm sandbox pool | Phase 2 |
| P12 No verifiable proof | R26, R27, R28 | Record signer/verifier (tree hash + selection in payload; trusted signer keys); hosted storage; status checks | Sign/verify in Phase 1; storage + checks + signer registry in Phase 2 |
| P13 Invisible cost | R29–R34 | Savings estimator; calibration store (with payoff verdict); savings ledger; `proof-ci savings`; MCP `get_savings_report`; signed `RunRecord.payload.savings` | Phase 1 local; Phase 2 dashboard and status line (pricing is flat per committer, Section 6.12) |
| P14 Vendor lock-in | R35–R38 | Vendor-free core; adapters (Section 3.5); import-lint; generic agent identity; `AGENTS.md` + `CLAUDE.md`; generic-client conformance | Phase 1 core and first adapters; Phase 2 multi-forge status |

### 1.5 Who the user is

Phase 1 has two users, both first-class:

1. **A coding agent** calling MCP tools over stdio or invoking the CLI with `--json`. First-class MCP clients include Claude Code, Cursor, Codex, Windsurf, Cline, and VS Code Copilot agent mode. Any shell-capable agent can use the CLI. The software MUST treat them as interchangeable clients of the same interface.
2. **A human developer** (Chris, and later other contributors) running the same CLI on a laptop, reading human output on stderr, and inspecting JSON artifacts when needed.

The agent is not a second-class wrapper around a human UI. Machine-readable output is the primary interface. Human-readable output is a projection of the same data.

---

## 2. Non-goals and design principles

### 2.1 Non-goals

The following are explicitly out of scope. Do not implement them unless a later revision of this specification says otherwise.

1. **Not a replacement for GitHub Actions, GitLab CI, Jenkins, or Buildkite.** Those remain the pipeline of record for required checks, deployments, and compliance. `proof-ci` never becomes “the place production deploys from.”
2. **Not a zero-knowledge proving system.** Signed run records are plain Ed25519 signatures over canonical JSON. There is no ZK circuit, no SNARK, no “prove execution without revealing the log.”
3. **Not a new test framework.** Phase 1 wraps pytest. It does not invent an assertion API or replace pytest plugins the user already relies on.
4. **Not a general monorepo build system.** `proof-ci` is not Bazel, Nx, or Turborepo. It may later adopt similar caching ideas for tests; it does not schedule arbitrary build graphs in Phase 1.
5. **Not an agent.** `proof-ci` does not plan work, edit source files, or propose patches. It runs checks and returns structured facts.
6. **Not an automatic repair tool.** It may point at a likely cause in the diff. It does not rewrite the user’s code.
7. **Not a secrets manager in Phase 1.** No vault, no cloud credentials, no OIDC broker until Phase 3. Phase 1 only handles a local Ed25519 signing key.
8. **Not multi-language in Phase 1.** The data contracts are language-agnostic. The first runner is Python/pytest only. JavaScript, Go, Rust, and others are future work.
9. **Not remote execution in Phase 1.** Phase 1 runs on the machine that invoked it (the agent’s sandbox or the developer’s laptop).
10. **Not a GUI or web app in Phase 1.**
11. **Not a Linux-only tool.** Phase 1 officially supports Linux, macOS, and native Windows 11 (not WSL-only). The normative platform rules are in Section 5.20; the project's own CI runs on all three (Section 5.17). WSL is a supported way to run the Linux build on a Windows machine, not a substitute for native support.
12. **Not a requirement that users create an account.** Phase 1 is fully useful offline, with no telemetry and no phoning home.
13. **Not tied to any single agent, forge, CI system, or model provider.** `proof-ci` is not “a Claude Code plugin,” not “a GitHub App only,” and not a wrapper around one vendor’s model API. First implementations may support a subset of adapters (pytest, GitHub Actions, GitLab CI, MCP stdio); the core and the contracts stay vendor-neutral from day one.

### 2.2 Design principles

These principles settle disputes when the specification is silent on a minor detail.

1. **Machine-readable first.** Every command that produces results writes JSON artifacts. `--json` puts the primary object on stdout. Human text goes to stderr when `--json` is set, so agents can parse stdout safely.
2. **Deterministic.** The same tree, the same tests, and the same environment fingerprint should produce the same selection, the same cache keys, and (for a deterministic test) the same outcome. Flakes are recorded precisely because they violate this.
3. **Conservative selection over optimistic selection.** It is better to run too many tests than to skip a test that could have failed. When the import graph is uncertain, fall back toward running more tests, not fewer.
4. **Local-first.** Features must work without a network. Hosted services accelerate or share; they do not own the workflow.
5. **Least privilege.** Default configuration has no cloud credentials and cannot deploy anything. Discovery of CI files is read-only and never executes deploy/release/publish jobs.
6. **Verifiable.** A record that claims to report a check can be checked with `proof-ci record verify` using only the record file and a public key. The check proves integrity and signer identity (when the key is trusted), not execution; Section 4.7 states exactly what a signature proves.
7. **Small, stable contracts.** JSON schemas in Section 4 are the compatibility boundary. Implementation internals may change freely; schemas may not, without an explicit version bump and a question to Chris.
8. **Fail clearly.** Distinct exit codes for test failure, usage error, runtime error, and verification failure. Never exit 0 on a real (non-flaky, or flaky-but-not-allowed) test failure.
9. **Do not lie about isolation.** Phase 1 runs tests the same way `pytest` would on that machine. It does not sandbox filesystem, network, or process access. Phase 2 sandboxes do.
10. **Honest accounting.** Label every savings figure `measured` or `estimated`, show confidence, and show when a default price was used. Do not publish aggregate savings that are not backed by signed records.
11. **Vendor neutral.** Put vendor-specific code behind adapters (Section 3.5). Do not import vendor SDKs from core modules. Do not name a particular agent in MCP tool descriptions, data-contract field names, or CLI flags. Prefer generic identity fields (`agent_name`, `agent_version`, `model`) over vendor account identifiers.

---

## 3. System architecture

### 3.1 Position in the workflow

`proof-ci` is a layer between coding agents and existing CI.

```mermaid
flowchart TB
  subgraph Agents
    CC[Claude Code]
    CU[Cursor]
    CX[Codex]
    HUM[Human developer]
  end

  subgraph ProofCI["proof-ci (this project)"]
    MCP["MCP server (stdio)"]
    CLI["CLI (proof-ci)"]
    AFF[Affected-test selector]
    RUN[Test runner and pytest plugin]
    CACHE[Result cache]
    FLAKE[Flake store]
    REC[Record signer / verifier]
    DISC[CI config discovery]
    SAV[Savings estimator and ledger]
    MCP --- CLI
    CLI --> AFF
    CLI --> RUN
    CLI --> CACHE
    CLI --> FLAKE
    CLI --> REC
    CLI --> DISC
    CLI --> SAV
    AFF --> RUN
    RUN --> CACHE
    RUN --> FLAKE
    RUN --> SAV
    SAV --> REC
    RUN --> REC
  end

  subgraph Phase2["Phase 2 hosted service (optional)"]
    API[HTTPS API]
    RC[Remote cache]
    TF[Team flake history]
    RS[Record store and verify endpoint]
    SB[Warm sandbox pool]
    ST[GitHub / GitLab status checks]
    DASH[Savings dashboard]
    API --> RC
    API --> TF
    API --> RS
    API --> SB
    API --> ST
    API --> DASH
  end

  subgraph ExistingCI["Existing CI of record"]
    GHA[GitHub Actions]
    GL[GitLab CI]
  end

  CC -->|stdio MCP| MCP
  CU -->|stdio MCP| MCP
  CX -->|stdio MCP| MCP
  HUM --> CLI
  CLI -.->|Phase 2 only| API
  REC -->|signed RunRecord| ExistingCI
  RUN -->|promote only passing work| ExistingCI
  ST --> ExistingCI
```

Inner-loop sequence:

```mermaid
sequenceDiagram
  participant A as Coding agent
  participant M as proof-ci MCP / CLI
  participant T as pytest
  participant F as Flake store
  participant S as Record signer
  participant C as Existing CI

  A->>M: run_affected_tests
  M->>M: git diff + import graph
  M->>M: cache lookup
  M->>T: run selected cache-miss tests
  T-->>M: structured TestResult list
  M->>F: record outcomes; flag flips
  M->>M: compute savings vs calibration
  M-->>A: RunSummary plus savings headline
  opt agent asks why
    A->>M: explain_failure(test_id)
    M-->>A: diagnosis + repro command
  end
  opt results are green
    M->>S: sign RunRecord
    A->>C: push / open MR
    C->>C: trusted full pipeline still runs
  end
```

### 3.2 Component responsibilities

| Component | Responsibility | Phase 1 | Later |
| --- | --- | --- | --- |
| CLI | Parses flags, loads `proof-ci.toml`, writes artifacts, sets exit codes, prints human/JSON output. | Yes | Adds `--remote` behavior |
| MCP server | Exposes the six tools over stdio using the official MCP Python SDK. No network listener in Phase 1. | Yes | Same tools, optional remote backend |
| Config loader | Reads `proof-ci.toml`, applies defaults, resolves paths relative to the repo root. | Yes | Remote workspace settings |
| Affected-test selector | Computes the changed-file set, builds a Python import graph, maps tests to code, applies conservative fallbacks, returns pytest node ids. | Yes | Other languages |
| Result cache | Computes content-hash keys, stores passing results (never `failed` or `error` in Phase 1), answers hit/miss. | Local filesystem | Remote shared cache |
| Test runner | Invokes pytest as a subprocess through `[project].test_command` with the proof-ci plugin, reads the plugin's results file, enforces output size limits, redacts likely secrets in captured output. | Yes | Remote sandbox execution |
| pytest plugin | Emits one `TestResult` per item via pytest hooks into the file named by `PROOF_CI_RESULTS_FILE`; fills `Failure`; the runner fills `likely_cause`. | Yes | — |
| Flake store | SQLite history plus derived `FlakeRecord` rows; CLI to list/show/mark/unmark. | Local SQLite | Team-wide store |
| CI discovery | Parses GitHub workflow YAML and GitLab CI YAML; extracts likely test commands; never executes them. | Yes | Broader heuristics |
| Record signer/verifier | RFC 8785 canonicalization, Ed25519 sign and verify, keygen and key loading. The signed payload includes `savings` once that subsystem exists. | Local keys | Hosted storage + public verify URL |
| Savings estimator | Computes per-run savings, maintains calibration (including the payoff verdict) and a local ledger, prints a one-line headline, serves `proof-ci savings`. | Yes | Team dashboard, status-check line |
| Credentials broker | Short-lived scoped tokens per task. | No | Phase 3 |
| Approval gates | Human approval for deploys, production data, pipeline edits. | No | Phase 3 |
| Merge queue | Batching and auto-bisect. | No | Phase 3 |
| Audit log | Per agent, task, and prompt. | No (Phase 1 has only local signed records) | Phase 3 |

### 3.3 Trust boundaries

1. **Repository content is untrusted input.** Test code, fixtures, CI YAML, and markdown in the repo may contain prompt-injection text. `proof-ci` must not treat repo content as instructions to itself, and it must not execute discovered CI jobs other than the configured test command.
2. **The agent is untrusted with secrets.** Phase 1 therefore has no cloud secrets to leak. The only sensitive file is the local signing private key.
3. **Existing CI remains the trust root for merges and deploys.** A signed `proof-ci` record is evidence of what the `proof-ci` installation that held the signing key reported about a fast check, bound to a tree hash and a selection. In Phase 1 the signing key is readable by the agent process that runs `proof-ci`, so the record attests what that installation reported, under a key that agent could use. It is not a reason to skip the configured required CI pipeline unless a human later chooses to encode that policy. Proof that tests executed in a controlled environment requires Phase 2 remote execution with a server-held key.
4. **Phase 2 API boundaries are multi-tenant.** One workspace must not read another workspace’s cache, flakes, or records.
5. **Phase 1 does not isolate test processes** from the host beyond what pytest already does. Document this in the README so nobody mistakes `proof-ci run` for a security sandbox.

### 3.4 Artifact layout at runtime

Inside a target repository (not inside the `proof-ci` source tree):

```
<repo>/
  proof-ci.toml                 # committed
  .proof-ci/
    cache/                     # gitignored; content-addressed results
    collect-cache/             # gitignored; cached pytest collection (Section 5.8 Step 5)
    flakes.sqlite              # gitignored; local flake history
    savings.sqlite             # gitignored; savings ledger + calibration
    last-run.json              # gitignored; most recent RunSummary + result paths
    runs/<run_id>/
      summary.json
      results.json             # array of TestResult
      record.json              # unsigned or signed RunRecord
      collect.json             # written by the plugin in collect mode (Section 5.10)
      plugin-results.json      # written by the plugin in run mode (Section 5.10)
    logs/                      # optional captured output, gitignored
```

All text files under `.proof-ci/` are written and read as UTF-8 regardless of platform (Section 5.20).


`proof-ci init` adds the gitignored paths to the repo’s `.gitignore` if they are not already present.

### 3.5 Vendor-neutral core and adapters

The implementation is a **vendor-free core** plus **adapters** that sit behind shared interfaces. Core modules own selection, caching, flakes, savings, signing, configuration, and orchestration. Adapters own anything that mentions a particular agent product, git host, CI YAML dialect, or test runner beyond the language-agnostic `TestResult` contract.

```
src/proof_ci/                  # core: no vendor SDK imports
src/proof_ci/adapters/
  agent/                       # identity + MCP stdio surface
  forge/                       # GitHub, GitLab first
  ci/                          # GitHub Actions, GitLab CI first
  runners/                     # pytest first
```

The official MCP Python SDK (`mcp`) is a protocol library, not a model-vendor SDK. It MAY be imported from `proof_ci.adapters.agent` (or from `mcp_server.py` if that module lives under `adapters/agent/`). It MUST NOT be imported from savings, record, flakes, cache, or affected-test code. Forge SDKs (`github`, `gitlab`, and later Bitbucket/Azure DevOps/Gitea clients), model-provider SDKs (`anthropic`, `openai`, and similar), and any other vendor client library are forbidden in core and allowed only inside the matching adapter package. Enforcement: an import-lint test (import-linter or an equivalent AST scan listed in `tests/test_import_lint.py`) runs in the project’s own CI and fails the build on violations.

#### AgentAdapter (how any agent consumes proof-ci)

There is one agent-facing contract, not one class per product. Any of the following clients is equally valid: Claude Code, Cursor, Codex, Windsurf, Cline, VS Code Copilot agent mode, or any shell-capable agent.

Surfaces (all three are required in Phase 1):

1. **MCP over stdio** — the tool list and JSON schemas in Section 5.14. Tool descriptions MUST NOT name a particular agent.
2. **CLI with `--json`** — stdout is a parseable object; human text goes to stderr.
3. **Files under `.proof-ci/`** — `last-run.json`, `runs/<run_id>/summary.json`, `results.json`, and `record.json`.

Identity resolution (no vendor-specific environment variables):

- `resolve_identity(cli_args, environ) -> {id, agent_name, agent_version, model, session}`
- Reads `PROOF_CI_AGENT_ID`, `PROOF_CI_AGENT_NAME`, `PROOF_CI_AGENT_VERSION`, `PROOF_CI_AGENT_MODEL`, `PROOF_CI_AGENT_SESSION`, plus `--agent-id`. Core MUST NOT read `ANTHROPIC_*`, `OPENAI_*`, or other vendor account variables to populate identity.

#### ForgeAdapter

First implementations: GitHub and GitLab. Later: Bitbucket, Azure DevOps, Gitea. Phase 1 only needs detection and remote parsing (via `git` and URL parsing, no vendor SDK). `post_status` is required in Phase 2.

```
name: str   # "github" | "gitlab" | "bitbucket" | "azure_devops" | "gitea"
detect(repo_root: Path) -> bool
parse_remote(url: str) -> RemoteInfo   # host, owner, name, html_url
default_branch(remote: RemoteInfo) -> str
post_status(remote, sha, context, state, description, target_url) -> None
    # state is pending | success | failure | error
    # context, description, and target_url are forge-agnostic
```

Phase 2 status checks MUST call `post_status` on every configured `ForgeAdapter` with the same fields so GitHub and GitLab are treated equally. Adapter internals may map `post_status` onto GitHub Check Runs or GitLab commit statuses; that mapping stays inside the adapter.

#### CICommandSource

First implementations: GitHub Actions and GitLab CI. Later: Jenkins, CircleCI, Buildkite.

```
name: str
detect(repo_root: Path) -> bool
discover(repo_root: Path) -> list[DiscoveredCommand]
    # DiscoveredCommand: command, confidence ("high"|"medium"),
    #   source_path, job_name, skipped_reason | null
```

Discovery remains read-only and must not execute discovered scripts (Section 5.13). Core asks every detected source for candidates and ranks them; it does not parse `.github/workflows` or `.gitlab-ci.yml` itself.

#### TestRunnerAdapter

First implementation: pytest. Later: jest/vitest, `go test`, `cargo test`.

```
name: str
supports(repo_root: Path) -> bool
collect(repo_root, paths, extra_args) -> list[TestItem]   # test_id, file, line
run(repo_root, test_ids, extra_args) -> list[TestResult]
repro_command(test_id, extra_args) -> str
```

The pytest plugin in Section 5.10 is the first `TestRunnerAdapter`. The language-agnostic contracts in Section 4 do not mention pytest except as an example of how `test_id` looks.

---

## 4. Data contracts

All JSON objects produced or consumed by `proof-ci` MUST validate against the schemas in this section (JSON Schema draft 2020-12). Schema files live in the repository at `src/proof_ci/schemas/` and are the source of truth copied from this document.

Rules that apply to every contract:

- `schema_version` is the string `"1.0.0"` for this revision.
- Unknown fields MUST be rejected on write (`additionalProperties: false`) so we do not silently persist typos. Readers MAY ignore unknown fields when `schema_version` has a greater minor/patch (forward compatibility for a later 1.x), but Phase 1 writers never emit unknown fields.
- Paths are repo-relative, using forward slashes (`tests/test_foo.py`), even on Windows. Convert with `PurePath(p).as_posix()` at every boundary where a path enters a contract (Section 5.20).
- All JSON, TOML, SQL text, and `.gitignore` files are read and written as UTF-8 with `\n` line endings, on every platform. Never rely on the platform default encoding.
- Timestamps are UTC in RFC 3339 form with a `Z` suffix, millisecond precision allowed, e.g. `2026-11-08T04:26:00.000Z`.
- Durations are integers of milliseconds. **Signed payloads MUST NOT contain floating-point numbers.** This keeps canonicalization unambiguous.
- Output captured from tests is truncated to 8192 characters per stream, after redaction.

### 4.1 Shared definitions

Use these `$defs` conceptually in every schema (they may be duplicated per file or published as a shared document).

```json
{
  "$defs": {
    "schema_version": {
      "const": "1.0.0"
    },
    "outcome": {
      "type": "string",
      "enum": ["passed", "failed", "skipped", "error", "xfailed", "xpassed"]
    },
    "run_outcome": {
      "type": "string",
      "enum": ["passed", "failed", "error"]
    },
    "flake_status": {
      "type": "string",
      "enum": ["none", "suspected", "confirmed", "resolved", "ignored"]
    },
    "iso_time": {
      "type": "string",
      "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z$"
    },
    "repo_path": {
      "type": "string",
      "minLength": 1,
      "pattern": "^[^\\u0000]+$"
    }
  }
}
```

`outcome` values match pytest’s vocabulary:

- `passed`, `failed`, `skipped` — ordinary results.
- `error` — setup/teardown or collection error, not an assertion failure.
- `xfailed` — expected failure that did fail.
- `xpassed` — expected failure that passed.

`run_outcome` is rolled up:

- `passed` — no `failed` or `error` results, after applying `--allow-known-flakes` if that flag is set.
- `failed` — at least one disallowed `failed` or `error`.
- `error` — `proof-ci` itself could not complete the run (pytest crashed, repo not found, and so on). This is also exit code 3, not exit code 1.

### 4.2 TestResult and Failure

File: `src/proof_ci/schemas/test_result.json`

A `Failure` object is present if and only if `outcome` is `failed` or `error`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://proof-ci.dev/schemas/v1/test-result.json",
  "title": "TestResult",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "test_id",
    "outcome",
    "duration_ms",
    "location",
    "cache_hit"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "test_id": {
      "type": "string",
      "minLength": 1,
      "description": "Stable identifier. For pytest this is the nodeid, e.g. tests/test_foo.py::TestBox::test_bar[param]."
    },
    "outcome": {
      "type": "string",
      "enum": ["passed", "failed", "skipped", "error", "xfailed", "xpassed"]
    },
    "duration_ms": { "type": "integer", "minimum": 0 },
    "location": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "line"],
      "properties": {
        "file": { "type": "string", "minLength": 1 },
        "line": { "type": "integer", "minimum": 1 },
        "function": { "type": "string" }
      }
    },
    "message": {
      "type": "string",
      "description": "One-line error or skip reason. Empty string if passed."
    },
    "cache_hit": { "type": "boolean" },
    "flaky": {
      "type": "object",
      "additionalProperties": false,
      "required": ["is_flaky", "status"],
      "properties": {
        "is_flaky": { "type": "boolean" },
        "status": {
          "type": "string",
          "enum": ["none", "suspected", "confirmed", "resolved", "ignored"]
        },
        "flip_count": { "type": "integer", "minimum": 0 },
        "reason": { "type": "string" }
      }
    },
    "stdout": { "type": "string", "maxLength": 8192 },
    "stderr": { "type": "string", "maxLength": 8192 },
    "markers": {
      "type": "array",
      "items": { "type": "string" }
    },
    "failure": { "$ref": "#/$defs/Failure" }
  },
  "allOf": [
    {
      "if": {
        "properties": { "outcome": { "enum": ["failed", "error"] } },
        "required": ["outcome"]
      },
      "then": { "required": ["failure"] },
      "else": {
        "not": { "required": ["failure"] }
      }
    }
  ],
  "$defs": {
    "Failure": {
      "type": "object",
      "additionalProperties": false,
      "required": ["error_type", "error_message", "repro_command"],
      "properties": {
        "error_type": {
          "type": "string",
          "description": "Exception class name, e.g. AssertionError or FileNotFoundError."
        },
        "error_message": { "type": "string" },
        "file": {
          "type": "string",
          "description": "File of the failing assertion or exception, repo-relative if inside the repo."
        },
        "line": { "type": "integer", "minimum": 1 },
        "assertion": {
          "type": "string",
          "description": "Assertion line or comparison summary when available."
        },
        "traceback": {
          "type": "string",
          "maxLength": 16384,
          "description": "Plain-text traceback, already truncated."
        },
        "likely_cause": {
          "type": "object",
          "additionalProperties": false,
          "required": ["rationale"],
          "properties": {
            "file": { "type": "string" },
            "line": { "type": "integer", "minimum": 1 },
            "diff_hunk": {
              "type": "string",
              "maxLength": 4096,
              "description": "The intersecting unified-diff hunk, truncated."
            },
            "rationale": {
              "type": "string",
              "description": "Why this location was chosen, one or two sentences."
            }
          }
        },
        "repro_command": {
          "type": "string",
          "minLength": 1,
          "description": "A command that reruns only this test, e.g. pytest -q tests/test_foo.py::test_bar"
        }
      }
    }
  }
}
```

**How `likely_cause` is computed (normative):**

1. Collect traceback frames that refer to files inside the repository.
2. Intersect those files (and, when line numbers exist, those lines ±3) with the unified diff of the current change set (the same change set used for affected-test selection).
3. Prefer the frame nearest the failure (bottom-most user frame) that intersects the diff.
4. If no traceback frame intersects the diff, prefer the first changed file that the failing test imports (directly or transitively). Set `line` only if a single changed hunk exists in that file; otherwise omit `line`.
5. If nothing intersects, omit `likely_cause` entirely (do not invent one).
6. `rationale` always states which of the above rules fired, in plain English.

**How `repro_command` is computed:** `pytest -q {test_id}` plus any extra pytest args from `proof-ci.toml` `[project].pytest_args` that are required for collection (for example `-o addopts` is not repeated; config already handles that). Do not include `--json` or proof-ci-internal flags.

### 4.3 RunSummary

File: `src/proof_ci/schemas/run_summary.json`

This is the object printed on stdout when `proof-ci run --json` succeeds at the tooling level (tests themselves may have failed). It must stay small enough to drop into an agent context window. Full `TestResult` objects live in `results.json` and are fetched with `get_failure_details`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://proof-ci.dev/schemas/v1/run-summary.json",
  "title": "RunSummary",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "run_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "selection",
    "cache",
    "counts",
    "outcome",
    "summary",
    "failures",
    "results_path"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "run_id": {
      "type": "string",
      "format": "uuid",
      "description": "UUIDv4 generated at the start of the run."
    },
    "started_at": { "type": "string" },
    "finished_at": { "type": "string" },
    "duration_ms": { "type": "integer", "minimum": 0 },
    "commit_sha": { "type": "string" },
    "commit_tree": {
      "type": "string",
      "description": "Git tree hash of the working tree as tested (Section 4.7, Tree hash). Copied into RunRecord.payload.commit.tree when signing."
    },
    "working_tree_dirty": { "type": "boolean" },
    "selection": {
      "type": "object",
      "additionalProperties": false,
      "required": ["mode", "selected_count", "collected_count", "deselected_count"],
      "properties": {
        "mode": {
          "type": "string",
          "enum": ["affected", "all", "explicit", "failed"],
          "description": "failed = rerun of the tests that failed or errored in the previous run (--failed)."
        },
        "base_setting": {
          "type": "string",
          "description": "The configured or --base value before resolution: auto, HEAD, or a ref."
        },
        "base_ref": {
          "type": "string",
          "description": "The git ref actually diffed against after resolution (Section 5.8 Step 1). A full commit sha when base_setting was auto."
        },
        "changed_files": {
          "type": "array",
          "items": { "type": "string" }
        },
        "selected_count": { "type": "integer", "minimum": 0 },
        "collected_count": { "type": "integer", "minimum": 0 },
        "deselected_count": { "type": "integer", "minimum": 0 },
        "fallback_full_run": { "type": "boolean" },
        "fallback_reason": { "type": ["string", "null"] },
        "fallback_suppressed_by_tree": {
          "type": ["string", "null"],
          "description": "When a fallback glob matched but did not fire because the matching files are unchanged since the last run on this branch, the tree hash of that run (Section 5.8 Step 3). Null otherwise."
        }
      }
    },
    "cache": {
      "type": "object",
      "additionalProperties": false,
      "required": ["hits", "misses", "disabled"],
      "properties": {
        "hits": { "type": "integer", "minimum": 0 },
        "misses": { "type": "integer", "minimum": 0 },
        "disabled": { "type": "boolean" }
      }
    },
    "counts": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "passed",
        "failed",
        "skipped",
        "error",
        "xfailed",
        "xpassed",
        "flaky_failures",
        "total_selected"
      ],
      "properties": {
        "passed": { "type": "integer", "minimum": 0 },
        "failed": { "type": "integer", "minimum": 0 },
        "skipped": { "type": "integer", "minimum": 0 },
        "error": { "type": "integer", "minimum": 0 },
        "xfailed": { "type": "integer", "minimum": 0 },
        "xpassed": { "type": "integer", "minimum": 0 },
        "flaky_failures": { "type": "integer", "minimum": 0 },
        "total_selected": { "type": "integer", "minimum": 0 }
      }
    },
    "outcome": { "type": "string", "enum": ["passed", "failed", "error"] },
    "summary": {
      "type": "string",
      "description": "One human line, e.g. '1 failed, 10 passed, 2 cache hits in 1240 ms'."
    },
    "failures": {
      "type": "array",
      "description": "Compact failure list for the summary. Full details are in results.json.",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["test_id", "error_type", "error_message", "file", "line", "is_flaky", "repro_command"],
        "properties": {
          "test_id": { "type": "string" },
          "error_type": { "type": "string" },
          "error_message": { "type": "string" },
          "file": { "type": "string" },
          "line": { "type": ["integer", "null"], "minimum": 1 },
          "is_flaky": { "type": "boolean" },
          "repro_command": { "type": "string" }
        }
      }
    },
    "results_path": { "type": "string" },
    "record_path": { "type": ["string", "null"] },
    "savings_headline": {
      "type": "string",
      "description": "One-line headline, time first, money only when every applied price is configured (Section 5.12). Present after milestone M9."
    },
    "savings": {
      "$ref": "https://proof-ci.dev/schemas/v1/savings.json",
      "description": "Full savings object. Present after milestone M9; omitted before then."
    }
  }
}
```

`changed_files` may be omitted from stdout summaries that would become huge; it MUST still be written to `summary.json` on disk. Implementation rule: if `changed_files` has more than 200 entries, stdout JSON may replace the array with the first 200 and add no extra field (the full list remains on disk). Prefer simply always writing the full list — typical agent diffs are small. Include the full list in the on-disk artifact always.

### 4.4 FlakeRecord

File: `src/proof_ci/schemas/flake_record.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://proof-ci.dev/schemas/v1/flake-record.json",
  "title": "FlakeRecord",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "test_id",
    "status",
    "first_seen",
    "last_seen",
    "last_code_hash",
    "pass_count",
    "fail_count",
    "flip_count",
    "window",
    "updated_at"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "test_id": { "type": "string", "minLength": 1 },
    "status": {
      "type": "string",
      "enum": ["suspected", "confirmed", "resolved", "ignored"]
    },
    "first_seen": { "type": "string" },
    "last_seen": { "type": "string" },
    "last_code_hash": {
      "type": "string",
      "description": "Hex SHA-256 of the inputs used to decide that this test's code was unchanged."
    },
    "pass_count": { "type": "integer", "minimum": 0 },
    "fail_count": { "type": "integer", "minimum": 0 },
    "flip_count": { "type": "integer", "minimum": 0 },
    "window": {
      "type": "object",
      "additionalProperties": false,
      "required": ["days", "runs"],
      "properties": {
        "days": { "type": "integer", "minimum": 1 },
        "runs": { "type": "integer", "minimum": 0 }
      }
    },
    "reason": { "type": "string" },
    "updated_at": { "type": "string" }
  }
}
```

Note: the per-result `flaky.status` value `none` is used on `TestResult` when the test is not in the flake store. It does not appear on `FlakeRecord` itself, because a `FlakeRecord` only exists for tests that have been classified.


### 4.5 Savings and SavingsReport

Files: `src/proof_ci/schemas/savings.json` and `src/proof_ci/schemas/savings_report.json`.

Every completed `proof-ci run` (after milestone M9) produces a `Savings` object. It is written to the run artifacts, copied into `RunSummary.savings`, appended to the local savings ledger, and embedded at `RunRecord.payload.savings` so the signature covers it. Records created before M9 MAY omit `savings`; writers after M9 MUST include it.

**Integer money.** Signed payloads still MUST NOT contain floating-point numbers. All currency amounts in `Savings` are integers of **micro-USD** (one millionth of a US dollar). `$0.01` is `10000`. User-facing `[savings]` prices in `proof-ci.toml` MAY be decimal floats; convert with `micro = round(dollars * 1_000_000)` (half away from zero) before recording. CLI and MCP human text may display dollars to two decimal places; JSON artifacts use micro-USD.

**Tokenizer approximation (normative for Phase 1).** `token_count(text) = (utf8_byte_length(text) + 3) // 4`. This is a documented approximation (roughly four UTF-8 bytes per token for English prose), not a vendor tokenizer and not a claim about any model’s real billing. Record `"tokenizer": "utf8_bytes_div_4"` on every token metric. The byte counts it is applied to are measured (Section 5.12(c)); only the bytes-to-tokens step is an approximation. A later revision may add a pluggable tokenizer; do not silently change this formula.

**Metric order and honesty rules (normative).** Metrics are listed in order of importance to the user: `wrong_turns`, `ci_runs_avoided`, `local_compute`, `wait`, `tokens`. `local_compute` measures minutes of the *user's machine* not spent executing tests; it is priced only when `[savings].price_local_minutes` is true, at `local_compute_cost_per_minute`, never at the CI rate. `tokens_delta` may be negative (a green `RunSummary` is larger than `pytest -q`'s three lines); report it as it is. `prices_configured` is true only when every `[savings]` price key that was applied is present in the TOML; no dollar figure is shown in a headline unless it is true. `defaults_used` lists the price keys **absent from the TOML** (so a user who deliberately sets a price equal to the default is not marked as using a default).

Shared metric object shape (`$defs/metric` conceptually): `status` is `measured`, `estimated`, or `unavailable`; `confidence` is `high`, `medium`, or `low`; `rationale` is one or two sentences saying how the number was produced.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://proof-ci.dev/schemas/v1/savings.json",
  "title": "Savings",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "headline",
    "overall_confidence",
    "currency",
    "is_calibration_run",
    "prices_configured",
    "prices_used",
    "calibration",
    "wrong_turns",
    "ci_runs_avoided",
    "local_compute",
    "wait",
    "tokens",
    "totals"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "headline": {
      "type": "string",
      "description": "One line for CLI/MCP, e.g. 'Checked 14/1,200 (12 cached) in 22s; flaky flagged 1; CI runs avoided 0; local test-min not run ~18'. Money is appended only when prices_configured is true (Section 5.12)."
    },
    "overall_confidence": { "type": "string", "enum": ["high", "medium", "low"] },
    "currency": { "const": "USD" },
    "is_calibration_run": { "type": "boolean" },
    "prices_configured": {
      "type": "boolean",
      "description": "True only when every [savings] price key applied in this run is present in proof-ci.toml. Gates all dollar figures in headlines and dashboards."
    },
    "prices_used": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "ci_cost_per_minute_micro_usd",
        "price_local_minutes",
        "local_compute_cost_per_minute_micro_usd",
        "model_input_cost_per_million_tokens_micro_usd",
        "model_output_cost_per_million_tokens_micro_usd",
        "wasted_fix_cost_micro_usd",
        "engineer_hourly_cost_micro_usd",
        "defaults_used"
      ],
      "properties": {
        "ci_cost_per_minute_micro_usd": { "type": "integer", "minimum": 0 },
        "price_local_minutes": {
          "type": "boolean",
          "description": "[savings].price_local_minutes. False means local_compute.cost_micro_usd is 0 by rule."
        },
        "local_compute_cost_per_minute_micro_usd": { "type": "integer", "minimum": 0 },
        "model_input_cost_per_million_tokens_micro_usd": { "type": "integer", "minimum": 0 },
        "model_output_cost_per_million_tokens_micro_usd": { "type": "integer", "minimum": 0 },
        "wasted_fix_cost_micro_usd": { "type": "integer", "minimum": 0 },
        "engineer_hourly_cost_micro_usd": {
          "type": "integer",
          "minimum": 0,
          "description": "0 means the optional engineer hourly rate was not set."
        },
        "defaults_used": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Names of [savings] price keys absent from proof-ci.toml, so the documented default was applied."
        }
      }
    },
    "calibration": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "test_count", "payoff"],
      "properties": {
        "status": { "type": "string", "enum": ["missing", "stale", "current"] },
        "calibrated_at": { "type": ["string", "null"] },
        "test_count": { "type": "integer", "minimum": 0 },
        "full_suite_wall_ms": { "type": ["integer", "null"], "minimum": 0 },
        "fixed_overhead_ms": {
          "type": ["integer", "null"],
          "minimum": 0,
          "description": "Measured wall time of proof-ci's own fixed work (collect pass, git, hashing) during the calibration run."
        },
        "payoff": {
          "type": "string",
          "enum": ["unknown", "overhead_dominates", "marginal", "selection_saves_time"],
          "description": "Section 5.12 payoff verdict from full_suite_wall_ms versus fixed_overhead_ms. unknown when calibration is missing."
        },
        "uncalibrated_skipped_count": { "type": "integer", "minimum": 0 }
      }
    },
    "wrong_turns": { "$ref": "#/$defs/wrong_turns_metric" },
    "ci_runs_avoided": { "$ref": "#/$defs/ci_runs_metric" },
    "local_compute": { "$ref": "#/$defs/local_compute_metric" },
    "wait": { "$ref": "#/$defs/wait_metric" },
    "tokens": { "$ref": "#/$defs/tokens_metric" },
    "totals": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "cost_micro_usd",
        "wrong_turns_avoided",
        "ci_runs_avoided",
        "test_ms_not_executed",
        "wait_ms_saved",
        "tokens_delta"
      ],
      "properties": {
        "cost_micro_usd": { "type": "integer", "minimum": 0 },
        "wrong_turns_avoided": { "type": "integer", "minimum": 0 },
        "ci_runs_avoided": { "type": "integer", "minimum": 0 },
        "test_ms_not_executed": { "type": "integer", "minimum": 0 },
        "wait_ms_saved": { "type": "integer", "minimum": 0 },
        "tokens_delta": { "type": "integer", "description": "May be negative." }
      }
    }
  },
  "$defs": {
    "status": { "type": "string", "enum": ["measured", "estimated", "unavailable"] },
    "confidence": { "type": "string", "enum": ["high", "medium", "low"] },
    "wrong_turns_metric": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "confidence", "flagged_known_flake_failures", "cost_micro_usd", "rationale"],
      "properties": {
        "status": { "$ref": "#/$defs/status" },
        "confidence": { "$ref": "#/$defs/confidence" },
        "flagged_known_flake_failures": { "type": "integer", "minimum": 0 },
        "cost_micro_usd": { "type": "integer", "minimum": 0 },
        "rationale": { "type": "string" }
      }
    },
    "ci_runs_metric": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "confidence", "count", "dedupe_key", "deduplicated", "cost_micro_usd", "rationale"],
      "properties": {
        "status": { "$ref": "#/$defs/status" },
        "confidence": { "$ref": "#/$defs/confidence" },
        "count": { "type": "integer", "minimum": 0, "maximum": 1 },
        "dedupe_key": {
          "type": ["string", "null"],
          "description": "Hex SHA-256 over branch, commit_tree, and the sorted failing non-flaky test ids (Section 5.12(b)). Null when there was no qualifying failure."
        },
        "deduplicated": {
          "type": "boolean",
          "description": "True when the same dedupe_key already appears in the ledger, so count is 0 although the run failed."
        },
        "cost_micro_usd": { "type": "integer", "minimum": 0 },
        "rationale": { "type": "string" }
      }
    },
    "local_compute_metric": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "confidence", "deselected_count", "cache_hits", "test_ms_not_executed", "priced", "cost_micro_usd", "rationale"],
      "properties": {
        "status": { "$ref": "#/$defs/status" },
        "confidence": { "$ref": "#/$defs/confidence" },
        "deselected_count": { "type": "integer", "minimum": 0 },
        "cache_hits": { "type": "integer", "minimum": 0 },
        "test_ms_not_executed": {
          "type": "integer",
          "minimum": 0,
          "description": "Sum of calibrated durations of deselected and cache-hit tests: minutes of this machine not spent, not CI minutes."
        },
        "priced": { "type": "boolean" },
        "cost_micro_usd": { "type": "integer", "minimum": 0 },
        "rationale": { "type": "string" }
      }
    },
    "wait_metric": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "confidence", "baseline_kind", "baseline_ms", "actual_ms", "wait_ms_saved", "rationale"],
      "properties": {
        "status": { "$ref": "#/$defs/status" },
        "confidence": { "$ref": "#/$defs/confidence" },
        "baseline_kind": {
          "type": "string",
          "enum": ["none", "full_suite_local", "full_pipeline_user"],
          "description": "full_suite_local = calibration wall clock of the whole suite on this machine (a proxy, capped at medium confidence); full_pipeline_user = [savings].full_pipeline_baseline_ms supplied by the user."
        },
        "baseline_ms": { "type": ["integer", "null"], "minimum": 0 },
        "actual_ms": { "type": "integer", "minimum": 0 },
        "wait_ms_saved": { "type": "integer", "minimum": 0 },
        "rationale": { "type": "string" }
      }
    },
    "tokens_metric": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "confidence", "raw_source", "raw_bytes", "structured_bytes", "raw_tokens", "structured_tokens", "tokens_delta", "cost_micro_usd", "tokenizer", "rationale"],
      "properties": {
        "status": { "$ref": "#/$defs/status" },
        "confidence": { "$ref": "#/$defs/confidence" },
        "raw_source": {
          "const": "pytest_stdout",
          "description": "raw_bytes is the actual stdout of the pytest subprocess at the configured verbosity, not a reconstruction."
        },
        "raw_bytes": { "type": "integer", "minimum": 0 },
        "structured_bytes": { "type": "integer", "minimum": 0 },
        "raw_tokens": { "type": "integer", "minimum": 0 },
        "structured_tokens": { "type": "integer", "minimum": 0 },
        "tokens_delta": {
          "type": "integer",
          "description": "raw_tokens minus structured_tokens. Negative when the structured summary is larger than pytest's own output."
        },
        "cost_micro_usd": {
          "type": "integer",
          "minimum": 0,
          "description": "Input-token price applied to max(0, tokens_delta)."
        },
        "tokenizer": { "const": "utf8_bytes_div_4" },
        "rationale": { "type": "string" }
      }
    }
  }
}
```

`SavingsReport` (the object returned by `proof-ci savings` and MCP `get_savings_report`) aggregates ledger rows:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://proof-ci.dev/schemas/v1/savings-report.json",
  "title": "SavingsReport",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "since",
    "until",
    "group_by",
    "weekly",
    "runs",
    "overall_confidence",
    "defaults_used",
    "prices_configured",
    "headline",
    "totals",
    "groups"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "since": { "type": "string" },
    "until": { "type": "string" },
    "group_by": { "type": "string", "enum": ["repo", "agent", "branch"] },
    "weekly": { "type": "boolean" },
    "runs": { "type": "integer", "minimum": 0 },
    "overall_confidence": { "type": "string", "enum": ["high", "medium", "low"] },
    "defaults_used": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Union of defaults_used across the included runs."
    },
    "prices_configured": {
      "type": "boolean",
      "description": "True only when every included run had prices_configured true. Dollar totals are shown only then."
    },
    "headline": { "type": "string" },
    "totals": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "cost_micro_usd",
        "wrong_turns_avoided",
        "ci_runs_avoided",
        "test_ms_not_executed",
        "wait_ms_saved",
        "tokens_delta"
      ],
      "properties": {
        "cost_micro_usd": { "type": "integer", "minimum": 0 },
        "wrong_turns_avoided": { "type": "integer", "minimum": 0 },
        "ci_runs_avoided": { "type": "integer", "minimum": 0 },
        "test_ms_not_executed": { "type": "integer", "minimum": 0 },
        "wait_ms_saved": { "type": "integer", "minimum": 0 },
        "tokens_delta": { "type": "integer" }
      }
    },
    "groups": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["key", "runs", "totals", "overall_confidence"],
        "properties": {
          "key": { "type": "string" },
          "week": { "type": "string", "description": "ISO week, e.g. 2026-W45, when weekly is true." },
          "runs": { "type": "integer", "minimum": 0 },
          "totals": { "type": "object" },
          "overall_confidence": { "type": "string", "enum": ["high", "medium", "low"] }
        }
      }
    }
  }
}
```

### 4.6 RunRecord (signed)

File: `src/proof_ci/schemas/run_record.json`

A run record is a JSON object with two keys: `payload` (the signed content) and `signature` (metadata plus the signature value). **Only `payload` is canonicalized and signed.** The `signature` object is never part of the signed bytes.

`payload.selection` has exactly the shape of `RunSummary.selection` (Section 4.3) and is copied from the run's summary, including the full `changed_files` list. `payload.commit.tree` binds the record to the working tree as tested (Section 4.7, "Tree hash"). `payload.summary.executed` and `payload.summary.replayed` separate tests that ran in this process from cache replays, so a reader can tell how much of the record is fresh.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://proof-ci.dev/schemas/v1/run-record.json",
  "title": "RunRecord",
  "type": "object",
  "additionalProperties": false,
  "required": ["payload"],
  "properties": {
    "payload": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "schema_version",
        "run_id",
        "timestamp",
        "commit",
        "selection",
        "agent",
        "environment",
        "checks",
        "summary",
        "results"
      ],
      "properties": {
        "schema_version": { "const": "1.0.0" },
        "run_id": { "type": "string", "format": "uuid" },
        "timestamp": { "type": "string" },
        "commit": {
          "type": "object",
          "additionalProperties": false,
          "required": ["sha", "tree", "dirty"],
          "properties": {
            "sha": { "type": "string", "minLength": 1, "description": "Full sha of HEAD at run time." },
            "tree": {
              "type": "string",
              "minLength": 1,
              "description": "Git tree hash of the working tree as tested, including staged, unstaged, and untracked non-ignored files (Section 4.7, Tree hash). Equals the tree of HEAD when dirty is false."
            },
            "dirty": {
              "type": "boolean",
              "description": "True if the working tree or index differed from the named commit."
            }
          }
        },
        "selection": {
          "type": "object",
          "additionalProperties": false,
          "required": ["mode", "base_ref", "changed_files", "selected_count", "collected_count", "deselected_count", "fallback_full_run", "fallback_reason"],
          "properties": {
            "mode": { "type": "string", "enum": ["affected", "all", "explicit", "failed"] },
            "base_setting": { "type": "string" },
            "base_ref": { "type": "string" },
            "changed_files": { "type": "array", "items": { "type": "string" } },
            "selected_count": { "type": "integer", "minimum": 0 },
            "collected_count": { "type": "integer", "minimum": 0 },
            "deselected_count": { "type": "integer", "minimum": 0 },
            "fallback_full_run": { "type": "boolean" },
            "fallback_reason": { "type": ["string", "null"] },
            "fallback_suppressed_by_tree": { "type": ["string", "null"] }
          }
        },
        "agent": {
          "type": "object",
          "additionalProperties": false,
          "required": ["id"],
          "properties": {
            "id": {
              "type": "string",
              "minLength": 1,
              "description": "Opaque task or client identifier. Not a vendor account id."
            },
            "agent_name": {
              "type": "string",
              "description": "Generic product name supplied by the client (e.g. claude-code, cursor, codex, windsurf, cline, copilot, cli). Core does not interpret it."
            },
            "agent_version": { "type": "string" },
            "model": {
              "type": "string",
              "description": "Optional model identifier if the client reports one. Opaque; core does not specialize on vendor model names."
            },
            "session": { "type": "string" }
          }
        },
        "environment": {
          "type": "object",
          "additionalProperties": false,
          "required": ["python", "platform", "proof_ci_version"],
          "properties": {
            "python": { "type": "string" },
            "platform": { "type": "string" },
            "proof_ci_version": { "type": "string" },
            "os_release": { "type": "string" }
          }
        },
        "checks": {
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["name", "command"],
            "properties": {
              "name": { "type": "string" },
              "command": { "type": "string" },
              "selector": { "type": "string", "enum": ["affected", "all", "explicit", "failed"] }
            }
          }
        },
        "summary": {
          "type": "object",
          "additionalProperties": false,
          "required": ["outcome", "passed", "failed", "skipped", "error", "executed", "replayed", "duration_ms"],
          "properties": {
            "outcome": { "type": "string", "enum": ["passed", "failed", "error"] },
            "passed": { "type": "integer", "minimum": 0 },
            "failed": { "type": "integer", "minimum": 0 },
            "skipped": { "type": "integer", "minimum": 0 },
            "error": { "type": "integer", "minimum": 0 },
            "executed": {
              "type": "integer",
              "minimum": 0,
              "description": "Results produced by running pytest in this run (cache_hit false)."
            },
            "replayed": {
              "type": "integer",
              "minimum": 0,
              "description": "Results replayed from the cache in this run (cache_hit true). executed + replayed equals results.length."
            },
            "duration_ms": { "type": "integer", "minimum": 0 }
          }
        },
        "results": {
          "type": "array",
          "items": { "$ref": "https://proof-ci.dev/schemas/v1/test-result.json" }
        },
        "savings": {
          "$ref": "https://proof-ci.dev/schemas/v1/savings.json",
          "description": "Required on every run produced after milestone M9. Omitted only on records created before the savings subsystem existed."
        }
      }
    },
    "signature": {
      "type": "object",
      "additionalProperties": false,
      "required": ["alg", "key_id", "public_key", "value"],
      "properties": {
        "alg": { "const": "Ed25519" },
        "key_id": {
          "type": "string",
          "description": "First 16 hex characters of SHA-256 over the raw 32-byte public key."
        },
        "public_key": {
          "type": "string",
          "description": "Base64 (standard, not URL-safe) encoding of the raw 32-byte Ed25519 public key."
        },
        "value": {
          "type": "string",
          "description": "Base64 (standard, not URL-safe) encoding of the 64-byte Ed25519 signature."
        }
      }
    }
  }
}
```

An unsigned record (written before `proof-ci record sign`, or when the user ran without `--sign`) is the same object with `signature` omitted. `proof-ci record verify` MUST reject a record that has no `signature`.

### 4.7 Canonicalization and Ed25519 procedure

This procedure is normative. Tests MUST include golden vectors.

**What a signature proves (normative framing).** A valid Ed25519 signature over `payload` proves exactly two things: (1) the bytes of `payload` have not changed since they were signed, and (2) they were signed by the holder of the private key whose public half is `signature.public_key`. It does not prove that pytest executed, that the tests listed are the right tests, or that the machine was honest. In Phase 1 the private key is readable by the agent process that runs `proof-ci`, so a Phase 1 record attests *what that installation reported*. Three rules keep that attestation meaningful:

1. `proof-ci` signs only payloads it built itself from a run's artifacts (`summary.json`, `results.json`, and the `Savings` object of that run). There is no command that signs an arbitrary file (Section 5.7.5).
2. The payload names the tree that was tested (`commit.tree`), the selection that was applied (`selection`), and how many results were executed versus replayed from cache (`summary.executed`, `summary.replayed`). A reader can see that 2 of 1,200 tests were checked, or that every result was a replay.
3. A verifier decides whom to trust: `--pubkey` or `[record].trusted_pubkeys` names the keys a project accepts, and `verify` reports `signer: "known"` or `"unknown"`. A record that merely verifies against its own embedded key proves integrity, not identity.

Documentation, status checks, and marketing MUST describe records in these terms ("signed record of what proof-ci reported", "tamper-evident", "attributable to key …"), never as "proof the tests ran". Proof of execution is a Phase 2 property that requires remote execution with a server-held key.

**Tree hash (normative).** `commit.tree` is computed without touching the user's index:

1. Locate the real index with `git rev-parse --git-path index` (never assume `.git/index`: in a linked worktree `.git` is a file and the index lives under the common directory). Copy it to a temporary file and set `GIT_INDEX_FILE` to that file's **absolute** path for the following commands (git resolves a relative value against its own working directory).
2. `git add -A` (adds staged, unstaged, and untracked non-ignored files; records deletions). Whole-tree `add -A` without a pathspec is git 2.0 behaviour; see the git requirement below.
3. `git write-tree` → the hex tree hash. Use it as `commit.tree`.
4. Delete the temporary index. Never run `git add` against the real index.

When the working tree is clean, the result equals `git rev-parse HEAD^{tree}`; the implementation MAY short-circuit to that. If git is unavailable, `run --sign` and `record sign` fail with exit 3 (a record without a tree hash is not produced).

**Git requirement (normative).** `proof-ci` requires git >= 2.0. Every command that needs git runs `git --version` once per invocation (preflight), parses the version, and exits 3 with `git >= 2.0 required; found <version>` below that. Every supported platform in Section 5.20 ships a qualifying git.

**What is signed.** The UTF-8 bytes of the RFC 8785 (JSON Canonicalization Scheme) serialization of the `payload` object. The sibling `signature` object is excluded. When `payload.savings` is present it is part of those signed bytes: changing any savings figure, confidence label, price, or headline after signing MUST fail verification. Savings amounts use only integers (micro-USD, milliseconds, token counts) so they obey the no-float rule.

**Canonicalization rules** (RFC 8785):

1. Serialize with no insignificant whitespace.
2. Sort object keys by Unicode code point order of the key string.
3. Preserve array order.
4. Encode as UTF-8.
5. Escape strings as specified by RFC 8785.
6. Because signed payloads contain no floating-point numbers, number serialization is the decimal representation of integers with no exponent and no leading zeros (except the number `0`).

Implementation requirement: depend on the `rfc8785` package if it implements RFC 8785 faithfully; if it does not, implement the rules above and lock them with golden tests. Do not hand-roll a partial serializer without tests.

**Signing:**

1. Build the `payload` object in memory from the run's own artifacts (`summary.json`, `results.json`, the run's `Savings`, the tree hash, and the resolved identity). Do not include any signature fields inside `payload`. Refuse to sign if `selection.selected_count` is 0 (Section 5.8 Step 7).
2. Reject the payload if it contains a floating-point number anywhere (walk the tree).
3. `canonical = rfc8785.dumps(payload)` → `bytes`.
4. Load the Ed25519 private key (see key handling below).
5. `sig_bytes = private_key.sign(canonical)` using Ed25519 (RFC 8032) via the `cryptography` library.
6. `key_id = sha256(raw_public_key_bytes).hexdigest()[:16]`.
7. Write the envelope `{ "payload": payload, "signature": { "alg": "Ed25519", "key_id": key_id, "public_key": b64(raw_public_key_bytes), "value": b64(sig_bytes) } }`.
8. Pretty-print the envelope for the file on disk (2-space indent) — pretty-printing is fine because verification re-canonicalizes `payload` and ignores whitespace in the file.

**Verification:**

1. Parse the JSON file. Require `payload` and `signature`.
2. Require `signature.alg == "Ed25519"`.
3. Decode `signature.public_key` and `signature.value` as standard base64. Require lengths 32 and 64 bytes respectively.
4. Determine the trusted key set: if the caller supplied `--pubkey PATH`, it is that one key; otherwise it is the keys listed in `[record].trusted_pubkeys` (Section 5.4), each loaded from its PEM file; otherwise it is empty.
5. If the trusted set is non-empty, require `signature.public_key` to equal one of its raw 32-byte public keys; on mismatch the record is invalid (`reason: "signer not trusted"`, exit 4). This prevents a record from verifying against an embedded attacker key when the caller intended specific keys. If the set is empty, warn on stderr that verification is against the embedded public key (proves integrity, not identity) and continue; `--require-known-key` turns that case into a failure (exit 4, `reason: "no trusted key configured"`).
6. Recompute `key_id` and require it matches `signature.key_id`.
7. `canonical = rfc8785.dumps(payload)`.
8. `Ed25519PublicKey.from_public_bytes(raw_public_key).verify(sig_bytes, canonical)`.
9. Validate `payload` against the `RunRecord` schema (a record with a valid signature over a malformed payload is still rejected, `reason: "payload does not match schema"`).
10. On success, exit 0 and print `{ "valid": true, "key_id": ..., "run_id": ..., "signer": "known" | "unknown" }` (`known` when step 5 matched a trusted key). On failure, exit 4 and print `{ "valid": false, "reason": "..." }`.

**Key handling:**

- Algorithm: Ed25519 only.
- Private key file format: PKCS#8 PEM (`-----BEGIN PRIVATE KEY-----`).
- Public key file format: SubjectPublicKeyInfo PEM (`-----BEGIN PUBLIC KEY-----`).
- Default private key path: `Path(platformdirs.user_config_dir("proof-ci", appauthor=False)) / "ed25519.pem"` (`appauthor=False` avoids the `proof-ci\proof-ci` doubling on Windows).
- Override with environment variable `PROOF_CI_SIGNING_KEY` (highest precedence), then `--key PATH`, then `[record].key_path`.
- Key protection on POSIX (`os.name == "posix"`): create with mode `0600` (open with `O_CREAT | O_EXCL`, then `os.chmod`). Refuse to load a private key file whose mode has any group or other bits set (`stat.S_IMODE(mode) & 0o077 != 0`), with a message naming the file and the fix (`chmod 600`). **Mode-less filesystem exception:** some mounts do not carry POSIX modes and report `0777` or `0666` for every file (WSL2 `/mnt/c` DrvFs without the `metadata` option, 9p, CIFS/SMB, many FUSE mounts, some NFS exports). When the check fails *and* the file's owner uid equals the current uid *and* the filesystem type of the key's mount (from `/proc/mounts` on Linux, `mount` output on macOS) is one of `drvfs`, `9p`, `cifs`, `smbfs`, `fuse*`, `nfs*`, load the key but print a one-line warning naming the mount type and recommending a native filesystem location (`~/.config/proof-ci` inside WSL, not `/mnt/c`). Any other failing case is a refusal. Document this in the README under "Where to keep the signing key".
- Key protection on Windows (`os.name == "nt"`): `os.chmod` cannot express owner-only access (the reported mode is always `0o666`), so after creating the file run `icacls <path> /inheritance:r /grant:r "<current user>":F` (the current user from `%USERNAME%` first, then `os.getlogin()` as a fallback; `os.getlogin()` raises under some services and terminals), via `subprocess` with the path as an argument, not through a shell. Domain accounts (`DOMAIN\user`) are accepted by `icacls` as given. The "readable key" check on Windows runs `icacls <path>` and refuses the key if the output grants any access to `Everyone`, `Users`, `Authenticated Users`, or `BUILTIN\Users` (match case-insensitively on those principal names); entries for the current user, `SYSTEM`, and `Administrators` are accepted. The check MUST NOT use the POSIX mode bits on Windows. If `icacls` is not available, warn and continue (document this as the one platform exception).
- Never print the private key. Never return it from an MCP tool. Never copy it into a `RunRecord`, a cache entry, or a flake row.
- `proof-ci init` generates a key if none exists at the default path.
- `proof-ci record keygen [--force] [--key PATH]` also generates a key pair and writes `<path>` (private) and `<path>.pub` (public PEM).
- Trusted signers: `[record].trusted_pubkeys` is a list of repo-relative or absolute paths to public PEM files that `verify` accepts (Section 5.4). `proof-ci init` leaves it empty; `proof-ci record keygen` prints the `.pub` path and a hint to add it. A project that wants merge rules to pin an allowed signer set commits the `.pub` files and lists them there (Phase 2/3 reuse the same list server-side). Phase 1 does not require it.

### 4.8 Example objects

Passing `TestResult`:

```json
{
  "schema_version": "1.0.0",
  "test_id": "tests/test_math.py::test_add",
  "outcome": "passed",
  "duration_ms": 4,
  "location": { "file": "tests/test_math.py", "line": 4, "function": "test_add" },
  "message": "",
  "cache_hit": false,
  "flaky": { "is_flaky": false, "status": "none" },
  "markers": []
}
```

Failing `TestResult`:

```json
{
  "schema_version": "1.0.0",
  "test_id": "tests/test_math.py::test_div",
  "outcome": "failed",
  "duration_ms": 7,
  "location": { "file": "tests/test_math.py", "line": 12, "function": "test_div" },
  "message": "assert 1 == 0",
  "cache_hit": false,
  "flaky": { "is_flaky": false, "status": "none" },
  "markers": [],
  "failure": {
    "error_type": "AssertionError",
    "error_message": "assert 1 == 0",
    "file": "tests/test_math.py",
    "line": 13,
    "assertion": "assert div(1, 1) == 0",
    "traceback": "Traceback (most recent call last):\n  File \"tests/test_math.py\", line 13, in test_div\n    assert div(1, 1) == 0\nAssertionError: assert 1 == 0",
    "likely_cause": {
      "file": "src/demo/mathops.py",
      "line": 8,
      "diff_hunk": "@@ -5,6 +5,6 @@\n-    return 0\n+    return left // right",
      "rationale": "The failing traceback frame is in tests/test_math.py:13, which imports demo.mathops; that module intersects the current diff at line 8."
    },
    "repro_command": "pytest -q tests/test_math.py::test_div"
  }
}
```

Compact `RunSummary` (comments are not legal JSON; shown here as explanation only — never emit comments):

```json
{
  "schema_version": "1.0.0",
  "run_id": "11111111-1111-4111-8111-111111111111",
  "started_at": "2026-11-08T04:26:00.000Z",
  "finished_at": "2026-11-08T04:26:01.240Z",
  "duration_ms": 1240,
  "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "commit_tree": "cccccccccccccccccccccccccccccccccccccccc",
  "working_tree_dirty": true,
  "selection": {
    "mode": "affected",
    "base_setting": "auto",
    "base_ref": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "changed_files": ["src/demo/mathops.py"],
    "selected_count": 2,
    "collected_count": 8,
    "deselected_count": 6,
    "fallback_full_run": false,
    "fallback_reason": null
  },
  "cache": { "hits": 0, "misses": 2, "disabled": false },
  "counts": {
    "passed": 1,
    "failed": 1,
    "skipped": 0,
    "error": 0,
    "xfailed": 0,
    "xpassed": 0,
    "flaky_failures": 0,
    "total_selected": 2
  },
  "outcome": "failed",
  "summary": "1 failed, 1 passed in 1240 ms",
  "failures": [
    {
      "test_id": "tests/test_math.py::test_div",
      "error_type": "AssertionError",
      "error_message": "assert 1 == 0",
      "file": "tests/test_math.py",
      "line": 13,
      "is_flaky": false,
      "repro_command": "pytest -q tests/test_math.py::test_div"
    }
  ],
  "results_path": ".proof-ci/runs/11111111-1111-4111-8111-111111111111/results.json",
  "record_path": null,
  "savings_headline": "Checked 2/8 (0 cached) in 1s; flaky flagged 0; CI runs avoided 1; local test-min not run ~0 (uncalibrated)"
}
```


Example `Savings` object (uncalibrated run with default prices; figures are illustrative of shape, not a claimed result; note the negative `tokens_delta`, which is normal for a two-test run):

```json
{
  "schema_version": "1.0.0",
  "headline": "Checked 2/8 (0 cached) in 1s; flaky flagged 0; CI runs avoided 1; local test-min not run ~0 (uncalibrated)",
  "overall_confidence": "low",
  "currency": "USD",
  "is_calibration_run": false,
  "prices_configured": false,
  "prices_used": {
    "ci_cost_per_minute_micro_usd": 10000,
    "price_local_minutes": false,
    "local_compute_cost_per_minute_micro_usd": 0,
    "model_input_cost_per_million_tokens_micro_usd": 3000000,
    "model_output_cost_per_million_tokens_micro_usd": 15000000,
    "wasted_fix_cost_micro_usd": 25000000,
    "engineer_hourly_cost_micro_usd": 0,
    "defaults_used": [
      "ci_cost_per_minute",
      "model_input_cost_per_million_tokens",
      "model_output_cost_per_million_tokens",
      "wasted_fix_cost"
    ]
  },
  "calibration": {
    "status": "missing",
    "calibrated_at": null,
    "test_count": 0,
    "full_suite_wall_ms": null,
    "fixed_overhead_ms": null,
    "payoff": "unknown",
    "uncalibrated_skipped_count": 6
  },
  "wrong_turns": {
    "status": "measured",
    "confidence": "high",
    "flagged_known_flake_failures": 0,
    "cost_micro_usd": 0,
    "rationale": "No failures in this run had flaky.status of confirmed or ignored."
  },
  "ci_runs_avoided": {
    "status": "estimated",
    "confidence": "medium",
    "count": 1,
    "dedupe_key": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "deduplicated": false,
    "cost_micro_usd": 0,
    "rationale": "The run had a non-flaky failure not previously recorded for this branch and tree, so a push would likely have produced a failing CI run. No pipeline baseline is stored, so the avoided-run cost is 0 rather than invented."
  },
  "local_compute": {
    "status": "estimated",
    "confidence": "low",
    "deselected_count": 6,
    "cache_hits": 0,
    "test_ms_not_executed": 0,
    "priced": false,
    "cost_micro_usd": 0,
    "rationale": "No calibration exists yet, so deselected tests have no measured duration and local test time not executed is reported as 0 rather than invented. Local minutes are not priced (price_local_minutes is false)."
  },
  "wait": {
    "status": "unavailable",
    "confidence": "low",
    "baseline_kind": "none",
    "baseline_ms": null,
    "actual_ms": 1240,
    "wait_ms_saved": 0,
    "rationale": "No full-suite calibration or user-supplied pipeline baseline is stored."
  },
  "tokens": {
    "status": "measured",
    "confidence": "medium",
    "raw_source": "pytest_stdout",
    "raw_bytes": 612,
    "structured_bytes": 1640,
    "raw_tokens": 153,
    "structured_tokens": 410,
    "tokens_delta": -257,
    "cost_micro_usd": 0,
    "tokenizer": "utf8_bytes_div_4",
    "rationale": "Measured the pytest subprocess stdout (612 bytes at -q --tb=short) against the RunSummary JSON without the savings object (1640 bytes); the structured form is larger for this two-test run, so no token saving is claimed."
  },
  "totals": {
    "cost_micro_usd": 0,
    "wrong_turns_avoided": 0,
    "ci_runs_avoided": 1,
    "test_ms_not_executed": 0,
    "wait_ms_saved": 0,
    "tokens_delta": -257
  }
}
```

---

## 5. Phase 1 — local CLI and MCP server

Phase 1 is a free, open-source, local tool. There is no account, no network requirement, and no telemetry. It is a Python package whose distribution name and CLI are `proof-ci` and whose import package is `proof_ci`. It installs a CLI and an MCP server. It works with Claude Code, Cursor, and Codex via MCP over stdio.

### 5.1 Goals

A developer or agent, working in a Python repository that uses pytest, can:

1. Install `proof-ci` into the project's own Python environment (editable from the source repo, or later from PyPI). The pytest plugin must be importable by the interpreter that runs the project's tests, so a tool-only install (for example `pipx`) is not supported in Phase 1 (Section 5.10).
2. Run `proof-ci init` to write `proof-ci.toml` and local directories.
3. Run `proof-ci run` and receive a structured `RunSummary` in well under 30 seconds for a typical one-file change on a warm machine (design target).
4. See only affected tests selected, with a conservative full-run fallback when selection would be unsafe.
5. Inspect failures as JSON with test id, error, file:line, likely cause in the diff, repro command, and flaky flag.
6. Track flakes locally in SQLite and list them.
7. Sign and verify a `RunRecord`.
8. See a one-line savings headline after each run, inspect `proof-ci savings`, and find those figures inside the signed record.
9. Expose the same capabilities as six MCP tools over stdio.

### 5.2 Repository layout of the proof-ci project itself

Use a src layout and Hatchling.

```
proof-ci/
  pyproject.toml
  README.md
  AGENTS.md
  CLAUDE.md
  LICENSE                  # MIT
  .gitignore
  .gitlab-ci.yml
  .github/
    workflows/
      ci.yml
  src/
    proof_ci/
      __init__.py          # __version__ = "0.1.0"
      __main__.py          # python -m proof_ci → CLI
      cli.py               # Typer application
      config.py            # proof-ci.toml loading and defaults
      models.py            # Pydantic models matching Section 4
      affected.py          # changed files + import graph + selection
      cache.py             # content-hash cache
      runner.py            # orchestrates a run
      pytest_plugin.py     # loaded by the runner with -p; no pytest11 entry point
      flakes.py            # SQLite + detection
      record.py            # canonicalize, sign, verify, keygen
      discover.py          # GitHub / GitLab YAML discovery
      mcp_server.py        # stdio MCP server
      redact.py            # secret-shaped output redaction
      util.py              # repo root detection, timestamps, hashing
      savings.py           # calibration, estimation, ledger
      adapters/
        agent/             # identity resolver; MCP stdio surface
        forge/             # ForgeAdapter: github.py, gitlab.py
        ci/                # CICommandSource: github_actions.py, gitlab_ci.py
        runners/           # TestRunnerAdapter: pytest_runner.py
      schemas/
        test_result.json
        run_summary.json
        flake_record.json
        savings.json
        savings_report.json
        run_record.json
  tests/
    conftest.py
    test_cli_smoke.py
    test_config.py
    test_models.py
    test_affected.py
    test_cache.py
    test_flakes.py
    test_record.py
    test_discover.py
    test_runner.py
    test_mcp.py
    test_redact.py
    test_savings.py
    test_import_lint.py
    test_conformance_generic_client.py
    fixtures/
      golden/
        payload.json
        payload.canonical.json
        ...
  examples/
    demo-project/
      proof-ci.toml
      pyproject.toml
      src/demo/...
      tests/...
```

Do not invent extra top-level packages. Additional internal modules are allowed if they keep the files above from becoming large, but public CLI command names and MCP tool names are fixed.

### 5.3 `pyproject.toml` requirements

Minimum contents (versions may be pinned slightly higher if needed for compatibility, but do not add heavy unused dependencies):

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "proof-ci"                 # distribution name (pip install proof-ci)
version = "0.1.0"
description = "Agent-native CI layer: fast, structured, local-first checks for coding agents."
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [{ name = "Chris Whalen" }]
dependencies = [
  "typer>=0.12",
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "platformdirs>=4.0",
  "mcp>=1.2,<2",          # see note below: 2.x renamed FastMCP and changed import paths
  "cryptography>=42.0",
  "rfc8785>=0.1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-cov>=5.0",
  "ruff>=0.6",
  "mypy>=1.10",
  "import-linter>=2.0",
]

[project.scripts]
proof-ci = "proof_ci.cli:app"

# No [project.entry-points.pytest11]: the plugin is loaded explicitly with
# `-p proof_ci.pytest_plugin` by the runner (Section 5.10). An entry point would
# autoload the plugin into every pytest run on the machine and would register
# the module twice when the runner also passes -p.

[tool.hatch.build.targets.wheel]
packages = ["src/proof_ci"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true
packages = ["proof_ci"]

[tool.importlinter]
root_package = "proof_ci"

[[tool.importlinter.contracts]]
name = "core_must_not_import_vendor_sdks"
type = "forbidden"
source_modules = [
  "proof_ci.affected",
  "proof_ci.cache",
  "proof_ci.cli",
  "proof_ci.config",
  "proof_ci.flakes",
  "proof_ci.models",
  "proof_ci.record",
  "proof_ci.redact",
  "proof_ci.runner",
  "proof_ci.savings",
  "proof_ci.util",
]
forbidden_modules = [
  "github",
  "gitlab",
  "atlassian",
  "msrest",
  "azure",
  "anthropic",
  "openai",
]
# Adapter packages may import vendor SDKs. The MCP protocol library is allowed
# only from proof_ci.adapters.agent (see Section 3.5).
```

If `rfc8785` on PyPI cannot be imported or does not implement RFC 8785, replace it with an internal `proof_ci.canonicaljson` module covered by golden tests, and record that decision in `README.md`. Do not silently skip canonicalization tests.

**Why `mcp>=1.2,<2`.** The MCP Python SDK 2.x (published 2026) renamed `FastMCP` to `MCPServer`, deprecated the `mcp.server.fastmcp` import path, and reworked the low-level server; an unbounded `>=1.0` resolves to 2.x and breaks code written against the 1.x API that most client documentation still shows. Implement against 1.x, pin below 2, and migrate deliberately in a later revision (one milestone, with the conformance tests as the gate). Do not pin a single patch version; `pip install -e ".[dev]"` must resolve on a fresh machine.

CLI framework: Typer (Click-based). Do not use raw argparse. The Typer app object MUST be named `app` in `proof_ci.cli` so the `[project.scripts]` entry above works.

### 5.4 Configuration file

File name: `proof-ci.toml` at the repository root (the directory containing `.git`, or the nearest ancestor the user ran from that contains `proof-ci.toml`).

`proof-ci init` writes this default file. Every key below has a default; a missing file is tolerated by commands other than those that need a configured test command, in which case pytest defaults apply.

```toml
[project]
name = ""                          # default: directory name
# Array form is canonical and needs no quoting rules (Windows paths included).
# A plain string is accepted and split with shlex (posix=False on Windows).
test_command = ["pytest"]
pytest_args = []                   # extra args always passed to pytest, e.g. ["-q"] or ["-n", "auto"]
source_roots = ["src"]             # used by the import graph; if a root does not exist, skip it
test_paths = ["tests"]

[affected]
# "auto" | "HEAD" | an explicit git ref. "auto" diffs against the merge-base
# with the default branch and never silently selects nothing on a clean tree
# (Section 5.8 Step 1). "HEAD" diffs only uncommitted work.
base = "auto"
fallback_full_run_globs = [
  "pyproject.toml",
  "setup.cfg",
  "setup.py",
  "tox.ini",
  "noxfile.py",
  "conftest.py",
  "requirements.txt",
  "requirements*.txt",
  "constraints*.txt",
  "uv.lock",
  "poetry.lock",
  "Pipfile.lock",
  "pdm.lock",
  ".gitlab-ci.yml",
  ".github/workflows/**",
  "proof-ci.toml",
]

[cache]
enabled = true
dir = ".proof-ci/cache"
collect_cache_dir = ".proof-ci/collect-cache"
collect_cache_env_recheck_minutes = 60     # re-run collection to refresh the environment fingerprint when every selected test would be a cache hit

[flakes]
db = ".proof-ci/flakes.sqlite"
min_runs = 3
window_days = 30
min_flips_confirmed = 2

[record]
# empty means the platformdirs default
key_path = ""
agent_id = ""
# Public PEM files (repo-relative or absolute) that `record verify` accepts as
# known signers. Empty means verify only proves integrity (Section 4.7).
trusted_pubkeys = []

[discovery]
gitlab_ci = ".gitlab-ci.yml"
github_workflows = ".github/workflows"

[savings]
# Prices below are DEFAULTS for local arithmetic. They are not market
# quotes and must not be cited as typical industry rates. Replace them
# with the rates you actually pay. A price key that is ABSENT from this
# file is listed in prices_used.defaults_used, and no dollar figure is
# shown in a headline until every applied price key is present
# (prices_configured). `proof-ci init` writes the keys commented out so
# that a fresh install shows time, not money.
db = ".proof-ci/savings.sqlite"
# ci_cost_per_minute = 0.01                            # USD per CI minute, used only for avoided CI runs; DEFAULT
# model_input_cost_per_million_tokens = 3.0            # USD; DEFAULT
# model_output_cost_per_million_tokens = 15.0          # USD; DEFAULT
# wasted_fix_cost = 25.0                               # USD per wasted fix attempt; DEFAULT
# price_local_minutes = false                          # true to price your own machine's test minutes; DEFAULT false
# local_compute_cost_per_minute = 0.0                  # USD per local test minute, applied only when price_local_minutes = true
# engineer_hourly_cost = 0.0                           # optional USD/hour; unused unless set > 0
# full_pipeline_baseline_ms = 0                        # optional measured CI pipeline wall time (queue + run)
calibration_max_age_days = 14
calibration_stale_after_runs = 50
refresh_calibration_on_all = true
```

Resolution of the repository root, in order:

1. The nearest ancestor directory of the current working directory that contains `proof-ci.toml`.
2. Otherwise the nearest ancestor that contains a `.git` directory or file (worktrees).
3. Otherwise, fail with exit code 2 and a message telling the user to run `proof-ci init` or `cd` into the repo.

Paths in the file are relative to the repository root unless they are absolute.

### 5.5 Exit codes (all commands)

| Code | Meaning |
| --- | --- |
| 0 | Success. For `run`, all disallowed tests passed (see `--allow-known-flakes`). For `record verify`, the signature is valid. For `affected`, selection succeeded even if the list is empty. For `savings`, the report was produced (including an all-zero report). |
| 1 | One or more tests failed or errored (and were not allowed as known flakes). |
| 2 | Usage or configuration error (bad flags, missing repo, invalid TOML, unknown command). |
| 3 | Runtime error (pytest crashed, I/O failure, git missing when required, plugin failed unexpectedly). |
| 4 | Record verification failed (bad signature, missing signature, key mismatch). |

Do not use other exit codes in Phase 1.

### 5.6 Output convention (all commands)

- Without `--json`: print a human-readable report to stdout. Still write JSON artifacts under `.proof-ci/` whenever the command produced a run, a selection, or a verification result.
- With `--json`: print the primary JSON object to stdout (no trailing commentary on stdout). Print optional progress or warnings to stderr. JSON is UTF-8, 2-space indented, ending with a newline.
- `--help` uses Typer/Click defaults and exits 0.

### 5.7 CLI commands

Common options, accepted on **every leaf command** (`proof-ci run --json`, `proof-ci flakes list --json`, `proof-ci savings --json`, and so on). Click parses root-level options only before the subcommand name, so these MUST be declared on each command (use one shared helper that adds the three parameters) rather than only on the root callback. They MAY additionally be accepted on the root app; when given in both places the leaf value wins.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--json / --no-json` | `--no-json` | Machine-readable stdout. |
| `--repo PATH` | discovered | Override repository root. |
| `--config PATH` | `<repo>/proof-ci.toml` | Override config path. |

`proof-ci --version` prints `proof-ci 0.1.0` (or the current `__version__`) and exits 0.

#### 5.7.1 `proof-ci init`

```
proof-ci init [--force] [--discover] [--no-key]
```

Behavior:

1. Refuse to overwrite an existing `proof-ci.toml` unless `--force`.
2. Create `.proof-ci/` and the subdirectories listed in Section 3.4.
3. Ensure `.gitignore` contains `.proof-ci/cache/`, `.proof-ci/collect-cache/`, `.proof-ci/flakes.sqlite`, `.proof-ci/savings.sqlite`, `.proof-ci/last-run.json`, `.proof-ci/runs/`, and `.proof-ci/logs/`. Append a clearly marked block rather than rewriting the whole file. If `.gitignore` does not exist, create it.
4. If `--discover` is set, run CI discovery and, if a high-confidence test command is found, write it to `[project].test_command` in array form.
5. Unless `--no-key`, generate a signing key at the default path if none exists. If the config directory cannot be created or written (read-only or ephemeral `HOME`, typical in containers and CI), print a warning naming the path, set `key_created: false`, and continue; `run --sign` later reports where to point `PROOF_CI_SIGNING_KEY`. Never fail `init` because of the key.
6. If the process is running as uid 0 and the repository directory is owned by another uid (a container with a bind-mounted repo), print a warning that `.proof-ci/` will be root-owned on the host and recommend `docker run --user $(id -u):$(id -g)`.
7. Write `proof-ci.toml` with defaults (`test_command` as an array, price keys commented out, discovered command if any).
8. Exit 0. JSON shape when `--json`: `{ "repo": "...", "config_path": "...", "key_created": true/false, "key_skipped_reason": "..." or null, "discovered_command": [...] or null }`.

#### 5.7.2 `proof-ci affected`

```
proof-ci affected [--base REF] [--format ids|files|json]
```

`--base` overrides `[affected].base`. `--format` default is `ids` (one pytest node id per line) when `--json` is not set. `--json` implies JSON output regardless of `--format` and wins.

JSON shape:

```json
{
  "base_setting": "auto",
  "base_ref": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "changed_files": ["src/demo/mathops.py"],
  "selected_tests": ["tests/test_math.py::test_add", "tests/test_math.py::test_div"],
  "fallback_full_run": false,
  "fallback_reason": null
}
```

If selection falls back to a full run, `selected_tests` is the full collected set (or, if collection has not happened yet, an empty list with `fallback_full_run: true` and a reason — implementers SHOULD collect so the list is populated). Exit 0 on an empty selection.

This command MUST NOT run tests.

#### 5.7.3 `proof-ci run`

```
proof-ci run
    [--base REF]
    [--all]
    [--failed]
    [--no-cache]
    [--allow-known-flakes]
    [--sign]
    [--agent-id ID]
    [--record-out PATH]
    [TESTS...]
```

Selection mode:

- If one or more `TESTS` arguments are present, mode is `explicit`. Each argument is a pytest node id or file path, passed through to pytest.
- Else if `--all`, mode is `all`.
- Else if `--failed`, mode is `failed`: the selected set is every test whose outcome in the previous run (`.proof-ci/last-run.json` → `results.json`) was `failed` or `error`, intersected with the current collection. This is the `pytest --lf` loop with proof-ci's structure, flake flags, and cache rules. Exit 2 with a clear message if there is no previous run; an empty intersection is treated like Step 7 of Section 5.8 (zero tests, exit 0, never signed). `--all`, `--failed`, and `TESTS` are mutually exclusive (exit 2).
- Else mode is `affected` using `--base` or config.

Behavior:

1. Resolve repo, config, and git metadata (`commit.sha`, `dirty`, and the tree hash of the working tree per Section 4.7, stored on the summary as `commit_tree`; omitted only when git is unavailable).
2. Collect the suite through the plugin in collect mode (Section 5.10), or reuse the collection cache when its key matches (Section 5.8 Step 5): a subprocess `<test_command> --collect-only -q -p proof_ci.pytest_plugin -p no:xdist <test_paths or explicit TESTS>` with `PROOF_CI_COLLECT_FILE` set. This yields the node ids, `collected_count`, and the environment fingerprint (`python`, `platform`, `deps_hash`) of the interpreter that will run the tests. Collection happens in every mode; in `all`, `explicit`, and `failed` modes it determines the node ids that cache lookup and calibration need.
3. Compute the selected test ids: the affected subset (Section 5.8), all collected ids (`--all`), the previous run's failed/error ids (`--failed`), or the collected ids matching the explicit arguments.
4. Look up cache entries unless `--no-cache`, `[cache].enabled = false`, or `selection.fallback_full_run` is true (Section 5.9; a fallback suppressed by the incremental rule does not disable the cache). Cache hits are not executed; their stored `TestResult` is replayed with `cache_hit: true`. Tests marked `confirmed` or `suspected` flaky are never served from cache.
5. Run the cache-miss node ids through the plugin in run mode: a subprocess `<test_command> -p proof_ci.pytest_plugin <configured pytest_args> <node ids>` with `PROOF_CI_RESULTS_FILE` set and the repository root as the working directory. Do not pass `-v` by default; the plugin's results file is the source of structured data. Never run pytest in-process (Section 5.10 explains why).
6. Record every freshly executed outcome in the flake store.
7. Compute `likely_cause` against the same change set used for selection (for `--all` and explicit, the change set is still `git diff` against the resolved base, or empty if git is unavailable).
8. Write `summary.json`, `results.json`, and update `.proof-ci/last-run.json` (a copy of the `RunSummary` plus a `results_path`).
9. Compute the `Savings` object (Section 5.12), write it onto the summary as `savings` and `savings_headline`, and append a row to the savings ledger. Calibration runs and `run --all` follow the rules in Section 5.12.
10. If `--sign`: refuse when `selection.selected_count` is 0 (print `cannot sign a run that selected no tests` on stderr, leave `record_path: null`, and exit 2 after writing the summary). Otherwise build a `RunRecord` whose payload includes `commit.tree`, `selection`, `summary.executed` / `summary.replayed`, and `payload.savings`; sign it; write it to `--record-out` or `.proof-ci/runs/<run_id>/record.json`; set `record_path` on the summary.
11. Print the summary (human or JSON). Human output MUST include the savings headline as its own line, for example `Checked 14/1,200 (12 cached) in 22s; flaky flagged 1; CI runs avoided 0; local test-min not run ~18`, and repeats the calibration payoff verdict when it is `overhead_dominates` (Section 5.12).
12. Exit 0/1/2/3 as specified (2 only for the refused-sign case above or a configuration error).

`--allow-known-flakes`: failures whose `flaky.status` is `confirmed` or `ignored` do not cause exit code 1 and do not make `outcome` `failed`. They still appear in `failures` and increment `counts.failed` and `counts.flaky_failures`. `suspected` flakes still fail the run — they are not trusted enough to ignore. `resolved` is not a reason to ignore a new failure.

`--agent-id` overrides `[record].agent_id`. If neither is set and `--sign` is requested, use `local` as the id and print a stderr warning that a more specific id is recommended.

Environment variables honored during a run:

| Variable | Meaning |
| --- | --- |
| `PROOF_CI_AGENT_ID` | Default agent id, overridden by `--agent-id`. |
| `PROOF_CI_AGENT_NAME` | Written to `payload.agent.agent_name` when signing. |
| `PROOF_CI_AGENT_VERSION` | Written to `payload.agent.agent_version` when signing. |
| `PROOF_CI_AGENT_MODEL` | Written to `payload.agent.model` when signing. |
| `PROOF_CI_AGENT_SESSION` | Written to `payload.agent.session` when signing. |
| `PROOF_CI_SIGNING_KEY` | Private key path. |

#### 5.7.4 `proof-ci flakes`

```
proof-ci flakes list [--status STATUS] [--json]
proof-ci flakes show TEST_ID [--json]
proof-ci flakes mark TEST_ID [--status confirmed|ignored] [--reason TEXT]
proof-ci flakes unmark TEST_ID
```

- `list` prints `FlakeRecord` objects (human table or `{ "flakes": [ ... ] }`). Default lists `suspected` and `confirmed`. `--status all` lists every non-resolved row; `--status resolved` lists resolved ones.
- `show` exits 2 if the test id is unknown.
- `mark` writes a manual status (`confirmed` or `ignored` only) and an optional reason. It does not delete history.
- `unmark` sets status to `resolved` and records reason `"manually unmarked"` if none is supplied. History rows remain.

#### 5.7.5 `proof-ci record sign`

```
proof-ci record sign [--run RUN_ID] [--output PATH] [--key PATH] [--agent-id ID]
```

- Signs a run that `proof-ci run` produced. Default `--run` is the run named in `.proof-ci/last-run.json`; `--run RUN_ID` selects `.proof-ci/runs/<run_id>/`. The payload is built from that run's `summary.json`, `results.json`, and its `Savings` object, plus the resolved identity. There is no option to sign an arbitrary input file; a payload `proof-ci` did not build is never signed (Section 4.7, "What a signature proves").
- `commit.tree` is recomputed at sign time (Section 4.7) and compared with `RunSummary.commit_tree` recorded by `run`. If they differ (the working tree changed since the run), signing fails with exit 2 and a message that the run no longer matches the tree; rerun instead.
- Refuses (exit 2) to sign a run whose `selection.selected_count` is 0.
- Default `--output` overwrites the run’s `record.json`.
- Fails with exit 2 if there is no last run or the run directory is missing.
- After signing, the file MUST validate against the `RunRecord` schema including `signature`.

#### 5.7.6 `proof-ci record verify`

```
proof-ci record verify PATH [--key PATH | --pubkey PATH] [--require-known-key]
```

See Section 4.7. JSON output: `{ "valid": true|false, "reason": "...", "key_id": "...", "run_id": "...", "signer": "known"|"unknown" }`. `reason` is present when `valid` is false and MAY be present as an informational note when verifying against an embedded key without `--require-known-key`. `signer` is present when `valid` is true. `PATH` may point anywhere on disk (verifying a record received from another machine is the main use); verification reads the file and never writes.

#### 5.7.7 `proof-ci record keygen`

```
proof-ci record keygen [--force] [--key PATH]
```

Creates the private PEM and a sibling `.pub` public PEM. Refuses to overwrite unless `--force`.

#### 5.7.8 `proof-ci savings`

```
proof-ci savings [--since WHEN] [--until WHEN] [--by repo|agent|branch] [--weekly] [--json]
proof-ci savings calibrate [--json]
```

`proof-ci savings` (report mode, the default when no subcommand is given) reads the local savings ledger and prints a `SavingsReport`.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--since WHEN` | `30d` | Inclusive start. Accept an RFC 3339 timestamp or a relative window `Nd`, `Nw` (days, weeks) counted back from now (UTC). |
| `--until WHEN` | now (UTC) | Inclusive end, same syntax as `--since`. |
| `--by repo\|agent\|branch` | `repo` | Grouping key. Phase 1 is single-repo, so `repo` is one bucket (the current repository). `agent` uses the stored `agent_id`. `branch` uses the branch recorded at run time. |
| `--weekly` | off | Also bucket groups by ISO week (UTC, Monday start). When set, each group includes a `week` field such as `2026-W45`. |
| `--json` | inherited | Print the `SavingsReport` object on stdout. |

Exit codes: 0 if the report was produced (including zero runs in the window); 2 for bad flags or an unparsable `--since`/`--until`; 3 if the ledger cannot be read.

`proof-ci savings calibrate` runs the full suite in order to measure per-test durations, the full-suite wall clock, and proof-ci's own fixed overhead (Section 5.12). It does not use the result cache for reads. Test-failure exit codes match `proof-ci run --all` (a failing suite still records durations for tests that completed). JSON shape when `--json`: the `Savings` object for that calibration run (with `is_calibration_run: true` and `calibration.payoff` set). Human output ends with the **payoff verdict**, one of:

- `selection_saves_time`: `Full suite 4m 12s; proof-ci fixed overhead 2.1s. Affected-test selection will save time here.`
- `marginal`: `Full suite 18s; proof-ci fixed overhead 2.4s. Selection saves little; the flake flags, structured failures, and signed records are the reason to keep proof-ci on this repo.`
- `overhead_dominates`: `Full suite 6s; proof-ci fixed overhead 2.2s. Plain pytest is faster here; keep proof-ci for structure and records only, or run it less often.`

The verdict is a heuristic (Section 5.12, "Payoff"), stated as advice, never as a measured saving.

#### 5.7.9 `proof-ci mcp`

```
proof-ci mcp
```

Starts the MCP server on stdio and does not return until stdin closes. Do not open a TCP port. Do not print banners to stdout (stdout is the MCP transport). Log startup diagnostics to stderr only.

Optional future flag `--http` is out of scope for Phase 1.

### 5.8 Affected-test algorithm

This algorithm is the heart of Phase 1. Implement it in `affected.py` with unit tests for each step.

#### Step 1 — Resolve the base

1. If the caller passed `--base`, use it (`auto` is allowed).
2. Else use `[affected].base` (default `auto`).
3. If the value is `auto`:
   1. Discover the default branch among `origin/HEAD`, `origin/main`, `origin/master`, `main`, `master` (first that `git rev-parse --verify` accepts).
   2. If one exists, compute `M = git merge-base HEAD <default-branch>`. If `M != HEAD`, the base is `M`. If `M == HEAD` (HEAD is on, or fully merged into, the default branch): the base is `HEAD` when the working tree is dirty, else `HEAD~1` when it exists, else `HEAD`.
   3. If no default branch exists: the base is `HEAD` when the working tree is dirty, else `HEAD~1` when it exists, else `HEAD`.
   4. Record the configured value in `selection.base_setting` (`auto`) and the resolved full sha in `selection.base_ref`.
   The intent: a committed change on a clean tree must still select the tests it affects. Agents commit often; `git diff HEAD` on a clean tree is empty and would produce a green run that tested nothing.
4. If the value is `HEAD` or any other ref, use it as the git diff base and record it in both `base_setting` and `base_ref` (`base_ref` is the resolved sha when the value is a symbolic ref).
5. If this is not a git repository: exit 2 unless the command is `run --all` or `run` with explicit tests.

#### Step 2 — Compute changed files

Let `BASE` be the resolved ref.

```
git diff --name-only --diff-filter=ACMRD BASE
```

This includes unstaged and staged changes relative to `BASE` when `BASE` is `HEAD` or an ancestor, because `git diff BASE` compares `BASE` to the working tree.

Add untracked files:

```
git ls-files --others --exclude-standard
```

Normalize to repo-relative forward-slash paths. Ignore files under `.proof-ci/` itself.

If `git` fails, exit 3 with the stderr from git.

#### Step 3 — Conservative full-run fallback

If any changed file matches a glob in `[affected].fallback_full_run_globs` (matched with `fnmatch` semantics plus `**` against the full repo-relative path, so `conftest.py` matches only the root file — use a small helper and test it), select **all** tests. Set `fallback_full_run = true` and `fallback_reason` to a string listing the matching files and globs, for example: `full run because pyproject.toml matched fallback glob 'pyproject.toml'`.

**Incremental rule (normative).** With `base = auto` the changed set covers the whole branch, so a dependency bump committed early on a long branch would otherwise force a full run on every later run until merge. Therefore a matching file fires the fallback only if it differs from the most recent proof-ci run on this branch:

1. Let `F` be the changed files that match a fallback glob, and `T_now` the current tree hash (Section 4.7).
2. Look up the most recent savings-ledger row for the current branch (`branch` and `commit_tree` columns, Section 5.12). If none exists, fire as above (first run on a branch is always honest).
3. Otherwise compute `D = git diff-tree -r --name-only <T_last> <T_now>` (both are tree objects; this also catches untracked files because both trees were built with `add -A`).
4. Fire only if `F ∩ D` is non-empty. If it is empty, do not fire: proceed with ordinary selection, set `fallback_full_run = false`, and set `selection.fallback_suppressed_by_tree = T_last` with the matching files listed in `fallback_reason` prefixed by `suppressed:`.
5. The cache bypass of Section 5.9 follows the fired/suppressed outcome, not the raw glob match.

`T_last` is a tree hash, not a commit, so this works with uncommitted changes and after amends. Document the rule in the README; it is the reason a branch recovers its speed one run after a config change.

A change to root `conftest.py` is a full run. A change to `tests/subpkg/conftest.py` is not matched by the default glob `conftest.py` (which is root-only). Instead, select all tests under `tests/subpkg/` and its descendants. Document and test this special case.

#### Step 4 — Build the Python import graph

Consider `.py` files under `[project].source_roots` and `[project].test_paths`, plus any other changed `.py` files in the repo that are not in a virtualenv or `.proof-ci`.

Ignore directories named `__pycache__`, `.venv`, `venv`, `node_modules`, `.git`, `.proof-ci`, `.tox`, `.nox`, `.mypy_cache`, `.ruff_cache`, `build`, `dist`.

For each Python file:

1. Parse it with `ast.parse`. On `SyntaxError`, treat the file as opaque: it depends on nothing and nothing new can be discovered from it, but a change to it still affects tests that import the module it would have been, and affects the test file itself if it is a test file.
2. Collect `import x`, `import x as y`, `from x import y`, and relative imports. Resolve relative imports using the file’s package (based on parent directories that contain `__init__.py`, or namespace layout under `src/`).
3. Map top-level modules to files by walking `source_roots` and `test_paths` (and the repo root if neither contains the module). `demo.mathops` → `src/demo/mathops.py` or `src/demo/mathops/__init__.py`.
4. Do not execute any user code while building the graph.

The graph is directed: file A depends on file B if A imports the module implemented by B (directly). Transitive dependents are computed by walking the reverse graph.

Dynamic imports (`importlib.import_module`, `__import__`, exec of constructed names) are a known gap. If a file’s AST contains `importlib.import_module` or `__import__`, mark that file `dynamic = true`. Changing a file `X` then selects, in addition to static dependents, every `dynamic` file that lives in the same package prefix as `X` or that is a test file under the matching tests directory. This is conservative, not complete. Mention the limitation in the README.

#### Step 5 — Map tests to code

A “test item candidate” at selection time is a test file (`test_*.py` or `*_test.py` under `test_paths`, following pytest’s default naming). Fine-grained node ids are obtained by asking pytest to collect in a subprocess through the plugin's collect mode (Section 5.10): `<test_command> --collect-only -q -p proof_ci.pytest_plugin -p no:xdist <test_paths>` with `PROOF_CI_COLLECT_FILE` set. Never collect in-process. Cache collection results within a single CLI invocation (one collection per run).

**Collection cache (normative).** Collection imports every test module and is the dominant fixed cost on large suites, so its output is cached across runs under `.proof-ci/collect-cache/<key_hex>.json`:

- `key_hex` is the SHA-256 of the RFC 8785 serialization of `{ "test_files": { path: sha256, ... } (every test_*.py / *_test.py under test_paths), "conftests": { path: sha256, ... } (every conftest.py under the repo, excluding ignored directories), "pytest_config": sha256 of the pytest configuration file in effect or "" , "pytest_args": [...], "test_command": [...] }`.
- The entry stores the collect-mode output (`items`, `python`, `platform`, `inipath`, `installed`) and the time it was written.
- **Environment consistency.** A cached entry supplies `python`/`platform`/`installed` for cache keys. If any test executes in this run, the run-mode results file reports the live values; if they differ from the entry, the entry is deleted, cache keys are recomputed with the live values, any cache hits that were served under the stale values are discarded and executed in a second pytest pass, and the run continues. If the run would execute nothing (every selected test is a cache hit) and the entry is older than `[cache].collect_cache_env_recheck_minutes` (default 60), run the collect pass anyway to refresh the environment before serving hits.
- `--no-cache` bypasses the collection cache too. LRU-trim it with the result cache (Section 5.9).
- Record in `RunSummary.cache` nothing new; log `collection: cached|fresh` to stderr under `PROOF_CI_DEBUG=1`.

A test item *T* is affected if any of the following hold:

1. The test file that contains *T* is in the changed set.
2. Any file *T* imports, directly or transitively, is in the changed set.
3. A non-Python changed file is referenced by *T*’s test file (see Step 6).
4. Full-run fallback (Step 3) fired.

If pytest collection fails, exit 3. Do not pretend an empty suite passed.

#### Step 6 — Non-Python file changes

For each changed file that is not `.py` and did not already trigger a full-run fallback:

1. Skip obvious non-code documentation only if the path matches `*.md`, `*.rst`, `*.txt` under `docs/` or the repo root (for example `README.md`, `CHANGELOG.md`, `LICENSE`). These do **not** select tests. (A markdown file under `tests/` is not skipped.)
2. Otherwise, scan the contents of each test file as text for the changed file’s repo-relative path or its basename. If found, every test item in that test file is affected.
3. If no test file mentions it, do **not** select extra tests. Record this in debug logs only (stderr when `PROOF_CI_DEBUG=1`). This is an accepted source of under-selection; document it.

#### Step 7 — Empty selection

If nothing is affected, `proof-ci run` does not invoke pytest for execution, writes a `RunSummary` with zero selected tests, `outcome: "passed"`, and exits 0. The summary line is `0 selected, 0 failed (nothing affected)`, and stderr states the resolved base and whether the working tree was clean, so a reader can tell *why* nothing was selected. A zero-test run is never signed: `run --sign` exits 2 with `record_path: null` (Section 5.7.3 step 10), `record sign` refuses it, and MCP `run_affected_tests` with `sign: true` returns a tool error naming the `run_id`. A green record must mean at least one test was checked.

### 5.9 Caching keys

A cache entry is stored at:

```
<cache.dir>/<key_hex[:2]>/<key_hex>.json
```

where `key_hex` is the lowercase hex SHA-256 of the **cache key material**, itself a canonical JSON object (RFC 8785) with these keys, in this meaning:

```json
{
  "test_id": "tests/test_math.py::test_add",
  "input_hashes": {
    "tests/test_math.py": "<sha256 hex of file bytes>",
    "tests/conftest.py": "<sha256 hex of file bytes>",
    "pyproject.toml": "<sha256 hex of file bytes>",
    "src/demo/mathops.py": "<sha256 hex of file bytes>"
  },
  "pytest_args": ["-q"],
  "python": "3.11.9",
  "platform": "Linux",
  "deps_hash": "<sha256 hex>"
}
```

Rules:

- `input_hashes` includes, keyed by repo-relative path and sorted by path (canonical JSON will sort keys):
  1. the test file;
  2. every file in its transitive import closure that was resolved to a path inside the repo (Section 5.8 Step 4);
  3. every `conftest.py` on the path from the test file's directory up to the repository root, inclusive (pytest loads them all, and fixtures change outcomes without being imported);
  4. the pytest configuration file in effect, whichever of `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg` pytest reports as its `inifile` (the collect-mode plugin records `config.inipath`); and
  5. every non-Python file that Section 5.8 Step 6 associates with this test file (a file whose repo-relative path or basename appears in the test file's text), when that file exists in the repo.
- `python` is `platform.python_version()` **of the interpreter that runs pytest**, reported by the plugin in collect mode (Section 5.10), not of the process running `proof-ci` (they may differ).
- `platform` is `platform.system()` (e.g. `Linux`, `Darwin`, `Windows`).
- `pytest_args` is the configured list, not ephemeral flags like `--json`.
- `deps_hash` is the SHA-256 over the concatenation of (a) any of the following files that exist, in this order, each prefixed by its path and a newline: `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements.txt`; followed by (b) the line `installed\n` and the sorted `name==version` lines of `importlib.metadata.distributions()` in the interpreter that runs pytest, reported by the plugin in collect mode. Part (b) makes `pip install` of an unlocked dependency change the key. If no lockfile exists, part (a) is empty and the field is still present.
- Do not include wall-clock time, run id, or hostname.

What may be cached: `passed`, `skipped`, `xfailed`, `xpassed`. What must not be cached in Phase 1: `failed` and `error` (a replayed failure can hide a fix that lives outside the key's inputs, and collection/setup failures are often transient), and any result whose test is `suspected` or `confirmed` flaky. Cache reads are also skipped for the whole run when `selection.fallback_full_run` is true (a configuration change forced the full run precisely so that everything executes); writes are still allowed. When the incremental rule of Section 5.8 Step 3 suppresses a fallback (`fallback_suppressed_by_tree` set), the cache is read normally: the matching files are byte-identical to the last run, and they are part of every key anyway.

A cache hit replays the stored `TestResult` but updates `cache_hit` to `true`. It still counts toward flake history as a replayed outcome **only if you re-execute**. Do **not** write replayed cache hits into the flake history — otherwise a cached pass/fail pair cannot arise, and caching would freeze flake detection. Document this interaction in a comment near the code.

`--no-cache` skips both reads and writes.

There is no automatic cache GC in Phase 1 beyond: if the cache directory exceeds 500 MB, delete least-recently-used entries until it is under 400 MB. Update mtime on hit so LRU works.

### 5.10 pytest integration

Implement `src/proof_ci/pytest_plugin.py` (and `src/proof_ci/adapters/runners/pytest_runner.py` as the `TestRunnerAdapter`). Do **not** register a `pytest11` entry point; the runner always passes `-p proof_ci.pytest_plugin`. The plugin module MUST import only the standard library and pytest (no pydantic, typer, or other proof-ci dependencies) so it loads cheaply inside the project's pytest process and cannot shadow project packages.

**Subprocess only.** pytest always runs as a child process of `proof-ci`, launched from `[project].test_command` with the repository root as the working directory. Rationale: (1) `test_command` may name a different interpreter than the one running `proof-ci`; (2) the MCP server is long-lived and repeated `pytest.main()` calls in one process leak `sys.modules` and plugin state; (3) a crash in a test cannot take the MCP server down. Consequence: `proof-ci` (at least `proof_ci.pytest_plugin`) MUST be importable by the interpreter that `test_command` resolves to, so the package is installed into the project's own environment (`pip install proof-ci`, `uv add --dev proof-ci`, or `pip install -e .` from a checkout). Before the first collection in a run, the runner MUST preflight `<test_command> --version` and, if `PROOF_CI_COLLECT_FILE` is not written by the collect step, exit 3 with the message `proof-ci's pytest plugin is not importable by '<test_command>'; install proof-ci into the project's environment`.

**Activation.** The plugin is inert unless one of these environment variables is set in the pytest process:

| Variable | Mode | What the plugin writes at `pytest_sessionfinish` |
| --- | --- | --- |
| `PROOF_CI_COLLECT_FILE=<path>` | collect | `{ "python": platform.python_version(), "platform": platform.system(), "inipath": "<repo-relative or null>", "installed": ["name==version", ...sorted], "items": [ { "test_id", "file", "line" }, ... ] }` |
| `PROOF_CI_RESULTS_FILE=<path>` | run | `{ "python": ..., "platform": ..., "results": [ TestResult-shaped objects without `cache_hit`/`flaky` ] }` |

The runner passes the path under `.proof-ci/runs/<run_id>/` (`collect.json`, `plugin-results.json`), waits for the subprocess to exit, then parses the file. Files are written as UTF-8 with `json.dump(..., ensure_ascii=False)`. The runner never parses pytest's stdout for results.

The plugin MUST:

1. Hook `pytest_runtest_logreport` (or `pytest_runtest_makereport` with a wrapper) and keep the `call` phase as the primary outcome. If `setup` failed, the outcome is `error`. If `teardown` failed after a passing `call`, the outcome is `error` and `message` notes teardown.
2. Populate `TestResult` fields from `report.nodeid`, `report.duration` (convert seconds to integer milliseconds by rounding), `report.location`, `report.longrepr`, and captured stdout/stderr (`report.capstdout` / `report.capstderr` when present). Normalise `report.location`: `location[0]` is OS-native (`tests\\test_a.py` on Windows) and relative to the rootdir, so convert it to a forward-slash repo-relative path; `location[1]` is 0-based, so `TestResult.location.line = location[1] + 1` (Section 5.20). The runner then canonicalises the path part of every `test_id` and `location.file` to git's committed case (Section 5.20 rule 1) before the result enters any contract or store.
3. Truncate stdout/stderr to 8192 characters each and run them through the redaction rules of Section 5.15 before storage. Because the plugin may not import `proof_ci.redact` (it depends only on stdlib and pytest), the redaction regexes live in a stdlib-only module `proof_ci/_redact_patterns.py` that both `redact.py` and the plugin import.
4. Build `Failure` when needed: take `error_type` and `error_message` from `call.excinfo` in a `pytest_runtest_makereport` hookwrapper (fall back to parsing `longreprtext` for setup/teardown errors); attach `longreprtext` as `traceback`; set `repro_command` (Section 4.2 and Section 5.20 for quoting); leave `likely_cause` to the runner, which fills it after it sees the diff.
5. In collect mode, record every collected item at `pytest_collection_finish` (`item.nodeid`, `item.location`) plus `config.inipath` and the installed-distribution list.
6. Remain silent on pytest’s own terminal output beyond what pytest already prints. The runner can call pytest with `-q` or `--override-ini=addopts=` as needed so human pytest noise does not drown JSON mode. When `proof-ci run --json` is set, pass `-q` and `--tb=short` unless the user already set `pytest_args` that conflict.

Fallback: if the results file is missing or unparsable after pytest exits (the plugin did not load, the interpreter could not import it, or pytest crashed before `pytest_sessionfinish`), the runner MUST exit 3 with pytest's stderr rather than parse junitxml as a silent downgrade. (junitxml loses `likely_cause` and redaction. Do not pretend it is equivalent.)

**pytest-xdist passthrough.** Users put `-n auto` (or `-n 4`) in `[project].pytest_args`; proof-ci passes it through unchanged on run-mode invocations and adds `-p no:xdist` on collect-mode invocations (collection is serial). The plugin's design assumes that with xdist the controller process receives every worker's `TestReport` through `pytest_runtest_logreport` and `pytest_runtest_makereport` is **not** called on the controller for worker-executed tests, so `error_type`/`error_message` must also be derivable from the report (`report.longrepr.reprcrash.message` / `longreprtext`) when `call.excinfo` is unavailable. **This assumption is marked to-verify in M4**: the M4 tests include a run with `-n 2` against the demo project asserting that every `TestResult` is present with a `Failure` for the deliberate failure. If the assumption fails, the plugin must register a worker-side hook that serialises `Failure` fields into `report.user_properties`, which xdist does transport. `duration_ms` under xdist is per-test and unaffected; the run's wall clock is shorter, which the `wait` metric reflects honestly.

### 5.11 Flake detection algorithm and SQLite schema

Database file: `[flakes].db`, default `.proof-ci/flakes.sqlite`. Use the stdlib `sqlite3` module. Enable foreign keys. Create tables if they do not exist on first use.

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_outcomes (
    id INTEGER PRIMARY KEY,
    test_id TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    outcome TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    run_id TEXT NOT NULL,
    commit_sha TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_outcomes_test_code
    ON test_outcomes (test_id, code_hash, started_at);

CREATE TABLE IF NOT EXISTS flake_records (
    test_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_code_hash TEXT NOT NULL,
    pass_count INTEGER NOT NULL,
    fail_count INTEGER NOT NULL,
    flip_count INTEGER NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0
);
```

`code_hash` for a test is the SHA-256 hex of the RFC 8785 serialization of `{ "test_id": ..., "input_hashes": { ... } }` — the same `input_hashes` as the cache key in Section 5.9 (test file, import closure, `conftest.py` chain, pytest configuration file, referenced non-Python files), **without** `python`, `platform`, `pytest_args`, or `deps_hash`. The point of `code_hash` is “did this test’s source inputs change?”, not “did the interpreter change?”.

On every freshly executed (not cache-replayed) result with outcome `passed`, `failed`, `xpassed`, or `xfailed`:

1. Insert a `test_outcomes` row. Treat `xpassed` as a pass and `xfailed` as a fail for counting purposes, but store the raw outcome string.
2. Recompute the `FlakeRecord` for that `test_id` as follows.

**Detection (normative):**

Let `W` be now minus `window_days`. Consider rows for this `test_id` with `started_at >= W`.

Group those rows by `code_hash`. For each group:

- `pass_count` = number of pass-like outcomes.
- `fail_count` = number of fail-like outcomes (`failed` or `xfailed`).
- Sort the group by `started_at`. `flip_count` = number of adjacent pairs with different pass/fail categories (ignore `skipped` rows in the sequence).

The test is a **candidate** if there exists a `code_hash` group with `pass_count >= 1` and `fail_count >= 1` and `(pass_count + fail_count) >= min_runs`.

Status transitions, applied after each insert, unless `manual = 1` (set by `flakes mark`; `unmark` sets `manual = 0` and `status = resolved`):

| Current status | Condition | Next status |
| --- | --- | --- |
| (no row) | candidate, and max group `flip_count` < `min_flips_confirmed` | `suspected` |
| (no row) | candidate, and max group `flip_count` >= `min_flips_confirmed` | `confirmed` |
| `suspected` | candidate and max `flip_count` >= `min_flips_confirmed` | `confirmed` |
| `suspected` or `confirmed` | not a candidate in the window, and the last `min_runs` outcomes for the latest `code_hash` are all pass-like or all fail-like | `resolved` |
| `resolved` | becomes a candidate again | `suspected` (or `confirmed` if flips already meet the threshold) |
| `ignored` | never auto-changed | `ignored` |

`pass_count`, `fail_count`, and `flip_count` stored on `flake_records` are those of the `code_hash` group with the greatest `flip_count` in the window (ties: the most recently used hash). `last_code_hash` is that group’s hash. `reason` for automatic updates should be a short generated sentence, e.g. `passed 4 and failed 2 times on unchanged code hash abcd2345… (3 flips)`.

Defaults: `min_runs = 3`, `window_days = 30`, `min_flips_confirmed = 2`.

### 5.12 Savings: calibration, estimation, and ledger

Implement this in `src/proof_ci/savings.py`. Every completed run after milestone M9 produces a `Savings` object using the rules below. Figures that cannot be measured are reported as `0` with `status: "unavailable"` or `status: "estimated"` and low confidence — never invented from an assumed industry average. Metrics are reported in the order of Section 4.5: wrong turns, CI runs avoided, local compute, wait, tokens. The headline leads with time and counts; dollars appear only when `prices_configured` is true.

#### Calibration

A calibration is a measured full-suite run whose purpose is to store per-test durations and a full-suite wall clock.

**When it runs**

- `proof-ci savings calibrate` always performs a calibration.
- `proof-ci run --all` refreshes calibration when `[savings].refresh_calibration_on_all` is true (the default).

**How it runs**

1. Select every collected test (mode `all`). Do not serve cache hits; write-through to the cache after execution is allowed.
2. For each test that reaches an outcome of `passed`, `failed`, `skipped`, `xfailed`, or `xpassed`, upsert `calibration_tests` with `test_id`, `duration_ms`, `measured_at`, and `run_id`.
3. Write `calibration_meta` with `calibrated_at` (now, UTC), `wall_ms` (the wall clock of the pytest subprocess for the whole suite), `fixed_overhead_ms` (wall clock of everything else proof-ci did in this run: git, hashing, the collect pass, cache and ledger I/O), `test_count`, `commit_sha`, `python`, `platform`, and `run_id`.
4. Mark the `Savings` object `is_calibration_run: true`. Compute savings as usual except `test_ms_not_executed = 0` and `wait_ms_saved = 0` (this run *is* the baseline; claiming it saved a full suite against itself would be circular).

**Payoff (heuristic, normative wording).** From `wall_ms` and `fixed_overhead_ms`: `overhead_dominates` when `wall_ms < 3 * fixed_overhead_ms`; `marginal` when `wall_ms < 10 * fixed_overhead_ms`; otherwise `selection_saves_time`; `unknown` when calibration is missing. Store it in `calibration_meta.payoff`, copy it to `Savings.calibration.payoff` on every run, print the verdict sentences of Section 5.7.8 after `savings calibrate`, and repeat the verdict once in `proof-ci run` human output when it is `overhead_dominates` (so a user on a six-second suite learns immediately that plain pytest is faster). The verdict is advice about this repository, not a saving, and is never priced.

**Freshness**

- `missing` — no `calibration_meta` row exists.
- `stale` — `now - calibrated_at` exceeds `calibration_max_age_days`, or more than `calibration_stale_after_runs` non-calibration runs have occurred since `calibrated_at`.
- `current` — otherwise.

Optional `[savings].full_pipeline_baseline_ms`, when set to an integer greater than zero, is a user-measured CI pipeline wall time (queue plus run). It is used for wait-time and avoided-CI-run costing in preference to `calibration_meta.wall_ms`. Record that choice in the metric `rationale`. It is still a measured input from the user, not a built-in default.

#### Per-run estimation

Walk the five metrics. Convert configured dollar prices to integer micro-USD with `micro = round(dollars * 1_000_000)` (half away from zero; use `decimal.Decimal.quantize(ROUND_HALF_UP)`, not the built-in `round`) before any arithmetic. Cost formulas use integer division:

`ci_cost = (quantity_ms * ci_cost_per_minute_micro_usd) // 60_000`

`local_cost = (test_ms_not_executed * local_compute_cost_per_minute_micro_usd) // 60_000` (only when `price_local_minutes` is true)

`token_cost = (max(0, tokens_delta) * model_input_cost_per_million_tokens_micro_usd) // 1_000_000`

Phase 1 prices token savings as **input** tokens (what the agent did not have to ingest). `output` rate is recorded in `prices_used` for honesty; `tokens.cost_micro_usd` uses the input rate. Set unused `engineer_hourly_cost_micro_usd` to `0`.

**`defaults_used` and `prices_configured`.** `defaults_used` lists the `[savings]` price key names that are **absent from `proof-ci.toml`** (`ci_cost_per_minute`, `model_input_cost_per_million_tokens`, `model_output_cost_per_million_tokens`, `wasted_fix_cost`, and `local_compute_cost_per_minute` when `price_local_minutes` is true). A key present with any value, including a value equal to the default, is not listed. `prices_configured` is true when `defaults_used` is empty. `engineer_hourly_cost` and `full_pipeline_baseline_ms` never appear (they have no billed default).

**(a) Wrong turns avoided.** `flagged_known_flake_failures` is the number of results in this run whose `outcome` is `failed` or `error` and whose `flaky.status` is `confirmed` or `ignored` (not `suspected`). `cost_micro_usd = count * wasted_fix_cost_micro_usd`. This count is `measured` with `confidence = high` (we observed the flags). The *valuation* uses a configured price; if `wasted_fix_cost` is absent from the TOML, it is in `defaults_used`.

**(b) Full CI runs avoided (deduplicated).** A run *qualifies* if its `outcome` is `failed` and `counts.failed - counts.flaky_failures > 0`. For a qualifying run compute `dedupe_key = sha256(rfc8785({ "branch": branch, "commit_tree": commit_tree, "failing": sorted non-flaky failing test_ids }))`. If a ledger row with the same `ci_runs_dedupe_key` already exists, `count = 0` and `deduplicated = true` (the agent is iterating on a failure it has already seen; only the first observation is a CI run it would have pushed). Otherwise `count = 1`. Non-qualifying runs have `count = 0`, `dedupe_key = null`, `deduplicated = false`. `count = 1` is `estimated` with `confidence = medium` (we cannot know the user would have pushed); `count = 0` is `measured` with `confidence = high`. Cost a counted run as `(baseline_ms * ci_cost_per_minute_micro_usd) // 60_000` using the same baseline as (d); if no baseline exists, `cost_micro_usd = 0` rather than inventing one.

**(c) Local compute not executed.** `test_ms_not_executed` is the sum of calibrated `duration_ms` for tests deselected by affected-test selection, plus calibrated `duration_ms` (falling back to the cached result’s `duration_ms` if the test is absent from calibration) for cache hits. Skip any deselected test that has no calibrated duration; count those in `calibration.uncalibrated_skipped_count`. This is time on the **user's machine**, never CI time; the rationale must say so. `priced` is `[savings].price_local_minutes`; when false, `cost_micro_usd = 0` by rule.

- If calibration is `current` and `uncalibrated_skipped_count == 0`: `status = measured`, `confidence = high`.
- If calibration is `current` but some skipped tests were uncalibrated: `status = estimated`, `confidence = medium`.
- If calibration is `stale`: `status = estimated`, `confidence = medium`.
- If calibration is `missing`: set `test_ms_not_executed = 0`, `cost_micro_usd = 0`, `status = estimated`, `confidence = low`. Do not invent a per-test duration.
- Calibration runs: `test_ms_not_executed = 0`, `status = measured`, `confidence = high`, rationale explains the circularity rule.

**(d) Wait versus the full local suite.** `actual_ms` is this run’s wall clock. `baseline_kind` and `baseline_ms`: `full_pipeline_user` and `[savings].full_pipeline_baseline_ms` if set; else `full_suite_local` and `calibration_meta.wall_ms`; else `none` and `null`. `wait_ms_saved = max(0, baseline_ms - actual_ms)`.

- `full_pipeline_user`: `status = measured`, `confidence = high` (the number is the user's own measurement of their pipeline).
- `full_suite_local` from a `current` calibration: `status = measured`, **`confidence = medium` at most** (it is the time the whole suite takes on this machine, a proxy for what the user would otherwise wait for, not CI queue time); the rationale says "versus running the full suite locally".
- `full_suite_local` from a stale calibration: `status = estimated`, `confidence = low`.
- `none`: `wait_ms_saved = 0`, `status = unavailable`, `confidence = low`.
- Calibration runs: `wait_ms_saved = 0` as above.

**(e) Tokens versus pytest's own output.** `raw_bytes` is the UTF-8 byte length of the **actual stdout of the pytest subprocess** for this run at the configured verbosity (the runner already captures it; for cache-replayed tests nothing was printed, so nothing is added). `structured_bytes` is the byte length of the `RunSummary` JSON without the `savings` object (keep `savings_headline`) to avoid a circular size comparison. `raw_tokens = token_count(raw_bytes)`, `structured_tokens = token_count(structured_bytes)`, `tokens_delta = raw_tokens - structured_tokens` (signed; negative on most green runs and small runs), `cost_micro_usd` from `max(0, tokens_delta)`. `status = measured` (the bytes are real), `confidence = medium` (the tokenizer is an approximation). Rationale names the source, the verbosity flags in effect, and the tokenizer. Never reconstruct a verbose log as the baseline.

**Totals.** `cost_micro_usd` is the sum of `wrong_turns.cost_micro_usd`, `ci_runs_avoided.cost_micro_usd`, `local_compute.cost_micro_usd`, and `tokens.cost_micro_usd`. Do not convert `wait_ms_saved` into dollars (wait is time, not a charge). Copy the five quantity fields into `totals` (`wrong_turns_avoided`, `ci_runs_avoided`, `test_ms_not_executed`, `wait_ms_saved`, `tokens_delta`).

**Overall confidence.** The minimum of wrong-turns, ci-runs-avoided, local-compute, and wait confidences, except that `unavailable` wait does not by itself cap overall below the others. Token confidence (always medium) does not cap overall below medium. If calibration is `missing`, overall is `low`. If calibration is `stale`, overall is at most `medium`.

**Headline (normative pattern).** Time and counts first; money only when `prices_configured` is true.

`Checked {selected}/{collected} ({hits} cached) in {human_duration}; flaky flagged {wrong_turns}; CI runs avoided {ci_runs}; local test-min not run ~{local_minutes}{calibration_note}{money}`

- Use thousands separators in the counts. `human_duration` is a short wall-clock such as `22s` or `1m 04s`. `local_minutes` is `test_ms_not_executed // 60000` (show `<1` if positive but under one minute).
- `calibration_note` is ` (uncalibrated)` when calibration is `missing`, ` (stale calibration)` when `stale`, else empty.
- `money` is `; ~${dollars} est.` with two decimals, present **only** when `prices_configured` is true and `totals.cost_micro_usd > 0`; it is always suffixed `est.` because every cost is a configured price times an estimate. When `prices_configured` is false, no dollar sign appears anywhere in the headline.
- `tokens_delta` does not appear in the headline (it is small, often negative, and would invite the straw-man comparison); it is in the object and the report.
- Example (configured prices): `Checked 14/1,200 (12 cached) in 22s; flaky flagged 1; CI runs avoided 0; local test-min not run ~18; ~$25.00 est.`
- Example (fresh install): `Checked 14/1,200 (12 cached) in 22s; flaky flagged 1; CI runs avoided 0; local test-min not run ~18`.

#### SQLite ledger

Database file: `[savings].db`, default `.proof-ci/savings.sqlite`. Same stdlib `sqlite3` rules as the flake store (create-on-first-use, foreign keys on).

```sql
CREATE TABLE IF NOT EXISTS calibration_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    calibrated_at TEXT NOT NULL,
    wall_ms INTEGER NOT NULL,
    fixed_overhead_ms INTEGER NOT NULL,
    payoff TEXT NOT NULL,
    test_count INTEGER NOT NULL,
    commit_sha TEXT,
    python TEXT,
    platform TEXT,
    run_id TEXT
);

CREATE TABLE IF NOT EXISTS calibration_tests (
    test_id TEXT PRIMARY KEY,
    duration_ms INTEGER NOT NULL,
    measured_at TEXT NOT NULL,
    run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS savings_ledger (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    commit_sha TEXT,
    commit_tree TEXT,
    branch TEXT,
    agent_id TEXT,
    agent_name TEXT,
    headline TEXT NOT NULL,
    overall_confidence TEXT NOT NULL,
    prices_configured INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    selected_count INTEGER NOT NULL,
    collected_count INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    wrong_turns_avoided INTEGER NOT NULL,
    ci_runs_avoided INTEGER NOT NULL,
    ci_runs_dedupe_key TEXT,
    test_ms_not_executed INTEGER NOT NULL,
    wait_ms_saved INTEGER NOT NULL,
    tokens_delta INTEGER NOT NULL,
    cost_micro_usd INTEGER NOT NULL,
    defaults_used TEXT NOT NULL,
    savings_json TEXT NOT NULL,
    record_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_savings_time ON savings_ledger (recorded_at);
CREATE INDEX IF NOT EXISTS idx_savings_agent ON savings_ledger (agent_id);
CREATE INDEX IF NOT EXISTS idx_savings_branch ON savings_ledger (branch, recorded_at);
CREATE INDEX IF NOT EXISTS idx_savings_dedupe ON savings_ledger (ci_runs_dedupe_key);
```

`defaults_used` is stored as a JSON array of strings. `savings_json` is the full `Savings` object. `branch` is `git rev-parse --abbrev-ref HEAD` at run time, or `HEAD` when detached. `commit_tree` is the tree hash of Section 4.7 (null when git was unavailable); the incremental fallback rule of Section 5.8 Step 3 reads the most recent row for the branch. `ci_runs_dedupe_key` supports Section 5.12(b). `agent_name` is the generic field from identity resolution. Cache-replayed runs still write a ledger row (they produced a `RunSummary`); they do not refresh calibration. The `SavingsReport` sums `tokens_delta` as a signed integer and reports `prices_configured` as the conjunction over included rows.

### 5.13 CI config discovery


Module: `discover.py` is a facade: it loads every detected `CICommandSource` adapter (Section 3.5) and ranks their candidates. Phase 1 ships GitHub Actions and GitLab CI sources. Later adapters (Jenkins, CircleCI, Buildkite) plug in without changing this facade. Read-only. Never execute a discovered command as part of discovery itself.

Supported inputs:

- GitLab: the file at `[discovery].gitlab_ci` if it exists. Parse with PyYAML (`safe_load`). Walk top-level keys that look like jobs (exclude reserved keys: `stages`, `variables`, `include`, `default`, `workflow`, `image`, `services`, `before_script`, `after_script`, `cache`, `pages` as a special job is still a job — include `pages` only if its script looks like tests; by default skip job names matching `^(pages|deploy|release|publish|notify|cleanup)\b` case-insensitive).
- GitHub Actions: every `*.yml` and `*.yaml` in `[discovery].github_workflows`. For each job, for each step with a `run:` string, consider that string.

A command is a **candidate** if, after stripping comments and leading `set -` noise, it matches at least one of:

- contains `pytest` as a word
- contains `python -m pytest` or `python3 -m pytest`
- contains `hatch test` or `hatch run test`
- contains `nox` with an argument suggesting tests
- contains `tox` (any) — confidence medium
- equals `make test` or `make check` — confidence medium

Confidence:

- high: `pytest` or `python -m pytest`
- medium: `tox`, `nox`, `hatch test`, `make test`
- skip: scripts that also mention `deploy`, `terraform apply`, `kubectl`, `helm`, `docker push`, or a job `environment:` whose name contains `prod`.

`proof-ci init --discover` writes the highest-confidence unique command into `[project].test_command`. If several high-confidence commands differ only by flags, prefer the shortest. Discovery results can also be printed by a hidden/debug path; a public `proof-ci discover` command is optional and not required in Phase 1. If you add it, it must be read-only and support `--json`.

The runner uses `[project].test_command` as the prefix when invoking tests, always as a subprocess (Section 5.10). Phase 1 only supports commands whose executable is `pytest` or `python -m pytest` / `python3 -m pytest` (with optional extra args), and the interpreter behind that command must be able to import `proof_ci.pytest_plugin`. If the configured command is something else (`tox`, `make test`), Phase 1 MUST print a clear error (exit 2) telling the user to set `test_command = ["pytest"]` or an equivalent pytest invocation. Do not shell out to `make` or `tox` in Phase 1 — discovery may *suggest* them, but execution stays on pytest so the plugin always loads.

### 5.14 MCP tools

Implement with the official MCP Python SDK (`mcp` package) over stdio, from the agent adapter package (Section 3.5).

Server name: `proof-ci`. Announce the six tools below. Each tool’s input and output must match these schemas. Tool descriptions MUST be vendor-neutral: they must not name Claude, Cursor, Codex, Windsurf, Cline, Copilot, or any other agent product. Errors from the tool (bad args, no last run) are MCP tool errors with a readable message, not a crashed server.

**Output form (normative).** Every tool returns its output object as JSON text (`json.dumps(obj, ensure_ascii=False, indent=2)`) in a single `TextContent` block. Where the pinned SDK (`mcp>=1.2,<2`, Section 5.3) supports tool output schemas, the tool also declares the output schema and returns the same object as `structuredContent`; the text block is always present so that a client that ignores structured output still gets the full object. Tools MUST NOT split one result across several content blocks or add prose around the JSON.

**Process model.** `run_affected_tests` runs pytest as a subprocess (Section 5.10) and blocks until it finishes; the server stays responsive to other requests. Long suites are the client's timeout problem, so the tool description states that `all: true` can take as long as the full suite.

Working directory: the process current working directory, which any MCP client will set to the project root. Honor `--repo` only via the `repo` argument when present.

Shared optional input field on every tool: `repo` (string, optional) — override repository root.

#### Tool `run_affected_tests`

Description: Select tests affected by the current change and run them. Returns a short `RunSummary`. Use `get_failure_details` or `explain_failure` for one test’s full diagnosis.

Input:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "repo": { "type": "string" },
    "base": { "type": "string", "description": "Git ref, HEAD, or auto." },
    "all": { "type": "boolean", "default": false },
    "failed": { "type": "boolean", "default": false, "description": "Rerun only the tests that failed or errored in the previous run." },
    "no_cache": { "type": "boolean", "default": false },
    "allow_known_flakes": { "type": "boolean", "default": false },
    "sign": { "type": "boolean", "default": false },
    "agent_id": { "type": "string" },
    "tests": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Explicit pytest node ids or paths. If set, selection mode is explicit."
    }
  }
}
```

Output: a `RunSummary` object as defined in Section 4.3.

#### Tool `get_failure_details`

Description: Return the full `TestResult` for one test from a run (default: the last run).

Input:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["test_id"],
  "properties": {
    "repo": { "type": "string" },
    "test_id": { "type": "string" },
    "run_id": { "type": "string", "description": "Defaults to the last run." }
  }
}
```

Output: a `TestResult` object. If the test passed or was skipped, still return the object. If unknown, return a tool error.

#### Tool `list_flaky_tests`

Description: List locally known flaky tests.

Input:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "repo": { "type": "string" },
    "status": {
      "type": "string",
      "enum": ["suspected", "confirmed", "resolved", "ignored", "all"]
    }
  }
}
```

Output: `{ "flakes": [ FlakeRecord, ... ] }`.

#### Tool `verify_record`

Description: Verify an Ed25519-signed RunRecord file.

Input:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["path"],
  "properties": {
    "repo": { "type": "string" },
    "path": { "type": "string" },
    "pubkey": { "type": "string", "description": "Optional path to a PEM public key." },
    "require_known_key": { "type": "boolean", "default": false }
  }
}
```

Output: `{ "valid": true, "key_id": "...", "run_id": "...", "signer": "known" | "unknown" }` or `{ "valid": false, "reason": "..." }`. Trusted keys come from `pubkey` when given, else `[record].trusted_pubkeys` (Section 4.7).

#### Tool `explain_failure`

Description: Explain one failing test in short, agent-oriented language: what failed, where, the likely cause in the diff, whether it is flaky, the repro command, and suggested next steps. Prefer this over dumping logs into the context window.

Input: same as `get_failure_details`.

Output:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "test_id",
    "outcome",
    "summary",
    "repro_command",
    "is_flaky",
    "next_steps"
  ],
  "properties": {
    "test_id": { "type": "string" },
    "outcome": { "type": "string" },
    "summary": { "type": "string" },
    "likely_cause": { "type": ["object", "null"] },
    "repro_command": { "type": "string" },
    "is_flaky": { "type": "boolean" },
    "flake_status": { "type": "string" },
    "related_diff": { "type": ["string", "null"] },
    "next_steps": {
      "type": "array",
      "items": { "type": "string" }
    }
  }
}
```

`next_steps` is generated by simple rules, not an LLM:

- If `is_flaky` and status is `confirmed` or `ignored`: tell the agent not to rewrite production code solely to satisfy this test; suggest rerunning once and, if it passes, ignoring it or marking it.
- If `likely_cause` is present: tell the agent to inspect that file and line first.
- Always include “rerun with the repro command to confirm.”
- If no `likely_cause`: tell the agent the failure did not intersect the current diff and to consider an environmental issue or an incomplete import graph.


#### Tool `get_savings_report`

Description: Return an aggregated savings report from the local ledger for a time window. Use this instead of inventing cost or token-savings figures.

Input:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "repo": { "type": "string" },
    "since": {
      "type": "string",
      "description": "RFC 3339 timestamp or relative window such as 7d or 4w. Default 30d."
    },
    "until": { "type": "string" },
    "by": { "type": "string", "enum": ["repo", "agent", "branch"] },
    "weekly": { "type": "boolean", "default": false }
  }
}
```

Output: a `SavingsReport` object as defined in Section 4.5.

Claude Code configuration snippet to document in README, `AGENTS.md`, and `CLAUDE.md` (the command is the same for every MCP client):

```json
{
  "mcpServers": {
    "proof-ci": {
      "command": "proof-ci",
      "args": ["mcp"]
    }
  }
}
```

The same `command` / `args` work for Cursor, Codex, Windsurf, Cline, and VS Code Copilot agent mode. Do not document a Claude-only installation path without also documenting the generic MCP stdio invocation.

Because `proof-ci` is installed in the project's environment (Section 5.10), `command` should be the environment's own executable when that environment is not on the client's `PATH`. Document the native Windows form alongside the POSIX one (JSON requires escaped backslashes; forward slashes also work on Windows):

```json
{
  "mcpServers": {
    "proof-ci": {
      "command": "C:\\path\\to\\project\\.venv\\Scripts\\proof-ci.exe",
      "args": ["mcp"]
    }
  }
}
```

An equivalent that avoids the `.exe` path is `"command": "C:/path/to/project/.venv/Scripts/python.exe", "args": ["-m", "proof_ci", "mcp"]`. On POSIX the same shape uses `.venv/bin/proof-ci` or `.venv/bin/python -m proof_ci mcp`.

### 5.15 Security considerations (Phase 1)

1. **No network listener.** The MCP server is stdio only.
2. **No telemetry, no automatic update check, no crash reporter.**
3. **Private keys** are created owner-only (mode `0600` on POSIX; owner-only ACL via `icacls` on Windows, Section 4.7), never printed, never included in records, never returned by MCP tools. `verify_record` and `record verify` only ever handle public keys plus the signature value.
4. **Output redaction.** Before storing stdout/stderr/traceback, replace spans that look like secrets with `***`. At minimum, redact:
   - Tokens matching `(?i)(api[_-]?key|secret|password|token|authorization|bearer)["'=\s:]+[^\s"'\\]{8,}`
   - Strings matching `AKIA[0-9A-Z]{16}`
   - Strings matching `ghp_[A-Za-z0-9]{20,}` and `glpat-[A-Za-z0-9_\-]{20,}`
   - JWT-shaped `eyJ`…`.`…`.`… strings
   Redaction is best-effort, not a guarantee. Document that.
5. **Path confinement.** Test ids, explicit `TESTS` / `tests` arguments, `--output`, and `--record-out` must resolve inside the repository (or, for key paths, the user-specified key location). Refuse path traversal that would read or write outside the repo for run artifacts. Reject any test argument that begins with `-` (it would be parsed by pytest as an option). `record verify PATH` and MCP `verify_record.path` are read-only and may point anywhere.
6. **Untrusted repo content is data.** Do not `eval` or `exec` repository files while building the import graph or discovering CI. AST parse and YAML `safe_load` only.
7. **Discovery never executes jobs** and never selects deploy/release/publish jobs as the test command.
8. **Phase 1 is not a sandbox.** README and `AGENTS.md` must say so in plain language: running `proof-ci run` executes the project’s tests with the same privileges as `pytest`.
9. **Do not persist environment variables** into records or summaries.

### 5.16 Testing strategy

All of the following must exist and pass before Phase 1 is done.

**Unit tests**

- Config loading, defaults, and repo-root discovery.
- Pydantic models accept the example JSON in Section 4 and reject documents with extra required-field omissions.
- Schema files in `src/proof_ci/schemas/` validate those examples (use a draft 2020-12 capable validator; `jsonschema` is acceptable as a test-only dependency).
- Import-graph construction from a temporary directory of Python files, including relative imports and a syntax-error file.
- Affected selection: source change selects importing tests; unrelated change selects nothing; `pyproject.toml` change selects all; root `conftest.py` selects all; nested `conftest.py` selects that subtree; `README.md` selects nothing; a data file mentioned in a test file selects that file’s tests; a data file mentioned nowhere selects nothing.
- Base resolution (`auto`): a committed change on a clean feature branch selects the tests it affects (merge-base); a committed change on a clean default branch uses `HEAD~1`; a dirty tree on the default branch uses `HEAD`; `base_setting` and the resolved `base_ref` are recorded.
- Incremental fallback: on a branch whose first commit changed `pyproject.toml`, the first run is a full run; the second run on the same branch with only a source edit is **not** a full run and records `fallback_suppressed_by_tree`; editing `pyproject.toml` again fires the fallback again.
- Collection cache: the second run with unchanged test files, conftests, and pytest config spawns no collect-only subprocess (assert via the fake `test_command`); touching one test file or one conftest invalidates it; a changed installed-distribution list reported by the run pass invalidates it and re-executes served hits.
- `--failed`: after a run with one failure, `run --failed` selects exactly that test; `--failed` with `--all` exits 2; `--failed` with no previous run exits 2.
- Case canonicalisation: on a case-insensitive filesystem, invoking with `Tests/test_x.py` produces the same `test_id` as `tests/test_x.py` (the committed case); the test is skipped on case-sensitive filesystems with a reason.
- Cache key stability (same inputs → same hex) and sensitivity (any input file byte change → different hex), including a byte change in `tests/conftest.py`, in the pytest configuration file, in a data file the test file references, and in the installed-distribution list; `failed` and `error` results are never written to the cache; a run with `fallback_full_run` reads nothing from the cache.
- Flake algorithm: no flag after two opposite results (`min_runs = 3`); `suspected` after three mixed results with one flip; `confirmed` after enough flips; `resolved` after a stable streak on a new hash; `ignored` stays ignored; cache replays do not insert outcomes.
- Canonicalization golden vector and a round-trip sign/verify. A mutated payload must fail verify (exit code 4). A record verifying against a different `--pubkey` must fail. With `[record].trusted_pubkeys` set, a record from an unlisted key fails and a listed key reports `signer: "known"`; with it empty, verify succeeds with `signer: "unknown"` and `--require-known-key` fails. `commit.tree` equals `git rev-parse HEAD^{tree}` on a clean tree and changes when an untracked file is added; `payload.selection` and `summary.executed` / `summary.replayed` are present and consistent with `results`; `record sign` refuses a zero-test run; there is no code path that signs an arbitrary input file.
- Redaction catches the patterns in Section 5.15.
- Discovery extracts a `pytest` command from sample GitHub and GitLab YAML fixtures and ignores a `deploy` job.
- Savings: uncalibrated runs report `local_compute` confidence `low` and do not invent skipped-test durations; after calibration, deselected tests contribute their stored `duration_ms`; `local_compute.cost_micro_usd` is 0 unless `price_local_minutes` is true; `utf8_bytes_div_4` is stable; `tokens.raw_bytes` equals the captured pytest stdout length and `tokens_delta` is negative on a two-test green run; known-flake failures increment `wrong_turns`; the first run with a given non-flaky failing set sets `ci_runs_avoided.count` to 1 and the second run with the same `dedupe_key` sets it to 0 with `deduplicated: true`; `wait.baseline_kind = full_suite_local` never exceeds `medium`; `defaults_used` lists absent keys only (a key set to the default value is not listed) and `prices_configured` flips the money segment of the headline; `calibration.payoff` is `overhead_dominates` for a suite shorter than three times the measured fixed overhead; mutating `payload.savings` after signing fails verify; dollar conversion uses integer micro-USD (no floats in the signed object).
- Import-lint: a core module that imports `github` or `anthropic` (injected in the test) fails the contract; adapter modules may import those names.

**Integration tests**

Run against `examples/demo-project` via the CLI (subprocess or Typer’s runner):

- `proof-ci init` is idempotent without `--force` (second call exits 2) and succeeds with `--force`.
- `proof-ci run --all` on the demo project reports the deliberate failure and exits 1.
- Changing one source file and running `proof-ci run` selects the related tests, not the whole suite.
- `proof-ci run --json` stdout parses as a `RunSummary`.
- `proof-ci record sign` then `proof-ci record verify` on the demo project succeeds; `proof-ci run --sign` on a run that selected zero tests exits 2 and writes no record.
- pytest is launched as a subprocess (assert via a fake `test_command` script that records its arguments and environment) and the runner exits 3 when `PROOF_CI_RESULTS_FILE` is not produced.
- A flaky demo test that is executed enough times under a frozen `code_hash` becomes `suspected` / `confirmed`, and `proof-ci flakes list --json` shows it.
- `proof-ci mcp` exposes exactly the six tool names in Section 5.14 (a lightweight SDK-level test; a full stdio handshake test is desirable if cheap).
- `proof-ci run --json` includes `savings_headline` and a `Savings` object; `proof-ci savings --json` returns a `SavingsReport`; `proof-ci savings calibrate` writes `calibration_meta`.
- A generic-client conformance test (`tests/test_conformance_generic_client.py`) drives the CLI with `--json` and the MCP server over stdio without any Claude-specific headers, tools, or prompts, and asserts the documented JSON shapes.


**Example project (`examples/demo-project`)**

A tiny installable package `demo` with:

- `src/demo/mathops.py` — functions used by tests.
- `src/demo/textops.py` — unrelated functions, so selection can ignore them.
- `tests/test_math.py` — passing tests and one **deliberately failing** test (`test_div_wrong_expectation` or similar) whose failure is stable.
- `tests/test_text.py` — passing tests that do not import `mathops`.
- `tests/test_flaky.py` — a test that fails when the environment variable `PROOF_CI_DEMO_FLAKE=1` is set and passes otherwise, so tests can force flips without sleeping or using wall-clock randomness.
- `tests/test_data.py` plus `tests/data/sample.json` — the test reads that JSON file by path, to exercise non-Python selection.
- Its own `proof-ci.toml` and `pyproject.toml`.

The proof-ci project’s own test suite should not be broken by the demo project’s deliberate failure: only integration tests that intentionally invoke `proof-ci run` against the demo should see that failure.

### 5.17 The tool’s own CI

Ship both files, kept equivalent in what they run:

**`.gitlab-ci.yml`** — at least two jobs, or one job with a matrix if simpler:

- `lint`: `ruff check src tests` and `ruff format --check src tests`
- `typecheck`: `mypy`
- `importlint`: `lint-imports` (or `pytest tests/test_import_lint.py`)
- `test`: `pytest` on Python 3.11, 3.12, and 3.13 (3.13 is required: Fedora and Arch ship it as the default interpreter)

**`.github/workflows/ci.yml`** — the same four concerns, triggered on `push` and `pull_request`, with the `test` job in a matrix of `ubuntu-latest`, `macos-latest`, and `windows-latest` on Python 3.11, 3.12, and 3.13. The Windows leg is required, not allowed-to-fail: it is the project's evidence for Section 5.20. An optional `ubuntu-24.04-arm` leg (Python 3.12 only) is included when the hosting plan provides it and marked `continue-on-error: true` until it has been green for a release; it is the evidence for the aarch64 "expected" claim. Lint, typecheck, and import-lint run on `ubuntu-latest` only.

The GitLab pipeline runs on Linux; Windows and macOS coverage comes from the GitHub matrix. Use whichever runners the hosting group provides for this project (do not assume shared runners are available).

Neither pipeline should require secrets. Neither should try to publish to PyPI in Phase 1.

### 5.18 Documentation

**`README.md`** must include: one-paragraph what and why, leading with what the user gets in the order of Section 1.2 (dependents the agent did not know to run, a flake-aware verdict, one structured failure, a signed record of what ran, fewer wasted turns) and never with CI minutes or token dollars; the "when it is worth installing" paragraph from Section 1.2 including the `pytest --lf` / testmon / picked / xdist comparison and the `savings calibrate` payoff verdict; the layer-not-replacement framing; vendor neutrality (any MCP or CLI client); install into the project's own environment (`pip install -e .` during development; why a tool-only install does not work, Section 5.10); the support matrix of Section 5.20 (Linux distros, macOS, Windows 10/11, WSL2, containers with `--user`, what is expected-but-untested and what is unsupported) and the Windows MCP configuration example; "Where to keep the signing key" (Section 4.7, including the mode-less filesystem warning); what a signed record does and does not prove (Section 4.7 wording); quickstart (`init`, `run`, `run --json`, `flakes`, `savings`, `record sign` / `verify`, `mcp`); a generic MCP stdio snippet plus notes that the same snippet works in Claude Code, Cursor, Codex, Windsurf, Cline, and VS Code Copilot agent mode; a short description of affected-test selection, conservative fallbacks, and savings headlines (including that figures are labeled estimated vs measured); an honest statement that Phase 1 is not a security sandbox and not a CI replacement; pointer to this build spec if it lives in-tree (it does not have to — this spec may live outside the code repo).

**`AGENTS.md`** is the generic agent-facing guide. **`CLAUDE.md`** MUST contain identical guidance (byte-for-byte equality is the simplest way to guarantee this; a test should compare them or compare a shared included body). Written *to* an agent, without naming a required product:

- Prefer `run_affected_tests` or `proof-ci run --json` over invoking pytest directly.
- Read the `RunSummary` first, including `savings_headline`. Only fetch `explain_failure`, `get_failure_details`, or `get_savings_report` for specifics. Do not paste raw pytest logs into context.
- Treat savings figures as estimates, not invoices; a headline without a dollar sign means prices are not configured. Do not publish aggregate savings on behalf of the human. Use `run --failed` to rerun the last failures instead of calling pytest directly.
- If `is_flaky` is true and status is `confirmed`, do not rewrite unrelated production code to satisfy that test.
- Do not change `proof-ci` data contracts without asking the human.
- After a green run that matters (pre-push), run with `--sign` (or `sign: true`). A signed record is a tamper-evident statement of what proof-ci reported for a tree hash and a selection; do not describe it as proof that the tests ran.
- Never request or print the signing private key.
- Identify yourself with the generic environment variables (`PROOF_CI_AGENT_NAME`, and optionally version, model, session), not vendor-specific ones.

**`docs/CROSS_AGENT_CHECKLIST.md`** is a short manual checklist: install `proof-ci`, configure MCP stdio (or CLI `--json`), run the demo project, inspect a `RunSummary` and a signed record, and call `get_savings_report`, once in Claude Code and once in at least one other agent (Cursor or Codex). It is a release activity for Chris, not an automated test.

### 5.19 Phase 1 acceptance criteria (definition of done)

Phase 1 is done when all of the following are true:

1. The package installs from a clean checkout with `pip install -e ".[dev]"` on Python 3.11+.
2. `proof-ci --help` lists `init`, `affected`, `run`, `flakes`, `record`, `savings`, and `mcp`.
3. Every command in Section 5.7 exists with the flags specified, the JSON shapes specified, and the exit codes specified. Tests cover the exit codes for `run` (0, 1), `record verify` (0, 4), and a usage error (2).
4. Affected selection matches the algorithm in Section 5.8, demonstrated on the demo project and by unit tests for each fallback, including the incremental fallback rule and the collection cache. `proof-ci run --failed` reruns the previous run's failures.
5. Cache hits skip execution and set `cache_hit: true`; changing one imported byte, one byte of a `conftest.py` on the test's path, or one byte of a referenced data file forces a miss. `failed` and `error` results are never served from the cache.
6. Failures emitted by the pytest plugin validate against `test_result.json` and include `repro_command`. When the traceback intersects the diff, `likely_cause` is present and correct on the demo project’s deliberate failure after a matching source edit. pytest runs only as a subprocess through `[project].test_command`, and a missing results file is exit 3.
7. Flake detection marks the demo flaky test after forced flips, without marking stable tests.
8. `record sign` / `record verify` implement Section 4.7, including refusal to verify a tampered payload, `signer: known | unknown` against `[record].trusted_pubkeys`, a required `commit.tree` that matches the tested tree, `payload.selection`, `summary.executed` / `summary.replayed`, refusal to sign a zero-test run, and no path that signs an arbitrary file. Mutating `payload.savings` after signing also fails verification.
9. `proof-ci mcp` advertises exactly the six tools in Section 5.14 (including `get_savings_report`). Additional tools are not allowed in Phase 1 without asking Chris. Tool descriptions do not name a specific agent. Each tool returns its JSON object as a single text content block.
10. Redaction unit tests pass. The private key file is created owner-only: mode `0600` on POSIX, owner-only ACL on Windows, and the readable-key check refuses a key that other users can read on either platform (Section 4.7).
11. `.gitlab-ci.yml` and `.github/workflows/ci.yml` exist and run lint, typecheck, import-lint, and tests; the GitHub `test` matrix includes `windows-latest` and `macos-latest` and is green.
12. `README.md`, `AGENTS.md`, and `CLAUDE.md` exist. `AGENTS.md` and `CLAUDE.md` contain identical guidance. The README’s MCP snippet is the generic stdio invocation.
13. LICENSE is MIT.
14. There is no account, no network call on the default path, and no telemetry.
15. The project’s own test suite is green in CI.
16. Every run writes a `Savings` object and a ledger row. Uncalibrated runs label local compute and wait as low-confidence (or unavailable) and do not invent baselines. Price keys absent from the TOML appear in `defaults_used`; no dollar figure appears in any headline until `prices_configured` is true; local test minutes are never priced at the CI rate; `ci_runs_avoided` is deduplicated; `tokens` is measured from pytest's own stdout and may be negative; `savings calibrate` prints a payoff verdict. `proof-ci savings` supports `--since`, `--by`, `--weekly`, and `--json`.
17. Agent identity on signed records uses the generic fields `id`, `agent_name`, `agent_version`, and `model` — not vendor-specific keys. Core modules import no vendor SDKs (import-lint is green).
18. `tests/test_conformance_generic_client.py` passes. `docs/CROSS_AGENT_CHECKLIST.md` (or an equivalent section of the README) exists and lists manual steps for Claude Code and at least one other agent (Cursor or Codex). Completing the manual checklist is a release activity for Chris; shipping the checklist is part of Phase 1 done.
19. The full test suite passes on native Windows 11 (locally on Chris's machine and on `windows-latest` in CI), and the quickstart in the README has been executed once from a Windows shell (PowerShell) with an MCP client configured as in Section 5.14. Section 5.20 rules are covered by tests where a test is possible (encoding, path normalisation, key protection, `repro_command` quoting).
20. `[affected].base` defaults to `auto`; a committed change on a clean tree selects its tests; a zero-test run is never signed.
21. `test_command` is accepted in array and string form and a Windows path in either form reaches the subprocess intact; `git --version` preflight exits 3 below 2.0; the tree hash works in a linked worktree; `init --no-key` and an unwritable config dir do not fail `init`; repo paths are canonicalised to git's committed case.

### 5.20 Platform rules (Linux, macOS, Windows)

Phase 1 supports the platforms in the matrix below. The rules that follow are normative and apply to every module. Each is small; together they are the difference between a tool that runs on the user's machine and one that does not.

**Support matrix (normative wording for the README).**

| Platform | Status | Notes |
| --- | --- | --- |
| Linux x86-64: Ubuntu 22.04+/Debian 12+, Fedora 40+/RHEL 9+ (and 8 with AppStream Python), Arch | **Supported, tested in CI** (`ubuntu-latest`) | Needs Python >= 3.11: Ubuntu 22.04 ships 3.10 and RHEL 8/9 default to 3.6/3.9, so use the distro's `python3.11`/`python3.12` package, deadsnakes, or `uv python install`. git >= 2.0 is present on every listed release. Python 3.13 (Fedora, Arch default) is a required CI leg. |
| Linux aarch64 (Graviton, Raspberry Pi OS 64-bit, Apple-silicon VMs) | **Expected to work; CI leg optional** | All pinned dependencies publish manylinux aarch64 wheels. Reported, not promised, until the `ubuntu-24.04-arm` leg has been green for a release. |
| Alpine / musl | **Expected to work, untested** | `cryptography` and `pydantic-core` publish musllinux wheels; the rest is pure Python. |
| WSL2 (Ubuntu or other distro on Windows) | **Supported as Linux** | Keep the repository and the signing key inside the WSL filesystem. See the WSL2 paragraph below. |
| Containers (Docker/Podman, CI images) | **Supported as Linux** | See the container rules below (`--user`, `init --no-key`, ephemeral `HOME`, SQLite on bind mounts). |
| macOS 13+ on Apple silicon and Intel | **Supported, tested in CI** (`macos-latest`, Apple silicon) | Apple git (>= 2.39) qualifies. Default APFS is case-insensitive: rule 1 applies. |
| Windows 10 (any build supported by the CPython release) and Windows 11, x64 | **Supported, tested in CI** (`windows-latest`) | Native, not WSL-only. Rules 2, 4, 6, 7, 13, 14 below. |
| Windows on ARM | **Expected to work, untested** | Depends on `cryptography` and `pydantic-core` `win_arm64` wheels for the chosen Python; PyYAML falls back to pure Python. |
| FreeBSD, OpenBSD, other BSDs | **Unsupported** | The POSIX code paths may run, but `cryptography` has no BSD wheels and nothing is tested. Say so rather than letting "POSIX" imply support. |

1. **Paths.** Use `pathlib` for all filesystem work. Every path that enters a data contract, a cache key, a flake row, a ledger row, or a `repro_command` is repo-relative with forward slashes: `PurePath(p).relative_to(repo_root).as_posix()`. Compare paths after normalisation, never as raw strings. Git already prints forward slashes on Windows; pytest's `report.location[0]` and `item.location[0]` do not (they are OS-native and rootdir-relative), so normalise them in the plugin (Section 5.10). `location[1]` from pytest is 0-based; contracts are 1-based. **Case canonicalisation:** on case-insensitive filesystems (default APFS, NTFS, WSL `/mnt/c`) the same file can be named `Tests/test_x.py` and `tests/test_x.py`; git stores one spelling. Before any repo-relative path (including the path part of a pytest node id) enters a contract or store, map it to git's committed spelling: build a case-folded index from `git ls-files` once per run and replace the path with the indexed spelling when a case-insensitive match exists (untracked files keep their on-disk spelling). This keeps `test_id`, cache keys, flake rows, and calibration rows identical across Linux, macOS, and Windows checkouts of the same repository.
2. **Text I/O.** Every `open()` for text passes `encoding="utf-8"` (and `newline="\n"` when writing); every `subprocess.run` that captures text passes `encoding="utf-8", errors="replace"`; JSON is written with `ensure_ascii=False`. At CLI startup call `sys.stdout.reconfigure(encoding="utf-8")` and the same for `stderr` so JSON output is not mangled by a cp1252 console. SQLite text is UTF-8 by default; do not pass `str` through any platform codec.
3. **Line endings.** Write `\n`. Read with universal newlines. Do not let `git config core.autocrlf` change what gets hashed: hash file bytes exactly as they are on disk (the cache key and `code_hash` are per-machine anyway).
4. **Signing key protection.** POSIX: mode `0600` on creation, refuse keys with group/other bits. Windows: owner-only ACL via `icacls` on creation; the readable-key check inspects the ACL, not the mode bits, and refuses keys readable by `Everyone`, `Users`, `Authenticated Users`, or `BUILTIN\Users`. Full rules in Section 4.7. Tests assert the POSIX mode on POSIX and the ACL on Windows; neither test is skipped on its own platform.
5. **Config directory.** `platformdirs.user_config_dir("proof-ci", appauthor=False)` (on Windows this is `%LOCALAPPDATA%\proof-ci`, not `...\proof-ci\proof-ci`).
6. **`repro_command` quoting.** Wrap the node id in double quotes whenever it contains any of `[`, `]`, space, `"`, `'`, `$`, or backtick: `pytest -q "tests/test_x.py::test_y[a-b]"`. Double quotes are honoured by bash, zsh, PowerShell, and cmd.exe; single quotes are not honoured by cmd.exe. Escape an embedded `"` as `\"`. Never emit a `repro_command` that only works in one shell.
7. **Subprocesses.** Launch pytest and git with argument lists, never `shell=True`. `[project].test_command` is canonically a TOML array and is used as-is; `init` and `init --discover` write the array form. A string value is accepted and split with `shlex.split(cmd, posix=(os.name != "nt"))` — `posix=True` would strip the backslashes out of a Windows path (`C:\venv\Scripts\python.exe` becomes `C:venvScriptspython.exe`), so it must not be used there; a string containing a backslash on Windows may therefore not use POSIX-style escapes. Then let `subprocess` locate the executable: on Windows `CreateProcess` resolves a bare `pytest` to `pytest.exe` (which is what a venv's `Scripts\` directory provides) but **not** to `.cmd`/`.bat` shims, so a `test_command` that resolves to a shim must be written as `["python", "-m", "pytest"]`. Pass `cwd=repo_root` and an environment that inherits the parent's plus the `PROOF_CI_*` activation variable.
8. **SQLite everywhere.** Open connections per operation (not one per process lifetime), set `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`, and close them before returning, so the MCP server and a concurrent CLI do not hold the file open across each other's writes. If `PRAGMA journal_mode=WAL` does not return `wal` (some bind mounts and network filesystems lack the shared memory WAL needs, notably Docker Desktop bind mounts and NFS), fall back to `journal_mode=DELETE` for that database, log it once under `PROOF_CI_DEBUG=1`, and continue.
9. **Platform fingerprint.** `platform.system()` yields `Windows`; it is part of the cache key as on other platforms. Do not special-case it anywhere else.
10. **Symlinks and junctions.** Resolve the repository root with `Path.resolve()` once and compare resolved paths; do not assume symlinks exist or do not exist.
11. **CI.** `windows-latest` and `macos-latest` are required legs of the GitHub `test` matrix, Python 3.13 is a required version, and `ubuntu-24.04-arm` is an optional leg (Section 5.17).
12. **MCP configuration.** Document the Windows command form from Section 5.14 (venv `Scripts\proof-ci.exe` or `python.exe -m proof_ci mcp`) and the WSL form below.
13. **Windows long paths.** `MAX_PATH` is 260 characters. `python.exe` opts into long paths through its manifest, but git does not: document `git config core.longpaths true` for deep repositories, keep `.proof-ci/` file names short (the run directory is a UUID; cache files are `<2 hex>/<64 hex>.json`), and have `init` warn when `len(str(repo_root)) > 150`.
14. **Windows identity.** Read the current user from `%USERNAME%` first and fall back to `os.getlogin()` (which raises under some services and terminals); use it only for the `icacls` grant.
15. **Containers.** `init --no-key` skips key generation; `init` tolerates an unwritable or ephemeral config directory (Section 5.7.1) and warns when running as root against a repository owned by another uid. The README's container section says: run with `--user $(id -u):$(id -g)` so `.proof-ci/` is not root-owned on the host; mount a persistent key and point `PROOF_CI_SIGNING_KEY` at it if records must be signed by a stable key (a fresh key in an ephemeral `HOME` never matches `trusted_pubkeys`); SQLite falls back from WAL on bind mounts (rule 8).
16. **git version.** git >= 2.0, preflighted once per invocation (Section 4.7).

**WSL2.** WSL2 is a supported Linux environment, with the Linux build of `proof-ci` installed inside the distribution. Keep the repository on the WSL filesystem (`~/...`), not under `/mnt/c`: DrvFs is several times slower for git and pytest, is case-insensitive (rule 1 applies), shows every file as mode `0777` (the signing key must live on the WSL side, see the mode-less filesystem exception in Section 4.7), and marks every file executable. A Windows-side MCP client can drive the WSL install with `"command": "wsl.exe", "args": ["-d", "Ubuntu", "--cd", "/home/<user>/project", "-e", "/home/<user>/project/.venv/bin/proof-ci", "mcp"]`; document this next to the native Windows example. A native Windows `proof-ci` and a WSL `proof-ci` on the same checkout keep separate `.proof-ci/` state only if the repository is not shared across the boundary; sharing one checkout between the two is not supported (different `platform`, different modes, different line-ending settings).

Anything platform-specific that is not covered here (for example a future `fcntl` lock) MUST have a Windows equivalent or a documented no-op, decided when it is introduced, not deferred.

---

## 6. Phase 2 — hosted service

Phase 2 is a hosted companion behind the **same** MCP server and CLI. Local mode remains the default. Nothing in Phase 2 may break Phase 1’s offline behavior.

Do not implement Phase 2 until Phase 1 meets Section 5.19. This section is written so a later series of Claude Code sessions can build it without reinventing product decisions.

### 6.1 Goals

- Share a remote result cache across a team so one agent’s pass is another agent’s cache hit.
- Share flake history so a flake discovered on one machine is visible to others.
- Store signed run records and expose a public verification URL.
- Offer warm sandboxes that match a declared environment, to cut provisioning time.
- Report a status check back to GitHub and GitLab so humans see proof-ci results next to classical CI. Classical CI still runs. Post to both forges through `ForgeAdapter.post_status` with the same fields; include the savings headline in the summary line.
- Accept signed records from any agent (`payload.agent.agent_name`) and any supported forge. Do not require a Claude-specific or GitHub-specific client.
- Offer a team savings dashboard (wrong turns, CI runs avoided, local test time, trends by repo/agent/team), computed only from verified records. Pricing is flat per committer (Section 6.12); the dashboard informs, it does not invoice.

### 6.2 Architecture

```mermaid
flowchart LR
  CLI[proof-ci CLI / MCP]
  API[HTTPS API]
  AUTH[AuthN / AuthZ]
  CACHE[Content-addressed cache store]
  FLAKE[Per-workspace flake store]
  REC[Record store]
  SB[Warm sandbox pool]
  GH[GitHub Checks API]
  GL[GitLab commit statuses]
  VERIFY[Public verify endpoint]

  CLI -->|Bearer token or device flow| API
  API --> AUTH
  API --> CACHE
  API --> FLAKE
  API --> REC
  API --> SB
  API --> GH
  API --> GL
  REC --> VERIFY
```

Suggested deployment shape (not an implementation mandate): a single HTTPS service (Python, FastAPI or equivalent) with Postgres for metadata, an object store (S3-compatible) for cache blobs and records, and a worker pool for sandboxes. Substitute equivalent components if there is a good reason, but keep the external API stable.

A **workspace** is the tenancy unit. It typically maps to a GitHub organization / GitLab group plus an optional list of repositories. Users belong to workspaces. Tokens are scoped to one workspace.

### 6.3 Data model (logical)

| Entity | Key fields | Notes |
| --- | --- | --- |
| User | id, email, name, created_at | Authenticated via GitHub/GitLab OAuth in the obvious first implementation, or email magic-link. |
| Workspace | id, slug, name, plan | `plan` is a string hook: `free`, `team`, `enterprise`. |
| WorkspaceMember | workspace_id, user_id, role | Roles: `owner`, `member`, `read`. |
| Repository | id, workspace_id, host (`github`\|`gitlab`), external_id, full_name | |
| CacheEntry | workspace_id, key_hex, blob_locator, size, created_at, last_access | Content-addressed; never store secrets. |
| FlakeRecordRow | workspace_id, repo_id, test_id, fields from Section 4.4 | Team-wide, keyed by repo. |
| RunRecordRow | id, workspace_id, repo_id, run_id, commit_sha, payload, signature, created_at | Unique `(workspace_id, run_id)`. |
| AgentToken | id, workspace_id, prefix, hash, scopes, expires_at | The CLI stores the raw token locally; the server stores only a hash. |
| SandboxImage | workspace_id, name, digest, declared env | |
| StatusDelivery | id, record_id, host, external_ref, state, last_error | Posted through `ForgeAdapter`; GitHub and GitLab are peers. |
| SavingsLedgerRow | workspace_id, repo_id, run_id, agent_name, branch, totals, confidence, defaults_used, recorded_at | Copied from verified uploaded records only. |

Retention: cache entries LRU-evicted per workspace quota; records retained according to plan (see pricing hooks). Phase 2 should not invent specific dollar prices in code; it should read limits from configuration.

### 6.4 Authentication and tenancy

- Human signup: OAuth with GitHub and GitLab.
- Agent/CLI auth: a workspace-scoped personal access token (or device-flow token) sent as `Authorization: Bearer <token>`.
- Every query that reads or writes cache, flakes, records, sandboxes, or savings is filtered by `workspace_id` derived from the token. There is no global admin query that mixes workspaces in application code paths.
- Authorization checks belong in one place (a dependency / middleware), not ad hoc in each handler.
- Tokens are hashed at rest (SHA-256 is acceptable for high-entropy generated tokens; do not store reversible tokens).
- Cross-tenant tests are mandatory: a token from workspace A must receive 404 (not 403) when addressing workspace B’s objects by id, to avoid existence leaks where it is cheap to do so. For actions that need to say “you do not have access to this workspace,” 403 is fine when the workspace id is already known to the caller.

### 6.5 Remote cache protocol

Modeled on the “free local, paid remote cache” idea used by Turborepo and Nx, adapted to test results.

CLI configuration additions (Phase 2):

```toml
[remote]
url = "https://api.example.com"   # example; real URL TBD
workspace = "acme"
# token comes from env PROOF_CI_TOKEN or `proof-ci login`
```

Protocol (HTTPS, JSON + raw bytes):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/cache/{key_hex}` | Returns 200 with the cached `TestResult` JSON, or 404. |
| PUT | `/v1/cache/{key_hex}` | Body is the `TestResult` JSON. 409 if the key exists with different bytes (treat as success if bytes match). |
| HEAD | `/v1/cache/{key_hex}` | Existence check. |

Rules:

- The server MUST validate the body against the `TestResult` schema before storing.
- The server MUST reject bodies whose captured output appears to contain private keys (`-----BEGIN PRIVATE KEY-----`, `-----BEGIN OPENSSH PRIVATE KEY-----`) or that exceed 256 KiB.
- Cache writes from one repo should include an `X-Proof-CI-Repo: owner/name` header so the server can namespace keys as `sha256(workspace_id + repo + key_hex)` internally. The CLI still computes `key_hex` as in Phase 1; the server adds tenancy.
- On cache read, the CLI validates the JSON against the schema before replaying it. Never execute anything from a cache body — a cache body is data, not a script.
- If the remote is configured but unreachable, the CLI logs a warning on stderr and falls back to local cache plus local execution. It does not fail the run because the cache is down (the run itself still has to succeed or fail on real tests).

Lookup order: local cache, then remote cache, then execute. A remote hit is written through to the local cache. A local miss that executes successfully is written to both.

### 6.6 Team flake history

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/repos/{repo_full_name}/flakes` | List, filterable by `status`. |
| POST | `/v1/repos/{repo_full_name}/flakes/outcomes` | Batch-upload new `test_outcomes` rows from a local run (no cache replays). |
| POST | `/v1/repos/{repo_full_name}/flakes/{test_id}/mark` | Manual mark / unmark (member role or above). |

After each `proof-ci run`, if remote is configured, the CLI uploads freshly executed outcomes and pulls updated `FlakeRecord`s. Remote status wins when `updated_at` is newer; the local SQLite mirror is a cache of the team view plus any outcomes not yet uploaded.

Detection algorithm stays exactly Section 5.11, executed on the server against the workspace+repo history so everyone shares the same classification.

### 6.7 Record storage and public verification

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/repos/{repo_full_name}/records` | Upload a signed `RunRecord`. Server verifies the signature before storing. Reject unsigned records. |
| GET | `/v1/repos/{repo_full_name}/records/{run_id}` | Fetch a stored record (auth required). |
| GET | `/v1/verify/{record_ref}` | **Public**, unauthenticated. `{record_ref}` is an opaque, unguessable id (at least 128 bits of entropy) issued at upload time — not the raw `run_id` alone. |
| GET | `/v1/repos/{repo_full_name}/savings` | Aggregated `SavingsReport` for one repo (`since`, `until`, `by`, `weekly`). Auth required. |
| GET | `/v1/workspaces/{slug}/savings` | Team-wide aggregate across repositories. Auth required. |
| GET | `/v1/workspaces/{slug}/savings/dashboard` | Dashboard payload: cost per merged change, trends by repo, agent (`agent_name`), and team. Auth required. |

The public verify endpoint returns:

```json
{
  "valid": true,
  "key_id": "...",
  "run_id": "...",
  "commit_sha": "...",
  "outcome": "passed",
  "workspace": "acme",
  "repo": "acme/demo"
}
```

It does **not** return full stdout/stderr from test results by default (those may leak sensitive project data). A `?include=results` option is auth-gated.

The CLI prints the public verify URL after a successful signed upload so an agent can cite it.

The server MUST accept any well-formed, signature-valid `RunRecord` regardless of `payload.agent.agent_name` and regardless of which forge hosted the commit. It MUST NOT reject a record because the agent is not Claude Code or the forge is not GitHub.

Signer identity (added in 1.1.0, same rule as Section 4.7): a workspace registers the public keys it trusts (the same PEM files a project lists in `[record].trusted_pubkeys`). Upload and the public verify endpoint report `signer: "known" | "unknown"` against that registry; status checks and the dashboard count only `known` records toward anything a merge rule or an invoice relies on. The endpoint's `valid: true` continues to mean integrity only.

### 6.8 Warm sandbox pool

Phase 2 is where “same environment everywhere” becomes literal.

Declare an environment in `proof-ci.toml`:

```toml
[environment]
image = "ghcr.io/example/proof-ci-python:3.11-pytest"
python = "3.11"
services = []          # reserved: names of snapshot-backed services
```

The control plane keeps a small pool of already-started sandboxes for each `{workspace, image digest}`. An incoming `run` with `remote_execute: true` (CLI flag `--remote-execute`, MCP argument `remote_execute`) is scheduled onto a warm sandbox:

1. Sync the git commit (and, if `dirty`, a pack of the working-tree diff — prefer asking the agent to commit; if dirty sync is supported, size-limit it).
2. Mount or restore declared service snapshots (Postgres, Redis) if configured. Phase 2 must support *at least* a snapshot-less image and document service snapshots as an incremental deliverable if they slip.
3. Run `proof-ci run` inside the sandbox with the same CLI.
4. Stream the `RunSummary` back.
5. Recycle or discard the sandbox. Never reuse a sandbox across workspaces. Reuse across sequential runs of the *same* workspace and image is allowed only after a reset to a known snapshot.

Target (design target, not a published benchmark): a warm hit should start executing tests in a few seconds rather than waiting for a cold VM image pull.

Phase 2 sandboxes **are** an isolation boundary: no workspace credentials mounted other than those needed for the check; no access to other workspaces’ files; outbound network default-deny except for what the workspace policy allows (default: the proof-ci API and the git host).

### 6.9 Status-check integrations

After a signed record is uploaded, the service posts:

- Call `ForgeAdapter.post_status` for every connected forge with the same generic fields: `context = "proof-ci"`, `state` derived from `payload.summary.outcome`, `description` formed as `{summary} · {savings_headline}` (for example `1 failed, 10 passed · Checked 14/1,200 (12 cached) in 22s; flaky flagged 1; CI runs avoided 0; local test-min not run ~18`), and `target_url` pointing at the auth-gated record page or the public verify URL.
- GitHub’s adapter maps that call onto a Check Run; GitLab’s adapter maps it onto a commit status. Neither forge gets extra information the other does not.

Installation: a GitHub App and a GitLab app/integration, stored per workspace. The CLI does not post statuses itself; the server does, so tokens never sit on the agent’s machine.

Existing CI still runs. `proof-ci` is an additional check, not a replacement. Documentation must say how to add it as a required check *in addition to* the existing pipeline, not instead of it.

### 6.10 How the CLI and MCP switch between local and remote

Priority:

1. If `[remote]` is not configured and `PROOF_CI_TOKEN` is unset, every command is local (Phase 1 behavior).
2. If remote is configured, cache, flake, and savings-ledger traffic use the remote protocol with local fallback, unless `--offline` is passed (Phase 2 CLI flag) in which case behavior is pure Phase 1.
3. Execution stays local unless `--remote-execute` is passed *and* an `[environment].image` is declared.
4. Signing stays local (the private key does not go to the server). The signed envelope may be uploaded.
5. MCP tools gain the same optional inputs: `offline`, `remote_execute`. Defaults preserve Phase 1 behavior.

`proof-ci login` (Phase 2) performs the device/OAuth flow and stores a token in `platformdirs.user_config_dir("proof-ci", appauthor=False) / "credentials.toml"` with the same owner-only protection as the signing key (Section 4.7). `proof-ci logout` deletes it.

### 6.11 Security and tenancy (Phase 2 additions)

- TLS everywhere.
- Rate-limit cache PUT/GET and verify by token and by IP.
- Validate all uploaded JSON against Section 4 schemas.
- Do not run uploaded records as code.
- Sandbox isolation as above.
- Audit at least: login, token create/revoke, record upload, flake mark, sandbox execute. (Full Phase 3 attribution comes later.)
- Do not store raw GitHub/GitLab user access tokens in logs.

### 6.12 Pricing hooks

Do not hard-code consumer prices. Implement limit checks keyed by `workspace.plan`:

- Monthly remote-cache stored bytes and GET/PUT counts.
- Monthly remote-execute minutes.
- Record retention days.
- Number of repositories.

When a limit is exceeded, return HTTP 402 or 429 with a stable error code (`cache_quota_exceeded`, `execute_quota_exceeded`) and a human message. The CLI prints that message and falls back to local mode where possible (cache and execute). Do not invent dollar figures in user-facing strings.

The monetization rationale, for implementers: the MCP server is easy to copy; what a team pays for, in order, is (1) **team flake history** (one machine's `confirmed` flake saves every other agent a wrong turn), (2) the **record store, status checks, and signer registry** (a merge rule can require a `signer: known` record), (3) warm sandboxes, and (4) the shared result cache, which is included but not the lead because its cross-machine hit rate is limited by `python`, `platform`, and installed-distribution matching.

**Flat per-committer pricing.** `workspace.plan` maps to a monthly price per *committer* (a user who authored a commit referenced by an uploaded record in the billing month), with the limits above as plan parameters. The dollar amount lives in server configuration, never in code or user-facing strings. Savings figures from records are shown on the dashboard and never used to compute an invoice: the inputs are customer-configured prices and estimates, so a savings-based charge would be both gameable and dishonest. The invoice preview lists period, plan, counted committers (with the `run_id`s that made each one count), and limits consumed.

### 6.13 Phase 2 acceptance criteria

1. A workspace can be created, a token issued, and the Phase 1 CLI authenticated with `proof-ci login`.
2. Two machines on the same workspace share cache hits for the same key.
3. A flake confirmed on machine A appears in `proof-ci flakes list` on machine B after sync.
4. A signed record uploaded to the API verifies at the public endpoint; a tampered payload is rejected at upload and at verify.
5. A GitHub Check Run and a GitLab commit status are posted for an uploaded record on a connected repository.
6. `--offline` restores pure Phase 1 behavior with no network calls.
7. Unreachable remote does not fail a local run.
8. Cross-tenant isolation tests pass.
9. Remote execute on a declared image returns a `RunSummary` equivalent to a local run of the same commit (dirty-tree support may be deferred if documented).
10. Quota hooks return the documented error codes.
11. Savings dashboard endpoints return wrong turns, CI runs avoided, local test time not executed, and trends by repo, `agent_name`, and team, computed only from verified records; dollar figures appear only when every contributing record has `prices_configured: true`. Cross-tenant isolation tests cover these endpoints.
12. GitHub Check Runs and GitLab commit statuses both include the savings headline and are posted through `ForgeAdapter` with identical generic fields.
13. The invoice preview counts committers per plan and never reads a savings figure; unsigned, tampered, or `signer: unknown` records do not make a committer count.

---

## 7. Phase 3 — enterprise

Phase 3 adds controls that a security-conscious organization will require before letting agents operate at high volume. Do not start Phase 3 before Phase 2’s acceptance criteria are met.

### 7.1 Credentials broker

Problem recap: long-lived cloud credentials in an agent-accessible environment are incompatible with prompt injection and confused-deputy risk.

**Behavior:**

- A workspace defines *integrations* (for example a cloud role, a package registry, a staging database) with a *scope document* listing allowed actions and resource patterns.
- When an agent task needs a credential, the CLI/MCP requests one: `proof-ci creds issue --integration aws-staging --task TASK_ID`.
- The broker returns a **short-lived** token (minutes, not hours) whose lifetime and permissions are the intersection of the integration’s scope, the requesting agent’s role, and the task’s declared purpose.
- Preferred issuance path is OIDC federation (the sandbox or the developer machine presents an OIDC identity; the cloud vendor mints temporary credentials). The broker should not become a long-term store of raw third-party secrets. Where a raw secret must be stored (legacy), it is wrapped at rest and never logged, and issuance still returns a derived short-lived credential if the third party supports it.
- Each issuance is written to the audit log: agent id, task id, session/prompt hash, integration, scopes, expiry.
- Default deny: an integration that is not explicitly granted to the workspace+repo+agent is not issued.

Phase 1 and Phase 2 CLIs have no `creds` command; this command is new in Phase 3.

### 7.2 Approval gates and policy language

Destructive or sensitive actions require a human approval before the broker issues credentials or before the merge queue merges.

**Actions that MUST be gateable** (and are gated by default in the `strict` preset):

- Deploying to an environment named `prod` or `production`.
- Reading or writing data sources tagged `production`.
- Editing pipeline definitions (`.github/workflows/**`, `.gitlab-ci.yml`, other CI config paths).
- Issuing credentials whose scope includes `iam:*`, `*:*`, or otherwise wildcard admin.
- Changing workspace policy itself.

**Policy file** (committed, `proof-ci/policy.toml` or a server-side document with the same schema):

```toml
version = 1
preset = "strict"   # strict | standard | off

[[allow]]
action = "cache.write"
who = "agent:*"

[[allow]]
action = "creds.issue"
integration = "aws-staging"
who = "agent:*"
require_approval = false

[[allow]]
action = "creds.issue"
integration = "aws-prod"
who = "agent:*"
require_approval = true
approvers = ["role:owner"]

[[deny]]
action = "pipeline.edit"
who = "agent:*"
```

Evaluation is first-match deny, then allow, then default deny for gated actions. The language is intentionally small. Do not invent a general-purpose policy VM in the first Phase 3 milestone.

Approvals appear as a durable request (API + optional Slack/email notification). An approver allows or rejects. Timeouts fail closed. The agent receives a structured MCP result: `{ "status": "denied"|"pending"|"approved", "reason": "..." }` — never a raw secret while `pending` or `denied`.

### 7.3 Merge queue with batching and auto-bisect

Problem recap: at agent volume, many small green branches collide, and “merge when green” retargets endlessly.

**Behavior:**

1. Enqueue merge requests / pull requests that have a valid signed `proof-ci` record *and* have passed the existing CI of record.
2. Batch the next *N* (configurable, default 5) queued changes, merge them speculatively into a batch branch, and run proof-ci affected tests for the union of their change sets, plus existing CI if policy requires it for batches.
3. If the batch is green, merge the batch (fast-forward or merge commit, per workspace setting) in queue order.
4. If the batch is red, **bisect** the batch (not the git history of one branch): test each half, recur until the culprit change is identified. Requeue the culprit at the back or mark it failed; let the rest form a new batch.
5. Report attribution: which agent’s change broke the batch, with the `RunRecord` of the failing bisect run.

Phase 3 does not replace GitHub/GitLab merge queues if the host already has one; it may drive them via API or provide this logic where the host queue cannot batch-and-bisect at this granularity. Prefer integration over a fully original queue UI.

### 7.4 Audit log

Every privileged event is an append-only row:

| Field | Meaning |
| --- | --- |
| timestamp | RFC 3339 UTC |
| workspace_id | |
| actor_type | `human` \| `agent` \| `system` |
| actor_id | user id or agent id |
| task_id | optional |
| session | optional agent session id |
| prompt_hash | SHA-256 of the originating prompt if provided by the MCP client; never store the raw prompt by default (a workspace setting may enable raw prompt retention with a retention limit) |
| action | e.g. `creds.issue`, `approval.grant`, `record.upload`, `queue.merge`, `policy.edit` |
| target | integration name, repo, record id, … |
| result | `ok` \| `denied` \| `error` |
| reason | short |

Properties: append-only at the application layer, selectable by workspace owners, exportable as JSONL. Retention is a plan hook. Agents can append (via their actions) but cannot edit or delete.

### 7.5 Phase 3 acceptance criteria

1. Short-lived credentials can be issued via OIDC for at least one concrete integration (document which), expire on schedule, and are audited.
2. A `strict` policy blocks production credential issuance and pipeline-definition edits until a human approves; the agent sees `pending` / `denied`, not a token.
3. The merge queue batches multiple ready changes, merges a green batch, and bisects a red batch down to a single culprit.
4. Audit export contains the fields above for credential issuance, approvals, and merges.
5. Disabling Phase 3 features (no policy file, no broker configured) leaves Phase 2 behavior intact.

---

## 8. Milestone plan

Each milestone is sized for a single Claude Code session. Implement them in order. Do not start milestone *N+1* until milestone *N* meets its done criteria. Do not implement Phase 2 or 3 milestones until Phase 1 is accepted.

Files listed as “touched” are the expected focus, not a prohibition on tiny supporting edits (for example `__init__.py`). Do not redesign data contracts while implementing a milestone.

### Phase 1 milestones

#### M1 — Package scaffold

- **Goal.** A installable empty CLI that reports its version and help.
- **Files.** `pyproject.toml`, `src/proof_ci/__init__.py`, `src/proof_ci/__main__.py`, `src/proof_ci/cli.py`, empty `src/proof_ci/adapters/{agent,forge,ci,runners}/` packages, `tests/test_cli_smoke.py`, `LICENSE`, `.gitignore`.
- **Tests that must pass.** `pip install -e ".[dev]"` succeeds; `proof-ci --version` prints `proof-ci 0.1.0`; `proof-ci --help` exits 0; `pytest tests/test_cli_smoke.py` passes on Linux and on native Windows (run it on Chris's machine before closing the milestone).
- **Done.** MIT LICENSE present; Hatchling src layout as in Section 5.2, including the adapter package tree; Typer `app` object exists; the shared `--json` / `--repo` / `--config` parameter helper from Section 5.7 exists; stdout/stderr reconfigured to UTF-8 at startup (Section 5.20). No `pytest11` entry point. No vendor SDK imports in core.

#### M2 — Configuration and `init`

- **Goal.** `proof-ci init` writes `proof-ci.toml`, `.proof-ci/`, and `.gitignore` entries. Config loader resolves repo root and defaults.
- **Files.** `src/proof_ci/config.py`, `src/proof_ci/cli.py`, `tests/test_config.py`.
- **Tests.** Init in a temp directory; second init without `--force` exits 2; `--force` overwrites; missing keys take defaults; repo-root discovery from a subdirectory.
- **Done.** Section 5.4 and Section 5.7.1 honored, including `[affected].base = "auto"`, `[record].trusted_pubkeys = []`, `test_command` written as an array (string form accepted and split per Section 5.20 rule 7, with a Windows-path test), the `[savings]` price keys written commented out so `defaults_used` lists them and `prices_configured` is false on a fresh install, `--no-key`, a tolerated unwritable config directory, and the root-ownership warning. All files written as UTF-8 with `\n` (Section 5.20). Key generation may wait until M8; if skipped, `init` does not yet create a signing key (document a `# TODO(M8)` and do not pretend the key exists).

#### M3 — Data contracts as Pydantic models and schema files

- **Goal.** Section 4 schemas live in `src/proof_ci/schemas/` and have matching Pydantic v2 models in `models.py`. Examples in Section 4.8 validate, including `Savings`.
- **Files.** `src/proof_ci/schemas/*.json`, `src/proof_ci/models.py`, `tests/test_models.py`.
- **Tests.** Each example object validates; omitting a required field fails; a failing `TestResult` without `failure` fails validation; a passing `TestResult` with `failure` fails validation; a `RunRecord` agent object accepts `agent_name` / `agent_version` / `model` and rejects vendor-specific extra keys.
- **Done.** `schema_version` is `"1.0.0"` everywhere. No extra fields on the models (`extra = "forbid"`). Agent identity fields are the generic ones in Section 4.6.

#### M4 — pytest plugin and `proof-ci run --all`

- **Goal.** `proof-ci run --all` executes pytest through the plugin and writes a `RunSummary` plus `results.json`. No affected selection yet.
- **Files.** `src/proof_ci/pytest_plugin.py`, `src/proof_ci/adapters/runners/pytest_runner.py`, `src/proof_ci/runner.py`, `src/proof_ci/redact.py`, `src/proof_ci/cli.py`, `tests/test_runner.py`, start `examples/demo-project/`.
- **Tests.** Demo project `--all` exits 1 because of the deliberate failure; `--json` stdout is a valid `RunSummary`; `failures` contains the deliberate test id and a `repro_command` (double-quoted when the node id needs it); stdout/stderr redaction unit tests; pytest is launched as a subprocess with `-p proof_ci.pytest_plugin` and `PROOF_CI_RESULTS_FILE` / `PROOF_CI_COLLECT_FILE` (assert with a fake `test_command`); `report.location` is normalised to forward slashes and 1-based lines (assert on Windows and POSIX); the plugin module imports only stdlib and pytest.
- **Done.** Exit codes 0 (on a path with only passing tests, which you can exercise with an explicit passing node id) and 1 work. A missing results file is exit 3 (test by pointing `test_command` at an interpreter that cannot import the plugin). Pytest is invoked through `TestRunnerAdapter`, never in-process; `RunSummary.savings` may still be omitted until M9. `run --failed` reruns the previous run's failed/error tests (mode `failed`; exit 2 without a previous run or combined with `--all`/`TESTS`). The pytest subprocess stdout is captured and stored for M9's token metric. **xdist to-verify:** a test runs the demo project with `pytest_args = ["-n", "2"]` and asserts every `TestResult` is present with a `Failure` for the deliberate failure; if controller-side `logreport` does not carry what the plugin needs, implement the `user_properties` transport described in Section 5.10 before closing M4 and record the finding in the milestone report.

#### M5 — Affected-test selection

- **Goal.** Implement Section 5.8 end-to-end. `proof-ci affected` and `proof-ci run` (default mode) use it.
- **Files.** `src/proof_ci/affected.py`, `src/proof_ci/cli.py`, `src/proof_ci/runner.py`, `tests/test_affected.py`.
- **Tests.** Every case listed under “Unit tests” / affected selection in Section 5.16, plus a demo-project integration test that a `mathops.py` edit selects math tests and not text tests.
- **Done.** `--base`, `auto` (default; merge-base, `HEAD~1` on a clean default branch, `HEAD` on a dirty tree), `HEAD`, fallback globs with the incremental rule of Step 3 (reads the last ledger row's `commit_tree` for the branch; until M9 exists, read a `.proof-ci/last-tree-<branch-hash>.txt` stub written by `run` and migrate to the ledger in M9), `fallback_suppressed_by_tree` recorded, the collection cache of Step 5 with its environment-consistency rule, repo-path case canonicalisation (Section 5.20 rule 1), nested `conftest.py` special case, `base_setting` / `base_ref` recorded, empty selection exits 0 without executing tests and explains why on stderr; `--sign` on an empty selection exits 2 with no record (the signing itself arrives in M8; the refusal check can be stubbed now and must stay).

#### M6 — Result cache

- **Goal.** Section 5.9.
- **Files.** `src/proof_ci/cache.py`, runner integration, `tests/test_cache.py`.
- **Tests.** Key stability and sensitivity, including sensitivity to `tests/conftest.py`, the pytest configuration file, a referenced data file, and the installed-distribution list; second run of an unchanged passing test is a cache hit (`cache_hit: true`, `cache.hits >= 1`); `--no-cache` executes again; a `fallback_full_run` run reads nothing from the cache; `failed` and `error` are never written; flaky suspected/confirmed tests are not read from cache (can be tested by inserting a flake row).
- **Done.** LRU trim exists for both the result cache and the collection cache (may be tested with a monkeypatched size threshold rather than writing 500 MB). `python`, `platform`, and the installed-distribution part of `deps_hash` come from the collect-mode plugin output (or the collection-cache entry, with the consistency rule of Section 5.8 Step 5), not from the `proof-ci` process. Cache reads are bypassed only when the fallback actually fired, not when it was suppressed by the incremental rule.

#### M7 — Flake store and `proof-ci flakes`

- **Goal.** Section 5.11 and Section 5.7.4.
- **Files.** `src/proof_ci/flakes.py`, CLI subcommands, `tests/test_flakes.py`.
- **Tests.** Algorithm table in Section 5.11; cache replays do not insert outcomes; `mark` / `unmark`; demo flaky test with `PROOF_CI_DEMO_FLAKE` flipping becomes `suspected` then `confirmed`.
- **Done.** `proof-ci run` attaches `flaky` on each `TestResult`. `--allow-known-flakes` allows `confirmed` and `ignored` only.

#### M8 — Signed records

- **Goal.** Section 4.7 and `record sign` / `verify` / `keygen`. `init` now creates a key if missing. `run --sign` writes a signed record whose payload includes `commit.tree`, `selection`, and `summary.executed` / `summary.replayed`. `record sign` signs only a run `proof-ci run` produced (no arbitrary input). `verify` honours `--pubkey`, `[record].trusted_pubkeys`, `--require-known-key`, and reports `signer`. `payload.agent` uses generic identity fields. `payload.savings` MAY be omitted until M9; if present it is signed with the rest of the payload.
- **Files.** `src/proof_ci/record.py`, CLI, `tests/test_record.py`, golden fixtures.
- **Tests.** Golden canonicalization; sign then verify exits 0 with `signer: "unknown"` when no trusted key is configured and `"known"` when the `.pub` is listed; a record from an unlisted key fails when `trusted_pubkeys` is non-empty; mutated payload exits 4; `--require-known-key` without a key fails; `commit.tree` equals `HEAD^{tree}` on a clean tree and changes when an untracked file is added; `record sign` refuses a zero-test run and a run whose tree changed; key protection: mode `0600` asserted on POSIX, owner-only ACL asserted via `icacls` on Windows, and the readable-key refusal tested on both; private key never appears in the record JSON; agent fields are `id` / `agent_name` / `agent_version` / `model` / `session`.
- **Done.** No floats in payloads; `key_id` matches the specified hash prefix; no code path signs a payload `proof-ci` did not build; the tree hash uses `git rev-parse --git-path index` and an absolute `GIT_INDEX_FILE` (tested in a linked worktree); `git --version` preflight exits 3 below 2.0; the mode-less filesystem exception of Section 4.7 is implemented and tested with a monkeypatched mount type; Windows reads `%USERNAME%` before `os.getlogin()`. Do not implement the savings estimator yet.

#### M9 — Savings measurement, ledger, and `proof-ci savings`

- **Goal.** Section 5.12 and Section 5.7.8. Compute a `Savings` object on every run (metrics in the order wrong turns, CI runs avoided with deduplication, local compute not priced by default, wait versus the full local suite, tokens measured from pytest's stdout), print the time-first headline, persist the SQLite ledger (with `commit_tree` and `ci_runs_dedupe_key`; migrate M5's last-tree stub into it), refresh calibration including `fixed_overhead_ms` and the payoff verdict, embed `payload.savings` when signing, and ship `proof-ci savings` / `proof-ci savings calibrate`.
- **Files.** `src/proof_ci/savings.py`, runner and CLI integration, `tests/test_savings.py`.
- **Tests.** The unit and integration cases listed under savings in Section 5.16: uncalibrated low confidence; calibrated deselected durations; local minutes unpriced by default and priced only with `price_local_minutes`; tokenizer stability and `raw_bytes` equal to captured stdout; negative `tokens_delta` on a small green run; wrong-turns count; `ci_runs_avoided` once per `dedupe_key`; wait capped at `medium` for `full_suite_local`; `defaults_used` = absent keys; headline without a dollar sign until `prices_configured`; payoff verdict thresholds; no floats; tampered `payload.savings` fails verify; `savings --json` matches `SavingsReport`; `--since` / `--by` / `--weekly` group the ledger.
- **Done.** Every `proof-ci run` writes a headline and a ledger row. Calibration runs set `is_calibration_run: true`, record `fixed_overhead_ms` and `payoff`, and do not claim local compute or wait savings against themselves. Absent price keys appear in `defaults_used`; a fresh `init` shows a headline with no money in it.

#### M10 — CI config discovery

- **Goal.** Section 5.13. `init --discover` writes a high-confidence pytest command. Discovery goes through `CICommandSource` adapters for GitHub Actions and GitLab CI.
- **Files.** `src/proof_ci/discover.py`, `src/proof_ci/adapters/ci/github_actions.py`, `src/proof_ci/adapters/ci/gitlab_ci.py`, `tests/test_discover.py`, fixtures with sample `.gitlab-ci.yml` and GitHub workflow YAML.
- **Tests.** Extracts pytest; ignores deploy jobs; does not execute anything (the test is that no subprocess of the discovered command is spawned — discovery has no `subprocess` of user scripts).
- **Done.** Non-pytest discovered commands are stored only as suggestions; the runner still refuses to execute `make` / `tox` in Phase 1. Core `discover.py` does not parse vendor YAML itself.

#### M11 — MCP server

- **Goal.** Section 5.14. Six tools, stdio only, vendor-neutral descriptions.
- **Files.** `src/proof_ci/adapters/agent/` (or `mcp_server.py` living there), `tests/test_mcp.py`, `tests/test_conformance_generic_client.py`.
- **Tests.** Tool list is exactly the six names (`run_affected_tests`, `get_failure_details`, `list_flaky_tests`, `verify_record`, `explain_failure`, `get_savings_report`); descriptions do not mention a vendor; `run_affected_tests` against the demo project returns a `RunSummary` with a savings headline; `explain_failure` and `get_savings_report` work; `verify_record` on a signed file works; the generic-client harness passes; stdout of the server is not polluted by banners (if feasible to assert).
- **Done.** `proof-ci mcp` is the entry. Built against `mcp>=1.2,<2`; each tool returns one JSON text block (plus `structuredContent` where the SDK supports it); `run_affected_tests` with `sign: true` on a zero-test run is a tool error. README can still wait for M12, but the server itself is complete.

#### M12 — Example project polish, own CI, and docs

- **Goal.** Section 5.16 example project completeness, Section 5.17 pipelines (including import-lint), Section 5.18 docs.
- **Files.** `examples/demo-project/**`, `.gitlab-ci.yml`, `.github/workflows/ci.yml`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/CROSS_AGENT_CHECKLIST.md`.
- **Tests.** Full integration list in Section 5.16 is green on `ubuntu-latest`, `macos-latest`, and `windows-latest` on Python 3.11, 3.12, and 3.13 (the optional `ubuntu-24.04-arm` leg is present and `continue-on-error`); the project’s own `pytest` does not fail because of the demo’s deliberate failing test; `AGENTS.md` and `CLAUDE.md` compare equal (or share one included body); the README contains the Section 5.20 support matrix, the container and WSL2 guidance, "Where to keep the signing key", and the Section 1.2 "when it is worth installing" paragraph.
- **Done.** A stranger can read the README and run the quickstart using any MCP client or the CLI, on Linux, macOS, or native Windows; the README states what a signed record proves (Section 4.7 wording), that install is into the project's environment, and shows the Windows MCP configuration.

#### M13 — Acceptance pass

- **Goal.** Walk Section 5.19 as a checklist. Fix gaps. Do not add features.
- **Files.** Whatever failed the checklist.
- **Tests.** The entire suite, lint, typecheck, and import-lint.
- **Done.** Every item in Section 5.19 (20 criteria) is true, including the Windows run on Chris's machine. Write a short `PHASE1_STATUS.md` in the repo summarizing the checklist with yes/no evidence (command names or test names). This file is for Chris, not for end users.

### Phase 2 milestones (after Phase 1 acceptance)

These are larger; each may still be one Claude session if scoped as below, or split further at implementation time without changing the external API.

| ID | Goal | Done when |
| --- | --- | --- |
| P2-M1 | API skeleton, workspace/user/token model, auth middleware | Cross-tenant 404/403 tests pass on a dummy resource |
| P2-M2 | Remote cache GET/PUT/HEAD + CLI write-through / fallback | Two local processes share a hit through a test server |
| P2-M3 | Team flake upload/list/mark + CLI sync | Shared classification matches Section 5.11 |
| P2-M4 | Record upload + public verify endpoint | Tampered payload rejected |
| P2-M5 | `proof-ci login` / `logout` / `--offline` | Token file mode 0600; `--offline` makes zero network calls (mock/assert) |
| P2-M6 | GitHub Check Run + GitLab commit status posting via `ForgeAdapter.post_status` | Both mocked hosts receive the same generic fields plus the savings headline |
| P2-M7 | Warm sandbox pool + `--remote-execute` | One declared image path works; cross-workspace reuse impossible |
| P2-M8 | Quota hooks | Documented 402/429 error codes |
| P2-M9 | Savings dashboard, aggregate API, status-check headline, per-committer invoice preview, and Section 6.13 checklist | Dashboard and isolation tests pass; invoices never read savings figures; checklist similar to M13 |

### Phase 3 milestones (after Phase 2 acceptance)

| ID | Goal | Done when |
| --- | --- | --- |
| P3-M1 | Credentials broker + one OIDC integration | Short-lived token issued and audited |
| P3-M2 | Policy language + approval flow | `strict` preset fail-closed tests pass |
| P3-M3 | Merge queue batching | Green batch merges in order |
| P3-M4 | Auto-bisect on red batch | Culprit identified; others proceed |
| P3-M5 | Audit export + Phase 3 acceptance checklist (Section 7.5) | JSONL export contains required fields |

---

## 9. Ready-to-paste Claude Code prompts

Use these prompts as-is. Each one tells Claude Code to read this specification, implement only that milestone, write tests, run them, and stop with a report. If a data contract appears insufficient, Claude must ask Chris before changing it.

Replace `/absolute/path/to/proof-ci_build_spec.md` with the actual path of this file (or add the file to the repository and point at the in-repo path). Replace `/absolute/path/to/proof-ci-repo` with the source repository root.

### 9.1 Kickoff prompt

```
You are implementing proof-ci, an open-source agent-native CI layer.

Read the build specification at:

  /absolute/path/to/proof-ci_build_spec.md

Read it all the way through before writing any code. That document is authoritative. This conversation is not a license to invent product decisions, extra MCP tools, extra CLI commands, or schema fields.

Rules:
- Implement only what the spec asks for, milestone by milestone, starting at M1 when I say so (or starting at M1 now if this is a fresh repo).
- Do not implement Phase 2 or Phase 3 during Phase 1 work.
- Do not change data contracts in Section 4. If a contract cannot be implemented as written, stop and ask me before editing any schema.
- Machine-readable JSON and exit codes must match the spec exactly.
- Write tests first or alongside the code. Run the tests. Do not leave the tree red.
- Python 3.11+, src layout, Hatchling, Typer, MIT license.
- Linux, macOS, and native Windows 11 are all supported in Phase 1; follow Section 5.20 (UTF-8 everywhere, forward-slash repo paths, no shell=True, owner-only key ACL on Windows).
- pytest runs only as a subprocess through [project].test_command with -p proof_ci.pytest_plugin and PROOF_CI_RESULTS_FILE / PROOF_CI_COLLECT_FILE (Section 5.10). Never call pytest.main() in-process. No pytest11 entry point.
- Signed records are tamper-evident statements of what proof-ci reported, bound to a tree hash and a selection; never describe them as proof that tests ran (Section 4.7).
- Positioning in docs and messages leads with what the user gets (dependents the agent did not know to run, a flake-aware verdict, one structured failure, a signed record, fewer wasted turns), never with CI minutes or token dollars (Section 1.2). Savings figures are time-first; money only when every applied price is configured (Section 5.12).
- No telemetry, no network listener, no account system in Phase 1.
- Keep the core vendor-neutral (Section 3.5). Put vendor-specific code in adapters. Do not import vendor SDKs from core modules. Do not assume the client is Claude Code in MCP tool descriptions, CLI flags, or data-contract field names.
- When you finish a milestone, stop. Report: what you implemented, files touched, tests run and their results, anything you were unsure about, and the exact next milestone name. Do not start the next milestone until I ask.

If the repository is empty, create the layout from Section 5.2 as you work through the milestones. Do not add features that belong to a later milestone just because they are easy.

Confirm you have read Sections 1–5 and list the Phase 1 milestones in order. Then wait for my next instruction unless I already named a milestone in this message.
```

### 9.2 Phase 1 milestone prompts

**M1**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Sections 5.2, 5.3, 5.5, 5.6, and milestone M1 in Section 8.

Implement only M1: package scaffold.

Create the Hatchling src layout (including empty src/proof_ci/adapters/{agent,forge,ci,runners}/ packages), pyproject.toml with the dependencies listed in Section 5.3 (mcp pinned >=1.2,<2; no pytest11 entry point), a Typer app named `app` in src/proof_ci/cli.py with the shared --json/--repo/--config parameter helper from Section 5.7, python -m proof_ci support, UTF-8 stdout/stderr reconfiguration at startup, MIT LICENSE, .gitignore, and tests/test_cli_smoke.py. Do not add vendor SDK imports to core modules.

`proof-ci --version` must print `proof-ci 0.1.0`. `proof-ci --help` must exit 0. The smoke test must pass on native Windows as well as Linux (Section 5.20).

Do not implement init, run, MCP, or signing yet. You may stub empty subcommand groups only if Typer needs them; prefer not to.

Write tests, run `pip install -e ".[dev]"` and `pytest`, and stop. Report files touched, test results, and any uncertainties. Ask me before changing any data contract. Next milestone after this is M2; do not start it.
```

**M2**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Sections 5.4, 5.7.1, and milestone M2 in Section 8.

Implement only M2: configuration loading and `proof-ci init` (including `--force`, `--no-key`, and `--json`). Write proof-ci.toml defaults as specified, including [affected].base = "auto", [record].trusted_pubkeys = [], test_command as a TOML array (accept a string too, split with shlex posix=(os.name != "nt"); test that a Windows path survives), and the [savings] price keys commented out. Create .proof-ci/ directories (including collect-cache/) and append the specified entries to .gitignore. Tolerate an unwritable config directory and warn when running as root against a repo owned by another uid. All text files are written as UTF-8 with "\n" line endings (Section 5.20).

Key generation is M8. Do not generate signing keys now. Leave a TODO(M8) if init would otherwise create a key.

Write tests as listed in M2, run them, and stop with a report. Do not start M3. Ask before changing data contracts.
```

**M3**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially all of Section 4 and milestone M3 in Section 8.

Implement only M3: copy the JSON Schema draft 2020-12 documents into src/proof_ci/schemas/ (including savings.json and savings_report.json) and write matching Pydantic v2 models with extra='forbid'. Add tests that the examples in Section 4.8 validate and that the required conditional (Failure present iff outcome is failed or error) is enforced. Agent identity fields are id, agent_name, agent_version, model, and session — do not invent vendor-specific keys.

Do not implement signing, running tests, or CLI commands beyond what already exists. If you believe a schema is inconsistent, stop and ask me rather than “fixing” it.

Write tests, run them, and stop with a report. Do not start M4.
```

**M4**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Sections 5.7.3, 5.10, 5.15 (redaction), 5.16, and milestone M4 in Section 8.

Implement only M4: the pytest plugin, redaction, runner, and `proof-ci run --all` / explicit test arguments. Create examples/demo-project with the deliberate failing test, passing tests, and the layout described in Section 5.16. `proof-ci run --all` against the demo project must exit 1 and produce a valid RunSummary whose failures list includes a repro_command.

pytest runs only as a subprocess launched from [project].test_command (array form) with `-p proof_ci.pytest_plugin`; the plugin is inert unless PROOF_CI_COLLECT_FILE or PROOF_CI_RESULTS_FILE is set and writes JSON to that file at session finish (Section 5.10). Do not register a pytest11 entry point. Do not call pytest.main() in-process. The plugin module may import only the standard library and pytest. Normalise report.location to forward-slash repo-relative paths and 1-based lines; double-quote repro_command node ids that need it (Section 5.20). Capture and keep the pytest subprocess stdout for the later token metric. A missing results file is exit 3. Implement `run --failed` (mode "failed", Section 5.7.3). Run the pytest-xdist to-verify test described in M4 and Section 5.10 and report the result; implement the user_properties transport if the controller-side logreport assumption fails.

Keep pytest behind TestRunnerAdapter as specified in Section 3.5. Do not implement affected selection, cache, flakes, signing, savings estimation, discovery, or MCP yet. `proof-ci run` without `--all` may error with a clear message that affected mode arrives in M5, or may temporarily behave like `--all` only if you print a warning; prefer the clear error.

Write tests, run them, and stop with a report. Do not start M5. Ask before changing data contracts.
```

**M5**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Section 5.8, Section 5.7.2, and milestone M5 in Section 8.

Implement only M5: the affected-test algorithm and wire it into `proof-ci affected` and the default `proof-ci run` mode. Honor --base, HEAD, and auto, with auto as the default and the exact resolution rules of Section 5.8 Step 1 (merge-base; HEAD~1 on a clean default branch; HEAD on a dirty tree). Record base_setting and the resolved base_ref. Collection runs through the plugin's collect mode in a subprocess (Section 5.10), never in-process, with the collection cache of Section 5.8 Step 5 (key = test files + conftests + pytest config + pytest_args + test_command; environment-consistency rule). Implement fallback_full_run_globs with the incremental rule of Step 3 (compare matching files against the last run's tree on this branch via git diff-tree; record fallback_suppressed_by_tree; use the last-tree stub described in M5 until the ledger exists), the nested conftest.py special case, non-Python file heuristics, dynamic-import conservatism, repo-path case canonicalisation to git's committed spelling (Section 5.20 rule 1), and empty-selection behavior (exit 0, do not execute tests, explain the resolved base on stderr; `--sign` on an empty selection exits 2 and writes no record).

Add the unit tests listed in Section 5.16 for affected selection and base resolution, plus a demo-project integration test (edit mathops.py → math tests selected, text tests not selected) and one where the edit is committed on a clean feature branch and is still selected.

Do not implement cache, flakes, signing, discovery, or MCP yet.

Write tests, run them, and stop with a report. Do not start M6. Ask before changing data contracts.
```

**M6**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Section 5.9 and milestone M6 in Section 8.

Implement only M6: result caching. Use RFC 8785 canonical JSON of the specified key material and SHA-256. input_hashes must include the test file, its import closure, every conftest.py from the test's directory up to the repo root, the pytest configuration file in effect, and non-Python files the test file references (Section 5.9); python/platform and the installed-distribution part of deps_hash come from the collect-mode plugin output or the collection-cache entry, with the consistency rule of Section 5.8 Step 5 (re-execute served hits if the run pass reports a different environment). Replay hits with cache_hit=true. Never cache outcome=failed or outcome=error. Skip cache reads only when fallback_full_run is true (not when the incremental rule suppressed it). LRU-trim the collection cache together with the result cache. Do not serve cache to suspected/confirmed flaky tests (if the flake store does not exist yet, hook a clearly named function that currently returns False and will be filled in M7).

Write the key stability/sensitivity tests (including conftest, config file, data file, and installed-distribution sensitivity) and a second-run hit test. Implement LRU trim (test with a lowered threshold). Honor --no-cache and [cache].enabled.

Do not implement flakes, signing, discovery, or MCP yet.

Write tests, run them, and stop with a report. Do not start M7. Ask before changing data contracts.
```

**M7**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Sections 5.11, 5.7.4, and milestone M7 in Section 8.

Implement only M7: SQLite flake store, detection algorithm (including the status-transition table), CLI `proof-ci flakes list|show|mark|unmark`, attaching `flaky` to each TestResult, and `--allow-known-flakes` (confirmed and ignored only). Cache replays must not insert outcomes.

Add examples/demo-project/tests/test_flaky.py controlled by PROOF_CI_DEMO_FLAKE as specified. Add unit tests for the transition table.

Do not implement signing, discovery, or MCP yet.

Write tests, run them, and stop with a report. Do not start M8. Ask before changing data contracts.
```

**M8**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Sections 4.6, 4.7, 5.7.5–5.7.7, and milestone M8 in Section 8.

Implement only M8: RFC 8785 canonicalization, Ed25519 sign/verify via the cryptography library, `proof-ci record keygen|sign|verify`, `proof-ci run --sign`, and key creation from `proof-ci init` if a key is missing. The payload must include commit.tree (git write-tree against a temporary copy of the index located with `git rev-parse --git-path index`, absolute GIT_INDEX_FILE, Section 4.7; test it in a linked worktree), selection (copied from the RunSummary), and summary.executed / summary.replayed. Preflight `git --version` and exit 3 below 2.0. Implement the mode-less filesystem exception for the key check (owner uid match plus mount type in the listed set, with a warning) and read %USERNAME% before os.getlogin() on Windows. `record sign` signs only a run that `proof-ci run` produced (default: the last run; `--run RUN_ID`); there is no option to sign an arbitrary file, and it refuses zero-test runs and runs whose tree changed. Key protection: mode 0600 and refuse group/world-readable keys on POSIX; owner-only ACL via icacls and an ACL-based readable-key check on Windows (Section 4.7). Never write the private key into a record or log.

Use the generic agent identity fields (id, agent_name, agent_version, model, session) populated from PROOF_CI_AGENT_* and --agent-id. Do not add vendor-specific fields. payload.savings may be omitted until M9 (omit it; do not invent an empty object).

Add golden tests, the mutation/wrong-key cases, trusted_pubkeys known/unknown cases, tree-hash tests, and the Windows/POSIX key-protection tests. Exit code 4 on verification failure. Honor --pubkey, [record].trusted_pubkeys, and --require-known-key, and report signer: known|unknown. Describe records in docstrings and messages as tamper-evident statements of what proof-ci reported, never as proof that tests ran.

Do not implement savings estimation, discovery, or MCP yet. Keep core vendor-neutral.

Write tests, run them, and stop with a report. Do not start M9. Ask before changing data contracts.
```

**M9**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Sections 4.5, 5.12, 5.7.8, and milestone M9 in Section 8.

Implement only M9: the savings estimator, calibration store, SQLite ledger (with commit_tree and ci_runs_dedupe_key; migrate M5's last-tree stub into it), one-line headline after every `proof-ci run`, embedding of payload.savings in signed records, and `proof-ci savings` / `proof-ci savings calibrate` with --since, --until, --by, --weekly, and --json, including the payoff verdict.

Follow the honesty rules of Section 5.12 exactly: metrics in the order wrong_turns, ci_runs_avoided (deduplicated by branch + commit_tree + failing set), local_compute (minutes of the user's machine, priced only when price_local_minutes is true), wait (baseline_kind; full_suite_local capped at medium), tokens (raw_bytes from the captured pytest stdout; tokens_delta may be negative). No invented baselines, integer micro-USD only (Decimal ROUND_HALF_UP), defaults_used = price keys absent from the TOML, prices_configured gates every dollar figure, headline time-first with money only when prices_configured. Calibration runs record fixed_overhead_ms and payoff and must not claim local compute or wait savings against themselves.

Write the tests listed in Section 5.16 for savings, including a tampered-savings verify failure. Keep this module vendor-neutral (no vendor SDKs, no agent-specific branches).

Write tests, run them, and stop with a report. Do not start M10. Ask before changing data contracts.
```

**M10**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Section 5.13, Section 3.5 (CICommandSource), and milestone M10 in Section 8.

Implement only M10: read-only discovery of test commands through CICommandSource adapters for GitHub Actions and GitLab CI. `proof-ci init --discover` writes a high-confidence pytest invocation to [project].test_command. Never execute discovered scripts. Ignore deploy/release/publish/prod jobs. If the configured test_command is not a pytest invocation, `proof-ci run` exits 2 with a message telling the user to set a pytest command.

Core discover.py is a facade; YAML parsing lives in adapters/ci/. Do not import vendor SDKs.

Write fixture-based tests. Do not implement MCP yet.

Write tests, run them, and stop with a report. Do not start M11. Ask before changing data contracts.
```

**M11**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Section 5.14, Section 3.5, and milestone M11 in Section 8.

Implement only M11: the MCP server over stdio (`proof-ci mcp`) using the official MCP Python SDK (pinned mcp>=1.2,<2) from the agent adapter package. Advertise exactly these six tools: run_affected_tests, get_failure_details, list_flaky_tests, verify_record, explain_failure, get_savings_report. Match the input and output schemas in Section 5.14. Each tool returns its JSON object as one text content block (plus structuredContent where the SDK supports it). Tool descriptions must not name Claude, Cursor, Codex, or any other agent. Do not add other tools. Do not open a TCP port. Do not write banners to stdout. run_affected_tests runs pytest as a subprocess (never in-process) and returns a tool error for sign:true on a zero-test run.

Write tests (tool listing plus end-to-end calls against the demo project for run_affected_tests, explain_failure, and get_savings_report) and tests/test_conformance_generic_client.py, which drives the CLI and MCP server as a generic client with no vendor-specific behavior.

Write tests, run them, and stop with a report. Do not start M12. Ask before changing data contracts.
```

**M12**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Sections 5.16–5.18 and milestone M12 in Section 8.

Implement only M12: finish examples/demo-project (including tests/test_data.py and tests/data/sample.json if missing), add .gitlab-ci.yml and .github/workflows/ci.yml that run lint, typecheck, import-lint, and pytest (GitHub test matrix: ubuntu-latest, macos-latest, windows-latest on Python 3.11, 3.12, and 3.13; the Windows leg is required; an optional continue-on-error ubuntu-24.04-arm leg), and write README.md, AGENTS.md, and CLAUDE.md as specified (lead with what the user gets per Section 1.2 and include its alternatives paragraph; identical agent-facing guidance in AGENTS.md and CLAUDE.md; generic MCP stdio snippet plus the native Windows and WSL forms; install-into-the-project-environment note; the Section 5.20 support matrix, container and WSL2 guidance, and "Where to keep the signing key"; what a signed record does and does not prove; savings headline explanation including the payoff verdict; sandbox honesty statement). Add docs/CROSS_AGENT_CHECKLIST.md covering Claude Code and at least one other agent (Cursor or Codex).

Make sure the proof-ci project's own pytest suite stays green (the demo project's deliberate failure must not be collected as part of the unit suite).

Run the full test suite, plus ruff, mypy, and import-lint if configured. Stop with a report. Do not start M13.
```

**M13**

```
Read /absolute/path/to/proof-ci_build_spec.md, especially Section 5.19 and milestone M13 in Section 8.

Do not add features. Walk every acceptance criterion in Section 5.19. Fix any gap. Run the full test suite, ruff, mypy, and import-lint.

Write PHASE1_STATUS.md listing each of the 20 criteria with yes/no and a pointer to evidence (test name or command). Criterion 19 (native Windows run) needs evidence from Chris's machine or the windows-latest CI log.

Stop with a report summarizing remaining no's, if any. Ask before changing data contracts.
```

### 9.3 Phase 2 summary prompt

```
Phase 1 is accepted. Read /absolute/path/to/proof-ci_build_spec.md Sections 4, 6, and 8 (Phase 2 milestones).

We will implement Phase 2 milestone by milestone, starting with P2-M1 when I name it. The CLI and MCP tool names from Phase 1 stay stable. Local offline behavior remains the default. Do not break Phase 1 acceptance tests.

Remote cache, team flakes, record storage, public verify with the signer registry, warm sandboxes, GitHub/GitLab status checks (via ForgeAdapter, posted equally), the savings dashboard, and flat per-committer pricing are specified in Section 6. Lead the paid offer with team flake history and the record store / status checks / signer registry; the shared cache is an included extra. Pricing is plan-limit hooks plus a per-committer plan price read from server configuration; do not hard-code dollar amounts, and never compute an invoice from savings figures. Accept signed records from any agent_name and any supported forge.

Keep the hosted service vendor-neutral: no Claude-only or GitHub-only API paths for features that GitLab or another agent should also have.

If a Section 4 contract needs a field to support remotes, stop and ask me before adding it.

When I name a Phase 2 milestone, implement only that milestone, write tests, run them, and stop with a report.
```

### 9.4 Phase 3 summary prompt

```
Phase 2 is accepted. Read /absolute/path/to/proof-ci_build_spec.md Sections 7 and 8 (Phase 3 milestones).

We will implement Phase 3 milestone by milestone, starting with P3-M1 when I name it. Do not break Phase 1 or Phase 2 behavior when Phase 3 features are unconfigured.

Build the credentials broker (short-lived, least-privilege, OIDC-first), the small policy language with human approval gates (fail closed on production, pipeline edits, wildcard admin), the merge queue with batching and auto-bisect, and the append-only audit log.

Never return a credential for a pending or denied approval. Never store raw prompts unless a workspace setting explicitly enables it.

When I name a Phase 3 milestone, implement only that milestone, write tests, run them, and stop with a report. Ask before changing data contracts.
```

---

## 10. Risks, open questions, and success metrics

### 10.1 Risks and mitigations

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Import graph misses dynamic imports and plugin hooks | Under-selection: an agent ships a real break that `proof-ci run` did not see | Conservative fallbacks (Section 5.8); document the gap; prefer running too many tests; consider coverage-based selection later |
| Over-selection on config changes | Inner loop becomes a full suite and feels like classical CI; with `base = auto` a config change early on a branch would otherwise force a full run on every later run | Full run on dependency/config globs is intentional and correct for the run that sees the change; the incremental rule (Section 5.8 Step 3) suppresses it once the matching files are unchanged since the last run on the branch; keep the glob list tight and documented |
| Fixed overhead exceeds the suite | On a small or fast suite proof-ci is slower than plain pytest and users conclude it does not work | `savings calibrate` payoff verdict (`overhead_dominates`) tells them so; collection cache (Section 5.8 Step 5) removes the dominant fixed cost on large suites; README says when not to use it |
| Flake detector marks a real regression as flaky | Agent ignores a genuine break | `code_hash` isolates results to unchanged inputs; `suspected` is not ignored by `--allow-known-flakes`; humans can `unmark` |
| Flake detector is too shy | Agents still loop on a well-known flake | `min_runs` default of 3 is low enough to catch repeated flips; team history in Phase 2 accumulates evidence faster |
| Cache key omits an input | False cache hits hide failures | Key includes transitive source hashes, the `conftest.py` chain, the pytest config file, referenced data files, pytest args, Python version, platform, lockfile hash, and the installed-distribution list; never cache `failed` or `error`; no cache reads on a full-run fallback |
| Clean committed tree selects nothing | A green, signed, zero-test run looks like evidence | `base = auto` with merge-base / `HEAD~1` rules; stderr explains an empty selection; zero-test runs are never signed |
| Cache or record stores a secret from test output | Leak via artifacts or Phase 2 storage | Redaction (Section 5.15); size limits; server-side rejection of private-key PEMs; records omit environment variables |
| Agents ignore structured output and ingest raw logs anyway | Token waste, the original pain | `AGENTS.md` / `CLAUDE.md` instructions; `explain_failure` as the preferred drill-down; keep summaries short |
| MCP surface is copied by a competitor | Hard to monetize a protocol alone | Accepted. Phase 1 is MIT by design. Moat is shared cache, shared flakes, hosted verification, sandboxes, and Phase 3 controls |
| YAML discovery executes something dangerous | Prompt-injected repo content runs a deploy | Discovery is `safe_load` + string inspection only; runner executes pytest only |
| Signing keys treated casually, or a record read as more than it is | A forged or over-read record is treated as proof of execution | Owner-only key files on every platform; `[record].trusted_pubkeys` and `signer: known\|unknown`; `--require-known-key`; records bind a tree hash and a selection; `record sign` signs only runs `proof-ci` produced; Section 4.7 wording in every doc; Phase 2 verifies and checks the signer registry before storage |
| People treat Phase 1 as a security sandbox | False sense of isolation | Explicit non-goal and README warning |
| People treat a green proof-ci record as permission to skip classical CI | Lost trust root | Repeated framing: existing CI still runs; status checks are additional |
| Windows breaks in a way CI does not see | The founder's own machine cannot run the tool; agents on Windows get mangled paths or cp1252 JSON | Section 5.20 rules; `windows-latest` is a required CI leg; acceptance criterion 19 requires a native Windows run |
| In-process pytest in a long-lived MCP server | State leaks between runs; a test crash kills the server; wrong interpreter | Subprocess-only execution through `test_command` (Section 5.10); preflight that the plugin is importable |
| Phase 2 remote execute of a dirty tree is hard | Agents often have uncommitted work | Prefer the agent commit; dirty sync is size-limited and may trail the rest of Phase 2 |
| Savings estimates are gamed or over-read (inflated prices, skipped calibration, laptop minutes read as CI minutes, one failure counted twenty times) | Users or agents could advertise fake ROI, or stop believing any figure | Signed `payload.savings`; `defaults_used` = absent keys and `prices_configured` gate every dollar; local minutes never priced at the CI rate; `ci_runs_avoided` deduplicated by failing set + tree; tokens measured from real pytest stdout; billing never reads savings (Section 6.12); mutating savings breaks verify |
| Baseline drift | A stale calibration overstates local compute and wait savings as the suite grows or slows | `stale` status from max age and run count; `run --all` refreshes calibration; stale and missing calibration are named in the headline |
| Users distrust savings figures | One overconfident headline poisons the feature | Never invent a baseline; show measured vs estimated; time first and money only when prices are configured; never publish aggregates except from signed records |
| Accidental lock-in to Claude Code | This spec is implemented with Claude Code, so it is easy to bake in Claude-only MCP metadata, docs, or env vars | Section 3.5 adapters; import-lint; identical `AGENTS.md` / `CLAUDE.md`; generic-client conformance tests; manual cross-agent checklist |

### 10.2 Open questions

These are deliberately unresolved. Do not pick an answer in code during Phase 1 if the specification already gave a default. If a later phase needs a decision, ask Chris.

1. **Other languages.** When does JavaScript/TypeScript (Jest/Vitest/pytest-equivalent) land? Data contracts are ready; runners are not. Default: after Phase 1 is used on real Python repos.
2. **Coverage-based selection.** Import graphs miss some dependencies. Should Phase 1.x grow an optional “learn from prior coverage” mode? Default: not in Phase 1.
3. **Agent identity strings.** Environment variables populate generic fields (`agent_name`, `agent_version`, `model`). The exact `agent_name` string each client should send (`claude-code` vs `cursor` vs `codex`, and so on) is not standardized here; accept any string and do not special-case vendors.
4. **Should a project commit a flake allow-list?** Phase 1 gitignores the SQLite database. A committed YAML allow-list might help teams before Phase 2. Deferred — do not add a second flake source of truth in Phase 1.
5. **pytest-xdist.** Parallel pytest changes report ordering and possibly plugin assumptions. Phase 1 does not promise xdist support. If it works without extra code, fine; do not spend a milestone on it.
6. **Monorepos.** Multiple packages / multiple `test_paths`. Config already has lists for `source_roots` and `test_paths`. True polyglot monorepo orchestration is out of scope.
7. **Public verify unguessable URLs vs signed-URL expiry.** Section 6.7 chooses unguessable ids. Expiring signed URLs are an alternative if records must later become private.
8. **Merge queue host integration.** Drive GitHub’s merge queue vs implement our own? Section 7.3 prefers integration; the first hosted target should be chosen when Phase 3 starts.
9. **Raw prompt retention.** Default off. Some enterprises will want it on for incident response. Confirm retention days with them; do not enable it globally.
10. **PyPI publication and Git hosting.** This specification does not choose the public GitHub/GitLab URL or the PyPI account. Chris will decide at first release.
11. **Tokenizer.** Phase 1 freezes `utf8_bytes_div_4`. Should a later revision plug in a vendor tokenizer? Only if it remains optional and does not become a required vendor dependency in core.
12. **Standard attestation envelope.** The in-toto attestation framework defines a test-result predicate (`https://in-toto.io/attestation/test-result/v0.1`) and the DSSE envelope that Sigstore and forge attestation features can verify. Should a later revision wrap `RunRecord.payload` as an in-toto statement (subject = `commit.tree`, predicate = the payload) so existing verifiers can check proof-ci records? Phase 1 keeps the bespoke `{payload, signature}` envelope of Section 4.6; decide when Phase 2 record storage is designed. Not a Phase 1 task.

### 10.3 Success metrics

Do not invent baseline numbers you have not measured. Measure these after Phase 1 exists, then set numerical goals from that data.

| Metric | How to measure | Design intent |
| --- | --- | --- |
| Time-to-feedback | Wall-clock of `proof-ci run` for a typical one-file change in the demo project and in at least one real repo, on a warm machine | Target under 30 seconds for that class of Python unit-test change (R4). Compare to the repo’s existing CI queue time as a qualitative contrast, not as a published industry statistic |
| Tokens delta | `Savings.tokens.tokens_delta` on signed runs (measured against pytest's real stdout at the configured verbosity) | Expect negative values on green runs and small positives on failing runs; publish the distribution, not a ratio |
| Local test minutes not executed | Sum of `test_ms_not_executed` on calibrated runs | A measure of the user's machine time, never compared to or reported as CI minutes |
| Payoff verdict distribution | Fraction of calibrated repositories with `payoff = selection_saves_time` vs `marginal` vs `overhead_dominates` | Tells Chris which repositories the tool is for; informs the README's "when to use it" paragraph |
| Calibration freshness | Fraction of runs with `calibration.status = current` | Should rise after teams run `savings calibrate` or `run --all` |
| Savings headline coverage | Fraction of runs that emit `savings_headline` | Should be all runs after M9 |
| Default-price usage | Fraction of ledger rows with a non-empty `defaults_used` | Should fall as users put real rates in `proof-ci.toml`; Phase 2 billing requires this to be empty |
| Cross-agent conformance | Generic-client tests stay green; manual checklist completed for Claude Code and at least one other agent | No Claude-only behavior in core |
| Selection precision / recall | On a fixture repo, which tests *should* run after a given diff (human-labeled) vs which tests *did* run | Optimize for recall first (conservative selection). Track precision so full-run fallbacks do not become the hidden default |
| Cache hit rate | `cache.hits / (cache.hits + cache.misses)` over inner-loop runs that are not `--all` | Should rise as an agent iterates without touching inputs of most tests |
| Flake false-positive rate | Fraction of tests automatically marked `suspected` or `confirmed` that a human later `unmark`s because they were real, deterministic failures | Keep this low; if humans often unmark, tighten the algorithm (higher `min_runs` or `min_flips_confirmed`) |
| Flake false-negative rate | Known-flaky tests (human labeled) that remain `none` after `min_runs` mixed outcomes | Should be near zero for tests that actually flip on an unchanged `code_hash` |
| Adoption | Weekly active repositories with at least one `proof-ci run`; MCP sessions that call `run_affected_tests`; signed records produced | Meaningful only after third parties can install the tool |
| Phase 2-specific | Remote cache hit rate across a workspace; verify-endpoint hits; status checks posted; remote-execute start delay on a warm sandbox | Used to judge whether hosted features earn their keep |
| Phase 3-specific | Median credential lifetime; approval wait time; batch-merge success rate; bisect time to culprit; audit completeness (privileged actions with a row) | Used to judge whether enterprises can trust agents at volume |

When publishing any number (README, blog post, launch materials), only cite figures you measured on named workloads, or do not cite a figure.

### 10.4 Final instruction to implementers

Build the layer that any coding agent can actually use: fast, structured, local-first, conservative when unsure, honest about estimates and isolation, vendor-neutral at the core, and able to show what it checked. Lead with what the user gets: dependents the agent did not know to run, a flake-aware verdict, one structured failure, a signed record of what ran, fewer wasted turns. Report CI minutes and token dollars honestly and never lead with them. Do not build a new Jenkins. Do not build a Claude-only plugin. Do not build a startup pitch. Follow the milestones, keep the contracts stable, and ask Chris when the specification is silent on something that would affect an interface.

---

*End of proof-ci build specification v1.0.0.*
