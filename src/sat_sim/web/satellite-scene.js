import * as THREE from 'three';
import { inertialPositionToEarthFixed } from './realtime-orbit.mjs';

const EARTH_ROTATION_S = 86164.0905;
const ORBIT_PERIOD_S = 5730;
const ORBIT_ALTITUDE_KM = 550;
const EARTH_RADIUS_KM = 6371;
const INCLINATION = THREE.MathUtils.degToRad(51.6);
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
  mode: 'demo',
  liveSession: null,
  liveLastSequence: -1,
  liveTrail: [],
  pendingLiveFrame: null,
  lastLiveSimTimeS: null
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

function buildOrbitPath() {
  const points = [];
  for (let index = 0; index <= 240; index += 1) {
    points.push(orbitPoint((index / 240) * Math.PI * 2));
  }
  return [{ name: '演示轨道（非仿真结果）', points }];
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
  const satellite = state.mode === 'demo'
    ? { name: '任务卫星（演示数据）', ...orbitPoint(0) }
    : null;
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
    .pathsData(state.mode === 'demo' ? buildOrbitPath() : [])
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
    .objectsData(satellite ? [satellite] : [])
    .objectLabel('name')
    .objectLat('lat')
    .objectLng('lng')
    .objectAltitude('alt')
    .objectFacesSurface(false)
    .objectThreeObject(() => makeSatelliteModel());

  const controls = globe.controls();
  controls.autoRotate = false;
  controls.enableDamping = true;
  controls.minDistance = globe.getGlobeRadius() * 1.08;
  controls.maxDistance = globe.getGlobeRadius() * 18;
  globe.pointOfView(satellite ? overviewPoint(satellite) : { lat: 0, lng: 0, altitude: 2.8 }, 0);
  controls.addEventListener('start', freeCamera);
  return globe;
}

function updateSatellite(now) {
  if (!state.active || !state.ready || !state.globe || !state.satellite || state.mode !== 'demo') return;
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
  state.frame = requestAnimationFrame(updateSatellite);
}

