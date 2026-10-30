"""Analytical checks of the finite-Earth radiance integral, not flight validation."""
import math

import numpy as np
import pytest

from sat_sim.thermal_environment import (
    ASTRONOMICAL_UNIT_M as AU, EARTH_EQUATORIAL_RADIUS_M as R, surface_irradiance,
)


def test_uniform_earth_ir_nadir_has_exact_disk_view_factor():
    for distance in (R+100_000.,7_000_000.,2.*R,10.*R):
        result = surface_irradiance([distance,0,0],[AU,0,0],[[-1,0,0],[1,0,0]],earth_ir_exitance_w_m2=234.)
        assert result.earth_view_factors[0]==pytest.approx((R/distance)**2,rel=1e-12)
        assert result.earth_ir_w_m2[0]==pytest.approx(234.*(R/distance)**2,rel=1e-12)
        assert result.earth_view_factors[1]==0.


def test_direct_solar_incidence_and_inverse_square_law():
    position=np.array([7_000_000.,0,0])
    normals=[[1,0,0],[-1,0,0],[0,1,0]]
    one=surface_irradiance(position,position+[AU,0,0],normals,earth_albedo=0.)
    two=surface_irradiance(position,position+[2.*AU,0,0],normals,earth_albedo=0.)
    assert one.solar_w_m2.tolist()==pytest.approx([1361.,0.,0.])
    assert two.solar_w_m2[0]==pytest.approx(one.solar_w_m2[0]/4.)


def test_spacecraft_eclipse_only_attenuates_direct_solar_not_earth_radiance():
    args=([7_000_000.,0,0],[AU,0,0],[[-1,0,0],[1,0,0]])
    sunlit=surface_irradiance(*args,eclipse_factor=1.)
    shadow=surface_irradiance(*args,eclipse_factor=0.)
    assert np.all(shadow.solar_w_m2==0.)
    assert shadow.earth_ir_w_m2.tolist()==sunlit.earth_ir_w_m2.tolist()
    assert shadow.albedo_w_m2.tolist()==sunlit.albedo_w_m2.tolist()
    assert shadow.albedo_w_m2[0]>0.


def test_earth_night_side_has_no_albedo_but_still_has_ir():
    result=surface_irradiance([-7_000_000.,0,0],[AU,0,0],[[1,0,0]],eclipse_factor=0.)
    assert result.albedo_w_m2[0]==0.
    assert result.earth_ir_w_m2[0]>0.


@pytest.mark.parametrize('phase',[0.,math.pi/2.])
def test_far_field_reflected_flux_matches_lambert_sphere_phase_law(phase):
    distance=1000.*R
    position=distance*np.array([math.cos(phase),math.sin(phase),0.])
    result=surface_irradiance(position,[AU,0,0],[-position/distance],radial_order=64,azimuth_order=256)
    phase_function=(math.sin(phase)+(math.pi-phase)*math.cos(phase))/math.pi
    expected=(2./3.)*.3*1361.*(R/distance)**2*phase_function
    # Finite observer distance contributes O(R/r) beyond the far-field law.
    assert result.albedo_w_m2[0]==pytest.approx(expected,rel=.003)


def test_zero_optical_environment_sources_remain_independent():
    result=surface_irradiance([7_000_000.,0,0],[AU,0,0],[[-1,0,0]],earth_albedo=0.,earth_ir_exitance_w_m2=0.)
    assert result.albedo_w_m2[0]==0.
    assert result.earth_ir_w_m2[0]==0.


@pytest.mark.parametrize('kwargs',[
    {'earth_albedo':-1.},{'earth_albedo':1.1},{'earth_ir_exitance_w_m2':-1.},
    {'eclipse_factor':-1.},{'eclipse_factor':1.1},{'solar_irradiance_at_au_w_m2':float('nan')},
    {'earth_radius_m':0.},{'astronomical_unit_m':0.},{'radial_order':True},{'azimuth_order':0},
])
def test_invalid_environment_never_silently_falls_back(kwargs):
    with pytest.raises(ValueError,match='THERMAL_ENV_'):
        surface_irradiance([7_000_000.,0,0],[AU,0,0],[[-1,0,0]],**kwargs)


@pytest.mark.parametrize('position,normals',[
    ([R,0,0],[[-1,0,0]]),([0,0,0],[[-1,0,0]]),([7_000_000.,0,float('inf')],[[-1,0,0]]),
    ([7_000_000.,0,0],[[0,0,0]]),([7_000_000.,0,0],[[1,0]]),
])
def test_invalid_geometry_is_rejected(position,normals):
    with pytest.raises(ValueError,match='THERMAL_ENV_'):
        surface_irradiance(position,[AU,0,0],normals)
