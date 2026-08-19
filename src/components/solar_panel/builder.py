"""solar_panel component builder module.

Provides both Python model config builders and Basilisk-native component factories.

Basilisk integration notes
---------------------------
1. The ``simpleSolarPanel`` module represents a fixed solar-panel normal,
   consumes Basilisk sun/spacecraft/eclipse messages, produces cosine-law power,
   and exposes a power-node output that can be wired to ``simpleBattery``.
2. Platform tracking layer (optional): ``SolarPanelTrackingSysModel`` updates
   the panel normal with a maximum slew-rate constraint using Rodrigues rotation,
   running inside the Basilisk simulation loop via ``sysModel.SysModel``.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import SolarPanelConfig, SolarPanelNativeConfig, SOLAR_CONSTANT_W_M2, Vector3

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Sequence

from ..dynamic_models import v3, dot, cross, norm, unit, expand, normalize_safe

from ..power_sink.builder import build_simple_power_sink
from .degradation import SolarPanelDegradation
from .degradation import apply_solar_panel_degradation
from .faults import SolarPanelFaultType
from .faults import FaultSpec




@dataclass(frozen=True)
class SolarPanelState:
    normal_b: tuple = (1.0, 0.0, 0.0)


@dataclass(frozen=True)
class SolarTrackingResult:
    time_s: tuple
    normals_b: tuple
    power_w: tuple


def compute_solar_power(c, n, sun, shadow):
    return max(
        0.0,
        c.max_power_w
        * c.efficiency
        * max(0, min(1, shadow))
        * max(0, dot(unit(n), unit(sun))),
    )


def slew_normal_toward(cur, target, max_ang):
    c = unit(cur)
    t = unit(target)
    a = math.acos(max(-1, min(1, dot(c, t))))
    if a <= 1e-12 or max_ang >= a:
        return t
    k = unit(cross(c, t))
    kxc = cross(k, c)
    kd = dot(k, c)
    m = max(0, max_ang)
    return unit(
        tuple(
            c[i] * math.cos(m) + kxc[i] * math.sin(m) + k[i] * kd * (1 - math.cos(m))
            for i in range(3)
        )
    )


def step_solar_tracking(s, c, sun, shadow, dt_s):
    n = slew_normal_toward(s.normal_b, sun, max(0, c.max_slew_rate_rad_s) * dt_s)
    return SolarPanelState(n), compute_solar_power(c, n, sun, shadow)


def simulate_tracking_profile(s, c, suns, shadows, dt_s):
    if dt_s <= 0:
        raise ValueError("dt_s")
    t = [0.0]
    ns = [s.normal_b]
    p = [
        compute_solar_power(
            c, s.normal_b, suns[0] if suns else s.normal_b, shadows[0] if shadows else 1
        )
    ]
    for i, sun in enumerate(suns):
        s, pw = step_solar_tracking(
            s, c, sun, shadows[i] if i < len(shadows) else 1, dt_s
        )
        t.append((i + 1) * dt_s)
        ns.append(s.normal_b)
        p.append(pw)
    return SolarTrackingResult(tuple(t), tuple(ns), tuple(p))


def _build_nominal_solar_panel_config_base_impl(max_power_w: float = 100, max_slew_rate_rad_s: float = 0.2, efficiency: float = 0.28, degradation: SolarPanelDegradation | None = None, fault_specs: list[FaultSpec] | None = None) -> SolarPanelConfig:
    """Build a nominal SolarPanelConfig with all parameters defaulted."""
    config = SolarPanelConfig(
        max_power_w=max_power_w,
        max_slew_rate_rad_s=max_slew_rate_rad_s,
        efficiency=efficiency,
    )
    if degradation is not None:
        config = apply_solar_panel_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_solar_panel_config_faults(config, fault_specs)
    return config


def apply_solar_panel_config_faults(config: SolarPanelConfig, fault_specs: list[FaultSpec]) -> SolarPanelConfig:
    """Apply fault specifications to solar panel configuration."""
    from dataclasses import replace

    new_config = config

    for spec in fault_specs:
        if not isinstance(spec.fault_type, SolarPanelFaultType):
            continue

        if spec.fault_type == SolarPanelFaultType.Failure:
            new_config = replace(
                new_config,
                max_power_w=new_config.max_power_w * (1.0 - spec.magnitude),
                efficiency=new_config.efficiency * (1.0 - spec.magnitude)
            )

        elif spec.fault_type == SolarPanelFaultType.Degradation:
            new_config = replace(
                new_config,
                efficiency=new_config.efficiency * (1.0 - spec.magnitude)
            )

        elif spec.fault_type == SolarPanelFaultType.DeploymentFailure:
            new_config = replace(
                new_config,
                max_power_w=0.0
            )

    return new_config


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
        location='src/components/solar_panel/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )


def _require_basilisk_messaging_sysmodel() -> None:
    global _messaging, _sysModel
    if _messaging is None or _sysModel is None:
        try:
            from Basilisk.architecture import messaging, sysModel

            _messaging = messaging
            _sysModel = sysModel
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                f"Basilisk messaging/sysModel modules are unavailable: {exc}"
            ) from exc


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


_SolarPanelTrackingBase = _sysModel.SysModel if _sysModel is not None else object



def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _mrp_to_dcm(
    sigma: Sequence[float],
) -> tuple[Vector3, Vector3, Vector3]:
    """Convert Modified Rodrigues Parameters to Direction Cosine Matrix.

    Returns DCM such that v_body = DCM @ v_inertial.
    """
    s0, s1, s2 = v3(sigma)
    s_norm2 = s0 * s0 + s1 * s1 + s2 * s2

    d = (1.0 - s_norm2) ** 2 + 4.0 * (s0 * s0 + s1 * s1 + s2 * s2)
    if d < 1e-12:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    c = 1.0 - s_norm2
    dcm00 = 4.0 * (s0 * s0 - s1 * s1 - s2 * s2) + c * c
    dcm11 = 4.0 * (-s0 * s0 + s1 * s1 - s2 * s2) + c * c
    dcm22 = 4.0 * (-s0 * s0 - s1 * s1 + s2 * s2) + c * c
    dcm01 = 8.0 * s0 * s1 + 4.0 * c * s2
    dcm02 = 8.0 * s0 * s2 - 4.0 * c * s1
    dcm10 = 8.0 * s0 * s1 - 4.0 * c * s2
    dcm12 = 8.0 * s1 * s2 + 4.0 * c * s0
    dcm20 = 8.0 * s0 * s2 + 4.0 * c * s1
    dcm21 = 8.0 * s1 * s2 - 4.0 * c * s0

    factor = 1.0 / d
    return (
        (dcm00 * factor, dcm01 * factor, dcm02 * factor),
        (dcm10 * factor, dcm11 * factor, dcm12 * factor),
        (dcm20 * factor, dcm21 * factor, dcm22 * factor),
    )


def _dcm_times_vec(
    dcm: tuple[Vector3, Vector3, Vector3],
    v: Sequence[float],
) -> Vector3:
    vv = v3(v)
    return (
        dcm[0][0] * vv[0] + dcm[0][1] * vv[1] + dcm[0][2] * vv[2],
        dcm[1][0] * vv[0] + dcm[1][1] * vv[1] + dcm[1][2] * vv[2],
        dcm[2][0] * vv[0] + dcm[2][1] * vv[1] + dcm[2][2] * vv[2],
    )




@dataclass(frozen=True)
class SolarPanelNativeState:
    """Solar-panel attitude state used by the platform tracking layer."""

    normal_b: Vector3 = (1.0, 0.0, 0.0)


@dataclass(frozen=True)
class SolarPanelPowerSample:
    """Time-sample emitted by the native-compatible solar-panel runner."""

    time_s: float
    normal_b: Vector3
    sun_direction_b: Vector3
    shadow_factor: float
    cos_incidence: float
    ideal_power_w: float
    power_w: float
    source: str
    tracking_boundary: str

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SolarPanelNativeBundle:
    """Created Basilisk/native-compatible solar-panel objects.

    When ``simple_solar_panel`` is not None, it is the native Basilisk module
    handling power generation.  When ``tracking_sys_model`` is not None, it is
    the platform tracking module that updates the panel normal inside the
    simulation loop.
    """

    config: SolarPanelNativeConfig
    simple_solar_panel: Any | None
    tracking_sys_model: "SolarPanelTrackingSysModel | None"
    deterministic_power_node: Any | None
    power_output_msg: Any | None
    native_simple_solar_panel_available: bool
    tracking_boundary: str
    note: str


@dataclass(frozen=True)
class SolarPanelNativeProfileResult:
    """Profile result for UI/reporting and tests."""

    samples: tuple[SolarPanelPowerSample, ...]

    @property
    def rows(self) -> list[dict[str, Any]]:
        return [sample.to_row() for sample in self.samples]

    @property
    def final_power_w(self) -> float:
        return self.samples[-1].power_w if self.samples else 0.0

    @property
    def final_normal_b(self) -> Vector3:
        return self.samples[-1].normal_b if self.samples else (1.0, 0.0, 0.0)


def basilisk_simple_solar_panel_available() -> bool:
    """Return True when Basilisk's ``simpleSolarPanel`` module can be imported."""

    try:
        from Basilisk.simulation import simpleSolarPanel  # noqa: F401

        return True
    except Exception:
        return False


