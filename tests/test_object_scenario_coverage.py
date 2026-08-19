from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from sat_sim.object_scenario_coverage import audit_object_scenario_coverage, render_coverage_markdown

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_scope_and_scenario_baselines_are_schema_valid_and_exact() -> None:
    scope = _read("configs/acceptance/object_scope.json")
    scenarios = _read("configs/acceptance/scenario_baseline.json")
    scope_schema = _read("src/sat_sim/schemas/object_scope.schema.json")
    scenario_schema = _read("src/sat_sim/schemas/scenario_baseline.schema.json")
    Draft202012Validator(scope_schema).validate(scope)
    Draft202012Validator(scenario_schema).validate(scenarios)
    assert len(scope["objects"]) == 31
    assert len({item["object_id"] for item in scope["objects"]}) == 31
    assert {item["object_id"] for item in scope["objects"]} == {
        item["object_id"] for item in scenarios["objects"]
    }
    assert all(
        item["scenarios"][mode]
        for item in scenarios["objects"]
        for mode in ("nominal", "fault", "degradation")
    )


def test_coverage_audit_passes_approved_engineering_baseline_without_overclaim() -> None:
    report = audit_object_scenario_coverage()
    assert report["technical_baseline_status"] == "PASS"
    assert report["gate_status"] == "PASS"
    assert report["counts"]["component"] == {
        "total": 24,
        "primary_active": 24,
        "nominal": 24,
        "fault": 24,
        "degradation": 24,
        "all_three_modes": 24,
    }
    assert report["counts"]["subsystem"]["total"] == 6
    assert report["counts"]["whole_spacecraft"]["all_three_modes"] == 1
    assert report["approval"]["reviewer"] == "project_owner"
    assert report["blockers"] == []


def test_coverage_markdown_contains_gate_and_all_objects() -> None:
    report = audit_object_scenario_coverage()
    text = render_coverage_markdown(report)
    assert "G0_SCOPE_BASELINE" in text
    assert "Gate：`G0_SCOPE_BASELINE` = **PASS**" in text
    assert "不代表真实硬件" in text
    assert sum(line.startswith("| `component.") for line in text.splitlines()) == 24
