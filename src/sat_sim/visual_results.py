"""Project completed run results back onto Visual Composer graphs.

The projector is deliberately read-only and deterministic.  It consumes only
sealed/run-bundle outputs plus the submitted graph metadata.  It never executes
user code and never mutates the graph or TaskSpec.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Sequence

from .assembly_graph import assembly_contract

VISUAL_RUN_OVERLAY_SCHEMA_VERSION = "sat-sim.visual-run-overlay.v1"
_TIME_FIELDS = {"time_s", "t_s", "spacecraft.time_s"}


@dataclass(frozen=True)
class SeriesSummary:
    field: str
    count: int
    first: float
    last: float
    minimum: float
    maximum: float
    mean: float
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "count": self.count,
            "first": self.first,
            "last": self.last,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean,
            "unit": self.unit,
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        cooked = float(value)
        return cooked if math.isfinite(cooked) else None
    return None


def _guess_unit(field: str) -> str | None:
    key = field.lower()
    if key.endswith("_rad_s") or "speed_rad_s" in key or "omega_" in key:
        return "rad/s"
    if key.endswith("_deg") or "error_deg" in key:
        return "deg"
    if key.endswith("_temp_c") or key.endswith("_c"):
        return "degC"
    if key.endswith("_wh"):
        return "Wh"
    if key.endswith("_w") or "power_w" in key:
        return "W"
    if key.endswith("_nm") or "torque_nm" in key:
        return "N*m"
    if key.endswith("_m") or "altitude_m" in key or "radius_m" in key:
        return "m"
    if key.endswith("_flag") or key.startswith("label."):
        return "bool"
    if key.endswith(".soc") or key.endswith("_ratio") or "shadow_factor" in key or "fraction" in key:
        return "ratio"
    return None


def _series_summaries(rows: Sequence[Mapping[str, Any]]) -> dict[str, SeriesSummary]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, raw in row.items():
            field = str(key)
            if field in _TIME_FIELDS:
                continue
            number = _finite_number(raw)
            if number is None:
                continue
            values.setdefault(field, []).append(number)
    out: dict[str, SeriesSummary] = {}
    for field, samples in values.items():
        if not samples:
            continue
        out[field] = SeriesSummary(
            field=field,
            count=len(samples),
            first=samples[0],
            last=samples[-1],
            minimum=min(samples),
            maximum=max(samples),
            mean=sum(samples) / len(samples),
            unit=_guess_unit(field),
        )
    return out


def _metric_values(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else payload
    return {str(key): value for key, value in _mapping(raw).items()}


def _alias_field_score(alias: str, field: str) -> int:
    alias = alias.lower().strip()
    field_l = field.lower()
    if not alias:
        return 0
    if field_l.startswith(f"{alias}."):
        return 100
    if alias == "orbit_environment":
        if field_l.startswith("environment."):
            return 96
        if field_l.startswith("orbit."):
            return 94
    if alias == "reaction_wheel":
        if field_l.startswith("adcs.rw."):
            return 100
        if field_l == "adcs.power.rw_power_w":
            return 88
    if alias == "wheel_allocator":
        if field_l.startswith("adcs.control.command_torque_nm"):
            return 100
        if field_l.startswith("adcs.control.applied_torque_nm"):
            return 90
    if alias == "rigid_body":
        if field_l.startswith("adcs.attitude."):
            return 100
        if field_l.startswith("adcs.rate.") or field_l.startswith("adcs.pointing."):
            return 98
        if field_l.startswith("adcs.environment."):
            return 82
    if alias == "saturation_monitor":
        if "saturation" in field_l and field_l.startswith("adcs."):
            return 100
    tokens = [token for token in re.split(r"[_\-.]+", alias) if len(token) >= 3]
    if tokens and all(token in field_l for token in tokens[:2]):
        return 55
    return 0


def _headline_priority(alias: str, field: str) -> int:
    key = field.lower()
    alias = alias.lower()
    preferred: dict[str, tuple[str, ...]] = {
        "eps": ("eps.battery.soc", "eps.power.margin_w", "eps.battery.net_power_w"),
        "thermal": ("thermal.node.battery_temp_c", "thermal.node.bus_temp_c", "thermal.heater.power_w"),
        "orbit_environment": ("environment.shadow_factor", "orbit.altitude_m", "orbit.radius_m"),
        "reaction_wheel": ("adcs.rw.max_abs_speed_rad_s", "adcs.rw.speed_rad_s_0", "adcs.power.rw_power_w"),
        "wheel_allocator": ("adcs.control.command_torque_nm_0", "adcs.control.applied_torque_nm_0"),
        "rigid_body": ("adcs.pointing.error_deg", "adcs.rate.omega_bn_b_rad_s_0", "adcs.attitude.quaternion_norm_error"),
        "saturation_monitor": ("adcs.rw.saturation_flag", "adcs.rw.speed_saturation_flag", "adcs.control.torque_saturation_flag"),
    }
    for index, name in enumerate(preferred.get(alias, ())):
        if key == name:
            return 1000 - index
    if key.endswith(".soc") or "error" in key or "margin" in key or "temp" in key or "shadow_factor" in key:
        return 200
    return 0


def _metric_score(alias: str, field: str) -> int:
    key = field.lower()
    alias = alias.lower()
    if alias == "assembly":
        return 100 if key.startswith("qoi.") else 20
    if key.startswith(f"qoi.{alias}.") or key.startswith(f"{alias}."):
        return 100
    if alias == "orbit_environment" and (key.startswith("qoi.orbit.") or key.startswith("qoi.environment.")):
        return 100
    if alias in {"reaction_wheel", "wheel_allocator", "rigid_body", "saturation_monitor"} and key.startswith("qoi.adcs."):
        return 35
    return 0


def _node_overlay(
    node: Mapping[str, Any],
    *,
    series: Mapping[str, SeriesSummary],
    metrics: Mapping[str, Any],
    role: str = "",
) -> dict[str, Any]:
    node_id = str(node.get("id") or "")
    alias = str(node.get("moduleAlias") or node.get("module_alias") or node.get("type") or "")
    scored_series = sorted(
        ((field, _alias_field_score(alias, field)) for field in series),
        key=lambda item: (-item[1], -_headline_priority(alias, item[0]), item[0]),
    )
    selected_fields = [field for field, score in scored_series if score > 0][:12]
    metric_rows = []
    for field, value in metrics.items():
        score = _metric_score(alias, field)
        if score <= 0:
            continue
        metric_rows.append((field, value, score))
    metric_rows.sort(key=lambda item: (-item[2], 0 if str(item[0]).startswith("qoi.") else 1, str(item[0])))
    metric_payload = [{"field": field, "value": value, "unit": _guess_unit(field)} for field, value, _ in metric_rows[:8]]
    series_payload = [series[field].to_dict() for field in selected_fields]
    headline: dict[str, Any] | None = None
    if selected_fields:
        ranked = sorted(selected_fields, key=lambda field: (-_headline_priority(alias, field), -_alias_field_score(alias, field), field))
        headline = series[ranked[0]].to_dict()
        headline["kind"] = "series"
    elif metric_payload:
        headline = {**metric_payload[0], "kind": "metric"}
    return {
        "node_id": node_id,
        "module_alias": alias or None,
        "role": role or str(node.get("role") or ""),
        "headline": headline,
        "metrics": metric_payload,
        "series": series_payload,
        "series_count": len(series_payload),
    }


def _binding_source_candidates(binding: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    alias = str(binding.get("source_alias") or "")
    source_path = str(binding.get("source_path") or "")
    direct: list[str] = []
    if source_path:
        direct.append(source_path)
        prefix = f"{alias}.trace."
        if alias and source_path.startswith(prefix):
            direct.append(source_path[len(prefix):])
    if alias == "wheel_allocator" and source_path.endswith("motor_torque_nm"):
        direct.extend([f"adcs.control.command_torque_nm_{index}" for index in range(3)])
    if alias == "reaction_wheel" and source_path.endswith("wheel_speed_rad_s"):
        direct.extend([f"adcs.rw.speed_rad_s_{index}" for index in range(3)])
    found = [field for field in direct if field in fields]
    if found:
        return list(dict.fromkeys(found))[:3]
    scored = sorted(((field, _alias_field_score(alias, field)) for field in fields), key=lambda item: (-item[1], item[0]))
    return [field for field, score in scored if score >= 90][:3]


def _assembly_overlay(
    graph: Mapping[str, Any],
    series: Mapping[str, SeriesSummary],
    metrics: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parent_id = str(graph.get("parent_capability_id") or "")
    try:
        contract = assembly_contract(parent_id) if parent_id else {}
    except Exception:
        contract = {}
    modules = {str(item.get("alias") or ""): item for item in _sequence(_mapping(contract).get("modules")) if isinstance(item, Mapping)}
    bindings = {str(item.get("binding_id") or ""): item for item in _sequence(_mapping(contract).get("bindings")) if isinstance(item, Mapping)}
    nodes = [item for item in _sequence(graph.get("nodes")) if isinstance(item, Mapping)]
    node_overlays = [
        _node_overlay(node, series=series, metrics=metrics, role=str(_mapping(modules.get(str(node.get("moduleAlias") or ""))).get("role") or node.get("role") or ""))
        for node in nodes
    ]
    node_overlays = [item for item in node_overlays if item["headline"] or item["series"] or item["metrics"] or item["module_alias"] == "assembly"]
    edge_overlays: list[dict[str, Any]] = []
    for edge in _sequence(graph.get("edges")):
        if not isinstance(edge, Mapping):
            continue
        binding_id = str(edge.get("bindingId") or edge.get("binding_id") or "")
        binding = _mapping(bindings.get(binding_id))
        candidates = _binding_source_candidates(binding, list(series))
        if not candidates:
            continue
        signals = [series[field].to_dict() for field in candidates if field in series]
        if not signals:
            continue
        unit = str(_mapping(binding.get("source_payload")).get("unit") or signals[0].get("unit") or "") or None
        for signal in signals:
            if signal.get("unit") is None:
                signal["unit"] = unit
        edge_overlays.append({
            "edge_id": str(edge.get("id") or ""),
            "binding_id": binding_id,
            "source_port": str(edge.get("sourcePort") or edge.get("source_port") or ""),
            "target_port": str(edge.get("targetPort") or edge.get("target_port") or ""),
            "signals": signals,
            "primary": signals[0],
        })
    return node_overlays, edge_overlays


def _scope_probe_candidates(
    probe: Mapping[str, Any],
    *,
    ports: Mapping[str, Mapping[str, Any]],
    fields: Sequence[str],
) -> list[str]:
    """Resolve a read-only Scope probe to already-recorded telemetry fields.

    Scope is deliberately an observer only.  It never creates a physical edge,
    changes fan-out, or mutates the parent solver.  A probe is accepted only for
    a registered *output* simulation port and is projected onto telemetry that
    already exists in the sealed Run Bundle.
    """

    port_id = str(probe.get("sourcePort") or probe.get("source_port") or "")
    port = _mapping(ports.get(port_id))
    if not port or str(port.get("direction") or "") != "output":
        return []
    alias = str(port.get("module_alias") or "")
    source_path = str(port.get("path") or "")
    return _binding_source_candidates(
        {"source_alias": alias, "source_path": source_path},
        fields,
    )


def _scope_overlays(
    graph: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    series: Mapping[str, SeriesSummary],
) -> list[dict[str, Any]]:
    port_rows = _sequence(_mapping(contract.get("simulation_ports")).get("ports"))
    ports = {
        str(item.get("port_id") or ""): item
        for item in port_rows
        if isinstance(item, Mapping) and item.get("port_id")
    }
    node_aliases = {
        str(node.get("id") or ""): str(node.get("moduleAlias") or node.get("module_alias") or "")
        for node in _sequence(graph.get("nodes"))
        if isinstance(node, Mapping) and node.get("id")
    }
    out: list[dict[str, Any]] = []
    for raw_scope in _sequence(graph.get("scopes")):
        if not isinstance(raw_scope, Mapping):
            continue
        probes: list[dict[str, Any]] = []
        for raw_probe in _sequence(raw_scope.get("probes")):
            if not isinstance(raw_probe, Mapping):
                continue
            source_node = str(raw_probe.get("sourceNode") or raw_probe.get("source_node") or "")
            port_id = str(raw_probe.get("sourcePort") or raw_probe.get("source_port") or "")
            port = _mapping(ports.get(port_id))
            expected_alias = str(port.get("module_alias") or "")
            actual_alias = node_aliases.get(source_node, "")
            endpoint_matches = bool(source_node and expected_alias and actual_alias == expected_alias)
            candidates = _scope_probe_candidates(raw_probe, ports=ports, fields=list(series)) if endpoint_matches else []
            signals = [series[field].to_dict() for field in candidates if field in series]
            unit = str(_mapping(port.get("payload")).get("unit") or "") or None
            for signal in signals:
                if signal.get("unit") is None:
                    signal["unit"] = unit
            probes.append({
                "probe_id": str(raw_probe.get("id") or ""),
                "source_node": source_node,
                "source_port": port_id,
                "source_path": str(port.get("path") or ""),
                "source_module_alias": expected_alias or None,
                "endpoint_matches": endpoint_matches,
                "signals": signals,
                "primary": signals[0] if signals else None,
                "resolved": bool(signals),
                "unresolved_reason": None if signals else ("scope_endpoint_mismatch" if not endpoint_matches else "telemetry_not_recorded"),
            })
        observer_kind = str(raw_scope.get("kind") or "scope").lower()
        if observer_kind not in {"scope", "display", "workspace"}:
            observer_kind = "scope"
        default_name = {"scope": "Scope", "display": "Display", "workspace": "To Workspace"}[observer_kind]
        out.append({
            "scope_id": str(raw_scope.get("id") or ""),
            "observer_id": str(raw_scope.get("id") or ""),
            "observer_kind": observer_kind,
            "name": str(raw_scope.get("name") or default_name),
            "probes": probes,
            "resolved_probe_count": sum(1 for item in probes if item.get("resolved")),
            "series_count": sum(len(item.get("signals") or []) for item in probes),
        })
    return out



def resolve_observer_signal_fields(
    *,
    assembly_graph: Mapping[str, Any],
    observer_id: str,
    available_fields: Sequence[str],
) -> dict[str, Any]:
    """Resolve a saved observer sink to recorded telemetry fields without executing code.

    V14 keeps observers non-physical.  Resolution uses only the registered
    assembly contract plus the observer's source node/port references.
    """

    parent_id = str(_mapping(assembly_graph).get("parent_capability_id") or "")
    try:
        contract = assembly_contract(parent_id) if parent_id else {}
    except Exception:
        contract = {}
    port_rows = _sequence(_mapping(_mapping(contract).get("simulation_ports")).get("ports"))
    ports = {
        str(item.get("port_id") or ""): item
        for item in port_rows
        if isinstance(item, Mapping) and item.get("port_id")
    }
    node_aliases = {
        str(node.get("id") or ""): str(node.get("moduleAlias") or node.get("module_alias") or "")
        for node in _sequence(_mapping(assembly_graph).get("nodes"))
        if isinstance(node, Mapping) and node.get("id")
    }
    observer = next(
        (
            item
            for item in _sequence(_mapping(assembly_graph).get("scopes"))
            if isinstance(item, Mapping) and str(item.get("id") or "") == str(observer_id)
        ),
        None,
    )
    if observer is None:
        return {"ok": False, "reason_code": "OBSERVER_NOT_FOUND", "observer_id": observer_id, "observer_kind": None, "fields": [], "unresolved_probes": []}
    kind = str(observer.get("kind") or "scope").lower()
    if kind not in {"scope", "display", "workspace"}:
        kind = "scope"
    fields: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for raw_probe in _sequence(observer.get("probes")):
        if not isinstance(raw_probe, Mapping):
            continue
        source_node = str(raw_probe.get("sourceNode") or raw_probe.get("source_node") or "")
        port_id = str(raw_probe.get("sourcePort") or raw_probe.get("source_port") or "")
        port = _mapping(ports.get(port_id))
        expected_alias = str(port.get("module_alias") or "")
        actual_alias = node_aliases.get(source_node, "")
        if not source_node or not expected_alias or actual_alias != expected_alias:
            unresolved.append({"probe_id": str(raw_probe.get("id") or ""), "reason_code": "scope_endpoint_mismatch"})
            continue
        resolved = _scope_probe_candidates(raw_probe, ports=ports, fields=available_fields)
        if not resolved:
            unresolved.append({"probe_id": str(raw_probe.get("id") or ""), "reason_code": "telemetry_not_recorded"})
            continue
        for field in resolved:
            if field not in fields:
                fields.append(field)
    return {
        "ok": bool(fields),
        "reason_code": None if fields else "OBSERVER_FIELDS_UNRESOLVED",
        "observer_id": str(observer.get("id") or observer_id),
        "observer_kind": kind,
        "name": str(observer.get("name") or {"scope": "Scope", "display": "Display", "workspace": "To Workspace"}[kind]),
        "fields": fields,
        "unresolved_probes": unresolved,
    }

def _flow_overlay(
    graph: Mapping[str, Any],
    series: Mapping[str, SeriesSummary],
    metrics: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [item for item in _sequence(graph.get("nodes")) if isinstance(item, Mapping)]
    ranked_series = sorted(series.values(), key=lambda item: (-_headline_priority("", item.field), item.field))
    qoi = [{"field": field, "value": value, "unit": _guess_unit(field)} for field, value in metrics.items() if str(field).startswith("qoi.")]
    out: list[dict[str, Any]] = []
    for node in nodes:
        node_type = str(node.get("type") or "")
        payload: dict[str, Any] = {"node_id": str(node.get("id") or ""), "module_alias": node_type, "role": node_type, "metrics": [], "series": [], "series_count": 0, "headline": None}
        if node_type in {"model", "outputs", "run"}:
            payload["metrics"] = qoi[:8]
        if node_type in {"model", "outputs"}:
            payload["series"] = [item.to_dict() for item in ranked_series[:12]]
            payload["series_count"] = len(payload["series"])
        if node_type == "run" and qoi:
            payload["headline"] = {**qoi[0], "kind": "metric"}
        elif payload["series"]:
            payload["headline"] = {**payload["series"][0], "kind": "series"}
        if payload["headline"] or payload["metrics"] or payload["series"] or node_type == "run":
            out.append(payload)
    return out, []


def build_visual_run_overlay(
    *,
    run_id: str,
    run_record: Mapping[str, Any] | None,
    validation_outcome: Mapping[str, Any] | None,
    metrics_payload: Mapping[str, Any] | None,
    telemetry_rows: Sequence[Mapping[str, Any]],
    visual_graph: Mapping[str, Any] | None = None,
    assembly_graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact graph overlay from already-produced run artifacts."""

    if visual_graph is not None and assembly_graph is not None:
        raise ValueError("visual overlay accepts only one graph source")
    mode = "assembly" if assembly_graph is not None else "flow" if visual_graph is not None else "unknown"
    graph = assembly_graph if assembly_graph is not None else visual_graph if visual_graph is not None else {}
    series = _series_summaries([row for row in telemetry_rows if isinstance(row, Mapping)])
    metrics = _metric_values(_mapping(metrics_payload))
    scope_overlays: list[dict[str, Any]] = []
    if mode == "assembly":
        node_overlays, edge_overlays = _assembly_overlay(_mapping(graph), series, metrics)
        parent_id = str(_mapping(graph).get("parent_capability_id") or "")
        try:
            contract = assembly_contract(parent_id) if parent_id else {}
        except Exception:
            contract = {}
        scope_overlays = _scope_overlays(_mapping(graph), contract=_mapping(contract), series=series)
    elif mode == "flow":
        node_overlays, edge_overlays = _flow_overlay(_mapping(graph), series, metrics)
    else:
        node_overlays, edge_overlays = [], []
    run = _mapping(run_record)
    validation = _mapping(validation_outcome)
    return {
        "schema_version": VISUAL_RUN_OVERLAY_SCHEMA_VERSION,
        "ok": mode != "unknown",
        "mode": mode,
        "run_id": run_id,
        "run_status": str(run.get("status") or "UNKNOWN"),
        "validation_result": validation.get("result") or run.get("validation_result"),
        "node_overlays": node_overlays,
        "edge_overlays": edge_overlays,
        "scope_overlays": scope_overlays,
        "observer_overlays": scope_overlays,
        "series_catalog": [summary.to_dict() for _, summary in sorted(series.items())],
        "telemetry_row_count": len(telemetry_rows),
        "numeric_series_count": len(series),
        "metric_count": len(metrics),
        "arbitrary_code_inspected": False,
    }


__all__ = ["VISUAL_RUN_OVERLAY_SCHEMA_VERSION", "build_visual_run_overlay", "resolve_observer_signal_fields"]
