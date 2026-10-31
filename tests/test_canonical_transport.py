"""Pure JSON/schema transport checks; no model, physics or service execution."""
import copy
import json
import math

import pytest

import sat_sim.canonical_transport as transport
from sat_sim.canonical_transport import CanonicalTransportError, parse_canonical_transport, task_spec_json
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.task_spec import spec_sha256


def _base():
    # Public v1 schema, deliberately omit optional/default fields. No archive input.
    return {
        "schema_version": "1.0.0",
        "task": {"id": "transport_example"},
        "simulation": {"level": "component", "duration_s": 10.0, "sample_s": 1.0},
        "outputs": {"output_root": "reports/transport-example"},
        "model": {"config": {"radius_m": 7000000.0, "zero": -0.0}},
        "metadata": {"nested": {"value": 1.0}},
    }


def _reject(code, spec, raw=transport._MISSING):
    with pytest.raises(CanonicalTransportError) as caught:
        if raw is transport._MISSING:
            parse_canonical_transport(spec)
        else:
            parse_canonical_transport(spec, raw)
    assert caught.value.reason_code == "CANONICAL_TRANSPORT_" + code
    assert str(caught.value).startswith(caught.value.reason_code + ":")


def test_browser_integral_float_loss_keeps_authoritative_v1_hash_and_negative_zero():
    source = _base()
    raw = task_spec_json(source)
    browser = copy.deepcopy(source)
    browser["model"]["config"] = {"radius_m": 7000000, "zero": 0}
    browser["metadata"]["nested"]["value"] = 1
    actual = parse_canonical_transport(browser, raw)
    assert type(actual["model"]["config"]["radius_m"]) is float
    assert math.copysign(1, actual["model"]["config"]["zero"]) == -1
    assert spec_sha256(actual) == spec_sha256(canonicalize_task_spec(source))
    assert spec_sha256(actual) != spec_sha256(canonicalize_task_spec(browser))


def test_explicit_editor_number_type_edit_uses_new_text_and_changes_existing_hash():
    original = _base()
    edited = copy.deepcopy(original)
    edited["model"]["config"]["radius_m"] = 7000000
    actual = parse_canonical_transport(edited, task_spec_json(edited))
    assert type(actual["model"]["config"]["radius_m"]) is int
    assert spec_sha256(actual) != spec_sha256(canonicalize_task_spec(original))


def test_writer_validates_without_inserting_defaults_or_changing_structure():
    source = _base()
    before = copy.deepcopy(source)
    encoded = task_spec_json(source)
    assert json.loads(encoded) == source == before
    assert "mission" not in json.loads(encoded)
    assert "7000000.0" in encoded and '"zero": -0.0' in encoded
    assert parse_canonical_transport(source, encoded) == canonicalize_task_spec(source)


def test_legacy_writer_preserves_original_document_and_parser_migrates_after_binding():
    legacy = to_runtime_task_spec(canonicalize_task_spec(_base()))
    raw = task_spec_json(legacy)
    assert json.loads(raw) == legacy
    assert json.loads(raw)["schema_version"] == "0.1.0"
    assert parse_canonical_transport(legacy, raw) == canonicalize_task_spec(legacy)


@pytest.mark.parametrize("source", [{}, {"simulation": {}}, {"schema_version": "0.1.0", "simulation": {}, "task_type": "component"}, {"schema_version": "0.1.0", "task_id": "", "simulation": {}, "task_type": "component"}, {"schema_version": "0.1.0", "task_id": "partial_task", "task_type": "component"}, {"schema_version": "0.1.0", "task_id": "partial_task", "simulation": {}}, {"task_id": "partial_task", "simulation": {}, "task_type": True}])
def test_explicit_text_and_writer_reject_empty_or_unidentified_partial_documents(source):
    _reject("SCHEMA_INVALID", None, json.dumps(source))
    _reject("SCHEMA_INVALID", source, json.dumps(source))
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_SCHEMA_INVALID"):
        task_spec_json(source)


def test_omitted_text_retains_legacy_empty_object_migration_compatibility():
    assert parse_canonical_transport({}) == canonicalize_task_spec({})


