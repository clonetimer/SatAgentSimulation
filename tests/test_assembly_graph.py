from __future__ import annotations

import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.assembly_graph import (
    ASSEMBLY_GRAPH_SCHEMA_VERSION,
    V6_ASSEMBLY_GRAPH_SCHEMA_VERSION,
    assembly_catalog,
    assembly_contract,
    validate_assembly_graph,
)
from sat_sim.module_replacements import module_library_catalog


PARENT = "whole_spacecraft.power_thermal_orbit_coupled.v1"


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app)


def test_assembly_catalog_exposes_active_registered_composites() -> None:
    items = assembly_catalog()
    by_id = {item["parent_capability_id"]: item for item in items}
    assert PARENT in by_id
    assert by_id[PARENT]["module_count"] >= 4
    assert by_id[PARENT]["binding_count"] >= 5
    assert by_id[PARENT]["runtime_run"] is True


def test_default_registered_assembly_validates_and_reports_feedback() -> None:
    contract = assembly_contract(PARENT)
    graph = contract["default_graph"]
    assert graph["schema_version"] == ASSEMBLY_GRAPH_SCHEMA_VERSION
    result = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert result.ok is True
    assert result.required_binding_count == 5
    assert result.edge_count == 5
    assert result.feedback_groups
    assert any(item.code == "ASSEMBLY_REGISTERED_FEEDBACK_PRESENT" for item in result.warnings)


def test_scope_annotations_do_not_change_physical_assembly_validation() -> None:
    contract = assembly_contract("subsystem.adcs_fidelity.v1")
    graph = copy.deepcopy(contract["default_graph"])
    graph["scopes"] = [
        {
            "id": "scope-1",
            "name": "RW speed",
            "x": 850,
            "y": 80,
            "probes": [
                {
                    "id": "probe-1",
                    "sourceNode": "module-reaction_wheel",
                    "sourcePort": "reaction_wheel.out.wheel_speed_rad_s",
                }
            ],
        }
    ]
    result = validate_assembly_graph(graph, expected_parent_capability_id="subsystem.adcs_fidelity.v1")
    assert result.ok is True
    assert result.node_count == len(contract["default_graph"]["nodes"])
    assert result.edge_count == len(contract["default_graph"]["edges"])
    assert result.selected_modules == validate_assembly_graph(contract["default_graph"]).selected_modules


def test_assembly_rejects_missing_binding_wrong_endpoint_and_internal_substitution() -> None:
    contract = assembly_contract(PARENT)
    graph = copy.deepcopy(contract["default_graph"])
    graph["edges"].pop()
    result = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert result.ok is False
    assert "ASSEMBLY_REQUIRED_BINDING_MISSING" in {item.code for item in result.errors}

    graph = copy.deepcopy(contract["default_graph"])
    graph["edges"][0]["target"] = graph["edges"][1]["target"]
    result = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert result.ok is False
    assert "ASSEMBLY_BINDING_ENDPOINT_MISMATCH" in {item.code for item in result.errors}

    graph = copy.deepcopy(contract["default_graph"])
    internal = next(node for node in graph["nodes"] if node["moduleAlias"] == "eps")
    internal["capabilityId"] = "subsystem.eps.basic.v1"
    result = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert result.ok is False
    assert "ASSEMBLY_INTERNAL_MODULE_SUBSTITUTION" in {item.code for item in result.errors}


