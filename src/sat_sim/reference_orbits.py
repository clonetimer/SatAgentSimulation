"""Strict, offline CCSDS OEM state-vector input for external orbit comparisons."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re


@dataclass(frozen=True)
class ReferenceState:
    epoch: datetime
    position_m: tuple[float, float, float]
    velocity_m_s: tuple[float, float, float]


@dataclass(frozen=True)
class ReferenceOrbit:
    metadata: dict[str, str]
    states: tuple[ReferenceState, ...]


def parse_utc(value: str) -> datetime:
    # OEM UTC permits an omitted trailing Z. An explicit non-UTC offset is not
    # silently reinterpreted. Leap-second records require a dedicated parser.
    parsed = datetime.fromisoformat(value.removesuffix('Z'))
    if parsed.tzinfo is not None and parsed.utcoffset().total_seconds() != 0:
        raise ValueError('OEM_NON_UTC_OFFSET')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)?', value):
        raise ValueError('OEM_UNSUPPORTED_EPOCH_FORMAT_OR_PRECISION')
    return parsed.replace(tzinfo=timezone.utc)


def parse_earth_oem(text: str) -> ReferenceOrbit:
    """Read one Earth/EME2000/UTC OEM segment; km and km/s become SI.

    Covariance, acceleration and multiple segments are intentionally rejected
    rather than silently mixed into a different supported reference contract.
    """
    header: dict[str, str] = {}
    metadata: dict[str, str] = {}
    states: list[ReferenceState] = []
    inside = False
    segments = 0
    metadata_finished = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('COMMENT'):
            continue
        if line == 'META_START':
            segments += 1
            if inside or segments != 1:
                raise ValueError('OEM_MULTIPLE_OR_NESTED_SEGMENTS')
            inside = True
            continue
        if line == 'META_STOP':
            if not inside:
                raise ValueError('OEM_METADATA_BOUNDARY_INVALID')
            inside = False
            metadata_finished = True
            continue
        if '=' in line:
            key, value = (part.strip() for part in line.split('=', 1))
            target = metadata if inside else header
            if metadata_finished and not inside:
                raise ValueError('OEM_UNSUPPORTED_POST_METADATA_RECORD')
            if key in target:
                raise ValueError('OEM_DUPLICATE_METADATA')
            target[key] = value
            continue
        if inside or not metadata_finished:
            raise ValueError('OEM_STATE_OUTSIDE_DATA_SECTION')
        fields = line.split()
        if len(fields) != 7:
            raise ValueError('OEM_STATE_REQUIRES_POSITION_AND_VELOCITY')
        epoch = parse_utc(fields[0])
        values = tuple(float(value) * 1000. for value in fields[1:])
        if not all(math.isfinite(value) for value in values):
            raise ValueError('OEM_NONFINITE_STATE')
        if states and epoch <= states[-1].epoch:
            raise ValueError('OEM_NON_MONOTONIC_EPOCHS')
        states.append(ReferenceState(epoch, values[:3], values[3:]))
    if inside or segments != 1 or not metadata_finished or len(states) < 2:
        raise ValueError('OEM_INCOMPLETE_SEGMENT')
    if header.get('CCSDS_OEM_VERS') != '2.0':
        raise ValueError('OEM_VERSION_UNSUPPORTED')
    required = ('OBJECT_NAME', 'OBJECT_ID', 'CENTER_NAME', 'REF_FRAME', 'TIME_SYSTEM', 'START_TIME', 'STOP_TIME')
    if not all(metadata.get(key) for key in required):
        raise ValueError('OEM_REQUIRED_METADATA_MISSING')
    for key, value in {'CENTER_NAME':'EARTH', 'REF_FRAME':'EME2000', 'TIME_SYSTEM':'UTC'}.items():
        if metadata.get(key, '').upper() != value:
            raise ValueError(f'OEM_REFERENCE_CONTRACT_UNSUPPORTED: {key}')
    start = parse_utc(metadata['START_TIME'])
    stop = parse_utc(metadata['STOP_TIME'])
    usable_start = parse_utc(metadata.get('USEABLE_START_TIME', metadata['START_TIME']))
    usable_stop = parse_utc(metadata.get('USEABLE_STOP_TIME', metadata['STOP_TIME']))
    if not start <= usable_start < usable_stop <= stop:
        raise ValueError('OEM_VALIDITY_RANGE_INVALID')
    if states[0].epoch < start or states[-1].epoch > stop:
        raise ValueError('OEM_STATES_OUTSIDE_DECLARED_RANGE')
    return ReferenceOrbit({**header, **metadata}, tuple(states))
