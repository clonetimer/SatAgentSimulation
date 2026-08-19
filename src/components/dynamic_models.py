
from __future__ import annotations

from dataclasses import dataclass, field, asdict, is_dataclass
from math import sqrt, sin, cos, acos, asin, erfc, log10, pi
from random import Random
from typing import Sequence, Tuple, List, Dict, Any, Union, Optional

from .exceptions import (
    ConfigRangeError, ConfigValueError, ConfigMissingError,
    validate_positive, validate_non_negative, validate_between_zero_one,
    validate_not_none, validate_range
)

G0: float = 9.80665

Vec3 = Tuple[float, float, float]


def v3(v: Sequence[float]) -> Vec3:
    o: List[float] = [float(x) for x in list(v)[:3]]
    while len(o) < 3:
        o.append(0.0)
    return (o[0], o[1], o[2])


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def cross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    ax, ay, az = v3(a)
    bx, by, bz = v3(b)
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def norm(v: Sequence[float]) -> float:
    return sqrt(max(0.0, dot(v, v)))


def unit(v: Sequence[float]) -> Vec3:
    x = v3(v)
    n = norm(x)
    if n <= 0:
        raise ValueError('zero vector')
    return (x[0] / n, x[1] / n, x[2] / n)


def normalize_safe(v: Sequence[float]) -> Vec3:
    """Normalize vector, returning (1,0,0) for zero/missing vectors."""
    x = v3(v)
    n = norm(x)
    if n <= 1e-12:
        return (1.0, 0.0, 0.0)
    return (x[0] / n, x[1] / n, x[2] / n)


def expand(vals: Union[int, float, Sequence[float]], n: int, default: float = 0.0) -> Tuple[float, ...]:
    if isinstance(vals, (int, float)):
        return tuple(float(vals) for _ in range(n))
    out: List[float] = [float(x) for x in list(vals)[:n]]
    while len(out) < n:
        out.append(float(default))
    return tuple(out)


def jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj

@dataclass(frozen=True)
class BatteryConfig:
    capacity_wh: float = 100.0
    initial_soc: float = 0.5
    charge_efficiency: float = 1.0
    discharge_efficiency: float = 1.0
    min_soc: float = 0.0
    max_soc: float = 1.0
    cv_soc_threshold: float = 0.7
    cv_voltage_factor: float = 0.95
    max_charge_current_a: float = 10.0
    nominal_voltage_v: float = 28.0
    min_temp_c: float = 0.0
    max_temp_c: float = 45.0
    optimal_temp_c: float = 25.0


@dataclass(frozen=True)
class BatteryState:
    storage_wh: float
    capacity_wh: float
    soc: float
    shunt_dissipated_wh: float = 0.0


@dataclass(frozen=True)
class BatteryProfileResult:
    time_s: Tuple[float, ...]
    soc: Tuple[float, ...]
    storage_wh: Tuple[float, ...]
    shunt_dissipated_wh: Tuple[float, ...]


def _temperature_factor(temp_c: float, cfg: BatteryConfig) -> float:
    if temp_c < cfg.min_temp_c:
        return max(0.0, 0.1 + (temp_c - cfg.min_temp_c) * 0.9 / (cfg.optimal_temp_c - cfg.min_temp_c))
    if temp_c > cfg.max_temp_c:
        return max(0.0, 1.0 - (temp_c - cfg.max_temp_c) / 10.0)
    return 1.0


def _cc_cv_charge_limit(soc: float, cfg: BatteryConfig) -> float:
    if soc < cfg.cv_soc_threshold:
        return cfg.max_charge_current_a * cfg.nominal_voltage_v
    if soc >= 0.95:
        return cfg.max_charge_current_a * cfg.nominal_voltage_v * 0.1
    ratio = (soc - cfg.cv_soc_threshold) / (0.95 - cfg.cv_soc_threshold)
    return cfg.max_charge_current_a * cfg.nominal_voltage_v * (1.0 - ratio * 0.9)


def initialize_battery(c: BatteryConfig) -> BatteryState:
    validate_positive(c.capacity_wh, "capacity_wh")
    validate_between_zero_one(c.initial_soc, "initial_soc")
    validate_between_zero_one(c.min_soc, "min_soc")
    validate_between_zero_one(c.max_soc, "max_soc")
    if c.min_soc > c.max_soc:
        raise ConfigRangeError("min_soc", c.min_soc, 0.0, c.max_soc)
    cap = max(0.0, c.capacity_wh)
    soc = max(c.min_soc, min(c.max_soc, c.initial_soc))
    return BatteryState(cap * soc, cap, soc, 0.0)


