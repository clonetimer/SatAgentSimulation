"""Basilisk-first backend registry for subsystem-level modeling.

v3.22 clarifies the architecture:

* Basilisk is the preferred implementation for physical dynamics, sensors,
  actuators, power, thermal, communication access and propulsion effects.
* Non-Basilisk layers remain only where they are intentionally reference,
  orchestration, or policy modules rather than physical dynamics backends.

This registry does not build the subsystems itself.  It records the intended
backend ownership and gives tests/scripts a single place to inspect backend
coverage before v4 whole-spacecraft integration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Iterable, Literal

BackendName = Literal["python", "basilisk", "mixed"]
BasiliskStatus = Literal[
    "preferred",
    "dynamics",
    "bridge",
    "smoke",
    "reference_only",
    "strategy_only",
]


@dataclass(frozen=True)
class SubsystemBackendCapability:
    """Subsystem backend ownership and migration status."""

    name: str
    domain: str
    preferred_backend: BackendName
    python_role: str
    basilisk_role: str
    basilisk_status: BasiliskStatus
    builder_entrypoint: str
    python_entrypoint: str | None = None
    basilisk_entrypoint: str | None = None
    support_data_required: bool = False
    v4_blocker: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


BACKEND_CAPABILITIES: tuple[SubsystemBackendCapability, ...] = (
    SubsystemBackendCapability(
        name="eps",
        domain="Power",
        preferred_backend="basilisk",
        python_role="PDU/load strategy, low-SOC policy, reference power balance, fast SOC oracle",
        basilisk_role="simpleBattery/simplePowerSink/simpleSolarPanel bridge; physical power-node backend",
        basilisk_status="bridge",
        builder_entrypoint="subsystems.eps.builder:build_eps_context",
        python_entrypoint=None,
        basilisk_entrypoint="subsystems.eps.runner:run_nominal_case",
        support_data_required=False,
        notes="Full sun/eclipse/spacecraft-state solar-panel backend is deferred to v4 skeleton integration.",
    ),
    SubsystemBackendCapability(
        name="propulsion",
        domain="Propulsion",
        preferred_backend="basilisk",
        python_role="Burn permission policy, min-pulse shaping reference, impulse/propellant oracle",
        basilisk_role="thrusterDynamicEffector/fuelTank bridge and future spacecraft dynamics coupling",
        basilisk_status="bridge",
        builder_entrypoint="subsystems.propulsion.builder:build_propulsion_context",
        python_entrypoint="subsystems.propulsion.runner:run_reference_nominal_case",
        basilisk_entrypoint="subsystems.propulsion.runner:run_nominal_case",
        support_data_required=False,
        notes="True force/torque spacecraft response is deferred to integrated Basilisk skeleton.",
    ),
    SubsystemBackendCapability(
        name="comm_data",
        domain="Communication/Data",
        preferred_backend="basilisk",
        python_role="Mission data-generation policy, storage/downlink reference, link-budget oracle",
        basilisk_role="groundLocation / transmitter / data-node message bridge",
        basilisk_status="bridge",
        builder_entrypoint="subsystems.comm_data.builder:build_comm_data_context",
        python_entrypoint=None,
        basilisk_entrypoint="subsystems.comm_data.runner:run_nominal_case",
        support_data_required=False,
        notes="Full groundLocation->transmitter->storage graph is deferred until spacecraft-state integration.",
    ),
    SubsystemBackendCapability(
        name="thermal",
        domain="Thermal",
        preferred_backend="basilisk",
        python_role="Heater hysteresis, thermal-safe policy, reference node dynamics",
        basilisk_role="sensorThermal/tempMeasurement bridge and future environmental thermal backend",
        basilisk_status="bridge",
        builder_entrypoint="subsystems.thermal.builder:build_thermal_subsystem",
        python_entrypoint=None,
        basilisk_entrypoint="subsystems.thermal.runner:run_nominal_case",
        support_data_required=False,
        notes="Power/environment coupling is deferred to v4 spacecraft orchestration.",
    ),
    SubsystemBackendCapability(
        name="adcs",
        domain="ADCS",
        preferred_backend="mixed",
        python_role="Unified ADCS: sensors, control, actuators, command chain, mode management",
        basilisk_role="RW/MTB/VSCMG/IMU/star tracker module and message bridge",
        basilisk_status="smoke",
        builder_entrypoint="subsystems.adcs.builder:build_adcs_command_chain_config",
        python_entrypoint="subsystems.adcs.runner:run_backend",
        basilisk_entrypoint="subsystems.adcs.builder:run_adcs_actuator_basilisk_smoke",
        support_data_required=False,
        notes="Unified ADCS subsystem combining sensors, control, actuators, and command chain.",
    ),
    SubsystemBackendCapability(
        name="adcs_closed_loop",
        domain="ADCS/RW Closed Loop",
        preferred_backend="basilisk",
        python_role="No replacement physical backend; Python only saves/plots summaries",
        basilisk_role="RW-only inertialPoint spacecraft dynamics closed-loop",
        basilisk_status="dynamics",
        builder_entrypoint="subsystems.adcs_closed_loop.builder:run_backend",
        python_entrypoint="subsystems.adcs_closed_loop.runner:run_nominal_case",
        basilisk_entrypoint="subsystems.adcs_closed_loop.builder:run_rw_only_inertial_pointing",
        support_data_required=False,
    ),
    SubsystemBackendCapability(
        name="adcs_mtb_detumble_closed_loop",
        domain="ADCS/MTB Detumble Dynamics",
        preferred_backend="basilisk",
        python_role="Reference B-dot loop and CSV/plot oracle",
        basilisk_role="spacecraft + MtbEffector dynamics backend with synthetic B-field",
        basilisk_status="dynamics",
        builder_entrypoint="subsystems.adcs_mtb_detumble_closed_loop.builder:run_backend",
        python_entrypoint="subsystems.adcs_mtb_detumble_closed_loop.runner:run_nominal_case",
        basilisk_entrypoint="subsystems.adcs_mtb_detumble_closed_loop.builder:run_basilisk_mtb_detumble_dynamics",
        support_data_required=False,
        notes="WMM/orbit-driven field is deferred; synthetic B-field keeps weak-supportData behavior.",
    ),

    SubsystemBackendCapability(
        name="payload",
        domain="Payload",
        preferred_backend="mixed",
        python_role="Reduced-order payload instrument, power/heat/data/pointing contract oracle",
        basilisk_role="Message bridge boundary for payload power, heat, data generation and pointing gate",
        basilisk_status="bridge",
        builder_entrypoint="subsystems.payload.builder:build_payload_context",
        python_entrypoint=None,
        basilisk_entrypoint="subsystems.payload.runner:run_nominal_case",
        support_data_required=False,
        notes="component completeness adds payload as an independent subsystem; whole-spacecraft binding is deferred to spacecraft interface.",
    ),
)


def iter_capabilities() -> Iterable[SubsystemBackendCapability]:
    return iter(BACKEND_CAPABILITIES)


def get_capability(name: str) -> SubsystemBackendCapability:
    for capability in BACKEND_CAPABILITIES:
        if capability.name == name:
            return capability
    raise KeyError(f"unknown subsystem capability: {name}")


def _load_entrypoint(entrypoint: str):
    module_name, attr_name = entrypoint.split(":", 1)
    module = import_module(module_name)
    return getattr(module, attr_name)


def validate_entrypoints() -> list[str]:
    """Return import errors for registered entry points."""

    errors: list[str] = []
    for capability in BACKEND_CAPABILITIES:
        for label, entrypoint in (
            ("builder", capability.builder_entrypoint),
            ("python", capability.python_entrypoint),
            ("basilisk", capability.basilisk_entrypoint),
        ):
            if not entrypoint:
                continue
            try:
                _load_entrypoint(entrypoint)
            except Exception as exc:  # pragma: no cover - tested via caller
                errors.append(f"{capability.name}.{label}: {entrypoint}: {exc}")
    return errors


def basilisk_available() -> bool:
    try:
        import Basilisk  # noqa: F401

        return True
    except Exception:
        return False


def to_markdown_table() -> str:
    rows = [
        "| Subsystem | Preferred backend | Basilisk status | Python role | Basilisk role |",
        "|---|---|---|---|---|",
    ]
    for c in BACKEND_CAPABILITIES:
        rows.append(
            f"| `{c.name}` | `{c.preferred_backend}` | `{c.basilisk_status}` | {c.python_role} | {c.basilisk_role} |"
        )
    return "\n".join(rows) + "\n"


def backend_matrix_summary() -> dict[str, object]:
    return {
        "basilisk_available": basilisk_available(),
        "subsystem_count": len(BACKEND_CAPABILITIES),
        "preferred_basilisk_count": sum(1 for c in BACKEND_CAPABILITIES if c.preferred_backend == "basilisk"),
        "preferred_python_count": sum(1 for c in BACKEND_CAPABILITIES if c.preferred_backend == "python"),
        "preferred_mixed_count": sum(1 for c in BACKEND_CAPABILITIES if c.preferred_backend == "mixed"),
        "capabilities": [c.to_dict() for c in BACKEND_CAPABILITIES],
    }