def test_assembly_api_compiles_and_exports_executable_python(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        catalog_response = client.get("/visual-composer/assemblies")
        assert catalog_response.status_code == 200
        assert any(item["parent_capability_id"] == PARENT for item in catalog_response.json()["assemblies"])

        contract_response = client.get(f"/visual-composer/assemblies/{PARENT}")
        assert contract_response.status_code == 200
        graph = contract_response.json()["assembly"]["default_graph"]

        form_response = client.get(f"/forms/capabilities/{PARENT}")
        assert form_response.status_code == 200
        form_data = form_response.json()["form_schema"]["default_form"]

        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form_data, "require_all_bindings": True},
        )
        assert compiled.status_code == 200
        payload = compiled.json()
        assert payload["ok"] is True
        assert payload["assembly_validation"]["ok"] is True
        assert payload["result"]["task_spec"]["model"]["capability_id"] == PARENT

        task_spec = payload["result"]["task_spec"]
        exported = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": graph, "task_spec": task_spec, "require_all_bindings": True},
        )
        assert exported.status_code == 200
        code_payload = exported.json()
        assert code_payload["ok"] is True
        assert code_payload["kind"] == "assembly-python"
        assert code_payload["provenance"]["arbitrary_module_substitution"] is False
        assert code_payload["provenance"]["port_contract_source"] == "explicit-v5"
        assert len(code_payload["provenance"]["port_contract_fingerprint"]) == 64
        assert "parent_managed_stateful_feedback" in code_payload["provenance"]["solver_policies"]
        assert "ASSEMBLY_GRAPH =" in code_payload["code"]
        assert "validate_assembly_graph" in code_payload["code"]
        compile(code_payload["code"], code_payload["filename"], "exec")


def test_scope_annotations_do_not_change_generated_assembly_python(tmp_path: Path) -> None:
    capability_id = "subsystem.adcs_fidelity.v1"
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{capability_id}").json()["assembly"]
        graph = copy.deepcopy(contract["default_graph"])
        form_data = client.get(f"/forms/capabilities/{capability_id}").json()["form_schema"]["default_form"]
        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form_data, "require_all_bindings": True},
        ).json()
        assert compiled["ok"] is True
        task_spec = compiled["result"]["task_spec"]
        baseline = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": graph, "task_spec": task_spec, "require_all_bindings": True},
        ).json()

        observed = copy.deepcopy(graph)
        observed["scopes"] = [{
            "id": "scope-rw",
            "name": "RW speed",
            "x": 880,
            "y": 80,
            "probes": [{
                "id": "probe-rw",
                "sourceNode": "module-reaction_wheel",
                "sourcePort": "reaction_wheel.out.wheel_speed_rad_s",
            }],
        }]
        compiled_with_scope = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": observed, "form_data": form_data, "require_all_bindings": True},
        ).json()
        with_scope = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": observed, "task_spec": task_spec, "require_all_bindings": True},
        ).json()

    assert baseline["ok"] is True
    assert compiled_with_scope["ok"] is True
    assert compiled_with_scope["result"]["task_spec"] == task_spec
    assert with_scope["ok"] is True
    assert with_scope["code"] == baseline["code"]
    assert "scope-rw" not in with_scope["code"]


def test_assembly_export_rejects_mutated_unregistered_edge(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{PARENT}").json()["assembly"]
        graph = copy.deepcopy(contract["default_graph"])
        form_data = client.get(f"/forms/capabilities/{PARENT}").json()["form_schema"]["default_form"]
        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form_data},
        ).json()
        assert compiled["ok"] is True
        task_spec = compiled["result"]["task_spec"]
        graph["edges"][0]["bindingId"] = "invented-binding"
        exported = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": graph, "task_spec": task_spec},
        )
        assert exported.status_code == 200
        payload = exported.json()
        assert payload["ok"] is False
        assert payload["code"] is None
        codes = {item["code"] for item in payload["assembly_validation"]["errors"]}
        assert "ASSEMBLY_BINDING_UNREGISTERED" in codes


