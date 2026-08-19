from __future__ import annotations

from sat_sim.lmstudio_acceptance import _legacy_degradation_effects
from sat_sim.nl_e2e_matrix import generate_nl_e2e_cases


def test_full_nl_e2e_matrix_is_generated_from_exact_scope_and_modes() -> None:
    cases = generate_nl_e2e_cases()
    assert len(cases) == 31 * 3 * 3
    assert len({case.case_id for case in cases}) == len(cases)
    assert {
        case.expected_mode for case in cases
    } == {"nominal", "fault", "degradation"}
    assert sum(case.expected_mode == "nominal" for case in cases) == 31 * 3
    assert sum(bool(case.expected_effects) for case in cases) == 31 * 2 * 3


def test_each_object_has_chinese_english_and_alias_variants() -> None:
    cases = generate_nl_e2e_cases()
    ids = {case.case_id for case in cases}
    for object_id in (
        "whole_spacecraft",
        "subsystem_propulsion",
        "component_battery",
        "component_transmitter",
    ):
        for mode in ("nominal", "fault", "degradation"):
            for variant in ("zh", "en", "alias"):
                assert f"{object_id}__{mode}__{variant}" in ids


def test_english_cases_express_contract_identity_mode_and_effect() -> None:
    cases = generate_nl_e2e_cases(variants=("en",))
    nominal = next(case for case in cases if case.case_id == "whole_spacecraft__nominal__en")
    fault = next(case for case in cases if case.case_id == "whole_spacecraft__fault__en")

    assert "capability_id=whole_spacecraft.unified_native.v1" in nominal.request_text
    assert "mode=nominal" in nominal.request_text
    assert "mode=fault" in fault.request_text
    assert "effect=payload_instrument_off" in fault.request_text


def test_alias_cases_use_human_event_labels_without_machine_effect_ids() -> None:
    cases = generate_nl_e2e_cases(variants=("alias",))
    fault = next(case for case in cases if case.case_id == "whole_spacecraft__fault__alias")

    assert "模拟“载荷仪器关闭（故障）”" in fault.request_text
    assert "payload_instrument_off" not in fault.request_text


def test_runtime_effects_come_from_capability_event_catalogs() -> None:
    cases = generate_nl_e2e_cases(variants=("zh",))
    by_id = {case.case_id: case for case in cases}
    assert by_id["whole_spacecraft__fault__zh"].expected_effects == (
        "payload_instrument_off",
    )
    assert by_id["subsystem_propulsion__degradation__zh"].expected_effects == (
        "performance_degradation",
    )
    assert by_id["component_transmitter__fault__zh"].expected_effects
    for case in cases:
        if case.expected_mode == "nominal":
            assert case.expected_effects == ()
        else:
            assert len(case.expected_effects) == 1


def test_legacy_structured_degradation_leaf_names_are_effects() -> None:
    assert _legacy_degradation_effects({
        "eps": {
            "solar_panel": {
                "efficiency_loss_pct": 20.0,
                "radiation_damage_factor": 0.0,
            }
        }
    }) == {"efficiency_loss_pct", "radiation_damage_factor"}
