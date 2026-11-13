"""Audited single-machine parameter and telemetry naming dictionary."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

_DATA_FILES = (
    "workbench_quantity_dictionary_adcs.json",
    "workbench_quantity_dictionary_eps_thermal.json",
    "workbench_quantity_dictionary_comm_payload_propulsion.json",
)
_COMPONENTS: dict[str, dict[str, Any]] = {}
for _filename in _DATA_FILES:
    _data = json.loads(Path(__file__).with_name(_filename).read_text(encoding="utf-8"))
    if _data.get("schema_version") != "workbench-quantity-dictionary.v2":
        raise ValueError(f"QUANTITY_DICTIONARY_VERSION_INVALID:{_filename}")
    for _key, _row in _data.get("components", {}).items():
        if _key in _COMPONENTS:
            raise ValueError(f"QUANTITY_DICTIONARY_DUPLICATE_COMPONENT:{_key}")
        _COMPONENTS[_key] = _row
if len(_COMPONENTS) != 24:
    raise ValueError(f"QUANTITY_DICTIONARY_COMPONENT_COUNT_INVALID:{len(_COMPONENTS)}")
DICTIONARY_VERSION = "workbench-quantity-dictionary.v2"
_OUTPUTS: dict[str, list[tuple[str, str, str | None, str | None, str, str | None]]] = {}
_PARAMETERS: dict[str, dict[str, tuple[str, str]]] = {}
for _key, _row in _COMPONENTS.items():
    _capability = f"component.{_key}.v1"
    _PARAMETERS[_capability] = {str(name): (str(label), str(unit)) for name, label, unit in _row["parameters"]}
    for _output in _row["outputs"]:
        _field, _label, _unit, _dtype, _kind, *_owner = _output
        _OUTPUTS.setdefault(str(_field), []).append((_key, str(_label), _unit, _dtype, str(_kind), _owner[0] if _owner else None))


def _component_key(capability_id: str) -> str | None:
    prefix, suffix = "component.", ".v1"
    if not capability_id.startswith(prefix) or not capability_id.endswith(suffix):
        return None
    key = capability_id[len(prefix):-len(suffix)]
    return key if key in _COMPONENTS else None


def component_catalog() -> list[dict[str, Any]]:
    """Return 24 component model categories; never claim these are installed counts."""
    return [{
        "object_id": f"component.{key}", "capability_id": f"component.{key}.v1",
        "display_name_zh": row["name"], "classification": row["classification"],
        "source_contract_path": f"src/sat_sim/capabilities/component.{key}.v1.yaml",
        "source_contract_blob": row["source_blob"], "inventory_count": None,
        "inventory_count_source": None,
    } for key, row in _COMPONENTS.items()]


def parameter_definition(capability_id: str, parameter_name: str) -> dict[str, Any] | None:
    key = _component_key(capability_id)
    row = _PARAMETERS.get(capability_id, {}).get(parameter_name)
    if key is None or row is None:
        return None
    return {
        "display_name_zh": f"{_COMPONENTS[key]['name']} · {row[0]}",
        "unit": row[1], "object_id": f"component.{key}",
        "source_capability_id": capability_id, "source_field": parameter_name,
        "source_contract_path": f"src/sat_sim/capabilities/{capability_id}.yaml",
        "source_contract_blob": _COMPONENTS[key]["source_blob"],
    }


def parameter_display_name(capability_id: str, parameter_name: str) -> str | None:
    row = parameter_definition(capability_id, parameter_name)
    return row["display_name_zh"] if row else None


def output_definition(source_field: str, *, capability_id: str | None = None,
                      kind: str | None = None) -> dict[str, Any] | None:
    for key, quantity, unit, dtype, section, owner in _OUTPUTS.get(source_field, []):
        source_capability = f"component.{key}.v1"
        if capability_id is not None and source_capability != capability_id:
            continue
        if kind is not None and section != kind:
            continue
        owner_name = owner or _COMPONENTS[key]["name"]
        return {
            "display_name_zh": f"{owner_name} · {quantity}", "unit": unit,
            "dtype": dtype, "kind": section, "object_id": f"component.{key}",
            "source_capability_id": source_capability, "source_field": source_field,
            "source_contract_path": f"src/sat_sim/capabilities/component.{key}.v1.yaml",
            "source_contract_blob": _COMPONENTS[key]["source_blob"],
        }
    return None


def output_display_name(source_field: str) -> str | None:
    row = output_definition(source_field)
    return row["display_name_zh"] if row else None


def dictionary_entries() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for capability_id, parameters in _PARAMETERS.items():
        for name in parameters:
            definition = parameter_definition(capability_id, name)
            rows.append({**definition, "kind": "parameter", "aggregation": "configuration"})
    for field, definitions in _OUTPUTS.items():
        for _key, _label, _unit, _dtype, kind, _owner in definitions:
            definition = output_definition(field, capability_id=f"component.{_key}.v1", kind=kind)
            rows.append({**definition, "aggregation": "source_contract"})
    return rows


def bind_quantity_dictionary(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate audited labels against exact source YAML field names, units and dtypes."""
    capability_id = str(contract.get("capability_id") or "")
    key = _component_key(capability_id)
    if key is None:
        raise ValueError("QUANTITY_CAPABILITY_NOT_REVIEWED")
    parameters = contract.get("parameters")
    outputs = contract.get("outputs")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, Mapping) or not isinstance(outputs, Mapping):
        raise ValueError("QUANTITY_INVALID_CONTRACT")
    entries: list[dict[str, Any]] = []
    reviewed_parameters: set[str] = set()
    for name, expected in _PARAMETERS.get(capability_id, {}).items():
        source = parameters.get(name)
        if not isinstance(source, Mapping):
            raise ValueError(f"QUANTITY_SOURCE_PARAMETER_MISSING:{name}")
        if source.get("unit", "none") != expected[1]:
            raise ValueError(f"QUANTITY_UNIT_MISMATCH:{name}")
        entries.append({**parameter_definition(capability_id, name),
                        "kind": "parameter", "source_definition": deepcopy(dict(source))})
        reviewed_parameters.add(name)
    rows_by_kind: dict[str, dict[str, Mapping[str, Any]]] = {}
    for section in ("trace", "summary"):
        rows = outputs.get(section)
        if not isinstance(rows, list):
            raise ValueError(f"QUANTITY_INVALID_SECTION:{section}")
        indexed: dict[str, Mapping[str, Any]] = {}
        for source in rows:
            if not isinstance(source, Mapping) or not isinstance(source.get("name"), str):
                raise ValueError(f"QUANTITY_INVALID_FIELD:{section}")
            name = source["name"]
            if name in indexed:
                raise ValueError(f"QUANTITY_DUPLICATE_FIELD:{name}")
            indexed[name] = source
        rows_by_kind[section] = indexed
    reviewed_outputs: set[tuple[str, str]] = set()
    for field, definitions in _OUTPUTS.items():
        for _key, _label, _unit, _dtype, kind, _owner in definitions:
            if _key != key:
                continue
            source = rows_by_kind[kind].get(field)
            if source is None:
                raise ValueError(f"QUANTITY_SOURCE_FIELD_MISSING:{kind}:{field}")
            definition = output_definition(field, capability_id=capability_id, kind=kind)
            if source.get("unit", "none") != definition["unit"]:
                raise ValueError(f"QUANTITY_UNIT_MISMATCH:{field}")
            if definition["dtype"] is not None and source.get("dtype") != definition["dtype"]:
                raise ValueError(f"QUANTITY_TYPE_MISMATCH:{field}")
            entries.append({**definition, "source_definition": deepcopy(dict(source))})
            reviewed_outputs.add((kind, field))
    ignored = {"time_s", "sample_index", "task_id", "case_id", "target_level", "target_name", "mode"}
    unreviewed_fields = sorted({
        field for kind, rows in rows_by_kind.items() for field in rows
        if (kind, field) not in reviewed_outputs and field not in ignored
        and not field.startswith("label.")
        and not any(token in field for token in ("*", "?", "["))
    })
    unreviewed_parameters = sorted(name for name in parameters
                                   if name not in reviewed_parameters and name != "effects")
    return {"schema_version": DICTIONARY_VERSION, "capability_id": capability_id,
            "entries": entries, "unreviewed_fields": unreviewed_fields,
            "unreviewed_parameters": unreviewed_parameters}


def bind_all_component_dictionaries(contracts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(contracts, Mapping):
        raise ValueError("QUANTITY_CONTRACTS_MUST_BE_MAPPING")
    bound = {}
    for capability_id, contract in contracts.items():
        if _component_key(str(capability_id)) is None:
            continue
        if str(contract.get("capability_id") or "") != capability_id:
            raise ValueError(f"QUANTITY_CAPABILITY_ID_MISMATCH:{capability_id}")
        bound[capability_id] = bind_quantity_dictionary(contract)
    return {"schema_version": DICTIONARY_VERSION, "capabilities": bound,
            "entry_count": sum(len(row["entries"]) for row in bound.values())}


def recorded_output_entries(available_fields: set[str]) -> list[dict[str, Any]]:
    if not isinstance(available_fields, (set, frozenset)) or any(not isinstance(x, str) for x in available_fields):
        raise ValueError("RECORDED_FIELDS_MUST_BE_STRING_SET")
    return [row for row in dictionary_entries()
            if row["kind"] in {"trace", "summary"} and row["source_field"] in available_fields]
