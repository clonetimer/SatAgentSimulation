"""Native-message radiative boundary for the generic LEO lumped network.

Areas are effective exposed areas, not a CAD model. Body nodes use six equal
orthogonal faces; the wing uses its actual EPS front normal and two sides.
The network integrates this boundary with the same explicit one-step coupling
as its conduction and radiation terms. No replacement temperatures are used.
"""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np
from Basilisk.architecture import messaging, astroConstants
from Basilisk.utilities import RigidBodyKinematics

from sat_sim.thermal_environment import surface_irradiance


class NativeThermalEnvironment:
    def __init__(self, *, spacecraft_msg, sun_msg, earth_msg, eclipse_msg, panel,
                 earth_ir_exitance_w_m2=234., earth_albedo=.30,
                 panel_front_absorptivity=.90, panel_back_absorptivity=.35,
                 radial_order=16, azimuth_order=64):
        self.state = messaging.SCStatesMsgReader()
        self.sun = messaging.SpicePlanetStateMsgReader()
        self.earth = messaging.SpicePlanetStateMsgReader()
        self.eclipse = messaging.EclipseMsgReader()
        self.power = messaging.PowerNodeUsageMsgReader()
        for reader, msg in ((self.state, spacecraft_msg), (self.sun, sun_msg),
                            (self.earth, earth_msg), (self.eclipse, eclipse_msg),
                            (self.power, panel.nodePowerOutMsg)):
            reader.subscribeTo(msg)
        self.panel = panel
        self.earth_ir = float(earth_ir_exitance_w_m2)
        self.albedo = float(earth_albedo)
        self.front_alpha = float(panel_front_absorptivity)
        self.back_alpha = float(panel_back_absorptivity)
        for name, value, upper in (('EARTH_IR', self.earth_ir, 1000.),
                                   ('ALBEDO', self.albedo, 1.),
                                   ('FRONT_ALPHA', self.front_alpha, 1.),
                                   ('BACK_ALPHA', self.back_alpha, 1.)):
            if not math.isfinite(value) or not 0. <= value <= upper:
                raise ValueError(f'NATIVE_THERMAL_ENV_{name}_INVALID')
        if float(panel.panelEfficiency) > self.front_alpha:
            raise ValueError('NATIVE_THERMAL_ENV_EFFICIENCY_EXCEEDS_ABSORPTIVITY')
        from sat_sim.thermal_quadrature import prepare_earth_disk_kernel
        prepare_earth_disk_kernel()
        self.radial_order, self.azimuth_order = radial_order, azimuth_order
        self.last_heat = {}

    def evidence(self):
        from importlib.metadata import version
        return {
            'model': 'finite_spherical_earth_lambertian_v1',
            'earth_ir_exitance_w_m2': self.earth_ir, 'earth_albedo': self.albedo,
            'solar_irradiance_at_au_w_m2': float(astroConstants.SOLAR_FLUX_EARTH),
            'radial_order': self.radial_order, 'azimuth_order': self.azimuth_order,
            'quadrature_backend': 'numba_nopython_float64_no_fastmath_no_parallel',
            'quadrature_runtime': {name:version(name) for name in ('numpy','numba','llvmlite')},
            'geometry': 'body_nodes_six_equal_faces_wing_actual_eps_normal_two_sides',
            'panel_front_solar_absorptivity': self.front_alpha,
            'panel_back_solar_absorptivity': self.back_alpha,
            'panel_emissivity': 'thermal_node_emissivity_both_sides',
            'panel_area': 'EPS_panelArea_each_side',
            'electrical_energy': 'actual_EPS_output_subtracted_from_front_absorbed_solar',
            'radiator_geometry': 'six_equal_faces_effective_area_times_view_factor_when_exposed',
            'radiator_optics': 'node_shortwave_absorptivity_and_radiator_IR_emissivity',
            'coupling': 'explicit_previous_tick_heat_same_as_network_conduction',
            'assumptions': [
                'Uniform global IR and albedo; no local weather, land or cloud maps.',
                'Effective exposed surfaces; no CAD occlusion, cavities or mutual radiation.',
                'Wing optical values are declared engineering assumptions, not measured hardware data.',
                '234 W/m2 is a global annual mean, not an instantaneous hot/cold bound.',
                'Radiator view_factor is treated as an effective exposed-area multiplier.',
            ],
            'references': ['https://llis.nasa.gov/lesson/693',
                           'https://www.nasa.gov/sites/default/files/atoms/files/std8070.1.pdf'],
        }

    def apply(self, network, current_sim_nanos):
        for reader in (self.state, self.sun, self.earth, self.eclipse, self.power):
            if not reader.isLinked() or not reader.isWritten():
                if int(current_sim_nanos) == 0:
                    return
                raise RuntimeError('NATIVE_THERMAL_ENV_MESSAGE_NOT_READY')
        state, sun, earth = self.state(), self.sun(), self.earth()
        rotation = np.asarray(RigidBodyKinematics.MRP2C(state.sigma_BN)).T
        body_normals = np.concatenate((np.eye(3), -np.eye(3)))
        panel_normal = np.asarray(self.panel.nHat_B, dtype=float).reshape(3)
        if not np.isfinite(panel_normal).all() or not math.isclose(float(np.linalg.norm(panel_normal)), 1., abs_tol=1e-8):
            raise ValueError('NATIVE_THERMAL_ENV_PANEL_NORMAL_INVALID')
        normals = np.vstack((body_normals, panel_normal, -panel_normal)) @ rotation.T
        flux = surface_irradiance(
            np.asarray(state.r_BN_N) - np.asarray(earth.PositionVector),
            np.asarray(sun.PositionVector) - np.asarray(earth.PositionVector), normals,
            eclipse_factor=float(self.eclipse().shadowFactor),
            earth_ir_exitance_w_m2=self.earth_ir, earth_albedo=self.albedo,
            solar_irradiance_at_au_w_m2=float(astroConstants.SOLAR_FLUX_EARTH),
            astronomical_unit_m=float(astroConstants.AU)*1000.,
            radial_order=self.radial_order, azimuth_order=self.azimuth_order,
        )
        electrical = float(self.power().netPower)
        if not math.isfinite(electrical) or electrical < -1e-9:
            raise ValueError('NATIVE_THERMAL_ENV_ELECTRICAL_OUTPUT_INVALID')
        heat = {}
        body_solar = float(np.mean(flux.solar_w_m2[:6]))
        body_albedo = float(np.mean(flux.albedo_w_m2[:6]))
        body_ir = float(np.mean(flux.earth_ir_w_m2[:6]))
        for name, node in network.nodes.items():
            p = node.params
            if name == 'solar_panel':
                area = float(self.panel.panelArea)
                if not math.isfinite(area) or area <= 0:
                    raise ValueError('NATIVE_THERMAL_ENV_PANEL_AREA_INVALID')
                # Emission and absorption use exactly the same two-sided area.
                if p.surface_area_m2 != 2.*area:
                    node.params = replace(p, surface_area_m2=2.*area)
                alpha_area = area*np.array([self.front_alpha, self.back_alpha])
                direct = float(flux.solar_w_m2[6:] @ alpha_area)
                if electrical > direct + max(1e-6, abs(direct)*1e-8):
                    raise ValueError('NATIVE_THERMAL_ENV_ELECTRICAL_EXCEEDS_ABSORBED_SOLAR')
                direct -= electrical
                reflected = float(flux.albedo_w_m2[6:] @ alpha_area)
                infrared = float(np.sum(flux.earth_ir_w_m2[6:])*p.emissivity*area)
            else:
                direct = body_solar*p.absorptivity*p.surface_area_m2
                reflected = body_albedo*p.absorptivity*p.surface_area_m2
                infrared = body_ir*p.emissivity*p.surface_area_m2
            radiator = network.radiators.get(name)
            if radiator is not None:
                rp = radiator.params
                exposed = not rp.deployable or radiator._radiator_on
                if exposed:
                    area = rp.area_m2*rp.view_factor*max(0., radiator.rejection_factor)
                    direct += body_solar*p.absorptivity*area
                    reflected += body_albedo*p.absorptivity*area
                    infrared += body_ir*rp.emissivity*area
            node._solar_heat_w += direct + reflected
            node._radiation_heat_w += infrared
            heat[name] = {'solar_heat_w': direct, 'albedo_heat_w': reflected,
                          'earth_ir_heat_w': infrared,
                          'electrical_extracted_w': electrical if name == 'solar_panel' else 0.}
        self.last_heat = heat