def _old_task_center_alias():
    # Same public shape/version as the existing TaskCenter API regression,
    # without importing its server fixtures or depending on archived artifacts.
    return {
        "schema_version": "1.0",
        "task": {"id": "task_versioned", "name": "Legacy versioned task"},
        "simulation": {"duration_s": 10.0, "sample_s": 1.0, "level": "component"},
        "model": {"capability_id": "component.reaction_wheel.v1", "target": {"mode": "nominal"}},
        "parameters": {"profile": "demo", "values": {}},
        "events": {"faults": [], "degradations": []},
        "outputs": {"qoi": ["qoi.adcs.reaction_wheel.final_speed_rad_s_0"], "plots": ["adcs.reaction_wheel.speed_rad_s_0"], "output_root": "datasets/task_versioned"},
        "assurance": {"parameter_profile": "demo"},
    }


def test_object_only_old_task_center_1_0_alias_preserves_existing_migration_contract():
    source = _old_task_center_alias()
    snapshot = copy.deepcopy(source)
    assert parse_canonical_transport(source) == canonicalize_task_spec(source)
    assert source == snapshot


def test_old_object_alias_does_not_relax_explicit_text_or_writer_contract():
    source = _old_task_center_alias()
    raw = json.dumps(source)
    _reject("SCHEMA_INVALID", source, raw)
    _reject("SCHEMA_INVALID", None, raw)
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_SCHEMA_INVALID"):
        task_spec_json(source)


@pytest.mark.parametrize("declared_version", [True, False])
@pytest.mark.parametrize("discriminator", ["task_type", "capability_id"])
def test_identifiable_legacy_alias_documents_with_or_without_version_stay_supported(declared_version, discriminator):
    source = {
        "task_id": "legacy_transport_alias",
        "simulation": {"duration_s": 15.0, "sample_s": 1.0, "seed": 7},
        "parameters": {"heat_capacity_j_k": 50.0},
        "outputs": {"output_root": "reports/legacy-alias", "record_fields": ["temperature_k"]},
        discriminator: "component" if discriminator == "task_type" else "component.thermal_node.v1",
    }
    if declared_version:
        source["schema_version"] = "0.1.0"
    raw = task_spec_json(source)
    assert json.loads(raw) == source
    actual = parse_canonical_transport(source, raw)
    assert actual == canonicalize_task_spec(source)
    assert actual["task"]["id"] == source["task_id"]
    assert actual["simulation"]["random_seed"] == 7
    assert actual["outputs"]["qoi"] == ["temperature_k"]


def test_object_only_legacy_mode_still_canonicalizes_without_mutation():
    original = _base()
    snapshot = copy.deepcopy(original)
    assert parse_canonical_transport(original) == canonicalize_task_spec(snapshot)
    assert original == snapshot


@pytest.mark.parametrize("raw,code", [(None, "RAW_TYPE"), (True, "RAW_TYPE"), (1, "RAW_TYPE"), ({}, "RAW_TYPE"), ([], "RAW_TYPE"), (b"{}", "RAW_TYPE"), ("", "RAW_EMPTY"), (" \t\n", "RAW_EMPTY")])
def test_explicit_invalid_sidecar_never_falls_back_to_valid_object(raw, code):
    _reject(code, _base(), raw)


@pytest.mark.parametrize("before,after", [(True, 1), (False, 0), (1, True), ("1", 1), (1, "1"), (None, False), ([], {}), ([1, 2], [2, 1]), ([1], [1, 2]), ({"a": 1}, {"b": 1})])
def test_binding_compares_json_types_structure_and_values_before_schema_coercion(before, after):
    source = _base()
    source["metadata"]["value"] = before
    companion = copy.deepcopy(source)
    companion["metadata"]["value"] = after
    _reject("VALUE_MISMATCH", companion, task_spec_json(source))


def test_known_schema_numeric_coercion_cannot_hide_raw_object_mismatch():
    source = _base()
    raw = json.dumps(source)
    companion = copy.deepcopy(source)
    companion["simulation"]["duration_s"] = "10.0"
    _reject("VALUE_MISMATCH", companion, raw)


