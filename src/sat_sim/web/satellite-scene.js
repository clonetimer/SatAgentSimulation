import * as THREE from 'three';
import { GLTFLoader } from '/assets/vendor/three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from '/assets/vendor/three/examples/jsm/controls/OrbitControls.js';

const EARTH_RADIUS_M = 6371000;
const ORBIT_ALTITUDE_M = 550000;
const ORBIT_PERIOD_DEMO_S = 90;
const INCLINATION = THREE.MathUtils.degToRad(51.6);
const state = {
  active: false, renderer: null, scene: null, camera: null, controls: null,
  satellite: null, earth: null, resizeObserver: null, startTime: 0, tracking: true
};
const byId = (id) => document.getElementById(id);

function status(message, kind = 'info') {
  const element = byId('satelliteSceneStatus');
  if (!element) return;
  element.textContent = message;
  element.dataset.kind = kind;
}

function orbitPosition(phase) {
  const radius = EARTH_RADIUS_M + ORBIT_ALTITUDE_M;
  return new THREE.Vector3(
    radius * Math.cos(phase),
    radius * Math.sin(phase) * Math.sin(INCLINATION),
    radius * Math.sin(phase) * Math.cos(INCLINATION)
  );
}

function makeLabel() {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 96;
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = 'rgba(5, 12, 24, 0.78)';
  context.beginPath();
  context.roundRect(8, 8, 496, 80, 18);
  context.fill();
  context.font = 'bold 34px sans-serif';
  context.fillStyle = '#ffffff';
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.fillText('任务卫星（示意模型）', 256, 48);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(850000, 159000, 1);
  sprite.position.set(0, 300000, 0);
  return sprite;
}

function makeStars(scene) {
  const positions = [];
  for (let index = 0; index < 1800; index += 1) {
    const u = (index * 0.6180339887498949) % 1;
    const v = (index * 0.7548776662466927) % 1;
    const theta = 2 * Math.PI * u;
    const phi = Math.acos(2 * v - 1);
    const radius = EARTH_RADIUS_M * 18;
    positions.push(
      radius * Math.sin(phi) * Math.cos(theta),
      radius * Math.cos(phi),
      radius * Math.sin(phi) * Math.sin(theta)
    );
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  const material = new THREE.PointsMaterial({ color: 0xb9d6ff, size: EARTH_RADIUS_M * 0.0012, sizeAttenuation: true });
  scene.add(new THREE.Points(geometry, material));
}

function makeOrbit(scene) {
  const points = [];
  for (let index = 0; index <= 360; index += 1) {
    points.push(orbitPosition((index / 360) * Math.PI * 2));
  }
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = new THREE.LineDashedMaterial({
    color: 0xf2c14e, dashSize: 140000, gapSize: 90000, transparent: true, opacity: 0.9
  });
  const line = new THREE.Line(geometry, material);
  line.computeLineDistances();
  scene.add(line);
  return line;
}

async function createScene(host) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x030915);

  const camera = new THREE.PerspectiveCamera(42, host.clientWidth / host.clientHeight, 1000, EARTH_RADIUS_M * 80);
  camera.position.set(EARTH_RADIUS_M * 1.8, EARTH_RADIUS_M * 1.15, EARTH_RADIUS_M * 2.6);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(host.clientWidth, host.clientHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  renderer.domElement.setAttribute('aria-label', '三维地球、卫星与演示轨道');
  host.replaceChildren(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.065;
  controls.minDistance = EARTH_RADIUS_M * 1.04;
  controls.maxDistance = EARTH_RADIUS_M * 18;
  controls.target.set(0, 0, 0);
  controls.update();

  scene.add(new THREE.AmbientLight(0x8ba6c7, 1.0));
  const sun = new THREE.DirectionalLight(0xfff0d6, 2.2);
  sun.position.set(EARTH_RADIUS_M * 5, EARTH_RADIUS_M * 3, EARTH_RADIUS_M * 2);
  scene.add(sun);

  const textureLoader = new THREE.TextureLoader();
  const [west, east, gltf] = await Promise.all([
    textureLoader.loadAsync('/assets/vendor/earth/naturalearth-ii-west.jpg'),
    textureLoader.loadAsync('/assets/vendor/earth/naturalearth-ii-east.jpg'),
    new GLTFLoader().loadAsync('/assets/satellite-demo.gltf')
  ]);
  const mapCanvas = document.createElement('canvas');
  mapCanvas.width = west.image.width + east.image.width;
  mapCanvas.height = Math.max(west.image.height, east.image.height);
  const mapContext = mapCanvas.getContext('2d');
  mapContext.drawImage(west.image, 0, 0);
  mapContext.drawImage(east.image, west.image.width, 0);
  const earthTexture = new THREE.CanvasTexture(mapCanvas);
  earthTexture.colorSpace = THREE.SRGBColorSpace;
  earthTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();

  const earth = new THREE.Mesh(
    new THREE.SphereGeometry(EARTH_RADIUS_M, 128, 80),
    new THREE.MeshStandardMaterial({ map: earthTexture, roughness: 0.96, metalness: 0.0 })
  );
  earth.rotation.y = Math.PI;
  scene.add(earth);

  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(EARTH_RADIUS_M * 1.014, 96, 64),
    new THREE.MeshBasicMaterial({
      color: 0x4a9fe5, transparent: true, opacity: 0.105, side: THREE.BackSide, depthWrite: false
    })
  );
  scene.add(atmosphere);

  makeStars(scene);
  makeOrbit(scene);

  const satellite = new THREE.Group();
  const model = gltf.scene;
  model.scale.setScalar(25000);
  satellite.add(model);
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(18000, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0xffd166 })
  );
  satellite.add(marker);
  satellite.add(makeLabel());
  scene.add(satellite);

  state.scene = scene;
  state.camera = camera;
  state.renderer = renderer;
  state.controls = controls;
  state.earth = earth;
  state.satellite = satellite;
  state.startTime = performance.now();
  state.tracking = true;
  return { scene, camera, renderer, controls, satellite, earth };
}

