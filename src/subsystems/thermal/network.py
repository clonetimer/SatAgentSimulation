"""THERM-1 reduced-order spacecraft thermal network.

This module implements a deterministic, auditable thermal network intended for
script generation and early engineering trade studies.  It is intentionally not
a Thermal Desktop/ESATAN replacement and does not claim flight correlation.  The
model uses a small set of lumped nodes, conductive links, optional radiative
links, environment heat fluxes, and heater/radiator boundary conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
import math

from subsystems.thermal.control import (
    THERMAL_CONTROL_SCHEMA_VERSION,
    ThermalControlError,
    ThermalControlPolicy,
    compute_heater_command,
    initial_control_state,
)

THERMAL_NETWORK_SCHEMA_VERSION = "therm1.reduced_order_thermal_network.v1"
STEFAN_BOLTZMANN = 5.670374419e-8


class ThermalNetworkError(ValueError):
    """Raised when a reduced-order thermal network configuration is invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ThermalNetworkError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise ThermalNetworkError(f"{name} must be positive")
    return out


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise ThermalNetworkError(f"{name} must be non-negative")
    return out


def _ratio(value: Any, name: str) -> float:
    out = _finite(value, name)
    if not 0.0 <= out <= 1.0:
        raise ThermalNetworkError(f"{name} must be in [0, 1]")
    return out


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_of_numbers(value: Any, *, name: str) -> list[float]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ThermalNetworkError(f"{name} must be a sequence of numbers")
    return [_finite(v, f"{name}[]") for v in value]


@dataclass(frozen=True)
class ThermalNodeSpec:
    """Lumped node definition."""

    node_id: str
    heat_capacity_j_k: float
    initial_temp_c: float = 20.0
    area_m2: float = 0.0
    absorptivity: float = 0.60
    emissivity: float = 0.82
    external: bool = False
    radiator_multiplier: float = 1.0

    @classmethod
    def from_mapping(cls, node_id: str, data: Mapping[str, Any]) -> "ThermalNodeSpec":
        return cls(
            node_id=str(node_id),
            heat_capacity_j_k=_positive(data.get("heat_capacity_j_k", data.get("capacity_j_k", 1200.0)), f"nodes.{node_id}.heat_capacity_j_k"),
            initial_temp_c=_finite(data.get("initial_temp_c", data.get("temp_c", 20.0)), f"nodes.{node_id}.initial_temp_c"),
            area_m2=_nonnegative(data.get("area_m2", 0.0), f"nodes.{node_id}.area_m2"),
            absorptivity=_ratio(data.get("absorptivity", 0.60), f"nodes.{node_id}.absorptivity"),
            emissivity=_ratio(data.get("emissivity", 0.82), f"nodes.{node_id}.emissivity"),
            external=bool(data.get("external", False)),
            radiator_multiplier=_nonnegative(data.get("radiator_multiplier", 1.0), f"nodes.{node_id}.radiator_multiplier"),
        )


@dataclass(frozen=True)
class ConductiveLink:
    """Linear conductive link between two nodes."""

    node_a: str
    node_b: str
    conductance_w_k: float

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ConductiveLink":
        return cls(
            node_a=str(data.get("node_a")),
            node_b=str(data.get("node_b")),
            conductance_w_k=_nonnegative(data.get("conductance_w_k", 0.0), "conductive_links[].conductance_w_k"),
        )


@dataclass(frozen=True)
class RadiativeLink:
    """Grey-body radiative exchange link between two thermal nodes."""

    node_a: str
    node_b: str
    exchange_area_m2: float
    emissivity: float = 0.75
    view_factor: float = 1.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RadiativeLink":
        return cls(
            node_a=str(data.get("node_a")),
            node_b=str(data.get("node_b")),
            exchange_area_m2=_nonnegative(data.get("exchange_area_m2", data.get("area_m2", 0.0)), "radiative_links[].exchange_area_m2"),
            emissivity=_ratio(data.get("emissivity", 0.75), "radiative_links[].emissivity"),
            view_factor=_ratio(data.get("view_factor", 1.0), "radiative_links[].view_factor"),
        )


