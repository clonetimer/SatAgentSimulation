"""Official Basilisk MonteCarloController integration for verified native cases.

TaskSpec remains the universal experiment description and sampling layer.  For
capabilities registered in :mod:`native_controller_registry`, exact sampled
values are translated to Controller initial-condition modifications.  Bridge,
legacy and project-equation capabilities are never silently wrapped as native.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .native_controller_registry import native_controller_contract
from .statistics import summarize_numeric_samples


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _nested_get(payload: Mapping[str, Any], path: str, default: Any = None) -> Any:
    cursor: Any = payload
    for part in path.split("."):
        if not isinstance(cursor, Mapping) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def _capability_id(spec: Mapping[str, Any]) -> str:
    model = _mapping(spec.get("model"))
    return str(model.get("capability_id") or spec.get("capability_id") or "")


@dataclass(frozen=True)
class BasiliskMonteCarloCompatibilityPlan:
    controller: str
    seed: int
    sample_count: int
    dispersions: dict[str, Mapping[str, Any]]
    capability_id: str = ""
    run_retention: str = "official_controller_archive_plus_platform_report"
    eligible: bool = False
    unsupported_paths: tuple[str, ...] = ()
    status: str = "not_eligible"

    def to_dict(self) -> dict[str, Any]:
        contract = native_controller_contract(self.capability_id)
        return {
            "controller": self.controller,
            "seed": self.seed,
            "sample_count": self.sample_count,
            "dispersions": self.dispersions,
            "capability_id": self.capability_id,
            "run_retention": self.run_retention,
            "eligible": self.eligible,
            "unsupported_paths": list(self.unsupported_paths),
            "status": self.status,
            "registered_parameter_paths": list(contract.path_map) if contract else [],
            "case_kind": contract.case_kind if contract else None,
            "boundary": (
                "Verified native Basilisk batches use the official MonteCarloController. "
                "TaskSpec owns schema-backed sampling and per-case seeds; bridge, legacy and "
                "project-equation capabilities remain on the Run-Bundle experiment engine."
            ),
        }


def build_basilisk_mc_plan(
    base_task_spec: Mapping[str, Any],
    dispersions: Mapping[str, Mapping[str, Any]],
    *,
    sample_count: int,
    seed: int,
) -> BasiliskMonteCarloCompatibilityPlan:
    cid = _capability_id(base_task_spec)
    contract = native_controller_contract(cid)
    path_map = contract.path_map if contract else {}
    unsupported = tuple(sorted(path for path in dispersions if path not in path_map))
    eligible = contract is not None and not unsupported
    return BasiliskMonteCarloCompatibilityPlan(
        controller="Basilisk.utilities.MonteCarlo.Controller.Controller",
        seed=int(seed),
        sample_count=int(sample_count),
        dispersions={str(k): dict(v) for k, v in dispersions.items()},
        capability_id=cid,
        eligible=eligible,
        unsupported_paths=unsupported,
        status="runnable_official_controller" if eligible else "not_eligible",
    )


@dataclass
class _FoundationControllerCase:
    duration_s: float = 60.0
    step_s: float = 1.0
    sample_s: float = 5.0
    initial_pointing_error_deg: float = 5.0
    orbit_rate_rad_s: float = 0.0011
    orbit_radius_m: float = 7_000_000.0
    inclination_deg: float = 5.0
    simulation_seed: int = 0
    trace_rows: list[dict[str, Any]] = field(default_factory=list)
    retained_summary: dict[str, Any] = field(default_factory=dict)

    def execute(self) -> None:
        from sat_sim.bsk_engine.scenario_base import FoundationOrbitAttitudeScenario
        from sat_sim.bsk_engine.types import BSKScenarioConfig

        cfg = BSKScenarioConfig(
            scenario_id="official_mc_foundation_case",
            capability_id="whole_spacecraft.bsksim_foundation.v1",
            duration_s=max(float(self.duration_s), 1e-6),
            step_s=max(float(self.step_s), 1e-6),
            sample_s=max(float(self.sample_s), float(self.step_s), 1e-6),
            mode_request="inertialPoint",
            parameters={
                "initial_pointing_error_deg": float(self.initial_pointing_error_deg),
                "orbit_rate_rad_s": float(self.orbit_rate_rad_s),
                "orbit_radius_m": float(self.orbit_radius_m),
                "inclination_deg": float(self.inclination_deg),
                "simulation_seed": int(self.simulation_seed),
            },
            events=(),
            requested_outputs=(),
        )
        rows, runtime = FoundationOrbitAttitudeScenario(cfg)._run_native_foundation()
        self.trace_rows = [dict(row) for row in rows]
        errors = [float(row.get("attitude.pointing_error_deg", 0.0)) for row in rows]
        radii = [float(row.get("orbit.radius_m", 0.0)) for row in rows]
        applied = {
            "simulation.duration_s": float(self.duration_s),
            "simulation.step_s": float(self.step_s),
            "simulation.sample_s": float(self.sample_s),
            "parameters.values.initial_pointing_error_deg": float(self.initial_pointing_error_deg),
            "parameters.values.orbit_rate_rad_s": float(self.orbit_rate_rad_s),
            "parameters.values.orbit_radius_m": float(self.orbit_radius_m),
            "parameters.values.inclination_deg": float(self.inclination_deg),
        }
        self.retained_summary = {
            "status": "SUCCEEDED",
            "backend_type": "basilisk_native",
            "official_controller": True,
            "simulation_seed": int(self.simulation_seed),
            "trace_rows": len(rows),
            "controller_applied_parameters": applied,
            "requested_initial_pointing_error_deg": float(self.initial_pointing_error_deg),
            "initial_recorded_pointing_error_deg": errors[0] if errors else None,
            "initial_condition_application_error_deg": (errors[0] - float(self.initial_pointing_error_deg)) if errors else None,
            "final_pointing_error_deg": errors[-1] if errors else None,
            "max_pointing_error_deg": max(errors) if errors else None,
            "mean_orbit_radius_m": (sum(radii) / len(radii)) if radii else None,
            "runtime": runtime,
        }


@dataclass
class _UnifiedControllerCase:
    capability_id: str = "subsystem.adcs_unified_native.v1"
    duration_s: float = 20.0
    step_s: float = 0.2
    sample_s: float = 1.0
    simulation_seed: int = 0
    initial_pointing_error_deg: float = 8.0
    orbit_radius_m: float = 7_000_000.0
    inclination_deg: float = 35.0
    controller_k: float = 3.5
    controller_p: float = 30.0
    rw_max_torque_nm: float = 0.2
    initial_soc: float = 0.62
    battery_capacity_wh: float = 160.0
    solar_panel_area_m2: float = 2.5
    solar_efficiency: float = 0.28
    bus_power_w: float = 18.0
    adcs_power_w: float = 12.0
    payload_power_w: float = 38.0
    downlink_power_w: float = 16.0
    min_operational_soc: float = 0.2
    payload_max_pointing_error_deg: float = 20.0
    payload_data_rate_bps: float = 2_500_000.0
    downlink_rate_bps: float = 1_500_000.0
    storage_capacity_bits: float = 6_000_000_000.0
    initial_payload_temp_k: float = 295.0
    events: tuple[Any, ...] = field(default_factory=tuple)
    trace_rows: list[dict[str, Any]] = field(default_factory=list)
    retained_summary: dict[str, Any] = field(default_factory=dict)

    def _values(self) -> dict[str, Any]:
        return {
            "simulation_seed": int(self.simulation_seed),
            "initial_pointing_error_deg": float(self.initial_pointing_error_deg),
            "orbit_radius_m": float(self.orbit_radius_m),
            "inclination_deg": float(self.inclination_deg),
            "controller_k": float(self.controller_k),
            "controller_p": float(self.controller_p),
            "rw_max_torque_nm": float(self.rw_max_torque_nm),
            "initial_soc": float(self.initial_soc),
            "battery_capacity_wh": float(self.battery_capacity_wh),
            "solar_panel_area_m2": float(self.solar_panel_area_m2),
            "solar_efficiency": float(self.solar_efficiency),
            "bus_power_w": float(self.bus_power_w),
            "adcs_power_w": float(self.adcs_power_w),
            "payload_power_w": float(self.payload_power_w),
            "downlink_power_w": float(self.downlink_power_w),
            "min_operational_soc": float(self.min_operational_soc),
            "payload_max_pointing_error_deg": float(self.payload_max_pointing_error_deg),
            "payload_data_rate_bps": float(self.payload_data_rate_bps),
            "downlink_rate_bps": float(self.downlink_rate_bps),
            "storage_capacity_bits": float(self.storage_capacity_bits),
            "initial_payload_temp_k": float(self.initial_payload_temp_k),
        }

    def execute(self) -> None:
        from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, UnifiedRuntimeConfig

        adcs_only = self.capability_id == "subsystem.adcs_unified_native.v1"
        cfg = UnifiedRuntimeConfig(
            capability_id=self.capability_id,
            duration_s=max(float(self.duration_s), 1e-6),
            step_s=max(float(self.step_s), 1e-6),
            sample_s=max(float(self.sample_s), float(self.step_s), 1e-6),
            adcs_only=adcs_only,
            values=self._values(),
            events=tuple(self.events),
        )
        result = UnifiedNativeRuntime(cfg).run()
        rows = [dict(row) for row in result.trace_rows]
        self.trace_rows = rows
        first = rows[0] if rows else {}
        last = rows[-1] if rows else {}
        errors = [float(row.get("adcs.pointing_error_deg", 0.0)) for row in rows]
        radii = [float(row.get("orbit.radius_m", 0.0)) for row in rows]
        wheel_speeds = [
            abs(float(row.get(f"adcs.rw.speed_rad_s_{index}", 0.0)))
            for row in rows for index in range(3)
        ]
        solar = [float(row.get("eps.solar_array_power_w", 0.0)) for row in rows]
        applied = {
            "simulation.duration_s": float(self.duration_s),
            "simulation.step_s": float(self.step_s),
            "simulation.sample_s": float(self.sample_s),
            **{f"parameters.values.{key}": value for key, value in self._values().items() if key != "simulation_seed"},
        }
        summary = dict(result.summary)
        summary.update({
            "status": "SUCCEEDED",
            "official_controller": True,
            "simulation_seed": int(self.simulation_seed),
            "controller_applied_parameters": applied,
            "initial_recorded_pointing_error_deg": first.get("adcs.pointing_error_deg"),
            "final_pointing_error_deg": last.get("adcs.pointing_error_deg"),
            "max_pointing_error_deg": max(errors) if errors else None,
            "mean_orbit_radius_m": (sum(radii) / len(radii)) if radii else None,
            "max_rw_speed_rad_s": max(wheel_speeds) if wheel_speeds else None,
        })
        if not adcs_only:
            summary.update({
                "initial_recorded_soc": first.get("eps.battery_soc"),
                "initial_recorded_battery_capacity_wh": first.get("eps.effective_battery_capacity_wh"),
                "initial_recorded_payload_data_rate_bps": first.get("payload.generated_bps"),
                "initial_recorded_payload_temp_k": first.get("thermal.payload_temp_k"),
                "final_battery_soc": last.get("eps.battery_soc"),
                "final_storage_bits": last.get("data.storage_bits"),
                "final_payload_temp_k": last.get("thermal.payload_temp_k"),
                "max_solar_array_power_w": max(solar) if solar else None,
            })
        self.retained_summary = summary


@dataclass(frozen=True)
class _CaseFactory:
    case_kind: str
    defaults: dict[str, Any]

    def __call__(self) -> Any:
        if self.case_kind == "foundation":
            return _FoundationControllerCase(**copy.deepcopy(self.defaults))
        return _UnifiedControllerCase(**copy.deepcopy(self.defaults))


def _configure_case(case: Any) -> None:
    return None


def _execute_case(case: Any) -> None:
    case.execute()


def _retain_case(case: Any) -> dict[str, Any]:
    return {"summary": copy.deepcopy(case.retained_summary), "trace_rows": copy.deepcopy(case.trace_rows)}


def _simulation_defaults(base_task_spec: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    simulation = _mapping(base_task_spec.get("simulation"))
    values = _mapping(_mapping(base_task_spec.get("parameters")).get("values"))
    sim = {
        "duration_s": float(simulation.get("duration_s", 60.0) or 60.0),
        "step_s": float(simulation.get("step_s", _mapping(simulation.get("solver")).get("step_s", 1.0)) or 1.0),
        "sample_s": float(simulation.get("sample_s", 5.0) or 5.0),
        "simulation_seed": int(simulation.get("random_seed", 0) or 0),
    }
    return sim, values


def _case_defaults(base_task_spec: Mapping[str, Any], capability_id: str) -> dict[str, Any]:
    sim, values = _simulation_defaults(base_task_spec)
    if capability_id == "whole_spacecraft.bsksim_foundation.v1":
        return {
            "duration_s": sim["duration_s"], "step_s": sim["step_s"], "sample_s": sim["sample_s"],
            "initial_pointing_error_deg": float(values.get("initial_pointing_error_deg", 5.0) or 5.0),
            "orbit_rate_rad_s": float(values.get("orbit_rate_rad_s", 0.0011) or 0.0011),
            "orbit_radius_m": float(values.get("orbit_radius_m", 7_000_000.0) or 7_000_000.0),
            "inclination_deg": float(values.get("inclination_deg", 5.0) or 5.0),
            "simulation_seed": sim["simulation_seed"],
        }
    from sat_sim.bsk_engine.event_manager import parse_bsk_events
    defaults = _UnifiedControllerCase(capability_id=capability_id).__dict__.copy()
    for key in ("trace_rows", "retained_summary"):
        defaults.pop(key, None)
    defaults.update({"capability_id": capability_id, **sim, "events": tuple(parse_bsk_events(base_task_spec))})
    for key in list(defaults):
        if key in values and key not in {"capability_id", "events"}:
            defaults[key] = values[key]
    return defaults


def _variant_modifications(variant: Mapping[str, Any], capability_id: str) -> dict[str, Any]:
    contract = native_controller_contract(capability_id)
    if contract is None:
        return {}
    spec = _mapping(variant.get("task_spec"))
    parameters = _mapping(variant.get("parameters"))
    mods: dict[str, Any] = {}
    for path, value in parameters.items():
        mapped = contract.path_map.get(str(path))
        if mapped:
            mods[mapped] = value
    simulation_seed = variant.get("simulation_seed")
    if simulation_seed is None:
        simulation_seed = _nested_get(spec, "simulation.random_seed", 0)
    mods["simulation_seed"] = int(simulation_seed or 0)
    return mods


def _numeric_output_statistics(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    keys: set[str] = set()
    for run in runs:
        if run.get("status") != "SUCCEEDED":
            continue
        for key, value in _mapping(run.get("summary")).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                keys.add(str(key))
    return {
        key: summarize_numeric_samples(
            _mapping(run.get("summary")).get(key)
            for run in runs if run.get("status") == "SUCCEEDED"
        )
        for key in sorted(keys)
    }


def _parameter_application_checks(capability_id: str, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    contract = native_controller_contract(capability_id)
    if contract is None:
        return {}
    checks: dict[str, Any] = {}
    for descriptor in contract.parameters:
        pairs: list[tuple[float, float]] = []
        sensitivity_values: list[float] = []
        case_values: list[float] = []
        for run in runs:
            if run.get("status") != "SUCCEEDED":
                continue
            params = _mapping(run.get("parameters"))
            if descriptor.task_path not in params:
                continue
            requested = params.get(descriptor.task_path)
            summary = _mapping(run.get("summary"))
            applied = _mapping(summary.get("controller_applied_parameters")).get(descriptor.task_path)
            if isinstance(requested, (int, float)) and isinstance(applied, (int, float)):
                case_values.append(float(applied))
            if descriptor.recorder_summary_field:
                recorded = summary.get(descriptor.recorder_summary_field)
                if isinstance(requested, (int, float)) and isinstance(recorded, (int, float)):
                    pairs.append((float(requested), float(recorded)))
            if descriptor.sensitivity_summary_field:
                observed = summary.get(descriptor.sensitivity_summary_field)
                if isinstance(observed, (int, float)):
                    sensitivity_values.append(float(observed))
        if not case_values and not pairs:
            continue
        requested_values = [
            float(_mapping(run.get("parameters"))[descriptor.task_path])
            for run in runs
            if run.get("status") == "SUCCEEDED"
            and isinstance(_mapping(run.get("parameters")).get(descriptor.task_path), (int, float))
        ]
        case_error = max((abs(requested_values[i] - case_values[i]) for i in range(min(len(requested_values), len(case_values)))), default=None)
        recorder_error = max((abs(a - b) for a, b in pairs), default=None)
        unique_requested = len(set(requested_values))
        sensitivity_observed = unique_requested <= 1 or len(set(sensitivity_values)) > 1
        checks[descriptor.task_path] = {
            "sample_count": len(requested_values),
            "unique_requested_count": unique_requested,
            "unique_case_value_count": len(set(case_values)),
            "case_attribute_max_abs_error": case_error,
            "recorder_sample_count": len(pairs),
            "recorder_max_abs_error": recorder_error,
            "applied_to_case": bool(case_values) and case_error is not None and case_error <= 1e-9,
            "recorder_verified": (not descriptor.recorder_summary_field) or (bool(pairs) and recorder_error is not None and recorder_error <= 1e-9),
            "sensitivity_observed": sensitivity_observed,
            "applied": bool(case_values) and case_error is not None and case_error <= 1e-9 and ((not descriptor.recorder_summary_field) or (bool(pairs) and recorder_error is not None and recorder_error <= 1e-9)),
            "recorder_summary_field": descriptor.recorder_summary_field,
            "sensitivity_summary_field": descriptor.sensitivity_summary_field,
        }
    return checks


def _execute_basilisk_controller_variants_in_process(
    base_task_spec: Mapping[str, Any],
    variants: Sequence[Mapping[str, Any]],
    *,
    archive_dir: str | Path | None = None,
    thread_count: int = 1,
) -> dict[str, Any]:
    cid = _capability_id(base_task_spec)
    contract = native_controller_contract(cid)
    if contract is None:
        raise ValueError(f"official Basilisk Controller is not available for capability {cid!r}")
    if not variants:
        raise ValueError("at least one Monte Carlo variant is required")

    from Basilisk.utilities.MonteCarlo import Controller, RetentionPolicy  # type: ignore

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if archive_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="sat_sim_basilisk_mc_")
        root = Path(temporary.name)
    else:
        root = Path(archive_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
    ic_dir = root / "initial_conditions"
    controller_archive = root / "controller_archive"
    ic_dir.mkdir(parents=True, exist_ok=True)
    controller_archive.parent.mkdir(parents=True, exist_ok=True)

    for index, variant in enumerate(variants):
        (ic_dir / f"run{index}.json").write_text(
            json.dumps(_variant_modifications(variant, cid), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    controller = Controller.Controller()
    controller.setSimulationFunction(_CaseFactory(contract.case_kind, _case_defaults(base_task_spec, cid)))
    controller.setConfigureFunction(_configure_case)
    controller.setExecutionFunction(_execute_case)
    controller.setExecutionCount(len(variants))
    controller.setThreadCount(max(1, int(thread_count)))
    controller.setShowProgressBar(False)
    controller.setVerbose(False)
    controller.setShouldDisperseSeeds(False)
    controller.setICDir(str(ic_dir))
    controller.setICRunFlag(True)
    controller.setArchiveDir(str(controller_archive))
    retention = RetentionPolicy.RetentionPolicy()
    retention.addRetentionFunction(_retain_case)
    controller.addRetentionPolicy(retention)

    failures = controller.runInitialConditions(list(range(len(variants))))
    runs: list[dict[str, Any]] = []
    for index, variant in enumerate(variants):
        retained = controller.getRetainedData(index) if index not in failures else {}
        custom = retained.get("custom") if isinstance(retained, Mapping) else {}
        summary = custom.get("summary") if isinstance(custom, Mapping) else {}
        trace = custom.get("trace_rows") if isinstance(custom, Mapping) else []
        runs.append({
            "case_index": index,
            "status": "FAILED" if index in failures else "SUCCEEDED",
            "parameters": copy.deepcopy(_mapping(variant.get("parameters"))),
            "simulation_seed": variant.get("simulation_seed"),
            "summary": copy.deepcopy(summary) if isinstance(summary, Mapping) else {},
            "trace_rows": copy.deepcopy(trace) if isinstance(trace, Sequence) else [],
            "controller_parameter_file": str(ic_dir / f"run{index}.json"),
            "controller_retention_file": str(ic_dir / f"run{index}.data"),
        })

    report = {
        "schema_version": "basilisk-monte-carlo-runtime.v2",
        "controller": "Basilisk.utilities.MonteCarlo.Controller.Controller",
        "controller_invoked": True,
        "capability_id": cid,
        "case_kind": contract.case_kind,
        "execution_mode": "official_controller_initial_condition_cases",
        "sample_count": len(variants),
        "failure_count": len(failures),
        "failures": list(failures),
        "archive_root": str(root),
        "run_retention": "official RetentionPolicy plus per-case .json/.data archive",
        "runs": runs,
        "output_statistics": _numeric_output_statistics(runs),
        "parameter_application_checks": _parameter_application_checks(cid, runs),
        "boundary": (
            "TaskSpec owns validated sampling and per-run seeds; the official Basilisk Controller owns verified native case creation, modification, execution and retention."
        ),
    }
    (root / "platform_basilisk_mc_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if temporary is not None:
        report["archive_root_temporary"] = True
        temporary.cleanup()
    return report


def _controller_worker_environment() -> dict[str, str]:
    """Build a deterministic environment for the isolated Controller process.

    The official Basilisk Controller creates multiprocessing Manager, Process,
    and Pool objects internally. Running it in a fresh interpreter and forcing
    the ``spawn`` start method avoids inheriting threads/native runtime state
    from API, pytest, or worker parents.
    """
    env = dict(os.environ)
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        env[name] = "1"
    source_root = Path(__file__).resolve().parents[2]
    project_root = source_root.parent
    existing = env.get("PYTHONPATH", "")
    entries = [str(source_root), str(project_root)]
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    env["SAT_SIM_BASILISK_MC_ISOLATED_WORKER"] = "1"
    return env


def execute_basilisk_controller_variants(
    base_task_spec: Mapping[str, Any],
    variants: Sequence[Mapping[str, Any]],
    *,
    archive_dir: str | Path | None = None,
    thread_count: int = 1,
    controller_timeout_s: float | None = None,
) -> dict[str, Any]:
    """Execute the official Basilisk Controller in a fresh spawn process.

    The public API remains synchronous. Input and output cross the process
    boundary through canonical JSON files so large retained traces are not
    duplicated through a multiprocessing queue. The worker forces ``spawn``
    before importing Basilisk, eliminating the Python 3.13 fork-from-
    multithreaded-parent warning and the associated deadlock risk.
    """
    cid = _capability_id(base_task_spec)
    if native_controller_contract(cid) is None:
        raise ValueError(f"official Basilisk Controller is not available for capability {cid!r}")
    if not variants:
        raise ValueError("at least one Monte Carlo variant is required")

    timeout_s = (
        float(controller_timeout_s)
        if controller_timeout_s is not None
        else max(300.0, 120.0 * len(variants))
    )
    if timeout_s <= 0:
        raise ValueError("controller_timeout_s must be positive")

    worker_script = Path(__file__).with_name("basilisk_mc_worker.py")
    if not worker_script.is_file():
        raise RuntimeError(f"Basilisk Monte Carlo worker is missing: {worker_script}")

    with tempfile.TemporaryDirectory(prefix="sat_sim_basilisk_mc_control_") as control_dir:
        control_root = Path(control_dir)
        request_path = control_root / "request.json"
        response_path = control_root / "response.json"
        worker_archive = Path(archive_dir).resolve() if archive_dir is not None else control_root / "archive"
        request = {
            "schema_version": "sat-sim.basilisk-mc-worker-request.v1",
            "base_task_spec": copy.deepcopy(dict(base_task_spec)),
            "variants": copy.deepcopy([dict(item) for item in variants]),
            "archive_dir": str(worker_archive),
            "thread_count": max(1, int(thread_count)),
        }
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(worker_script),
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[3],
                env=_controller_worker_environment(),
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"official Basilisk Controller exceeded controller_timeout_s={timeout_s}"
            ) from exc

        payload: dict[str, Any] = {}
        if response_path.is_file():
            try:
                loaded = json.loads(response_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, json.JSONDecodeError):
                payload = {}
        if completed.returncode != 0 or payload.get("status") != "PASS":
            detail = payload.get("error_message") or completed.stderr[-4000:] or completed.stdout[-4000:]
            raise RuntimeError(
                f"isolated Basilisk Monte Carlo worker failed with code {completed.returncode}: {detail}"
            )
        report = payload.get("report")
        if not isinstance(report, dict):
            raise RuntimeError("isolated Basilisk Monte Carlo worker returned no report")
        report["process_isolation"] = {
            "mode": "fresh_python_subprocess",
            "multiprocessing_start_method": payload.get("multiprocessing_start_method"),
            "native_thread_limits": payload.get("native_thread_limits"),
            "worker_pid": payload.get("worker_pid"),
            "worker_return_code": completed.returncode,
        }
        if archive_dir is None:
            report["archive_root_temporary"] = True
        return report


__all__ = [
    "BasiliskMonteCarloCompatibilityPlan",
    "build_basilisk_mc_plan",
    "execute_basilisk_controller_variants",
    "_execute_basilisk_controller_variants_in_process",
]
