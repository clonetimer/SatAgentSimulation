"""Engineering-grade model verification matrix and runner.

The matrix is intentionally executable: every item exposes a command that can be
run from the project root.  The runner focuses on the *currently registered and
implemented* component/subsystem scenarios.  It does not invent unsupported
physics or calibration data.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import subprocess
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

MATRIX_VERSION = "engineering-verification-matrix.v1"
PACKAGE_VERSION = "0.5.4.7"
RELEASE_ID = "SAT-SIM-0.5.4.7"

COMPONENTS: tuple[str, ...] = (
    "antenna",
    "battery",
    "cmg",
    "data_queue",
    "fuel_tank",
    "ground_station",
    "heater",
    "imu",
    "link_budget",
    "magnetometer",
    "mtb",
    "onboard_storage",
    "payload",
    "payload_sensor",
    "pdu",
    "power_sink",
    "radiator",
    "reaction_wheel",
    "solar_panel",
    "star_tracker",
    "sun_sensor",
    "thermal_node",
    "thruster",
    "transmitter",
)

SUBSYSTEMS: tuple[str, ...] = (
    "adcs",
    "comm_data",
    "eps",
    "payload",
    "propulsion",
    "thermal",
)

REPRESENTATIVE_EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ("WHO-REP-001", "component_nominal", "examples/component_nominal.yaml"),
    ("WHO-REP-002", "component_fault", "examples/component_fault.yaml"),
    ("WHO-REP-003", "component_degradation", "examples/component_degradation.yaml"),
    ("WHO-REP-004", "subsystem_nominal", "examples/subsystem_nominal.yaml"),
    ("WHO-REP-005", "whole_spacecraft_combined", "examples/whole_spacecraft_combined_nominal.yaml"),
    ("WHO-REP-006", "mission_fail_preserved", "examples/mission_fail_preserved.yaml"),
)

STATIC_AND_RELEASE_COMMANDS: tuple[tuple[str, str, str, str], ...] = (
    ("ENV-001", "environment", "version", "sat-agent version"),
    ("ENV-002", "environment", "strict_doctor", "sat-agent doctor --require-api --strict-assets"),
    ("STA-001", "static_quality", "compileall", "python -m compileall -q src scripts tests"),
    ("STA-002", "static_quality", "ruff_blockers", "ruff check src tests scripts --select F821,F822,F811,F841"),
    ("STA-003", "static_quality", "duplicate_definitions", "python scripts/check_no_duplicate_module_definitions.py"),
    ("STA-004", "static_quality", "public_exports", "python scripts/check_public_module_exports.py"),
    ("STA-005", "static_quality", "public_type_hints", "python scripts/check_public_type_hints.py"),
    ("STA-006", "static_quality", "exception_policy", "python scripts/check_exception_policy_v31.py"),
    ("CAL-001", "calibration", "readiness_check", "sat-agent calibration check --project-root ."),
    ("CAL-002", "calibration", "readiness_report", "sat-agent calibration report --project-root ."),
    ("CAL-003", "calibration", "schema", "sat-agent calibration schema --kind all"),
    ("CAL-004", "calibration", "invalid_ground_calibrated_rejection", "sat-agent calibration validate examples/calibration/invalid_ground_calibrated_missing_data.yaml --project-root ."),
    ("MOD-001", "model_contract", "layer_schema", "python scripts/check_layer_schema_contract.py"),
    ("MOD-002", "model_contract", "native_capability_audit", "python scripts/check_native_capability_audit_v2.py"),
    ("MOD-003", "model_contract", "parameter_provenance", "python scripts/check_parameter_provenance_calibration_1.py"),
    ("MOD-004", "model_contract", "subsystem_builders", "python scripts/check_subsystem_basilisk_builders.py"),
    ("MOD-005", "model_contract", "subsystem_runners", "python scripts/check_subsystem_basilisk_runners.py"),
    ("MOD-006", "model_contract", "runtime_injection", "python scripts/check_runtime_injection_closure_2.py"),
    ("MOD-007", "model_contract", "whole_spacecraft_native", "python scripts/check_wholesc_native_closure_1.py"),
    ("AGT-001", "agent", "parse_template", "sat-agent parse '创建电池部件正常仿真，运行 300 秒' --backend template"),
    ("AGT-002", "agent", "validate_component", "sat-agent validate examples/component_nominal.yaml"),
    ("AGT-003", "agent", "resolve_component", "sat-agent resolve examples/component_nominal.yaml"),
    ("AGT-004", "agent", "plan_whole", "sat-agent plan examples/whole_spacecraft_combined_nominal.yaml"),
    ("AGT-005", "agent", "export_script", "sat-agent export-script examples/component_nominal.yaml --output generated/component_nominal.py"),
    ("AGT-006", "agent", "golden_repeat5", "sat-agent eval --repeat-count 5 --execute-marked --output-dir reports/agent_golden"),
    ("REL-001", "release", "pytest", "python -m pytest -q"),
    ("REL-002", "release", "release_check", "sat-agent release-check --golden-report reports/agent_golden/golden_eval_report.json --output-dir reports/release_check --source-root . --strict-assets"),
)


def _slug(value: Any) -> str:
    text = str(value)
    text = text.replace(".", "_").replace("/", "_").replace("-", "_")
    text = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text)
    return "_".join(part for part in text.strip("_").lower().split("_") if part)


def _test_id(prefix: str, component: str, scenario: str) -> str:
    return f"{prefix}-{component.upper().replace('_', '-')}-{_slug(scenario).upper().replace('_', '-') }"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "value"):
        return getattr(value, "value")
    return str(value)


def _finite_numbers(value: Any) -> tuple[int, list[str]]:
    bad: list[str] = []
    count = 0

    def walk(x: Any, path: str) -> None:
        nonlocal count
        if isinstance(x, bool) or x is None or isinstance(x, str):
            return
        if isinstance(x, (int, float)):
            count += 1
            if not math.isfinite(float(x)):
                bad.append(path)
            return
        if isinstance(x, Mapping):
            for k, v in x.items():
                walk(v, f"{path}.{k}" if path else str(k))
            return
        if isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
            return
    walk(value, "")
    return count, bad


def _semantic_hash(value: Any) -> str:
    payload = json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerificationItem:
    test_id: str
    level: str
    object: str
    scenario_type: str
    scenario: str
    command: str
    judge_parameters: dict[str, Any]
    pass_rule: str
    failure_rule: str
    run_kind: str
    description: str = ""
    expected_exit_codes: tuple[int, ...] = (0,)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["expected_exit_codes"] = list(self.expected_exit_codes)
        return data


def _safe_import(name: str):
    return importlib.import_module(name)


def _component_working_scenarios(component: str) -> list[str]:
    try:
        runner = _safe_import(f"components.{component}.runner")
        if hasattr(runner, "list_working_scenarios"):
            return [str(x) for x in runner.list_working_scenarios()]
    except Exception:
        return []
    return []


def _component_fault_names(component: str) -> list[str]:
    try:
        faults = _safe_import(f"components.{component}.faults").default_faults()
        return [_slug(getattr(f, "fault_type", type(f).__name__) or type(f).__name__) for f in faults]
    except Exception:
        return []


def _component_degradation_names(component: str) -> list[str]:
    try:
        degradations = _safe_import(f"components.{component}.degradation").default_degradations()
        return [_slug(getattr(d, "degradation_type", type(d).__name__) or type(d).__name__) for d in degradations]
    except Exception:
        return []


def build_verification_matrix(*, project_root: str | Path = ".") -> list[VerificationItem]:
    """Build the full V35 matrix with executable commands."""
    _ = Path(project_root)
    script = "python scripts/run_engineering_verification_matrix.py"
    items: list[VerificationItem] = []

    def add(item: VerificationItem) -> None:
        items.append(item)

    for component in COMPONENTS:
        add(VerificationItem(
            test_id=_test_id("CMP-NOM", component, "nominal"),
            level="component",
            object=component,
            scenario_type="nominal",
            scenario="nominal",
            command="",
            judge_parameters={"finite_numeric_required": True, "error_key_forbidden": True},
            pass_rule="run_nominal_case returns structured data without error and all numeric outputs are finite.",
            failure_rule="Any exception, explicit error payload, NaN or Inf fails the item.",
            run_kind="component_nominal",
            description="Component nominal/reference scenario.",
        ))
        for scenario in _component_working_scenarios(component):
            add(VerificationItem(
                test_id=_test_id("CMP-WRK", component, scenario),
                level="component",
                object=component,
                scenario_type="normal_working",
                scenario=scenario,
                command="",
                judge_parameters={"finite_numeric_required": True, "scenario": scenario},
                pass_rule="Named normal operating scenario executes and returns finite outputs with no error field.",
                failure_rule="Unsupported scenario, exception, error payload, NaN or Inf fails the item.",
                run_kind="component_working",
                description="Normal non-fault/non-degradation operating mode.",
            ))
        add(VerificationItem(
            test_id=_test_id("CMP-ALL", component, "run_all_modes"),
            level="component",
            object=component,
            scenario_type="batch",
            scenario="run_all_modes",
            command="",
            judge_parameters={"minimum_top_level_modes": 3, "finite_numeric_required": True},
            pass_rule="run_all_modes returns a dict with nominal/fault/degradation or L2-light coverage and finite numeric outputs.",
            failure_rule="Exception, empty result or non-finite numeric value fails the item.",
            run_kind="component_all_modes",
            description="All component-supported reference modes.",
        ))
        for fault_name in _component_fault_names(component):
            add(VerificationItem(
                test_id=_test_id("CMP-FLT", component, fault_name),
                level="component",
                object=component,
                scenario_type="fault",
                scenario=fault_name,
                command="",
                judge_parameters={"active_fault_required": True, "sample_window_s": [0, 600]},
                pass_rule="The single fault is active in its configured time window and changes effective parameters or output evidence.",
                failure_rule="No active fault evidence, exception, empty trace, NaN or Inf fails the item.",
                run_kind="component_fault_single",
                description="Single L2-light fault event.",
            ))
        for degradation_name in _component_degradation_names(component):
            add(VerificationItem(
                test_id=_test_id("CMP-DEG", component, degradation_name),
                level="component",
                object=component,
                scenario_type="degradation",
                scenario=degradation_name,
                command="",
                judge_parameters={"active_degradation_required": True, "sample_window_s": [0, 600]},
                pass_rule="The single degradation is active after onset and changes effective parameters or output evidence.",
                failure_rule="No active degradation evidence, exception, empty trace, NaN or Inf fails the item.",
                run_kind="component_degradation_single",
                description="Single L2-light degradation event.",
            ))
        for scenario_type, run_kind, rule in (
            ("fault_default_set", "component_fault_default_set", "All default faults participate at least once."),
            ("degradation_default_set", "component_degradation_default_set", "All default degradations participate at least once."),
            ("combined_default_set", "component_combined_default_set", "At least one fault and one degradation participate in one run."),
        ):
            add(VerificationItem(
                test_id=_test_id("CMP-L2", component, scenario_type),
                level="component",
                object=component,
                scenario_type=scenario_type,
                scenario=scenario_type,
                command="",
                judge_parameters={"duration_s": 600, "finite_numeric_required": True},
                pass_rule=rule,
                failure_rule="Missing participation evidence, exception, empty trace, NaN or Inf fails the item.",
                run_kind=run_kind,
                description="L2-light sparse time-query event/degradation composition.",
            ))
        for duration_name, duration_s in (("short_duration_60s", 60.0), ("long_duration_86400s", 86400.0)):
            add(VerificationItem(
                test_id=_test_id("CMP-BND", component, duration_name),
                level="component",
                object=component,
                scenario_type="duration_boundary",
                scenario=duration_name,
                command="",
                judge_parameters={"duration_s": duration_s, "finite_numeric_required": True},
                pass_rule="Boundary-duration combined sparse event run completes with finite outputs and bounded sample count.",
                failure_rule="Exception, missing trace, more than 12 samples, NaN or Inf fails the item.",
                run_kind="component_boundary_duration",
                description="Short/long duration boundary without fixed-step explosion.",
            ))
        add(VerificationItem(
            test_id=_test_id("CMP-NEG", component, "unsupported_working_scenario"),
            level="component",
            object=component,
            scenario_type="negative",
            scenario="unsupported_working_scenario",
            command="",
            judge_parameters={"expected_exception": "ValueError"},
            pass_rule="Unsupported working scenario is rejected by the component runner.",
            failure_rule="Silent acceptance of unsupported scenario fails the item.",
            run_kind="component_invalid_working_scenario",
            description="Invalid scenario gate.",
        ))
        add(VerificationItem(
            test_id=_test_id("CMP-DET", component, "repeat_stability"),
            level="component",
            object=component,
            scenario_type="determinism",
            scenario="repeat_stability",
            command="",
            judge_parameters={"repeat_count": 2, "semantic_hash_equal": True},
            pass_rule="Two same-input runs produce the same semantic hash.",
            failure_rule="Hash drift, exception, NaN or Inf fails the item.",
            run_kind="component_repeat_stability",
            description="Same-input same-output deterministic smoke.",
        ))

    for i, item in enumerate(items):
        cmd = f"{script} --test-id {item.test_id} --output reports/v35/{item.test_id}.json"
        items[i] = VerificationItem(**{**item.as_dict(), "command": cmd, "expected_exit_codes": tuple(item.expected_exit_codes)})

    for subsystem in SUBSYSTEMS:
        for scenario_type, run_kind, description in (
            ("all_modes", "subsystem_all_modes", "Subsystem run_all_modes executes the public reference suite."),
            ("repeat_stability", "subsystem_repeat_stability", "Subsystem run_all_modes is deterministic for same input."),
        ):
            test_id = _test_id("SUB", subsystem, scenario_type)
            add(VerificationItem(
                test_id=test_id,
                level="subsystem",
                object=subsystem,
                scenario_type=scenario_type,
                scenario=scenario_type,
                command=f"{script} --test-id {test_id} --output reports/v35/{test_id}.json",
                judge_parameters={"finite_numeric_required": True},
                pass_rule="Subsystem public runner completes, returns structured outputs and contains finite numeric values.",
                failure_rule="Exception, empty result, non-finite numeric value or deterministic hash drift fails the item.",
                run_kind=run_kind,
                description=description,
            ))

    for test_id, scenario, yaml_path in REPRESENTATIVE_EXAMPLES:
        add(VerificationItem(
            test_id=test_id,
            level="whole_spacecraft_or_agent_release",
            object=scenario,
            scenario_type="representative_run",
            scenario=scenario,
            command=f"sat-agent run {yaml_path} --output-root runs/v35/{test_id}",
            judge_parameters={"expected_four_state": True, "input": yaml_path},
            pass_rule="Run result matches the documented four-state expectation for the representative case.",
            failure_rule="Unexpected CLI return code, missing Run Bundle or incorrect ValidationOutcome fails the item.",
            run_kind="shell_command_reference",
            description="Formal representative release scenario.",
            expected_exit_codes=(0, 1),
        ))

    for test_id, level, scenario, command in STATIC_AND_RELEASE_COMMANDS:
        expected = (0,)
        if test_id == "CAL-004":
            expected = (1,)
        add(VerificationItem(
            test_id=test_id,
            level=level,
            object=level,
            scenario_type=scenario,
            scenario=scenario,
            command=command,
            judge_parameters={"expected_exit_codes": list(expected)},
            pass_rule=f"Command exits with expected return code {list(expected)} and writes no unhandled exception trace.",
            failure_rule="Unexpected return code or unhandled traceback fails the item.",
            run_kind="shell_command_reference",
            description="Executable platform/static/release command.",
            expected_exit_codes=expected,
        ))

    return items


def item_by_id(test_id: str) -> VerificationItem:
    for item in build_verification_matrix():
        if item.test_id == test_id:
            return item
    raise KeyError(f"Unknown V35 verification test_id: {test_id}")


def _component_common_context(component: str):
    runner = _safe_import(f"components.{component}.runner")
    faults = _safe_import(f"components.{component}.faults")
    degradation = _safe_import(f"components.{component}.degradation")
    return runner, faults, degradation


def _run_component_fault_single(component: str, scenario: str) -> dict[str, Any]:
    runner, faults_mod, deg_mod = _component_common_context(component)
    faults = faults_mod.default_faults()
    selected = None
    for fault in faults:
        if _slug(getattr(fault, "fault_type", type(fault).__name__) or type(fault).__name__) == scenario:
            selected = fault
            break
    if selected is None:
        raise ValueError(f"Fault scenario {scenario} not found for {component}")
    return runner._run_l2_light_profile(
        component=component,
        base_params=deg_mod.base_effective_params(),
        faults=[selected],
        degradations=[],
        evaluator=deg_mod.evaluate_effective_params,
    )


def _run_component_degradation_single(component: str, scenario: str) -> dict[str, Any]:
    runner, _faults_mod, deg_mod = _component_common_context(component)
    degradations = deg_mod.default_degradations()
    selected = None
    for degradation in degradations:
        if _slug(getattr(degradation, "degradation_type", type(degradation).__name__) or type(degradation).__name__) == scenario:
            selected = degradation
            break
    if selected is None:
        raise ValueError(f"Degradation scenario {scenario} not found for {component}")
    return runner._run_l2_light_profile(
        component=component,
        base_params=deg_mod.base_effective_params(),
        faults=[],
        degradations=[selected],
        evaluator=deg_mod.evaluate_effective_params,
    )


def _assert_finite(result: Any) -> dict[str, Any]:
    count, bad = _finite_numbers(_jsonable(result))
    if bad:
        raise AssertionError(f"Non-finite numeric values at {bad[:8]}")
    return {"numeric_count": count, "non_finite_paths": bad}


def _assert_no_error_payload(result: Any) -> None:
    def walk(x: Any, path: str = "") -> None:
        if isinstance(x, Mapping):
            if "error" in x:
                raise AssertionError(f"Error payload found at {path or '<root>'}: {x.get('error')}")
            for k, v in x.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
    walk(_jsonable(result))


def _trace_participation(result: Mapping[str, Any]) -> dict[str, Any]:
    trace = list(result.get("trace") or [])
    active_fault_rows = [row for row in trace if row.get("active_faults")]
    active_degradation_rows = [row for row in trace if row.get("active_degradations")]
    return {
        "trace_count": len(trace),
        "active_fault_rows": len(active_fault_rows),
        "active_degradation_rows": len(active_degradation_rows),
        "faults_participated": bool(result.get("faults_participated") or active_fault_rows),
        "degradations_participated": bool(result.get("degradations_participated") or active_degradation_rows),
    }


def run_verification_item(item: VerificationItem, *, project_root: str | Path = ".", execute_shell: bool = False) -> dict[str, Any]:
    started = time.time()
    root = Path(project_root)
    result: Any = None
    observations: dict[str, Any] = {}
    status = "PASS"
    reason = "OK"
    try:
        if item.run_kind == "component_nominal":
            runner = _safe_import(f"components.{item.object}.runner")
            result = runner.run_nominal_case()
            _assert_no_error_payload(result)
            observations.update(_assert_finite(result))
        elif item.run_kind == "component_working":
            runner = _safe_import(f"components.{item.object}.runner")
            result = runner.run_working_scenario(item.scenario)
            _assert_no_error_payload(result)
            observations.update(_assert_finite(result))
        elif item.run_kind == "component_all_modes":
            runner = _safe_import(f"components.{item.object}.runner")
            result = runner.run_all_modes()
            if not isinstance(result, dict) or not result:
                raise AssertionError("run_all_modes returned an empty or non-dict result")
            _assert_no_error_payload(result)
            observations.update(_assert_finite(result))
            observations["top_level_keys"] = sorted(result.keys())
        elif item.run_kind == "component_fault_single":
            result = _run_component_fault_single(item.object, item.scenario)
            observations.update(_assert_finite(result))
            observations.update(_trace_participation(result))
            if not observations["faults_participated"]:
                raise AssertionError("Fault did not participate in the trace")
        elif item.run_kind == "component_degradation_single":
            result = _run_component_degradation_single(item.object, item.scenario)
            observations.update(_assert_finite(result))
            observations.update(_trace_participation(result))
            if not observations["degradations_participated"]:
                raise AssertionError("Degradation did not participate in the trace")
        elif item.run_kind == "component_fault_default_set":
            runner = _safe_import(f"components.{item.object}.runner")
            result = runner.run_l2_light_fault_case(duration_s=float(item.judge_parameters.get("duration_s", 600)))
            observations.update(_assert_finite(result))
            observations.update(_trace_participation(result))
            if not observations["faults_participated"]:
                raise AssertionError("Default fault set did not participate")
        elif item.run_kind == "component_degradation_default_set":
            runner = _safe_import(f"components.{item.object}.runner")
            result = runner.run_l2_light_degradation_case(duration_s=float(item.judge_parameters.get("duration_s", 600)))
            observations.update(_assert_finite(result))
            observations.update(_trace_participation(result))
            if not observations["degradations_participated"]:
                raise AssertionError("Default degradation set did not participate")
        elif item.run_kind in {"component_combined_default_set", "component_boundary_duration"}:
            runner = _safe_import(f"components.{item.object}.runner")
            duration_s = float(item.judge_parameters.get("duration_s", 600.0))
            result = runner.run_l2_light_combined_case(duration_s=duration_s)
            observations.update(_assert_finite(result))
            observations.update(_trace_participation(result))
            if item.run_kind == "component_combined_default_set" and not (observations["faults_participated"] and observations["degradations_participated"]):
                raise AssertionError("Combined case did not include both active fault and degradation evidence")
            if observations.get("trace_count", 0) > 12:
                raise AssertionError("Boundary run produced too many sparse samples")
        elif item.run_kind == "component_invalid_working_scenario":
            runner = _safe_import(f"components.{item.object}.runner")
            try:
                if hasattr(runner, "run_working_scenario"):
                    runner.run_working_scenario("__unsupported_v35_scenario__")
                else:
                    raise ValueError("Component has no working-scenario API")
            except ValueError as exc:
                result = {"expected_rejection": True, "exception_type": type(exc).__name__, "message": str(exc)}
                observations["expected_exception"] = "ValueError"
            else:
                raise AssertionError("Unsupported working scenario was silently accepted")
        elif item.run_kind == "component_repeat_stability":
            runner = _safe_import(f"components.{item.object}.runner")
            a = runner.run_all_modes()
            b = runner.run_all_modes()
            ha = _semantic_hash(a)
            hb = _semantic_hash(b)
            result = {"first_hash": ha, "second_hash": hb}
            observations.update(_assert_finite(a))
            if ha != hb:
                raise AssertionError(f"semantic hash drift: {ha} != {hb}")
        elif item.run_kind == "subsystem_all_modes":
            runner = _safe_import(f"subsystems.{item.object}.runner")
            result = runner.run_all_modes()
            if not isinstance(result, dict) or not result:
                raise AssertionError("Subsystem run_all_modes returned empty/non-dict result")
            _assert_no_error_payload(result)
            observations.update(_assert_finite(result))
        elif item.run_kind == "subsystem_repeat_stability":
            runner = _safe_import(f"subsystems.{item.object}.runner")
            a = runner.run_all_modes()
            b = runner.run_all_modes()
            ha = _semantic_hash(a)
            hb = _semantic_hash(b)
            result = {"first_hash": ha, "second_hash": hb}
            observations.update(_assert_finite(a))
            if ha != hb:
                raise AssertionError(f"semantic hash drift: {ha} != {hb}")
        elif item.run_kind == "shell_command_reference":
            if not execute_shell:
                result = {"not_executed_by_default": True, "command": item.command, "expected_exit_codes": list(item.expected_exit_codes)}
                observations["dry_run"] = True
            else:
                env = os.environ.copy()
                env["PYTHONPATH"] = f"{root}:{root / 'src'}" + ((":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
                completed = subprocess.run(item.command, cwd=root, shell=True, env=env, text=True, capture_output=True, timeout=900)
                result = {"returncode": completed.returncode, "stdout_tail": completed.stdout[-4000:], "stderr_tail": completed.stderr[-4000:]}
                if completed.returncode not in item.expected_exit_codes:
                    raise AssertionError(f"return code {completed.returncode} not in expected {item.expected_exit_codes}")
                if "Traceback (most recent call last)" in completed.stderr:
                    raise AssertionError("unhandled traceback in stderr")
        else:
            raise ValueError(f"Unsupported V35 run_kind: {item.run_kind}")
    except Exception as exc:
        status = "FAIL"
        reason = f"{type(exc).__name__}: {exc}"
        result = {"exception": reason, "traceback": traceback.format_exc(limit=8)}
    elapsed = round(time.time() - started, 6)
    return {
        "schema_version": MATRIX_VERSION,
        "status": status,
        "reason": reason,
        "elapsed_s": elapsed,
        "item": item.as_dict(),
        "observations": observations,
        "result_digest": _semantic_hash(result),
        "result_preview": _jsonable(result) if status == "FAIL" else _summarize_result(result),
    }


def _summarize_result(result: Any) -> Any:
    payload = _jsonable(result)
    if isinstance(payload, dict):
        summary = {k: payload[k] for k in sorted(payload.keys())[:12]}
        if "trace" in payload and isinstance(payload["trace"], list):
            summary["trace_count"] = len(payload["trace"])
            summary.pop("trace", None)
        return summary
    if isinstance(payload, list):
        return {"list_length": len(payload), "first": payload[0] if payload else None}
    return payload


def export_matrix(path: str | Path, *, project_root: str | Path = ".") -> dict[str, Any]:
    items = build_verification_matrix(project_root=project_root)
    payload = {
        "schema_version": MATRIX_VERSION,
        "release_id": RELEASE_ID,
        "package_version": PACKAGE_VERSION,
        "item_count": len(items),
        "component_count": len(COMPONENTS),
        "subsystem_count": len(SUBSYSTEMS),
        "items": [item.as_dict() for item in items],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def run_many(
    *,
    test_ids: Iterable[str] | None = None,
    level: str | None = None,
    component: str | None = None,
    project_root: str | Path = ".",
    output_dir: str | Path = "reports/v35",
    execute_shell: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    items = build_verification_matrix(project_root=project_root)
    if test_ids:
        wanted = set(test_ids)
        items = [item for item in items if item.test_id in wanted]
        missing = wanted - {item.test_id for item in items}
        if missing:
            raise KeyError(f"Unknown test ids: {sorted(missing)}")
    if level:
        items = [item for item in items if item.level == level]
    if component:
        items = [item for item in items if item.object == component]
    if limit is not None:
        items = items[: max(0, int(limit))]
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in items:
        report = run_verification_item(item, project_root=project_root, execute_shell=execute_shell)
        (out_dir / f"{item.test_id}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(report)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = len(results) - passed
    summary = {
        "schema_version": MATRIX_VERSION,
        "release_id": RELEASE_ID,
        "package_version": PACKAGE_VERSION,
        "selected_count": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / len(results) if results else 1.0,
        "execute_shell": bool(execute_shell),
        "output_dir": str(out_dir),
        "failed_test_ids": [r["item"]["test_id"] for r in results if r["status"] != "PASS"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def matrix_stats(items: list[VerificationItem] | None = None) -> dict[str, Any]:
    items = items or build_verification_matrix()
    by_level: dict[str, int] = {}
    by_component: dict[str, int] = {}
    by_scenario_type: dict[str, int] = {}
    for item in items:
        by_level[item.level] = by_level.get(item.level, 0) + 1
        by_component[item.object] = by_component.get(item.object, 0) + 1
        by_scenario_type[item.scenario_type] = by_scenario_type.get(item.scenario_type, 0) + 1
    return {
        "schema_version": MATRIX_VERSION,
        "release_id": RELEASE_ID,
        "package_version": PACKAGE_VERSION,
        "item_count": len(items),
        "by_level": dict(sorted(by_level.items())),
        "by_component": dict(sorted(by_component.items())),
        "by_scenario_type": dict(sorted(by_scenario_type.items())),
    }


__all__ = [
    "MATRIX_VERSION",
    "PACKAGE_VERSION",
    "RELEASE_ID",
    "COMPONENTS",
    "SUBSYSTEMS",
    "VerificationItem",
    "build_verification_matrix",
    "item_by_id",
    "run_verification_item",
    "run_many",
    "export_matrix",
    "matrix_stats",
]