def incidence_cosine(
    panel_normal_b: Sequence[float], sun_direction_b: Sequence[float]
) -> float:
    """Return ``max(0, n_hat dot s_hat)`` for panel/sun incidence."""

    return max(0.0, dot(unit(panel_normal_b), unit(sun_direction_b)))


def compute_cosine_power_w(
    max_power_w: float,
    efficiency: float,
    panel_normal_b: Sequence[float],
    sun_direction_b: Sequence[float],
    shadow_factor: float = 1.0,
) -> float:
    """Compute project-level cosine solar-panel power.

    Formula used by the project UI and platform layer:
    ``P = Pmax * eta * shadow * max(0, n_hat dot s_hat)``.
    """

    return (
        max(0.0, float(max_power_w))
        * max(0.0, float(efficiency))
        * clamp01(float(shadow_factor))
        * incidence_cosine(panel_normal_b, sun_direction_b)
    )


def slew_normal_rodrigues(
    current_normal_b: Sequence[float],
    target_sun_direction_b: Sequence[float],
    max_slew_angle_rad: float,
) -> Vector3:
    """Slew a panel normal toward the sun with Rodrigues rotation.

    This is not a Basilisk ``simpleSolarPanel`` native feature.  It is the
    project/platform tracking layer used to update the normal before evaluating
    solar power.
    """

    current = unit(current_normal_b)
    target = unit(target_sun_direction_b)
    angle = math.acos(max(-1.0, min(1.0, dot(current, target))))
    if angle <= 1e-12:
        return current
    if max_slew_angle_rad >= angle:
        return target
    max_angle = max(0.0, float(max_slew_angle_rad))
    axis = cross(current, target)
    if norm(axis) <= 1e-12:
        return current
    axis = unit(axis)
    axis_cross_current = cross(axis, current)
    axis_dot_current = dot(axis, current)
    rotated = tuple(
        current[i] * math.cos(max_angle)
        + axis_cross_current[i] * math.sin(max_angle)
        + axis[i] * axis_dot_current * (1.0 - math.cos(max_angle))
        for i in range(3)
    )
    return unit(rotated)


