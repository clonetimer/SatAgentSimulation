"""Research-only observation layer for R84-R3A ideal wheel-speed feedback.

All parameters are explicit sensitivity assumptions until tied to one device/ICD.
This layer never changes Basilisk dynamics or exposes fault truth.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Sequence


@dataclass(frozen=True)
class WheelSpeedMeasurementAssumptions:
    sample_every_n_native_points: int
    resolution_rad_s: float
    noise_sigma_rad_s: float
    bias_rad_s: float
    dropout_probability: float
    timestamp_latency_s: float
    timestamp_jitter_sigma_s: float

    def __post_init__(self) -> None:
        if type(self.sample_every_n_native_points) is not int or self.sample_every_n_native_points < 1:
            raise ValueError("sample_every_n_native_points must be a positive integer")
        for name in ("resolution_rad_s", "noise_sigma_rad_s", "bias_rad_s",
                     "dropout_probability", "timestamp_latency_s", "timestamp_jitter_sigma_s"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.resolution_rad_s < 0 or self.noise_sigma_rad_s < 0:
            raise ValueError("resolution and noise must be nonnegative")
        if not 0 <= self.dropout_probability <= 1:
            raise ValueError("dropout_probability must be in [0, 1]")
        if self.timestamp_latency_s < 0 or self.timestamp_jitter_sigma_s < 0:
            raise ValueError("timestamp latency and jitter must be nonnegative")


def observe_wheel_speeds(
    native_rows: Sequence[dict[str, float]],
    assumptions: WheelSpeedMeasurementAssumptions,
    *,
    seed: int,
) -> list[dict[str, float | bool | None]]:
    """Return an unlabelled three-wheel telemetry stream on the native clock.

    Each wheel has an independent deterministic RNG stream. Dropped samples retain
    a timestamp and validity bit; values are null so consumers cannot confuse a
    held value with an actual measurement. Jitter affects receipt time only.
    """
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("seed must be a nonnegative 63-bit integer")
    if not native_rows:
        raise ValueError("native_rows must be nonempty")
    generators = [random.Random(seed * 4 + wheel) for wheel in range(3)]
    result: list[dict[str, float | bool | None]] = []
    previous_time = -math.inf
    for index, native in enumerate(native_rows):
        time_s = native.get("time_s")
        if type(time_s) not in (float, int) or not math.isfinite(time_s) or time_s <= previous_time:
            raise ValueError("native timestamps must be finite and strictly increasing")
        previous_time = time_s
        if index % assumptions.sample_every_n_native_points:
            continue
        row: dict[str, float | bool | None] = {"source_time_s": float(time_s)}
        for wheel, rng in enumerate(generators):
            key = f"sensor.adcs.rw.speed_rad_s_{wheel}"
            value = native.get(key)
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError(f"invalid native wheel speed: {key}")
            receipt = max(time_s, time_s + assumptions.timestamp_latency_s + rng.gauss(0, assumptions.timestamp_jitter_sigma_s))
            row[f"wheel_{wheel}_receipt_time_s"] = receipt
            valid = rng.random() >= assumptions.dropout_probability
            row[f"wheel_{wheel}_valid"] = valid
            noisy = value + assumptions.bias_rad_s + rng.gauss(0, assumptions.noise_sigma_rad_s)
            row[key] = ((round(noisy / assumptions.resolution_rad_s) * assumptions.resolution_rad_s)
                        if assumptions.resolution_rad_s else noisy) if valid else None
        result.append(row)
    return result
