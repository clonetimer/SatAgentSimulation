import * as THREE from 'three';

const EARTH_ROTATION_S = 86164.0905;
const ORBIT_PERIOD_S = 5730;
const ORBIT_ALTITUDE_KM = 550;
const EARTH_RADIUS_KM = 6371;
const WGS84_A_M = 6378137;
const WGS84_F = 1 / 298.257223563;
const WGS84_E2 = WGS84_F * (2 - WGS84_F);
const INCLINATION = THREE.MathUtils.degToRad(51.6);
const TELEMETRY_TRAIL_LIMIT = 600;
const state = {
  active: false,
  ready: false,
  generation: 0,
  loadingTimer: 0,
  globe: null,
  satellite: null,
  modelAxesGroup: null,
  showBodyAxes: false,
  resizeObserver: null,
  startedAt: 0,
  lastUpdateAt: 0,
  tracking: false,
  frame: 0,
  telemetrySourceId: null,
  telemetryStreamId: null,
  telemetrySourceLabel: null,
  telemetryEpochUtc: null,
  telemetryTrail: [],
  latestTelemetryPoint: null,
  telemetrySampled: false,
  telemetryTotalRows: null,
  selectedObject: null,
  simulationGeometry: null,
  simulationGeometryRenderer: null,
  simulationGeometryScene: null,
  simulationGeometryCamera: null,
  simulationGeometryGroup: null,
  simulationGeometryResizeObserver: null,
  simulationGeometryFrame: 0,
  simulationGeometryVisible: false,
  simulationGeometryDrag: null,
  simulationGeometryRadius: 0.75,
  simulationGeometryTheta: 0.78,
  simulationGeometryPhi: 1.05
};

const byId = (id) => document.getElementById(id);

function setText(id, value) {
  const element = byId(id);
  if (element) element.textContent = value;
}

function renderTelemetryInspector(point = state.latestTelemetryPoint) {
  const hasSource = Boolean(point && state.telemetrySourceId);
  const sealedRun = Boolean(hasSource && state.telemetrySourceId.startsWith('run:'));
  const source = byId('sceneDataSource');
  if (source) {
    source.textContent = sealedRun ? 'Run 历史结果（已封存）' : (hasSource ? (state.telemetrySourceLabel || '真实 Basilisk 遥测') : '演示数据（非仿真结果）');
    source.dataset.state = sealedRun ? 'sealed' : (hasSource ? 'live' : 'demo');
  }
  setText('sceneSimTime', hasSource ? `${point.sim_time_s.toFixed(2)} s` : '—');
  setText('sceneAltitude', hasSource ? `${point.altitude_km.toFixed(2)} km` : '—');
  setText('sceneGeographicPosition', hasSource ? `${point.lat.toFixed(3)}°, ${point.lng.toFixed(3)}°` : '—');
  const truthValid = Boolean(hasSource && point.attitude_valid && point.attitude_truth);
  setText('sceneTruthStatus', truthValid ? '有效 · 刚体状态真值' : (hasSource ? '不可用 · 不使用估计值替代' : '等待真实遥测'));
  setText('sceneMrpValue', truthValid ? point.attitude_truth.mrp_BN.map(value => value.toFixed(6)).join(', ') : '—');
  setText('sceneQuaternionValue', truthValid ? point.attitude_truth.quaternion_BN.map(value => value.toFixed(6)).join(', ') : '—');
  setText('sceneRotationValue', truthValid && point.rotation
    ? [point.rotation.x, point.rotation.y, point.rotation.z].map(value => value.toFixed(2) + '°').join(', ')
    : '—');
  setText('sceneEstimatorStatus', '尚未接入');
  const frameSource = hasSource
    ? (point.session_id && point.stream
      ? `${point.session_id} / ${point.stream}`
      : (state.telemetrySourceId?.startsWith('run:') ? state.telemetrySourceId : '—'))
    : '—';
  setText('sceneFrameSource', frameSource);
  setText('sceneFrameSequence', hasSource && point.sequence !== null ? String(point.sequence) : (state.telemetrySourceId?.startsWith('run:') ? '封存样本' : '—'));
  setText('sceneFrameQuality', hasSource ? (point.quality || (state.telemetrySourceId?.startsWith('run:') ? 'SEALED' : 'UNKNOWN')) : '—');
}

function syncBodyAxes() {
  if (state.modelAxesGroup) state.modelAxesGroup.visible = state.showBodyAxes;
  const button = document.querySelector('#satelliteSceneView [data-scene-action="axes"]');
  if (button) button.setAttribute('aria-pressed', String(state.ready && state.showBodyAxes));
}

function toggleBodyAxes() {
  state.showBodyAxes = !state.showBodyAxes;
  syncBodyAxes();
  syncCameraActions();
  return state.showBodyAxes;
}

const status = (message, kind = 'info') => {
  const element = byId('satelliteSceneStatus');
  if (!element) return;
  element.textContent = message;
  element.dataset.kind = kind;
};

function orbitPoint(phase) {
  const latitude = Math.asin(Math.sin(INCLINATION) * Math.sin(phase));
  const earthRotation = (ORBIT_PERIOD_S / EARTH_ROTATION_S) * Math.PI * 2;
  const longitude = phase - (phase / (Math.PI * 2)) * earthRotation;
  return {
    lat: THREE.MathUtils.radToDeg(latitude),
    lng: THREE.MathUtils.radToDeg(longitude),
    alt: ORBIT_ALTITUDE_KM / EARTH_RADIUS_KM
  };
}

