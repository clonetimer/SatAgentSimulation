"""Release gate for cross-subsystem physical-coupling completeness."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .release_closure import RELEASE_ID, RELEASE_VERSION

SCHEMA_VERSION = "sat-sim.coupling-completeness-gate.v1"

# Current PARTIAL QoI entries are important engineering gaps but are staged as
# P1 advisories.  Unknown/new PARTIAL entries are release-blocking by default so
# a newly introduced incomplete chain cannot silently inherit an allow-list.
ADVISORY_PARTIAL_IDS = {
    "eps_payload.pdu_status_to_payload_generation_feedback",
    "eps_comm.pdu_status_to_downlink_rate_feedback",
    "thermal_eps.eps_heater_status_to_thermal_heat_feedback",
    "prop_adcs.thruster_fault_to_attitude_response",
    "adcs_payload.adcs_pointing_to_payload_command_feedback",
    "adcs_eps.control_effort_to_power_load_feedback",
    "thermal_payload.generated_payload_power_to_thermal_heat_feedback",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_coupling_completeness_gate(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for report in reports:
        for row in report.get("checks", []) or []:
            if isinstance(row, Mapping):
                checks.append(dict(row))

    def check_identifier(row: Mapping[str, Any]) -> str:
        # QoI profiles historically used ``id`` while release checks use
        # ``check_id``.  Treat both as the same contract.
        return str(row.get("check_id") or row.get("id") or "")

    # Multiple profile generations may contain the same check ID.  Resolve the
    # current state conservatively: any FAIL wins; otherwise a direct PASS
    # supersedes a historical PARTIAL; otherwise retain PARTIAL.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in checks:
        grouped.setdefault(check_identifier(row), []).append(row)
    failures: list[dict[str, Any]] = []
    partials: list[dict[str, Any]] = []
    resolved_checks: list[dict[str, Any]] = []
    for check_id, rows in grouped.items():
        selected = next((row for row in rows if str(row.get("status")) == "FAIL"), None)
        if selected is None:
            selected = next((row for row in reversed(rows) if str(row.get("status")) == "PASS"), None)
        if selected is None:
            selected = next((row for row in rows if str(row.get("status")) == "PARTIAL"), rows[-1])
        resolved_checks.append(selected)
        status_value = str(selected.get("status"))
        if status_value == "FAIL":
            failures.append(selected)
        elif status_value == "PARTIAL":
            partials.append(selected)

    advisory = [row for row in partials if check_identifier(row) in ADVISORY_PARTIAL_IDS]
    blocking = [row for row in partials if check_identifier(row) not in ADVISORY_PARTIAL_IDS]
    release_blocking_count = len(failures) + len(blocking)
    status = "FAIL" if release_blocking_count else ("PASS_WITH_ADVISORIES" if advisory else "PASS")
    return {
        "schema_version": SCHEMA_VERSION,
        "release_id": RELEASE_ID,
        "release_version": RELEASE_VERSION,
        "generated_at_utc": _utc_now(),
        "status": status,
        "check_count": len(resolved_checks),
        "raw_check_count": len(checks),
        "failure_count": len(failures),
        "partial_count": len(partials),
        "release_blocking_count": release_blocking_count,
        "advisory_partial_count": len(advisory),
        "blocking_check_ids": [check_identifier(row) for row in failures + blocking],
        "advisory_check_ids": [check_identifier(row) for row in advisory],
        "policy": {
            "unknown_partial_is_blocking": True,
            "advisory_allowlist": sorted(ADVISORY_PARTIAL_IDS),
            "full_pass_requires_zero_partial": True,
        },
        "claim_boundary": (
            "PASS_WITH_ADVISORIES is not physical-coupling completeness; it only means no unknown or P0 blocking gap was found."
        ),
    }


__all__ = ["SCHEMA_VERSION", "ADVISORY_PARTIAL_IDS", "build_coupling_completeness_gate"]
