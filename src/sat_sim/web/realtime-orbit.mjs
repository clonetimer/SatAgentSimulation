const EARTH_EQUATORIAL_RADIUS_M = 6378137.0;
const SECONDS_PER_DAY = 86400.0;
const UNIX_EPOCH_JD = 2440587.5;
const J2000_JD = 2451545.0;

function normalizeDegrees(value) {
  return ((value % 360) + 360) % 360;
}

/**
 * Convert a Basilisk N-frame inertial position to an Earth-fixed subpoint.
 * UTC is used as a UT1 approximation and GMST uses the IAU-1982 expression.
 * This is suitable for scene visualization, not navigation-grade geodesy.
 */
export function inertialPositionToEarthFixed({
  positionM,
  epochUtc,
  simTimeS,
  timeSystem = 'UTC',
  positionFrame = 'J2000_INERTIAL',
} = {}) {
  if (positionFrame !== 'J2000_INERTIAL') throw new Error('ORBIT_POSITION_FRAME_UNSUPPORTED');
  if (timeSystem !== 'UTC') throw new Error('ORBIT_TIME_SYSTEM_UNSUPPORTED');
  if (!Array.isArray(positionM) || positionM.length !== 3 ||
      positionM.some(value => typeof value !== 'number' || !Number.isFinite(value))) {
    throw new Error('ORBIT_POSITION_VECTOR_INVALID');
  }
  if (typeof simTimeS !== 'number' || !Number.isFinite(simTimeS) || simTimeS < 0) {
    throw new Error('ORBIT_SIM_TIME_INVALID');
  }
  const epochMs = Date.parse(epochUtc);
  if (!Number.isFinite(epochMs)) throw new Error('ORBIT_EPOCH_INVALID');

  const [xI, yI, zI] = positionM;
  const radiusM = Math.hypot(xI, yI, zI);
  if (!(radiusM > EARTH_EQUATORIAL_RADIUS_M && radiusM <= 100_000_000)) {
    throw new Error('ORBIT_POSITION_RADIUS_OUT_OF_RANGE');
  }

  const julianDate = epochMs / 86400000 + UNIX_EPOCH_JD + simTimeS / SECONDS_PER_DAY;
  const centuries = (julianDate - J2000_JD) / 36525.0;
  const gmstDeg = normalizeDegrees(
    280.46061837 +
    360.98564736629 * (julianDate - J2000_JD) +
    0.000387933 * centuries * centuries -
    (centuries * centuries * centuries) / 38710000.0
  );
  const theta = gmstDeg * Math.PI / 180.0;
  const cosTheta = Math.cos(theta);
  const sinTheta = Math.sin(theta);

  // Rotate inertial coordinates by -GMST about the Earth spin axis.
  const xE = cosTheta * xI + sinTheta * yI;
  const yE = -sinTheta * xI + cosTheta * yI;
  const zE = zI;
  const horizontalM = Math.hypot(xE, yE);
  const latitudeDeg = Math.atan2(zE, horizontalM) * 180.0 / Math.PI;
  const longitudeDeg = Math.atan2(yE, xE) * 180.0 / Math.PI;
  return Object.freeze({
    latitudeDeg,
    longitudeDeg: normalizeDegrees(longitudeDeg + 180.0) - 180.0,
    altitudeM: radiusM - EARTH_EQUATORIAL_RADIUS_M,
    altitudeRatio: (radiusM - EARTH_EQUATORIAL_RADIUS_M) / EARTH_EQUATORIAL_RADIUS_M,
    radiusM,
    gmstDeg,
    coordinateFrame: 'EARTH_FIXED_GMST_APPROX',
  });
}

export const REALTIME_ORBIT_CONSTANTS = Object.freeze({
  earthEquatorialRadiusM: EARTH_EQUATORIAL_RADIUS_M,
  secondsPerDay: SECONDS_PER_DAY,
});
