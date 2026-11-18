"""Resolve explicitly declared backend profiles without changing registry caches."""
from __future__ import annotations

from copy import deepcopy
import fnmatch
from typing import Any, Mapping

from .capability_registry import CapabilityContract, get_capability
from .task_spec import TaskSpecError
from .task_validator import ValidationIssue


_PROFILE_SECTIONS = frozenset({
    "outputs", "produces", "consumes", "modes", "operator", "implementation",
    "source_binding", "integration_contract", "known_physics_limits", "summary", "simulation_parameters",
})


def select_capability_backend(
    contract: CapabilityContract, physics_backend: str | None = None,
) -> CapabilityContract:
    """Project only capabilities that explicitly declare backend profiles.

    Top-level data is the default contract; profile sections replace, rather
    than deep-merge, their reference counterparts so unavailable outputs cannot
    leak back into a native contract.
    """
    profiles = contract.data.get("backend_profiles")
    if not isinstance(profiles, Mapping) or not profiles:
        return contract
    parameters = contract.data["parameters"]
    definition = parameters["physics_backend"]
    selected = definition["default"] if physics_backend is None else physics_backend
    if not isinstance(selected, str) or selected not in definition["enum"]:
        raise TaskSpecError(f"unknown physics backend {selected!r} for {contract.capability_id}")
    if selected == definition["default"]:
        return contract
    profile = profiles.get(selected)
    if not isinstance(profile, Mapping):
        raise TaskSpecError(f"missing backend profile {selected!r} for {contract.capability_id}")
    unknown = set(profile) - _PROFILE_SECTIONS - {"parameter_names"}
    if unknown:
        raise TaskSpecError(f"unsupported backend profile sections: {sorted(unknown)}")
    names = profile.get("parameter_names")
    if not isinstance(names, list) or "physics_backend" not in names or any(name not in parameters for name in names):
        raise TaskSpecError("backend profile must name existing parameters including physics_backend")
    data = deepcopy(contract.data)
    data["parameters"] = {name: deepcopy(parameters[name]) for name in names}
    data["parameters"]["physics_backend"]["default"] = selected
    for section in _PROFILE_SECTIONS.intersection(profile):
        data[section] = deepcopy(profile[section])
    data["selected_physics_backend"] = selected
    return CapabilityContract(contract.capability_id, data, contract.path)


def get_task_capability(
    spec: Mapping[str, Any], capability_id: str | None = None,
) -> CapabilityContract:
    """Select using the same effective parameter mapping used by execution."""
    from .task_models import is_canonical_task_spec, to_runtime_task_spec
    runtime = to_runtime_task_spec(spec)
    cid = capability_id or str(runtime.get("capability_id") or "")
    parameters = runtime.get("parameters") or {}
    contract = select_capability_backend(get_capability(cid), parameters.get("physics_backend"))
    original_parameters = spec.get("parameters") or {}
    if (contract.data.get("selected_physics_backend") and is_canonical_task_spec(spec)
            and original_parameters.get("overrides")):
        raise TaskSpecError("backend profiles require explicit parameters.values; parameter overrides are not applied by this runtime")
    return contract


def validate_backend_output_requests(
    spec: Mapping[str, Any], contract: CapabilityContract | None = None,
) -> tuple[ValidationIssue, ...]:
    """Reject requests for unavailable outputs before any native graph runs."""
    from .task_models import to_runtime_task_spec
    contract = contract or get_task_capability(spec)
    if not contract.data.get("selected_physics_backend"):
        return ()
    runtime = to_runtime_task_spec(spec)
    outputs = runtime.get("outputs", {})
    def invalid(path: str, message: str) -> tuple[ValidationIssue, ...]:
        return (ValidationIssue("error", path, message, "backend_output_invalid"),)
    if not isinstance(outputs, Mapping):
        return invalid("$.outputs", "requires an outputs mapping")
    declared = contract.data["outputs"]
    traces = [row["name"] for row in declared.get("trace", [])]
    summaries = [row["name"] for row in declared.get("summary", [])]
    requests = [("qoi", outputs.get("qoi", []), traces + summaries),
                ("record_fields", outputs.get("record_fields", []), traces + summaries),
                ("plots", outputs.get("plots", []), traces)]
    streams = outputs.get("telemetry_streams", [])
    if not isinstance(streams, (list, tuple)):
        return invalid("$.outputs.telemetry_streams", "requires a list of telemetry streams")
    for index, stream in enumerate(streams):
        if not isinstance(stream, Mapping):
            return invalid(f"$.outputs.telemetry_streams[{index}]", "requires a stream mapping")
        requests.append((f"telemetry_streams[{index}].fields", stream.get("fields", []), traces))
    issues = []
    for section, fields, available in requests:
        if not isinstance(fields, (list, tuple)) or any(not isinstance(field, str) or not field.strip() for field in fields):
            return invalid(f"$.outputs.{section}", "requires a list of nonempty field names")
        for index, field in enumerate(fields):
            if not any(fnmatch.fnmatchcase(name, str(field)) or fnmatch.fnmatchcase(str(field), name) for name in available):
                issues.append(ValidationIssue(
                    "error", f"$.outputs.{section}[{index}]",
                    f"output {field!r} is unavailable for physics backend {contract.data['selected_physics_backend']!r}",
                    "backend_output_unavailable",
                ))
    return tuple(issues)
