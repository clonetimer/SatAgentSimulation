/* DT-4.1 display-only BIRDS-4 RW outline. The actual simulation inputs and
 * all Basilisk dynamics belong to the existing validated TaskSpec runtime. */
import * as THREE from 'three';
import { GLTFLoader } from '/assets/vendor/globe/loaders/GLTFLoader.js';

const SOURCE_SHA256 = '3019d97d4990553bcfab39d9cbe0ea15bad9a294f517481b65df1ec71ce4d795';
const SOURCE_SIZE = 1410016;
const SOURCE_URL = '/assets/reference-cad/birds4/rw.glb';
const EXPECTED_NODE_COUNT = 25;
const EXPECTED_MESH_COUNT = 24;
const SOURCE_NAME = 'B4_RW_Assembly_v55.glb';
const byId = id => document.getElementById(id);
const sceneApi = window.SatScene;
const control = byId('simulationRwReferenceToggle');
const fileInput = byId('simulationRwReferenceFile');
const statusNode = byId('simulationRwReferenceStatus');

let source = null;
let approvedGeometry = null;
let layer = null;
let layerScene = null;
let mounts = [];
let rotorMarkers = [];
let active = false;
let viewportVisible = Boolean(sceneApi?.isRwReferenceViewportVisible?.());
let exploded = false;
let loadingToken = 0;
let assetDigest = null;

