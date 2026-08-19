from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from components.antenna.builder import _antenna_state_value
from integration.qoi_profiles import run_pairwise_profiles
from sat_sim.coupling_gate import ADVISORY_PARTIAL_IDS, build_coupling_completeness_gate
from sat_sim.execution_planner import plan_task_spec
from sat_sim.release_closure import RELEASE_ID, RELEASE_VERSION
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.validation_outcome import ExecutionStatus, ValidationResult, evaluate_validation_outcome
from subsystems.comm_data.runner import run_comm_data_rf_native_scenario
from whole_spacecraft.runner import run_whole_spacecraft_native_case
from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

ROOT = Path(__file__).resolve().parents[1]


def _whole_case(*, sigma: tuple[float, float, float], duration_s: float = 40.0):
    return run_whole_spacecraft_native_case(
        WholeSpacecraftRunConfig(
            duration_s=duration_s,
            sample_s=10.0,
            structure=WholeSpacecraftConfig(mission_initial_sigma_bn=sigma),
        )
    )


def test_rf_antenna_state_uses_native_off_rx_tx_rxtx_enum() -> None:
    assert _antenna_state_value("off") == 0
    assert _antenna_state_value("rx") == 1
    assert _antenna_state_value("tx") == 2
    assert _antenna_state_value("rxtx") == 3
    assert _antenna_state_value("rx_tx") == 3
    assert _antenna_state_value("tx", available=False) == 0
    with pytest.raises(ValueError):
        _antenna_state_value("available")


def test_native_rf_chain_has_real_receiver_cnr_without_floor() -> None:
    summary, rows, checks = run_comm_data_rf_native_scenario()
    assert summary.status == "PASS"
    assert summary.spacecraft_state_final == 2  # TX
    assert summary.ground_state_final == 1  # RX
    assert summary.cnr1_final == 0.0
    assert summary.cnr2_final > 1.0
    assert summary.cnr2_final < 1.0e9
    assert summary.native_fspl_db > 100.0
    assert checks["cnr"] is True
    assert all(row.spacecraft_state == 2 and row.ground_state == 1 for row in rows)


def test_eclipse_attitude_solar_chain_changes_generation_and_soc() -> None:
    sun, sun_rows = _whole_case(sigma=(0.0, 0.0, 0.0))
    anti, anti_rows = _whole_case(sigma=(0.0, 1.0, 0.0))

    assert sun.solar_power_coupling_status == "PASS"
    assert anti.solar_power_coupling_status == "PASS"
    assert sun.min_solar_power_w > 90.0
    assert anti.max_solar_power_w == pytest.approx(0.0, abs=1e-12)
    assert sun.final_soc > anti.final_soc + 0.005
    assert sun.solar_power_sample_count > 0
    assert anti.solar_power_sample_count > 0


def test_whole_status_requires_execution_and_mission_success() -> None:
    sun, _ = _whole_case(sigma=(0.0, 0.0, 0.0))
    anti, _ = _whole_case(sigma=(0.0, 1.0, 0.0))

    assert sun.execution_status == "PASS"
    assert sun.mission_status == "PASS"
    assert sun.status == "PASS"
    assert sun.native_rf_cnr_max_linear > 1.0
    assert sun.gated_downlink_cnr_max_linear == pytest.approx(sun.native_rf_cnr_max_linear)
    assert sun.mission_delivered_bits > 0.0

    assert anti.execution_status == "PASS"
    assert anti.mission_status == "FAIL"
    assert anti.status == "FAIL"
    assert anti.gated_downlink_cnr_max_linear == 0.0
    assert anti.mission_delivered_bits == 0.0


def test_validation_outcome_cannot_turn_runner_summary_fail_into_pass() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0562_summary_fail")
    planned = plan_task_spec(spec)
    outcome = evaluate_validation_outcome(
        spec=spec,
        resolved=planned.resolved_spec,
        summary={"status": "FAIL", "execution_status": "PASS", "mission_status": "FAIL"},
        trace_rows=(),
        execution_status=ExecutionStatus.SUCCEEDED,
    )
    assert outcome.result == ValidationResult.FAIL
    assert any(check.reason_code == "SUMMARY_STATUS_FAILED" for check in outcome.checks)


def test_coupling_gate_consumes_qoi_id_and_blocks_unknown_partial() -> None:
    pairwise = run_pairwise_profiles()
    gate = build_coupling_completeness_gate([pairwise])
    actual_partial_ids = {
        row["id"] for row in pairwise["checks"] if row["status"] == "PARTIAL"
    }
    assert actual_partial_ids <= ADVISORY_PARTIAL_IDS
    assert gate["status"] == "PASS_WITH_ADVISORIES"
    assert set(gate["advisory_check_ids"]) == actual_partial_ids
    assert gate["release_blocking_count"] == 0

    unknown = build_coupling_completeness_gate([
        {"checks": [{"id": "new_chain.missing_feedback", "status": "PARTIAL"}]}
    ])
    assert unknown["status"] == "FAIL"
    assert unknown["release_blocking_count"] == 1
    assert unknown["blocking_check_ids"] == ["new_chain.missing_feedback"]


def test_legacy_degradation_wrapper_resolves_authoritative_module() -> None:
    from validation.legacy.whole_spacecraft.degradation_config import WholeSatelliteDegradation
    from whole_spacecraft.degradation import WholeSpacecraftDegradation

    assert WholeSatelliteDegradation is WholeSpacecraftDegradation


def test_final_acceptance_identity_is_current_and_not_hardcoded_to_old_count() -> None:
    from sat_sim import __version__

    assert RELEASE_VERSION == __version__
    assert RELEASE_VERSION in RELEASE_ID
    text = (ROOT / "reports/final_acceptance_report.md").read_text(encoding="utf-8")
    assert RELEASE_VERSION in text
    assert "真实硬件或飞行验证" in text
