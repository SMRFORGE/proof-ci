"""Pydantic v2 models matching the JSON Schemas in ``proof_ci/schemas`` (spec Section 4).

The schema files are the compatibility boundary; these models mirror them so
code can build and validate objects without hand-writing dicts. Tests check
that both accept and reject the same corpus (spec examples plus mutations).

Serialisation rule: a field that is optional in the schema and *not* nullable
is omitted when it is ``None`` (the schema would reject ``null``). Fields whose
schema type includes ``"null"`` are listed in ``_nullable`` and always emitted.
Use :meth:`ContractModel.to_json` for anything written to disk or stdout.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

__all__ = [
    "SCHEMA_FILES",
    "SCHEMA_VERSION",
    "Agent",
    "CacheStats",
    "Calibration",
    "Check",
    "CiRunsMetric",
    "Commit",
    "ContractModel",
    "Counts",
    "Environment",
    "Failure",
    "FailureSummary",
    "FlakeInfo",
    "FlakeRecord",
    "FlakeWindow",
    "LikelyCause",
    "LocalComputeMetric",
    "Location",
    "PricesUsed",
    "RecordSelection",
    "RecordSummary",
    "RunPayload",
    "RunRecord",
    "RunSummary",
    "Savings",
    "SavingsGroup",
    "SavingsReport",
    "SavingsTotals",
    "Selection",
    "Signature",
    "TestResult",
    "TokensMetric",
    "WaitMetric",
    "WrongTurnsMetric",
    "load_schema",
]

SCHEMA_VERSION = "1.0.0"

SCHEMA_FILES: dict[str, str] = {
    "test_result": "test_result.json",
    "run_summary": "run_summary.json",
    "flake_record": "flake_record.json",
    "savings": "savings.json",
    "savings_report": "savings_report.json",
    "run_record": "run_record.json",
}


def load_schema(name: str) -> dict[str, Any]:
    """Load a bundled schema by short name (``"test_result"``) or file name."""
    filename = SCHEMA_FILES.get(name, name)
    text = resources.files("proof_ci.schemas").joinpath(filename).read_text(encoding="utf-8")
    result: dict[str, Any] = json.loads(text)
    return result


NonNeg = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(ge=1)]
NonEmpty = Annotated[str, Field(min_length=1)]
SchemaVersion = Literal["1.0.0"]
Outcome = Literal["passed", "failed", "skipped", "error", "xfailed", "xpassed"]
RunOutcome = Literal["passed", "failed", "error"]
SelectionMode = Literal["affected", "all", "explicit", "failed"]
Confidence = Literal["high", "medium", "low"]
MetricStatus = Literal["measured", "estimated", "unavailable"]


class ContractModel(BaseModel):
    """Base for every contract object: strict keys, schema-shaped output."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    _nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _reject_null_for_non_nullable(cls, data: Any) -> Any:
        if isinstance(data, dict):
            bad = sorted(k for k, v in data.items() if v is None and k not in cls._nullable)
            if bad:
                raise ValueError(f"null is not allowed for: {', '.join(bad)}")
        return data

    @model_serializer(mode="wrap")
    def _drop_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        fields = type(self).model_fields
        out: dict[str, Any] = {}
        for k, v in data.items():
            if v is None:
                if k not in self._nullable:
                    continue
                # A nullable optional field is emitted only if it was set or is required.
                if k not in self.model_fields_set and not fields[k].is_required():
                    continue
            out[k] = v
        return out

    def to_json(self) -> dict[str, Any]:
        """Plain JSON-compatible dict, valid against the matching schema."""
        result: dict[str, Any] = self.model_dump(mode="json")
        return result


# --------------------------------------------------------------- TestResult (4.2)


class Location(ContractModel):
    file: NonEmpty
    line: Positive
    function: str | None = None


class FlakeInfo(ContractModel):
    is_flaky: bool
    status: Literal["none", "suspected", "confirmed", "resolved", "ignored"]
    flip_count: NonNeg | None = None
    reason: str | None = None


class LikelyCause(ContractModel):
    file: str | None = None
    line: Positive | None = None
    diff_hunk: str | None = None
    rationale: str


class Failure(ContractModel):
    error_type: str
    error_message: str
    file: str | None = None
    line: Positive | None = None
    assertion: str | None = None
    traceback: str | None = None
    likely_cause: LikelyCause | None = None
    repro_command: NonEmpty


