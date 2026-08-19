from __future__ import annotations

from sat_sim.capability_planner import plan_capability_for_request
from sat_sim.nl_rejection_matrix import REJECTION_CASES
from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent


def test_rejection_matrix_covers_required_categories() -> None:
    assert {
        case.category for case in REJECTION_CASES
    } >= {
        "ambiguity",
        "unknown_capability",
        "unknown_effect",
        "overclaim",
        "prompt_injection",
        "arbitrary_code",
        "out_of_scope",
    }


def test_every_rejection_case_fails_closed_in_planner() -> None:
    failures = {}
    for case in REJECTION_CASES:
        plan = plan_capability_for_request(case.request_text)
        codes = {
            item.reason_code
            for item in plan.unsupported_requirements
            if item.reason_code
        }
        if plan.supported or not (set(case.expected_reason_codes) & codes):
            failures[case.case_id] = {
                "supported": plan.supported,
                "codes": sorted(codes),
            }

    assert failures == {}


def test_subsystem_boundary_returns_structured_rejection_envelope(tmp_path) -> None:
    case = next(item for item in REJECTION_CASES if item.case_id == "finite_element_thermal")
    result = run_unified_agent(UnifiedAgentRequest(
        input_kind="natural_language",
        request_text=case.request_text,
        output_dir=tmp_path,
        backend="template",
        compile_if_valid=True,
    ))

    assert result.ok is False
    assert result.compiled is None
    assert result.task_spec["simulation"]["level"] == "component"
    assert result.task_spec["metadata"]["planner"]["supported"] is False