function makeSatelliteModel() {
  // Schematic display frame convention: local +X follows the solar-panel
  // span, +Y points toward the antenna/panel normal, and +Z lies in the panel plane.
  // This is a visualization convention, not a claimed CAD/as-built mounting.
  const root = new THREE.Group();
  root.name = 'Original schematic satellite model';

  const bus = new THREE.Mesh(
    new THREE.BoxGeometry(2.4, 2.1, 2.1),
    new THREE.MeshStandardMaterial({ color: 0xc4ccd7, metalness: 0.78, roughness: 0.32 })
  );
  root.add(bus);

  const panelMaterial = new THREE.MeshStandardMaterial({
    color: 0x1c5d9b, metalness: 0.3, roughness: 0.48,
    emissive: 0x071b38, emissiveIntensity: 0.25
  });
  const panelGeometry = new THREE.BoxGeometry(5.5, 0.12, 2.5);
  for (const side of [-1, 1]) {
    const panel = new THREE.Mesh(panelGeometry, panelMaterial);
    panel.position.set(side * 4.0, 0, 0);
    root.add(panel);
    const hinge = new THREE.Mesh(
      new THREE.CylinderGeometry(0.18, 0.18, 1.0, 10),
      new THREE.MeshStandardMaterial({ color: 0xb9a66c, metalness: 0.8, roughness: 0.3 })
    );
    hinge.rotation.z = Math.PI / 2;
    hinge.position.x = side * 2.55;
    root.add(hinge);
  }

  const antenna = new THREE.Mesh(
    new THREE.ConeGeometry(0.72, 1.2, 20),
    new THREE.MeshStandardMaterial({ color: 0xd6dbe2, metalness: 0.7, roughness: 0.25 })
  );
  antenna.position.set(0, 1.55, 0);
  root.add(antenna);

  const beacon = new THREE.Mesh(
    new THREE.SphereGeometry(0.18, 12, 8),
    new THREE.MeshBasicMaterial({ color: 0xffd166 })
  );
  beacon.position.set(0, -1.2, 0);
  root.add(beacon);

  // Optional body-frame triad: X red, Y green, Z blue.
  const axes = new THREE.Group();
  axes.name = 'Body frame axes (visualization convention)';
  const axisLength = 7.2;
  axes.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 0), axisLength, 0xff5d5d, 1.1, 0.55));
  axes.add(new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 0), axisLength, 0x53d68a, 1.1, 0.55));
  axes.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 0, 0), axisLength, 0x58a6ff, 1.1, 0.55));
  axes.visible = state.showBodyAxes;
  root.add(axes);
  state.modelAxesGroup = axes;
  return root;
}

function buildDemoOrbitPath() {
  const points = [];
  for (let index = 0; index <= 240; index += 1) {
    points.push(orbitPoint((index / 240) * Math.PI * 2));
  }
  return [{ name: '演示轨道（非仿真结果）', points }];
}

function julianDate(epochUtc, simTimeS) {
  const epochMs = Date.parse(epochUtc);
  if (!Number.isFinite(epochMs) || !Number.isFinite(simTimeS) || simTimeS < 0) return null;
  return epochMs / 86400000 + 2440587.5 + simTimeS / 86400;
}

function gmstAngle(epochUtc, simTimeS) {
  const jd = julianDate(epochUtc, simTimeS);
  if (jd === null) return null;
  // IAU 1982 GMST approximation, shared by position and attitude transforms.
  const centuries = (jd - 2451545.0) / 36525.0;
  const gmstDeg = 280.46061837 + 360.98564736629 * (jd - 2451545.0)
    + 0.000387933 * centuries * centuries
    - centuries * centuries * centuries / 38710000.0;
  return THREE.MathUtils.degToRad(((gmstDeg % 360) + 360) % 360);
}

function quaternionMultiplyScalarFirst(a, b) {
  const [aw, ax, ay, az] = a;
  const [bw, bx, by, bz] = b;
  return [
    aw * bw - ax * bx - ay * by - az * bz,
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
  ];
}

function attitudeToGlobeQuaternion(quaternionBN, epochUtc, simTimeS) {
  if (!Array.isArray(quaternionBN) || quaternionBN.length !== 4
      || !quaternionBN.every(value => typeof value === 'number' && Number.isFinite(value))) return null;
  const norm = Math.hypot(...quaternionBN);
  if (!Number.isFinite(norm) || Math.abs(norm - 1) > 1e-6) return null;
  const theta = gmstAngle(epochUtc, simTimeS);
  if (theta === null) return null;

  // Globe.gl world axes: X=90E, Y=north, Z=prime-meridian equator.
  // [xG,yG,zG] = [yE,zE,xE], a proper cyclic rotation.
  const qGE = [0.5, -0.5, -0.5, -0.5];
  // ECEF relative to inertial N: rotate around +Z by -GMST.
  const qEN = [Math.cos(theta / 2), 0, 0, -Math.sin(theta / 2)];
  // Basilisk EP2C(q_BN) is the passive N-to-B DCM. Three.js applies a
  // Hamilton quaternion actively, so R_three(q_BN) = EP2C(q_BN).transpose():
  // these SAME components already orient body axes in N. Conjugating them
  // here would reverse every nonzero attitude. Only reorder at the THREE API.
  const qNB = quaternionBN;
  const result = quaternionMultiplyScalarFirst(
    quaternionMultiplyScalarFirst(qGE, qEN), qNB
  );
  const resultNorm = Math.hypot(...result);
  if (!Number.isFinite(resultNorm) || resultNorm < 1e-12) return null;
  return result.map(value => value / resultNorm);
}

function attitudeToGlobeRotation(quaternionBN, epochUtc, simTimeS) {
  const globeQuaternion = attitudeToGlobeQuaternion(quaternionBN, epochUtc, simTimeS);
  if (!globeQuaternion) return null;
  const euler = new THREE.Euler().setFromQuaternion(
    new THREE.Quaternion(globeQuaternion[1], globeQuaternion[2], globeQuaternion[3], globeQuaternion[0]),
    'XYZ'
  );
  return {
    x: THREE.MathUtils.radToDeg(euler.x),
    y: THREE.MathUtils.radToDeg(euler.y),
    z: THREE.MathUtils.radToDeg(euler.z),
  };
}