function status(message, kind = 'info') {
  if (statusNode) {
    statusNode.textContent = message;
    statusNode.dataset.state = kind;
  }
}
function syncControl() {
  if (control) {
    control.setAttribute('aria-pressed', String(active));
    control.textContent = active ? '关闭 RW 参考外形' : '显示 RW 参考外形';
    control.disabled = !approvedGeometry;
  }
}
function requestRender() { sceneApi?.requestRwReferenceRender?.(); }
function clearMounts() {
  if (layer) layer.clear();
  mounts = [];
  rotorMarkers = [];
  requestRender();
}
function ensureLayer() {
  const host = sceneApi?.getRwReferenceDisplayScene?.();
  if (!host) return false;
  if (layer && layerScene !== host) {
    layerScene?.remove(layer);
    layer = null;
  }
  if (!layer) {
    layer = new THREE.Group();
    layer.name = 'DT41_BIRDS4_RW_REFERENCE_ONLY';
    layer.userData.referenceOnly = true;
    host.add(layer);
    layerScene = host;
  }
  return true;
}
function updateVisibility() {
  if (layer) layer.visible = Boolean(active && viewportVisible && approvedGeometry);
  requestRender();
}
function updateOffsets() {
  if (!approvedGeometry) return;
  mounts.forEach((group, index) => {
    const p = approvedGeometry.positions[index];
    if (exploded) {
      const angle = index * Math.PI * 2 / approvedGeometry.count;
      group.position.set(p[0] + 0.09 * Math.cos(angle),
        p[1] + (index % 2 === 0 ? 0.045 : -0.045),
        p[2] + 0.09 * Math.sin(angle));
    } else group.position.set(...p);
  });
  requestRender();
}
function disposeSource() {
  clearMounts();
  if (!source) return;
  const geometries = new Set(), materials = new Set();
  source.traverse(node => {
    if (node.geometry) geometries.add(node.geometry);
    const mats = Array.isArray(node.material) ? node.material : node.material ? [node.material] : [];
    mats.forEach(m => materials.add(m));
  });
  geometries.forEach(g => g.dispose());
  materials.forEach(m => m.dispose());
  source = null;
  assetDigest = null;
}
function addRotorIndicator(aligned) {
  // Visual-only angular phase pointer: no CAD part or supporting structure is
  // spun. The +Y source-axis contract is independent of mesh topology.
  const rotor = new THREE.Group();
  rotor.name = 'RW_VISUAL_PHASE_POINTER_NOT_HARDWARE';
  const material = new THREE.MeshBasicMaterial({color:0x63e6f1,depthTest:false});
  const bead = new THREE.Mesh(new THREE.SphereGeometry(0.0022,10,8), material);
  bead.position.set(0.018,0.017,0);
  rotor.add(bead);
  const tail = new THREE.Mesh(new THREE.SphereGeometry(0.0012,8,6), material);
  tail.position.set(-0.018,0.017,0);
  rotor.add(tail);
  aligned.add(rotor);
  return rotor;
}
function rebuild() {
  clearMounts();
  if (!approvedGeometry || !source || !ensureLayer()) return false;
  const bounds = new THREE.Box3().setFromObject(source);
  if (bounds.isEmpty()) return false;
  const center = bounds.getCenter(new THREE.Vector3());
  for (let index = 0; index < approvedGeometry.count; index += 1) {
    const p = approvedGeometry.positions[index];
    const axis = approvedGeometry.axes[index];
    const mount = new THREE.Group();
    mount.name = 'TaskSpec_RW_' + (index + 1) + '_REFERENCE_COPY';
    mount.position.set(...p);
    mount.userData = {referenceOnly:true, origin:'TaskSpec', notAsBuilt:true, index};
    const aligned = new THREE.Group();
    aligned.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),new THREE.Vector3(...axis));
    mount.add(aligned);
    // Center only the *visual asset* in its own frame; this must not be read
    // back as an installation point, shaft datum or spacecraft-B transform.
    const copy = source.clone(true);
    copy.position.sub(center);
    aligned.add(copy);
    rotorMarkers.push(addRotorIndicator(aligned));
    layer.add(mount);
    mounts.push(mount);
  }
  updateOffsets();
  updateVisibility();
  return true;
}
async function decodePinnedGlb(buffer) {
  if (!globalThis.crypto?.subtle) throw Error('Web Crypto 不可用，无法验证文件完整性');
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength !== SOURCE_SIZE)
    throw Error('参考外形文件大小与固定工件不一致');
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256',buffer))]
    .map(byte => byte.toString(16).padStart(2,'0')).join('');
  if (digest !== SOURCE_SHA256) throw Error('参考外形 SHA-256 与固定工件不一致');
  const head = new DataView(buffer);
  if (head.getUint32(0,true) !== 0x46546c67 || head.getUint32(4,true) !== 2
      || head.getUint32(8,true) !== SOURCE_SIZE)
    throw Error('固定工件不是完整的 glTF 2.0 binary');
  const parsed = await new GLTFLoader().parseAsync(buffer,'');
  if (!parsed.scene || parsed.parser?.json?.nodes?.length !== EXPECTED_NODE_COUNT
      || parsed.parser?.json?.meshes?.length !== EXPECTED_MESH_COUNT)
    throw Error('参考外形装配节点或 mesh 数与固定工件不符');
  let meshCount = 0;
  parsed.scene.traverse(node => {if (node.isMesh) meshCount += 1;});
  if (meshCount !== EXPECTED_MESH_COUNT)
    throw Error('GLB 可渲染 mesh 数不一致');
  return {model:parsed.scene,digest};
}
async function loadBuffer(buffer, token, displayName) {
  const result = await decodePinnedGlb(buffer);
  if (token !== loadingToken) {
    result.model.traverse(node => {
      node.geometry?.dispose?.();
      const mats = Array.isArray(node.material) ? node.material : node.material ? [node.material] : [];
      mats.forEach(m => m.dispose?.());
    });
    return false;
  }
  disposeSource();
  source = result.model;
  assetDigest = result.digest;
  active = true;
  syncControl();
  rebuild();
  status('已验证 ' + displayName + ' · 25 nodes / 24 meshes · ' +
    approvedGeometry.count + ' 只轮的外形复制布局来自 TaskSpec，非 CAD 实装。', 'verified');
  return true;
}
async function activateFromServer() {
  const token = ++loadingToken;
  status('正在读取并校验本地固定 RW 子装配 GLB…');
  try {
    const response = await fetch(SOURCE_URL,{cache:'no-store'});
    if (!response.ok) throw Error(response.status === 404
      ? '本地未部署固定 RW 子装配。请从指定 CI artifact 仅提取 build/birds4/output/B4_RW_Assembly_v55.glb，或手动选择同一 GLB。'
      : '固定 RW 子装配读取失败（HTTP ' + response.status + '）');
    await loadBuffer(await response.arrayBuffer(),token,SOURCE_NAME);
  } catch (error) {
    if (token !== loadingToken) return;
    active = false;
    clearMounts();
    syncControl();
    updateVisibility();
    status(String(error.message || error), 'error');
  }
}
function setGeometry(geometry) {
  loadingToken += 1;
  approvedGeometry = geometry && Number.isInteger(geometry.count)
    && (geometry.count === 3 || geometry.count === 4)
    && geometry.positions?.length === geometry.count
    && geometry.axes?.length === geometry.count ? geometry : null;
  active = false;
  exploded = false;
  clearMounts();
  syncControl();
  updateVisibility();
  status(approvedGeometry
    ? '参考图层关闭；可按需加载固定 BIRDS-4 RW 子装配。安装位置和轴向仅来自已校验 TaskSpec。'
    : '缺少已校验的整星 TaskSpec；不显示参考外形或安装位置。');
}
function setPhases(phases) {
  if (!active || !Array.isArray(phases)) return;
  rotorMarkers.forEach((marker,index) => {
    const phase = phases[index];
    // Phase is already integrated from strict same-session telemetry time
    // (equivalent to rotation.y += omega*dt for piecewise constant speed).
    // A missing/invalid sample resets the visualization instead of inventing motion.
    marker.rotation.y = typeof phase === 'number' && Number.isFinite(phase) ? phase : 0;
  });
  requestRender();
}
function setVisible(value) {viewportVisible = Boolean(value); updateVisibility();}
function setExploded(value) {exploded = Boolean(value); updateOffsets();}
function diagnostics() {
  return {sha256:assetDigest,source_name:assetDigest ? SOURCE_NAME : null,
    verified:assetDigest === SOURCE_SHA256, layer_enabled:active,
    layer_visible:Boolean(layer?.visible), instance_count:mounts.length,
    mesh_count_per_instance:source ? EXPECTED_MESH_COUNT : 0,
    instance_origins:mounts.map(m=>m.userData.origin),
    installed_b_positions_from_taskspec:approvedGeometry?.positions?.map(p=>[...p]) || [],
    spin_axes_b_from_taskspec:approvedGeometry?.axes?.map(a=>[...a]) || [],
    axis_source:'validated_TaskSpec_rw_axis_b',
    position_source:'validated_TaskSpec_rw_mount_b',
    world_transform_m_used:false,as_built_approved:false,
    rotor_visual_phases_rad:rotorMarkers.map(marker=>marker.rotation.y)};
}
control?.addEventListener('click',() => {
  if (active) {
    loadingToken += 1;
    active = false;
    clearMounts();
    syncControl();
    updateVisibility();
    status('参考图层关闭。Basilisk 物理输入及现有仿真几何不变。');
    return;
  }
  if (!approvedGeometry) {status('请先完成整星 TaskSpec 校验。','error');return;}
  if (source) {
    active = true;
    rebuild();
    syncControl();
    status('固定 BIRDS-4 子装配已启用；复制安装布局来自 TaskSpec，非 CAD 实装。','verified');
  } else activateFromServer();
});
fileInput?.addEventListener('change',async () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  if (!approvedGeometry) {status('请先完成整星 TaskSpec 校验。','error');return;}
  const token = ++loadingToken;
  active = false;
  clearMounts();
  syncControl();
  status('正在核验所选本地 RW 子装配 SHA-256…');
  try {
    if (file.name !== SOURCE_NAME) throw Error('仅允许 B4_RW_Assembly_v55.glb');
    await loadBuffer(await file.arrayBuffer(),token,file.name);
  } catch (error) {
    if (token !== loadingToken) return;
    updateVisibility();
    status(String(error.message || error),'error');
  } finally {
    fileInput.value = '';
  }
});
window.RwReferenceOverlay = {setGeometry,setPhases,setVisible,setExploded,getDiagnostics:diagnostics};
setGeometry(sceneApi?.getRwReferenceGeometry?.() || null);
setVisible(sceneApi?.isRwReferenceViewportVisible?.());
