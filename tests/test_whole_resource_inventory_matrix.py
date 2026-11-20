"""Public TaskSpec variants must export truthful native resource inventories."""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path

import pytest
import yaml

pytest.importorskip("Basilisk")

from sat_sim.capability_registry import get_adapter_for_capability
from sat_sim.task_compiler import compile_task_spec
from sat_sim.unified_execution import build_execution_request


def _execute_public(spec):
    compiled = compile_task_spec(spec)
    request = build_execution_request(compiled, spec)
    runtime_spec = request.metadata.get("resolved_runtime_task_spec", spec)
    adapter = get_adapter_for_capability(request.capability_id)
    assert not [issue for issue in adapter.validate(runtime_spec) if str(issue.severity).lower() == "error"]
    return adapter.run(runtime_spec)


def _save(case, spec, result, tmp_path):
    root = Path(os.environ.get("P10A7_EVIDENCE_DIR", str(tmp_path)))
    root.mkdir(parents=True, exist_ok=True)
    import Basilisk
    repo = Path(__file__).resolve().parents[1]
    sources = [
        "src/integration/power_authority_inventory.py", "src/integration/whole_resource_inventory.py",
        "src/integration/thermal_resource_evidence.py", "src/integration/resource_feedback.py",
        "src/components/thermal_node/basilisk_scheduled.py", "src/subsystems/thermal/thermal_network.py",
        "src/subsystems/thermal/builder.py", "src/subsystems/eps/builder.py",
        "src/whole_spacecraft/builder.py", "src/whole_spacecraft/runner.py",
        "src/whole_spacecraft/battery_evidence.py", "src/sat_sim/bsk_engine/unified_native.py",
        "src/sat_sim/capabilities/whole_spacecraft.unified_native.v1.yaml",
        "src/sat_sim/capabilities/whole_spacecraft.composite_digital_twin.v1.yaml",
    ]
    payload = {"case": case, "task_spec": spec, "summary": result.summary,
        "provenance": {"basilisk_version": Basilisk.__version__, "ci_revision": os.environ.get("GITHUB_SHA"),
            "implementation_sha256": {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in sources}},
        "trace_sample_count": len(result.trace_rows),
        "first_trace_row": result.trace_rows[0], "last_trace_row": result.trace_rows[-1]}
    (root / f"{case}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


@pytest.mark.parametrize("propulsion", [False, True])
@pytest.mark.parametrize("network,native_comm", [(True, True), (True, False), (False, True), (False, False)])
def test_public_composite_configuration_matrix_has_zero_power_reconstruction_proxies(network, native_comm, propulsion, tmp_path):
    name = f"composite-network-{int(network)}-comm-{int(native_comm)}-propulsion-{int(propulsion)}"
    spec = {
        "schema_version": "0.1.0", "task_id": name, "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.composite_digital_twin.v1",
        "target": {"level": "whole_spacecraft", "name": "composite_digital_twin", "mode": "nominal"},
        "simulation": {"duration_s": 20.0, "sample_s": 0.5, "backend": "basilisk"},
        "parameters": {"initial_soc": 0.9, "solar_power_w": 0.0, "thermal_step_s": 10.0,
            "thermal_use_network": network, "propulsion_enabled": propulsion,
            "propulsion_on_time_s": [0.8, 0.0],
            "coupling": {"enable_comm_native_odh_downlink": native_comm}},
    }
    template = yaml.safe_load((Path(__file__).resolve().parents[1] / "examples/whole_spacecraft_composite_digital_twin_nominal.yaml").read_text())
    template.update(spec)
    spec = template
    result = _execute_public(spec)
    inventory = result.summary["power_authority_inventory"]
    assert inventory["status"] == "PASS", inventory["errors"]
    counts = inventory["counts"]
    assert counts["engineering_proxy_provider_count"] == counts["unknown_provider_count"] == counts["unregistered_provider_count"] == 0
    assert counts["project_physical_provider_count"] == (2 if network else 1)
    assert counts["electrical_provider_count"] == counts["official_provider_count"] + counts["project_physical_provider_count"]
    assert counts["scheduled_model_count"] == len(inventory["scheduled_models"])
    assert result.summary["battery_evidence_status"] == "PASS"
    assert result.summary["resource_closure_evidence"]["status"] == "PASS"
    assert len(result.summary["resource_closure_evidence"]["thermal_transfer"]["heaters"]) == (2 if network else 1)
    assert abs(result.summary["energy_balance_residual_j"]) < 1e-6
    assert result.trace_rows[0]["time_s"] == 0.5
    assert result.trace_rows[0]["battery_source_time_s"] == pytest.approx(0.4)
    if propulsion:
        sources = [source for node in result.summary["resource_closure_evidence"]["thermal_transfer"]["nodes"]
            for source in node["sources"] if source["label"] == "propulsion.actual_activity"]
        assert sum(source["deposited_heat_energy_j"] for source in sources) == pytest.approx(13.6)
    _save(name, spec, result, tmp_path)


@pytest.mark.parametrize("propulsion", [False, True])
def test_public_unified_inventory_includes_project_heaters_and_scheduled_children(propulsion, tmp_path):
    name = f"unified-propulsion-{int(propulsion)}"
    from sat_sim.scenario_templates import instantiate_scenario_template
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id=name)
    spec["simulation"].update(duration_s=12.0, step_s=0.2, sample_s=1.0)
    spec["parameters"]["values"].update(initial_soc=0.9, propulsion_enabled=propulsion,
        propulsion_burn_start_s=0.4, propulsion_burn_on_time_s=0.8, propulsion_power_w=20.0)
    result = _execute_public(spec)
    inventory = result.summary["power_authority_inventory"]
    assert inventory["status"] == "PASS", inventory["errors"]
    assert inventory["counts"]["engineering_proxy_provider_count"] == 0
    assert inventory["counts"]["project_physical_provider_count"] == 2
    assert inventory["counts"]["unregistered_provider_count"] == 0
    assert any(item["model_class"].endswith(".ThermalRadiator") for item in inventory["scheduled_models"])
    assert result.summary["resource_closure_evidence"]["status"] == "PASS"
    _save(name, spec, result, tmp_path)


