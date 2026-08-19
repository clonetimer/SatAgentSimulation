from sat_sim.adapters.whole_spacecraft_bsksim_coupled import WholeSpacecraftBSKSimCoupledAdapter
from sat_sim.capability_registry import get_capability, list_capabilities


def _spec():
    return {
        "schema_version": "1.0.0",
        "task_id": "test_bsksim_coupled",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.bsksim_coupled.v1",
        "model": {"capability_id": "whole_spacecraft.bsksim_coupled.v1", "target": {"level": "whole_spacecraft", "name": "bsksim_coupled", "mode": "nominal"}},
        "simulation": {"duration_s": 120.0, "step_s": 1.0, "sample_s": 10.0},
        "target": {"level": "whole_spacecraft", "name": "bsksim_coupled", "mode": "nominal"},
        "parameters": {"values": {"initial_soc": 0.62, "eclipse_fraction": 0.35, "solar_power_max_w": 95.0, "payload_data_rate_bps": 2.5e6, "downlink_rate_max_bps": 1.5e6}},
        "outputs": {"plots": ["eps.battery_soc", "thermal.bus_temp_c", "data.storage_bits"]},
    }


def test_whole_spacecraft_bsksim_coupled_capability_loads():
    ids = {c.capability_id for c in list_capabilities()}
    assert "whole_spacecraft.bsksim_coupled.v1" in ids
    contract = get_capability("whole_spacecraft.bsksim_coupled.v1")
    assert contract.data["adapter"]["class_path"].endswith("WholeSpacecraftBSKSimCoupledAdapter")


def test_whole_spacecraft_bsksim_coupled_run_has_coupling_evidence():
    adapter = WholeSpacecraftBSKSimCoupledAdapter()
    result = adapter.run(_spec())
    assert result.summary["status"] == "complete"
    assert result.summary["coupling_integrity_pass"] is True
    assert result.summary["strong_coupling_chain_count"] >= 8
    assert result.summary["min_battery_soc"] is not None
    assert len(result.trace_rows) > 3
    row = result.trace_rows[0]
    for field in ("orbit.sun_incidence_cos", "eps.solar_array_power_w", "thermal.bus_temp_c", "payload.generated_data_bits", "comm.downlink_rate_bps", "data.storage_bits"):
        assert field in row
    assert result.metadata["model_source_boundary"]["strong_coupling_bridge"] is False
    assert result.metadata["model_source_boundary"]["project_coupled_equation_engine"] is True
    assert result.summary["backend_type"] == "project_coupled_equation_engine"
    assert result.metadata["model_source_boundary"]["full_basilisk_native_sysmodels"] is False


def test_whole_spacecraft_bsksim_coupled_event_labels():
    spec = _spec()
    spec["events"] = {"faults": [{"id": "link_loss", "effect": "comm_data_downlink_link_loss", "target": "communication", "start_s": 20.0, "end_s": 60.0}]}
    result = WholeSpacecraftBSKSimCoupledAdapter().run(spec)
    active = [row for row in result.trace_rows if 20.0 <= row["time_s"] <= 60.0]
    assert active
    assert any(row.get("label.fault_active") is True for row in active)
    assert result.labels["fault_environment"]["episode_count"] >= 1
