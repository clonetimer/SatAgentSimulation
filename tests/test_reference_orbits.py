"""Parser fixtures test format/units only, not spacecraft physics."""
from datetime import timezone

import pytest

from sat_sim.reference_orbits import parse_earth_oem, parse_utc


OEM = '''CCSDS_OEM_VERS = 2.0
CREATION_DATE = 2026-10-25T00:00:00
ORIGINATOR = parser-fixture-not-flight-data
META_START
OBJECT_NAME = parser-fixture
OBJECT_ID = fixture
CENTER_NAME = Earth
REF_FRAME = EME2000
TIME_SYSTEM = UTC
START_TIME = 2026-10-26T00:00:00
STOP_TIME = 2026-10-26T00:04:00
META_STOP
2026-10-26T00:00:00.000 7000 0 0 0 7.5 0
2026-10-26T00:04:00.000 6800 1700 0 -1.8 7.2 0
'''


def test_oem_positions_and_velocities_become_si_without_interpolation():
    result = parse_earth_oem(OEM)
    assert len(result.states) == 2
    assert result.states[0].position_m == (7_000_000., 0., 0.)
    assert result.states[0].velocity_m_s == (0., 7500., 0.)
    assert result.states[0].epoch.tzinfo == timezone.utc
    assert (result.states[1].epoch-result.states[0].epoch).total_seconds() == 240.


@pytest.mark.parametrize(('old','new'), [
    ('EME2000', 'ITRF93'), ('TIME_SYSTEM = UTC', 'TIME_SYSTEM = TAI'),
    ('CENTER_NAME = Earth', 'CENTER_NAME = Mars'),
    ('7000 0 0', 'nan 0 0'),
    ('2026-10-26T00:04:00.000', '2026-10-26T00:00:00.000'),
    ('2026-10-26T00:04:00.000', '2026-10-26T00:08:00.000'),
])
def test_unsupported_or_invalid_reference_is_rejected(old, new):
    with pytest.raises(ValueError):
        parse_earth_oem(OEM.replace(old,new))


def test_oem_multiple_segments_are_not_silently_concatenated():
    with pytest.raises(ValueError, match='OEM_MULTIPLE_OR_NESTED_SEGMENTS'):
        parse_earth_oem(OEM + 'META_START\n')


def test_non_utc_offset_is_not_reinterpreted_as_utc():
    with pytest.raises(ValueError, match='OEM_NON_UTC_OFFSET'):
        parse_utc('2026-10-26T00:00:00+08:00')