class TestResult(ContractModel):
    __test__ = False  # not a pytest test class

    schema_version: SchemaVersion
    test_id: NonEmpty
    outcome: Outcome
    duration_ms: NonNeg
    location: Location
    message: str | None = None
    cache_hit: bool
    flaky: FlakeInfo | None = None
    stdout: str | None = None
    stderr: str | None = None
    markers: list[str] | None = None
    failure: Failure | None = None

    @model_validator(mode="after")
    def _failure_iff_failed(self) -> TestResult:
        failed = self.outcome in ("failed", "error")
        if failed and self.failure is None:
            raise ValueError(f"outcome {self.outcome!r} requires 'failure'")
        if not failed and self.failure is not None:
            raise ValueError(f"outcome {self.outcome!r} must not carry 'failure'")
        return self


# --------------------------------------------------------------- Savings (4.5)


class PricesUsed(ContractModel):
    ci_cost_per_minute_micro_usd: NonNeg
    price_local_minutes: bool
    local_compute_cost_per_minute_micro_usd: NonNeg
    model_input_cost_per_million_tokens_micro_usd: NonNeg
    model_output_cost_per_million_tokens_micro_usd: NonNeg
    wasted_fix_cost_micro_usd: NonNeg
    engineer_hourly_cost_micro_usd: NonNeg
    defaults_used: list[str]


class Calibration(ContractModel):
    _nullable = frozenset({"calibrated_at", "full_suite_wall_ms", "fixed_overhead_ms"})

    status: Literal["missing", "stale", "current"]
    calibrated_at: str | None = None
    test_count: NonNeg
    full_suite_wall_ms: NonNeg | None = None
    fixed_overhead_ms: NonNeg | None = None
    payoff: Literal["unknown", "overhead_dominates", "marginal", "selection_saves_time"]
    uncalibrated_skipped_count: NonNeg | None = None


class WrongTurnsMetric(ContractModel):
    status: MetricStatus
    confidence: Confidence
    flagged_known_flake_failures: NonNeg
    cost_micro_usd: NonNeg
    rationale: str


class CiRunsMetric(ContractModel):
    _nullable = frozenset({"dedupe_key"})

    status: MetricStatus
    confidence: Confidence
    count: Annotated[int, Field(ge=0, le=1)]
    dedupe_key: str | None
    deduplicated: bool
    cost_micro_usd: NonNeg
    rationale: str


class LocalComputeMetric(ContractModel):
    status: MetricStatus
    confidence: Confidence
    deselected_count: NonNeg
    cache_hits: NonNeg
    test_ms_not_executed: NonNeg
    priced: bool
    cost_micro_usd: NonNeg
    rationale: str


class WaitMetric(ContractModel):
    _nullable = frozenset({"baseline_ms"})

    status: MetricStatus
    confidence: Confidence
    baseline_kind: Literal["none", "full_suite_local", "full_pipeline_user"]
    baseline_ms: NonNeg | None
    actual_ms: NonNeg
    wait_ms_saved: NonNeg
    rationale: str


class TokensMetric(ContractModel):
    status: MetricStatus
    confidence: Confidence
    raw_source: Literal["pytest_stdout"]
    raw_bytes: NonNeg
    structured_bytes: NonNeg
    raw_tokens: NonNeg
    structured_tokens: NonNeg
    tokens_delta: int
    cost_micro_usd: NonNeg
    tokenizer: Literal["utf8_bytes_div_4"]
    rationale: str


class SavingsTotals(ContractModel):
    cost_micro_usd: NonNeg
    wrong_turns_avoided: NonNeg
    ci_runs_avoided: NonNeg
    test_ms_not_executed: NonNeg
    wait_ms_saved: NonNeg
    tokens_delta: int


class Savings(ContractModel):
    schema_version: SchemaVersion
    headline: str
    overall_confidence: Confidence
    currency: Literal["USD"]
    is_calibration_run: bool
    prices_configured: bool
    prices_used: PricesUsed
    calibration: Calibration
    wrong_turns: WrongTurnsMetric
    ci_runs_avoided: CiRunsMetric
    local_compute: LocalComputeMetric
    wait: WaitMetric
    tokens: TokensMetric
    totals: SavingsTotals


class SavingsGroup(ContractModel):
    key: str
    week: str | None = None
    runs: NonNeg
    # The schema leaves group totals open ({"type": "object"}); the model uses
    # the same shape as the report totals so the two cannot drift.
    totals: SavingsTotals
    overall_confidence: Confidence