def step_battery(s: BatteryState, c: BatteryConfig, net_power_w: float, dt_s: float, temp_c: Optional[float] = None) -> BatteryState:
    """Step battery state forward in time.
    
    Args:
        s: Current battery state
        c: Battery configuration
        net_power_w: Net power into battery (positive = charging, negative = discharging)
        dt_s: Time step in seconds
        temp_c: Optional battery temperature in Celsius for temperature-dependent charging
    
    Returns:
        Updated battery state after the time step
    
    Notes:
        - Implements CC-CV (Constant Current-Constant Voltage) charging limiting
        - Applies temperature factor if temperature is provided
        - Calculates shunt dissipation for excess charge power
        - Clamps SOC between min_soc and max_soc
    """
    validate_positive(dt_s, "dt_s")
    if net_power_w >= 0:
        if temp_c is not None:
            temp_factor = _temperature_factor(temp_c, c)
            net_power_w = net_power_w * temp_factor
        charge_limit_w = _cc_cv_charge_limit(s.soc, c)
        charge_power_w = min(net_power_w, charge_limit_w)
        eff = c.charge_efficiency
        st = max(c.min_soc * s.capacity_wh, min(c.max_soc * s.capacity_wh, s.storage_wh + charge_power_w * dt_s / 3600 * eff))
        shunt_wh = (net_power_w - (st - s.storage_wh) * 3600 / dt_s / eff) * dt_s / 3600 if net_power_w > 0 else 0.0
        shunt_wh = max(0.0, shunt_wh)
    else:
        eff = 1 / max(c.discharge_efficiency, 1e-12)
        st = max(c.min_soc * s.capacity_wh, min(c.max_soc * s.capacity_wh, s.storage_wh + net_power_w * dt_s / 3600 * eff))
        shunt_wh = 0.0
    return BatteryState(st, s.capacity_wh, st / s.capacity_wh if s.capacity_wh > 0 else 0, s.shunt_dissipated_wh + shunt_wh)


def simulate_battery_power_profile(s: BatteryState, c: BatteryConfig, profile: Sequence[float], dt_s: float, temp_profile: Optional[Sequence[float]] = None) -> BatteryProfileResult:
    """Simulate battery over a power profile.
    
    Args:
        s: Initial battery state
        c: Battery configuration
        profile: Sequence of net power values (W) for each time step
        dt_s: Time step in seconds
        temp_profile: Optional sequence of temperatures (°C) for each time step
    
    Returns:
        BatteryProfileResult with time, SOC, storage, and shunt dissipation arrays
    """
    if dt_s <= 0:
        raise ValueError('dt_s')
    t = [0.0]
    soc = [s.soc]
    wh = [s.storage_wh]
    sh = [s.shunt_dissipated_wh]
    for i, p in enumerate(profile):
        temp_c = temp_profile[i] if temp_profile and i < len(temp_profile) else None
        s = step_battery(s, c, p, dt_s, temp_c)
        t.append((i + 1) * dt_s)
        soc.append(s.soc)
        wh.append(s.storage_wh)
        sh.append(s.shunt_dissipated_wh)
    return BatteryProfileResult(tuple(t), tuple(soc), tuple(wh), tuple(sh))

@dataclass(frozen=True)
class ThermalNodeConfig:
    """Thermal node configuration.
    
    Attributes:
        ambient_k: Ambient temperature in Kelvin
        tau_s: Thermal time constant in seconds
        heat_gain_k_per_w: Temperature rise per watt of power (K/W)
        min_temp_k: Minimum allowable temperature (K)
        max_temp_k: Maximum allowable temperature (K)
    """
    ambient_k: float = 300.0
    tau_s: float = 10.0
    heat_gain_k_per_w: float = 1.0
    min_temp_k: float = 0.0
    max_temp_k: float = 1000.0


@dataclass(frozen=True)
class ThermalNodeState:
    """Thermal node state.
    
    Attributes:
        temp_k: Current temperature in Kelvin
    """
    temp_k: float = 300.0


@dataclass(frozen=True)
class ThermalProfileResult:
    """Result of thermal simulation over time.
    
    Attributes:
        time_s: Time array in seconds
        temp_k: Temperature array in Kelvin
    """
    time_s: Tuple[float, ...]
    temp_k: Tuple[float, ...]


def step_thermal_node(s: ThermalNodeState, c: ThermalNodeConfig, power_w: float, dt_s: float) -> ThermalNodeState:
    """Step thermal node state forward in time.
    
    Implements first-order thermal dynamics:
    T(t+dt) = T(t) + alpha * (T_target - T(t))
    
    where alpha = min(1, dt/tau) and T_target = ambient + heat_gain * power
    
    Args:
        s: Current thermal node state
        c: Thermal node configuration
        power_w: Power input to the node (W)
        dt_s: Time step in seconds
    
    Returns:
        Updated thermal node state
    """
    validate_positive(dt_s, "dt_s")
    validate_positive(c.tau_s, "tau_s")
    alpha = min(1.0, dt_s / max(c.tau_s, 1e-12))
    target = c.ambient_k + c.heat_gain_k_per_w * power_w
    temp = max(c.min_temp_k, min(c.max_temp_k, s.temp_k + alpha * (target - s.temp_k)))
    return ThermalNodeState(temp)


def simulate_thermal_power_profile(s: ThermalNodeState, c: ThermalNodeConfig, profile: Sequence[float], dt_s: float) -> ThermalProfileResult:
    """Simulate thermal node over a power profile.
    
    Args:
        s: Initial thermal node state
        c: Thermal node configuration
        profile: Sequence of power values (W) for each time step
        dt_s: Time step in seconds
    
    Returns:
        ThermalProfileResult with time and temperature arrays
    """
    if dt_s <= 0:
        raise ValueError('dt_s')
    t = [0.0]
    temp = [s.temp_k]
    for i, p in enumerate(profile):
        s = step_thermal_node(s, c, p, dt_s)
        t.append((i + 1) * dt_s)
        temp.append(s.temp_k)
    return ThermalProfileResult(tuple(t), tuple(temp))

