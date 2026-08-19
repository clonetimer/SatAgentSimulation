"""Public exports for the reaction wheel component package."""

from .builder import (  # noqa: F401
    attach_reaction_wheels_to_spacecraft,
    basilisk_available,
    build_reaction_wheel_bundle,
    require_basilisk_reaction_wheel,
    write_rw_torque_message,
)
from .model import (  # noqa: F401
    apply_rw_command_faults,
    apply_rw_config_faults,
    apply_rw_config_constraints,
    build_nominal_reaction_wheel_dynamics_config,
    clamp_motor_torque,
    momentum_norm_nms,
    norm_rw,
    rotational_energy_j,
    simulate_prescribed_torque_profile,
    step_wheel_speed,
    total_momentum_vector,
)
from .schemas import (  # noqa: F401
    ReactionWheelBundle,
    ReactionWheelCommandConfig,
    ReactionWheelDynamicsConfig,
    ReactionWheelProfileResult,
    ReactionWheelSpec,
    ReactionWheelState,
)
from .faults import (  # noqa: F401
    RWFaultType,
    apply_reaction_wheel_spec_faults,
    apply_runtime_reaction_wheel_fault,
    build_reaction_wheel_fault_spec,
    reaction_wheel_fault_friction_factor,
)
from .degradation import (  # noqa: F401
    ReactionWheelDegradation,
    ReactionWheelDegradationRate,
    apply_native_friction_factor,
    apply_native_friction_factor_to_specs,
    apply_reaction_wheel_spec_degradation,
)

from .degradation import apply_native_runtime_effect, native_runtime_effect_supported  # noqa: F401

from .constraints import (  # noqa: F401
    RWConstraintType,
    ReactionWheelConstraintSpec,
    apply_reaction_wheel_constraints,
    apply_reaction_wheel_spec_constraints,
    apply_runtime_reaction_wheel_constraint,
    build_reaction_wheel_constraint_spec,
)