def step_solar_panel_native_compatible(
    state: SolarPanelNativeState,
    config: SolarPanelNativeConfig,
    sun_direction_b: Sequence[float] | None = None,
    shadow_factor: float | None = None,
    dt_s: float = 1.0,
    time_s: float = 0.0,
) -> tuple[SolarPanelNativeState, SolarPanelPowerSample]:
    """Advance the solar-panel profile by one step.

    When ``enable_tracking`` is false, the normal is fixed, matching Basilisk
    ``simpleSolarPanel`` semantics.  When true, the normal is updated by the
    project tracking boundary before power is computed.
    """

    sun = unit(
        sun_direction_b if sun_direction_b is not None else config.sun_direction_b
    )
    shadow = clamp01(
        config.shadow_factor if shadow_factor is None else float(shadow_factor)
    )
    normal = unit(state.normal_b)
    boundary = "basilisk_fixed_panel"
    if config.enable_tracking:
        normal = slew_normal_rodrigues(
            normal,
            sun,
            max(0.0, float(config.max_slew_rate_rad_s)) * max(0.0, float(dt_s)),
        )
        boundary = "platform_tracking_rodrigues_not_basilisk_native"
    cos_inc = incidence_cosine(normal, sun)
    ideal_power = max(0.0, float(config.max_power_w)) * max(
        0.0, float(config.efficiency)
    )
    power = ideal_power * shadow * cos_inc
    sample = SolarPanelPowerSample(
        time_s=float(time_s),
        normal_b=normal,
        sun_direction_b=sun,
        shadow_factor=shadow,
        cos_incidence=cos_inc,
        ideal_power_w=ideal_power,
        power_w=power,
        source=(
            "simpleSolarPanel_semantics_plus_platform_tracking"
            if config.enable_tracking
            else "simpleSolarPanel_semantics_fixed_normal"
        ),
        tracking_boundary=boundary,
    )
    return SolarPanelNativeState(normal_b=normal), sample


