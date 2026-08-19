"""Episode-level evidence for faults, degradations and runtime constraints.

An active event window is scheduling evidence only.  Physical evidence requires
at least one expected non-label observable and an effect marker or a measurable
change.  This prevents a timestamp/label from being reported as a verified
fault effect.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sat_sim.bsk_engine.types import BSKEventSpec

from .fault_catalog import get_fault_contract
from .fault_observation import FaultObservation, observations_for_window
from .fault_state import state_from_event


def _prefixes_from_observables(fields: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(field).replace("*", "") for field in fields if str(field).strip())


def _active_count(observations: Sequence[FaultObservation]) -> int:
    return sum(1 for o in observations if o.phase == "during" and o.active)


def _effect_seen(effect: str, observations: Sequence[FaultObservation]) -> bool:
    needle = str(effect).strip()
    if not needle:
        return False
    for obs in observations:
        text = " ".join(str(v) for v in obs.selected_values.values())
        if needle in text:
            return True
    return False


def _is_physical_field(name: str) -> bool:
    return not (
        name.startswith("label.")
        or name.endswith("active_effects")
        or name.startswith("event.")
        or name.startswith("modifier.")
    )


def _physical_observable_fields(observations: Sequence[FaultObservation]) -> tuple[str, ...]:
    fields: set[str] = set()
    for obs in observations:
        if obs.phase != "during":
            continue
        fields.update(k for k in obs.selected_values if _is_physical_field(str(k)))
    return tuple(sorted(fields))


def _different(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left != right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        scale = max(1.0, abs(float(left)), abs(float(right)))
        return abs(float(left) - float(right)) > 1e-9 * scale
    return left != right


def _changed_physical_fields(observations: Sequence[FaultObservation]) -> tuple[str, ...]:
    before = next((o for o in reversed(observations) if o.phase == "before"), None)
    during = [o for o in observations if o.phase == "during"]
    if not during:
        return ()
    fields = _physical_observable_fields(observations)
    changed: list[str] = []
    for field in fields:
        values = [o.selected_values[field] for o in during if field in o.selected_values]
        if not values:
            continue
        if before is not None and field in before.selected_values:
            if any(_different(before.selected_values[field], value) for value in values):
                changed.append(field)
        elif any(_different(values[0], value) for value in values[1:]):
            changed.append(field)
        else:
            # A contract-specific effective value is still physical evidence even
            # when constant throughout a short event window.
            changed.append(field)
    return tuple(sorted(set(changed)))


def _timeline_label_seen(category: str, observations: Sequence[FaultObservation]) -> bool:
    key = f"label.{category}_active"
    return any(bool(o.selected_values.get(key)) for o in observations if o.phase == "during")


@dataclass(frozen=True)
class FaultEpisode:
    episode_id: str
    category: str
    effect: str
    display_name_zh: str
    target: str
    target_kind: str
    start_s: float
    end_s: float | None
    trigger_semantics: str
    state: dict[str, Any]
    observations: tuple[FaultObservation, ...] = ()
    evidence_status: str = "not_evaluated"
    evidence_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["observations"] = [o.to_dict() for o in self.observations]
        out["state"] = dict(self.state)
        out["evidence_summary"] = dict(self.evidence_summary)
        return out


def build_fault_episode(event: BSKEventSpec, trace_rows: Sequence[Mapping[str, Any]]) -> FaultEpisode:
    contract = get_fault_contract(event.effect, event.category)
    state = state_from_event(event)
    prefixes = _prefixes_from_observables(contract.expected_observables)
    observations = observations_for_window(trace_rows, event.start_s, event.end_s, prefixes)
    active_samples = _active_count(observations)
    effect_text_seen = _effect_seen(event.effect, observations)
    timeline_label_seen = _timeline_label_seen(event.category, observations)
    physical_fields = _physical_observable_fields(observations)
    changed_fields = _changed_physical_fields(observations)

    if active_samples == 0:
        status = "scheduled_not_observed"
    elif contract.evidence_policy == "timeline_only":
        status = "observed_timeline_only" if timeline_label_seen else "timeline_label_missing"
    elif not physical_fields:
        status = "missing_observables"
    elif effect_text_seen or changed_fields:
        status = "observed"
    else:
        status = "effect_not_observed"

    return FaultEpisode(
        episode_id=f"episode::{event.event_id}",
        category=event.category,
        effect=event.effect,
        display_name_zh=contract.display_name_zh,
        target=event.target,
        target_kind=contract.target_kind,
        start_s=event.start_s,
        end_s=event.end_s,
        trigger_semantics=contract.trigger_semantics,
        state=state.to_dict(),
        observations=tuple(observations),
        evidence_status=status,
        evidence_summary={
            "active_sample_count": active_samples,
            "observation_count": len(observations),
            "effect_text_seen": effect_text_seen,
            "timeline_label_seen": timeline_label_seen,
            "physical_observable_fields": list(physical_fields),
            "changed_physical_fields": list(changed_fields),
            "expected_observables": list(contract.expected_observables),
            "evidence_policy": contract.evidence_policy,
            "physical_effect_verified": status == "observed",
        },
    )


def build_fault_episodes(events: Sequence[BSKEventSpec], trace_rows: Sequence[Mapping[str, Any]]) -> tuple[FaultEpisode, ...]:
    return tuple(build_fault_episode(event, trace_rows) for event in events)


__all__ = ["FaultEpisode", "build_fault_episode", "build_fault_episodes"]
