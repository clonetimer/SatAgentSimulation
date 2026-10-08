"""Formal simulation-port contracts for registered visual assemblies.

V5 introduces an explicit port contract above legacy ``composition.field_mappings``.
The contract is intentionally metadata-only: it cannot name arbitrary Python classes
or introduce couplings that are absent from the parent Capability composition.

Capabilities may opt in with a top-level ``simulation_ports`` block.  Existing V4
contracts remain usable through a conservative legacy inference layer and are marked
``legacy-inferred-v4`` so callers can distinguish explicit interface metadata from
heuristics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from .capability_registry import CapabilityContract, get_capability

SIMULATION_PORT_SCHEMA_VERSION = "sat-sim.simulation-ports.v1"
MAX_PORTS = 256
MAX_BINDINGS = 256
MAX_PORT_ID_LENGTH = 180
_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARENT_ALIAS = "assembly"
_PARENT_SOURCE_PREFIXES = {"TaskSpec", "parameters", "simulation", "spacecraft"}
_ALLOWED_DIRECTIONS = {"input", "output"}
_ALLOWED_SHAPES = {"scalar", "timeseries", "scalar_or_series", "boolean", "object", "event"}
_ALLOWED_FAN = {"one", "many"}
_ALLOWED_SOLVER_POLICIES = {
    "feed_forward",
    "parent_managed_feedback",
    "parent_managed_stateful_feedback",
    "delayed_feedback",
    "configuration_mapping",
    "accounting",
    "parent_owned",
}


@dataclass(frozen=True)
class SimulationPortIssue:
    code: str
    message: str
    path: str = "$.simulation_ports"
    severity: str = "error"
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.details is None:
            payload.pop("details")
        return payload


def _issue(
    issues: list[SimulationPortIssue],
    code: str,
    message: str,
    *,
    path: str = "$.simulation_ports",
    severity: str = "error",
    details: dict[str, Any] | None = None,
) -> None:
    issues.append(SimulationPortIssue(code, message, path, severity, details))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _field_mapping_paths(raw: Mapping[str, Any]) -> tuple[str, str]:
    source = raw.get("source") if isinstance(raw.get("source"), str) else raw.get("from")
    target = raw.get("target") if isinstance(raw.get("target"), str) else raw.get("to")
    return str(source or "").strip(), str(target or "").strip()


def _first_token(path: str) -> str | None:
    if not path:
        return None
    token = path.split(".", 1)[0].strip()
    return token if _ALIAS_RE.fullmatch(token) else None


def _resolve_alias(path: str, aliases: set[str]) -> str:
    token = _first_token(path)
    if not token or token in _PARENT_SOURCE_PREFIXES:
        return _PARENT_ALIAS
    return token if token in aliases else _PARENT_ALIAS


def _safe_token(value: str) -> str:
    cooked = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return cooked or "signal"


def _type_from_parameter(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_type = str(meta.get("type") or "").strip().lower()
    unit = str(meta.get("unit") or "").strip() or "1"
    if raw_type.startswith("array"):
        return {"dtype": "number", "shape": "timeseries", "unit": unit}
    if raw_type in {"boolean", "bool"}:
        return {"dtype": "bool", "shape": "boolean", "unit": "1"}
    if raw_type in {"object", "mapping", "dict"}:
        return {"dtype": "object", "shape": "object", "unit": "1"}
    if raw_type in {"integer", "number", "float"}:
        return {"dtype": "number", "shape": "scalar", "unit": unit}
    if raw_type == "number_or_array[number]":
        return {"dtype": "number", "shape": "scalar_or_series", "unit": unit}
    return None


def _trace_payload(contract: CapabilityContract | None, field_name: str) -> dict[str, Any] | None:
    if contract is None:
        return None
    outputs = _mapping(contract.data.get("outputs"))
    for item in _sequence(outputs.get("trace")):
        if not isinstance(item, Mapping) or str(item.get("name") or "") != field_name:
            continue
        dtype = str(item.get("dtype") or "number").strip().lower()
        unit = str(item.get("unit") or "1").strip() or "1"
        return {"dtype": "bool" if unit == "bool" else dtype, "shape": "timeseries", "unit": unit}
    return None


def _semantic_payload(path: str, *, series: bool = False) -> dict[str, Any]:
    lowered = path.lower()
    if any(token in lowered for token in ("access", "enabled", "active", "flag", "validation")):
        return {"dtype": "bool", "shape": "timeseries" if series else "boolean", "unit": "1"}
    if any(token in lowered for token in ("shadow_factor", "soc", "efficiency", "fraction", "ratio")):
        unit = "ratio"
    elif any(token in lowered for token in ("temp_c", "temperature")):
        unit = "degC"
    elif any(token in lowered for token in ("capacity_wh", "_wh")):
        unit = "Wh"
    elif any(token in lowered for token in ("generated_bps", "downlink_rate", "_bps")):
        unit = "bit/s"
    elif any(token in lowered for token in ("bits", "data")):
        unit = "bit"
    elif any(token in lowered for token in ("torque", "_nm")):
        unit = "N*m"
    elif any(token in lowered for token in ("thrust_n", ".thrust")):
        unit = "N"
    elif any(token in lowered for token in ("delta_v", "velocity", "_m_s")):
        unit = "m/s"
    elif any(token in lowered for token in ("duration_s", "time_s", "_period_s")):
        unit = "s"
    elif any(token in lowered for token in ("propellant", "_kg")):
        unit = "kg"
    elif "power" in lowered or re.search(r"(?:^|[._])[^.]*_w(?:$|[._])", lowered):
        unit = "W"
    elif any(token in lowered for token in ("angle", "_deg", "pointing")):
        unit = "deg"
    else:
        unit = "signal"
    return {"dtype": "number", "shape": "timeseries" if series else "scalar", "unit": unit}


def _lookup_target_payload(path: str, alias: str, capability_id: str | None) -> dict[str, Any] | None:
    if not capability_id or alias == _PARENT_ALIAS:
        return None
    try:
        contract = get_capability(capability_id)
    except Exception:
        return None
    prefix = f"{alias}.parameters."
    if path.startswith(prefix):
        param = path[len(prefix):].split(".", 1)[0]
        return _type_from_parameter(_mapping(_mapping(contract.data.get("parameters")).get(param)))
    return None


def _lookup_source_payload(path: str, alias: str, capability_id: str | None) -> dict[str, Any] | None:
    if not capability_id or alias == _PARENT_ALIAS:
        return None
    try:
        contract = get_capability(capability_id)
    except Exception:
        return None
    trace_prefix = f"{alias}.trace."
    if path.startswith(trace_prefix):
        return _trace_payload(contract, path[len(trace_prefix):])
    plain_prefix = f"{alias}."
    if path.startswith(plain_prefix):
        return _trace_payload(contract, path[len(plain_prefix):])
    return None


def payload_type(payload: Mapping[str, Any]) -> str:
    shape = str(payload.get("shape") or "scalar")
    unit = str(payload.get("unit") or "1")
    dtype = str(payload.get("dtype") or "number")
    if shape == "boolean" or dtype == "bool":
        return "bool" if shape != "timeseries" else "timeseries[bool]"
    if shape == "object":
        return "object"
    if shape == "event":
        return f"event[{unit}]"
    if shape == "timeseries":
        return f"timeseries[{unit}]"
    if shape == "scalar_or_series":
        return f"scalar_or_series[{unit}]"
    return f"scalar[{unit}]"


def _normal_timing(value: Any, *, default_policy: str = "parent_owned") -> dict[str, Any]:
    timing = dict(_mapping(value))
    return {
        "domain": str(timing.get("domain") or "sampled"),
        "rate_policy": str(timing.get("rate_policy") or default_policy),
        "sample_period_source": timing.get("sample_period_source"),
    }


def _normal_payload(value: Any) -> dict[str, Any]:
    payload = dict(_mapping(value))
    dtype = str(payload.get("dtype") or "number")
    shape = str(payload.get("shape") or "scalar")
    schema_id = str(payload.get("schema_id") or f"sat-sim.signal.{shape}.{dtype}.v1")
    return {
        "schema_id": schema_id,
        "dtype": dtype,
        "shape": shape,
        "unit": str(payload.get("unit") or "1"),
    }


def _port_contract_fingerprint(payload: Mapping[str, Any]) -> str:
    core = {
        "schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "clock": payload.get("clock"),
        "ports": payload.get("ports"),
        "bindings": payload.get("bindings"),
    }
    raw = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def _binding_payload(
    *,
    binding_id: str,
    source_port: Mapping[str, Any],
    target_port: Mapping[str, Any],
    raw_mapping: Mapping[str, Any],
    transform: Mapping[str, Any] | None = None,
    solver: Mapping[str, Any] | None = None,
    contract_source: str,
) -> dict[str, Any]:
    source_payload = _normal_payload(source_port.get("payload"))
    target_payload = _normal_payload(target_port.get("payload"))
    source_type = payload_type(source_payload)
    target_type = payload_type(target_payload)
    data_type = source_type if source_type == target_type else f"transform[{source_type}->{target_type}]"
    source_path, target_path = _field_mapping_paths(raw_mapping)
    transform_payload = dict(transform or {})
    solver_payload = dict(solver or {})
    return {
        "binding_id": binding_id,
        "source_alias": str(source_port["module_alias"]),
        "target_alias": str(target_port["module_alias"]),
        "source_path": source_path or str(source_port.get("path") or ""),
        "target_path": target_path or str(target_port.get("path") or ""),
        "source_port": str(source_port["port_id"]),
        "target_port": str(target_port["port_id"]),
        "data_type": data_type,
        "source_type": source_type,
        "target_type": target_type,
        "source_payload": source_payload,
        "target_payload": target_payload,
        "source_timing": dict(source_port.get("timing") or {}),
        "target_timing": dict(target_port.get("timing") or {}),
        "source_direct_feedthrough": bool(source_port.get("direct_feedthrough", False)),
        "source_state_owner": source_port.get("state_owner"),
        "target_state_owner": target_port.get("state_owner"),
        "source_fan_out": str(source_port.get("fan_out") or "many"),
        "target_fan_in": str(target_port.get("fan_in") or "one"),
        "policy": str(raw_mapping.get("policy") or transform_payload.get("policy") or "registered_mapping"),
        "transform": transform_payload or {"policy": "identity" if source_type == target_type else "registered_transform"},
        "solver": {
            "policy": str(solver_payload.get("policy") or "parent_owned"),
            "delay_steps": int(solver_payload.get("delay_steps") or 0),
        },
        "description": str(raw_mapping.get("description") or ""),
        "wireable": str(source_port["module_alias"]) != str(target_port["module_alias"]),
        "registered": True,
        "contract_source": contract_source,
    }


def _validate_port(
    raw: Mapping[str, Any],
    *,
    index: int,
    module_aliases: set[str],
    issues: list[SimulationPortIssue],
) -> dict[str, Any] | None:
    path = f"$.simulation_ports.ports[{index}]"
    port_id = str(raw.get("port_id") or "").strip()
    module_alias = str(raw.get("module_alias") or "").strip()
    direction = str(raw.get("direction") or "").strip().lower()
    signal_path = str(raw.get("path") or "").strip()
    if not port_id or len(port_id) > MAX_PORT_ID_LENGTH:
        _issue(issues, "PORT_ID_INVALID", "port_id is missing or too long.", path=f"{path}.port_id")
        return None
    if module_alias not in module_aliases:
        _issue(issues, "PORT_MODULE_UNKNOWN", f"Unknown module alias {module_alias!r}.", path=f"{path}.module_alias")
    if direction not in _ALLOWED_DIRECTIONS:
        _issue(issues, "PORT_DIRECTION_INVALID", "direction must be input or output.", path=f"{path}.direction")
    if not signal_path:
        _issue(issues, "PORT_PATH_MISSING", "Port path is required.", path=f"{path}.path")
    raw_payload = _mapping(raw.get("payload"))
    for key in ("schema_id", "dtype", "shape", "unit"):
        if key not in raw_payload or raw_payload.get(key) in (None, ""):
            _issue(issues, "PORT_PAYLOAD_METADATA_MISSING", f"Explicit port payload must declare {key}.", path=f"{path}.payload.{key}")
    payload = _normal_payload(raw_payload)
    if payload["shape"] not in _ALLOWED_SHAPES:
        _issue(issues, "PORT_SHAPE_INVALID", f"Unsupported payload shape {payload['shape']!r}.", path=f"{path}.payload.shape")
    raw_timing = _mapping(raw.get("timing"))
    for key in ("domain", "rate_policy", "sample_period_source"):
        if key not in raw_timing or raw_timing.get(key) in (None, ""):
            _issue(issues, "PORT_TIMING_METADATA_MISSING", f"Explicit port timing must declare {key}.", path=f"{path}.timing.{key}")
    timing = _normal_timing(raw_timing)
    if "state_owner" not in raw or not str(raw.get("state_owner") or "").strip():
        _issue(issues, "PORT_STATE_OWNER_MISSING", "Explicit port must declare state_owner.", path=f"{path}.state_owner")
    if direction == "output" and "direct_feedthrough" not in raw:
        _issue(issues, "PORT_DIRECT_FEEDTHROUGH_MISSING", "Explicit output port must declare direct_feedthrough.", path=f"{path}.direct_feedthrough")
    if direction == "input" and "fan_in" not in raw:
        _issue(issues, "PORT_FAN_IN_MISSING", "Explicit input port must declare fan_in.", path=f"{path}.fan_in")
    if direction == "output" and "fan_out" not in raw:
        _issue(issues, "PORT_FAN_OUT_MISSING", "Explicit output port must declare fan_out.", path=f"{path}.fan_out")
    fan_in = str(raw.get("fan_in") or "one")
    fan_out = str(raw.get("fan_out") or "many")
    if fan_in not in _ALLOWED_FAN:
        _issue(issues, "PORT_FAN_IN_INVALID", "fan_in must be one or many.", path=f"{path}.fan_in")
    if fan_out not in _ALLOWED_FAN:
        _issue(issues, "PORT_FAN_OUT_INVALID", "fan_out must be one or many.", path=f"{path}.fan_out")
    return {
        "port_id": port_id,
        "module_alias": module_alias,
        "direction": direction,
        "path": signal_path,
        "payload": payload,
        "timing": timing,
        "direct_feedthrough": bool(raw.get("direct_feedthrough", False)),
        "state_owner": str(raw.get("state_owner") or module_alias),
        "fan_in": fan_in,
        "fan_out": fan_out,
        "required": bool(raw.get("required", True)),
        "description": str(raw.get("description") or ""),
    }


def _explicit_contract(
    contract: CapabilityContract,
    modules: list[dict[str, Any]],
    raw_mappings: list[Mapping[str, Any]],
    explicit: Mapping[str, Any],
) -> dict[str, Any]:
    issues: list[SimulationPortIssue] = []
    schema_version = str(explicit.get("schema_version") or "")
    if schema_version != SIMULATION_PORT_SCHEMA_VERSION:
        _issue(issues, "PORT_SCHEMA_VERSION_UNSUPPORTED", f"Unsupported simulation port schema {schema_version!r}.", path="$.simulation_ports.schema_version")
    module_aliases = {str(item.get("alias") or "") for item in modules}
    raw_ports = _sequence(explicit.get("ports"))
    raw_bindings = _sequence(explicit.get("bindings"))
    if len(raw_ports) > MAX_PORTS:
        _issue(issues, "PORT_LIMIT_EXCEEDED", f"At most {MAX_PORTS} ports are allowed.", path="$.simulation_ports.ports")
        raw_ports = raw_ports[:MAX_PORTS]
    if len(raw_bindings) > MAX_BINDINGS:
        _issue(issues, "PORT_BINDING_LIMIT_EXCEEDED", f"At most {MAX_BINDINGS} bindings are allowed.", path="$.simulation_ports.bindings")
        raw_bindings = raw_bindings[:MAX_BINDINGS]

    ports: list[dict[str, Any]] = []
    port_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_ports):
        if not isinstance(raw, Mapping):
            _issue(issues, "PORT_ENTRY_INVALID", "Port entry must be an object.", path=f"$.simulation_ports.ports[{index}]")
            continue
        port = _validate_port(raw, index=index, module_aliases=module_aliases, issues=issues)
        if port is None:
            continue
        if port["port_id"] in port_by_id:
            _issue(issues, "PORT_ID_DUPLICATE", f"Duplicate port_id {port['port_id']!r}.", path=f"$.simulation_ports.ports[{index}].port_id")
            continue
        ports.append(port)
        port_by_id[port["port_id"]] = port

    registered_pairs = {_field_mapping_paths(item): item for item in raw_mappings if all(_field_mapping_paths(item))}
    consumed_mapping_pairs: set[tuple[str, str]] = set()
    bindings: list[dict[str, Any]] = []
    seen_binding_ids: set[str] = set()
    seen_targets: set[str] = set()
    source_counts: dict[str, int] = {}
    for index, raw in enumerate(raw_bindings):
        path = f"$.simulation_ports.bindings[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "PORT_BINDING_INVALID", "Binding entry must be an object.", path=path)
            continue
        binding_id = str(raw.get("binding_id") or "").strip()
        source_id = str(raw.get("source_port") or "").strip()
        target_id = str(raw.get("target_port") or "").strip()
        if not binding_id or len(binding_id) > MAX_PORT_ID_LENGTH:
            _issue(issues, "PORT_BINDING_ID_INVALID", "binding_id is missing or too long.", path=f"{path}.binding_id")
            continue
        if binding_id in seen_binding_ids:
            _issue(issues, "PORT_BINDING_ID_DUPLICATE", f"Duplicate binding_id {binding_id!r}.", path=f"{path}.binding_id")
            continue
        seen_binding_ids.add(binding_id)
        source = port_by_id.get(source_id)
        target = port_by_id.get(target_id)
        if source is None or target is None:
            _issue(issues, "PORT_BINDING_PORT_UNKNOWN", "Binding references an unknown source or target port.", path=path, details={"source_port": source_id, "target_port": target_id})
            continue
        if source["direction"] != "output" or target["direction"] != "input":
            _issue(issues, "PORT_BINDING_DIRECTION_INVALID", "Bindings must connect output -> input.", path=path)
            continue
        pair = (str(source["path"]), str(target["path"]))
        mapping = registered_pairs.get(pair)
        if mapping is None:
            _issue(
                issues,
                "PORT_BINDING_NOT_IMPLEMENTED",
                "Explicit simulation-port binding is not backed by composition.field_mappings.",
                path=path,
                details={"source_path": pair[0], "target_path": pair[1]},
            )
            continue
        consumed_mapping_pairs.add(pair)
        transform = dict(_mapping(raw.get("transform")))
        solver = dict(_mapping(raw.get("solver")))
        if not str(transform.get("policy") or "").strip():
            _issue(issues, "PORT_TRANSFORM_POLICY_MISSING", "Explicit binding must declare transform.policy.", path=f"{path}.transform.policy")
        if not str(solver.get("policy") or "").strip():
            _issue(issues, "PORT_SOLVER_POLICY_MISSING", "Explicit binding must declare solver.policy.", path=f"{path}.solver.policy")
        if "delay_steps" not in solver:
            _issue(issues, "PORT_SOLVER_DELAY_MISSING", "Explicit binding must declare solver.delay_steps.", path=f"{path}.solver.delay_steps")
        solver_policy = str(solver.get("policy") or "parent_owned")
        if solver_policy not in _ALLOWED_SOLVER_POLICIES:
            _issue(issues, "PORT_SOLVER_POLICY_INVALID", f"Unsupported solver policy {solver_policy!r}.", path=f"{path}.solver.policy")
        try:
            delay_steps = int(solver.get("delay_steps") or 0)
        except Exception:
            delay_steps = -1
        if delay_steps < 0:
            _issue(issues, "PORT_DELAY_INVALID", "delay_steps must be a non-negative integer.", path=f"{path}.solver.delay_steps")
        source_payload = _normal_payload(source["payload"])
        target_payload = _normal_payload(target["payload"])
        if source_payload != target_payload and not str(transform.get("policy") or "").strip():
            _issue(issues, "PORT_TRANSFORM_REQUIRED", "Payload/unit mismatch requires an explicit transform policy.", path=f"{path}.transform")
        if str(source["timing"].get("rate_policy")) != str(target["timing"].get("rate_policy")) and not str(transform.get("resampling") or "").strip():
            _issue(issues, "PORT_RESAMPLING_POLICY_REQUIRED", "Timing-policy mismatch requires transform.resampling metadata.", path=f"{path}.transform.resampling")
        if target["fan_in"] == "one" and target_id in seen_targets:
            _issue(issues, "PORT_TARGET_FAN_IN_EXCEEDED", f"Input port {target_id!r} permits one driver.", path=path)
        seen_targets.add(target_id)
        source_counts[source_id] = source_counts.get(source_id, 0) + 1
        bindings.append(_binding_payload(
            binding_id=binding_id,
            source_port=source,
            target_port=target,
            raw_mapping=mapping,
            transform=transform,
            solver={"policy": solver_policy, "delay_steps": max(0, delay_steps)},
            contract_source="explicit-v5",
        ))

    for source_id, count in source_counts.items():
        source = port_by_id[source_id]
        if source["fan_out"] == "one" and count > 1:
            _issue(issues, "PORT_SOURCE_FAN_OUT_EXCEEDED", f"Output port {source_id!r} permits one consumer.", details={"count": count})

    missing = sorted(set(registered_pairs) - consumed_mapping_pairs)
    for source_path, target_path in missing:
        _issue(
            issues,
            "PORT_FIELD_MAPPING_UNDECLARED",
            "composition.field_mappings contains a coupling not declared in simulation_ports.bindings.",
            details={"source_path": source_path, "target_path": target_path},
        )

    clock = dict(_mapping(explicit.get("clock")))
    for key in ("owner", "policy", "sample_period_source"):
        if key not in clock or clock.get(key) in (None, ""):
            _issue(issues, "PORT_CLOCK_METADATA_MISSING", f"Explicit simulation_ports.clock must declare {key}.", path=f"$.simulation_ports.clock.{key}")
    normalized_clock = {
        "owner": str(clock.get("owner") or contract.capability_id),
        "policy": str(clock.get("policy") or "parent_runtime"),
        "sample_period_source": clock.get("sample_period_source") or "simulation.sample_s",
    }
    payload: dict[str, Any] = {
        "schema_version": SIMULATION_PORT_SCHEMA_VERSION,
        "source": "explicit-v5",
        "strict": True,
        "clock": normalized_clock,
        "ports": ports,
        "bindings": bindings,
    }
    payload["fingerprint"] = _port_contract_fingerprint(payload)
    payload["validation"] = {
        "ok": not any(item.severity == "error" for item in issues),
        "issues": [item.to_dict() for item in issues],
        "errors": [item.to_dict() for item in issues if item.severity == "error"],
        "warnings": [item.to_dict() for item in issues if item.severity != "error"],
    }
    return payload


def _legacy_inferred_contract(
    contract: CapabilityContract,
    modules: list[dict[str, Any]],
    raw_mappings: list[Mapping[str, Any]],
) -> dict[str, Any]:
    aliases = {str(item["alias"]) for item in modules}
    capability_by_alias = {str(item["alias"]): item.get("capability_id") for item in modules}
    ports: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_mappings):
        source_path, target_path = _field_mapping_paths(raw)
        if not source_path or not target_path:
            continue
        binding_id = f"binding-{index:03d}"
        source_alias = _resolve_alias(source_path, aliases)
        target_alias = _resolve_alias(target_path, aliases)
        source_payload = _lookup_source_payload(source_path, source_alias, capability_by_alias.get(source_alias))
        if source_payload is None:
            source_payload = _semantic_payload(source_path, series=("trace." in source_path.lower() or "profile" in source_path.lower()))
        target_payload = _lookup_target_payload(target_path, target_alias, capability_by_alias.get(target_alias))
        if target_payload is None:
            target_payload = _semantic_payload(target_path, series="profile" in target_path.lower())

        # Preserve V4 binding-specific port IDs exactly.  This is deliberate:
        # legacy assembly JSON can therefore be opened/validated in V5 without
        # a migration rewrite, while explicit V5 contracts use stable reusable
        # semantic port IDs.
        source_port = {
            "port_id": f"out:{binding_id}",
            "module_alias": source_alias,
            "direction": "output",
            "path": source_path,
            "payload": dict(source_payload),
            "timing": {"domain": "sampled", "rate_policy": "parent_owned", "sample_period_source": "simulation.sample_s"},
            "direct_feedthrough": False,
            "state_owner": source_alias,
            "fan_in": "one",
            "fan_out": "one",
            "required": True,
            "description": "V4 compatibility port inferred from composition.field_mappings.",
        }
        target_port = {
            "port_id": f"in:{binding_id}",
            "module_alias": target_alias,
            "direction": "input",
            "path": target_path,
            "payload": dict(target_payload),
            "timing": {"domain": "sampled", "rate_policy": "parent_owned", "sample_period_source": "simulation.sample_s"},
            "direct_feedthrough": False,
            "state_owner": target_alias,
            "fan_in": "one",
            "fan_out": "many",
            "required": True,
            "description": "V4 compatibility port inferred from composition.field_mappings.",
        }
        ports.extend((source_port, target_port))
        bindings.append(_binding_payload(
            binding_id=binding_id,
            source_port=source_port,
            target_port=target_port,
            raw_mapping=raw,
            transform={"policy": str(raw.get("policy") or ("identity" if source_payload == target_payload else "legacy_registered_transform"))},
            solver={"policy": "parent_owned", "delay_steps": 0},
            contract_source="legacy-inferred-v4",
        ))

    issues = [SimulationPortIssue(
        code="PORT_CONTRACT_LEGACY_INFERRED",
        message="simulation_ports is not explicitly declared; ports were inferred from V4 field mappings.",
        severity="warning",
        details={"capability_id": contract.capability_id},
    )]
    payload: dict[str, Any] = {
        "schema_version": SIMULATION_PORT_SCHEMA_VERSION,
        "source": "legacy-inferred-v4",
        "strict": False,
        "clock": {"owner": contract.capability_id, "policy": "parent_runtime", "sample_period_source": "simulation.sample_s"},
        "ports": ports,
        "bindings": bindings,
    }
    payload["fingerprint"] = _port_contract_fingerprint(payload)
    payload["validation"] = {
        "ok": True,
        "issues": [item.to_dict() for item in issues],
        "errors": [],
        "warnings": [item.to_dict() for item in issues],
    }
    return payload


def build_simulation_port_contract(
    contract: CapabilityContract,
    modules: list[dict[str, Any]],
    raw_mappings: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the formal V5 port contract for one composite Capability."""

    explicit = contract.data.get("simulation_ports")
    if isinstance(explicit, Mapping):
        return _explicit_contract(contract, modules, raw_mappings, explicit)
    return _legacy_inferred_contract(contract, modules, raw_mappings)


__all__ = [
    "SIMULATION_PORT_SCHEMA_VERSION",
    "MAX_PORTS",
    "MAX_BINDINGS",
    "MAX_PORT_ID_LENGTH",
    "SimulationPortIssue",
    "build_simulation_port_contract",
    "payload_type",
]
