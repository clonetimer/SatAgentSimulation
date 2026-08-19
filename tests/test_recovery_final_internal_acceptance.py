from __future__ import annotations

from pathlib import Path
import json

from sat_sim.adcs.basilisk_fsw import BasiliskAdcsFswConfig, _bsk_run5_runtime_scope
from sat_sim.capability_registry import get_capability
from sat_sim.form_schema import capability_form_schema
from sat_sim.internal_capability_acceptance import ACCEPTED_BSK_RUNTIME_VERSIONS, evaluate_trace_quality
from sat_sim.spacecraft.basilisk_6dof import Basilisk6DofConfig, _bsk_run6_runtime_scope
from sat_sim.task_models import to_runtime_task_spec
from sat_sim.unified_agent import normalize_form_task_spec

ROOT = Path(__file__).resolve().parents[1]


def _runtime_default(capability_id: str) -> dict:
    form = capability_form_schema(capability_id)["default_form"]
    return to_runtime_task_spec(normalize_form_task_spec(form, task_id=f"test_{capability_id.replace('.', '_')}"))


def test_basilisk_default_forms_select_real_native_runtime_scope() -> None:
    for capability_id in (
        "subsystem.adcs_basilisk_fsw.v1",
        "whole_spacecraft.basilisk_6dof.v1",
        "whole_spacecraft.bsksim_foundation.v1",
    ):
        form = capability_form_schema(capability_id)["default_form"]
        assert form["simulation"]["backend"] == "basilisk"
        assert form["simulation"]["duration_s"] == 60.0
        assert form["simulation"]["sample_s"] == 1.0
        assert form["simulation"]["step_s"] == 0.1

    adcs = _runtime_default("subsystem.adcs_basilisk_fsw.v1")
    assert adcs["parameters"]["target_mode"] == "inertial"
    assert adcs["parameters"]["fsw_step_s"] == 0.5
    assert _bsk_run5_runtime_scope(BasiliskAdcsFswConfig.from_task_spec(adcs))[0] is True

    six_dof = _runtime_default("whole_spacecraft.basilisk_6dof.v1")
    assert six_dof["parameters"]["target_mode"] == "inertial"
    assert six_dof["parameters"]["fsw_step_s"] == 0.5
    six_dof_config = Basilisk6DofConfig.from_task_spec(six_dof)
    assert six_dof_config.orbit_config.force_models.spherical_harmonics.enabled is False
    assert _bsk_run6_runtime_scope(six_dof_config)[0] is True


def test_configuration_only_modes_are_not_misreported_as_native_runtime() -> None:
    adcs = _runtime_default("subsystem.adcs_basilisk_fsw.v1")
    adcs["parameters"]["target_mode"] = "nadir"
    supported, reason = _bsk_run5_runtime_scope(BasiliskAdcsFswConfig.from_task_spec(adcs))
    assert supported is False
    assert "guidance_mode=nadir" in reason

    six_dof = _runtime_default("whole_spacecraft.basilisk_6dof.v1")
    six_dof["parameters"]["target_mode"] = "nadir"
    supported, reason = _bsk_run6_runtime_scope(Basilisk6DofConfig.from_task_spec(six_dof))
    assert supported is False
    assert "target_mode=nadir" in reason


def test_capability_dependency_identity_matches_project_lock() -> None:
    for capability_id in (
        "subsystem.adcs_basilisk_fsw.v1",
        "whole_spacecraft.basilisk_6dof.v1",
        "whole_spacecraft.bsksim_foundation.v1",
    ):
        contract = get_capability(capability_id)
        implementation = contract.data["implementation"]
        assert implementation["optional_dependency"] == "bsk==2.11.0+satfix1"
        assert implementation["uses_legacy_runner"] is False


def test_native_dynamics_priorities_put_rw_effector_before_spacecraft() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = {
        "src/sat_sim/adcs/basilisk_fsw.py": (
            "AddModelToTask(dyn_task, sc_object, 1)",
            "AddModelToTask(dyn_task, rw_effector, 2)",
        ),
        "src/sat_sim/spacecraft/basilisk_6dof.py": (
            "AddModelToTask(dyn_task, sc_object, 1)",
            "AddModelToTask(dyn_task, rw_effector, 2)",
        ),
        "src/sat_sim/bsk_engine/scenario_base.py": (
            'AddModelToTask("DynamicsTask", sc, 1)',
            'AddModelToTask("DynamicsTask", rw_effector, 2)',
        ),
    }
    for relative, markers in expected.items():
        source = (root / relative).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source