@dataclass(frozen=True)
class ThermalEnvironmentConfig:
    """Orbit/attitude-derived thermal boundary inputs."""

    solar_flux_w_m2: float = 1361.0
    albedo_flux_w_m2: float = 120.0
    earth_ir_flux_w_m2: float = 237.0
    deep_space_temp_k: float = 3.0
    shadow_factor: float = 1.0
    eclipse_period_s: float = 5400.0
    eclipse_duration_s: float = 0.0
    eclipse_start_s: float = 0.0
    face_solar_exposure: Mapping[str, float] = field(default_factory=lambda: {"+X": 1.0, "+Y": 0.20, "-Y": 0.20})
    face_albedo_exposure: Mapping[str, float] = field(default_factory=lambda: {"-Z": 1.0, "+X": 0.10})
    face_earth_ir_exposure: Mapping[str, float] = field(default_factory=lambda: {"-Z": 1.0, "+X": 0.15, "-X": 0.15, "+Y": 0.15, "-Y": 0.15})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ThermalEnvironmentConfig":
        return cls(
            solar_flux_w_m2=_nonnegative(data.get("solar_flux_w_m2", 1361.0), "environment.solar_flux_w_m2"),
            albedo_flux_w_m2=_nonnegative(data.get("albedo_flux_w_m2", 120.0), "environment.albedo_flux_w_m2"),
            earth_ir_flux_w_m2=_nonnegative(data.get("earth_ir_flux_w_m2", 237.0), "environment.earth_ir_flux_w_m2"),
            deep_space_temp_k=_nonnegative(data.get("deep_space_temp_k", 3.0), "environment.deep_space_temp_k"),
            shadow_factor=_ratio(data.get("shadow_factor", 1.0), "environment.shadow_factor"),
            eclipse_period_s=_positive(data.get("eclipse_period_s", 5400.0), "environment.eclipse_period_s"),
            eclipse_duration_s=_nonnegative(data.get("eclipse_duration_s", 0.0), "environment.eclipse_duration_s"),
            eclipse_start_s=_finite(data.get("eclipse_start_s", 0.0), "environment.eclipse_start_s"),
            face_solar_exposure=_exposure_map(data.get("face_solar_exposure"), default={"+X": 1.0, "+Y": 0.20, "-Y": 0.20}),
            face_albedo_exposure=_exposure_map(data.get("face_albedo_exposure"), default={"-Z": 1.0, "+X": 0.10}),
            face_earth_ir_exposure=_exposure_map(data.get("face_earth_ir_exposure"), default={"-Z": 1.0, "+X": 0.15, "-X": 0.15, "+Y": 0.15, "-Y": 0.15}),
        )


