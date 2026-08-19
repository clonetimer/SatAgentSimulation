from __future__ import annotations

import pytest

from sat_sim_kernel import (
    ActionBinding,
    BindingSet,
    DuplicateIdError,
    EffectBinding,
    EvidenceBinding,
    ExpressionSpec,
    FieldMapping,
    LocalId,
    ModelPath,
    ObjectRef,
    PropertyBinding,
)


def test_four_binding_types_form_one_stable_binding_set() -> None:
    property_binding = PropertyBinding(
        LocalId("pointing_status"),
        ObjectRef("spacecraft.attitude.pointing_status"),
        ModelPath("spacecraft.adcs.pointing_error_deg"),
        ExpressionSpec("value < 0.5"),
    )
    action_binding = ActionBinding(
        LocalId("safe_mode"),
        ObjectRef("spacecraft.attitude.enter_safe_mode"),
        ModelPath("spacecraft.adcs.mode_command"),
        value_expression=ExpressionSpec("'SAFE'", variables=()),
    )
    effect_binding = EffectBinding(
        LocalId("inject_jamming"),
        ObjectRef("spacecraft.attitude.inject_rw_jamming"),
        LocalId("rw_jamming"),
        LocalId("rw_set"),
        parameter_mappings=(FieldMapping(LocalId("wheel_index"), LocalId("wheel_index")),),
    )
    evidence_binding = EvidenceBinding(
        LocalId("pointing_evidence"),
        LocalId("pointing_error_trace"),
        ModelPath("spacecraft.adcs.pointing_error_deg"),
        ObjectRef("spacecraft.attitude.evidence.pointing_error"),
    )
    bindings = BindingSet(
        LocalId("attitude_bindings"),
        properties=(property_binding,),
        actions=(action_binding,),
        effects=(effect_binding,),
        evidence=(evidence_binding,),
    )
    assert len(bindings.content_sha256) == 64


def test_binding_ids_are_unique_across_all_binding_types() -> None:
    duplicate_id = LocalId("same")
    with pytest.raises(DuplicateIdError):
        BindingSet(
            LocalId("bad"),
            properties=(PropertyBinding(duplicate_id, ObjectRef("spacecraft.a.value"), ModelPath("model.a.value")),),
            evidence=(EvidenceBinding(duplicate_id, LocalId("trace"), ModelPath("model.a.value"), ObjectRef("spacecraft.a.evidence")),),
        )