def test_trace_quality_distinguishes_report_only_and_native_telemetry() -> None:
    reference = evaluate_trace_quality(
        "reference.public_satellite_case.v1",
        ({"case_id": "public_case", "source_count": 2, "flight_validated": False},),
    )
    assert reference.passed is True
    assert reference.time_axis_required is False

    adcs = evaluate_trace_quality(
        "subsystem.adcs_basilisk_fsw.v1",
        (
            {
                "time_s": 0.0,
                "attitude.sigma_bn_norm": 0.1,
                "attitude.omega_bn_b_norm_rad_s": 0.01,
                "control.cmd_torque_b_norm_nm": 0.02,
                "rw.motor_torque_norm_nm": 0.02,
                "rw.speed_rad_s_max_abs": 10.0,
            },
            {
                "time_s": 1.0,
                "attitude.sigma_bn_norm": 0.05,
                "attitude.omega_bn_b_norm_rad_s": 0.005,
                "control.cmd_torque_b_norm_nm": 0.01,
                "rw.motor_torque_norm_nm": 0.01,
                "rw.speed_rad_s_max_abs": 10.5,
            },
        ),
    )
    assert adcs.passed is True

    invalid = evaluate_trace_quality(
        "subsystem.adcs_basilisk_fsw.v1",
        ({"time_s": 0.0, "attitude.sigma_bn_norm": float("nan")},),
    )
    assert invalid.passed is False
    assert invalid.finite_numeric_values is False
    assert invalid.missing_required_groups


def test_runtime_policy_accepts_official_and_satfix1_without_offline_bundle() -> None:
    assert ACCEPTED_BSK_RUNTIME_VERSIONS == {"2.11.0", "2.11.0+satfix1"}
    manifest = json.loads((ROOT / "third_party" / "THIRD_PARTY_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS_MANDATORY_ASSETS_EXTERNAL_BSK_POLICY"
    assert manifest["runtime_dependency_mode"] == "external_python_environment"
    optional_bsk = [
        row for row in manifest["files"]
        if row["path"].endswith(".whl") or row["path"].endswith("bsk_2.11.0_satfix1_build_report.json")
    ]
    assert optional_bsk
    assert all(row["required"] is False for row in optional_bsk)


def test_runtime_identity_gate_covers_all_native_modules_used_by_six_capabilities() -> None:
    from sat_sim.internal_capability_acceptance import basilisk_runtime_evidence

    imports = basilisk_runtime_evidence().required_module_imports
    for module in (
        "Basilisk.simulation.extForceTorque",
        "Basilisk.utilities.macros",
        "Basilisk.utilities.orbitalMotion",
        "Basilisk.utilities.simIncludeGravBody",
        "Basilisk.utilities.simIncludeRW",
    ):
        assert module in imports


def test_native_runtime_source_preserves_raw_messages_and_real_effectors() -> None:
    adcs = (ROOT / "src/sat_sim/adcs/basilisk_fsw.py").read_text(encoding="utf-8")
    six_dof = (ROOT / "src/sat_sim/spacecraft/basilisk_6dof.py").read_text(encoding="utf-8")
    foundation = (ROOT / "src/sat_sim/bsk_engine/scenario_base.py").read_text(encoding="utf-8")

    assert 'controller.Ki = -1.0' in adcs
    assert '"telemetry_policy": "raw_native_message_values_no_posthoc_clipping"' in adcs
    assert 'motor_rec.motorTorque[idx]' in adcs

    assert 'external_torque = extForceTorque.ExtForceTorque()' in six_dof
    assert 'sc_object.addDynamicEffector(external_torque)' in six_dof
    assert 'AddModelToTask(dyn_task, external_torque, 3)' in six_dof
    assert '"telemetry_policy": "raw_native_message_values_no_posthoc_clipping"' in six_dof

    assert 'controller.Ki = -1.0' in foundation
    assert 'runtime_truth_status' in foundation