def test_v5_explicit_simulation_ports_expose_units_timing_solver_and_shared_fanout() -> None:
    contract = assembly_contract(PARENT)
    ports = contract["simulation_ports"]
    assert ports["schema_version"] == "sat-sim.simulation-ports.v1"
    assert ports["source"] == "explicit-v5"
    assert ports["strict"] is True
    assert ports["validation"]["ok"] is True
    assert len(ports["ports"]) == 9
    assert len(ports["bindings"]) == 5
    assert len(ports["fingerprint"]) == 64

    shadow = next(item for item in ports["ports"] if item["port_id"] == "orbit_environment.out.shadow_factor")
    assert shadow["payload"] == {"schema_id": "sat-sim.signal.timeseries.number.v1", "dtype": "number", "shape": "timeseries", "unit": "ratio"}
    assert shadow["timing"]["rate_policy"] == "parent_aligned"
    assert shadow["fan_out"] == "many"
    assert shadow["direct_feedthrough"] is False

    shadow_bindings = [item for item in ports["bindings"] if item["source_port"] == shadow["port_id"]]
    assert {item["binding_id"] for item in shadow_bindings} == {"shadow_to_eps_solar", "shadow_to_thermal_solar"}
    assert all(item["solver"]["policy"] == "feed_forward" for item in shadow_bindings)

    feedback = next(item for item in ports["bindings"] if item["binding_id"] == "thermal_heater_to_eps_load")
    assert feedback["source_payload"]["unit"] == "W"
    assert feedback["target_payload"]["unit"] == "W"
    assert feedback["solver"]["policy"] == "parent_managed_stateful_feedback"


def test_v5_assembly_rejects_stale_port_contract_fingerprint() -> None:
    contract = assembly_contract(PARENT)
    graph = copy.deepcopy(contract["default_graph"])
    graph["port_contract"]["fingerprint"] = "0" * 64
    result = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert result.ok is False
    assert "ASSEMBLY_PORT_CONTRACT_STALE" in {item.code for item in result.errors}


def test_v4_composite_without_explicit_ports_uses_marked_compatibility_contract() -> None:
    capability_id = "whole_spacecraft.basic_power_orbit.v1"
    contract = assembly_contract(capability_id)
    ports = contract["simulation_ports"]
    assert ports["source"] == "legacy-inferred-v4"
    assert ports["strict"] is False
    assert ports["validation"]["ok"] is True
    assert any(item["code"] == "PORT_CONTRACT_LEGACY_INFERRED" for item in ports["validation"]["warnings"])
    result = validate_assembly_graph(contract["default_graph"], expected_parent_capability_id=capability_id)
    assert result.ok is True
    assert result.port_contract_source == "legacy-inferred-v4"


