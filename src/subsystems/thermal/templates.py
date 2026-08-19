"""THERM-1B public-case-compatible thermal network templates.

The templates translate public thermal-modeling patterns into project-native
``subsystem.thermal_reduced_order.v1`` network payloads.  They are intentionally
public-reference-informed defaults, not copied flight models and not
Thermal Desktop/ESATAN equivalents.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

try:  # PyYAML is already required by the capability registry.
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    yaml = None  # type: ignore
    _YAML_IMPORT_ERROR = exc
else:
    _YAML_IMPORT_ERROR = None

THERMAL_TEMPLATE_SCHEMA_VERSION = "therm1b.thermal_network_template.v1"
DEFAULT_TEMPLATE_ID = "cubesat_7node_box_template"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
THERMAL_TEMPLATE_ROOT = PROJECT_ROOT / "reference_cases" / "thermal_public_cases"


class ThermalTemplateError(ValueError):
    """Raised when a public thermal template is missing or malformed."""


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:  # pragma: no cover
        raise ThermalTemplateError("PyYAML is required to load thermal templates") from _YAML_IMPORT_ERROR
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ThermalTemplateError(f"thermal template must be a mapping: {path}")
    return payload


def list_thermal_template_ids(root: str | Path | None = None) -> tuple[str, ...]:
    """Return available thermal template IDs from the reference-case tree."""

    base = Path(root) if root is not None else THERMAL_TEMPLATE_ROOT
    if not base.exists():
        return tuple()
    ids: list[str] = []
    for path in sorted(base.glob("*/thermal_network.yaml")):
        try:
            data = _load_yaml(path)
            tid = str(data.get("template_id", path.parent.name)).strip()
            if tid:
                ids.append(tid)
        except Exception:
            continue
    return tuple(sorted(set(ids)))


def _template_path(template_id: str, root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else THERMAL_TEMPLATE_ROOT
    candidate = base / template_id / "thermal_network.yaml"
    if candidate.exists():
        return candidate
    for path in base.glob("*/thermal_network.yaml") if base.exists() else []:
        try:
            if str(_load_yaml(path).get("template_id", "")) == template_id:
                return path
        except Exception:
            continue
    raise ThermalTemplateError(f"unknown thermal template_id {template_id!r}; known: {', '.join(list_thermal_template_ids(base))}")


def load_thermal_template(template_id: str = DEFAULT_TEMPLATE_ID, *, root: str | Path | None = None) -> dict[str, Any]:
    """Load and validate one public thermal template."""

    data = _load_yaml(_template_path(template_id, root))
    validate_thermal_template(data)
    return data


def validate_thermal_template(template: Mapping[str, Any]) -> None:
    """Lightweight schema validation used by adapters and tests."""

    if template.get("schema_version") != THERMAL_TEMPLATE_SCHEMA_VERSION:
        raise ThermalTemplateError(f"template schema_version must be {THERMAL_TEMPLATE_SCHEMA_VERSION}")
    template_id = template.get("template_id")
    if not isinstance(template_id, str) or not template_id.strip():
        raise ThermalTemplateError("template_id is required")
    topology = template.get("node_topology")
    if not isinstance(topology, Mapping) or topology.get("type") not in {"single_node", "six_face_internal", "component_augmented"}:
        raise ThermalTemplateError("node_topology.type must be single_node, six_face_internal, or component_augmented")
    nodes = template.get("nodes")
    if not isinstance(nodes, Mapping) or not nodes:
        raise ThermalTemplateError("nodes must be a non-empty mapping")
    for node_id, node in nodes.items():
        if not isinstance(node, Mapping):
            raise ThermalTemplateError(f"node {node_id!r} must be a mapping")
        if float(node.get("heat_capacity_j_k", 0.0)) <= 0.0:
            raise ThermalTemplateError(f"node {node_id!r} heat_capacity_j_k must be positive")
    for field in ("conductive_links", "radiative_links"):
        value = template.get(field, []) or []
        if not isinstance(value, list):
            raise ThermalTemplateError(f"{field} must be a list")
        for i, link in enumerate(value):
            if not isinstance(link, Mapping):
                raise ThermalTemplateError(f"{field}[{i}] must be a mapping")
            if not (link.get("node_a") or link.get("from")) or not (link.get("node_b") or link.get("to")):
                raise ThermalTemplateError(f"{field}[{i}] must define from/to or node_a/node_b")
    source = template.get("source_case")
    if not isinstance(source, Mapping):
        raise ThermalTemplateError("source_case is required")
    validation_limits = template.get("validation_limits")
    if not isinstance(validation_limits, Mapping):
        raise ThermalTemplateError("validation_limits is required")


def _link_payload(link: Mapping[str, Any], *, radiative: bool = False) -> dict[str, Any]:
    out = dict(link)
    out["node_a"] = str(out.pop("from", out.get("node_a")))
    out["node_b"] = str(out.pop("to", out.get("node_b")))
    if radiative:
        out.setdefault("exchange_area_m2", out.get("area_m2", 0.0))
    else:
        out.setdefault("conductance_w_k", 0.0)
    return out


def _deep_merge(base: dict[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = deepcopy(value)
    return out


def template_to_task_parameters(template: Mapping[str, Any], *, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Convert a template into ``parameters`` accepted by the reduced-order adapter."""

    validate_thermal_template(template)
    params: dict[str, Any] = {
        "template_id": template["template_id"],
        "model_basis": f"therm1b_template:{template['template_id']}",
        "network": {
            "nodes": deepcopy(dict(template.get("nodes") or {})),
            "conductive_links": [_link_payload(link) for link in (template.get("conductive_links") or [])],
            "radiative_links": [_link_payload(link, radiative=True) for link in (template.get("radiative_links") or [])],
        },
        "environment": deepcopy(dict(template.get("environment") or {})),
        "attitude": deepcopy(dict(template.get("attitude") or {})),
        "internal_power_by_node_w": deepcopy(dict(template.get("internal_power_by_node_w") or {"internal": 0.0})),
        "heater_node": str((template.get("heater") or {}).get("node", "internal")),
        "heater_power_w": float((template.get("heater") or {}).get("power_w", 0.0)),
        "heater_setpoint_c": float((template.get("heater") or {}).get("setpoint_c", 0.0)),
        "heater_deadband_c": float((template.get("heater") or {}).get("deadband_c", 0.0)),
        "heater_control": deepcopy(dict((template.get("heater") or {}).get("control") or {
            "mode": "threshold",
            "node": (template.get("heater") or {}).get("node", "internal"),
            "max_power_w": (template.get("heater") or {}).get("power_w", 0.0),
            "setpoint_c": (template.get("heater") or {}).get("setpoint_c", 0.0),
            "deadband_c": (template.get("heater") or {}).get("deadband_c", 0.0),
        })),
        "min_temp_c_by_node": deepcopy(dict((template.get("limits") or {}).get("min_temp_c_by_node") or {})),
        "max_temp_c_by_node": deepcopy(dict((template.get("limits") or {}).get("max_temp_c_by_node") or {})),
        "template_source_case": deepcopy(dict(template.get("source_case") or {})),
        "template_validation_limits": deepcopy(dict(template.get("validation_limits") or {})),
    }
    if overrides:
        params = _deep_merge(params, overrides)
    # Convenience compatibility: allow template_overrides.nodes to patch the
    # project network node map without requiring users to repeat network.nodes.
    if isinstance(params.get("nodes"), Mapping):
        network = dict(params.get("network") if isinstance(params.get("network"), Mapping) else {})
        network["nodes"] = _deep_merge(dict(network.get("nodes") if isinstance(network.get("nodes"), Mapping) else {}), params["nodes"])
        params["network"] = network
    if isinstance(params.get("conductive_links"), list):
        network = dict(params.get("network") if isinstance(params.get("network"), Mapping) else {})
        network["conductive_links"] = deepcopy(params["conductive_links"])
        params["network"] = network
    if isinstance(params.get("radiative_links"), list):
        network = dict(params.get("network") if isinstance(params.get("network"), Mapping) else {})
        network["radiative_links"] = deepcopy(params["radiative_links"])
        params["network"] = network
    return params


