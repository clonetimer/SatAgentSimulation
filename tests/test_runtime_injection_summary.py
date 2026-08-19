from __future__ import annotations

import json
from types import SimpleNamespace

from subsystems.runtime_injection import (
    RuntimeInjectionRecord,
    runtime_injection_evidence,
    runtime_injection_summary,
)
from sat_sim.validation_outcome import _modifier_payloads


def test_runtime_injection_summary_is_json_serializable() -> None:
    record = RuntimeInjectionRecord(
        time_s=1.0,
        event_name="feed_failure",
        scenario="feed_system_restriction",
        phase="start",
        component="propulsion",
        target_id="thruster_effector",
        injection="parameter_update",
        module_key="thruster_bundle",
        parameter="thrustFactor",
        applied_to_basilisk=True,
        before={"value": 1.0},
        after={"value": 0.5},
    )
    context = SimpleNamespace(base_parameters={"runtime_injection_records": [record]})

    summary = runtime_injection_summary(context)

    assert summary["runtime_injection_records"][0]["event_name"] == "feed_failure"
    assert summary["actual_basilisk_application_count"] == 1
    json.dumps(summary)


def test_validation_reads_verified_requested_runtime_events() -> None:
    summary = {
        "runtime_injection": {
            "actual_basilisk_application_count": 1,
            "requested_events": [{
                "modifier_id": "feed_failure",
                "effect_id": "thrust_or_feed_failure",
                "runtime_delivery_verified": True,
            }],
        }
    }

    assert _modifier_payloads(summary)[0]["modifier_id"] == "feed_failure"


def test_runtime_injection_evidence_links_taskspec_event_after_application() -> None:
    record = RuntimeInjectionRecord(
        time_s=2.0,
        event_name="native_feed_failure",
        scenario="feed_system_restriction",
        phase="start",
        component="propulsion",
        target_id="thruster",
        injection="parameter_update",
        module_key="thruster",
        parameter="thrust_factor",
        applied_to_basilisk=True,
    )
    context = SimpleNamespace(base_parameters={"runtime_injection_records": [record]})

    evidence = runtime_injection_evidence(
        context,
        fault_events=[{
            "id": "user_feed_failure",
            "effect": "thrust_or_feed_failure",
        }],
    )

    assert evidence["requested_events"] == [{
        "modifier_id": "user_feed_failure",
        "effect_id": "thrust_or_feed_failure",
        "kind": "fault",
        "runtime_delivery_verified": True,
    }]