def simulate_solar_panel_native_compatible_profile(
    config: SolarPanelNativeConfig | None = None,
    duration_s: float = 600.0,
    dt_s: float = 60.0,
    sun_directions_b: Sequence[Sequence[float]] | None = None,
    shadow_factors: Sequence[float] | None = None,
) -> SolarPanelNativeProfileResult:
    """Generate a solar-panel power profile for UI/testing/reporting."""

    cfg = config or SolarPanelNativeConfig()
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")
    if duration_s < 0.0:
        raise ValueError("duration_s must be non-negative")
    steps = max(1, int(round(float(duration_s) / float(dt_s))))
    state = SolarPanelNativeState(unit(cfg.initial_normal_b))
    samples: list[SolarPanelPowerSample] = []
    for i in range(steps + 1):
        sun = (
            sun_directions_b[i]
            if sun_directions_b is not None and i < len(sun_directions_b)
            else cfg.sun_direction_b
        )
        shadow = (
            shadow_factors[i]
            if shadow_factors is not None and i < len(shadow_factors)
            else cfg.shadow_factor
        )
        if i == 0:
            _, sample = step_solar_panel_native_compatible(
                state, cfg, sun, shadow, dt_s=0.0, time_s=0.0
            )
        else:
            state, sample = step_solar_panel_native_compatible(
                state,
                cfg,
                sun,
                shadow,
                dt_s=dt_s,
                time_s=min(float(duration_s), i * float(dt_s)),
            )
        samples.append(sample)
    return SolarPanelNativeProfileResult(tuple(samples))


