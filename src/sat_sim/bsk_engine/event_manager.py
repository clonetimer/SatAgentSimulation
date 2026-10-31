"""Event parsing and categorization for BSKSim-style scenarios."""
from __future__ import annotations

import math

from collections.abc import Mapping, Sequence
from typing import Any

from .types import BSKEventSpec


def _event_iter(spec: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    events = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    out: list[tuple[str, Mapping[str, Any]]] = []
    for category in ("faults", "degradations", "constraints"):
        for item in events.get(category, []) or []:
            if isinstance(item, Mapping):
                out.append((category[:-1], item))
    modifiers = spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
    for category in ("faults", "degradations", "constraints"):
        for item in modifiers.get(category, []) or []:
            if isinstance(item, Mapping):
                out.append((category[:-1], item))
    # Runtime conversion retains legacy-fault delivery in top-level arrays.
    # These declarations must reach the same native controllers and evidence
    # as canonical/modifier events, rather than silently running nominally.
    for category in ("faults", "degradations", "constraints"):
        items = spec.get(category)
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
            out.extend((category[:-1], item) for item in items if isinstance(item, Mapping))
    return out


def parse_bsk_events(spec: Mapping[str, Any]) -> tuple[BSKEventSpec, ...]:
    parsed: list[BSKEventSpec] = []
    seen: set[tuple[str, str, str]] = set()
    for category, item in _event_iter(spec):
        params = item.get("parameters") if isinstance(item.get("parameters"), Mapping) else {}
        start = item.get("start_s", item.get("onset_time_s", 0.0))
        end = item.get("end_s")
        if end is None and item.get("duration_s") is not None:
            duration = float(item.get("duration_s") or 0.0)
            end = None if duration < 0.0 else float(start or 0.0) + duration
        effect = item.get("effect") or item.get("type") or item.get(f"{category}_type") or "unspecified"
        params = dict(params)
        if effect in {"eps_battery_capacity_loss", "solar_panel_efficiency_loss", "thermal_radiator_rejection_loss"}:
            magnitude = item.get("magnitude")
            if magnitude is None:
                magnitude = item.get("severity")
            if magnitude is not None:
                if (isinstance(magnitude, bool) or not isinstance(magnitude, (int, float))
                        or not math.isfinite(magnitude) or not 0.0 <= magnitude <= 1.0):
                    raise ValueError("LOSS_MAGNITUDE_MUST_BE_FINITE_FRACTION_IN_0_1")
                # Typed effect parameters retain their established precedence.
                # Otherwise scalar loss is translated to the physical fraction
                # consumed by both native parameter-event controllers.
                if not any(key in params for key in (
                        "remaining_capacity_ratio", "remaining_efficiency_ratio", "remaining_rejection_ratio",
                        "remaining_ratio", "loss_fraction", "loss_pct")):
                    params["remaining_ratio"] = 1.0 - float(magnitude)
        event_id = str(
            item.get("id")
            or item.get("event_id")
            or item.get("modifier_id")
            or item.get("fault_id")
            or item.get("degradation_id")
            or item.get("constraint_id")
            or f"{category}_{len(parsed)}"
        )
        dedup_key = (category, event_id, str(effect))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        parsed.append(BSKEventSpec(
            event_id=event_id,
            category=category,
            effect=str(effect),
            target=str(item.get("target") or item.get("target_id") or "spacecraft"),
            start_s=float(start or 0.0),
            end_s=float(end) if end is not None else None,
            parameters=dict(params),
        ))
    return tuple(parsed)


class BSKEventManager:
    def __init__(self, events: Sequence[BSKEventSpec] = ()) -> None:
        self.events = tuple(events)

    def active_labels(self, time_s: float) -> dict[str, bool]:
        return {
            "label.fault_active": any(e.category == "fault" and e.active_at(time_s) for e in self.events),
            "label.degradation_active": any(e.category == "degradation" and e.active_at(time_s) for e in self.events),
            "label.constraint_active": any(e.category == "constraint" and e.active_at(time_s) for e in self.events),
        }
