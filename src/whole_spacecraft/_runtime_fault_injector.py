"""Whole-spacecraft runtime fault scheduler.

Ownership boundary
------------------
This module owns event registration, reversible mutation bookkeeping, recovery,
and audit evidence.  It does not implement component fault physics.  Runtime
mapping follows ``whole_spacecraft -> subsystems.runtime_fault_router ->
subsystem faults -> component faults``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional
import uuid

try:
    from Basilisk.utilities import SimulationBaseClass
    BASILISK_AVAILABLE = True
except ImportError:
    SimulationBaseClass = None  # type: ignore[assignment]
    BASILISK_AVAILABLE = False

from subsystems.fault_base import FaultSpec
from subsystems.runtime_fault_router import route_runtime_fault


def _fault_value(spec: FaultSpec) -> str:
    return str(getattr(spec.fault_type, "value", spec.fault_type))


def _fault_name(spec: FaultSpec) -> str:
    return str(getattr(spec.fault_type, "name", _fault_value(spec)))


@dataclass
class FaultRecord:
    spec: FaultSpec
    originals: dict[str, Any] = field(default_factory=dict)
    post_restore_actions: list[Callable[[], None]] = field(default_factory=list)
    restore_errors: list[str] = field(default_factory=list)


class FaultInjector:
    """Schedule runtime events and delegate their implementation to subsystems."""

    def __init__(
        self,
        sim: Any,
        spacecraft: Any,
        fault_specs: List[FaultSpec],
        component_registry: Mapping[str, Any] | None = None,
    ):
        self.sim = sim
        self.spacecraft = spacecraft
        self.fault_specs = fault_specs
        self.component_registry = dict(component_registry or {})
        self.active_faults: Dict[str, FaultRecord] = {}
        self.fault_events: Dict[str, Any] = {}

    def schedule_all_faults(self) -> None:
        for spec in self.fault_specs:
            fault_id = self._generate_fault_id(spec)
            onset_time_ns = int(float(spec.onset_time_s) * 1e9)
            self._schedule_fault_injection(fault_id, spec, onset_time_ns)
            if spec.is_transient():
                recovery_time_ns = int((float(spec.onset_time_s) + float(spec.duration_s)) * 1e9)
                self._schedule_fault_recovery(fault_id, spec, recovery_time_ns)

    def _generate_fault_id(self, spec: FaultSpec) -> str:
        fault_type_str = _fault_value(spec)
        base_id = f"{spec.target_id}_{fault_type_str}" if spec.target_id else fault_type_str
        return f"{base_id}_{uuid.uuid4().hex[:8]}"

    def _create_scheduled_action(
        self,
        action: Callable[[FaultSpec], None],
        spec: FaultSpec,
        fault_id: str,
        phase: str,
    ) -> Callable[[Any], None]:
        def wrapper(parent_sim: Any = None) -> None:
            event_info = self.fault_events.setdefault(fault_id, {"spec": spec})
            event_info[f"{phase}_triggered"] = True
            event_info[f"{phase}_trigger_time_ns"] = self._current_time_ns(parent_sim)
            action(spec)
        return wrapper

    def _current_time_ns(self, parent_sim: Any = None) -> int | None:
        for candidate in (parent_sim, self.sim):
            try:
                return int(candidate.TotalSim.CurrentNanos)
            except Exception:
                continue
        return None

    def _event_map(self) -> Mapping[str, Any]:
        try:
            event_map = getattr(self.sim, "eventMap")
            if isinstance(event_map, Mapping):
                return event_map
            return dict(event_map)
        except Exception:
            return {}

    def _register_basilisk_event(
        self,
        *,
        event_name: str,
        action: Callable[[Any], None],
        trigger_time_ns: int,
    ) -> tuple[bool, str | None]:
        if not (BASILISK_AVAILABLE and self.sim is not None and hasattr(self.sim, "createNewEvent")):
            return False, "Basilisk SimBaseClass.createNewEvent unavailable"
        try:
            self.sim.createNewEvent(
                event_name,
                eventRate=max(1, int(trigger_time_ns) if int(trigger_time_ns) > 0 else 1),
                eventActive=True,
                conditionTime=int(trigger_time_ns),
                actionFunction=action,
                exactRateMatch=False,
            )
            registered = event_name in self._event_map()
            return bool(registered), None if registered else "event not found in sim.eventMap after registration"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _schedule_fault_injection(self, fault_id: str, spec: FaultSpec, onset_time_ns: int) -> None:
        event_name = f"fault_inject_{fault_id}"
        action = self._create_scheduled_action(self._inject_fault, spec, fault_id, "injection")
        registered, error = self._register_basilisk_event(
            event_name=event_name,
            action=action,
            trigger_time_ns=onset_time_ns,
        )
        self.fault_events[fault_id] = {
            "injection_event": event_name if registered else None,
            "spec": spec,
            "onset_time_ns": int(onset_time_ns),
            "event_registered": bool(registered),
            "injection_triggered": False,
            "fallback": not bool(registered),
            "scheduler": "basilisk_event_condition_time" if registered else "unregistered",
            "registration_error": error,
            "mutation_targets": [],
        }

    def _schedule_fault_recovery(self, fault_id: str, spec: FaultSpec, recovery_time_ns: int) -> None:
        event_name = f"fault_recover_{fault_id}"
        action = self._create_scheduled_action(self._recover_fault, spec, fault_id, "recovery")
        registered, error = self._register_basilisk_event(
            event_name=event_name,
            action=action,
            trigger_time_ns=recovery_time_ns,
        )
        info = self.fault_events.setdefault(fault_id, {"spec": spec, "mutation_targets": []})
        info["recovery_event"] = event_name if registered else None
        info["recovery_time_ns"] = int(recovery_time_ns)
        info["recovery_registered"] = bool(registered)
        info["recovery_triggered"] = False
        if error:
            info["recovery_registration_error"] = error
        if not registered:
            info["fallback"] = True

    def inject_immediate_fault(self, spec: FaultSpec) -> str:
        """Inject one event immediately while retaining normal audit evidence."""

        fault_id = self._generate_fault_id(spec)
        self.fault_events[fault_id] = {
            "injection_event": "immediate",
            "spec": spec,
            "onset_time_ns": 0,
            "event_registered": True,
            "injection_triggered": True,
            "injection_trigger_time_ns": self._current_time_ns(),
            "fallback": False,
            "scheduler": "immediate",
            "mutation_targets": [],
        }
        self.active_faults[fault_id] = FaultRecord(spec=spec)
        self._inject_fault(spec)
        return fault_id

    def _inject_fault(self, spec: FaultSpec) -> None:
        fault_id = self._find_fault_id_by_spec(spec)
        if fault_id is None:
            fault_id = self._generate_fault_id(spec)
            self.fault_events[fault_id] = {
                "spec": spec,
                "event_registered": False,
                "injection_triggered": True,
                "fallback": True,
                "scheduler": "unscheduled_direct_call",
                "mutation_targets": [],
            }
        self.active_faults.setdefault(fault_id, FaultRecord(spec=spec))

        result = route_runtime_fault(
            spec,
            self.component_registry,
            set_attr=lambda obj, attr, value: self._set_attr_with_backup(spec, obj, attr, value),
            resolve_component=self._get_component,
            record_mutation=lambda obj, attr, before, after: self._record_direct_mutation(spec, obj, attr, before, after),
            register_post_restore=lambda action: self._register_post_restore(spec, action),
        )
        info = self.fault_events.setdefault(fault_id, {"spec": spec, "mutation_targets": []})
        info["route"] = {
            "handled": result.handled,
            "subsystem": result.subsystem,
            "fault_value": result.fault_value,
            "target_id": result.target_id,
            "reason": result.reason,
            "component_results": [dict(item) for item in result.mutations],
        }
        if result.handled and result.mutations and info.get("first_mutation_time_ns") is None:
            info["first_mutation_time_ns"] = self._current_time_ns()

    def _recover_fault(self, spec: FaultSpec) -> None:
        fault_id = self._find_fault_id_by_spec(spec) or self._find_active_fault_id(spec)
        record = self.active_faults.pop(fault_id, None) if fault_id else None
        if isinstance(record, FaultRecord):
            self._restore_record(record)
            info = self.fault_events.setdefault(fault_id, {"spec": spec, "mutation_targets": []})
            info["restore_errors"] = list(record.restore_errors)
            info["recovery_restore_status"] = "PASS" if not record.restore_errors else "FAIL"

    def _set_attr_with_backup(self, spec: FaultSpec, obj: Any, attr: str, value: Any) -> None:
        if obj is None or not hasattr(obj, attr):
            return
        fault_id, record = self._ensure_record(spec)
        key = f"{id(obj)}:{attr}"
        before = getattr(obj, attr)
        if key not in record.originals:
            record.originals[key] = (obj, attr, before)
        setattr(obj, attr, value)
        self._append_mutation(fault_id, record.spec, obj, attr, before, value)

    def _record_direct_mutation(self, spec: FaultSpec, obj: Any, attr: str, before: Any, after: Any) -> None:
        fault_id, record = self._ensure_record(spec)
        self._append_mutation(fault_id, record.spec, obj, attr, before, after)

    def _register_post_restore(self, spec: FaultSpec, action: Callable[[], None]) -> None:
        _fault_id, record = self._ensure_record(spec)
        if action not in record.post_restore_actions:
            record.post_restore_actions.append(action)

    def _ensure_record(self, spec: FaultSpec) -> tuple[str, FaultRecord]:
        fault_id = self._find_fault_id_by_spec(spec) or self._find_active_fault_id(spec)
        if fault_id is None:
            fault_id = self._generate_fault_id(spec)
            self.fault_events.setdefault(fault_id, {"spec": spec, "mutation_targets": []})
        record = self.active_faults.get(fault_id)
        if not isinstance(record, FaultRecord):
            record = FaultRecord(spec=spec)
            self.active_faults[fault_id] = record
        return fault_id, record

    def _append_mutation(
        self,
        fault_id: str,
        spec: FaultSpec,
        obj: Any,
        attr: str,
        before: Any,
        after: Any,
    ) -> None:
        info = self.fault_events.setdefault(fault_id, {"spec": spec, "mutation_targets": []})
        info.setdefault("mutation_targets", []).append({
            "target_id": str(getattr(spec, "target_id", "") or ""),
            "object_class": type(obj).__name__,
            "object_tag": str(getattr(obj, "ModelTag", getattr(obj, "name", ""))),
            "attr": attr,
            "before": self._safe_repr(before),
            "after": self._safe_repr(after),
            "time_ns": self._current_time_ns(),
        })

    def _restore_record(self, record: FaultRecord) -> None:
        for obj, attr, value in reversed(tuple(record.originals.values())):
            try:
                setattr(obj, attr, value)
            except Exception as exc:
                record.restore_errors.append(
                    f"restore {type(obj).__name__}.{attr} failed: {type(exc).__name__}: {exc}"
                )
        for action in record.post_restore_actions:
            try:
                action()
            except Exception as exc:
                record.restore_errors.append(
                    f"post-restore action failed: {type(exc).__name__}: {exc}"
                )

    def is_fault_active(self, fault_id: str) -> bool:
        return fault_id in self.active_faults

    def get_active_faults(self) -> Dict[str, FaultSpec]:
        return {
            fault_id: record.spec if isinstance(record, FaultRecord) else record
            for fault_id, record in self.active_faults.items()
        }

    def _find_fault_id_by_spec(self, spec: FaultSpec) -> Optional[str]:
        for fault_id, fault_info in self.fault_events.items():
            if fault_info.get("spec") == spec:
                return fault_id
        return None

    def _find_active_fault_id(self, spec: FaultSpec) -> Optional[str]:
        for fault_id, record in self.active_faults.items():
            if isinstance(record, FaultRecord) and record.spec == spec:
                return fault_id
            if record == spec:
                return fault_id
        return None

    def _safe_repr(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        try:
            text = repr(value)
        except Exception:
            text = f"<{type(value).__name__}>"
        return text[:240]

    def event_audit(self) -> dict[str, Any]:
        events: dict[str, Any] = {}
        sim_event_map = self._event_map()
        for fault_id, info in self.fault_events.items():
            item = dict(info)
            spec = item.get("spec")
            if spec is not None:
                item["spec"] = {
                    "fault_type": _fault_value(spec),
                    "fault_name": _fault_name(spec),
                    "target_id": str(getattr(spec, "target_id", "") or ""),
                    "onset_time_s": float(getattr(spec, "onset_time_s", 0.0)),
                    "duration_s": float(getattr(spec, "duration_s", -1.0)),
                    "magnitude": float(getattr(spec, "magnitude", 0.0)),
                }
            event_name = item.get("injection_event")
            if event_name in sim_event_map:
                event_obj = sim_event_map[event_name]
                item["event_registered"] = True
                item["event_occur_counter"] = int(getattr(event_obj, "occurCounter", 0))
                item["injection_triggered"] = bool(item.get("injection_triggered")) or int(getattr(event_obj, "occurCounter", 0)) > 0
                item["event_active_after_run"] = bool(getattr(event_obj, "eventActive", False))
            recovery_name = item.get("recovery_event")
            if recovery_name in sim_event_map:
                event_obj = sim_event_map[recovery_name]
                item["recovery_occur_counter"] = int(getattr(event_obj, "occurCounter", 0))
                item["recovery_triggered"] = bool(item.get("recovery_triggered")) or int(getattr(event_obj, "occurCounter", 0)) > 0
            requested_start_ns = item.get("onset_time_ns")
            requested_end_ns = item.get("recovery_time_ns")
            actual_start_ns = item.get("injection_trigger_time_ns")
            first_mutation_ns = item.get("first_mutation_time_ns")
            actual_end_ns = item.get("recovery_trigger_time_ns")
            item["requested_start_time_s"] = None if requested_start_ns is None else float(requested_start_ns) / 1e9
            item["actual_trigger_time_s"] = None if actual_start_ns is None else float(actual_start_ns) / 1e9
            item["first_observable_mutation_time_s"] = None if first_mutation_ns is None else float(first_mutation_ns) / 1e9
            item["requested_end_time_s"] = None if requested_end_ns is None else float(requested_end_ns) / 1e9
            item["actual_recovery_time_s"] = None if actual_end_ns is None else float(actual_end_ns) / 1e9
            item["trigger_quantization_delay_s"] = (
                None if actual_start_ns is None or requested_start_ns is None
                else (float(actual_start_ns) - float(requested_start_ns)) / 1e9
            )
            events[fault_id] = item
        registered_count = sum(1 for item in events.values() if item.get("event_registered"))
        triggered_count = sum(1 for item in events.values() if item.get("injection_triggered"))
        fallback_count = sum(1 for item in events.values() if item.get("fallback"))
        mutation_count = sum(len(item.get("mutation_targets", ()) or ()) for item in events.values())
        routed_count = sum(1 for item in events.values() if bool((item.get("route") or {}).get("handled")))
        recovery_expected = [item for item in events.values() if item.get("recovery_time_ns") is not None]
        recovery_registered_count = sum(1 for item in recovery_expected if item.get("recovery_registered"))
        recovery_triggered_count = sum(1 for item in recovery_expected if item.get("recovery_triggered"))
        recovery_restore_failure_count = sum(
            1 for item in recovery_expected if item.get("recovery_restore_status") == "FAIL"
        )
        status = (
            "PASS"
            if events
            and registered_count == len(events)
            and triggered_count == len(events)
            and fallback_count == 0
            and routed_count == len(events)
            and mutation_count > 0
            and recovery_registered_count == len(recovery_expected)
            and recovery_triggered_count == len(recovery_expected)
            and recovery_restore_failure_count == 0
            else "FAIL"
        )
        return {
            "fault_count": len(events),
            "event_registered_count": registered_count,
            "event_triggered_count": triggered_count,
            "fallback_count": fallback_count,
            "routed_count": routed_count,
            "mutation_target_count": mutation_count,
            "recovery_expected_count": len(recovery_expected),
            "recovery_registered_count": recovery_registered_count,
            "recovery_triggered_count": recovery_triggered_count,
            "recovery_restore_failure_count": recovery_restore_failure_count,
            "runtime_event_status": status,
            "events": events,
        }

    def _get_component(self, target_id: Optional[str], component_type: str) -> Optional[Any]:
        if target_id and target_id in self.component_registry:
            return self.component_registry[target_id]
        if self.spacecraft is None:
            return None
        if target_id:
            mock_children = getattr(self.spacecraft, "_mock_children", None)
            if isinstance(mock_children, dict) and target_id in mock_children:
                return getattr(self.spacecraft, target_id)
            if hasattr(self.spacecraft, "__dict__") and target_id in vars(self.spacecraft):
                return vars(self.spacecraft)[target_id]
            components = getattr(self.spacecraft, "components", None)
            if isinstance(components, dict) and target_id in components:
                return components[target_id]
        component_list = getattr(self.spacecraft, f"{component_type}s", None)
        if isinstance(component_list, (list, tuple)):
            for component in component_list:
                if target_id in {
                    getattr(component, "name", None),
                    getattr(component, "id", None),
                    getattr(component, "ModelTag", None),
                }:
                    return component
        models = getattr(self.spacecraft, "models", None)
        if isinstance(models, (list, tuple)):
            for model in models:
                if target_id in {getattr(model, "ModelTag", None), getattr(model, "name", None)}:
                    return model
        return None


__all__ = ["FaultInjector", "FaultRecord", "BASILISK_AVAILABLE"]
