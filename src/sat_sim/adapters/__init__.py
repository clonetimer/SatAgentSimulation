"""Explicit capability adapters."""

from .component_battery import BatteryAdapter
from .component_reaction_wheel import ReactionWheelAdapter
from .component_solar_panel import SolarPanelAdapter
from .orbit_environment_leo_simple import OrbitEnvironmentLeoSimpleAdapter
from .orbit_environment_orbit_fidelity import OrbitEnvironmentOrbitFidelityAdapter
from .orbit_environment_basilisk_hf import OrbitEnvironmentBasiliskHfAdapter
from .subsystem_eps_basic import EpsBasicAdapter
from .subsystem_adcs_basic_rw_pointing import AdcsBasicRwPointingAdapter
from .subsystem_adcs_closed_loop_basic import AdcsClosedLoopBasicAdapter
from .subsystem_adcs_fidelity import AdcsFidelityAdapter
from .subsystem_adcs_bsksim import AdcsBSKSimAdapter
from .subsystem_adcs_basilisk_fsw import AdcsBasiliskFswAdapter
from .subsystem_thermal_basic_lumped import ThermalBasicLumpedAdapter
from .subsystem_comm_basic_ground_pass import CommBasicGroundPassAdapter
from .whole_spacecraft_basic_power_orbit import WholeSpacecraftBasicPowerOrbitAdapter
from .whole_spacecraft_basic_power_attitude_orbit import WholeSpacecraftBasicPowerAttitudeOrbitAdapter
from .whole_spacecraft_basic_power_thermal_orbit import WholeSpacecraftBasicPowerThermalOrbitAdapter
from .whole_spacecraft_power_thermal_orbit_coupled import PowerThermalOrbitCoupledAdapter
from .whole_spacecraft_comm_payload_mission_coupled import CommPayloadMissionCoupledAdapter
from .whole_spacecraft_maneuver_orbit_attitude import ManeuverOrbitAttitudeAdapter
from .whole_spacecraft_orbit_adcs_fidelity import OrbitAdcsFidelityAdapter
from .whole_spacecraft_orbit_attitude_thermal import WholeSpacecraftOrbitAttitudeThermalAdapter
from .whole_spacecraft_bsksim_coupled import WholeSpacecraftBSKSimCoupledAdapter

__all__ = [
    "BatteryAdapter",
    "ReactionWheelAdapter",
    "SolarPanelAdapter",
    "OrbitEnvironmentLeoSimpleAdapter",
    "OrbitEnvironmentOrbitFidelityAdapter",
    "OrbitEnvironmentBasiliskHfAdapter",
    "EpsBasicAdapter",
    "AdcsBasicRwPointingAdapter",
    "AdcsClosedLoopBasicAdapter",
    "AdcsFidelityAdapter",
    "AdcsBSKSimAdapter",
    "AdcsBasiliskFswAdapter",
    "ThermalBasicLumpedAdapter",
    "CommBasicGroundPassAdapter",
    "WholeSpacecraftBasicPowerOrbitAdapter",
    "WholeSpacecraftBasicPowerAttitudeOrbitAdapter",
    "WholeSpacecraftBasicPowerThermalOrbitAdapter",
    "PowerThermalOrbitCoupledAdapter",
    "CommPayloadMissionCoupledAdapter",
    "ManeuverOrbitAttitudeAdapter",
    "OrbitAdcsFidelityAdapter",
    "WholeSpacecraftOrbitAttitudeThermalAdapter",
    "WholeSpacecraftBSKSimCoupledAdapter",
]