@dataclass(frozen=True)
class DataQueueConfig: capacity_bits:float=100.0
@dataclass(frozen=True)
class DataQueueState: queue_bits:float=0.0; dropped_bits:float=0.0; downlinked_bits:float=0.0
@dataclass(frozen=True)
class DataQueueProfileResult: time_s:tuple; queue_bits:tuple; dropped_bits:tuple; downlinked_bits:tuple
def step_data_queue(s,c,generated_bps,downlink_bps,dt_s):
    validate_positive(dt_s, "dt_s")
    validate_non_negative(c.capacity_bits, "capacity_bits")
    validate_non_negative(generated_bps, "generated_bps")
    validate_non_negative(downlink_bps, "downlink_bps")
    q=max(0,s.queue_bits)+max(0,generated_bps)*dt_s; down=min(q,max(0,downlink_bps)*dt_s); q-=down; drop=max(0,q-c.capacity_bits); q-=drop; return DataQueueState(q,s.dropped_bits+drop,s.downlinked_bits+down)
def simulate_queue_profile(s,c,gen,down,dt_s):
    if dt_s<=0: raise ValueError('dt_s')
    t=[0.0]; qs=[s.queue_bits]; ds=[s.dropped_bits]; dl=[s.downlinked_bits]
    for i in range(max(len(gen),len(down))):
        s=step_data_queue(s,c,gen[i] if i<len(gen) else 0,down[i] if i<len(down) else 0,dt_s); t.append((i+1)*dt_s); qs.append(s.queue_bits); ds.append(s.dropped_bits); dl.append(s.downlinked_bits)
    return DataQueueProfileResult(tuple(t),tuple(qs),tuple(ds),tuple(dl))

@dataclass(frozen=True)
class SolarPanelConfig:
    """Solar panel configuration.
    
    Attributes:
        max_power_w: Maximum power output under full sun (W)
        efficiency: Electrical efficiency (0-1)
        max_slew_rate_rad_s: Maximum rotation rate (rad/s)
    """
    max_power_w: float = 120.0
    efficiency: float = 0.28
    max_slew_rate_rad_s: float = 0.05


@dataclass(frozen=True)
class SolarPanelState:
    """Solar panel state.
    
    Attributes:
        normal_b: Panel normal vector in body frame
    """
    normal_b: Vec3 = (1.0, 0.0, 0.0)


@dataclass(frozen=True)
class SolarTrackingResult:
    """Result of solar tracking simulation.
    
    Attributes:
        time_s: Time array in seconds
        normals_b: Panel normal vectors over time
        power_w: Power output over time (W)
    """
    time_s: Tuple[float, ...]
    normals_b: Tuple[Vec3, ...]
    power_w: Tuple[float, ...]


def compute_solar_power(c: SolarPanelConfig, n: Sequence[float], sun: Sequence[float], shadow: float) -> float:
    """Compute solar panel power output.
    
    Args:
        c: Solar panel configuration
        n: Panel normal vector
        sun: Sun direction vector
        shadow: Shadow factor (0 = full shadow, 1 = full sun)
    
    Returns:
        Power output in watts
    """
    return max(0.0, c.max_power_w * c.efficiency * max(0, min(1, shadow)) * max(0, dot(unit(n), unit(sun))))


def slew_normal_toward(cur: Sequence[float], target: Sequence[float], max_ang: float) -> Vec3:
    """Slew a vector toward a target with maximum angular step.
    
    Uses Rodrigues' rotation formula for smooth rotation.
    
    Args:
        cur: Current vector
        target: Target vector
        max_ang: Maximum angular step (rad)
    
    Returns:
        New vector rotated toward target
    """
    c = unit(cur)
    t = unit(target)
    a = acos(max(-1, min(1, dot(c, t))))
    if a <= 1e-12 or max_ang >= a:
        return t
    k = unit(cross(c, t))
    kxc = cross(k, c)
    kd = dot(k, c)
    m = max(0, max_ang)
    return unit(tuple(c[i] * cos(m) + kxc[i] * sin(m) + k[i] * kd * (1 - cos(m)) for i in range(3)))


def step_solar_tracking(s: SolarPanelState, c: SolarPanelConfig, sun: Sequence[float], shadow: float, dt_s: float) -> Tuple[SolarPanelState, float]:
    """Step solar tracking for one time step.
    
    Args:
        s: Current solar panel state
        c: Solar panel configuration
        sun: Sun direction vector
        shadow: Shadow factor (0-1)
        dt_s: Time step in seconds
    
    Returns:
        Tuple of (updated state, power output in watts)
    """
    n = slew_normal_toward(s.normal_b, sun, max(0, c.max_slew_rate_rad_s) * dt_s)
    return SolarPanelState(n), compute_solar_power(c, n, sun, shadow)