function eciToGlobePoint(position, epochUtc, simTimeS) {
  if (!Array.isArray(position) || position.length !== 3 ||
      !position.every(value => typeof value === 'number' && Number.isFinite(value))) return null;
  const theta = gmstAngle(epochUtc, simTimeS);
  if (theta === null) return null;
  const cosTheta = Math.cos(theta), sinTheta = Math.sin(theta);
  const x = Number(position[0]), y = Number(position[1]), z = Number(position[2]);
  // Rotate inertial N/ECI coordinates into Earth-fixed coordinates.
  const xe = cosTheta * x + sinTheta * y;
  const ye = -sinTheta * x + cosTheta * y;
  const p = Math.hypot(xe, ye);
  const longitude = Math.atan2(ye, xe);
  let latitude = Math.atan2(z, p * (1 - WGS84_E2));
  let altitudeM = 0;
  for (let index = 0; index < 10; index += 1) {
    const sinLat = Math.sin(latitude);
    const primeVertical = WGS84_A_M / Math.sqrt(1 - WGS84_E2 * sinLat * sinLat);
    const cosLat = Math.cos(latitude);
    altitudeM = Math.abs(cosLat) > 1e-10
      ? p / cosLat - primeVertical
      : Math.abs(z) - primeVertical * (1 - WGS84_E2);
    const denominator = p * (1 - WGS84_E2 * primeVertical / (primeVertical + altitudeM));
    const nextLatitude = Math.atan2(z, denominator);
    if (!Number.isFinite(nextLatitude)) return null;
    if (Math.abs(nextLatitude - latitude) < 1e-12) { latitude = nextLatitude; break; }
    latitude = nextLatitude;
  }
  const sinLat = Math.sin(latitude);
  const primeVertical = WGS84_A_M / Math.sqrt(1 - WGS84_E2 * sinLat * sinLat);
  altitudeM = Math.abs(Math.cos(latitude)) > 1e-10
    ? p / Math.cos(latitude) - primeVertical
    : Math.abs(z) - primeVertical * (1 - WGS84_E2);
  const altitudeKm = altitudeM / 1000;
  if (![latitude, longitude, altitudeKm].every(Number.isFinite) || altitudeKm < -1 || altitudeKm > 100_000) return null;
  return {
    lat: THREE.MathUtils.radToDeg(latitude),
    lng: THREE.MathUtils.radToDeg(longitude),
    alt: Math.max(0, altitudeKm) / EARTH_RADIUS_KM,
    altitude_km: altitudeKm,
    sim_time_s: simTimeS
  };
}

function pointFromTelemetry(frame, epochUtc) {
  const values = frame?.values || frame || {};
  const position = ['x', 'y', 'z'].map(axis => values[`orbit.position_m_${axis}`]);
  const rawTime = frame?.sim_time_s ?? values.time_s;
  if (typeof rawTime !== 'number' || !Number.isFinite(rawTime) || rawTime < 0) return null;
  const simTimeS = rawTime;
  const point = eciToGlobePoint(position, epochUtc, simTimeS);
  if (!point) return null;
  const quaternion = ['q0', 'q1', 'q2', 'q3']
    .map(axis => values[`attitude.truth.quaternion_BN_${axis}`]);
  const mrp = ['x', 'y', 'z']
    .map(axis => values[`attitude.truth.mrp_BN_${axis}`]);
  const quaternionNorm = quaternion.every(value => typeof value === 'number' && Number.isFinite(value))
    ? Math.hypot(...quaternion) : NaN;
  const attitudeValid = values['attitude.truth.valid'] === 1
    && mrp.every(value => typeof value === 'number' && Number.isFinite(value))
    && quaternion.every(value => typeof value === 'number' && Number.isFinite(value))
    && Math.abs(quaternionNorm - 1) <= 1e-6;
  const rotation = attitudeValid
    ? attitudeToGlobeRotation(quaternion, epochUtc, simTimeS)
    : null;
  const validRotation = rotation || {x: 0, y: 0, z: 0};
  return {
    ...point,
    attitude_truth: attitudeValid ? { mrp_BN: mrp, quaternion_BN: quaternion } : null,
    attitude_valid: Boolean(attitudeValid && rotation),
    rotation: validRotation,
    sequence: Number.isSafeInteger(frame?.sequence) ? frame.sequence : null,
    session_id: typeof frame?.session_id === 'string' ? frame.session_id : null,
    stream: typeof frame?.stream === 'string' ? frame.stream : null,
    quality: typeof frame?.quality === 'string' ? frame.quality : null,
    name: '任务卫星（真实 Basilisk 遥测）'
  };
}

function telemetryPath() {
  const segments = [];
  let current = [];
  for (const point of state.telemetryTrail) {
    if (point.breakBefore && current.length > 1) {
      segments.push(current);
      current = [];
    } else if (point.breakBefore && current.length === 1) {
      current = [];
    }
    current.push({lat: point.lat, lng: point.lng, alt: 0.002});
  }
  if (current.length > 1) segments.push(current);
  return segments.map((points, index) => ({
    name: segments.length > 1 ? `Basilisk 实际遥测地面轨迹 · ${index + 1}` : 'Basilisk 实际遥测地面轨迹',
    points
  }));
}

function renderTelemetryPosition() {
  if (!state.globe || !state.satellite || !state.latestTelemetryPoint) return;
  Object.assign(state.satellite, state.latestTelemetryPoint);
  state.globe.objectsData([state.satellite]);
  state.globe.pathsData(telemetryPath());
  renderTelemetryInspector(state.latestTelemetryPoint);
  if (state.tracking) {
    state.globe.pointOfView({
      lat: state.satellite.lat, lng: state.satellite.lng, altitude: 1.8
    }, 0);
  }
}