def test_v5_simulation_port_endpoint_returns_normalized_contract(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get(f"/visual-composer/simulation-ports/{PARENT}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["parent_capability_id"] == PARENT
        assert payload["simulation_ports"]["source"] == "explicit-v5"
        assert len(payload["simulation_ports"]["fingerprint"]) == 64


def test_legacy_v4_graph_port_ids_remain_compatible_without_fingerprint() -> None:
    capability_id = "whole_spacecraft.basic_power_orbit.v1"
    contract = assembly_contract(capability_id)
    graph = copy.deepcopy(contract["default_graph"])
    graph["schema_version"] = "sat-sim.module-assembly.v1"
    graph.pop("port_contract", None)
    assert graph["edges"][0]["sourcePort"].startswith("out:binding-")
    assert graph["edges"][0]["targetPort"].startswith("in:binding-")
    result = validate_assembly_graph(graph, expected_parent_capability_id=capability_id)
    assert result.ok is True
    assert "ASSEMBLY_SCHEMA_LEGACY_V4" in {item.code for item in result.warnings}


def test_explicit_simulation_ports_yaml_matches_packaged_json_schema() -> None:
    import json
    from jsonschema import Draft202012Validator
    from sat_sim.capability_registry import get_capability

    schema_path = Path("src/sat_sim/schemas/simulation_ports.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    raw = get_capability(PARENT).data["simulation_ports"]
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda item: list(item.path))
    assert not errors, [error.message for error in errors]


def test_v7_module_replacement_contract_exposes_dependency_and_internal_slots() -> None:
    contract = assembly_contract(PARENT)
    replacements = contract["module_replacements"]
    assert replacements["schema_version"] == "sat-sim.module-replacements.v2"
    assert replacements["source"] == "explicit-v7"
    assert replacements["validation"]["ok"] is True
    assert len(replacements["fingerprint"]) == 64
    assert contract["replaceable_module_count"] == 3

    slot = next(item for item in replacements["slots"] if item["module_alias"] == "orbit_environment")
    assert slot["replaceable"] is True
    assert slot["slot_kind"] == "dependency"
    assert slot["interface_id"] == "sat-sim.interface.orbit-shadow-source.v1"
    by_id = {item["capability_id"]: item for item in slot["candidates"]}
    assert by_id["orbit_environment.medium_fidelity.v1"]["compatible"] is True
    assert by_id["orbit_environment.medium_fidelity.v1"]["is_baseline"] is True
    assert by_id["orbit_environment.orbit_fidelity.v1"]["compatible"] is True
    assert by_id["orbit_environment.orbit_fidelity.v1"]["is_baseline"] is False

    eps = next(item for item in replacements["slots"] if item["module_alias"] == "eps")
    thermal = next(item for item in replacements["slots"] if item["module_alias"] == "thermal")
    assert eps["slot_kind"] == "parent_managed_internal"
    assert eps["baseline_capability_id"] is None
    assert eps["candidates"][0]["capability_id"] == "subsystem.eps.source_native.v1"
    assert eps["candidates"][0]["compatible"] is True
    assert thermal["slot_kind"] == "parent_managed_internal"
    assert thermal["baseline_capability_id"] is None
    assert thermal["candidates"][0]["capability_id"] == "subsystem.thermal.source_native.v1"
    assert thermal["candidates"][0]["compatible"] is True


def test_v6_registered_orbit_module_replacement_validates_and_unregistered_candidate_is_rejected() -> None:
    contract = assembly_contract(PARENT)
    graph = copy.deepcopy(contract["default_graph"])
    orbit = next(node for node in graph["nodes"] if node["moduleAlias"] == "orbit_environment")
    orbit["capabilityId"] = "orbit_environment.orbit_fidelity.v1"
    result = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert result.ok is True
    assert result.replacement_count == 1
    assert result.selected_modules["orbit_environment"] == "orbit_environment.orbit_fidelity.v1"
    assert "ASSEMBLY_MODULE_REPLACEMENT_ACTIVE" in {item.code for item in result.warnings}

    orbit["capabilityId"] = "orbit_environment.basilisk_hf.v1"
    rejected = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert rejected.ok is False
    assert "ASSEMBLY_MODULE_REPLACEMENT_INCOMPATIBLE" in {item.code for item in rejected.errors}


def test_v5_graph_remains_compatible_but_cannot_activate_v6_replacement() -> None:
    contract = assembly_contract(PARENT)
    graph = copy.deepcopy(contract["default_graph"])
    graph["schema_version"] = "sat-sim.module-assembly.v2"
    graph.pop("module_contract", None)
    baseline = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert baseline.ok is True
    assert "ASSEMBLY_SCHEMA_LEGACY_V5" in {item.code for item in baseline.warnings}

    orbit = next(node for node in graph["nodes"] if node["moduleAlias"] == "orbit_environment")
    orbit["capabilityId"] = "orbit_environment.orbit_fidelity.v1"
    replacement = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert replacement.ok is False
    assert "ASSEMBLY_REPLACEMENT_REQUIRES_V6_SCHEMA" in {item.code for item in replacement.errors}


def test_v6_api_binds_replacement_into_taskspec_and_parent_runtime_uses_it(tmp_path: Path) -> None:
    from sat_sim.adapters.whole_spacecraft_power_thermal_orbit_coupled import PowerThermalOrbitCoupledAdapter

    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{PARENT}").json()["assembly"]
        replacement_contract = client.get(f"/visual-composer/module-replacements/{PARENT}")
        assert replacement_contract.status_code == 200
        assert replacement_contract.json()["ok"] is True

        graph = copy.deepcopy(contract["default_graph"])
        orbit = next(node for node in graph["nodes"] if node["moduleAlias"] == "orbit_environment")
        orbit["capabilityId"] = "orbit_environment.orbit_fidelity.v1"
        form_data = client.get(f"/forms/capabilities/{PARENT}").json()["form_schema"]["default_form"]
        form_data.setdefault("simulation", {})["duration_s"] = 120.0
        form_data["simulation"]["sample_s"] = 60.0

        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form_data, "require_all_bindings": True},
        )
        assert compiled.status_code == 200
        payload = compiled.json()
        assert payload["ok"] is True
        assert payload["assembly_validation"]["replacement_count"] == 1
        task_spec = payload["result"]["task_spec"]
        visual = task_spec["metadata"]["visual_assembly"]
        assert visual["module_selections"]["orbit_environment"] == "orbit_environment.orbit_fidelity.v1"
        assert len(visual["module_contract_fingerprint"]) == 64

        runtime_result = PowerThermalOrbitCoupledAdapter().run(task_spec)
        assert runtime_result.summary["orbit_provider_capability_id"] == "orbit_environment.orbit_fidelity.v1"
        assert runtime_result.summary["assembly_replacement_active"] is True
        assert runtime_result.trace_rows
        assert all(row["environment.orbit_provider_capability_id"] == "orbit_environment.orbit_fidelity.v1" for row in runtime_result.trace_rows)

        exported = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": graph, "task_spec": task_spec, "require_all_bindings": True},
        )
        assert exported.status_code == 200
        code_payload = exported.json()
        assert code_payload["ok"] is True
        assert code_payload["provenance"]["replacement_count"] == 1
        assert code_payload["provenance"]["selected_modules"]["orbit_environment"] == "orbit_environment.orbit_fidelity.v1"
        assert "validate_assembly_task_spec_binding" in code_payload["code"]


