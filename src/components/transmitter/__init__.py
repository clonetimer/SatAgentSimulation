"""Transmitter component with RF Power Amplifier support.

Models:
- TransmitterConfig, TransmitterResult (from builder.py)
- compute_transmitter (from builder.py)
- _twt_output_power, _sspa_output_power (from builder.py)

Schemas:
- TwtConfig, SspaConfig, GenericAmpConfig (from schemas.py)
- RfAmplifierConfig (from schemas.py)

Builder:
- build_twt_config, build_sspa_config (from builder.py)
- build_twt_transmitter_config, build_sspa_transmitter_config (from builder.py)
- RfAmplifierBuilder (from builder.py)

Runner:
- run_twt_nominal_case, run_sspa_nominal_case (from runner.py)
- run_amplifier_comparison, run_power_sweep (from runner.py)
- AmplifierProfile, AmplifierSummary (from runner.py)

Basilisk Native:
- TransmitterBasilisk (from builder.py)
- basilisk_available, require_basilisk (from builder.py)
"""
from .builder import (
    TransmitterConfig,
    TransmitterResult,
    compute_transmitter,
    _twt_output_power,
    _sspa_output_power,
    _compute_efficiency_with_power,
    build_twt_config,
    build_sspa_config,
    build_generic_amp_config,
    build_rf_amplifier_config,
    build_twt_transmitter_config,
    build_sspa_transmitter_config,
    RfAmplifierBuilder,
    TwtConfig,
    SspaConfig,
    GenericAmpConfig,
    RfAmplifierConfig,
)
from .runner import (
    run_amplifier_profile,
    run_twt_nominal_case,
    run_sspa_nominal_case,
    run_amplifier_comparison,
    run_power_sweep,
    build_orbit_profile,
    write_profile_csv,
    print_summary,
    AmplifierProfile,
    AmplifierSummary,
    AmplifierProfileRow,
    run_nominal_case,
)
from .builder import (
    TransmitterBasilisk,
    basilisk_available,
    require_basilisk,
    create_transmitter_basilisk,
    connect_power_sink,
)
from .faults import TransmitterFaultType  # noqa: F401
from .degradation import TransmitterDegradation, TransmitterDegradationRate  # noqa: F401

__all__ = [
    # Model
    "TransmitterConfig",
    "TransmitterResult",
    "compute_transmitter",
    "_twt_output_power",
    "_sspa_output_power",
    "_compute_efficiency_with_power",
    # Schemas
    "TwtConfig",
    "SspaConfig",
    "GenericAmpConfig",
    "RfAmplifierConfig",
    # Builder
    "build_twt_config",
    "build_sspa_config",
    "build_generic_amp_config",
    "build_rf_amplifier_config",
    "build_twt_transmitter_config",
    "build_sspa_transmitter_config",
    "RfAmplifierBuilder",
    # Runner
    "run_amplifier_profile",
    "run_twt_nominal_case",
    "run_sspa_nominal_case",
    "run_amplifier_comparison",
    "run_power_sweep",
    "build_orbit_profile",
    "write_profile_csv",
    "print_summary",
    "AmplifierProfile",
    "AmplifierSummary",
    "AmplifierProfileRow",
    "run_nominal_case",
    # Basilisk
    "TransmitterBasilisk",
    "basilisk_available",
    "require_basilisk",
    "create_transmitter_basilisk",
    "connect_power_sink",
    # Faults and Degradation
    "TransmitterFaultType",
    "TransmitterDegradation",
    "TransmitterDegradationRate",
]
