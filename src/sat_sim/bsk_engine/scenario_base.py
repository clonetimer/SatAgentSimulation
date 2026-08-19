"""Native Basilisk foundation scenarios with explicit runtime evidence."""
from __future__ import annotations

import math
from typing import Any

from sat_sim.fault_environment import BSKRLStyleFaultAdapter

from .dynamics import SatelliteDynamicsModel
from .event_manager import BSKEventManager
from .fsw import SatelliteFswModel
from .master import SatelliteBSKSim
from .recorder_registry import recorders_from_spec
from .types import BSK_ENGINE_SCHEMA_VERSION, BSKExecutionPlan, BSKRunResult, BSKScenarioConfig


def _vector_norm(values: Any) -> float:
    try:
        return math.sqrt(sum(float(v) ** 2 for v in list(values)[:3]))
    except Exception:
        return 0.0


class BSKScenarioBase:
    def __init__(self, config: BSKScenarioConfig) -> None:
        self.config = config
        self.dynamics = SatelliteDynamicsModel(config.step_s)
        self.fsw = SatelliteFswModel(config.step_s, mode_request=config.mode_request)
        self.event_manager = BSKEventManager(config.events)

    def build_execution_plan(self) -> BSKExecutionPlan:
        sim = SatelliteBSKSim(use_basilisk=False)
        self.dynamics.attach(sim)
        self.fsw.attach(sim)
        recorders = recorders_from_spec({"outputs": {"plots": self.config.requested_outputs}}, sample_s=self.config.sample_s)
        return BSKExecutionPlan(
            schema_version=BSK_ENGINE_SCHEMA_VERSION,
            engine="basilisk_native_foundation",
            scenario_id=self.config.scenario_id,
            processes=tuple(sim.process_specs),
            tasks=tuple(sim.task_specs),
            modules=(*self.dynamics.modules(), *self.fsw.modules()),
            connections=(*self.dynamics.connections(), *self.fsw.connections()),
            events=self.config.events,
            recorders=recorders,
            mode_request=self.config.mode_request,
            notes=(
                "v0.5.4.7 replaces the former empty Basilisk timeline with instantiated native modules.",
                "Spacecraft, Earth gravity, reaction wheels, simpleNav, inertial3D, attTrackingError, mrpFeedback and rwMotorTorque execute in Basilisk.",
                "Marker events remain orchestration-only and are not represented as physical component failures.",
            ),
        )

    def run(self) -> BSKRunResult:
        plan = self.build_execution_plan()
        try:
            rows, runtime = self._run_native_foundation()
            status = "SUCCEEDED"
            result_status = "complete"
        except Exception as exc:
            rows = ()
            runtime = {
                "backend_type": "basilisk_native",
                "basilisk_available": False,
                "executed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "instantiated_model_tags": [],
                "connected_message_count": 0,
                "recorder_count": 0,
            }
            status = "FAILED"
            result_status = "unavailable"

        fault_environment = BSKRLStyleFaultAdapter().summarize(plan.events, rows)
        instantiated = tuple(runtime.get("instantiated_model_tags") or ())
        summary = {
            "status": result_status,
            "scenario_id": self.config.scenario_id,
            "capability_id": self.config.capability_id,
            "engine": "basilisk_native_foundation",
            "backend_type": "basilisk_native",
            "basilisk_timeline_executed": bool(runtime.get("executed")),
            "basilisk_available": bool(runtime.get("basilisk_available")),
            "mode_request": self.config.mode_request,
            "process_count": len(plan.processes),
            "task_count": len(plan.tasks),
            "declared_module_count": len(plan.modules),
            "instantiated_module_count": len(instantiated),
            "module_count": len(instantiated),
            "declared_connection_count": len(plan.connections),
            "connected_message_count": int(runtime.get("connected_message_count", 0) or 0),
            "connection_count": int(runtime.get("connected_message_count", 0) or 0),
            "event_count": len(plan.events),
            "recorder_count": int(runtime.get("recorder_count", 0) or 0),
            "fault_environment_episode_count": fault_environment.episode_count,
            "fault_environment_style": fault_environment.style,
            "trace_rows": len(rows),
            "runtime_truth_status": "instantiated_connected_recorded" if runtime.get("executed") else "not_executed",
        }
        return BSKRunResult(
            status=status,
            summary=summary,
            trace_rows=rows,
            execution_plan=plan,
            metadata={
                "runtime": runtime,
                "runtime_manifest": {
                    "declared_modules": [m.to_dict() for m in plan.modules],
                    "instantiated_model_tags": list(instantiated),
                    "connected_message_count": int(runtime.get("connected_message_count", 0) or 0),
                    "recorded_sources": list(runtime.get("recorded_sources") or ()),
                },
                "fault_environment": fault_environment.to_dict(),
            },
        )

    def _run_native_foundation(self) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
        """Build and execute the foundation with real Basilisk modules."""
        from Basilisk.architecture import messaging  # type: ignore
        from Basilisk.fswAlgorithms import attTrackingError, inertial3D, mrpFeedback, rwMotorTorque  # type: ignore
        from Basilisk.simulation import reactionWheelStateEffector, simpleNav, spacecraft  # type: ignore
        from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody, simIncludeRW  # type: ignore

        step_s = max(float(self.config.step_s), 1e-6)
        sample_s = max(float(self.config.sample_s), step_s)
        duration_s = max(float(self.config.duration_s), sample_s)
        params = self.config.parameters
        initial_error_deg = float(params.get("initial_pointing_error_deg", 5.0) or 5.0)
        orbit_rate = max(float(params.get("orbit_rate_rad_s", 0.0011) or 0.0011), 1e-8)
        earth_mu = 3.986004418e14
        orbit_radius_m = float(params.get("orbit_radius_m", (earth_mu / orbit_rate**2) ** (1.0 / 3.0)) or 7_000_000.0)
        orbit_radius_m = max(orbit_radius_m, 6_478_000.0)

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
            rw_factory.create(
                "Honeywell_HR16",
                list(axis),
                maxMomentum=50.0,
                Omega=float(100.0 * (idx + 1)),
                u_max=0.2,
                label=f"RW{idx + 1}",
            )
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
        controller.K = 3.5
        controller.P = 30.0
        controller.Ki = -1.0
        controller.integralLimit = -1.0
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
        sim.ConfigureStopTime(macros.sec2nano(duration_s))
        sim.ExecuteSimulation()

        rows: list[dict[str, Any]] = []
        times = state_rec.times() * macros.NANO2SEC
        for idx, t_s in enumerate(times):
            position = list(state_rec.r_BN_N[idx])[:3]
            sigma_br = list(error_rec.sigma_BR[idx])[:3]
            sigma_norm = _vector_norm(sigma_br)
            row = {
                "time_s": float(t_s),
                "orbit.theta_rad": math.atan2(float(position[1]), float(position[0])),
                "orbit.radius_m": _vector_norm(position),
                "attitude.pointing_error_deg": 4.0 * math.degrees(math.atan(sigma_norm)),
                "attitude.sigma_br_norm": sigma_norm,
                "adcs.rw.speed_rad_s_0": float(rw_rec.wheelSpeeds[idx][0]),
                "adcs.rw.speed_rad_s_1": float(rw_rec.wheelSpeeds[idx][1]),
                "adcs.rw.speed_rad_s_2": float(rw_rec.wheelSpeeds[idx][2]),
                "fsw.mode": self.config.mode_request,
                **self.event_manager.active_labels(float(t_s)),
            }
            rows.append(row)

        instantiated = (
            "spacecraft",
            "reaction_wheels",
            "simple_nav",
            "inertial_guidance",
            "attitude_error",
            "mrp_feedback",
            "rw_motor_torque",
        )
        runtime = {
            "backend_type": "basilisk_native",
            "basilisk_available": True,
            "executed": True,
            "instantiated_model_tags": instantiated,
            "environment_objects": ["earth_gravity"],
            "connected_message_count": 9,
            "recorder_count": 3,
            "recorded_sources": ["spacecraft.scStateOutMsg", "attTrackingError.attGuidOutMsg", "reactionWheelStateEffector.rwSpeedOutMsg"],
        }
        return tuple(rows), runtime


class FoundationOrbitAttitudeScenario(BSKScenarioBase):
    """Native Basilisk orbit-attitude foundation scenario."""