def test_v6_export_rejects_taskspec_module_selection_drift(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{PARENT}").json()["assembly"]
        baseline_graph = copy.deepcopy(contract["default_graph"])
        form_data = client.get(f"/forms/capabilities/{PARENT}").json()["form_schema"]["default_form"]
        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": baseline_graph, "form_data": form_data},
        ).json()
        assert compiled["ok"] is True
        baseline_task_spec = compiled["result"]["task_spec"]

        replacement_graph = copy.deepcopy(baseline_graph)
        orbit = next(node for node in replacement_graph["nodes"] if node["moduleAlias"] == "orbit_environment")
        orbit["capabilityId"] = "orbit_environment.orbit_fidelity.v1"
        exported = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": replacement_graph, "task_spec": baseline_task_spec},
        )
        assert exported.status_code == 200
        payload = exported.json()
        assert payload["ok"] is False
        assert payload["assembly_validation"]["ok"] is True
        assert payload["assembly_task_binding"]["ok"] is False
        assert "ASSEMBLY_TASK_MODULE_SELECTION_MISMATCH" in {item["code"] for item in payload["assembly_task_binding"]["errors"]}


def test_v6_module_interfaces_match_packaged_json_schema() -> None:
    from jsonschema import Draft202012Validator
    from sat_sim.capability_registry import get_capability

    schema_path = Path("src/sat_sim/schemas/simulation_module_interface.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for capability_id in (
        "orbit_environment.medium_fidelity.v1",
        "orbit_environment.orbit_fidelity.v1",
        "subsystem.eps.source_native.v1",
        "subsystem.thermal.source_native.v1",
    ):
        raw = get_capability(capability_id).data["simulation_module_interface"]
        errors = sorted(validator.iter_errors(raw), key=lambda item: list(item.path))
        assert not errors, [f"{capability_id}: {error.message}" for error in errors]