def _exposure_map(value: Any, *, default: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return dict(default)
    return {str(k): _ratio(v, f"exposure.{k}") for k, v in value.items()}


@dataclass(frozen=True)
class ThermalNetworkConfig:
    """Complete reduced-order network configuration."""

    duration_s: float = 5400.0
    sample_s: float = 60.0
    task_id: str = "thermal_reduced_order_task"
    case_id: str = "case_000"
    nodes: tuple[ThermalNodeSpec, ...] = ()
    conductive_links: tuple[ConductiveLink, ...] = ()
    radiative_links: tuple[RadiativeLink, ...] = ()
    environment: ThermalEnvironmentConfig = field(default_factory=ThermalEnvironmentConfig)
    internal_power_by_node_w: Mapping[str, float] = field(default_factory=lambda: {"internal": 12.0})
    heater_node: str = "internal"
    heater_power_w: float = 8.0
    heater_setpoint_c: float = 2.0
    heater_deadband_c: float = 2.0
    control_policy: ThermalControlPolicy | None = None
    min_temp_c_by_node: Mapping[str, float] = field(default_factory=dict)
    max_temp_c_by_node: Mapping[str, float] = field(default_factory=dict)
    model_basis: str = "six_face_plus_internal_reduced_order"

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "ThermalNetworkConfig":
        sim = _mapping(spec.get("simulation"))
        params = _mapping(spec.get("parameters"))
        metadata = _mapping(spec.get("metadata"))
        network = _mapping(params.get("network"))
        env = ThermalEnvironmentConfig.from_mapping(_mapping(params.get("environment") or spec.get("environment")))
        node_payload = network.get("nodes") or params.get("nodes")
        link_payload = network.get("conductive_links") or params.get("conductive_links")
        rad_payload = network.get("radiative_links") or params.get("radiative_links")
        if node_payload is None:
            nodes, links, radiative = default_cubesat_thermal_network(
                initial_temp_c=_finite(params.get("initial_temp_c", 20.0), "parameters.initial_temp_c"),
                face_capacity_j_k=_positive(params.get("face_heat_capacity_j_k", 900.0), "parameters.face_heat_capacity_j_k"),
                internal_capacity_j_k=_positive(params.get("internal_heat_capacity_j_k", 5000.0), "parameters.internal_heat_capacity_j_k"),
                face_area_m2=_positive(params.get("face_area_m2", 0.01), "parameters.face_area_m2"),
                conductance_internal_face_w_k=_nonnegative(params.get("conductance_internal_face_w_k", 0.45), "parameters.conductance_internal_face_w_k"),
                radiator_face=str(params.get("radiator_face", "-X")),
                radiator_multiplier=_nonnegative(params.get("radiator_multiplier", 1.8), "parameters.radiator_multiplier"),
            )
        else:
            if not isinstance(node_payload, Mapping):
                raise ThermalNetworkError("parameters.network.nodes must be a mapping")
            nodes = tuple(ThermalNodeSpec.from_mapping(str(k), _mapping(v)) for k, v in node_payload.items())
            links = tuple(ConductiveLink.from_mapping(_mapping(v)) for v in (link_payload or []))
            radiative = tuple(RadiativeLink.from_mapping(_mapping(v)) for v in (rad_payload or []))
        internal_power = params.get("internal_power_by_node_w", {"internal": params.get("internal_power_w", 12.0)})
        if not isinstance(internal_power, Mapping):
            raise ThermalNetworkError("parameters.internal_power_by_node_w must be a mapping")
        internal_power_map = {str(k): _nonnegative(v, f"internal_power_by_node_w.{k}") for k, v in internal_power.items()}
        legacy_policy = ThermalControlPolicy.from_legacy(
            node_id=str(params.get("heater_node", "internal")),
            power_w=_nonnegative(params.get("heater_power_w", 8.0), "parameters.heater_power_w"),
            setpoint_c=_finite(params.get("heater_setpoint_c", 2.0), "parameters.heater_setpoint_c"),
            deadband_c=_nonnegative(params.get("heater_deadband_c", 2.0), "parameters.heater_deadband_c"),
        )
        heater_control = params.get("heater_control")
        if heater_control is None:
            control_policy = legacy_policy
        elif not isinstance(heater_control, Mapping):
            raise ThermalNetworkError("parameters.heater_control must be a mapping")
        else:
            try:
                control_policy = ThermalControlPolicy.from_mapping(heater_control, legacy=legacy_policy)
            except ThermalControlError as exc:
                raise ThermalNetworkError(str(exc)) from exc
        return cls(
            duration_s=_positive(sim.get("duration_s", 5400.0), "simulation.duration_s"),
            sample_s=_positive(sim.get("sample_s", 60.0), "simulation.sample_s"),
            task_id=str(spec.get("task_id", "thermal_reduced_order_task")),
            case_id=str(metadata.get("case_id", "case_000")),
            nodes=tuple(nodes),
            conductive_links=tuple(links),
            radiative_links=tuple(radiative),
            environment=env,
            internal_power_by_node_w=internal_power_map,
            heater_node=control_policy.node_id,
            heater_power_w=control_policy.max_power_w,
            heater_setpoint_c=control_policy.setpoint_c,
            heater_deadband_c=control_policy.deadband_c,
            control_policy=control_policy,
            min_temp_c_by_node=_limit_map(params.get("min_temp_c_by_node")),
            max_temp_c_by_node=_limit_map(params.get("max_temp_c_by_node")),
            model_basis=str(params.get("model_basis", "six_face_plus_internal_reduced_order")),
        )

    def validate(self) -> None:
        if not self.nodes:
            raise ThermalNetworkError("at least one thermal node is required")
        ids = {n.node_id for n in self.nodes}
        for link in self.conductive_links:
            if link.node_a not in ids or link.node_b not in ids:
                raise ThermalNetworkError(f"conductive link references unknown node: {link}")
        for link in self.radiative_links:
            if link.node_a not in ids or link.node_b not in ids:
                raise ThermalNetworkError(f"radiative link references unknown node: {link}")
        for node_id in self.internal_power_by_node_w:
            if node_id not in ids:
                raise ThermalNetworkError(f"internal power references unknown node: {node_id}")
        if self.heater_node not in ids:
            raise ThermalNetworkError(f"heater node does not exist: {self.heater_node}")
        if self.control.node_id not in ids:
            raise ThermalNetworkError(f"heater_control node does not exist: {self.control.node_id}")


    @property
    def control(self) -> ThermalControlPolicy:
        """Return the active thermal control policy, preserving legacy defaults."""

        if self.control_policy is not None:
            return self.control_policy
        return ThermalControlPolicy.from_legacy(
            node_id=self.heater_node,
            power_w=self.heater_power_w,
            setpoint_c=self.heater_setpoint_c,
            deadband_c=self.heater_deadband_c,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": THERMAL_NETWORK_SCHEMA_VERSION,
            "duration_s": self.duration_s,
            "sample_s": self.sample_s,
            "task_id": self.task_id,
            "case_id": self.case_id,
            "node_count": len(self.nodes),
            "nodes": [node.__dict__ for node in self.nodes],
            "conductive_links": [link.__dict__ for link in self.conductive_links],
            "radiative_links": [link.__dict__ for link in self.radiative_links],
            "environment": {
                "solar_flux_w_m2": self.environment.solar_flux_w_m2,
                "albedo_flux_w_m2": self.environment.albedo_flux_w_m2,
                "earth_ir_flux_w_m2": self.environment.earth_ir_flux_w_m2,
                "deep_space_temp_k": self.environment.deep_space_temp_k,
                "shadow_factor": self.environment.shadow_factor,
                "eclipse_period_s": self.environment.eclipse_period_s,
                "eclipse_duration_s": self.environment.eclipse_duration_s,
                "eclipse_start_s": self.environment.eclipse_start_s,
                "face_solar_exposure": dict(self.environment.face_solar_exposure),
                "face_albedo_exposure": dict(self.environment.face_albedo_exposure),
                "face_earth_ir_exposure": dict(self.environment.face_earth_ir_exposure),
            },
            "internal_power_by_node_w": dict(self.internal_power_by_node_w),
            "heater_node": self.heater_node,
            "heater_power_w": self.heater_power_w,
            "heater_setpoint_c": self.heater_setpoint_c,
            "heater_deadband_c": self.heater_deadband_c,
            "heater_control": self.control.to_dict(),
            "thermal_control_schema_version": THERMAL_CONTROL_SCHEMA_VERSION,
            "model_basis": self.model_basis,
        }


def _limit_map(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): _finite(v, f"limit.{k}") for k, v in value.items()}