class SolarPanelTrackingSysModel(_SolarPanelTrackingBase):
    """Basilisk-scheduled solar-panel tracking module.

    This is a custom :class:`sysModel.SysModel` that runs inside the Basilisk
    simulation loop and updates the panel normal toward the sun using Rodrigues
    rotation with a maximum slew-rate constraint.

    When ``panel_ref`` is set to a ``simpleSolarPanel`` instance, this module
    updates the panel's normal parameters at every time step so that the native
    Basilisk module computes power with the tracked normal direction.

    Input Messages
    --------------
    stateInMsg : SCStatesMsgReader
        Spacecraft state (attitude + position).  Used together with *sunInMsg*
        to compute the sun direction in the body frame.
    sunInMsg : SpicePlanetStateMsgReader
        Sun planet state (position in inertial frame).
    eclipseInMsg : EclipseMsgReader
        Eclipse / shadow factor (0 = full eclipse, 1 = full sun).  Not used
        for tracking, but read for completeness / logging.

    Outputs
    -------
    Updates the referenced ``simpleSolarPanel`` normal at every time step.
    Current normal is also accessible via the ``current_normal()`` Python method.
    """

    def __init__(self, config: SolarPanelNativeConfig | None = None):
        _require_basilisk_messaging_sysmodel()
        super().__init__()
        self.ModelTag = "solarPanelTrackingSysModel"
        self.config = config or SolarPanelNativeConfig()

        self.stateInMsg = _messaging.SCStatesMsgReader()
        self.sunInMsg = _messaging.SpicePlanetStateMsgReader()
        self.eclipseInMsg = _messaging.EclipseMsgReader()

        self._normal_b = normalize_safe(self.config.initial_normal_b)
        self._last_update_ns: int = 0
        self._initialized: bool = False
        self._current_sun_b: Vector3 = tuple(
            float(x) for x in self.config.fallback_sun_b
        )
        self._current_shadow: float = float(self.config.fallback_shadow)
        self._panel_ref: Any = None
        self._panel_area_m2: float = 0.0
        self._panel_efficiency: float = 1.0

    def set_panel_reference(
        self, panel: Any, area_m2: float, efficiency: float
    ) -> None:
        """Set reference to a simpleSolarPanel instance for normal updates.

        When set, the tracking module calls ``setPanelParameters`` on the
        referenced panel at every time step with the updated normal.
        """
        self._panel_ref = panel
        self._panel_area_m2 = float(area_m2)
        self._panel_efficiency = float(efficiency)

    def current_normal(self) -> Vector3:
        """Return the current panel normal in body frame."""
        return self._normal_b

    def current_sun_b(self) -> Vector3:
        """Return the last computed sun direction in body frame."""
        return self._current_sun_b

    def current_shadow(self) -> float:
        """Return the last shadow factor read."""
        return self._current_shadow

    def _compute_sun_b(self) -> Vector3:
        """Compute sun direction in body frame from subscribed messages."""
        try:
            sun_state = self.sunInMsg()
            sc_state = self.stateInMsg()

            sun_pos_n = (
                float(sun_state.PositionVector[0]),
                float(sun_state.PositionVector[1]),
                float(sun_state.PositionVector[2]),
            )
            sc_pos_n = (
                float(sc_state.r_BN_N[0]),
                float(sc_state.r_BN_N[1]),
                float(sc_state.r_BN_N[2]),
            )
            sigma = (
                float(sc_state.sigma_BN[0]),
                float(sc_state.sigma_BN[1]),
                float(sc_state.sigma_BN[2]),
            )

            sun_rel_n = (
                sun_pos_n[0] - sc_pos_n[0],
                sun_pos_n[1] - sc_pos_n[1],
                sun_pos_n[2] - sc_pos_n[2],
            )

            sun_rel_n_mag = math.sqrt(sum(x * x for x in sun_rel_n))
            if sun_rel_n_mag < 1e-12:
                return tuple(float(x) for x in self.config.fallback_sun_b)

            dcm = _mrp_to_dcm(sigma)
            sun_b = _dcm_times_vec(dcm, sun_rel_n)
            return normalize_safe(sun_b)
        except Exception:
            return tuple(float(x) for x in self.config.fallback_sun_b)

    def _compute_shadow(self) -> float:
        """Read shadow/illumination factor from eclipse message, or use fallback."""
        try:
            eclipse = self.eclipseInMsg()
            if hasattr(eclipse, "illuminationFactor"):
                shadow = float(getattr(eclipse, "illuminationFactor", 1.0))
            else:
                shadow = float(getattr(eclipse, "shadowFactor", 1.0))
            return max(0.0, min(1.0, shadow))
        except Exception:
            return float(self.config.fallback_shadow)

    def _update_tracking(self, sun_b: Vector3, dt_s: float) -> None:
        if not self.config.enable_tracking:
            return
        if dt_s <= 0.0:
            return
        if self.config.max_slew_rate_rad_s <= 0.0:
            return
        max_angle = self.config.max_slew_rate_rad_s * dt_s
        self._normal_b = slew_normal_rodrigues(self._normal_b, sun_b, max_angle)

    def Reset(self, CurrentSimNanos: int) -> None:
        self._normal_b = normalize_safe(self.config.initial_normal_b)
        self._last_update_ns = int(CurrentSimNanos)
        self._initialized = True
        self._current_shadow = float(self.config.fallback_shadow)
        self._current_sun_b = tuple(float(x) for x in self.config.fallback_sun_b)
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        from Basilisk.utilities import macros

        t_ns = int(CurrentSimNanos)

        if self._initialized and t_ns > self._last_update_ns:
            dt_s = (t_ns - self._last_update_ns) * macros.NANO2SEC
        else:
            dt_s = 0.0

        self._last_update_ns = t_ns

        sun_b = self._compute_sun_b()
        shadow = self._compute_shadow()
        self._current_sun_b = sun_b
        self._current_shadow = shadow

        self._update_tracking(sun_b, dt_s)

        if self._panel_ref is not None and hasattr(
            self._panel_ref, "setPanelParameters"
        ):
            try:
                self._panel_ref.setPanelParameters(
                    list(self._normal_b),
                    self._panel_area_m2,
                    self._panel_efficiency,
                )
            except Exception as exc:
                record_runtime_diagnostic(
                    code='SOLAR_PANEL_NATIVE_PARAMETER_UPDATE_FAILED',
                    category=DiagnosticCategory.NATIVE_MAPPING_FAILURE,
                    location='src/components/solar_panel/builder.py:UpdateState:01',
                    exception=exc,
                    strict=None,
                )

        self._initialized = True