function animate(now) {
  if (!state.active || !state.renderer) return;
  const elapsed = (now - state.startTime) / 1000;
  const phase = (elapsed / ORBIT_PERIOD_DEMO_S) * Math.PI * 2;
  const position = orbitPosition(phase);
  const next = orbitPosition(phase + 0.002);
  state.satellite.position.copy(position);
  state.satellite.lookAt(next);
  state.earth.rotation.y += 0.000018;
  if (state.tracking) state.controls.target.copy(position);
  state.controls.update();
  state.renderer.render(state.scene, state.camera);
}

function destroy() {
  state.active = false;
  if (state.resizeObserver) state.resizeObserver.disconnect();
  state.resizeObserver = null;
  if (state.renderer) {
    state.renderer.setAnimationLoop(null);
    state.renderer.dispose();
    state.renderer.domElement.remove();
  }
  state.controls?.dispose();
  state.scene?.traverse((object) => {
    if (object.geometry) object.geometry.dispose();
    if (object.material) {
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach((material) => {
        Object.values(material).forEach((value) => {
          if (value && value.isTexture) value.dispose();
        });
        material.dispose();
      });
    }
  });
  Object.assign(state, {
    renderer: null, scene: null, camera: null, controls: null,
    satellite: null, earth: null
  });
}

async function enter() {
  if (state.active && state.renderer) return;
  state.active = true;
  const host = byId('satelliteSceneCanvas');
  if (!host) return;
  try {
    status('正在加载本地地球影像与卫星模型…');
    await createScene(host);
    state.resizeObserver = new ResizeObserver(() => {
      if (!state.renderer || !state.camera) return;
      const width = Math.max(1, host.clientWidth);
      const height = Math.max(1, host.clientHeight);
      state.camera.aspect = width / height;
      state.camera.updateProjectionMatrix();
      state.renderer.setSize(width, height);
    });
    state.resizeObserver.observe(host);
    state.renderer.setAnimationLoop(animate);
    status('场景已就绪。地球影像和卫星模型来自本地资源；轨道与运动为演示数据，不代表真实仿真遥测。', 'success');
  } catch (error) {
    destroy();
    status('三维场景加载失败，仿真工作台其他功能不受影响。' + (error?.message ? ' ' + error.message : ''), 'error');
  }
}

function track() {
  if (!state.satellite || !state.controls) return;
  state.tracking = true;
  state.controls.target.copy(state.satellite.position);
}
function freeCamera() {
  state.tracking = false;
}
function home() {
  if (!state.camera || !state.controls) return;
  state.tracking = false;
  state.controls.target.set(0, 0, 0);
  state.camera.position.set(EARTH_RADIUS_M * 1.8, EARTH_RADIUS_M * 1.15, EARTH_RADIUS_M * 2.6);
  state.controls.update();
}

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-scene-action]')?.dataset.sceneAction;
  if (action === 'track') track();
  if (action === 'free') freeCamera();
  if (action === 'home') home();
});

window.SatScene = { enter, exit: destroy, track, freeCamera, home };