def simulate_tracking_profile(s: SolarPanelState, c: SolarPanelConfig, suns: Sequence[Sequence[float]], shadows: Sequence[float], dt_s: float) -> SolarTrackingResult:
    """Simulate solar tracking over a profile of sun directions.
    
    Args:
        s: Initial solar panel state
        c: Solar panel configuration
        suns: Sequence of sun direction vectors
        shadows: Sequence of shadow factors (0-1)
        dt_s: Time step in seconds
    
    Returns:
        SolarTrackingResult with time, normals, and power arrays
    """
    t = [0.0]
    ns = [s.normal_b]
    p = [compute_solar_power(c, s.normal_b, suns[0] if suns else s.normal_b, shadows[0] if shadows else 1)]
    for i, sun in enumerate(suns):
        s, pw = step_solar_tracking(s, c, sun, shadows[i] if i < len(shadows) else 1, dt_s)
        t.append((i + 1) * dt_s)
        ns.append(s.normal_b)
        p.append(pw)
    return SolarTrackingResult(tuple(t), tuple(ns), tuple(p))

@dataclass(frozen=True)
class ReactionWheelCommandConfig: num_wheels:int=4; torque_limit_nm:float=0.2; deadzone_nm:float=0.0; failed_ids:tuple=(); stuck_torque_by_id:dict=field(default_factory=dict)
@dataclass(frozen=True)
class ReactionWheelDynamicsConfig: num_wheels:int=4; wheel_inertia_kg_m2:tuple=(0.1,0.1,0.1,0.1); max_motor_torque_nm:tuple=(0.2,0.2,0.2,0.2); max_speed_rad_s:tuple=(6000,6000,6000,6000); damping_nms:tuple=(0,0,0,0); wheel_axes_B:tuple=((1,0,0),(0,1,0),(0,0,1),(0.577350269,0.577350269,0.577350269))
@dataclass(frozen=True)
class ReactionWheelState: wheel_speeds_rad_s:tuple
@dataclass(frozen=True)
class ReactionWheelProfileResult: time_s:tuple; wheel_speeds_rad_s:tuple; motor_torque_nm:tuple; rotational_energy_j:tuple; momentum_norm_nms:tuple
def norm_rw(c):
    n=c.num_wheels; return ReactionWheelDynamicsConfig(n,expand(c.wheel_inertia_kg_m2,n,0.1),expand(c.max_motor_torque_nm,n,0.2),expand(c.max_speed_rad_s,n,6000),expand(c.damping_nms,n,0),tuple(unit(c.wheel_axes_B[i] if i<len(c.wheel_axes_B) else (1,0,0)) for i in range(n)))
def clamp_motor_torque(raw,c):
    c=norm_rw(c); return tuple(max(-c.max_motor_torque_nm[i],min(c.max_motor_torque_nm[i],float(raw[i]) if i<len(raw) else 0)) for i in range(c.num_wheels))
def step_wheel_speed(s,u,c,dt_s):
    validate_positive(dt_s, "dt_s")
    c=norm_rw(c); u=clamp_motor_torque(u,c); w=expand(s.wheel_speeds_rad_s,c.num_wheels,0); return ReactionWheelState(tuple(max(-c.max_speed_rad_s[i],min(c.max_speed_rad_s[i],w[i]+dt_s*(u[i]-c.damping_nms[i]*w[i])/c.wheel_inertia_kg_m2[i])) for i in range(c.num_wheels)))
def total_momentum_vector(s,c):
    c=norm_rw(c); w=expand(s.wheel_speeds_rad_s,c.num_wheels,0); hs=[tuple(c.wheel_inertia_kg_m2[i]*w[i]*a for a in c.wheel_axes_B[i]) for i in range(c.num_wheels)]; return tuple(sum(h[k] for h in hs) for k in range(3))
def momentum_norm_nms(s,c): return norm(total_momentum_vector(s,c))
def rotational_energy_j(s,c):
    c=norm_rw(c); w=expand(s.wheel_speeds_rad_s,c.num_wheels,0); return sum(0.5*c.wheel_inertia_kg_m2[i]*w[i]**2 for i in range(c.num_wheels))
def simulate_prescribed_torque_profile(s,profile,c,dt_s):
    if dt_s<=0: raise ValueError('dt_s')
    t=[0.0]; speeds=[s.wheel_speeds_rad_s]; tor=[]; e=[rotational_energy_j(s,c)]; h=[momentum_norm_nms(s,c)]
    for i,row in enumerate(profile): u=clamp_motor_torque(row,c); tor.append(u); s=step_wheel_speed(s,u,c,dt_s); t.append((i+1)*dt_s); speeds.append(s.wheel_speeds_rad_s); e.append(rotational_energy_j(s,c)); h.append(momentum_norm_nms(s,c))
    return ReactionWheelProfileResult(tuple(t),tuple(speeds),tuple(tor),tuple(e),tuple(h))
def apply_rw_command_faults(raw,c):
    out=[0.0]*c.num_wheels
    for i in range(min(c.num_wheels,len(raw))):
        v=0.0 if abs(raw[i])<c.deadzone_nm else max(-c.torque_limit_nm,min(c.torque_limit_nm,float(raw[i]))); out[i]=v
    for i in c.failed_ids:
        if 0<=i<len(out): out[i]=0.0
    for i,v in c.stuck_torque_by_id.items():
        if 0<=i<len(out): out[i]=max(-c.torque_limit_nm,min(c.torque_limit_nm,float(v)))
    return out