def build_simple_solar_panel(
    model_tag: str = "SolarPanel",
    panel_normal_b: Sequence[float] = (1.0, 0.0, 0.0),
    max_power_w: float = 100.0,
    efficiency: float = 1.0,
    *,
    panel_area_m2: float | None = None,
    solar_constant_w_m2: float = SOLAR_CONSTANT_W_M2,
    sun_in_msg: Any | None = None,
    state_in_msg: Any | None = None,
    eclipse_in_msg: Any | None = None,
    node_status_msg: Any | None = None,
):
    """Create and optionally wire a Basilisk ``SimpleSolarPanel``.

    ``sun_in_msg`` and ``state_in_msg`` must be Basilisk messages compatible
    with ``simpleSolarPanel.sunInMsg`` and ``simpleSolarPanel.stateInMsg``.
    ``eclipse_in_msg`` is optional.  This function does not implement tracking.
    """

    try:
        from Basilisk.simulation import simpleSolarPanel  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised only without Basilisk
        raise RuntimeError(
            f"Basilisk simpleSolarPanel is unavailable: {exc}"
        ) from exc

    panel = simpleSolarPanel.SimpleSolarPanel()
    panel.ModelTag = str(model_tag)
    area_m2 = (
        max(0.0, float(panel_area_m2))
        if panel_area_m2 is not None
        else max(0.0, float(max_power_w)) / max(float(solar_constant_w_m2), 1e-12)
    )
    panel.setPanelParameters(
        list(unit(panel_normal_b)), area_m2, max(0.0, float(efficiency))
    )

    if sun_in_msg is not None:
        panel.sunInMsg.subscribeTo(sun_in_msg)
    if state_in_msg is not None:
        panel.stateInMsg.subscribeTo(state_in_msg)
    if eclipse_in_msg is not None and hasattr(panel, "sunEclipseInMsg"):
        panel.sunEclipseInMsg.subscribeTo(eclipse_in_msg)
    if node_status_msg is not None and hasattr(panel, "nodeStatusInMsg"):
        panel.nodeStatusInMsg.subscribeTo(node_status_msg)
    return panel


def get_power_output_msg(panel_or_node: Any) -> Any:
    """Return a Basilisk power output message from a panel or power node."""

    for attr in ("nodePowerOutMsg", "powerOutMsg", "batPowerOutMsg"):
        if hasattr(panel_or_node, attr):
            return getattr(panel_or_node, attr)
    raise AttributeError(
        "object does not expose a recognized Basilisk power output message"
    )


def connect_power_output_to_battery(panel_or_node: Any, battery: Any) -> None:
    """Connect a solar-panel/power-node output to a Basilisk battery."""

    if not hasattr(battery, "addPowerNodeToModel"):
        raise AttributeError(
            "battery object does not provide addPowerNodeToModel"
        )
    battery.addPowerNodeToModel(get_power_output_msg(panel_or_node))


def build_solar_power_node(model_tag: str, source_power_w: float):
    """Create a deterministic Basilisk power source node.

    This legacy helper uses ``SimplePowerSink`` with positive ``nodePowerOut``.
    It is useful for weak-supportData battery wiring tests, but it is not a solar
    geometry model and does not consume sun/spacecraft/eclipse messages.
    """

    return build_simple_power_sink(model_tag, abs(float(source_power_w)))