def test_v7_module_library_exposes_only_registered_interface_modules(tmp_path: Path) -> None:
    library = module_library_catalog()
    by_id = {item["capability_id"]: item for item in library["modules"]}
    assert library["schema_version"] == "sat-sim.module-library.v1"
    assert "subsystem.eps.source_native.v1" in by_id
    assert "subsystem.thermal.source_native.v1" in by_id
    assert "orbit_environment.orbit_fidelity.v1" in by_id
    assert by_id["subsystem.eps.source_native.v1"]["interface_id"] == "sat-sim.interface.eps-coupled-power.v1"
    assert by_id["subsystem.thermal.source_native.v1"]["interface_id"] == "sat-sim.interface.thermal-coupled-lumped.v1"
    assert all(item["compatible_contract"] is True for item in by_id.values())

    with _client(tmp_path) as client:
        response = client.get("/visual-composer/module-library")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["module_library"]["module_count"] >= 3


def test_v6_baseline_graph_remains_compatible_but_cannot_insert_v7_internal_module() -> None:
    contract = assembly_contract(PARENT)
    graph = copy.deepcopy(contract["default_graph"])
    graph["schema_version"] = V6_ASSEMBLY_GRAPH_SCHEMA_VERSION
    baseline = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert baseline.ok is True
    assert "ASSEMBLY_SCHEMA_LEGACY_V6" in {item.code for item in baseline.warnings}

    eps = next(node for node in graph["nodes"] if node["moduleAlias"] == "eps")
    eps["capabilityId"] = "subsystem.eps.source_native.v1"
    replacement = validate_assembly_graph(graph, expected_parent_capability_id=PARENT)
    assert replacement.ok is False
    assert "ASSEMBLY_INTERNAL_REPLACEMENT_REQUIRES_V7_SCHEMA" in {item.code for item in replacement.errors}


def test_v7_internal_eps_thermal_replacements_compile_and_run_source_native_models(tmp_path: Path) -> None:
    from sat_sim.adapters.whole_spacecraft_power_thermal_orbit_coupled import PowerThermalOrbitCoupledAdapter

    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{PARENT}").json()["assembly"]
        graph = copy.deepcopy(contract["default_graph"])
        next(node for node in graph["nodes"] if node["moduleAlias"] == "eps")["capabilityId"] = "subsystem.eps.source_native.v1"
        next(node for node in graph["nodes"] if node["moduleAlias"] == "thermal")["capabilityId"] = "subsystem.thermal.source_native.v1"
        form_data = client.get(f"/forms/capabilities/{PARENT}").json()["form_schema"]["default_form"]
        form_data.setdefault("simulation", {})["duration_s"] = 180.0
        form_data["simulation"]["sample_s"] = 60.0

        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form_data, "require_all_bindings": True},
        )
        assert compiled.status_code == 200
        payload = compiled.json()
        assert payload["ok"] is True
        assert payload["assembly_validation"]["replacement_count"] == 2
        task_spec = payload["result"]["task_spec"]
        selections = task_spec["metadata"]["visual_assembly"]["module_selections"]
        assert selections["eps"] == "subsystem.eps.source_native.v1"
        assert selections["thermal"] == "subsystem.thermal.source_native.v1"

        runtime = PowerThermalOrbitCoupledAdapter().run(task_spec)
        assert runtime.summary["eps_provider_capability_id"] == "subsystem.eps.source_native.v1"
        assert runtime.summary["thermal_provider_capability_id"] == "subsystem.thermal.source_native.v1"
        assert runtime.summary["assembly_replacement_active"] is True
        assert runtime.trace_rows
        assert all(row["eps.module_provider_capability_id"] == "subsystem.eps.source_native.v1" for row in runtime.trace_rows)
        assert all(row["thermal.module_provider_capability_id"] == "subsystem.thermal.source_native.v1" for row in runtime.trace_rows)

        exported = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": graph, "task_spec": task_spec, "require_all_bindings": True},
        ).json()
        assert exported["ok"] is True
        assert exported["provenance"]["replacement_count"] == 2
        assert exported["provenance"]["selected_modules"]["eps"] == "subsystem.eps.source_native.v1"
        assert exported["provenance"]["selected_modules"]["thermal"] == "subsystem.thermal.source_native.v1"
        compile(exported["code"], exported["filename"], "exec")


