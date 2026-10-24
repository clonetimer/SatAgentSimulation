from sat_sim.validation.cross_level_owner_audit import parameter_reads


def test_only_literal_params_reads_are_reported():
    source = """
def f(params, values, other):
    a = params.get('wheel_inertia_kg_m2', 0.1)
    b = params['command_torque_nm']
    c = values.get('not_a_parameter')
    d = other['also_not_a_parameter']
    e = params.get(dynamic_key)
"""
    assert parameter_reads(source) == {"wheel_inertia_kg_m2", "command_torque_nm"}


def test_cross_level_audit_conservatively_refuses_physics_claim():
    from sat_sim.validation.cross_level_owner_audit import build_cross_level_owner_audit
    report = build_cross_level_owner_audit()
    assert report["physics_status"] == "NOT_VERIFIED"
    assert report["rw_independent_entry"]["native_reuse_verified"] is False
    assert report["rw_independent_entry"]["basilisk_required"] is False
    rw = [r for r in report["parameter_rows"] if r["capability_id"] == "component.reaction_wheel.v1"]
    assert rw
    assert {r["parameter"] for r in rw} >= {"command_torque_nm", "wheel_inertia_kg_m2"}
    assert all(r["dynamic_effect_evidence"] == "not_provided" for r in rw)
