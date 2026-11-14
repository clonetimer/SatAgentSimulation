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
  telemetryTotalRows: null
};

const byId = (id) => document.getElementById(id);
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
  // span, +Y points toward the antenna, and +Z is the panel-plane normal.
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
  // Basilisk q_BN maps N to B; invert it to orient B axes in N.
  const qNB = [quaternionBN[0], -quaternionBN[1], -quaternionBN[2], -quaternionBN[3]];
  const result = quaternionMultiplyScalarFirst(
    quaternionMultiplyScalarFirst(qGE, qEN), qNB
  );
  const resultNorm = Math.hypot(...result);
  if (!Number.isFinite(resultNorm) || resultNorm < 1e-12) return null;
  return result.map(value => value / resultNorm);
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
  const globeQuaternion = attitudeValid
    ? attitudeToGlobeQuaternion(quaternion, epochUtc, simTimeS) : null;
  const euler = globeQuaternion
    ? new THREE.Euler().setFromQuaternion(
        new THREE.Quaternion(globeQuaternion[1], globeQuaternion[2], globeQuaternion[3], globeQuaternion[0]),
        'XYZ'
      ) : null;
  const rotation = euler ? {
    x: THREE.MathUtils.radToDeg(euler.x),
    y: THREE.MathUtils.radToDeg(euler.y),
    z: THREE.MathUtils.radToDeg(euler.z),
  } : {x: 0, y: 0, z: 0};
  return {
    ...point,
    attitude_truth: attitudeValid ? { mrp_BN: mrp, quaternion_BN: quaternion } : null,
    attitude_valid: Boolean(attitudeValid && globeQuaternion),
    rotation,
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
    status(`已接入${state.telemetrySourceLabel}：仿真时间 ${point.sim_time_s.toFixed(2)} s；高度 ${point.altitude_km.toFixed(2)} km；${point.attitude_valid ? '本体姿态由 Basilisk 真值驱动' : '姿态不可用，本体朝向仅为示意'}。位置使用 GMST 近似与 WGS-84 椭球进行场景定位。`, point.attitude_valid ? 'success' : 'warning');
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
    status(`已加载${state.telemetrySourceLabel}：${state.telemetryTrail.length} 个轨迹样本${state.telemetrySampled ? '（抽样概览）' : ''}；末样本仿真时间 ${state.latestTelemetryPoint.sim_time_s.toFixed(2)} s。`, 'success');
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
    .objectsData([satellite])
    .objectLabel('name')
    .objectLat('lat')
    .objectLng('lng')
    .objectAltitude('alt')
    .objectFacesSurface(false)
    .objectRotation('rotation')
    .objectThreeObject(() => makeSatelliteModel());

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
    if (button.dataset.sceneAction !== 'home') {
      const selected = button.dataset.sceneAction === 'track' ? state.tracking : !state.tracking;
      button.setAttribute('aria-pressed', String(state.ready && selected));
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
    status(`场景已就绪，当前显示${state.telemetrySourceLabel || '真实 Basilisk 遥测'}。`, 'success');
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
  state.tracking = false;
  clearTimeout(state.loadingTimer);
  state.loadingTimer = 0;
  if (state.frame) cancelAnimationFrame(state.frame);
  state.frame = 0;
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

function selectObject({ name, hasSpatialAnchor = false } = {}) {
  if (!name) return;
  const readyPrefix = state.active && state.ready
    ? (state.telemetrySourceId
      ? `场景已接入${state.telemetrySourceLabel || '真实仿真遥测'}。${state.latestTelemetryPoint
        ? `仿真时间 ${state.latestTelemetryPoint.sim_time_s.toFixed(2)} s；高度 ${state.latestTelemetryPoint.altitude_km.toFixed(2)} km。`
        : ''}`
      : '场景当前使用演示数据。') : '';
  if (!hasSpatialAnchor) {
    status(`${readyPrefix}已选择：${name}。该对象没有经过审定的三维空间锚点，场景保持当前视角，不虚构部件位置。`, 'info');
    return;
  }
  status(`${readyPrefix}已选择：${name}。对象空间锚点已登记；局部模型节点映射将在资产映射验收后启用。`, 'info');
}

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-scene-action]')?.dataset.sceneAction;
  if (action === 'track') track();
  if (action === 'free') freeCamera();
  if (action === 'home') home();
});

window.SatScene = {
  enter, exit: destroy, track, freeCamera, home, selectObject,
  updateTelemetry, setRunTelemetry, clearTelemetrySource,
  eciToGlobePoint, attitudeToGlobeQuaternion,
};