@dataclass(frozen=True)
class ThrusterCommandConfig: num_thrusters:int=4; active_ids:tuple=(0,1,2,3); nominal_on_time_s:float=0.1; min_pulse_s:float=0.02; stuck_closed_ids:tuple=()
@dataclass(frozen=True)
class ThrusterPhysicalConfig: thrust_n:tuple=(1.0,1.0,1.0,1.0); isp_s:tuple=(280.0,280.0,280.0,280.0); directions_b:tuple=((1,0,0),(-1,0,0),(0,1,0),(0,-1,0)); lever_arms_b_m:tuple=((0,0.5,0),(0,0.5,0),(0.5,0,0),(0.5,0,0))
@dataclass(frozen=True)
class ThrusterPulseResult: impulse_ns:tuple; total_force_impulse_b_ns:tuple; total_torque_impulse_b_nms:tuple; propellant_used_kg:float
@dataclass(frozen=True)
class ThrusterPulseTrainResult: time_s:tuple; cumulative_propellant_kg:tuple; cumulative_impulse_ns:tuple
def shape_thruster_on_time(c,on):
    out=[0.0]*c.num_thrusters
    if on:
        for i in c.active_ids:
            if 0<=i<c.num_thrusters: out[i]=max(c.nominal_on_time_s,c.min_pulse_s)
    for i in c.stuck_closed_ids:
        if 0<=i<c.num_thrusters: out[i]=0.0
    return out
def _g(seq,i,default): return seq[i] if i<len(seq) else default
def compute_thruster_pulse(on,c):
    force=[0,0,0]; torque=[0,0,0]; impulses=[]; mass=0.0
    for i,t in enumerate(on):
        th=float(_g(c.thrust_n,i,c.thrust_n[-1])); imp=th*max(0,float(t)); impulses.append(imp); d=v3(_g(c.directions_b,i,(1,0,0))); r=v3(_g(c.lever_arms_b_m,i,(0,0,0)))
        for k in range(3): force[k]+=imp*d[k]
        tq=cross(r,[imp*x for x in d])
        for k in range(3): torque[k]+=tq[k]
        mass+=imp/(max(1e-12,float(_g(c.isp_s,i,c.isp_s[-1])))*G0)
    return ThrusterPulseResult(tuple(impulses),tuple(force),tuple(torque),mass)
def simulate_pulse_train(profile,c,dt_s):
    if dt_s<=0: raise ValueError('dt_s')
    t=[0.0]; m=[0.0]; im=[0.0]; cm=0.0; ci=0.0
    for i,row in enumerate(profile): r=compute_thruster_pulse(row,c); cm+=r.propellant_used_kg; ci+=sum(r.impulse_ns); t.append((i+1)*dt_s); m.append(cm); im.append(ci)
    return ThrusterPulseTrainResult(tuple(t),tuple(m),tuple(im))

@dataclass(frozen=True)
class FuelTankConfig: capacity_kg:float=3.0; initial_mass_kg:float=2.0; full_pressure_pa:float=2.5e6; dry_pressure_pa:float=1.5e5
@dataclass(frozen=True)
class FuelTankState: propellant_mass_kg:float; pressure_pa:float
@dataclass(frozen=True)
class FuelTankProfileResult: time_s:tuple; propellant_mass_kg:tuple; pressure_pa:tuple
def pressure_from_mass(m,c): return c.dry_pressure_pa+max(0,min(1,m/max(c.capacity_kg,1e-12)))*(c.full_pressure_pa-c.dry_pressure_pa)
def initialize_fuel_tank(c): m=max(0,min(c.capacity_kg,c.initial_mass_kg)); return FuelTankState(m,pressure_from_mass(m,c))
def step_fuel_tank(s,c,mdot,dt_s):
    validate_positive(dt_s, "dt_s")
    validate_positive(c.capacity_kg, "capacity_kg")
    m=max(0,min(c.capacity_kg,s.propellant_mass_kg-max(0,mdot)*dt_s)); return FuelTankState(m,pressure_from_mass(m,c))
def simulate_mass_flow_profile(s,c,profile,dt_s):
    t=[0.0]; m=[s.propellant_mass_kg]; p=[s.pressure_pa]
    for i,md in enumerate(profile): s=step_fuel_tank(s,c,md,dt_s); t.append((i+1)*dt_s); m.append(s.propellant_mass_kg); p.append(s.pressure_pa)
    return FuelTankProfileResult(tuple(t),tuple(m),tuple(p))

@dataclass(frozen=True)
class MtbConfig: num_axes:int=3; dipole_limit_am2:tuple=(1,1,1); lag_tau_s:float=0.0
@dataclass(frozen=True)
class MtbState: dipole_am2:tuple=(0,0,0)
@dataclass(frozen=True)
class MtbProfileResult: time_s:tuple; dipole_am2:tuple; torque_nm:tuple
def shape_mtb_command(raw,c): return tuple(max(-c.dipole_limit_am2[i],min(c.dipole_limit_am2[i],float(raw[i]) if i<len(raw) else 0)) for i in range(c.num_axes))
def update_mtb_dipole(raw,s,c,dt_s):
    cmd=shape_mtb_command(raw,c)
    if c.lag_tau_s<=0 or dt_s<=0: return MtbState(cmd)
    a=dt_s/(c.lag_tau_s+dt_s); prev=expand(s.dipole_am2,c.num_axes,0); return MtbState(tuple(prev[i]+a*(cmd[i]-prev[i]) for i in range(c.num_axes)))
