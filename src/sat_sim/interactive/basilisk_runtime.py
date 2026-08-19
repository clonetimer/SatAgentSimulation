"""Persistent Basilisk foundation runtime used for segmented feasibility proof."""
from __future__ import annotations

import math
from typing import Any

from sat_sim.bsk_engine.event_manager import BSKEventManager
from sat_sim.bsk_engine.types import BSKScenarioConfig


def _vector_norm(values: Any) -> float:
    try:
        return math.sqrt(sum(float(v) ** 2 for v in list(values)[:3]))
    except Exception:
        return 0.0


class FoundationBasiliskRuntime:
    """Own one Basilisk context and advance it through absolute stop times."""

    def __init__(self, config: BSKScenarioConfig) -> None:
        self.config = config
        self._event_manager = BSKEventManager(config.events)
        self._sim: Any = None
        self._macros: Any = None
        self._recorders: tuple[Any, Any, Any] | None = None
        self._current_time_s = 0.0
        self._emitted_count = 0
        self._pending_delta: tuple[dict[str, Any], ...] = ()
        self._prepared = False
        self._closed = False

    @property
    def current_time_s(self) -> float:
        return self._current_time_s

    def prepare(self) -> None:
        if self._prepared or self._closed:
            raise RuntimeError("runtime cannot be prepared twice or after close")
        from Basilisk.architecture import messaging  # type: ignore
        from Basilisk.fswAlgorithms import attTrackingError, inertial3D, mrpFeedback, rwMotorTorque  # type: ignore
        from Basilisk.simulation import reactionWheelStateEffector, simpleNav, spacecraft  # type: ignore
        from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody, simIncludeRW  # type: ignore

        step_s = max(float(self.config.step_s), 1e-6)
        sample_s = max(float(self.config.sample_s), step_s)
        params = self.config.parameters
        initial_error_deg = float(params.get("initial_pointing_error_deg", 5.0) or 5.0)
        orbit_rate = max(float(params.get("orbit_rate_rad_s", 0.0011) or 0.0011), 1e-8)
        earth_mu = 3.986004418e14
        orbit_radius_m = max(float(params.get("orbit_radius_m", (earth_mu / orbit_rate**2) ** (1.0 / 3.0)) or 7_000_000.0), 6_478_000.0)

        sim = SimulationBaseClass.SimBaseClass()
        dyn_process = sim.CreateNewProcess("DynamicsProcess")
        dyn_process.addTask(sim.CreateNewTask("DynamicsTask", macros.sec2nano(step_s)))
        fsw_process = sim.CreateNewProcess("FswProcess")
        fsw_process.addTask(sim.CreateNewTask("FswTask", macros.sec2nano(step_s)))

        sc = spacecraft.Spacecraft()
        sc.ModelTag = "spacecraft"
        sc.hub.mHub = 100.0
        sc.hub.IHubPntBc_B = [[100.0, 0.0, 0.0], [0.0, 80.0, 0.0], [0.0, 0.0, 60.0]]
        grav_factory = simIncludeGravBody.gravBodyFactory()
        earth = grav_factory.createEarth()
        earth.isCentralBody = True
        grav_factory.addBodiesTo(sc)
        oe = orbitalMotion.ClassicElements()
        oe.a = orbit_radius_m
        oe.e = 0.001
        oe.i = math.radians(float(params.get("inclination_deg", 5.0) or 5.0))
        oe.Omega = 0.0
        oe.omega = 0.0
        oe.f = float(params.get("initial_true_anomaly_rad", 0.0) or 0.0)
        r_n, v_n = orbitalMotion.elem2rv(earth.mu, oe)
        sc.hub.r_CN_NInit = r_n
        sc.hub.v_CN_NInit = v_n
        sigma0 = math.tan(math.radians(initial_error_deg) / 4.0)
        sc.hub.sigma_BNInit = [[sigma0], [0.0], [0.0]]
        sc.hub.omega_BN_BInit = [[0.01], [-0.004], [0.002]]
        sim.AddModelToTask("DynamicsTask", sc, 1)

        rw_effector = reactionWheelStateEffector.ReactionWheelStateEffector()
        rw_effector.ModelTag = "reaction_wheels"
        rw_factory = simIncludeRW.rwFactory()
        for idx, axis in enumerate(([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])):
            rw_factory.create("Honeywell_HR16", list(axis), maxMomentum=50.0, Omega=float(100.0 * (idx + 1)), u_max=0.2, label=f"RW{idx + 1}")
        rw_factory.addToSpacecraft("reaction_wheels", rw_effector, sc)
        sim.AddModelToTask("DynamicsTask", rw_effector, 2)
        rw_config_msg = rw_factory.getConfigMessage()

        nav = simpleNav.SimpleNav()
        nav.ModelTag = "simple_nav"
        nav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
        sim.AddModelToTask("DynamicsTask", nav)
        reference = inertial3D.inertial3D()
        reference.ModelTag = "inertial_guidance"
        reference.sigma_R0N = [0.0, 0.0, 0.0]
        sim.AddModelToTask("FswTask", reference)
        tracking = attTrackingError.attTrackingError()
        tracking.ModelTag = "attitude_error"
        tracking.attRefInMsg.subscribeTo(reference.attRefOutMsg)
        tracking.attNavInMsg.subscribeTo(nav.attOutMsg)
        sim.AddModelToTask("FswTask", tracking)
        controller = mrpFeedback.mrpFeedback()
        controller.ModelTag = "mrp_feedback"
        controller.K, controller.P, controller.Ki, controller.integralLimit = 3.5, 30.0, -1.0, -1.0
        controller.guidInMsg.subscribeTo(tracking.attGuidOutMsg)
        vehicle = messaging.VehicleConfigMsgPayload()
        vehicle.ISCPntB_B = [100.0, 0.0, 0.0, 0.0, 80.0, 0.0, 0.0, 0.0, 60.0]
        vehicle.massSC = 100.0
        vehicle_msg = messaging.VehicleConfigMsg().write(vehicle)
        controller.vehConfigInMsg.subscribeTo(vehicle_msg)
        controller.rwParamsInMsg.subscribeTo(rw_config_msg)
        controller.rwSpeedsInMsg.subscribeTo(rw_effector.rwSpeedOutMsg)
        sim.AddModelToTask("FswTask", controller)
        motor_torque = rwMotorTorque.rwMotorTorque()
        motor_torque.ModelTag = "rw_motor_torque"
        motor_torque.controlAxes_B = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        motor_torque.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
        motor_torque.rwParamsInMsg.subscribeTo(rw_config_msg)
        sim.AddModelToTask("FswTask", motor_torque)
        rw_effector.rwMotorCmdInMsg.subscribeTo(motor_torque.rwMotorTorqueOutMsg)

        sample_ns = macros.sec2nano(sample_s)
        state_rec = sc.scStateOutMsg.recorder(sample_ns)
        error_rec = tracking.attGuidOutMsg.recorder(sample_ns)
        rw_rec = rw_effector.rwSpeedOutMsg.recorder(sample_ns)
        sim.AddModelToTask("DynamicsTask", state_rec)
        sim.AddModelToTask("FswTask", error_rec)
        sim.AddModelToTask("DynamicsTask", rw_rec)
        sim.InitializeSimulation()
        self._sim, self._macros = sim, macros
        self._recorders = state_rec, error_rec, rw_rec
        self._prepared = True

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        if not self._prepared or self._closed or self._recorders is None:
            raise RuntimeError("runtime is not active")
        stop_time_s = float(stop_time_s)
        if stop_time_s <= self._current_time_s:
            raise ValueError("stop time must increase monotonically")
        if stop_time_s > float(self.config.duration_s) + 1e-12:
            raise ValueError("stop time exceeds configured duration")
        self._sim.ConfigureStopTime(self._macros.sec2nano(stop_time_s))
        self._sim.ExecuteSimulation()
        self._current_time_s = stop_time_s
        self._pending_delta = self._extract_delta()
        return self._pending_delta

    def _extract_delta(self) -> tuple[dict[str, Any], ...]:
        state_rec, error_rec, rw_rec = self._recorders or (None, None, None)
        times = state_rec.times() * self._macros.NANO2SEC
        rows: list[dict[str, Any]] = []
        for idx in range(self._emitted_count, len(times)):
            t_s = float(times[idx])
            position = list(state_rec.r_BN_N[idx])[:3]
            sigma_br = list(error_rec.sigma_BR[idx])[:3]
            sigma_norm = _vector_norm(sigma_br)
            rows.append({
                "time_s": t_s,
                "orbit.theta_rad": math.atan2(float(position[1]), float(position[0])),
                "orbit.radius_m": _vector_norm(position),
                "attitude.pointing_error_deg": 4.0 * math.degrees(math.atan(sigma_norm)),
                "attitude.sigma_br_norm": sigma_norm,
                "adcs.rw.speed_rad_s_0": float(rw_rec.wheelSpeeds[idx][0]),
                "adcs.rw.speed_rad_s_1": float(rw_rec.wheelSpeeds[idx][1]),
                "adcs.rw.speed_rad_s_2": float(rw_rec.wheelSpeeds[idx][2]),
                "fsw.mode": self.config.mode_request,
                **self._event_manager.active_labels(t_s),
            })
        self._emitted_count = len(times)
        return tuple(rows)

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        delta, self._pending_delta = self._pending_delta, ()
        return delta

    def apply_command(self, command: Any) -> None:
        raise NotImplementedError("foundation feasibility runtime has no command adapter")

    def finalize(self) -> dict[str, Any]:
        if not self._prepared:
            raise RuntimeError("runtime was not prepared")
        metadata = {
            "backend_type": "basilisk_native",
            "basilisk_available": True,
            "executed": self._current_time_s > 0.0,
            "persistent_instance": True,
            "final_sim_time_s": self._current_time_s,
            "instantiated_model_tags": ("spacecraft", "reaction_wheels", "simple_nav", "inertial_guidance", "attitude_error", "mrp_feedback", "rw_motor_torque"),
            "environment_objects": ["earth_gravity"],
            "connected_message_count": 9,
            "recorder_count": 3,
            "recorded_sources": ["spacecraft.scStateOutMsg", "attTrackingError.attGuidOutMsg", "reactionWheelStateEffector.rwSpeedOutMsg"],
        }
        self._closed = True
        self._sim = None
        self._recorders = None
        return metadata

    def abort(self) -> None:
        self._closed = True
        self._sim = None
        self._recorders = None
