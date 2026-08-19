from __future__ import annotations

from dataclasses import dataclass
import math

import pytest

from sat_sim_kernel import KernelValidationError, canonical_json, content_sha256, to_canonical_dict


@dataclass(frozen=True)
class Payload:
    name: str
    values: tuple[int, ...]
    metadata: dict[str, object]


def test_canonical_json_is_order_independent_for_mappings() -> None:
    left = Payload("x", (1, 2), {"b": 2, "a": {"z": 3, "y": 4}})
    right = Payload("x", (1, 2), {"a": {"y": 4, "z": 3}, "b": 2})
    assert canonical_json(left) == canonical_json(right)
    assert content_sha256(left) == content_sha256(right)


def test_unsupported_or_nondeterministic_values_fail() -> None:
    with pytest.raises(KernelValidationError):
        to_canonical_dict({1: "not-a-string-key"})
    with pytest.raises(KernelValidationError):
        to_canonical_dict({"values": {1, 2}})
    with pytest.raises(KernelValidationError):
        to_canonical_dict(math.nan)