def compute_mtb_torque_nm(m,b): return cross(m,b)
def simulate_dipole_profile(s,c,profile,b,dt_s):
    t=[0.0]; d=[s.dipole_am2]; tq=[compute_mtb_torque_nm(s.dipole_am2,b)]
    for i,cmd in enumerate(profile): s=update_mtb_dipole(cmd,s,c,dt_s); t.append((i+1)*dt_s); d.append(s.dipole_am2); tq.append(compute_mtb_torque_nm(s.dipole_am2,b))
    return MtbProfileResult(tuple(t),tuple(d),tuple(tq))

def project_torque_perpendicular_to_field(tau,b):
    b=v3(b); tau=v3(tau); b2=dot(b,b)
    if b2<=0: return ((0,0,0),tuple(tau))
    ps=dot(tau,b)/b2; par=[ps*x for x in b]; perp=[tau[i]-par[i] for i in range(3)]; return tuple(perp),tuple(par)
@dataclass(frozen=True)
class MtbMappingResult: dipole_am2:tuple; achievable_torque_nm:tuple; rejected_parallel_torque_nm:tuple; saturated:bool; zero_field_guard:bool
def map_torque_to_dipole(tau,b,c=None):
    c=c or MtbConfig(); b=v3(b); b2=dot(b,b)
    if b2<=1e-18: return MtbMappingResult((0,0,0),(0,0,0),tuple(v3(tau)),False,True)
    ach,rej=project_torque_perpendicular_to_field(tau,b); raw=tuple(x/b2 for x in cross(b,ach)); cmd=shape_mtb_command(raw,c); return MtbMappingResult(cmd,compute_mtb_torque_nm(cmd,b),rej,False,False)

@dataclass(frozen=True)
class SingleGimbalCmgConfig: wheel_inertia_kg_m2:float=1.0; wheel_speed_rad_s:float=10.0; gimbal_angle_rad:float=0.0; spin_axis_g0_b:tuple=(1,0,0); gimbal_axis_b:tuple=(0,0,1); gimbal_rate_limit_rad_s:float|None=1.0; wheel_speed_limit_rad_s:float|None=100.0
@dataclass(frozen=True)
class SingleGimbalCmgState: wheel_speed_rad_s:float; gimbal_angle_rad:float
@dataclass(frozen=True)
class CmgPhysicsResult: torque_nm:tuple; spin_axis_b:tuple; transverse_axis_b:tuple; wheel_momentum_nms:float; gimbal_rate_rad_s:float; guard_tripped:bool=False; guard_reason:str|None=None
@dataclass(frozen=True)
class CmgProfileResult: time_s:tuple; gimbal_angle_rad:tuple; wheel_speed_rad_s:tuple; torque_nm:tuple
@dataclass(frozen=True)
class CmgConfig: num_cmgs:int=4; wheel_torque_limit_nm:float=0.4; gimbal_torque_limit_nm:float=0.4
@dataclass(frozen=True)
class CmgCommandResult: wheel_torque_nm:tuple; gimbal_torque_nm:tuple; guard_tripped:bool; guard_reason:str|None=None
@dataclass(frozen=True)
class CmgArrayGeometry: configs:tuple=()
def rotate_about_axis(vec,axis,ang):
    v=v3(vec); k=unit(axis); kxv=cross(k,v); kd=dot(k,v); return tuple(v[i]*cos(ang)+kxv[i]*sin(ang)+k[i]*kd*(1-cos(ang)) for i in range(3))
def spin_axis_b(c,gamma_rad=None): return unit(rotate_about_axis(c.spin_axis_g0_b,c.gimbal_axis_b,c.gimbal_angle_rad if gamma_rad is None else gamma_rad))
def transverse_axis_b(c,gamma_rad=None): return unit(cross(unit(c.gimbal_axis_b),spin_axis_b(c,gamma_rad)))
def wheel_momentum_nms(c,wheel_speed_rad_s=None): return c.wheel_inertia_kg_m2*(c.wheel_speed_rad_s if wheel_speed_rad_s is None else wheel_speed_rad_s)
def compute_single_cmg_gyro_torque(c,gimbal_rate_rad_s,wheel_speed_rad_s=None,gamma_rad=None):
    rate=gimbal_rate_rad_s if c.gimbal_rate_limit_rad_s is None else max(-c.gimbal_rate_limit_rad_s,min(c.gimbal_rate_limit_rad_s,gimbal_rate_rad_s)); omega=c.wheel_speed_rad_s if wheel_speed_rad_s is None else wheel_speed_rad_s; h=wheel_momentum_nms(c,omega)
    if c.wheel_speed_limit_rad_s is not None and abs(omega)>c.wheel_speed_limit_rad_s: return CmgPhysicsResult((0,0,0),spin_axis_b(c,gamma_rad),transverse_axis_b(c,gamma_rad),h,rate,True,'wheel_speed_overlimit')
    gt=transverse_axis_b(c,gamma_rad); return CmgPhysicsResult(tuple(-h*rate*x for x in gt),spin_axis_b(c,gamma_rad),gt,h,rate)
