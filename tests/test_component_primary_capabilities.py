from __future__ import annotations

import math
import json
import os
from pathlib import Path
import subprocess
import sys

from sat_sim.capability_registry import get_adapter_for_capability, get_capability
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.script_exporter import export_runner_script
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_runner import run_compiled_task
from sat_sim.workbench_catalog import workbench_presentation_catalog

NEW_PRIMARY_TEMPLATES = (
    "component_cmg_nominal",
    "component_fuel_tank_nominal",
    "component_imu_nominal",
    "component_magnetometer_nominal",
    "component_pdu_nominal",
    "component_star_tracker_nominal",
    "component_sun_sensor_nominal",
    "component_thruster_nominal",
)


def test_all_24_components_have_active_direct_primary_capabilities() -> None:
    components = [
        item for item in workbench_presentation_catalog()["objects"]
        if item["level"] == "component"
    ]
    assert len(components) == 24
    assert all(item["primary_capability_id"] for item in components)
    assert all(item["independently_executable"] for item in components)
    assert not [item for item in components if item["integration_only"]]
    for item in components:
        contract = get_capability(item["primary_capability_id"])
        assert contract.is_active
        assert contract.exposed_to_agent
        assert contract.data["adapter"]["runtime_run"] is True
        assert contract.data["adapter"]["script_export"] is True


def test_new_primary_component_templates_execute_with_finite_evidence() -> None:
    for template_id in NEW_PRIMARY_TEMPLATES:
        spec = instantiate_scenario_template(template_id)
        result = run_compiled_task(compile_task_spec(spec), task_spec=spec, write_dataset=False)
        assert result.summary["status"] == "complete"
        assert result.trace_rows
        numeric = [
            float(value)
            for row in result.trace_rows
            for value in row.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        assert numeric and all(math.isfinite(value) for value in numeric)


def test_new_primary_adapters_and_contracts_declare_all_three_modes() -> None:
    for template_id in NEW_PRIMARY_TEMPLATES:
        spec = instantiate_scenario_template(template_id)
        capability_id = spec["model"]["capability_id"]
        adapter = get_adapter_for_capability(capability_id)
        contract = get_capability(capability_id)
        assert adapter.supported_modes == ("nominal", "fault", "degradation")
        assert all(contract.data["modes"][mode]["supported"] is True for mode in adapter.supported_modes)


def test_new_primary_capability_scripts_export_and_execute(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    for template_id in NEW_PRIMARY_TEMPLATES:
        spec = instantiate_scenario_template(template_id)
        case_root = tmp_path / template_id
        spec_path = case_root / "task_spec.json"
        script_path = case_root / "run.py"
        output_root = case_root / "output"
        case_root.mkdir()
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        export_runner_script(spec_path, script_path, kind="capability-python", output_root=output_root)
        completed = subprocess.run(
            [sys.executable, str(script_path), "--output-root", str(output_root)],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        assert completed.returncode == 0, f"{template_id}: {completed.stderr or completed.stdout}"
        payload = json.loads(completed.stdout)
        assert payload["ok"] is True
        assert payload["capability_id"] == spec["model"]["capability_id"]
        assert (Path(payload["dataset"]["output_root"]) / "manifest.json").is_file()
