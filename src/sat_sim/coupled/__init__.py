"""Route-B coupled spacecraft model library."""

from .power_thermal_orbit import (
    HF5_POWER_THERMAL_ORBIT_SCHEMA_VERSION,
    PowerThermalOrbitCoupledConfig,
    PowerThermalOrbitCoupledError,
    build_hf5_power_thermal_orbit_payload,
    propagate_power_thermal_orbit_coupled,
    summarize_power_thermal_orbit_coupled,
)
from .comm_payload_mission import (
    HF6_COMM_PAYLOAD_MISSION_SCHEMA_VERSION,
    CommPayloadMissionCoupledConfig,
    CommPayloadMissionCoupledError,
    build_hf6_comm_payload_mission_payload,
    propagate_comm_payload_mission_coupled,
    summarize_comm_payload_mission_coupled,
)

from .orbit_adcs_fidelity import (
    INT1_ORBIT_ADCS_SCHEMA_VERSION,
    OrbitAdcsIntegrationConfig,
    OrbitAdcsIntegrationError,
    build_int1_orbit_adcs_payload,
    propagate_orbit_adcs_fidelity,
    summarize_orbit_adcs_fidelity,
)

from .propulsion_orbit_attitude import (
    HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION,
    PropulsionOrbitAttitudeCoupledConfig,
    PropulsionOrbitAttitudeCoupledError,
    build_hf7_propulsion_orbit_attitude_payload,
    build_hf7_readiness_matrix,
    propagate_propulsion_orbit_attitude_coupled,
    summarize_propulsion_orbit_attitude_coupled,
)

from .orbit_attitude_thermal import (
    ORBIT_ATTITUDE_THERMAL_SCHEMA_VERSION,
    exposure_maps_for_attitude_mode,
    build_orbit_attitude_thermal_environment,
)

__all__ = [
    "ORBIT_ATTITUDE_THERMAL_SCHEMA_VERSION",
    "exposure_maps_for_attitude_mode",
    "build_orbit_attitude_thermal_environment",
    "summarize_orbit_adcs_fidelity",
    "propagate_orbit_adcs_fidelity",
    "build_int1_orbit_adcs_payload",
    "OrbitAdcsIntegrationError",
    "OrbitAdcsIntegrationConfig",
    "INT1_ORBIT_ADCS_SCHEMA_VERSION",
    "HF5_POWER_THERMAL_ORBIT_SCHEMA_VERSION",
    "PowerThermalOrbitCoupledConfig",
    "PowerThermalOrbitCoupledError",
    "build_hf5_power_thermal_orbit_payload",
    "propagate_power_thermal_orbit_coupled",
    "summarize_power_thermal_orbit_coupled",
    "HF6_COMM_PAYLOAD_MISSION_SCHEMA_VERSION",
    "CommPayloadMissionCoupledConfig",
    "CommPayloadMissionCoupledError",
    "build_hf6_comm_payload_mission_payload",
    "propagate_comm_payload_mission_coupled",
    "summarize_comm_payload_mission_coupled",
    "HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION",
    "PropulsionOrbitAttitudeCoupledConfig",
    "PropulsionOrbitAttitudeCoupledError",
    "build_hf7_propulsion_orbit_attitude_payload",
    "build_hf7_readiness_matrix",
    "propagate_propulsion_orbit_attitude_coupled",
    "summarize_propulsion_orbit_attitude_coupled",
]
