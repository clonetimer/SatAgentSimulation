"""Basilisk helpers for the fuel tank component."""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .faults import apply_fuel_tank_faults
from .faults import FaultSpec

from dataclasses import dataclass
from typing import Any, Sequence

from .model import (
    apply_fuel_tank_config_faults,
    build_nominal_fuel_tank_config,
    initialize_fuel_tank,
    pressure_from_mass,
    simulate_mass_flow_profile,
    step_fuel_tank,
)
from .schemas import FuelTankConfig, FuelTankProfileResult, FuelTankState


_messaging = None
_sysModel = None

try:
    from Basilisk.architecture import messaging, sysModel

    _messaging = messaging
    _sysModel = sysModel
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/fuel_tank/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )


@dataclass(frozen=True)
class FuelTankBuild:
    tank: object
    tank_model: object
    leak_rate_msg: Any | None = None


def basilisk_available() -> bool:
    return _messaging is not None and _sysModel is not None


def require_basilisk_fuel_tank() -> None:
    try:
        from Basilisk.simulation import fuelTank  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk fuelTank module is unavailable: {exc}") from exc


def _vec3(value: Sequence[float] | None, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> list[float]:
    vals = list(value or default)[:3]
    if len(vals) < 3:
        vals.extend(list(default[len(vals):]))
    return [float(vals[0]), float(vals[1]), float(vals[2])]


def _dcm33(value: Sequence[Sequence[float]] | None) -> list[list[float]]:
    if value is None:
        value = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    rows = [list(row)[:3] for row in list(value)[:3]]
    while len(rows) < 3:
        rows.append([0.0, 0.0, 0.0])
    for row_idx, row in enumerate(rows):
        while len(row) < 3:
            row.append(0.0)
        if not any(abs(float(x)) > 0.0 for x in row):
            row[row_idx] = 1.0
    return [[float(x) for x in row[:3]] for row in rows[:3]]


def _make_tank_model(model_type: str, *, initial_mass_kg: float, capacity_kg: float, radius_m: float, rho_fuel_kg_m3: float, length_tank_m: float, radius_inner_m: float):
    from Basilisk.simulation import fuelTank

    normalized = str(model_type or "constant_volume").strip().lower().replace("-", "_")
    if normalized in {"constant_volume", "volume"}:
        model = fuelTank.FuelTankModelConstantVolume()
        model.radiusTankInit = float(radius_m)
    elif normalized in {"constant_density", "density"}:
        model = fuelTank.FuelTankModelConstantDensity()
        model.radiusTankInit = float(radius_m)
        model.radiusTank = float(radius_m)
    elif normalized in {"centrifugal_burn", "centrifugal"}:
        model = fuelTank.FuelTankModelCentrifugalBurn()
        model.radiusTankInit = float(radius_m)
        model.lengthTank = float(length_tank_m)
        model.radiusInner = float(radius_inner_m)
    elif normalized in {"emptying", "emptying_tank"}:
        model = fuelTank.FuelTankModelEmptying()
        model.radiusTankInit = float(radius_m)
        model.rhoFuel = float(rho_fuel_kg_m3)
    else:
        raise ValueError(f"Unsupported Basilisk fuel-tank model type: {model_type!r}")
    model.propMassInit = float(initial_mass_kg)
    model.maxFuelMass = float(capacity_kg)
    return model, normalized


def write_fuel_leak_rate_message(leak_rate_kg_s: float):
    require_basilisk_fuel_tank()
    from Basilisk.architecture import messaging

    payload = messaging.MassFlowRateMsgPayload()
    payload.massFlowRate = max(0.0, float(leak_rate_kg_s))
    return messaging.MassFlowRateMsg().write(payload)


def build_fuel_tank_bundle_from_config(model_tag: str, config: FuelTankConfig) -> FuelTankBuild:
    """Build a Basilisk FuelTank using the component schema as the native contract."""

    require_basilisk_fuel_tank()
    from Basilisk.simulation import fuelTank

    tank = fuelTank.FuelTank()
    tank.ModelTag = model_tag
    tank_model, _ = _make_tank_model(
        config.tank_model,
        initial_mass_kg=float(config.initial_mass_kg),
        capacity_kg=float(config.capacity_kg),
        radius_m=float(config.radius_tank_m),
        rho_fuel_kg_m3=float(config.rho_fuel_kg_m3),
        length_tank_m=float(config.length_tank_m),
        radius_inner_m=float(config.radius_inner_m),
    )
    tank.setTankModel(tank_model)
    tank.setR_TB_B(_vec3(config.r_tb_b_m))
    tank.setDcm_TB(_dcm33(config.dcm_tb))
    tank.setFuelLeakRate(max(0.0, float(config.leak_rate_kg_s)))
    leak_msg = write_fuel_leak_rate_message(config.leak_rate_kg_s)
    tank.fuelLeakRateInMsg.subscribeTo(leak_msg)
    return FuelTankBuild(tank=tank, tank_model=tank_model, leak_rate_msg=leak_msg)


def build_constant_volume_fuel_tank_bundle(
    model_tag: str,
    initial_mass_kg: float,
    capacity_kg: float,
    radius_m: float = 0.1,
    *,
    leak_rate_kg_s: float = 0.0,
    r_tb_b_m: Sequence[float] = (0.0, 0.0, 0.0),
    dcm_tb: Sequence[Sequence[float]] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
) -> FuelTankBuild:
    cfg = FuelTankConfig(
        capacity_kg=float(capacity_kg),
        initial_mass_kg=float(initial_mass_kg),
        tank_model="constant_volume",
        radius_tank_m=float(radius_m),
        leak_rate_kg_s=float(leak_rate_kg_s),
        r_tb_b_m=tuple(float(x) for x in _vec3(r_tb_b_m)),
        dcm_tb=tuple(tuple(float(x) for x in row) for row in _dcm33(dcm_tb)),
    )
    return build_fuel_tank_bundle_from_config(model_tag, cfg)


def build_constant_volume_fuel_tank(
    model_tag: str,
    initial_mass_kg: float,
    capacity_kg: float,
    radius_m: float = 0.1,
    **kwargs,
):
    bundle = build_constant_volume_fuel_tank_bundle(model_tag, initial_mass_kg, capacity_kg, radius_m, **kwargs)
    _fuel_tank_bundles.append(bundle)
    return bundle.tank


_fuel_tank_bundles: list = []


def attach_fuel_tank_to_spacecraft(
    tank: object,
    spacecraft: object,
    thruster_effector: object | None = None,
) -> None:
    if thruster_effector is not None:
        tank.addThrusterSet(thruster_effector)
    spacecraft.addStateEffector(tank)