def build_solar_panel_native_bundle(
    config: SolarPanelNativeConfig | None = None,
    *,
    sun_in_msg: Any | None = None,
    state_in_msg: Any | None = None,
    eclipse_in_msg: Any | None = None,
    create_deterministic_power_node: bool = True,
    add_to_task: Any | None = None,
    strict_native: bool = False,
) -> SolarPanelNativeBundle:
    """Build Basilisk solar-panel objects and explicit boundary metadata.

    If Basilisk ``simpleSolarPanel`` is available, this creates the native fixed
    panel object and wires any supplied messages.  When tracking is enabled, a
    ``SolarPanelTrackingSysModel`` is also created that updates the panel normal
    inside the simulation loop.

    A deterministic power source node can also be created for existing battery
    smoke tests or weak-supportData runs.

    Parameters
    ----------
    config : SolarPanelNativeConfig, optional
        Panel configuration.
    sun_in_msg : optional
        Sun position message to subscribe to (SpicePlanetStateMsg).
    state_in_msg : optional
        Spacecraft state message to subscribe to (SCStatesMsg).
    eclipse_in_msg : optional
        Eclipse/shadow message to subscribe to (EclipseMsg).
    create_deterministic_power_node : bool
        If True, also create a SimplePowerSink with initial power as fallback.
    add_to_task : optional
        If provided, call ``AddModelToTask`` on this object for created modules.
    strict_native : bool
        If True, raises RuntimeError if simpleSolarPanel cannot be created or
        wired. If False (default), falls back to SimplePowerSink when native
        module is unavailable.

    Returns
    -------
    SolarPanelNativeBundle
        Bundle with all created objects and capability flags.
    """

    cfg = config or SolarPanelNativeConfig()
    sample_state, sample = step_solar_panel_native_compatible(
        SolarPanelNativeState(cfg.initial_normal_b), cfg, dt_s=0.0, time_s=0.0
    )
    del sample_state

    panel = None
    native_available = False
    note_parts: list[str] = []
    try:
        panel = build_simple_solar_panel(
            cfg.model_tag,
            sample.normal_b,
            cfg.max_power_w,
            cfg.efficiency,
            panel_area_m2=cfg.panel_area_m2,
            solar_constant_w_m2=cfg.solar_constant_w_m2,
            sun_in_msg=sun_in_msg,
            state_in_msg=state_in_msg,
            eclipse_in_msg=eclipse_in_msg,
        )
        native_available = True
        note_parts.append("Basilisk simpleSolarPanel created")
        if add_to_task is not None:
            if hasattr(add_to_task, "AddModelToTask"):
                add_to_task.AddModelToTask(str(cfg.model_tag), panel)
            elif hasattr(add_to_task, "addModelToTask"):
                add_to_task.addModelToTask(str(cfg.model_tag), panel)
    except Exception as exc:
        if strict_native:
            raise RuntimeError(
                f"simpleSolarPanel creation failed in strict_native mode: {exc}"
            ) from exc
        note_parts.append(
            f"Basilisk simpleSolarPanel unavailable or not wired: {exc}"
        )

    tracking_model: SolarPanelTrackingSysModel | None = None
    if cfg.enable_tracking and cfg.max_slew_rate_rad_s > 0.0:
        tracking_config = SolarPanelNativeConfig(
            model_tag=f"{cfg.model_tag}_Tracking",
            initial_normal_b=cfg.initial_normal_b,
            max_power_w=cfg.max_power_w,
            panel_area_m2=cfg.panel_area_m2,
            efficiency=cfg.efficiency,
            shadow_factor=cfg.shadow_factor,
            sun_direction_b=cfg.sun_direction_b,
            enable_tracking=True,
            max_slew_rate_rad_s=cfg.max_slew_rate_rad_s,
            solar_constant_w_m2=cfg.solar_constant_w_m2,
            fallback_sun_b=cfg.fallback_sun_b,
            fallback_shadow=cfg.fallback_shadow,
        )
        tracking_model = SolarPanelTrackingSysModel(tracking_config)
        tracking_model.ModelTag = f"{cfg.model_tag}_Tracking"

        if sun_in_msg is not None:
            tracking_model.sunInMsg.subscribeTo(sun_in_msg)
        if state_in_msg is not None:
            tracking_model.stateInMsg.subscribeTo(state_in_msg)
        if eclipse_in_msg is not None:
            tracking_model.eclipseInMsg.subscribeTo(eclipse_in_msg)

        if add_to_task is not None:
            tracking_tag = f"{cfg.model_tag}_Tracking"
            if hasattr(add_to_task, "AddModelToTask"):
                add_to_task.AddModelToTask(tracking_tag, tracking_model)
            elif hasattr(add_to_task, "addModelToTask"):
                add_to_task.addModelToTask(tracking_tag, tracking_model)

        if panel is not None:
            area_m2 = cfg.equivalent_panel_area_m2
            tracking_model.set_panel_reference(panel, area_m2, cfg.efficiency)
            note_parts.append("tracking wired to simpleSolarPanel")
        else:
            note_parts.append("platform tracking sysModel created")

    deterministic = None
    if create_deterministic_power_node:
        deterministic = build_solar_power_node(
            f"{cfg.model_tag}_DeterministicPowerNode", sample.power_w
        )
        note_parts.append("deterministic SimplePowerSink power node created")

    power_msg = None
    if panel is not None:
        try:
            power_msg = get_power_output_msg(panel)
        except Exception:
            power_msg = None
    if power_msg is None and deterministic is not None:
        try:
            power_msg = get_power_output_msg(deterministic)
        except Exception:
            power_msg = None

    boundary = (
        "platform_tracking_rodrigues_not_basilisk_native"
        if cfg.enable_tracking
        else "basilisk_fixed_panel"
    )
    return SolarPanelNativeBundle(
        config=cfg,
        simple_solar_panel=panel,
        tracking_sys_model=tracking_model,
        deterministic_power_node=deterministic,
        power_output_msg=power_msg,
        native_simple_solar_panel_available=native_available,
        tracking_boundary=boundary,
        note="; ".join(note_parts),
    )