@pytest.mark.parametrize("value", [True, "10"])
def test_consistent_pair_cannot_use_model_coercion_to_bypass_numeric_json_schema(value):
    source = _base()
    source["simulation"]["duration_s"] = value
    # The legacy object-only API deliberately retains its previous coercion.
    assert parse_canonical_transport(source)["simulation"]["duration_s"] == float(value)
    _reject("SCHEMA_INVALID", source, json.dumps(source))
    _reject("SCHEMA_INVALID", None, json.dumps(source))
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_SCHEMA_INVALID"):
        task_spec_json(source)


def test_binding_mismatch_is_checked_before_schema_coercion_or_strict_schema_error():
    source = _base()
    source["simulation"]["duration_s"] = True
    companion = copy.deepcopy(source)
    companion["simulation"]["duration_s"] = 1
    _reject("VALUE_MISMATCH", companion, json.dumps(source))


def test_consistent_pair_rejects_number_in_boolean_schema_field():
    source = _base()
    source["outputs"]["include_trace"] = 1
    _reject("SCHEMA_INVALID", source, json.dumps(source))
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_SCHEMA_INVALID"):
        task_spec_json(source)


def test_schema_defaults_cannot_hide_different_raw_object_structure():
    source = _base()
    _reject("VALUE_MISMATCH", canonicalize_task_spec(source), task_spec_json(source))


@pytest.mark.parametrize("number", [2**53, 2**53 + 1, -(2**53), float(2**53), 1e20, -1e20])
def test_paired_objects_reject_all_unsafe_integer_values_including_integral_floats(number):
    source = _base()
    source["metadata"]["value"] = number
    raw = task_spec_json(source)
    _reject("UNSAFE_INTEGER", source, raw)
    actual = parse_canonical_transport(None, raw)
    assert actual["metadata"]["value"] == number
    assert type(actual["metadata"]["value"]) is type(number)


def test_rounded_unsafe_raw_integer_cannot_bind_lossy_browser_object():
    source = _base()
    source["metadata"]["value"] = 9007199254740993
    companion = copy.deepcopy(source)
    companion["metadata"]["value"] = 9007199254740992
    _reject("UNSAFE_INTEGER", companion, task_spec_json(source))


@pytest.mark.parametrize("number", [2**53 - 1, -(2**53 - 1), 0.25, -0.25, 1e-200])
def test_safe_integers_and_nonintegral_floats_bind(number):
    source = _base()
    source["metadata"]["value"] = number
    assert parse_canonical_transport(source, task_spec_json(source))["metadata"]["value"] == number


@pytest.mark.parametrize("raw", ['{"task":{},"task":{}}', '{"metadata":{"x":1,"x":2}}'])
def test_duplicate_keys_are_rejected_at_any_nesting(raw):
    _reject("DUPLICATE_KEY", None, raw)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309"])
def test_nonfinite_json_numbers_and_float_overflow_are_rejected(token):
    _reject("NON_FINITE", None, '{"metadata":{"value":' + token + "}}")


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_object_metadata_rejected_even_without_sidecar(number):
    source = _base()
    source["metadata"]["value"] = number
    _reject("NON_FINITE", source)
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_NON_FINITE"):
        task_spec_json(source)


@pytest.mark.parametrize("raw", ["{", "null", "[]", "true", "7", '{"x":1,}'])
def test_invalid_or_nonobject_json_rejected(raw):
    _reject("JSON_INVALID", None, raw)


@pytest.mark.parametrize("source", [{"schema_version": "1.0.0"}, {"schema_version": "1.0.0", "task": {"id": "a"}}, {"schema_version": "unknown"}])
def test_valid_json_must_still_pass_task_schema(source):
    _reject("SCHEMA_INVALID", source, json.dumps(source))
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_SCHEMA_INVALID"):
        task_spec_json(source)


@pytest.mark.parametrize("companion", [[], True, 1, "{}"])
def test_companion_must_be_mapping(companion):
    _reject("OBJECT_INVALID", companion, task_spec_json(_base()))


def test_no_task_or_text_is_not_a_success():
    _reject("MISSING_TASK_SPEC", None)


@pytest.mark.parametrize("value", [{1: "numeric-key"}, (1, 2), {1, 2}])
def test_python_nonjson_values_do_not_gain_coerced_binding(value):
    source = _base()
    source["metadata"]["value"] = value
    _reject("OBJECT_INVALID", source)