function updateTelemetry(frame, options = {}) {
  // A real session frame is accepted only with an explicit, matching source
  // contract. Missing quality/session/stream metadata is not promoted to data.
  if (options.frame !== 'N_INERTIAL' || options.timeSystem !== 'UTC') return false;
  if (!frame || frame.quality !== 'GOOD' ||
      !Number.isSafeInteger(frame.sequence) || frame.sequence < 0 ||
      typeof frame.session_id !== 'string' || !frame.session_id ||
      typeof frame.stream !== 'string' || !frame.stream) return false;
  const sourceId = String(options.sourceId || '');
  const epochUtc = options.epochUtc;
  if (!sourceId || frame.session_id !== sourceId ||
      typeof options.streamId !== 'string' || frame.stream !== options.streamId ||
      !epochUtc) return false;
  const point = pointFromTelemetry(frame, epochUtc);
  if (!point || point.sequence === null) return false;

  // Validate the new source frame before clearing an existing source. A stale
  // or malformed frame from a different session must not erase a valid track.
  const streamId = options.streamId;
  const sourceChanged = state.telemetrySourceId !== sourceId || state.telemetryStreamId !== streamId;
  const previous = sourceChanged ? null : state.telemetryTrail[state.telemetryTrail.length - 1];
  if (previous && point.sim_time_s <= previous.sim_time_s) return false;
  if (previous && previous.sequence !== null && point.sequence <= previous.sequence) return false;
  if (previous && previous.sequence !== null && point.sequence > previous.sequence + 1) {
    point.breakBefore = true;
  }
  if (sourceChanged) {
    state.telemetrySourceId = sourceId;
    state.telemetryStreamId = streamId;
    state.telemetryTrail = [];
    state.latestTelemetryPoint = null;
  }
  state.telemetryTrail.push(point);
  if (state.telemetryTrail.length > TELEMETRY_TRAIL_LIMIT) {
    state.telemetryTrail.splice(0, state.telemetryTrail.length - TELEMETRY_TRAIL_LIMIT);
  }
  state.latestTelemetryPoint = point;
  state.telemetrySourceLabel = options.sourceLabel || '实时 Basilisk 遥测';
  state.telemetryEpochUtc = epochUtc;
  state.telemetrySampled = false;
  state.telemetryTotalRows = state.telemetryTrail.length;
  if (state.active && state.ready) {
    if (state.frame) cancelAnimationFrame(state.frame);
    state.frame = 0;
    renderTelemetryPosition();
    status(`已接入${state.telemetrySourceLabel}：仿真时间 ${point.sim_time_s.toFixed(2)} s；高度 ${point.altitude_km.toFixed(2)} km；${point.attitude_valid ? '本体姿态由 Basilisk 真值驱动（示意模型轴系）' : '姿态不可用，本体朝向仅为示意'}。位置使用 GMST 近似与 WGS-84 椭球进行场景定位。`, point.attitude_valid ? 'success' : 'warning');
  }
  return true;
}

function setRunTelemetry(rows, options = {}) {
  if (!options.verified || !options.runId || !options.epochUtc
      || options.frame !== 'N_INERTIAL' || options.timeSystem !== 'UTC') {
    clearTelemetrySource();
    return false;
  }
  const sourceId = `run:${options.runId}`;
  state.telemetrySourceId = sourceId;
  state.telemetryStreamId = null;
  state.telemetrySourceLabel = '已封存有限 Run 遥测';
  state.telemetryEpochUtc = options.epochUtc;
  const valid = (Array.isArray(rows) ? rows : [])
    .map(row => pointFromTelemetry(row, options.epochUtc))
    .filter(Boolean)
    .sort((a, b) => a.sim_time_s - b.sim_time_s);
  const bounded = valid.length > TELEMETRY_TRAIL_LIMIT
    ? valid.filter((_, index) => index === valid.length - 1 ||
        index % Math.ceil(valid.length / TELEMETRY_TRAIL_LIMIT) === 0).slice(-TELEMETRY_TRAIL_LIMIT)
    : valid;
  state.telemetryTrail = bounded;
  state.latestTelemetryPoint = bounded.at(-1) || null;
  state.telemetrySampled = Boolean(options.sampled || valid.length > bounded.length);
  state.telemetryTotalRows = Number(options.totalRows ?? valid.length);
  if (!state.latestTelemetryPoint) {
    state.telemetrySourceId = null;
    state.telemetryTrail = [];
    return false;
  }
  if (state.active && state.ready) {
    if (state.frame) cancelAnimationFrame(state.frame);
    state.frame = 0;
    renderTelemetryPosition();
    status(`已加载${state.telemetrySourceLabel}：${state.telemetryTrail.length} 个轨迹样本${state.telemetrySampled ? '（抽样概览）' : ''}；末样本仿真时间 ${state.latestTelemetryPoint.sim_time_s.toFixed(2)} s；${state.latestTelemetryPoint.attitude_valid ? '本体姿态由 Basilisk 真值驱动（示意模型轴系）' : '姿态不可用，本体朝向仅为示意'}。`, state.latestTelemetryPoint.attitude_valid ? 'success' : 'warning');
  }
  return true;
}

function clearTelemetrySource() {
  state.telemetrySourceId = null;
  state.telemetryStreamId = null;
  state.telemetrySourceLabel = null;
  state.telemetryEpochUtc = null;
  state.telemetryTrail = [];
  state.latestTelemetryPoint = null;
  state.telemetrySampled = false;
  state.telemetryTotalRows = null;
  renderTelemetryInspector(null);
  if (state.active && state.ready && state.globe && state.satellite) {
    Object.assign(state.satellite, {name: '任务卫星（示意模型）', ...orbitPoint(0), rotation: {x: 0, y: 0, z: 0}, attitude_valid: false, attitude_truth: null});
    state.globe.objectsData([state.satellite]);
    state.globe.pathsData(buildDemoOrbitPath());
    state.startedAt = performance.now();
    if (!state.frame) state.frame = requestAnimationFrame(updateSatellite);
    status('当前没有可用的真实仿真遥测，显示演示轨道（非仿真结果）。', 'warning');
  }
}

function overviewPoint(satellite) {
  // Keep the satellite on the near hemisphere while retaining a global view.
  return { lat: satellite.lat + 18, lng: satellite.lng + 20, altitude: 2.8 };
}

