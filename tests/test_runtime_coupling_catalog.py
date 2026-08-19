from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from sat_sim import agent_cli
from sat_sim.coupling_validation_suite import (
    _run_spec_isolated,
    validate_one_factor_change,
)


def test_optional_parent_mapping_counts_as_the_declared_one_factor_change() -> None:
    baseline = {"parameters": {}}
    perturbed = {
        "parameters": {
            "coupling": {"enable_comm_native_odh_downlink": False}
        }
    }
    report = validate_one_factor_change(
        baseline,
        perturbed,
        patch_path="parameters.coupling.enable_comm_native_odh_downlink",
    )
    assert report["status"] == "PASS"
    assert report["unexpected_differences"] == []


def test_isolated_runner_streams_to_file_and_requests_private_fast_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class FakeProcess:
        pid = 987654321
        returncode = 0

        def wait(self, timeout: float) -> int:
            observed["wait_timeout"] = timeout
            return 0

    def fake_popen(command, **kwargs):
        observed["command"] = list(command)
        observed["stdout"] = kwargs["stdout"]
        assert kwargs["stdout"] is not None
        assert hasattr(kwargs["stdout"], "write")
        kwargs["stdout"].write("sealed run\n")
        return FakeProcess()

    monkeypatch.setattr("sat_sim.coupling_validation_suite.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "sat_sim.coupling_validation_suite._cleanup_residual_process_group",
        lambda _pgid: None,
    )
    result = _run_spec_isolated(
        tmp_path / "task.yaml",
        output_root=tmp_path / "runs",
        run_id="case",
        timeout_s=12.0,
        log_path=tmp_path / "case.log",
    )
    assert "--isolated-process-exit" in observed["command"]
    assert result["return_code"] == 0
    assert result["timed_out"] is False
    assert (tmp_path / "case.log").read_text(encoding="utf-8") == "sealed run\n"


def test_agent_cli_private_isolated_exit_flag_is_parseable_but_hidden() -> None:
    parser = agent_cli.build_parser()
    args = parser.parse_args([
        "run", "case.yaml", "--isolated-process-exit", "--no-hard-timeout"
    ])
    assert args.isolated_process_exit is True
    assert args.hard_timeout is False
    assert "isolated-process-exit" not in parser.format_help()


def test_composite_capability_declares_all_newly_consumed_comm_parameters() -> None:
    capability = yaml.safe_load(
        Path("src/sat_sim/capabilities/whole_spacecraft.composite_digital_twin.v1.yaml")
        .read_text(encoding="utf-8")
    )
    declared = set(capability["parameters"])
    required = {
        "storage_initial_bits",
        "native_downlink_bit_rate_request_bps",
        "native_downlink_packet_size_bits",
        "native_downlink_max_retransmissions",
        "native_downlink_cnr_linear",
        "native_downlink_distance_m",
        "native_downlink_bandwidth_hz",
        "native_downlink_frequency_hz",
        "comm_power_reference_rate_bps",
        "thermal_use_network",
    }
    assert required <= declared


def test_release_v2_matrix_has_explicit_expected_failure_policies_and_ten_required_couplings() -> None:
    matrix = json.loads(
        Path("configs/verification/whole_spacecraft_coupling_causality_v2.json")
        .read_text(encoding="utf-8")
    )
    assert len(matrix["cases"]) == 11
    assert len(matrix["required_runtime_couplings"]) == 10
    cases = {item["case_id"]: item for item in matrix["cases"]}
    assert "eps_bus_load_to_thermal_heat" in cases
    assert "legacy_transmitter_to_storage_drain" in cases
    assert "eps_pdu_to_comm_activity" in cases["low_soc_to_payload_gate"]["covers_runtime_couplings"]
    for case_id in {
        "low_soc_to_payload_gate",
        "adcs_pointing_to_payload_and_downlink",
        "legacy_transmitter_to_storage_drain",
    }:
        policy = cases[case_id]["perturbed_run_acceptance"]
        assert policy["accepted_return_codes"] == [1]
        assert policy["accepted_execution_statuses"] == ["PASS"]
        assert policy["accepted_numerical_statuses"] == ["PASS"]


def test_runtime_catalog_includes_comm_activity_power_and_heat_bridge() -> None:
    source = Path("src/whole_spacecraft/builder.py").read_text(encoding="utf-8")
    assert 'coupling_matrix["comm_activity_to_eps_thermal"]' in source
    assert "CommPowerBridge -> battery + electronics thermal node" in source


def test_suite_command_flushes_sealed_evidence_then_uses_process_level_exit() -> None:
    source = Path("scripts/run_coupling_causality_suite.py").read_text(encoding="utf-8")
    assert "sys.stdout.flush()" in source
    assert "os._exit(int(exit_code))" in source