def test_cyclic_python_object_fails_with_stable_code():
    source = _base()
    source["metadata"]["self"] = source
    _reject("OBJECT_INVALID", source)


def test_raw_utf8_size_limit_counts_bytes_not_characters(monkeypatch):
    source = _base()
    source["metadata"]["label"] = "\u4e2d" * 40
    raw = task_spec_json(source)
    monkeypatch.setattr(transport, "MAX_JSON_BYTES", len(raw))
    assert len(raw.encode("utf-8")) > len(raw)
    _reject("RAW_TOO_LARGE", None, raw)
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_RAW_TOO_LARGE"):
        task_spec_json(source)


def test_two_document_budget_is_enforced_independently(monkeypatch):
    source = _base()
    raw = task_spec_json(source)
    compact = json.dumps(source, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    monkeypatch.setattr(transport, "MAX_PAYLOAD_BYTES", len(raw.encode("utf-8")) + len(compact.encode("utf-8")) - 1)
    _reject("PAYLOAD_TOO_LARGE", source, raw)
    assert parse_canonical_transport(None, raw) == canonicalize_task_spec(source)


def test_object_only_budget_is_bounded(monkeypatch):
    source = _base()
    monkeypatch.setattr(transport, "MAX_PAYLOAD_BYTES", 1)
    _reject("PAYLOAD_TOO_LARGE", source)


def test_stale_text_cannot_authorize_mutated_object_even_with_cached_hash_flags():
    source = _base()
    source["metadata"].update({"cached_validation": True, "plan_sha256": "a" * 64})
    raw = task_spec_json(source)
    companion = copy.deepcopy(source)
    companion["model"]["config"]["radius_m"] += 1
    _reject("VALUE_MISMATCH", companion, raw)


def test_new_task_identity_requires_matching_new_sidecar():
    source = _base()
    raw = task_spec_json(source)
    edited = copy.deepcopy(source)
    edited["task"]["id"] = "new_saved_task"
    _reject("VALUE_MISMATCH", edited, raw)
    assert parse_canonical_transport(edited, task_spec_json(edited))["task"]["id"] == "new_saved_task"


def test_unpaired_unicode_surrogates_rejected_before_emission():
    source = _base()
    source["metadata"]["value"] = "\ud800"
    _reject("OBJECT_INVALID", source)
    _reject("JSON_INVALID", None, "\ud800")
    raw = json.dumps(source)
    _reject("OBJECT_INVALID", None, raw)


def test_transport_limits_are_explicit_contract():
    assert transport.MAX_JSON_BYTES == 1024 * 1024
    assert transport.MAX_PAYLOAD_BYTES == 2 * 1024 * 1024


def test_writer_is_standard_multiline_text_without_changing_numbers_or_v1_identity():
    source = _base()
    source["metadata"]["numeric_array"] = [2, 2.0, -0.0, 0.125]
    raw = task_spec_json(source)
    assert raw == json.dumps(source, ensure_ascii=False, allow_nan=False, indent=2)
    assert raw.count("\n") > 10
    assert '\n  "task": {\n    "id": "transport_example"\n  }' in raw
    decoded = json.loads(raw)
    assert [type(value) for value in decoded["metadata"]["numeric_array"]] == [int, float, float, float]
    assert math.copysign(1.0, decoded["metadata"]["numeric_array"][2]) == -1.0
    assert spec_sha256(parse_canonical_transport(source, raw)) == spec_sha256(canonicalize_task_spec(source))


def test_writer_limit_counts_actual_pretty_utf8_including_indentation(monkeypatch):
    source = _base()
    source["metadata"]["labels"] = ["\u4e2d", "\u6587", "\u5b57"] * 10
    compact = json.dumps(source, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    pretty = json.dumps(source, ensure_ascii=False, allow_nan=False, indent=2)
    assert len(pretty.encode("utf-8")) > len(compact.encode("utf-8"))
    monkeypatch.setattr(transport, "MAX_JSON_BYTES", len(compact.encode("utf-8")))
    assert parse_canonical_transport(None, compact) == canonicalize_task_spec(source)
    with pytest.raises(CanonicalTransportError, match="CANONICAL_TRANSPORT_RAW_TOO_LARGE"):
        task_spec_json(source)