class SavingsReport(ContractModel):
    schema_version: SchemaVersion
    since: str
    until: str
    group_by: Literal["repo", "agent", "branch"]
    weekly: bool
    runs: NonNeg
    overall_confidence: Confidence
    defaults_used: list[str]
    prices_configured: bool
    headline: str
    totals: SavingsTotals
    groups: list[SavingsGroup]


# --------------------------------------------------------------- RunSummary (4.3)


class Selection(ContractModel):
    _nullable = frozenset({"fallback_reason", "fallback_suppressed_by_tree"})

    mode: SelectionMode
    base_setting: str | None = None
    base_ref: str | None = None
    changed_files: list[str] | None = None
    selected_count: NonNeg
    collected_count: NonNeg
    deselected_count: NonNeg
    fallback_full_run: bool | None = None
    fallback_reason: str | None = None
    fallback_suppressed_by_tree: str | None = None


class CacheStats(ContractModel):
    hits: NonNeg
    misses: NonNeg
    disabled: bool


class Counts(ContractModel):
    passed: NonNeg
    failed: NonNeg
    skipped: NonNeg
    error: NonNeg
    xfailed: NonNeg
    xpassed: NonNeg
    flaky_failures: NonNeg
    total_selected: NonNeg


class FailureSummary(ContractModel):
    _nullable = frozenset({"line"})

    test_id: str
    error_type: str
    error_message: str
    file: str
    line: Positive | None
    is_flaky: bool
    repro_command: str


class RunSummary(ContractModel):
    _nullable = frozenset({"record_path"})

    schema_version: SchemaVersion
    run_id: Annotated[UUID, Field(strict=False)]  # JSON carries the string form
    started_at: str
    finished_at: str
    duration_ms: NonNeg
    commit_sha: str | None = None
    commit_tree: str | None = None
    working_tree_dirty: bool | None = None
    selection: Selection
    cache: CacheStats
    counts: Counts
    outcome: RunOutcome
    summary: str
    failures: list[FailureSummary]
    results_path: str
    record_path: str | None = None
    savings_headline: str | None = None
    savings: Savings | None = None


# --------------------------------------------------------------- FlakeRecord (4.4)


class FlakeWindow(ContractModel):
    days: Positive
    runs: NonNeg


class FlakeRecord(ContractModel):
    schema_version: SchemaVersion
    test_id: NonEmpty
    status: Literal["suspected", "confirmed", "resolved", "ignored"]
    first_seen: str
    last_seen: str
    last_code_hash: str
    pass_count: NonNeg
    fail_count: NonNeg
    flip_count: NonNeg
    window: FlakeWindow
    reason: str | None = None
    updated_at: str


# --------------------------------------------------------------- RunRecord (4.6)


class Commit(ContractModel):
    sha: NonEmpty
    tree: NonEmpty
    dirty: bool


class RecordSelection(ContractModel):
    _nullable = frozenset({"fallback_reason", "fallback_suppressed_by_tree"})

    mode: SelectionMode
    base_setting: str | None = None
    base_ref: str
    changed_files: list[str]
    selected_count: NonNeg
    collected_count: NonNeg
    deselected_count: NonNeg
    fallback_full_run: bool
    fallback_reason: str | None
    fallback_suppressed_by_tree: str | None = None


class Agent(ContractModel):
    id: NonEmpty
    agent_name: str | None = None
    agent_version: str | None = None
    model: str | None = None
    session: str | None = None


class Environment(ContractModel):
    python: str
    platform: str
    proof_ci_version: str
    os_release: str | None = None


class Check(ContractModel):
    name: str
    command: str
    selector: SelectionMode | None = None


class RecordSummary(ContractModel):
    outcome: RunOutcome
    passed: NonNeg
    failed: NonNeg
    skipped: NonNeg
    error: NonNeg
    executed: NonNeg
    replayed: NonNeg
    duration_ms: NonNeg


class RunPayload(ContractModel):
    schema_version: SchemaVersion
    run_id: Annotated[UUID, Field(strict=False)]  # JSON carries the string form
    timestamp: str
    commit: Commit
    selection: RecordSelection
    agent: Agent
    environment: Environment
    checks: Annotated[list[Check], Field(min_length=1)]
    summary: RecordSummary
    results: list[TestResult]
    savings: Savings | None = None


class Signature(ContractModel):
    alg: Literal["Ed25519"]
    key_id: str
    public_key: str
    value: str


class RunRecord(ContractModel):
    payload: RunPayload
    signature: Signature | None = None
