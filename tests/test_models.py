"""M3: JSON Schemas and Pydantic models agree, and the spec Section 4.8 examples validate."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError
from referencing import Registry, Resource

from proof_ci import models as m

FIXTURES = Path(__file__).parent / "fixtures" / "spec_examples"

SCHEMAS = {name: m.load_schema(name) for name in m.SCHEMA_FILES}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)

MODEL_FOR: dict[str, type[BaseModel]] = {
    "test_result": m.TestResult,
    "run_summary": m.RunSummary,
    "flake_record": m.FlakeRecord,
    "savings": m.Savings,
    "savings_report": m.SavingsReport,
    "run_record": m.RunRecord,
}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def schema_ok(name: str, obj: Any) -> bool:
    validator = jsonschema.Draft202012Validator(SCHEMAS[name], registry=REGISTRY)
    return not list(validator.iter_errors(obj))


def model_ok(name: str, obj: Any) -> bool:
    try:
        MODEL_FOR[name].model_validate(obj)
    except ValidationError:
        return False
    return True


def run_record_example() -> dict[str, Any]:
    """A RunRecord assembled from the Section 4.8 examples."""
    summary = load("run_summary")
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "run_id": summary["run_id"],
        "timestamp": summary["finished_at"],
        "commit": {"sha": "a" * 40, "tree": "b" * 40, "dirty": False},
        "selection": {
            "mode": "affected",
            "base_setting": "auto",
            "base_ref": "c" * 40,
            "changed_files": ["src/demo/mathops.py"],
            "selected_count": 2,
            "collected_count": 6,
            "deselected_count": 4,
            "fallback_full_run": False,
            "fallback_reason": None,
        },
        "agent": {"id": "task-123", "agent_name": "cli", "model": "any-model"},
        "environment": {"python": "3.13.11", "platform": "Windows", "proof_ci_version": "0.1.0"},
        "checks": [{"name": "pytest", "command": "pytest", "selector": "affected"}],
        "summary": {
            "outcome": "failed",
            "passed": 1,
            "failed": 1,
            "skipped": 0,
            "error": 0,
            "executed": 2,
            "replayed": 0,
            "duration_ms": 412,
        },
        "results": [load("test_result_passed"), load("test_result_failed")],
        "savings": load("savings"),
    }
    return {
        "payload": payload,
        "signature": {"alg": "Ed25519", "key_id": "0" * 16, "public_key": "AAAA", "value": "BBBB"},
    }


EXAMPLES: dict[str, list[str]] = {
    "test_result": ["test_result_passed", "test_result_failed"],
    "run_summary": ["run_summary"],
    "savings": ["savings"],
}


def all_examples() -> list[tuple[str, dict[str, Any]]]:
    out = [(schema, load(fx)) for schema, names in EXAMPLES.items() for fx in names]
    summary_with_savings = load("run_summary")
    summary_with_savings["savings"] = load("savings")
    out.append(("run_summary", summary_with_savings))
    out.append(("run_record", run_record_example()))
    return out


# ------------------------------------------------------------------ schema files


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMAS[name])


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_file_matches_spec(name: str) -> None:
    spec = Path(__file__).resolve().parents[1] / "docs" / "spec" / "proof-ci_build_spec.md"
    if not spec.exists():
        pytest.skip("in-repo spec copy not present")
    lines = spec.read_text(encoding="utf-8").split("\n")
    target = SCHEMAS[name]["$id"]
    for i, line in enumerate(lines):
        if line.strip() == "```json":
            j = i + 1
            while lines[j].strip() != "```":
                j += 1
            try:
                obj = json.loads("\n".join(lines[i + 1 : j]))
            except json.JSONDecodeError:
                continue
            if obj.get("$id") == target:
                assert obj == SCHEMAS[name]
                return
    pytest.fail(f"{target} not found in the spec")


def test_schema_version_is_1_0_0_everywhere() -> None:
    for name, schema in SCHEMAS.items():
        props = schema["properties"]
        if name == "run_record":
            props = props["payload"]["properties"]
        assert props["schema_version"] == {"const": "1.0.0"}, name


# --------------------------------------------------------------------- examples


@pytest.mark.parametrize(
    ("name", "obj"), all_examples(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_examples_validate_in_schema_and_model(name: str, obj: dict[str, Any]) -> None:
    assert schema_ok(name, obj)
    assert model_ok(name, obj)


@pytest.mark.parametrize(
    ("name", "obj"), all_examples(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_model_round_trip_is_schema_valid_and_lossless(name: str, obj: dict[str, Any]) -> None:
    dumped = MODEL_FOR[name].model_validate(obj).to_json()  # type: ignore[attr-defined]
    assert dumped == obj
    assert schema_ok(name, dumped)


# ------------------------------------------------------- shared rejection corpus


def mutations() -> list[tuple[str, str, dict[str, Any]]]:
    """(schema, description, object) triples that both schema and model must reject."""
    cases: list[tuple[str, str, dict[str, Any]]] = []
    for name, obj in all_examples():
        root = obj["payload"] if name == "run_record" else obj
        for key in list(root):
            if name == "run_record" and key == "savings":
                continue
            schema_root = (
                SCHEMAS[name]["properties"]["payload"] if name == "run_record" else SCHEMAS[name]
            )
            if key not in schema_root.get("required", []):
                continue
            bad = copy.deepcopy(obj)
            (bad["payload"] if name == "run_record" else bad).pop(key)
            cases.append((name, f"missing {key}", bad))
        bad = copy.deepcopy(obj)
        (bad["payload"] if name == "run_record" else bad)["unexpected_extra"] = 1
        cases.append((name, "extra key", bad))
        bad = copy.deepcopy(obj)
        (bad["payload"] if name == "run_record" else bad)["schema_version"] = "2.0.0"
        cases.append((name, "wrong schema_version", bad))

    passed = load("test_result_passed")
    failed = load("test_result_failed")
    no_failure = copy.deepcopy(failed)
    no_failure.pop("failure")
    cases.append(("test_result", "failed without failure", no_failure))
    errored = copy.deepcopy(no_failure)
    errored["outcome"] = "error"
    cases.append(("test_result", "error without failure", errored))
    with_failure = copy.deepcopy(passed)
    with_failure["failure"] = failed["failure"]
    cases.append(("test_result", "passed with failure", with_failure))
    zero_line = copy.deepcopy(passed)
    zero_line["location"]["line"] = 0
    cases.append(("test_result", "0-based line", zero_line))
    negative = copy.deepcopy(passed)
    negative["duration_ms"] = -1
    cases.append(("test_result", "negative duration", negative))
    float_ms = copy.deepcopy(passed)
    float_ms["duration_ms"] = 1.5
    cases.append(("test_result", "float duration", float_ms))
    string_ms = copy.deepcopy(passed)
    string_ms["duration_ms"] = "12"
    cases.append(("test_result", "string duration", string_ms))
    null_message = copy.deepcopy(passed)
    null_message["message"] = None
    cases.append(("test_result", "null for non-nullable optional", null_message))

    savings = load("savings")
    two_ci_runs = copy.deepcopy(savings)
    two_ci_runs["ci_runs_avoided"]["count"] = 2
    cases.append(("savings", "ci_runs_avoided.count > 1", two_ci_runs))
    float_cost = copy.deepcopy(savings)
    float_cost["totals"]["cost_micro_usd"] = 0.5
    cases.append(("savings", "float micro-USD", float_cost))

    record = run_record_example()
    vendor_agent = copy.deepcopy(record)
    vendor_agent["payload"]["agent"]["anthropic_session_id"] = "x"
    cases.append(("run_record", "vendor-specific agent key", vendor_agent))
    no_checks = copy.deepcopy(record)
    no_checks["payload"]["checks"] = []
    cases.append(("run_record", "empty checks", no_checks))
    no_tree = copy.deepcopy(record)
    no_tree["payload"]["commit"].pop("tree")
    cases.append(("run_record", "missing commit.tree", no_tree))
    bad_alg = copy.deepcopy(record)
    bad_alg["signature"]["alg"] = "RSA"
    cases.append(("run_record", "wrong signature alg", bad_alg))
    bad_uuid = copy.deepcopy(record)
    bad_uuid["payload"]["run_id"] = "not-a-uuid"
    cases.append(("run_record", "run_id not a uuid", bad_uuid))
    return cases


MUTATIONS = mutations()


@pytest.mark.parametrize(
    ("name", "what", "obj"), MUTATIONS, ids=[f"{n}:{w}" for n, w, _ in MUTATIONS]
)
def test_model_rejects_what_schema_rejects(name: str, what: str, obj: dict[str, Any]) -> None:
    if what == "run_id not a uuid":
        # jsonschema does not assert "format" by default; the model is stricter here.
        assert not model_ok(name, obj)
        return
    assert not schema_ok(name, obj), f"schema accepted: {what}"
    assert not model_ok(name, obj), f"model accepted: {what}"


# ------------------------------------------------------------------ agent fields


def test_agent_accepts_generic_identity_fields() -> None:
    agent = m.Agent.model_validate(
        {"id": "t1", "agent_name": "cursor", "agent_version": "1.2", "model": "m", "session": "s"}
    )
    assert agent.to_json()["agent_name"] == "cursor"
    assert set(m.Agent.model_fields) == {"id", "agent_name", "agent_version", "model", "session"}


def test_unsigned_record_is_valid_shape() -> None:
    record = run_record_example()
    record.pop("signature")
    assert schema_ok("run_record", record)
    assert model_ok("run_record", record)
    assert "signature" not in m.RunRecord.model_validate(record).to_json()


def test_nullable_fields_are_emitted_as_null() -> None:
    summary = load("run_summary")
    summary["record_path"] = None
    dumped = m.RunSummary.model_validate(summary).to_json()
    assert dumped["record_path"] is None
    assert schema_ok("run_summary", dumped)


def test_flake_record_model_matches_schema() -> None:
    record = {
        "schema_version": "1.0.0",
        "test_id": "tests/test_x.py::test_y",
        "status": "suspected",
        "first_seen": "2026-11-09T12:00:00Z",
        "last_seen": "2026-11-09T12:05:00Z",
        "last_code_hash": "d" * 64,
        "pass_count": 2,
        "fail_count": 1,
        "flip_count": 2,
        "window": {"days": 30, "runs": 3},
        "updated_at": "2026-11-09T12:05:00Z",
    }
    assert schema_ok("flake_record", record)
    assert m.FlakeRecord.model_validate(record).to_json() == record
    record["window"]["days"] = 0
    assert not schema_ok("flake_record", record)
    assert not model_ok("flake_record", record)


def test_savings_report_validates() -> None:
    totals = load("savings")["totals"]
    report = {
        "schema_version": "1.0.0",
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-09T00:00:00Z",
        "group_by": "branch",
        "weekly": False,
        "runs": 3,
        "overall_confidence": "low",
        "defaults_used": ["ci_cost_per_minute"],
        "prices_configured": False,
        "headline": "3 runs",
        "totals": totals,
        "groups": [{"key": "main", "runs": 3, "totals": totals, "overall_confidence": "low"}],
    }
    assert schema_ok("savings_report", report)
    assert m.SavingsReport.model_validate(report).to_json() == report