def test_v7_export_rejects_internal_module_selection_drift(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{PARENT}").json()["assembly"]
        graph = copy.deepcopy(contract["default_graph"])
        next(node for node in graph["nodes"] if node["moduleAlias"] == "eps")["capabilityId"] = "subsystem.eps.source_native.v1"
        form_data = client.get(f"/forms/capabilities/{PARENT}").json()["form_schema"]["default_form"]
        compiled = client.post("/tasks/compile-assembly", json={"assembly_graph": graph, "form_data": form_data}).json()
        assert compiled["ok"] is True
        task_spec = compiled["result"]["task_spec"]
        task_spec["metadata"]["visual_assembly"]["module_selections"]["eps"] = None
        exported = client.post("/tasks/export-assembly-script", json={"assembly_graph": graph, "task_spec": task_spec})
        assert exported.status_code == 200
        payload = exported.json()
        assert payload["ok"] is False
        assert "ASSEMBLY_TASK_MODULE_SELECTION_MISMATCH" in {item["code"] for item in payload["assembly_task_binding"]["errors"]}


def test_v8_adcs_fidelity_exposes_reaction_wheel_internal_slot() -> None:
    capability_id = "subsystem.adcs_fidelity.v1"
    contract = assembly_contract(capability_id)
    assert contract["port_contract_valid"] is True
    assert contract["module_contract_valid"] is True
    modules = {item["alias"]: item for item in contract["modules"]}
    assert {"wheel_allocator", "reaction_wheel", "rigid_body", "saturation_monitor"}.issubset(modules)
    assert modules["reaction_wheel"]["replaceable"] is True

    slots = {item["module_alias"]: item for item in contract["module_replacements"]["slots"]}
    slot = slots["reaction_wheel"]
    assert slot["slot_kind"] == "parent_managed_internal"
    assert slot["interface_id"] == "sat-sim.interface.reaction-wheel-dynamics.v1"
    candidate = next(item for item in slot["candidates"] if item["capability_id"] == "component.reaction_wheel.v1")
    assert candidate["compatible"] is True

    ports = {item["port_id"]: item for item in contract["simulation_ports"]["ports"]}
    assert ports["reaction_wheel.in.motor_torque_nm"]["payload"]["unit"] == "N*m"
    assert ports["reaction_wheel.out.wheel_speed_rad_s"]["payload"]["unit"] == "rad/s"
    assert ports["reaction_wheel.out.wheel_speed_rad_s"]["direct_feedthrough"] is False
    assert ports["wheel_allocator.out.motor_torque_nm"]["fan_out"] == "many"
    assert "reaction_wheel.out.applied_motor_torque_nm" not in ports
    binding_ids = {item["binding_id"] for item in contract["simulation_ports"]["bindings"]}
    assert "allocator_to_rigid_body" in binding_ids
    result = validate_assembly_graph(contract["default_graph"], expected_parent_capability_id=capability_id)
    assert result.ok is True
    assert result.required_binding_count == 3


def test_v8_reaction_wheel_module_is_in_registered_library_and_schema_valid() -> None:
    from jsonschema import Draft202012Validator
    from sat_sim.capability_registry import get_capability

    library = module_library_catalog()
    by_id = {item["capability_id"]: item for item in library["modules"]}
    item = by_id["component.reaction_wheel.v1"]
    assert item["interface_id"] == "sat-sim.interface.reaction-wheel-dynamics.v1"
    assert item["compatible_contract"] is True
    assert {port["port_id"] for port in item["ports"]} == {"in.motor_torque_nm", "out.wheel_speed_rad_s"}

    schema = json.loads(Path("src/sat_sim/schemas/simulation_module_interface.schema.json").read_text(encoding="utf-8"))
    raw = get_capability("component.reaction_wheel.v1").data["simulation_module_interface"]
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda error: list(error.path))
    assert not errors, [error.message for error in errors]