def expand_task_spec_with_thermal_template(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Expand ``parameters.template_id`` into an explicit thermal network payload.

    If no template ID is present, the spec is returned with a shallow copy only.
    """

    out = dict(spec)
    params = dict(out.get("parameters") if isinstance(out.get("parameters"), Mapping) else {})
    template_id = params.get("template_id") or out.get("template_id")
    if not template_id:
        out["parameters"] = params
        return out
    overrides = params.get("template_overrides") if isinstance(params.get("template_overrides"), Mapping) else {}
    template = load_thermal_template(str(template_id))
    template_params = template_to_task_parameters(template, overrides=overrides)
    # Explicit task parameters override template defaults, except template_id and
    # network generated from the selected template.
    explicit = {k: deepcopy(v) for k, v in params.items() if k not in {"template_overrides"}}
    merged = _deep_merge(template_params, explicit)
    merged["template_id"] = str(template_id)
    out["parameters"] = merged
    metadata = dict(out.get("metadata") if isinstance(out.get("metadata"), Mapping) else {})
    metadata.setdefault("thermal_template_id", str(template_id))
    metadata.setdefault("thermal_template_claim", "public_reference_informed_not_flight_validated")
    out["metadata"] = metadata
    return out


def thermal_template_inventory(root: str | Path | None = None) -> dict[str, Any]:
    """Return an auditable inventory of installed public thermal templates."""

    templates: list[dict[str, Any]] = []
    for template_id in list_thermal_template_ids(root):
        data = load_thermal_template(template_id, root=root)
        templates.append({
            "template_id": template_id,
            "display_name": data.get("display_name"),
            "topology_type": (data.get("node_topology") or {}).get("type"),
            "node_count": len(data.get("nodes") or {}),
            "conductive_link_count": len(data.get("conductive_links") or []),
            "radiative_link_count": len(data.get("radiative_links") or []),
            "claim_level": (data.get("source_case") or {}).get("claim_level"),
            "source_urls": list((data.get("source_case") or {}).get("source_urls") or []),
            "flight_validated": False,
            "thermal_desktop_equivalent": False,
        })
    return {
        "schema_version": "therm1b.thermal_template_inventory.v1",
        "template_count": len(templates),
        "templates": templates,
        "can_claim_high_fidelity": False,
        "flight_validated": False,
    }


__all__ = [
    "THERMAL_TEMPLATE_SCHEMA_VERSION",
    "DEFAULT_TEMPLATE_ID",
    "THERMAL_TEMPLATE_ROOT",
    "ThermalTemplateError",
    "list_thermal_template_ids",
    "load_thermal_template",
    "validate_thermal_template",
    "template_to_task_parameters",
    "expand_task_spec_with_thermal_template",
    "thermal_template_inventory",
]