@pytest.mark.parametrize("network", [False, True])
def test_constant_solar_public_branch_binds_the_actual_scheduled_source(network):
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftCouplingConfig, WholeSpacecraftRunConfig
    cfg = WholeSpacecraftConfig(initial_soc=0.9, thermal_use_network=network, thermal_step_s=0.2,
        recorder_step_s=0.2, propulsion_enabled=False,
        coupling=WholeSpacecraftCouplingConfig(enable_orbit_sun_attitude_eclipse_to_eps_solar_power=False))
    graph = build_whole_spacecraft_graph(cfg)
    summary, _ = _execute_whole_spacecraft_graph(graph, WholeSpacecraftRunConfig(duration_s=2.0, sample_s=0.2, structure=cfg))
    assert summary.power_authority_inventory["status"] == "PASS", summary.power_authority_inventory["errors"]
    assert summary.resource_closure_evidence["status"] == "PASS"
    sources = [source for source in summary.power_authority_inventory["authorities"] if source["role"] == "solar_generation"]
    assert len(sources) == 1
    assert sources[0]["model_tag"] == graph.eps_graph.solar_source.ModelTag
    assert "SimplePowerSink" in sources[0]["model_class"]


@pytest.mark.parametrize("network", [False, True])
def test_intentionally_disabled_heater_funding_keeps_hardware_visible_and_closure_partial(network):
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftCouplingConfig, WholeSpacecraftRunConfig
    cfg = WholeSpacecraftConfig(initial_soc=0.9, thermal_use_network=network, thermal_step_s=0.2,
        recorder_step_s=0.2, propulsion_enabled=False,
        coupling=WholeSpacecraftCouplingConfig(enable_thermal_heaters_to_eps_load=False))
    graph = build_whole_spacecraft_graph(cfg)
    summary, _ = _execute_whole_spacecraft_graph(graph, WholeSpacecraftRunConfig(duration_s=2.0, sample_s=0.2, structure=cfg))
    assert summary.power_authority_inventory["status"] == "PASS"
    assert summary.power_authority_inventory["counts"]["project_physical_provider_count"] == (2 if network else 1)
    assert summary.resource_closure_evidence["status"] == "PARTIAL"
    assert summary.resource_closure_evidence["disabled_resource_links"] == ["thermal_heaters_to_eps_load"]


@pytest.mark.parametrize("source_name,priority,field", [("native_downlink", 38, "attemptedDataRate"), ("instrument", 40, "baudRate")])
def test_transient_invalid_native_power_activity_cannot_recover_to_a_successful_inventory(source_name, priority, field):
    from Basilisk.architecture import sysModel
    from Basilisk.utilities import macros
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig
    cfg = WholeSpacecraftConfig(initial_soc=0.9, thermal_step_s=0.2, recorder_step_s=1.0, propulsion_enabled=False)
    graph = build_whole_spacecraft_graph(cfg)
    model = graph.component_registry[source_name] if source_name == "native_downlink" else graph.instrument
    message = model.downlinkOutMsg if source_name == "native_downlink" else model.nodeDataOutMsg

    class TransientInvalidActivity(sysModel.SysModel):
        def UpdateState(self, now):
            if int(now) == macros.sec2nano(0.4):
                payload = message.read()
                setattr(payload, field, math.nan)
                message.write(payload, now, self.moduleID)

    injection = TransientInvalidActivity()
    graph.sim.AddModelToTask(graph.sim_task_name, injection, None, priority)
    summary, _ = _execute_whole_spacecraft_graph(graph, WholeSpacecraftRunConfig(duration_s=2.0, sample_s=1.0, structure=cfg))
    assert summary.power_authority_inventory["status"] == "ERROR"
    assert summary.resource_closure_evidence["status"] == "FAIL"
    assert summary.execution_status == "FAIL"
