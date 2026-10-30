"""Pure preflight of native RW architecture and coupled driver settings.

The runtime retains its own hardware checks. This catches inconsistent public
TaskSpecs before compile, without importing Basilisk or changing their values.
"""
import math
from .bsk_engine.rw_geometry import standard_rw_axes, resolve_rw_geometry


def validate_native_rw_settings(values):
    axes = standard_rw_axes(values.get('rw_configuration', 'orthogonal_3'))
    resolve_rw_geometry(values, axes)
    count = len(axes)
    if count == 3 and any(k.startswith('rw_') and k.endswith('_3') and v is not None for k, v in values.items()):
        raise ValueError('Fourth-wheel hardware parameters require rw_configuration=pyramid_4')
    for key, default, lower, upper, positive in [
        ('rw_max_torque_nm', .2, 0., .2, True),
        ('rw_motor_current_limit_a', 10., 0., 20., False),
        ('rw_motor_torque_constant_nm_per_a', .02, 0., .2, True),
    ]:
        for path in [key, *(f'{key}_{i}' for i in range(count))]:
            value = values.get(path, values.get(key, default))
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper or positive and value <= 0:
                raise ValueError(f'Invalid native RW driver setting: {path}')
    mode = values.get('rw_friction_mode', 'disabled')
    c, v = values.get('rw_coulomb_friction_nm', 0.), values.get('rw_viscous_friction_nms', 0.)
    if mode not in {'disabled','coulomb_viscous','per_wheel'}:
        raise ValueError('NATIVE_RW_FRICTION_MODE_INVALID')
    shared = (c, v)
    per_wheel = [(values.get(f'rw_coulomb_friction_nm_{i}', 0.), values.get(f'rw_viscous_friction_nms_{i}', 0.)) for i in range(count)]
    if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= .01 for pair in [shared, *per_wheel] for x in pair):
        raise ValueError('NATIVE_RW_PER_WHEEL_FRICTION_INVALID')
    if mode == 'disabled' and any(shared):
        raise ValueError('NATIVE_RW_FRICTION_DISABLED_REQUIRES_ZERO')
    if mode == 'coulomb_viscous' and not any(shared):
        raise ValueError('NATIVE_RW_FRICTION_ENABLED_REQUIRES_NONZERO')
    if mode != 'per_wheel' and any(any(pair) for pair in per_wheel):
        raise ValueError('NATIVE_RW_PER_WHEEL_FRICTION_MODE_MISMATCH')
    if mode == 'per_wheel' and (any(shared) or not any(any(pair) for pair in per_wheel)):
        raise ValueError('NATIVE_RW_PER_WHEEL_FRICTION_REQUIRES_EXCLUSIVE_NONZERO_OVERRIDES')