function createGlobe(host, generation) {
  if (typeof window.Globe !== 'function') {
    throw new Error('Globe.gl 本地运行时未加载。');
  }
  const width = Math.max(1, host.clientWidth);
  const height = Math.max(1, host.clientHeight);
  const satellite = state.latestTelemetryPoint
    ? {...state.latestTelemetryPoint}
    : {name: '任务卫星（示意模型）', ...orbitPoint(0)};
  const globe = new window.Globe(host, {
    animateIn: false,
    waitForGlobeReady: true,
    rendererConfig: { antialias: true, alpha: false, powerPreference: 'high-performance' }
  });
  // Retain ownership before any subsequent setup can throw.
  state.globe = globe;
  state.satellite = satellite;
  globe.onGlobeReady(() => {
    // Cached-image callbacks may fire during setup; finish configuration first.
    queueMicrotask(() => markReady(globe, generation));
  })
    .width(width)
    .height(height)
    .backgroundColor('#030915')
    .globeImageUrl('/assets/vendor/globe/textures/earth-blue-marble.jpg')
    .showGraticules(true)
    .showAtmosphere(true)
    .atmosphereColor('#4a9fe5')
    .atmosphereAltitude(0.14)
    .pathsData(state.telemetrySourceId ? telemetryPath() : buildDemoOrbitPath())
    .pathLabel('name')
    .pathPoints('points')
    .pathPointLat('lat')
    .pathPointLng('lng')
    .pathPointAlt('alt')
    .pathColor(() => '#f2c14e')
    .pathStroke(0.65)
    .pathDashLength(0.008)
    .pathDashGap(0.004)
    .pathDashAnimateTime(0)
    .objectLabel('name')
    .objectLat('lat')
    .objectLng('lng')
    .objectAltitude('alt')
    .objectFacesSurface(false)
    .objectRotation('rotation')
    .objectThreeObject(() => makeSatelliteModel())
    .objectsData([satellite]);

  const controls = globe.controls();
  controls.autoRotate = false;
  controls.enableDamping = true;
  controls.minDistance = globe.getGlobeRadius() * 1.08;
  controls.maxDistance = globe.getGlobeRadius() * 18;
  globe.pointOfView(overviewPoint(satellite), 0);
  controls.addEventListener('start', freeCamera);
  return globe;
}

function updateSatellite(now) {
  if (!state.active || !state.ready || !state.globe || !state.satellite) return;
  if (now - state.lastUpdateAt >= 100) {
    state.lastUpdateAt = now;
    const elapsed = (now - state.startedAt) / 1000;
    const phase = ((elapsed % ORBIT_PERIOD_S) / ORBIT_PERIOD_S) * Math.PI * 2;
    Object.assign(state.satellite, orbitPoint(phase));
    state.globe.objectsData([state.satellite]);
    if (state.tracking) {
      state.globe.pointOfView({
        lat: state.satellite.lat,
        lng: state.satellite.lng,
        altitude: 1.8
      }, 0);
    }
  }
  if (!state.telemetrySourceId) state.frame = requestAnimationFrame(updateSatellite);
}

function syncCameraActions() {
  document.querySelectorAll('#satelliteSceneView [data-scene-action]').forEach((button) => {
    button.disabled = !state.ready;
    if (button.dataset.sceneAction === 'track') {
      button.setAttribute('aria-pressed', String(state.ready && state.tracking));
    } else if (button.dataset.sceneAction === 'free') {
      button.setAttribute('aria-pressed', String(state.ready && !state.tracking));
    } else if (button.dataset.sceneAction === 'axes') {
      button.setAttribute('aria-pressed', String(state.ready && state.showBodyAxes));
    }
  });
}

function markReady(globe, generation) {
  if (!state.active || state.ready || state.globe !== globe || state.generation !== generation) return;
  clearTimeout(state.loadingTimer);
  state.loadingTimer = 0;
  state.ready = true;
  state.startedAt = performance.now();
  state.lastUpdateAt = 0;
  if (state.telemetrySourceId && state.latestTelemetryPoint) {
    renderTelemetryPosition();
    status(`场景已就绪，当前显示${state.telemetrySourceLabel || '真实 Basilisk 遥测'}；${state.latestTelemetryPoint.attitude_valid ? '本体姿态由 Basilisk 真值驱动（示意模型轴系）' : '姿态不可用，本体朝向仅为示意'}。`, state.latestTelemetryPoint.attitude_valid ? 'success' : 'warning');
  } else {
    state.frame = requestAnimationFrame(updateSatellite);
    status('场景已就绪。当前轨道与卫星位置为演示数据，不代表真实 Basilisk 仿真遥测。', 'success');
  }
  syncCameraActions();
}

function destroy() {
  const globe = state.globe;
  // Invalidate late imagery callbacks before invoking third-party cleanup.
  state.active = false;
  state.ready = false;
  state.generation += 1;
  state.globe = null;
  state.satellite = null;
  state.modelAxesGroup = null;
  state.tracking = false;
  clearTimeout(state.loadingTimer);
  state.loadingTimer = 0;
  if (state.frame) cancelAnimationFrame(state.frame);
  state.frame = 0;
  renderTelemetryInspector(state.latestTelemetryPoint);
  state.resizeObserver?.disconnect();
  state.resizeObserver = null;
  if (globe) {
    const renderer = globe.renderer();
    globe.controls()?.removeEventListener('start', freeCamera);
    globe.pauseAnimation();
    try {
      // Globe.gl 2.46.2's wrapper teardown also releases layers, tooltips and
      // internal tickers. Removing just the canvas leaves these owners alive.
      globe._destructor();
    } finally {
      renderer?.forceContextLoss?.();
      renderer?.domElement?.remove();
      byId('satelliteSceneCanvas')?.replaceChildren();
      syncCameraActions();
    }
  } else {
    byId('satelliteSceneCanvas')?.replaceChildren();
    syncCameraActions();
  }
}