def test_v8_adcs_reaction_wheel_insertion_compiles_runs_and_exports(tmp_path: Path) -> None:
    from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter

    capability_id = "subsystem.adcs_fidelity.v1"
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{capability_id}").json()["assembly"]
        graph = copy.deepcopy(contract["default_graph"])
        next(node for node in graph["nodes"] if node["moduleAlias"] == "reaction_wheel")["capabilityId"] = "component.reaction_wheel.v1"
        form_data = client.get(f"/forms/capabilities/{capability_id}").json()["form_schema"]["default_form"]
        form_data["simulation"]["duration_s"] = 30.0
        form_data["simulation"]["sample_s"] = 5.0
        form_data["simulation"]["step_s"] = 0.25

        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form_data, "require_all_bindings": True},
        ).json()
        assert compiled["ok"] is True
        assert compiled["assembly_validation"]["replacement_count"] == 1
        task_spec = compiled["result"]["task_spec"]
        assert task_spec["metadata"]["visual_assembly"]["module_selections"]["reaction_wheel"] == "component.reaction_wheel.v1"

        runtime = AdcsFidelityAdapter().run(task_spec)
        assert runtime.summary["reaction_wheel_provider_capability_id"] == "component.reaction_wheel.v1"
        assert runtime.summary["assembly_replacement_active"] is True
        assert runtime.trace_rows
        assert all(row["adcs.rw.provider_capability_id"] == "component.reaction_wheel.v1" for row in runtime.trace_rows)

        exported = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": graph, "task_spec": task_spec, "require_all_bindings": True},
        ).json()
        assert exported["ok"] is True
        assert exported["provenance"]["replacement_count"] == 1
        assert exported["provenance"]["selected_modules"]["reaction_wheel"] == "component.reaction_wheel.v1"
        compile(exported["code"], exported["filename"], "exec")

        # V13 Scope probes are read-only project annotations.  Adding one must
        # neither change physical validation nor change generated runtime code.
        scoped_graph = copy.deepcopy(graph)
        scoped_graph["scopes"] = [
            {
                "id": "scope-rw",
                "name": "Wheel speed",
                "x": 860,
                "y": 90,
                "probes": [
                    {
                        "id": "probe-rw-speed",
                        "sourceNode": "module-reaction_wheel",
                        "sourcePort": "reaction_wheel.out.wheel_speed_rad_s",
                    }
                ],
            }
        ]
        scoped_compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": scoped_graph, "form_data": form_data, "require_all_bindings": True},
        ).json()
        assert scoped_compiled["ok"] is True
        assert scoped_compiled["assembly_validation"]["replacement_count"] == 1
        assert scoped_compiled["assembly_validation"]["node_count"] == compiled["assembly_validation"]["node_count"]
        assert scoped_compiled["assembly_validation"]["edge_count"] == compiled["assembly_validation"]["edge_count"]
        assert scoped_compiled["result"]["task_spec"] == task_spec

        scoped_export = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": scoped_graph, "task_spec": task_spec, "require_all_bindings": True},
        ).json()
        assert scoped_export["ok"] is True
        assert scoped_export["code"] == exported["code"]
        assert "scope-rw" not in scoped_export["code"]


def test_v8_adcs_runtime_rejects_unregistered_reaction_wheel_selection() -> None:
    from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter

    spec = {
        "task_id": "v8-invalid-provider",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_fidelity.v1",
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "simulation": {"duration_s": 10.0, "sample_s": 5.0, "step_s": 0.25},
        "parameters": {},
        "metadata": {
            "visual_assembly": {
                "parent_capability_id": "subsystem.adcs_fidelity.v1",
                "module_selections": {"reaction_wheel": "component.cmg.v1"},
            }
        },
    }
    issues = AdcsFidelityAdapter().validate(spec)
    assert any(issue.code == "assembly_module_selection" for issue in issues)