def build_solar_panel(
    model_tag: str,
    max_power_w: float,
    *,
    efficiency: float = 1.0,
    normal_b: Sequence[float] = (1.0, 0.0, 0.0),
    tracking_enabled: bool = False,
    max_slew_rate_rad_s: float = 0.1,
    fallback_sun_b: Sequence[float] = (1.0, 0.0, 0.0),
    fallback_shadow: float = 1.0,
    sun_in_msg: Any | None = None,
    state_in_msg: Any | None = None,
    eclipse_in_msg: Any | None = None,
    add_to_task: Any | None = None,
) -> SolarPanelNativeBundle:
    """Create a solar-panel bundle with a flat-parameter convenience API.

    This is a thin wrapper around :func:`build_solar_panel_native_bundle` that
    accepts individual parameters instead of a config object.

    Parameters
    ----------
    model_tag : str
        Model name tag.
    max_power_w : float
        Maximum power at normal incidence, in Watts.
    efficiency : float
        Panel efficiency (0–1).
    normal_b : sequence of float
        Initial normal vector in body frame.
    tracking_enabled : bool
        Enable sun-tracking slew via platform sysModel.
    max_slew_rate_rad_s : float
        Maximum slew rate for tracking, in rad/s.
    fallback_sun_b : sequence of float
        Fallback sun direction in body frame.
    fallback_shadow : float
        Fallback shadow factor.
    sun_in_msg : optional
        Sun position message to subscribe to.
    state_in_msg : optional
        Spacecraft state message to subscribe to.
    eclipse_in_msg : optional
        Eclipse message to subscribe to.
    add_to_task : optional
        Task to add created modules to.

    Returns
    -------
    SolarPanelNativeBundle
        Bundle with all created objects.
    """

    config = SolarPanelNativeConfig(
        model_tag=str(model_tag),
        initial_normal_b=normalize_safe(normal_b),
        max_power_w=float(max_power_w),
        efficiency=float(efficiency),
        enable_tracking=bool(tracking_enabled),
        max_slew_rate_rad_s=float(max_slew_rate_rad_s),
        fallback_sun_b=normalize_safe(fallback_sun_b),
        fallback_shadow=clamp01(fallback_shadow),
    )
    return build_solar_panel_native_bundle(
        config,
        sun_in_msg=sun_in_msg,
        state_in_msg=state_in_msg,
        eclipse_in_msg=eclipse_in_msg,
        create_deterministic_power_node=False,
        add_to_task=add_to_task,
    )

# Component fault/degradation compatibility wrappers
from .degradation import SolarPanelDegradationRate
from .degradation import compute_degradation_state
from .faults import apply_solar_panel_faults
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_solar_panel_config_base = _build_nominal_solar_panel_config_base_impl

def build_nominal_solar_panel_config(
    *args,
    degradation: SolarPanelDegradation | None = None,
    degradation_rate: SolarPanelDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_solar_panel_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_solar_panel_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_solar_panel_faults(config, fault_specs)
    return config