def propagate_single_cmg_state(s,c,gimbal_rate_cmd_rad_s,wheel_torque_nm,dt_s):
    rate=max(-c.gimbal_rate_limit_rad_s,min(c.gimbal_rate_limit_rad_s,gimbal_rate_cmd_rad_s)) if c.gimbal_rate_limit_rad_s is not None else gimbal_rate_cmd_rad_s; omega=s.wheel_speed_rad_s+wheel_torque_nm/max(c.wheel_inertia_kg_m2,1e-12)*dt_s
    if c.wheel_speed_limit_rad_s is not None: omega=max(-c.wheel_speed_limit_rad_s,min(c.wheel_speed_limit_rad_s,omega))
    return SingleGimbalCmgState(omega,s.gimbal_angle_rad+rate*dt_s)
def simulate_gimbal_rate_profile(s,c,rates,wheel_torques,dt_s):
    t=[0.0]; g=[s.gimbal_angle_rad]; w=[s.wheel_speed_rad_s]; tq=[]
    for i in range(max(len(rates),len(wheel_torques))):
        rate=rates[i] if i<len(rates) else 0; wt=wheel_torques[i] if i<len(wheel_torques) else 0; tq.append(compute_single_cmg_gyro_torque(c,rate,s.wheel_speed_rad_s,s.gimbal_angle_rad).torque_nm); s=propagate_single_cmg_state(s,c,rate,wt,dt_s); t.append((i+1)*dt_s); g.append(s.gimbal_angle_rad); w.append(s.wheel_speed_rad_s)
    return CmgProfileResult(tuple(t),tuple(g),tuple(w),tuple(tq))
def shape_cmg_command(w,g,c,*_,**__): return CmgCommandResult(tuple(w[:c.num_cmgs]),tuple(g[:c.num_cmgs]),False,None)
def default_pyramid_geometry(momentum_nms=15.0): return CmgArrayGeometry(())
def steering_matrix(*a,**k): return []
def steering_rank_and_condition(*a,**k): return (0,float('inf'))

@dataclass(frozen=True)
class ImuConfig: gyro_scale:tuple=(1,1,1); accel_scale:tuple=(1,1,1); gyro_bias_rad_s:tuple=(0,0,0); accel_bias_m_s2:tuple=(0,0,0); gyro_bias_walk_std_rad_s_sqrt_s:float=0.0
@dataclass(frozen=True)
class ImuMeasurement: gyro_rad_s:tuple; accel_m_s2:tuple
@dataclass(frozen=True)
class ImuBiasState: gyro_bias_rad_s:tuple=(0,0,0)
@dataclass(frozen=True)
class ImuBiasProfileResult: time_s:tuple; gyro_bias_rad_s:tuple
def measure_imu(g,a,c,bias_state=None):
    b=bias_state.gyro_bias_rad_s if bias_state else (0,0,0); return ImuMeasurement(tuple(v3(g)[i]*c.gyro_scale[i]+c.gyro_bias_rad_s[i]+b[i] for i in range(3)),tuple(v3(a)[i]*c.accel_scale[i]+c.accel_bias_m_s2[i] for i in range(3)))
def simulate_gyro_bias_profile(s,c,steps,dt_s,seed=None):
    rng=Random(seed); t=[0.0]; b=[s.gyro_bias_rad_s]
    for i in range(steps): std=c.gyro_bias_walk_std_rad_s_sqrt_s*sqrt(dt_s); s=ImuBiasState(tuple(s.gyro_bias_rad_s[k]+rng.gauss(0,std) for k in range(3))); t.append((i+1)*dt_s); b.append(s.gyro_bias_rad_s)
    return ImuBiasProfileResult(tuple(t),tuple(b))

@dataclass(frozen=True)
class MagnetometerConfig: scale:tuple=(1,1,1); bias_t:tuple=(0,0,0); mounting_matrix_sb:tuple|None=None; clip_t:float|tuple|None=None; noise_std_t:float|tuple=0.0; noise_seed:int|None=None
def _mat(m): return [list(r) for r in (m or ((1,0,0),(0,1,0),(0,0,1)))]
def _mul(m,v): return [sum(m[i][j]*v[j] for j in range(3)) for i in range(3)]
def measure_magnetic_field_sensor(b,c):
    v=v3(b); return tuple(v[i]*c.scale[i]+c.bias_t[i] for i in range(3))
def measure_magnetic_field_body(b,c,output_frame='sensor'):
    return measure_magnetic_field_sensor(_mul(_mat(c.mounting_matrix_sb),v3(b)),c)
def measure_magnetic_field(b,c): return measure_magnetic_field_sensor(b,c)

@dataclass(frozen=True)
class StarTrackerConfig: drift_rate_mrp_s:tuple=(0,0,0); max_drift_norm:float=1.0
@dataclass(frozen=True)
class StarTrackerState: drift_bias_mrp:tuple=(0,0,0)
@dataclass(frozen=True)
class StarTrackerMeasurement: valid:bool; sigma_bn:tuple; drift_bias_mrp:tuple
@dataclass(frozen=True)
class StarTrackerProfileResult: time_s:tuple; drift_bias_mrp:tuple; sigma_bn:tuple
def step_star_tracker(s,sigma,c,dt_s):
    b=tuple(s.drift_bias_mrp[i]+c.drift_rate_mrp_s[i]*dt_s for i in range(3)); n=norm(b)
    if n>c.max_drift_norm: b=tuple(x*c.max_drift_norm/n for x in b)
    return StarTrackerState(b),StarTrackerMeasurement(True,tuple(v3(sigma)[i]+b[i] for i in range(3)),b)