async function enter() {
  if (state.active) return;
  const host = byId('satelliteSceneCanvas');
  if (!host) return;
  state.active = true;
  state.ready = false;
  state.tracking = false;
  const generation = ++state.generation;
  syncCameraActions();
  try {
    status('正在加载离线地球影像与三维卫星模型…');
    createGlobe(host, generation);
    state.loadingTimer = setTimeout(() => {
      if (!state.active || state.ready || state.generation !== generation) return;
      destroy();
      status('地球影像加载超时。请返回仿真向导后重试，其他仿真功能仍可使用。', 'error');
    }, 15000);
    state.resizeObserver = new ResizeObserver(() => {
      if (!state.active || !state.globe || state.generation !== generation) return;
      state.globe.width(Math.max(1, host.clientWidth));
      state.globe.height(Math.max(1, host.clientHeight));
    });
    state.resizeObserver.observe(host);
  } catch (error) {
    destroy();
    // Keep technical errors in diagnostics, not in the ordinary user view.
    console.warn('Satellite scene initialization failed', error);
    status('三维场景未能加载。请返回仿真向导后重试，其他仿真功能仍可使用。', 'error');
  }
}

function track() {
  if (!state.ready || !state.globe || !state.satellite) return;
  state.tracking = true;
  state.globe.pointOfView({
    lat: state.satellite.lat,
    lng: state.satellite.lng,
    altitude: 1.8
  }, 0);
  syncCameraActions();
}

function freeCamera() {
  state.tracking = false;
  if (state.ready && state.globe) {
    // Freeze the current view and cancel a pending home transition. Subsequent
    // telemetry/display ticks must not take the camera back from the user.
    const currentView = state.globe.pointOfView();
    state.globe.pointOfView(currentView, 0);
  }
  syncCameraActions();
}

function home() {
  if (!state.ready || !state.globe) return;
  state.tracking = false;
  state.globe.pointOfView(overviewPoint(state.satellite), 0);
  syncCameraActions();
}

const OBJECT_CLASS_LABELS = {
  physical: '物理对象', spacecraft: '整星', physical_group: '物理分组', equipment: '物理单机模型',
  external_equipment: '外部设备', logical: '逻辑对象', environment: '环境对象',
  control_function: '控制功能', region: '分系统区域', orbit: '轨道对象',
};
const OBJECT_KIND_LABELS = {
  whole_spacecraft: '整星', subsystem: '分系统', component: '部件类别',
  equipment: '设备模型类别', context_object: '上下文对象', context_group: '上下文分组',
  function: '功能对象', environment: '环境对象',
};
function renderSelectedObjectSemantics() {
  const selected = state.selectedObject;
  const set = (id, value) => setText(id, value == null || value === '' ? '未提供' : String(value));
  if (!selected) {
    set('sceneObjectName', '尚未选择对象');
    set('sceneObjectKind', '—');
    set('sceneObjectClass', '—');
    set('sceneObjectCapability', '—');
    set('sceneObjectTarget', '—');
    set('sceneObjectConfigScope', '—');
    set('sceneObjectAnchor', '未审定');
    set('sceneObjectMapping', '未建立');
    set('sceneObjectInventory', '不推断实装数量');
    set('sceneObjectSemanticsNote', '选择对象树中的整星、分系统或部件后，这里显示模型类别、能力归属和空间证据。');
    return;
  }
  set('sceneObjectName', selected.name);
  set('sceneObjectKind', OBJECT_KIND_LABELS[selected.kind] || selected.kind || '未分类');
  set('sceneObjectClass', OBJECT_CLASS_LABELS[selected.physicalClass] || selected.physicalClass || '未分类');
  set('sceneObjectCapability', selected.executionCapabilityId || '无独立执行能力');
  const targetLabels = {spacecraft:'整星',region:'分系统区域',part:'物理部件',ground:'外部地面设备',relationship:'逻辑关系',logical:'逻辑对象',environment:'轨道环境',orbit:'轨道对象'};
  const scopeLabels = {self:'本对象配置',parent:'所属分系统配置',none:'无独立配置'};
  set('sceneObjectTarget', targetLabels[selected.sceneTarget] || selected.sceneTarget || '未分类');
  set('sceneObjectConfigScope', scopeLabels[selected.configurationScope] || selected.configurationScope || '未声明');
  set('sceneObjectAnchor', selected.spatialAnchor ? '已登记，待坐标系审查' : '未审定，不生成三维位置');
  set('sceneObjectMapping', '未建立 CAD / GLB 节点映射');
  const inventory = selected.instanceSemantics === 'capability_model_not_vehicle_inventory'
    ? '模型类别，不代表实装设备数量'
    : (Number.isInteger(selected.inventoryCount) && selected.inventoryCountSource
      ? String(selected.inventoryCount) + '（来源：' + selected.inventoryCountSource + '）'
      : '无经审定的实装数量');
  set('sceneObjectInventory', inventory);
  const note = selected.spatialAnchor
    ? '对象已有空间锚点记录；只有完成坐标系、安装变换和资产节点映射审查后，才允许在三维场景中定位。'
    : '对象保留在工程对象树中，但没有经审定的空间锚点。场景不会为它虚构安装位置、朝向或实体模型。';
  set('sceneObjectSemanticsNote', note);
}

function selectObject(metadata = {}) {
  const { name, objectId = null, kind = null, physicalClass = null, sceneTarget = null,
    spatialAnchor = null, executionCapabilityId = null, configurationScope = null,
    instanceSemantics = null, inventoryCount = null, inventoryCountSource = null } = metadata;
  if (!name && !objectId) return;
  state.selectedObject = {
    objectId, name: name || objectId, kind, physicalClass, sceneTarget,
    spatialAnchor, executionCapabilityId, configurationScope,
    instanceSemantics, inventoryCount, inventoryCountSource,
  };
  renderSelectedObjectSemantics();
  const hasSpatialAnchor = Boolean(spatialAnchor);
  const readyPrefix = state.active && state.ready
    ? (state.telemetrySourceId
      ? `场景已就绪，当前显示${state.telemetrySourceLabel || '真实仿真遥测'}。${state.latestTelemetryPoint
        ? `仿真时间 ${state.latestTelemetryPoint.sim_time_s.toFixed(2)} s；高度 ${state.latestTelemetryPoint.altitude_km.toFixed(2)} km；${state.latestTelemetryPoint.attitude_valid ? '姿态由 Basilisk 真值驱动（示意模型轴系）' : '姿态不可用，本体朝向仅为示意'}。`
        : ''}`
      : '场景已就绪，当前使用演示数据（非仿真结果）。') : '';
  if (!hasSpatialAnchor) {
    status(`${readyPrefix}已选择：${state.selectedObject.name}。该对象没有经过审定的三维空间锚点，场景保持当前视角，不虚构部件位置。`, 'info');
    return;
  }
  status(`${readyPrefix}已选择：${state.selectedObject.name}。对象空间锚点已登记；局部模型节点映射将在资产映射验收后启用。`, 'info');
}

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-scene-action]')?.dataset.sceneAction;
  if (action === 'track') track();
  if (action === 'free') freeCamera();
  if (action === 'home') home();
  if (action === 'axes') toggleBodyAxes();
});