def default_cubesat_thermal_network(
    *,
    initial_temp_c: float = 20.0,
    face_capacity_j_k: float = 900.0,
    internal_capacity_j_k: float = 5000.0,
    face_area_m2: float = 0.01,
    conductance_internal_face_w_k: float = 0.45,
    radiator_face: str = "-X",
    radiator_multiplier: float = 1.8,
) -> tuple[tuple[ThermalNodeSpec, ...], tuple[ConductiveLink, ...], tuple[RadiativeLink, ...]]:
    """Build a 7-node CubeSat-style thermal network: six faces + internal node."""

    face_ids = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
    nodes = [ThermalNodeSpec("internal", internal_capacity_j_k, initial_temp_c=initial_temp_c, area_m2=0.0, external=False)]
    for fid in face_ids:
        nodes.append(ThermalNodeSpec(
            fid,
            face_capacity_j_k,
            initial_temp_c=initial_temp_c,
            area_m2=face_area_m2,
            absorptivity=0.62,
            emissivity=0.82,
            external=True,
            radiator_multiplier=radiator_multiplier if fid == radiator_face else 1.0,
        ))
    links = tuple(ConductiveLink("internal", fid, conductance_internal_face_w_k) for fid in face_ids)
    radiative = tuple(RadiativeLink("internal", fid, face_area_m2 * 0.35, emissivity=0.70, view_factor=0.20) for fid in face_ids)
    return tuple(nodes), links, radiative


