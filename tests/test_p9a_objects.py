"""P9 object contracts are checked against the real product catalog."""
import importlib.util
import pytest
from sat_sim.workbench_catalog import workbench_presentation_catalog

@pytest.fixture(scope="module")
def catalog():
    return workbench_presentation_catalog()

def builder():
    assert importlib.util.find_spec("sat_sim.presentation_objects"), "P9 object contract is missing"
    from sat_sim.presentation_objects import build_scene_object_catalog
    return build_scene_object_catalog

def test_adcs_name_does_not_claim_orbit_control(catalog):
    adcs = next(x for x in catalog["objects"] if x["object_id"] == "subsystem.adcs")
    assert adcs["name_zh"] == "姿态控制分系统"

def test_c_semantics_and_physical_counts_are_preserved(catalog):
    result = builder()(catalog, None, service_enabled=False)
    objects = {x["object_id"]: x for x in result["objects"]}
    assert len(objects) == len(result["objects"]) == 33
    assert result["counts"]["subsystem"] == 6
    assert result["counts"]["component"] == 24
    assert result["counts"]["functional"] == 1
    assert objects["orbit_environment"]["object_kind"] == "environment"
    control = objects["orbit_control"]
    assert control["object_kind"] == "function"
    assert control["execution"]["capability_id"] is None
    assert not any(x["selectable"] for x in control["execution"]["modes"].values())
    assert "subsystem.propulsion" in control["related_object_ids"]
    assert objects["component.thruster"]["parent_object_id"] == "subsystem.propulsion"
    assert objects["component.reaction_wheel"]["parent_object_id"] == "subsystem.adcs"
    assert objects["orbit_control"]["spatial"]["kind"] == "maneuver_plan"
    assert objects["orbit_environment"]["spatial"]["kind"] == "trajectory"
    assert objects["component.reaction_wheel"]["spatial"]["anchor_id"] is None
    for row in objects.values():
        parent = row["parent_object_id"]
        seen = {row["object_id"]}
        while parent:
            assert parent in objects and parent not in seen
            seen.add(parent)
            parent = objects[parent]["parent_object_id"]

def test_source_catalog_is_not_mutated(catalog):
    import copy
    before = copy.deepcopy(catalog)
    builder()(catalog, None, service_enabled=False)
    assert catalog == before

@pytest.mark.parametrize("enabled,policy,expected", [
    (False, {"creation": []}, "service_disabled"),
    (True, None, "discovery_unavailable"),
    (True, {"creation": []}, "not_registered"),
    (True, {"creation": [{"capability_id": "subsystem.adcs_unified_native.v1", "continuous": False, "supported": False, "reason_code": "SUPERVISED_NATIVE_ONLY_WHOLE_CONTINUOUS"}]}, "policy_blocked"),
])
def test_live_modes_fail_closed(catalog, enabled, policy, expected):
    rows = builder()(catalog, policy, service_enabled=enabled)["objects"]
    adcs = next(x for x in rows if x["object_id"] == "subsystem.adcs")
    mode = adcs["execution"]["modes"]["limited_realtime"]
    assert mode["status"] == expected
    assert mode["selectable"] is False

def test_approved_policy_does_not_create_physics_proof(catalog):
    policy = {"creation": [{"capability_id": "subsystem.adcs_unified_native.v1", "continuous": False, "supported": True, "reason_code": None}]}
    rows = builder()(catalog, policy, service_enabled=True)["objects"]
    adcs = next(x for x in rows if x["object_id"] == "subsystem.adcs")
    mode = adcs["execution"]["modes"]["limited_realtime"]
    assert mode["selectable"] is True
    assert mode["runtime_check_required"] is True
    assert not adcs["execution"]["modes"]["continuous_realtime"]["selectable"]

def test_policy_disabled_is_authoritative_even_with_stale_health(catalog):
    policy = {"enabled": False, "creation": [{"capability_id": "subsystem.adcs_unified_native.v1", "continuous": False, "supported": True}]}
    rows = builder()(catalog, policy, service_enabled=True)["objects"]
    adcs = next(x for x in rows if x["object_id"] == "subsystem.adcs")
    assert not adcs["execution"]["modes"]["limited_realtime"]["selectable"]

def test_public_scene_schema_validates_the_real_catalog(catalog):
    import json
    from importlib.resources import files
    import jsonschema
    path = files("sat_sim.schemas").joinpath("scene_object_catalog.schema.json")
    assert path.is_file(), "Scene JSON Schema is missing"
    jsonschema.validate(builder()(catalog, None, service_enabled=False), json.loads(path.read_text()))