function syncCameraActions() {
  document.querySelectorAll('#satelliteSceneView [data-scene-action]').forEach((button) => {
    button.disabled = !state.ready || (button.dataset.sceneAction === 'track' && !state.satellite);
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
  syncCameraActions();
  if (state.mode === 'realtime') {
    status('三维场景已就绪，正在等待当前实时会话的有效轨道遥测。', 'info');
    if (state.pendingLiveFrame) {
      const pending = state.pendingLiveFrame;
      state.pendingLiveFrame = null;
      applyRealtimeFrame(pending);
    }
  } else {
    state.frame = requestAnimationFrame(updateSatellite);
    status('场景已就绪。当前轨道与卫星位置为演示数据，不代表真实 Basilisk 仿真遥测。', 'success');
  }
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
  state.globe.pointOfView(state.satellite ? overviewPoint(state.satellite) : { lat: 0, lng: 0, altitude: 2.8 }, 0);
  syncCameraActions();
}

function selectObject({ name, hasSpatialAnchor = false } = {}) {
  if (!name) return;
  const readyPrefix = state.mode === 'realtime' && state.liveSession
    ? `实时仿真（${state.liveSession.modeLabel}） · ${state.liveSession.sessionId}。 `
    : state.active && state.ready ? '场景已就绪（演示数据）。' : '';
  if (!hasSpatialAnchor) {
    status(`${readyPrefix}已选择：${name}。该对象没有经过审定的局部三维资产锚点，不虚构部件坐标。`, 'info');
    return;
  }
  status(`${readyPrefix}已选择：${name}。对象空间锚点已登记；局部模型节点映射将在资产映射验收后启用。`, 'info');
}

function applyRealtimeFrame(payload) {
  const session = state.liveSession;
  const frame = payload?.frame;
  if (!session || !frame || frame.session_id !== session.sessionId ||
      (session.streamId && frame.stream !== session.streamId)) return false;
  if (frame.quality !== 'GOOD') {
    status(`实时遥测质量为 ${frame.quality || 'UNKNOWN'}；保留最后一个有效位置，不用无效帧更新场景。`, 'warning');
    return false;
  }
  if (!Number.isInteger(frame.sequence) || frame.sequence <= state.liveLastSequence) return false;
  const values = frame.values || {};
  const positionM = [
    values['orbit.position_m_x'],
    values['orbit.position_m_y'],
    values['orbit.position_m_z'],
  ];
  try {
    const earthFixed = inertialPositionToEarthFixed({
      positionM,
      epochUtc: session.epochUtc,
      simTimeS: frame.sim_time_s,
      timeSystem: session.timeSystem,
      positionFrame: session.positionFrame,
    });
    state.liveLastSequence = frame.sequence;
    state.lastLiveSimTimeS = frame.sim_time_s;
    state.satellite = {
      name: `真实仿真卫星 · ${session.sessionId}`,
      lat: earthFixed.latitudeDeg,
      lng: earthFixed.longitudeDeg,
      alt: earthFixed.altitudeRatio,
    };
    state.liveTrail.push({lat: earthFixed.latitudeDeg, lng: earthFixed.longitudeDeg, alt: earthFixed.altitudeRatio});
    if (state.liveTrail.length > 360) state.liveTrail.splice(0, state.liveTrail.length - 360);
    if (state.globe && state.ready) {
      state.globe.objectsData([state.satellite]);
      state.globe.pathsData([{
        name: `实时遥测轨迹 · ${session.sessionId}`,
        points: state.liveTrail.slice(),
      }]);
      if (state.tracking) {
        state.globe.pointOfView({lat: state.satellite.lat, lng: state.satellite.lng, altitude: 1.8}, 0);
      }
    }
    syncCameraActions();
    status(
      `真实遥测 · ${session.modeLabel} · ${session.sessionId} · ${frame.stream} · t=${Number(frame.sim_time_s).toFixed(2)} s · 序号 ${frame.sequence}。位置由 ${session.positionFrame} 经 GMST 近似换算至地固坐标，仅用于场景可视化。`,
      'success'
    );
    return true;
  } catch (error) {
    status(`实时轨道定位不可用：${error.message}。不以演示轨道替代真实位置。`, 'warning');
    return false;
  }
}

function setRealtimeSession({
  sessionId, mode = 'bounded', epochUtc, timeSystem = 'UTC',
  positionFrame, streamId = null, sessionState = 'RUNNING'
} = {}) {
  if (!sessionId) return clearRealtimeSession();
  const modeLabel = mode === 'continuous' ? '持续实时' : '限时实时';
  const next = {sessionId: String(sessionId), mode, modeLabel, epochUtc, timeSystem, positionFrame, streamId, sessionState};
  const changed = !state.liveSession || state.liveSession.sessionId !== next.sessionId ||
    state.liveSession.streamId !== next.streamId || state.liveSession.epochUtc !== next.epochUtc;
  state.mode = 'realtime';
  state.liveSession = next;
  if (changed) {
    state.liveLastSequence = -1;
    state.liveTrail = [];
    state.pendingLiveFrame = null;
    state.lastLiveSimTimeS = null;
    state.satellite = null;
    if (state.globe && state.ready) {
      state.globe.objectsData([]);
      state.globe.pathsData([]);
    }
  }
  if (state.active && state.ready && changed) {
    status(`已连接实时会话 ${next.sessionId}（${modeLabel}），等待包含完整三轴位置的有效遥测。`, 'info');
    syncCameraActions();
  }
}

function updateRealtimeFrame(payload) {
  if (!state.liveSession || payload?.sessionId !== state.liveSession.sessionId) return false;
  const frame = payload.frame;
  if (!frame || frame.session_id !== state.liveSession.sessionId ||
      (state.liveSession.streamId && frame.stream !== state.liveSession.streamId)) return false;
  if (!state.ready || !state.globe) {
    state.pendingLiveFrame = payload;
    return true;
  }
  return applyRealtimeFrame(payload);
}

function clearRealtimeSession() {
  state.mode = 'demo';
  state.liveSession = null;
  state.liveLastSequence = -1;
  state.liveTrail = [];
  state.pendingLiveFrame = null;
  state.lastLiveSimTimeS = null;
  state.satellite = {name: '任务卫星（演示数据）', ...orbitPoint(0)};
  if (state.globe && state.ready) {
    state.globe.objectsData([state.satellite]);
    state.globe.pathsData(buildOrbitPath());
    state.startedAt = performance.now();
    state.lastUpdateAt = 0;
    if (!state.frame) state.frame = requestAnimationFrame(updateSatellite);
    status('已退出实时场景，当前显示恢复为演示数据；不代表真实 Basilisk 仿真遥测。', 'info');
  }
  syncCameraActions();
}

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-scene-action]')?.dataset.sceneAction;
  if (action === 'track') track();
  if (action === 'free') freeCamera();
  if (action === 'home') home();
});

window.SatScene = { enter, exit: destroy, track, freeCamera, home, selectObject, setRealtimeSession, updateRealtimeFrame, clearRealtimeSession };
