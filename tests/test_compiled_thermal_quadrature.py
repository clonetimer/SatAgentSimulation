"""Compiled quadrature must agree with retained NumPy physics, not weaker grids."""
from functools import partial
import numpy as np
import pytest

from sat_sim.thermal_environment import surface_irradiance


@pytest.mark.parametrize('orders', [(4,8),(16,64),(32,128)])
def test_compiled_and_numpy_fluxes_agree_across_random_geometry_and_optics(orders):
    rng=np.random.default_rng(172)
    for _ in range(20):
        position=rng.normal(size=3)
        position=position/np.linalg.norm(position)*rng.uniform(6.7e6,10e6)
        sun=rng.normal(size=3)
        sun=sun/np.linalg.norm(sun)*rng.uniform(1.47e11,1.52e11)
        normals=rng.normal(size=(8,3))
        kwargs={'radial_order':orders[0],'azimuth_order':orders[1],
                'eclipse_factor':rng.uniform(0.,1.),'earth_albedo':rng.uniform(0.,.6),
                'earth_ir_exitance_w_m2':rng.uniform(0.,400.)}
        expected=surface_irradiance(position,sun,normals,integration_backend='numpy',**kwargs)
        actual=surface_irradiance(position,sun,normals,**kwargs)
        for field in ('solar_w_m2','albedo_w_m2','earth_ir_w_m2','earth_view_factors'):
            assert getattr(actual,field)==pytest.approx(getattr(expected,field),abs=1e-9,rel=1e-12)


def test_unknown_backend_never_silently_falls_back():
    with pytest.raises(ValueError,match='THERMAL_ENV_UNKNOWN_INTEGRATION_BACKEND'):
        surface_irradiance([7e6,0.,0.],[1.5e11,0.,0.],[[-1.,0.,0.]],integration_backend='unknown')


def test_actual_native_temperatures_match_retained_numpy_boundary(monkeypatch):
    from sat_sim.bsk_engine import thermal_environment as boundary
    from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime,UnifiedRuntimeConfig,WHOLE_UNIFIED_CAPABILITY_ID
    cfg=UnifiedRuntimeConfig(capability_id=WHOLE_UNIFIED_CAPABILITY_ID,duration_s=30.,step_s=.2,
        sample_s=1.,adcs_only=False,values={'environment_profile':'spice_wmm'},events=())
    monkeypatch.setattr(boundary,'surface_irradiance',partial(surface_irradiance,integration_backend='numpy'))
    expected=UnifiedNativeRuntime(cfg).run()
    monkeypatch.setattr(boundary,'surface_irradiance',surface_irradiance)
    actual=UnifiedNativeRuntime(cfg).run()
    assert expected.summary['mission_status']==actual.summary['mission_status']
    for field in actual.trace_rows[0]:
        if field.startswith('thermal.'):
            assert [row[field] for row in actual.trace_rows]==pytest.approx(
                [row[field] for row in expected.trace_rows],abs=1e-9,rel=1e-12)
