"""Construction-derived electrical authorities and native message evidence.

An electrical provider is a model and an actual output message port, not a
coupling edge, class name, or ModelTag.  Destination and recorder labels come
from explicit builder bindings; this module independently checks scheduled
providers and reads their real messages.  It does not infer battery or thermal
connections from those labels, or infer official provenance from SysModel
inheritance.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable


SCHEMA_VERSION = "sat-sim.power-authority-inventory.v1"

_PROJECT_AUTHORITIES = frozenset({
    "subsystems.thermal.thermal_network.ThermalHeater",
    "components.thermal_node.basilisk_scheduled.ThermalNodeScheduledSysModel",
})
_LEGACY_POWER_PROXIES = frozenset({
    "integration.resource_feedback.DataActivityPowerBridge",
    "integration.resource_feedback.DownlinkActivityPowerBridge",
    "integration.resource_feedback.AdcsControlPowerBridge",
    "integration.resource_feedback.StatusPowerBridge",
    "integration.resource_feedback.ThrusterActivityPowerBridge",
    "components.thermal_node.basilisk_scheduled.HeaterPowerFeedback",
})
_NON_ELECTRICAL_POWER_PORTS = frozenset({
    ("subsystems.thermal.thermal_network.ThermalHeater", "powerOutMsg"),
    ("subsystems.thermal.thermal_network.ThermalPowerBridge", "heatOutMsg"),
    ("subsystems.thermal.thermal_network.SolarHeatInput", "heatOutMsg"),
    ("components.thermal_node.basilisk_scheduled.ConstantThermalPowerInput", "heatOutMsg"),
    ("components.thermal_node.basilisk_scheduled.DynamicThermalPowerInput", "heatOutMsg"),
    ("components.thermal_node.basilisk_scheduled.ModeThermalInput", "heatOutMsg"),
    ("components.thermal_node.basilisk_scheduled.EclipseShadowFactorConverter", "shadowFactorOutMsg"),
    ("components.thermal_node.builder.ConstantThermalPowerInput", "heatOutMsg"),
    ("subsystems.thermal.basilisk_scheduled.ConstantHeatInput", "heatOutMsg"),
})


@dataclass(frozen=True)
class PowerAuthorityBinding:
    """One real output and its builder-owned role, labels, and assumptions."""

    model: Any
    output_attr: str
    role: str
    recorder_names: tuple[str, ...] = ()
    thermal_destinations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


def _class_name(model: Any) -> str:
    cls = type(model)
    return f"{cls.__module__}.{cls.__qualname__}"


def _identity(value: Any) -> tuple[str, int]:
    # SWIG properties produce new Python wrappers for the same C++ message.
    # Compare the native identity internally; never publish memory addresses.
    native = getattr(value, "this", None)
    if native is not None:
        try:
            return "native", int(native)
        except (TypeError, ValueError):
            pass
    return "python", id(value)


def _is_recorder(model: Any) -> bool:
    cls = type(model)
    return cls.__module__.startswith("Basilisk.architecture.messaging.") and cls.__name__.endswith("Recorder")


def _origin(model: Any, *, electrical: bool = False) -> str:
    name = _class_name(model)
    if name.startswith("Basilisk.simulation."):
        return "official"
    if name in _LEGACY_POWER_PROXIES:
        return "engineering_proxy"
    if electrical:
        return "project_physical" if name in _PROJECT_AUTHORITIES else "unknown"
    if name.startswith("Basilisk.fswAlgorithms."):
        return "official"
    if _is_recorder(model):
        return "official"
    if name.startswith(("integration.", "components.", "subsystems.", "whole_spacecraft.", "sat_sim.")):
        return "project_physical"
    return "unknown"


def _model_description(model: Any, *, electrical: bool = False) -> dict[str, Any]:
    return {
        "model_class": _class_name(model),
        "model_tag": str(getattr(model, "ModelTag", "")),
        "origin": _origin(model, electrical=electrical),
    }


def _labels(values: Iterable[str] | str) -> list[str]:
    if isinstance(values, str):
        values = (values,)
    return list(dict.fromkeys(str(value) for value in values))


def _merge_labels(target: list[str], values: Iterable[str] | str) -> None:
    for value in _labels(values):
        if value not in target:
            target.append(value)


def _message_evidence(message: Any, *, electrical: bool) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "message_class": _class_name(message),
        "written": None,
        "read_status": "ERROR",
        "source_time_ns": None,
        "error": None,
    }
    if electrical:
        evidence["net_power_w"] = None
    try:
        from Basilisk.architecture import messaging

        # Output messages do not themselves expose isWritten/timeWritten in
        # official Basilisk 2.11; a subscribed reader provides that evidence.
        reader_type = messaging.PowerNodeUsageMsgReader if electrical else getattr(
            messaging, f"{type(message).__name__}Reader"
        )
        reader = reader_type()
        reader.subscribeTo(message)
        if not reader.isLinked():
            raise ValueError("output message reader is not linked")
        evidence["written"] = bool(reader.isWritten())
        if not evidence["written"]:
            evidence["read_status"] = "NOT_READY"
            evidence["error"] = "output message has not been written"
            return evidence
        evidence["source_time_ns"] = int(reader.timeWritten())
        payload = reader()
        if electrical:
            power = float(payload.netPower)
            if not math.isfinite(power):
                raise ValueError("electrical netPower is not finite")
            evidence["net_power_w"] = power
        evidence["read_status"] = "VALID"
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    return evidence


def _model_read_evidence(model: Any) -> dict[str, Any]:
    """Preserve current and cumulative diagnostics, including recovered errors."""
    signals: dict[str, str] = {}
    errors: list[str] = []
    for name in ("input_read_status", "enable_read_status"):
        value = getattr(model, name, None)
        if value is not None:
            signals[name] = str(getattr(value, "value", value))
    trace = getattr(model, "trace", ())
    retained_error_count = 0
    if isinstance(trace, (list, tuple)) and trace:
        sample = trace[-1]
        value = getattr(sample, "input_status", None)
        if value is not None:
            signals["last_sample_input_status"] = str(getattr(value, "value", value))
        if getattr(sample, "error", None):
            errors.append(str(sample.error))
        # Older status adapters retain their complete trace instead of a
        # cumulative counter. Later recovery must not erase a bad interval.
        for sample in trace:
            value = getattr(sample, "input_status", None)
            if str(getattr(value, "value", value)) == "ERROR":
                retained_error_count += 1
                if len(errors) < 10:
                    _merge_labels(errors, (str(getattr(sample, "error", None) or "invalid native input"),))
    counts = {
        name: int(getattr(model, name, 0))
        for name in ("input_error_count", "input_read_error_count", "enable_error_count")
    }
    error_count = max(sum(counts.values()), retained_error_count)
    cumulative_error_present = error_count > 0
    for name in ("input_errors", "input_read_errors", "read_errors"):
        values = getattr(model, name, ())
        if values:
            # This current-read diagnostic list also contains startup
            # NOT_READY details. Cumulative failures have their own counters
            # or input_errors/read_errors lists.
            if name != "input_read_errors":
                cumulative_error_present = True
            _merge_labels(errors, [str(value) for value in list(values)[:10]])
    for name in ("input_read_error", "enable_read_error"):
        value = getattr(model, name, None)
        if value:
            _merge_labels(errors, (str(value),))
    status = "VALID"
    if cumulative_error_present or "ERROR" in signals.values():
        status = "ERROR"
    elif "NOT_READY" in signals.values():
        status = "NOT_READY"
    return {
        "status": status,
        "signals": signals,
        "input_error_count": error_count,
        "retained_error_sample_count": retained_error_count,
        "errors": errors[:10],
    }


def _electrical_ports(model: Any) -> list[str]:
    from Basilisk.architecture import messaging
    # Native recorder wrappers retain a source-message reference for grouped
    # export; that reference is an input, never a second electrical authority.
    if _is_recorder(model):
        return []
    # Python-owned ports can have arbitrary names. Native message properties
    # use the Basilisk *OutMsg interface. Do not evaluate unrelated SWIG
    # properties: some expose optional C++ pointers that are not safe to read.
    candidates = dict(getattr(model, "__dict__", {}))
    for name in dir(model):
        if name.endswith("OutMsg") and name not in candidates:
            candidates[name] = getattr(model, name, None)
    ports = []
    for name, message in candidates.items():
        if (_class_name(model), name) in _NON_ELECTRICAL_POWER_PORTS:
            continue
        if isinstance(message, messaging.PowerNodeUsageMsg):
            ports.append(name)
    return sorted(ports)


def _ancillary_records(models: Iterable[Any], role: str) -> list[dict[str, Any]]:
    records: dict[tuple[str, int], dict[str, Any]] = {}
    for item in models:
        binding = item if isinstance(item, PowerAuthorityBinding) else None
        model = binding.model if binding is not None else item
        if model is None:
            continue
        key = _identity(model)
        if key not in records:
            records[key] = {
                **_model_description(model),
                "role": role,
                "model_read_evidence": _model_read_evidence(model),
                "outputs": {},
            }
        record = records[key]
        names = [binding.output_attr] if binding is not None else [
            name for name in dir(model) if name.endswith("OutMsg")
        ]
        for name in names:
            message = getattr(model, name, None)
            if message is not None:
                record["outputs"][name] = _message_evidence(message, electrical=False)
    return list(records.values())


def build_power_authority_inventory(
    runtime: str,
    authorities: Iterable[PowerAuthorityBinding],
    status_adapters: Iterable[Any] = (),
    thermal_bridges: Iterable[Any] = (),
    scheduled_models: Iterable[Any] = (),
) -> dict[str, Any]:
    """Return JSON-safe source evidence without modifying model state/messages.

    An omitted/empty scheduled census leaves scheduling unverified. With a
    census, unregistered providers and registered-but-unscheduled providers are
    errors. Unknown electrical implementations always remain visible as errors.
    Unwritten outputs are NOT_READY, including their default all-zero payload.
    Readiness describes this snapshot, not a full-run conservation certificate.
    """
    errors: list[dict[str, Any]] = []
    models = {_identity(model): model for model in scheduled_models if model is not None}
    entries: dict[tuple[tuple[str, int], tuple[str, int]], dict[str, Any]] = {}
    provider_models: dict[tuple[tuple[str, int], tuple[str, int]], Any] = {}

    def error(code: str, model: Any, output_attr: str | None, detail: str) -> None:
        errors.append({
            "code": code, **_model_description(model, electrical=True),
            "output_attr": output_attr, "detail": detail,
        })

    def add(binding: PowerAuthorityBinding, *, registered: bool) -> None:
        model = binding.model
        message = getattr(model, binding.output_attr, None)
        if message is None:
            error("MISSING_ELECTRICAL_OUTPUT", model, binding.output_attr, "declared electrical output is absent")
            return
        if _class_name(model) in _PROJECT_AUTHORITIES:
            electrical_message = getattr(model, "electricalPowerOutMsg", None)
            if electrical_message is None or _identity(message) != _identity(electrical_message):
                error("NON_ELECTRICAL_OUTPUT", model, binding.output_attr, "project heater electrical authority must be its actual electricalPowerOutMsg")
                return
        key = (_identity(model), _identity(message))
        if key not in entries:
            entries[key] = {
                "provider_id": f"electrical_provider_{len(entries) + 1}",
                **_model_description(model, electrical=True),
                "output_attr": binding.output_attr,
                "output_attrs": [],
                "role": binding.role,
                "roles": [],
                "registered": registered,
                "scheduled": key[0] in models if models else None,
                "recorder_names": [],
                "thermal_destinations": [],
                "assumptions": [],
                "message_evidence": _message_evidence(message, electrical=True),
                "model_read_evidence": _model_read_evidence(model),
            }
            provider_models[key] = model
        entry = entries[key]
        _merge_labels(entry["output_attrs"], (binding.output_attr,))
        if registered or not entry["registered"]:
            _merge_labels(entry["roles"], (binding.role,))
        entry["registered"] = entry["registered"] or registered
        for name in ("recorder_names", "thermal_destinations", "assumptions"):
            _merge_labels(entry[name], getattr(binding, name))

    for binding in authorities:
        add(binding, registered=True)
    for model in models.values():
        for name in _electrical_ports(model):
            add(PowerAuthorityBinding(model, name, "unregistered"), registered=False)

    not_ready = False
    for key, entry in entries.items():
        model = provider_models[key]
        output = entry["output_attr"]
        if not entry["registered"]:
            error("UNREGISTERED_ELECTRICAL_PROVIDER", model, output, "scheduled electrical output has no authority binding")
        if entry["origin"] == "unknown":
            error("UNKNOWN_ELECTRICAL_PROVIDER", model, output, "electrical implementation has no recognized source classification")
        if entry["scheduled"] is False:
            error("UNSCHEDULED_ELECTRICAL_PROVIDER", model, output, "bound electrical model is absent from supplied scheduled census")
        evidence = entry["message_evidence"]
        if evidence["read_status"] == "ERROR":
            error("INVALID_ELECTRICAL_MESSAGE", model, output, evidence["error"] or "invalid message")
        not_ready |= evidence["read_status"] == "NOT_READY"
        read_evidence = entry["model_read_evidence"]
        if read_evidence["status"] == "ERROR":
            error("MODEL_INPUT_ERROR", model, output, "; ".join(read_evidence["errors"]) or "model reported an input error")
        not_ready |= read_evidence["status"] == "NOT_READY"

    adapters = _ancillary_records(status_adapters, "status_adapter")
    bridges = _ancillary_records(thermal_bridges, "thermal_bridge")
    for record in adapters + bridges:
        if record["model_read_evidence"]["status"] == "ERROR":
            errors.append({
                "code": "MODEL_INPUT_ERROR", "model_class": record["model_class"],
                "model_tag": record["model_tag"], "origin": record["origin"],
                "output_attr": None,
                "detail": "; ".join(record["model_read_evidence"]["errors"]) or "model reported an input error",
            })
        not_ready |= record["model_read_evidence"]["status"] == "NOT_READY"
        for name, evidence in record["outputs"].items():
            if evidence["read_status"] == "ERROR":
                errors.append({
                    "code": "INVALID_SUPPORT_MESSAGE", "model_class": record["model_class"],
                    "model_tag": record["model_tag"], "origin": record["origin"],
                    "output_attr": name, "detail": evidence["error"],
                })
            not_ready |= evidence["read_status"] == "NOT_READY"

    census = []
    for model_key, model in models.items():
        provider_ids = [entry["provider_id"] for key, entry in entries.items() if key[0] == model_key]
        record = {
            **_model_description(model, electrical=bool(provider_ids)),
            "kind": "recorder" if _is_recorder(model) else "model",
            "electrical_provider_ids": provider_ids,
        }
        if hasattr(model, "heater_hardware_present"):
            record["heater_hardware_present"] = bool(model.heater_hardware_present)
        census.append(record)

    providers = list(entries.values())
    counts = {
        "electrical_provider_count": len(providers),
        "official_provider_count": sum(entry["origin"] == "official" for entry in providers),
        "project_physical_provider_count": sum(entry["origin"] == "project_physical" for entry in providers),
        "engineering_proxy_provider_count": sum(entry["origin"] == "engineering_proxy" for entry in providers),
        "unknown_provider_count": sum(entry["origin"] == "unknown" for entry in providers),
        "unregistered_provider_count": sum(not entry["registered"] for entry in providers),
        "status_adapter_count": len(adapters),
        "thermal_bridge_count": len(bridges),
        "scheduled_model_count": len(census),
        "scheduled_recorder_count": sum(entry["kind"] == "recorder" for entry in census),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime": str(runtime),
        "status": "ERROR" if errors else "NOT_READY" if not_ready else "PASS",
        "counts_scope": "unique_model_and_electrical_output_message",
        "connection_evidence_scope": "builder_supplied_recorder_and_destination_labels",
        "scheduled_census_provided": bool(models),
        "authorities": providers,
        "status_adapters": adapters,
        "thermal_bridges": bridges,
        "scheduled_models": census,
        "counts": counts,
        "errors": errors,
    }


__all__ = ["PowerAuthorityBinding", "build_power_authority_inventory"]