def _eclipse_shadow(env: ThermalEnvironmentConfig, time_s: float) -> float:
    shadow = env.shadow_factor
    if env.eclipse_duration_s > 0.0:
        phase = (time_s - env.eclipse_start_s) % env.eclipse_period_s
        if 0.0 <= phase < env.eclipse_duration_s:
            shadow = 0.0
    return max(0.0, min(1.0, shadow))


def _radiation_to_sink_w(node: ThermalNodeSpec, temp_c: float, env: ThermalEnvironmentConfig) -> float:
    if not node.external or node.area_m2 <= 0.0 or node.emissivity <= 0.0:
        return 0.0
    temp_k = temp_c + 273.15
    sink_k = max(0.0, env.deep_space_temp_k)
    area = node.area_m2 * max(0.0, node.radiator_multiplier)
    return max(0.0, node.emissivity * STEFAN_BOLTZMANN * area * (temp_k**4 - sink_k**4))


def _radiative_exchange_w(link: RadiativeLink, temps_c: Mapping[str, float]) -> float:
    ta = temps_c[link.node_a] + 273.15
    tb = temps_c[link.node_b] + 273.15
    return link.emissivity * link.view_factor * STEFAN_BOLTZMANN * link.exchange_area_m2 * (ta**4 - tb**4)


def _external_heat_w(node: ThermalNodeSpec, env: ThermalEnvironmentConfig, shadow: float) -> tuple[float, float, float, float]:
    if not node.external or node.area_m2 <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)
    face = node.node_id
    solar = node.absorptivity * node.area_m2 * env.solar_flux_w_m2 * shadow * float(env.face_solar_exposure.get(face, 0.0))
    albedo = node.absorptivity * node.area_m2 * env.albedo_flux_w_m2 * shadow * float(env.face_albedo_exposure.get(face, 0.0))
    earth_ir = node.emissivity * node.area_m2 * env.earth_ir_flux_w_m2 * float(env.face_earth_ir_exposure.get(face, 0.0))
    return (solar + albedo + earth_ir, solar, albedo, earth_ir)


