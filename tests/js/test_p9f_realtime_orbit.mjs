import test from 'node:test';
import assert from 'node:assert/strict';
import { inertialPositionToEarthFixed } from '../../src/sat_sim/web/realtime-orbit.mjs';

test('GMST at J2000 epoch converts an inertial equatorial position to Earth-fixed longitude', () => {
  const result = inertialPositionToEarthFixed({
    positionM: [7000000, 0, 0],
    epochUtc: '2000-01-01T12:00:00Z',
    simTimeS: 0,
  });
  assert.ok(Math.abs(result.gmstDeg - 280.46061837) < 1e-7);
  assert.ok(Math.abs(result.latitudeDeg) < 1e-10);
  assert.ok(Math.abs(result.longitudeDeg - 79.53938163) < 1e-6);
  assert.ok(Math.abs(result.altitudeM - (7000000 - 6378137)) < 1e-6);
  assert.equal(result.coordinateFrame, 'EARTH_FIXED_GMST_APPROX');
});

test('one sidereal day returns approximately to the same Earth-fixed subpoint', () => {
  const input = {positionM:[0,7000000,0],epochUtc:'2026-01-01T00:00:00Z',timeSystem:'UTC'};
  const first = inertialPositionToEarthFixed({...input,simTimeS:0});
  const later = inertialPositionToEarthFixed({...input,simTimeS:86164.0905});
  assert.ok(Math.abs(first.latitudeDeg - later.latitudeDeg) < 1e-8);
  assert.ok(Math.abs(first.longitudeDeg - later.longitudeDeg) < 0.01);
});

test('unsupported frames, time systems and invalid vectors fail closed', () => {
  const base = {positionM:[7000000,0,0],epochUtc:'2026-01-01T00:00:00Z',simTimeS:1};
  assert.throws(() => inertialPositionToEarthFixed({...base,positionFrame:'BODY_FRAME'}), /ORBIT_POSITION_FRAME_UNSUPPORTED/);
  assert.throws(() => inertialPositionToEarthFixed({...base,timeSystem:'TAI'}), /ORBIT_TIME_SYSTEM_UNSUPPORTED/);
  assert.throws(() => inertialPositionToEarthFixed({...base,positionM:[1,2]}), /ORBIT_POSITION_VECTOR_INVALID/);
  assert.throws(() => inertialPositionToEarthFixed({...base,epochUtc:'not-a-date'}), /ORBIT_EPOCH_INVALID/);
  assert.throws(() => inertialPositionToEarthFixed({...base,simTimeS:-1}), /ORBIT_SIM_TIME_INVALID/);
  assert.throws(() => inertialPositionToEarthFixed({...base,positionM:[100,0,0]}), /ORBIT_POSITION_RADIUS_OUT_OF_RANGE/);
});