def measure_star_tracker(sigma,c,dt_s=0.0,state=None): return step_star_tracker(state or StarTrackerState(),sigma,c,dt_s)[1]
def simulate_star_tracker_profile(s,c,profile,dt_s):
    t=[0.0]; b=[s.drift_bias_mrp]; sig=[]
    for i,x in enumerate(profile): s,m=step_star_tracker(s,x,c,dt_s); t.append((i+1)*dt_s); b.append(s.drift_bias_mrp); sig.append(m.sigma_bn)
    return StarTrackerProfileResult(tuple(t),tuple(b),tuple(sig))

@dataclass(frozen=True)
class SunSensorConfig: min_intensity:float=1e-6
@dataclass(frozen=True)
class SunSensorMeasurement: valid:bool; sun_direction_b:tuple; intensity:float
@dataclass(frozen=True)
class SunSensorProfileResult: valid:tuple; intensity:tuple
def measure_sun_sensor(sun,shadow,c):
    n=norm(v3(sun)); inten=n*max(0,min(1,shadow)); return SunSensorMeasurement(inten>=c.min_intensity and n>0,unit(sun) if n>0 and inten>=c.min_intensity else (0,0,0),inten)
def simulate_sun_sensor_profile(suns,shadows,c):
    vals=[measure_sun_sensor(s,shadows[i] if i<len(shadows) else 1,c) for i,s in enumerate(suns)]; return SunSensorProfileResult(tuple(v.valid for v in vals),tuple(v.intensity for v in vals))

@dataclass(frozen=True)
class PowerSinkConfig: name:str='load'; base_w:float=0.0; mode_power_w:dict|None=None
def demand_w(c,mode='',enabled=True): return 0.0 if not enabled else float((c.mode_power_w or {}).get(mode,c.base_w))
@dataclass(frozen=True)
class PduConfig: bus_max_w:float=100.0; shed_order:tuple=()
@dataclass(frozen=True)
class PduResult: demand_after_w:float; shed:tuple; overload_remaining:bool
def apply_load_shedding(loads,c):
    active=dict(loads); demand=sum(max(0,float(v)) for v in active.values()); shed=[]
    for name in c.shed_order:
        if demand<=c.bus_max_w: break
        if name in active: demand-=max(0,float(active.pop(name))); shed.append(name)
    return PduResult(demand,tuple(shed),demand>c.bus_max_w)
def simulate_load_sequence(seq,c): return tuple(apply_load_shedding(x,c) for x in seq)
@dataclass(frozen=True)
class LinkBudgetConfig: raw_rate_bps:float=1000.0; tx_power_w:float=1.0; tx_gain_dbi:float=0.0; rx_gain_dbi:float=0.0; misc_loss_db:float=0.0; freq_hz:float=2.2e9; noise_temp_k:float=500.0; downlink_eff:float=1.0
@dataclass(frozen=True)
class LinkBudgetResult: effective_rate_bps:float; ber:float; ebn0_db:float; fspl_db:float
@dataclass(frozen=True)
class LinkBudgetProfileResult: slant_range_m:tuple; effective_rate_bps:tuple; ebn0_db:tuple
def compute_link_budget(c,slant_range_m):
    wl=299792458.0/c.freq_hz; fspl=20*log10(4*pi*max(1,slant_range_m)/wl); pr=10*log10(max(c.tx_power_w,1e-12))+c.tx_gain_dbi+c.rx_gain_dbi-fspl-c.misc_loss_db; eb=pr-(-228.6+10*log10(c.noise_temp_k))-10*log10(max(1,c.raw_rate_bps)); ber=max(0,min(1,0.5*erfc(sqrt(max(0,10**(eb/10)))))); return LinkBudgetResult(c.raw_rate_bps*(1-ber)*c.downlink_eff,ber,eb,fspl)
def simulate_range_profile(c,ranges):
    vals=[compute_link_budget(c,r) for r in ranges]; return LinkBudgetProfileResult(tuple(ranges),tuple(v.effective_rate_bps for v in vals),tuple(v.ebn0_db for v in vals))
@dataclass(frozen=True)
class GroundStationConfig: min_elevation_deg:float=5.0; max_range_m:float=2e6
@dataclass(frozen=True)
class GroundAccessResult: has_access:bool; elevation_deg:float; slant_range_m:float
@dataclass(frozen=True)
class GroundAccessProfileResult: has_access:tuple; elevation_deg:tuple
def compute_ground_access(station,sc,c):
    st=v3(station); sv=v3(sc); rho=[sv[i]-st[i] for i in range(3)]; r=norm(rho); up=unit(st); elev=asin(max(-1,min(1,dot(rho,up)/max(r,1e-12))))*180/pi; return GroundAccessResult(elev>=c.min_elevation_deg and r<=c.max_range_m,elev,r)
def simulate_access_profile(station,profile,c):
    vals=[compute_ground_access(station,x,c) for x in profile]; return GroundAccessProfileResult(tuple(v.has_access for v in vals),tuple(v.elevation_deg for v in vals))
POWER_DEVICE_SECTIONS=('base','adcs','payload','comm','thermal')