def propagate_thermal_network(config: ThermalNetworkConfig) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Propagate the thermal network with explicit Euler time integration."""

    config.validate()
    node_by_id = {node.node_id: node for node in config.nodes}
    temps: dict[str, float] = {node.node_id: node.initial_temp_c for node in config.nodes}
    rows: list[dict[str, Any]] = []
    n_samples = max(1, int(math.floor(config.duration_s / config.sample_s)) + 1)
    heater_energy_wh = 0.0
    radiator_energy_wh = 0.0
    max_energy_balance_residual_w = 0.0
    hot_count = 0
    cold_count = 0
    min_temp_seen = min(temps.values())
    max_temp_seen = max(temps.values())
    internal_min = temps.get("internal", next(iter(temps.values())))
    internal_max = internal_min
    control = config.control
    control_state = initial_control_state(control)
    heater_on_sample_count = 0
    heater_saturation_count = 0
    heater_transition_count = 0
    previous_heater_on: bool | None = None

    for sample_index in range(n_samples):
        time_s = min(sample_index * config.sample_s, config.duration_s)
        shadow = _eclipse_shadow(config.environment, time_s)
        net = {node_id: 0.0 for node_id in temps}
        solar_total = 0.0
        albedo_total = 0.0
        earth_ir_total = 0.0
        external_total = 0.0
        radiator_total = 0.0
        conductive_abs_total = 0.0
        radiative_abs_total = 0.0

        for node_id, power in config.internal_power_by_node_w.items():
            net[node_id] += power
        command = compute_heater_command(
            control,
            control_state,
            temperature_c=temps[control.node_id],
            dt_s=config.sample_s,
        )
        control_state = command.state
        heater_on = command.heater_on
        heater_power = command.power_w
        if heater_on:
            heater_on_sample_count += 1
        if command.saturation:
            heater_saturation_count += 1
        if previous_heater_on is not None and previous_heater_on != heater_on:
            heater_transition_count += 1
        previous_heater_on = heater_on
        net[control.node_id] += heater_power

        for link in config.conductive_links:
            q_a_to_b = link.conductance_w_k * (temps[link.node_a] - temps[link.node_b])
            net[link.node_a] -= q_a_to_b
            net[link.node_b] += q_a_to_b
            conductive_abs_total += abs(q_a_to_b)
        for link in config.radiative_links:
            q_a_to_b = _radiative_exchange_w(link, temps)
            net[link.node_a] -= q_a_to_b
            net[link.node_b] += q_a_to_b
            radiative_abs_total += abs(q_a_to_b)
        for node in config.nodes:
            q_ext, q_solar, q_albedo, q_earth_ir = _external_heat_w(node, config.environment, shadow)
            q_rad = _radiation_to_sink_w(node, temps[node.node_id], config.environment)
            net[node.node_id] += q_ext - q_rad
            external_total += q_ext
            solar_total += q_solar
            albedo_total += q_albedo
            earth_ir_total += q_earth_ir
            radiator_total += q_rad

        hot_flag = any(temps[nid] > config.max_temp_c_by_node.get(nid, 1.0e9) for nid in temps)
        cold_flag = any(temps[nid] < config.min_temp_c_by_node.get(nid, -1.0e9) for nid in temps)
        if hot_flag:
            hot_count += 1
        if cold_flag:
            cold_count += 1
        min_temp_seen = min(min_temp_seen, min(temps.values()))
        max_temp_seen = max(max_temp_seen, max(temps.values()))
        internal_min = min(internal_min, temps.get("internal", internal_min))
        internal_max = max(internal_max, temps.get("internal", internal_max))

        row: dict[str, Any] = {
            "task_id": config.task_id,
            "case_id": config.case_id,
            "time_s": round(time_s, 12),
            "sample_index": sample_index,
            "capability_id": "subsystem.thermal_reduced_order.v1",
            "model_basis": config.model_basis,
            "environment.shadow_factor": shadow,
            "environment.eclipse_flag": shadow <= 1.0e-12,
            "thermal.heat.solar_w": solar_total,
            "thermal.heat.albedo_w": albedo_total,
            "thermal.heat.earth_ir_w": earth_ir_total,
            "thermal.heat.external_total_w": external_total,
            "thermal.heater.power_w": heater_power,
            "thermal.heater.on": heater_on,
            "thermal.radiator.total_reject_w": radiator_total,
            "thermal.coupling.conductive_abs_w": conductive_abs_total,
            "thermal.coupling.radiative_abs_w": radiative_abs_total,
            "thermal.energy_balance.net_heat_w": sum(net.values()),
            "label.thermal_hot_flag": hot_flag,
            "label.thermal_cold_flag": cold_flag,
            "label.thermal_state": "hot" if hot_flag else "cold" if cold_flag else "nominal",
        }
        row.update(command.trace_fields())
        for node_id, temp in temps.items():
            row[f"thermal.node.{node_id}.temp_c"] = temp
            row[f"thermal.node.{node_id}.net_heat_w"] = net[node_id]
        rows.append(row)

        if sample_index == n_samples - 1:
            break
        dt = min(config.sample_s, max(0.0, config.duration_s - time_s))
        if dt <= 0.0:
            break
        heater_energy_wh += heater_power * dt / 3600.0
        radiator_energy_wh += radiator_total * dt / 3600.0
        old_temps = dict(temps)
        for node_id, node in node_by_id.items():
            temps[node_id] = temps[node_id] + net[node_id] * dt / node.heat_capacity_j_k
        for node_id, node in node_by_id.items():
            residual = node.heat_capacity_j_k * (temps[node_id] - old_temps[node_id]) / dt - net[node_id]
            max_energy_balance_residual_w = max(max_energy_balance_residual_w, abs(residual))

    final = rows[-1]
    heater_available_wh = control.max_power_w * config.duration_s / 3600.0 if control.max_power_w > 0.0 else 0.0
    heater_duty_cycle = 0.0 if heater_available_wh <= 0.0 else max(0.0, min(1.0, heater_energy_wh / heater_available_wh))
    summary = {
        "schema_version": THERMAL_NETWORK_SCHEMA_VERSION,
        "status": "pass",
        "capability_id": "subsystem.thermal_reduced_order.v1",
        "backend_type": "local_physics_proxy",
        "validation_basis": ["public_reference_informed", "internal_benchmark"],
        "can_claim_high_fidelity": False,
        "task_id": config.task_id,
        "case_id": config.case_id,
        "node_count": len(config.nodes),
        "conductive_link_count": len(config.conductive_links),
        "radiative_link_count": len(config.radiative_links),
        "trace_rows": len(rows),
        "qoi.thermal.initial_internal_temp_c": rows[0].get("thermal.node.internal.temp_c"),
        "qoi.thermal.final_internal_temp_c": final.get("thermal.node.internal.temp_c"),
        "qoi.thermal.min_internal_temp_c": internal_min,
        "qoi.thermal.max_internal_temp_c": internal_max,
        "qoi.thermal.min_node_temp_c": min_temp_seen,
        "qoi.thermal.max_node_temp_c": max_temp_seen,
        "qoi.thermal.heater_energy_wh": heater_energy_wh,
        "qoi.thermal.heater_duty_cycle": heater_duty_cycle,
        "qoi.thermal.heater_on_sample_count": heater_on_sample_count,
        "qoi.thermal.heater_saturation_count": heater_saturation_count,
        "qoi.thermal.heater_transition_count": heater_transition_count,
        "qoi.thermal.control_mode": control.mode,
        "qoi.thermal.control_node": control.node_id,
        "qoi.thermal.radiator_energy_wh": radiator_energy_wh,
        "qoi.thermal.max_energy_balance_residual_w": max_energy_balance_residual_w,
        "qoi.thermal.hot_count": hot_count,
        "qoi.thermal.cold_count": cold_count,
        "label.thermal_state": str(final["label.thermal_state"]),
        "model_limitations": [
            "reduced-order lumped thermal network, not finite-element thermal analysis",
            "public-reference-informed defaults, not flight-correlated parameters",
            "orbit/attitude inputs are scalar heat-flux traces unless explicitly supplied",
            "thermal control policies are deterministic early-design models, not flight software",
        ],
    }
    return summary, tuple(rows)


def build_thermal_network_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    config = ThermalNetworkConfig.from_task_spec(spec)
    return {"schema_version": THERMAL_NETWORK_SCHEMA_VERSION, "config": config.to_dict()}


__all__ = [
    "THERMAL_NETWORK_SCHEMA_VERSION",
    "ThermalNetworkError",
    "ThermalNodeSpec",
    "ConductiveLink",
    "RadiativeLink",
    "ThermalEnvironmentConfig",
    "ThermalNetworkConfig",
    "default_cubesat_thermal_network",
    "propagate_thermal_network",
    "build_thermal_network_payload",
]