const DEFAULT_RW_AXES = {
  orthogonal_3: [[1,0,0],[0,1,0],[0,0,1]],
  pyramid_4: [[1,1,1],[1,-1,-1],[-1,1,-1],[-1,-1,1]].map(axis => {
    const scale = 1 / Math.sqrt(3);
    return axis.map(value => value * scale);
  }),
};
function resolveDisplayRwGeometry(values) {
  if (!values || typeof values !== 'object'
      || !Object.prototype.hasOwnProperty.call(values,'rw_configuration')
      || !Object.prototype.hasOwnProperty.call(values,'rw_geometry_mode')) return null;
  const configuration = values.rw_configuration;
  const defaults = DEFAULT_RW_AXES[configuration];
  if (!defaults) return null;
  const mode = values.rw_geometry_mode || 'legacy_axis_offset';
  const count = defaults.length;
  const axes = [];
  const positions = [];
  if (mode === 'legacy_axis_offset') {
    const radius = Number(values.rw_mount_radius_m ?? 0);
    if (!Number.isFinite(radius) || radius < 0 || radius > 0.25) return null;
    for (let i=0;i<count;i+=1) {
      axes.push(defaults[i]);
      positions.push(defaults[i].map(v => v * radius));
    }
  } else if (mode === 'per_wheel') {
    if (Number(values.rw_mount_radius_m ?? 0) !== 0) return null;
    let anyOverride = false;
    for (let i=0;i<count;i+=1) {
      const axisKeys = ['x','y','z'].map(axis => 'rw_axis_b_'+axis+'_'+i);
      const supplied = axisKeys.map(key => values[key] != null);
      anyOverride ||= supplied.some(Boolean)
        || ['x','y','z'].some(axis => values['rw_mount_b_'+axis+'_m_'+i] != null);
      if (supplied.some(Boolean) && !supplied.every(Boolean)) return null;
      const axis = supplied.every(Boolean) ? axisKeys.map(key => Number(values[key])) : defaults[i];
      const norm2 = axis.reduce((sum,v)=>sum+v*v,0);
      if (!axis.every(Number.isFinite) || norm2 < 0.999 || norm2 > 1.001) return null;
      axes.push(axis);
      const pos = ['x','y','z'].map(axisName => Number(values['rw_mount_b_'+axisName+'_m_'+i] ?? 0));
      if (!pos.every(v=>Number.isFinite(v)&&Math.abs(v)<=0.25)) return null;
      positions.push(pos);
    }
    if (!anyOverride) return null;
  } else return null;
  return {configuration,mode,axes,positions,count};
}
function disposeSimulationGeometryGroup() {
  if (!state.simulationGeometryGroup) return;
  state.simulationGeometryGroup.traverse(node => {
    node.geometry?.dispose?.();
    const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : [];
    materials.forEach(material => material.dispose?.());
  });
  state.simulationGeometryGroup.clear();
}
function ensureSimulationGeometryViewer() {
  if (state.simulationGeometryRenderer) return true;
  const canvas = byId('simulationGeometryCanvas');
  if (!canvas) return false;
  try {
    const renderer = new THREE.WebGLRenderer({canvas,antialias:true,alpha:false});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const world = new THREE.Scene();
    world.background = new THREE.Color(0x0d1724);
    world.add(new THREE.HemisphereLight(0xe5f1ff,0x263345,2.1));
    const key = new THREE.DirectionalLight(0xffffff,2.5); key.position.set(1.5,2,2); world.add(key);
    const fill = new THREE.DirectionalLight(0x78aee8,1.1); fill.position.set(-2,-1,-1); world.add(fill);
    const grid = new THREE.GridHelper(0.6,12,0x45586d,0x26384b); world.add(grid);
    const axes = new THREE.AxesHelper(0.22); world.add(axes);
    const camera = new THREE.PerspectiveCamera(42,1,0.001,20);
    state.simulationGeometryRenderer=renderer;
    state.simulationGeometryScene=world;
    state.simulationGeometryCamera=camera;
    state.simulationGeometryGroup=new THREE.Group();
    world.add(state.simulationGeometryGroup);
    const resize = () => {
      const rect=canvas.getBoundingClientRect();
      if(!rect.width||!rect.height)return;
      renderer.setSize(rect.width,rect.height,false);
      camera.aspect=rect.width/rect.height;
      camera.updateProjectionMatrix();
      renderSimulationGeometry();
    };
    state.simulationGeometryResizeObserver=new ResizeObserver(resize);
    state.simulationGeometryResizeObserver.observe(canvas);
    canvas.addEventListener('pointerdown',event=>{
      state.simulationGeometryDrag={x:event.clientX,y:event.clientY};
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove',event=>{
      const drag=state.simulationGeometryDrag;if(!drag)return;
      state.simulationGeometryTheta-=(event.clientX-drag.x)*0.008;
      state.simulationGeometryPhi=THREE.MathUtils.clamp(state.simulationGeometryPhi+(event.clientY-drag.y)*0.008,0.08,Math.PI-0.08);
      drag.x=event.clientX;drag.y=event.clientY;renderSimulationGeometry();
    });
    canvas.addEventListener('pointerup',()=>{state.simulationGeometryDrag=null;});
    canvas.addEventListener('wheel',event=>{
      event.preventDefault();
      state.simulationGeometryRadius=THREE.MathUtils.clamp(state.simulationGeometryRadius*Math.exp(event.deltaY*0.001),0.12,5);
      renderSimulationGeometry();
    },{passive:false});
    resize();
    return true;
  } catch(error) {
    const status=byId('simulationGeometryStatus');
    if(status)status.textContent='仿真构型视图初始化失败；轨道与 CAD 参考视图不受影响。';
    return false;
  }
}
function renderSimulationGeometry() {
  if(!state.simulationGeometryRenderer||!state.simulationGeometryCamera||!state.simulationGeometryVisible)return;
  const camera=state.simulationGeometryCamera,r=state.simulationGeometryRadius,phi=state.simulationGeometryPhi,theta=state.simulationGeometryTheta;
  camera.position.set(r*Math.sin(phi)*Math.sin(theta),r*Math.cos(phi),r*Math.sin(phi)*Math.cos(theta));
  camera.lookAt(0,0,0);
  if(state.simulationGeometryFrame)return;
  state.simulationGeometryFrame=requestAnimationFrame(()=>{
    state.simulationGeometryFrame=0;
    if(state.simulationGeometryVisible)state.simulationGeometryRenderer.render(state.simulationGeometryScene,camera);
  });
}
function renderSimulationGeometryRows(geometry) {
  const host=byId('simulationGeometryRows');if(!host)return;
  host.replaceChildren();
  geometry.axes.forEach((axis,index)=>{
    const row=document.createElement('tr');
    const values=[geometry.positions[index].map(v=>v.toFixed(4)).join(', '),axis.map(v=>v.toFixed(4)).join(', ')];
    const wheel=document.createElement('td');wheel.textContent='RW'+(index+1);
    const position=document.createElement('td');position.textContent=values[0];
    const direction=document.createElement('td');direction.textContent=values[1];
    row.append(wheel,position,direction);host.append(row);
  });
}
function setSimulationConfiguration(payload) {
  if(!payload||payload.validated!==true||!String(payload.capabilityId||'').startsWith('whole_spacecraft.')) {
    clearSimulationConfiguration();return false;
  }
  const geometry=resolveDisplayRwGeometry(payload.values?.parameters?.values || payload.values);
  if(!geometry || !ensureSimulationGeometryViewer()) { clearSimulationConfiguration();return false; }
  state.simulationGeometry={...geometry,capabilityId:payload.capabilityId,validated:true};
  disposeSimulationGeometryGroup();
  geometry.positions.forEach((position,index)=>{
    const axis=geometry.axes[index];
    const material=new THREE.MeshStandardMaterial({color:[0x69b8f2,0xe4b65e,0x7acb9b,0xb79be8][index%4],metalness:0.55,roughness:0.35});
    const wheel=new THREE.Mesh(new THREE.CylinderGeometry(0.018,0.018,0.012,24),material);
    wheel.position.set(...position);
    wheel.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),new THREE.Vector3(...axis));
    state.simulationGeometryGroup.add(wheel);
    const marker=new THREE.Mesh(new THREE.SphereGeometry(0.004,12,8),new THREE.MeshBasicMaterial({color:material.color}));
    marker.position.set(...position);state.simulationGeometryGroup.add(marker);
    const arrow=new THREE.ArrowHelper(new THREE.Vector3(...axis),new THREE.Vector3(...position),0.085,material.color.getHex(),0.018,0.009);
    state.simulationGeometryGroup.add(arrow);
  });
  renderSimulationGeometryRows(geometry);
  const badge=byId('simulationGeometryBadge');
  if(badge){badge.textContent='TaskSpec 校验通过 · '+geometry.capabilityId;badge.dataset.state='live';}
  const summary=byId('simulationGeometrySummary');
  if(summary)summary.textContent=geometry.count+' 个反作用轮；构型 '+(geometry.configuration==='pyramid_4'?'四轮金字塔':'三轮正交')+'；几何模式 '+(geometry.mode==='per_wheel'?'逐轮安装':'预设轴向与共用偏置')+'。位置和轴向均以 B 系表示。';
  const status=byId('simulationGeometryStatus');
  if(status)status.textContent='显示的是已通过后端 TaskSpec 校验的仿真输入几何，不是 as-built 安装证明。';
  if(state.simulationGeometryVisible)renderSimulationGeometry();
  return true;
}
function clearSimulationConfiguration() {
  state.simulationGeometry=null;
  disposeSimulationGeometryGroup();
  const badge=byId('simulationGeometryBadge');if(badge){badge.textContent='等待整星 TaskSpec 校验';badge.dataset.state='demo';}
  const summary=byId('simulationGeometrySummary');if(summary)summary.textContent='请先选择整星统一 Basilisk 模型，完成参数校验后查看物理构型。';
  const host=byId('simulationGeometryRows');if(host)host.innerHTML='<tr><td colspan="3">尚无已校验配置</td></tr>';
  const status=byId('simulationGeometryStatus');if(status)status.textContent='没有已校验的整星构型。';
  if(state.simulationGeometryVisible)renderSimulationGeometry();
}
function setSimulationGeometryVisible(visible) {
  state.simulationGeometryVisible=Boolean(visible);
  if(state.simulationGeometryVisible&&ensureSimulationGeometryViewer())renderSimulationGeometry();
  else if(state.simulationGeometryFrame){cancelAnimationFrame(state.simulationGeometryFrame);state.simulationGeometryFrame=0;}
}
window.SatScene = {
  enter, exit: destroy, track, freeCamera, home, selectObject,
  setSimulationConfiguration, clearSimulationConfiguration, setSimulationGeometryVisible,
  updateTelemetry, setRunTelemetry, clearTelemetrySource,
  eciToGlobePoint, attitudeToGlobeQuaternion, attitudeToGlobeRotation,
  toggleBodyAxes,
  isBodyAxesVisible: () => Boolean(state.modelAxesGroup?.visible && state.showBodyAxes),
  hasBodyAxesModel: () => Boolean(state.modelAxesGroup),
};
