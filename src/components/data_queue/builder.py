"""data_queue component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import DataQueueConfig
from .faults import apply_data_queue_faults
from .faults import FaultSpec

from dataclasses import dataclass


_messaging = None
_sysModel = None

try:
    from Basilisk.architecture import messaging, sysModel
    _messaging = messaging
    _sysModel = sysModel
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/data_queue/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class DataQueueState:
    queue_bits: float = 0.0
    dropped_bits: float = 0.0
    downlinked_bits: float = 0.0


@dataclass(frozen=True)
class DataQueueProfileResult:
    time_s: tuple
    queue_bits: tuple
    dropped_bits: tuple
    downlinked_bits: tuple


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def step_data_queue(s, c, generated_bps, downlink_bps, dt_s):
    if dt_s < 0:
        raise ValueError('dt_s')
    q = max(0, s.queue_bits) + max(0, generated_bps) * dt_s
    down = min(q, max(0, downlink_bps) * dt_s)
    q -= down
    drop = max(0, q - c.capacity_bits)
    q -= drop
    return DataQueueState(q, s.dropped_bits + drop, s.downlinked_bits + down)


def simulate_queue_profile(s, c, gen, down, dt_s):
    if dt_s <= 0:
        raise ValueError('dt_s')
    t = [0.0]
    qs = [s.queue_bits]
    ds = [s.dropped_bits]
    dl = [s.downlinked_bits]
    for i in range(max(len(gen), len(down))):
        s = step_data_queue(
            s,
            c,
            gen[i] if i < len(gen) else 0,
            down[i] if i < len(down) else 0,
            dt_s,
        )
        t.append((i + 1) * dt_s)
        qs.append(s.queue_bits)
        ds.append(s.dropped_bits)
        dl.append(s.downlinked_bits)
    return DataQueueProfileResult(tuple(t), tuple(qs), tuple(ds), tuple(dl))


def _build_nominal_data_queue_config_base_impl(capacity_bits: float = 100.0, fault_specs: list[FaultSpec] | None = None) -> DataQueueConfig:
    """Build a nominal DataQueueConfig with default values.

    Args:
        capacity_bits: Queue capacity in bits

    Returns:
        DataQueueConfig: Nominal data queue configuration
    """
    return DataQueueConfig(capacity_bits=capacity_bits)


def require_basilisk_storage() -> None:
    try:
        from Basilisk.simulation import simpleStorageUnit  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk simpleStorageUnit is unavailable: {exc}") from exc


def build_simple_storage_unit(
    model_tag: str,
    capacity_bits: float,
    initial_bits: float = 0.0,
    read_rate_bps: float | None = None,
    write_rate_bps: float | None = None,
):
    """Create a Basilisk ``SimpleStorageUnit`` data storage component.

    Parameters
    ----------
    model_tag : str
        Module name for logging and debugging.
    capacity_bits : float
        Total storage capacity in bits.
    initial_bits : float, optional
        Initial data in storage in bits (default 0.0).
    read_rate_bps : float, optional
        Maximum read rate in bits per second.
    write_rate_bps : float, optional
        Maximum write rate in bits per second.

    Returns
    -------
    simpleStorageUnit.SimpleStorageUnit
        A configured storage unit model.
    """
    require_basilisk_storage()
    from Basilisk.simulation import simpleStorageUnit

    if read_rate_bps is not None or write_rate_bps is not None:
        raise ValueError(
            "SimpleStorageUnit has no native read/write-rate field; "
            "connect signed DataNodeUsageMsg publishers such as SimpleInstrument "
            "or DownlinkHandling to the storage unit instead"
        )

    storage = simpleStorageUnit.SimpleStorageUnit()
    storage.ModelTag = model_tag
    storage.storageCapacity = max(0.0, float(capacity_bits))
    storage.setDataBuffer(max(0.0, float(initial_bits)))
    return storage

# Component fault/degradation compatibility wrappers
from .degradation import DataQueueDegradation, DataQueueDegradationRate
from .degradation import apply_data_queue_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_data_queue_config_base = _build_nominal_data_queue_config_base_impl

def build_nominal_data_queue_config(
    *args,
    degradation: DataQueueDegradation | None = None,
    degradation_rate: DataQueueDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_data_queue_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_data_queue_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_data_queue_faults(config, fault_specs)
    return config

