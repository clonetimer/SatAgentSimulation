import * as THREE from 'three';

const EARTH_ROTATION_S = 86164.0905;
const ORBIT_PERIOD_S = 5730;
const ORBIT_ALTITUDE_KM = 550;
const EARTH_RADIUS_KM = 6371;
const INCLINATION = THREE.MathUtils.degToRad(51.6);
const state = {
  active: false,
  globe: null,
  satellite: null,
  resizeObserver: null,
  startedAt: 0,
  lastUpdateAt: 0,
  tracking: false,
  frame: 0
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

function createGlobe(host) {
  if (typeof window.Globe !== 'function') {
    throw new Error('Globe.gl 本地运行时未加载。');
  }
  const width = Math.max(1, host.clientWidth);
  const height = Math.max(1, host.clientHeight);
  const satellite = { name: '任务卫星（示意模型）', ...orbitPoint(0) };
  const globe = new window.Globe(host, {
    animateIn: false,
    waitForGlobeReady: true,
    rendererConfig: { antialias: true, alpha: false, powerPreference: 'high-performance' }
  })
    .width(width)
    .height(height)
    .backgroundColor('#030915')
    .globeImageUrl('/assets/vendor/globe/textures/earth-blue-marble.jpg')
    .bumpImageUrl('/assets/vendor/globe/textures/earth-topology.png')
    .showGraticules(true)
    .showAtmosphere(true)
    .atmosphereColor('#4a9fe5')
    .atmosphereAltitude(0.14)
    .pathsData(buildOrbitPath())
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
    .objectThreeObject(() => makeSatelliteModel());

  const controls = globe.controls();
  controls.autoRotate = false;
  controls.enableDamping = true;
  controls.minDistance = globe.getGlobeRadius() * 1.08;
  controls.maxDistance = globe.getGlobeRadius() * 18;
  globe.pointOfView({ lat: 18, lng: 120, altitude: 2.8 }, 0);
  state.globe = globe;
  state.satellite = satellite;
  return globe;
}

function updateSatellite(now) {
  if (!state.active || !state.globe || !state.satellite) return;
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

function destroy() {
  state.active = false;
  if (state.frame) cancelAnimationFrame(state.frame);
  state.frame = 0;
  if (state.resizeObserver) state.resizeObserver.disconnect();
  state.resizeObserver = null;
  if (state.globe) {
    state.globe.pauseAnimation();
    state.globe.controls()?.dispose?.();
    state.globe.renderer()?.dispose?.();
    const canvas = state.globe.renderer()?.domElement;
    canvas?.remove();
  }
  const host = byId('satelliteSceneCanvas');
  host?.replaceChildren();
  state.globe = null;
  state.satellite = null;
}

async function enter() {
  if (state.active && state.globe) return;
  state.active = true;
  const host = byId('satelliteSceneCanvas');
  if (!host) return;
  try {
    status('正在加载离线地球影像与三维卫星模型…');
    createGlobe(host);
    state.startedAt = performance.now();
    state.lastUpdateAt = 0;
    state.tracking = false;
    state.resizeObserver = new ResizeObserver(() => {
      if (!state.globe) return;
      state.globe.width(Math.max(1, host.clientWidth));
      state.globe.height(Math.max(1, host.clientHeight));
    });
    state.resizeObserver.observe(host);
    state.frame = requestAnimationFrame(updateSatellite);
    status('场景已就绪。当前轨道与卫星位置为演示数据，不代表真实 Basilisk 仿真遥测。', 'success');
  } catch (error) {
    destroy();
    status('三维场景加载失败，仿真工作台其他功能不受影响。' + (error?.message ? ' ' + error.message : ''), 'error');
  }
}

function track() {
  if (!state.globe || !state.satellite) return;
  state.tracking = true;
  state.globe.pointOfView({
    lat: state.satellite.lat,
    lng: state.satellite.lng,
    altitude: 1.8
  }, 600);
}

function freeCamera() {
  state.tracking = false;
}

function home() {
  if (!state.globe) return;
  state.tracking = false;
  state.globe.pointOfView({ lat: 18, lng: 120, altitude: 2.8 }, 800);
}

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-scene-action]')?.dataset.sceneAction;
  if (action === 'track') track();
  if (action === 'free') freeCamera();
  if (action === 'home') home();
});

window.SatScene = { enter, exit: destroy, track, freeCamera, home };
