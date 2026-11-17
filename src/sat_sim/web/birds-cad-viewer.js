import * as THREE from 'three';
import { GLTFLoader } from '/assets/vendor/globe/loaders/GLTFLoader.js';
const root=document.getElementById('birdsCadViewer');
const orbitMode=document.getElementById('satelliteOrbitMode');
const sceneLayout=document.querySelector('.p9d-scene-layout');
const modeTabs=document.querySelectorAll('[data-scene-mode]');
function setSceneMode(mode,syncRenderers=true){const cad=mode==='cad',simulation=mode==='simulation';orbitMode.hidden=cad||simulation;root.hidden=!cad;const simulationRoot=document.getElementById('simulationGeometryMode');if(simulationRoot)simulationRoot.hidden=!simulation;sceneLayout?.classList.toggle('is-cad-mode',cad);sceneLayout?.classList.toggle('is-simulation-mode',simulation);const title=document.getElementById('satelliteSceneTitle');if(title)title.textContent=cad?'BIRDS-4 参考 CAD':simulation?'ADCS 仿真构型':'卫星数字场景';modeTabs.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.sceneMode===mode)));if(!syncRenderers)return;if(window.BirdsCadViewer?.setViewerVisible)window.BirdsCadViewer.setViewerVisible(cad);window.SatScene?.setOrbitSceneVisible?.(!cad&&!simulation);if(simulation)window.SatScene?.setSimulationGeometryVisible?.(true);else window.SatScene?.setSimulationGeometryVisible?.(false);}
modeTabs.forEach(button=>button.addEventListener('click',()=>setSceneMode(button.dataset.sceneMode)));
// Match the initial selected tab without starting renderers in the hidden workspace.
// app.js owns renderer activation when the satellite workspace is actually entered.
const initialSceneMode=Array.from(modeTabs).find(button=>button.getAttribute('aria-pressed')==='true')?.dataset.sceneMode||'simulation';
setSceneMode(initialSceneMode,false);

const canvas=root.querySelector('canvas'),input=root.querySelector('#birdsCadFile'),status=root.querySelector('#birdsCadStatus'),tree=root.querySelector('#birdsCadTree'),info=root.querySelector('#birdsCadInspector'),reviewPanel=root.querySelector('#birdsCadReviewPanel');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;
const scene=new THREE.Scene();scene.background=new THREE.Color(0x101722);const camera=new THREE.PerspectiveCamera(42,1,.0001,1000);scene.add(new THREE.HemisphereLight(0xd9edff,0x303947,2));const key=new THREE.DirectionalLight(0xffffff,3);key.position.set(3,5,4);scene.add(key);const fill=new THREE.DirectionalLight(0x83b7e8,1.4);fill.position.set(-4,1,-3);scene.add(fill);const grid=new THREE.GridHelper(2,20,0x46576d,0x293849);scene.add(grid);const axes=new THREE.AxesHelper(.18);axes.visible=false;scene.add(axes);
let model=null,drag=null,selectionHelper=null,selectedNode=null,allNodes=[],radius=3,theta=.78,phi=1.05,target=new THREE.Vector3(),loadedGlbSha256=null,engineeringReviewPayload=null;const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();
function say(t,k){status.textContent=t;status.dataset.kind=k||'info';}
async function sha256Hex(buffer){if(!window.crypto?.subtle)return null;const digest=await window.crypto.subtle.digest('SHA-256',buffer);return Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('');}
function renderReviewPanel(){
  if(!reviewPanel)return;
  reviewPanel.replaceChildren();
  const heading=document.createElement('div');heading.className='birds-cad-review-heading';
  const titleBlock=document.createElement('div');const title=document.createElement('strong');title.textContent='工程映射审查';
  const subtitle=document.createElement('span');subtitle.textContent='候选组定位仅用于人工几何核查，不批准实装身份。';titleBlock.append(title,subtitle);
  const gate=document.createElement('span');gate.className='birds-cad-review-gate';
  const payload=engineeringReviewPayload,expected=payload?.intake?.glb_sha256;
  const digestMatch=Boolean(expected&&loadedGlbSha256&&expected===loadedGlbSha256);
  gate.dataset.state=digestMatch?'matched':payload?.ok?'mismatch':'unavailable';
  gate.textContent=digestMatch?'GLB 哈希一致':payload?.ok?'等待匹配的 GLB':'映射证据未就绪';
  heading.append(titleBlock,gate);reviewPanel.append(heading);
  const note=document.createElement('p');note.className='birds-cad-review-empty';
  if(!payload?.ok||!payload?.configured){note.textContent='尚无可用的已校验工程映射清单。';reviewPanel.append(note);return;}
  const candidates=(payload.mappings||[]).filter(row=>row.mapping_kind==='reference_cad'&&row.review_candidate);
  if(!candidates.length){note.textContent='当前映射清单没有可审查的参考 CAD 候选组。';reviewPanel.append(note);return;}
  note.textContent=digestMatch
    ? '已验证加载文件与注册清单的 SHA-256 一致。可定位候选组并检查装配层级、几何范围及局部/全局变换。'
    : '为防止节点索引错配，必须加载与注册清单 SHA-256 完全一致的 GLB；文件未匹配前不会启用候选定位。';
  reviewPanel.append(note);
  const digest=document.createElement('div');digest.className='birds-cad-review-digest';
  digest.textContent='证据 SHA-256：'+String(expected||'缺失')+(loadedGlbSha256?' · 当前文件：'+loadedGlbSha256:'');
  reviewPanel.append(digest);
  const list=document.createElement('div');list.className='birds-cad-review-list';
  for(const row of candidates){
    const candidate=row.review_candidate,card=document.createElement('article');card.className='birds-cad-review-card';
    const name=document.createElement('strong');name.textContent=row.name_zh||row.object_id;
    const meta=document.createElement('div');meta.className='birds-cad-review-meta';
    meta.textContent='组节点 #'+candidate.node_index+' · '+candidate.geometry_node_count+' 个几何节点 · '+candidate.descendant_mesh_nodes.length+' 个后代网格';
    const action=document.createElement('button');action.type='button';action.className='button button-secondary';
    action.textContent=digestMatch?'定位候选组':'等待 GLB 哈希匹配';action.disabled=!digestMatch||candidate.selector_promoted!==false||candidate.requires_visual_review!==true;
    action.addEventListener('click',()=>{if(!digestMatch||loadedGlbSha256!==expected)return;const selected=selectByIndex(candidate.node_index);if(!selected)say('当前 GLB 中找不到注册的候选节点索引。','error');});
    card.append(name,meta,action);
    if(candidate.descendant_mesh_nodes.length){
      const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='查看后代网格清单';
      const children=document.createElement('ul');children.className='birds-cad-review-descendants';
      candidate.descendant_mesh_nodes.forEach(child=>{const item=document.createElement('li');item.textContent='#'+child.node_index+' · '+child.name+' · mesh '+child.mesh_index;children.append(item);});
      details.append(summary,children);card.append(details);
    }
    list.append(card);
  }
  reviewPanel.append(list);
}
function setEngineeringReviewData(payload){engineeringReviewPayload=payload||null;renderReviewPanel();}
function selectByIndex(index){if(!loadedGlbSha256||!engineeringReviewPayload?.intake?.glb_sha256||loadedGlbSha256!==engineeringReviewPayload.intake.glb_sha256)return false;const node=allNodes.find(item=>item.userData.cadTreeIndex===index);if(!node)return false;select(node,true);return true;}
function cam(){camera.position.set(target.x+radius*Math.sin(phi)*Math.sin(theta),target.y+radius*Math.cos(phi),target.z+radius*Math.sin(phi)*Math.cos(theta));camera.lookAt(target);requestRender();}function fit(){if(!model)return;const b=new THREE.Box3().setFromObject(model),s=b.getSize(new THREE.Vector3());target.copy(b.getCenter(new THREE.Vector3()));const m=Math.max(s.x,s.y,s.z)||1;radius=m*2.4;camera.near=m/10000;camera.far=m*100;camera.updateProjectionMatrix();cam();grid.scale.setScalar(Math.max(m,.1));grid.position.y=b.min.y;}function resize(){const r=canvas.getBoundingClientRect();if(!r.width||!r.height)return;renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();requestRender();}
function clear(){selectedNode=null;allNodes=[];if(selectionHelper){scene.remove(selectionHelper);selectionHelper.geometry.dispose();selectionHelper.material.dispose();selectionHelper=null;}if(model){scene.remove(model);const geometries=new Set(),materials=new Set(),textures=new Set();model.traverse(n=>{if(n.geometry)geometries.add(n.geometry);const list=Array.isArray(n.material)?n.material:n.material?[n.material]:[];list.forEach(m=>{materials.add(m);Object.values(m).forEach(v=>{if(v&&v.isTexture)textures.add(v);});});});geometries.forEach(x=>x.dispose());textures.forEach(x=>{if(x.image&&typeof x.image.close==='function')x.image.close();x.dispose();});materials.forEach(x=>x.dispose());}model=null;loadedGlbSha256=null;tree.replaceChildren();info.textContent='选择部件查看原始节点名称与层级。';renderReviewPanel();}
const gltfLoader=new GLTFLoader();
async function parseGlb(buffer){
  const gltf=await gltfLoader.parseAsync(buffer,'');
  if(!gltf.scene)throw Error('GLB 中没有可显示的默认场景');
  return gltf;
}
function focusNode(n){const b=new THREE.Box3().setFromObject(n),s=b.getSize(new THREE.Vector3());target.copy(b.getCenter(new THREE.Vector3()));radius=Math.max(s.x,s.y,s.z,.001)*2.8;cam();}
function inspectorLine(label,value){const row=document.createElement('div');row.className='birds-cad-inspector-line';const name=document.createElement('span');name.textContent=label;const text=document.createElement('strong');text.textContent=value;row.append(name,text);return row;}
function vectorText(v,digits=4){return [v.x,v.y,v.z].map(value=>Number(value).toFixed(digits)).join(', ');}
function quaternionText(q){return [q.w,q.x,q.y,q.z].map(value=>Number(value).toFixed(5)).join(', ');}
function renderNodeInspector(n,bounds,size){
  n.updateWorldMatrix(true,false);
  const worldPosition=n.getWorldPosition(new THREE.Vector3());
  const worldQuaternion=n.getWorldQuaternion(new THREE.Quaternion());
  const worldScale=n.getWorldScale(new THREE.Vector3());
  const localPosition=n.position,localQuaternion=n.quaternion,localScale=n.scale;
  const ancestry=[];let current=n;
  while(current){ancestry.unshift(current.name||current.type);current=current.parent;}
  let meshCount=0;n.traverse(child=>{if(child.isMesh)meshCount+=1;});
  info.replaceChildren();
  const title=document.createElement('strong');title.className='birds-cad-inspector-name';title.textContent=n.name||n.type;info.append(title);
  const provenance=document.createElement('p');provenance.className='birds-cad-inspector-note';
  provenance.textContent='参考 CAD 装配坐标；尚未审定为航天器 B 系安装锚点或 as-built 实例。';
  info.append(provenance);
  const path=document.createElement('div');path.className='birds-cad-inspector-section';
  const pathTitle=document.createElement('strong');pathTitle.textContent='装配层级路径';path.append(pathTitle);
  const pathValue=document.createElement('p');pathValue.textContent=ancestry.join(' / ');path.append(pathValue);info.append(path);
  const dimensions=document.createElement('div');dimensions.className='birds-cad-inspector-section';
  const dimensionsTitle=document.createElement('strong');dimensionsTitle.textContent='几何范围（GLB 根坐标系，m）';dimensions.append(dimensionsTitle);
  dimensions.append(inspectorLine('包围盒 XYZ',vectorText(size)));
  dimensions.append(inspectorLine('网格数量',String(meshCount)));
  info.append(dimensions);
  const local=document.createElement('div');local.className='birds-cad-inspector-section';
  const localTitle=document.createElement('strong');localTitle.textContent='局部变换（父节点坐标）';local.append(localTitle);
  local.append(inspectorLine('位置 · GLB 层级单位',vectorText(localPosition)));
  local.append(inspectorLine('四元数 · w,x,y,z',quaternionText(localQuaternion)));
  local.append(inspectorLine('缩放',vectorText(localScale,5)));info.append(local);
  const world=document.createElement('div');world.className='birds-cad-inspector-section';
  const worldTitle=document.createElement('strong');worldTitle.textContent='参考装配根坐标变换';world.append(worldTitle);
  world.append(inspectorLine('位置 · m',vectorText(worldPosition)));
  world.append(inspectorLine('四元数 · w,x,y,z',quaternionText(worldQuaternion)));
  world.append(inspectorLine('缩放',vectorText(worldScale,5)));info.append(world);
}
function select(n,focus=false){
  selectedNode=n;
  if(selectionHelper){scene.remove(selectionHelper);selectionHelper.geometry.dispose();selectionHelper.material.dispose();}
  const b=new THREE.Box3().setFromObject(n),s=b.getSize(new THREE.Vector3());
  selectionHelper=new THREE.Box3Helper(b,0x55c7ff);scene.add(selectionHelper);
  tree.querySelectorAll('.birds-cad-tree-node').forEach(x=>x.classList.toggle('is-selected',Number(x.dataset.nodeIndex)===n.userData.cadTreeIndex));
  if(focus)focusNode(n);
  renderNodeInspector(n,b,s);requestRender();
  say('已选择 '+(n.name||n.type)+'。坐标属于 BIRDS-4 CAD 参考装配，不代表当前 Basilisk 仿真卫星实装配置。');
}function makeThreeTree(modelRoot,query=''){
  tree.replaceChildren();
  allNodes=[];
  let nodeIndex=0;
  modelRoot.traverse(node=>{node.userData.cadTreeIndex=nodeIndex++;allNodes.push(node);});
  const normalized=query.trim().toLocaleLowerCase();
  const included=new Set();
  if(normalized){
    allNodes.forEach(node=>{if((node.name||node.type||'').toLocaleLowerCase().includes(normalized)){let p=node;while(p&&p!==modelRoot){included.add(p);p=p.parent;}}});
  }
  function appendNode(node,depth,host,forceOpen=false){
    const children=node.children||[];
    const visibleChildren=normalized?children.filter(child=>included.has(child)):children;
    const hasChildren=visibleChildren.length>0;
    const wrap=document.createElement(hasChildren?'details':'div');
    if(hasChildren){
      wrap.className='birds-cad-tree-group';
      wrap.open=Boolean(normalized)||forceOpen;
      const summary=document.createElement('summary');
      summary.textContent=node.name||node.type;
      wrap.append(summary);
    }
    const row=document.createElement('div');
    row.className='birds-cad-tree-row';
    const button=document.createElement('button');
    button.type='button';button.className='birds-cad-tree-node';
    button.style.paddingLeft=(8+Math.min(depth,8)*10)+'px';
    button.textContent=hasChildren?'选择':(node.name||node.type);
    button.setAttribute('aria-label','选择 '+(node.name||node.type));
    button.dataset.nodeIndex=node.userData.cadTreeIndex;
    button.addEventListener('click',()=>select(node));
    button.addEventListener('dblclick',()=>select(node,true));
    const visibility=document.createElement('button');
    visibility.type='button';visibility.className='birds-cad-visibility';
    visibility.textContent=node.visible?'隐藏':'显示';
    visibility.setAttribute('aria-pressed',String(!node.visible));
    visibility.addEventListener('click',()=>{node.visible=!node.visible;visibility.textContent=node.visible?'隐藏':'显示';visibility.setAttribute('aria-pressed',String(!node.visible));requestRender();});
    row.append(button,visibility);
    (hasChildren?wrap:host).append(row);
    if(hasChildren){
      const nested=document.createElement('div');
      nested.className='birds-cad-tree-children';
      wrap.append(nested);
      let populated=false;
      const populate=()=>{
        if(populated)return;
        populated=true;
        visibleChildren.forEach(child=>appendNode(child,depth+1,nested,Boolean(normalized)));
      };
      if(normalized||wrap.open)populate();
      wrap.addEventListener('toggle',()=>{if(wrap.open)populate();});
    }
    host.append(wrap);
  }
  const roots=modelRoot.children.filter(node=>!normalized||included.has(node));
  roots.forEach(node=>appendNode(node,0,tree,Boolean(normalized)));
  if(normalized&&!roots.length){const empty=document.createElement('p');empty.className='birds-cad-tree-empty';empty.textContent='没有找到匹配的装配部件';tree.append(empty);}
}
async function load(buf,name){const digest=await sha256Hex(buf);const gltf=await parseGlb(buf);clear();model=gltf.scene;model.name=name;loadedGlbSha256=digest;scene.add(model);model.traverse(n=>{n.userData.cadNode=true;});makeThreeTree(model);fit();renderReviewPanel();await renderer.compileAsync(scene,camera);requestRender();say('已加载 '+name+' · '+allNodes.length+' 个场景节点 · SHA-256 '+(digest?'已计算':'不可用')+' · 参考 CAD');}
function readFileWithProgress(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onprogress=event=>{if(event.lengthComputable)say('正在读取模型 '+Math.round(event.loaded/event.total*100)+'% · '+(event.loaded/1048576).toFixed(1)+' / '+(event.total/1048576).toFixed(1)+' MB');};reader.onerror=()=>reject(reader.error||new Error('文件读取失败'));reader.onabort=()=>reject(new Error('用户取消了文件读取'));reader.onload=()=>resolve(reader.result);reader.readAsArrayBuffer(file);});}input.addEventListener('change',async()=>{const f=input.files&&input.files[0];if(!f)return;try{if(f.size>250*1024*1024)throw Error('模型超过 250 MB 安全加载上限');say('准备读取 '+f.name+' · '+(f.size/1048576).toFixed(1)+' MB');const buffer=await readFileWithProgress(f);say('正在解析几何与装配层级…');await load(buffer,f.name);}catch(e){say('模型加载失败：'+e.message,'error');}});canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,sx:e.clientX,sy:e.clientY,button:e.button};canvas.setPointerCapture(e.pointerId);});canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.button===2){const right=new THREE.Vector3().setFromMatrixColumn(camera.matrix,0),up=new THREE.Vector3().setFromMatrixColumn(camera.matrix,1);target.addScaledVector(right,-dx*radius*.0015).addScaledVector(up,dy*radius*.0015);}else{theta-=dx*.008;phi=THREE.MathUtils.clamp(phi+dy*.008,.08,Math.PI-.08);}cam();});canvas.addEventListener('pointerup',e=>{if(!drag)return;const moved=Math.abs(e.clientX-drag.sx)+Math.abs(e.clientY-drag.sy);drag=null;if(moved<4&&model){const r=canvas.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);const hit=ray.intersectObject(model,true)[0];if(hit){let n=hit.object;while(n&&n.userData.cadTreeIndex===undefined)n=n.parent;if(n)select(n);}}requestRender();});canvas.addEventListener('wheel',e=>{e.preventDefault();radius=THREE.MathUtils.clamp(radius*Math.exp(e.deltaY*.001),.002,10000);cam();},{passive:false});canvas.addEventListener('contextmenu',e=>e.preventDefault());const search=root.querySelector('#birdsCadSearch');let searchTimer=0;search.addEventListener('input',()=>{window.clearTimeout(searchTimer);searchTimer=window.setTimeout(()=>{if(model)makeThreeTree(model,search.value);},120);});root.querySelectorAll('[data-cad-tree-action]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.cadTreeAction==='restore'){allNodes.forEach(n=>n.visible=true);tree.querySelectorAll('.birds-cad-visibility').forEach(v=>{v.textContent='隐藏';v.setAttribute('aria-pressed','false');});requestRender();say('已恢复全部装配节点。');}else if(selectedNode){allNodes.forEach(n=>n.visible=isDescendant(n,selectedNode)||isDescendant(selectedNode,n));requestRender();say('已隔离选中部件及其子装配。');tree.querySelectorAll('.birds-cad-visibility').forEach(v=>{const idx=Number(v.parentElement.querySelector('.birds-cad-tree-node').dataset.nodeIndex);v.textContent=allNodes[idx].visible?'隐藏':'显示';v.setAttribute('aria-pressed',String(!allNodes[idx].visible));});}}));function isDescendant(node,ancestor){let p=node;while(p){if(p===ancestor)return true;p=p.parent;}return false;}root.querySelectorAll('[data-cad-view]').forEach(b=>b.addEventListener('click',()=>{const v=b.dataset.cadView;if(v==='fit')fit();else if(v==='axes'){axes.visible=!axes.visible;requestRender();}else if(v==='home'){theta=.78;phi=1.05;fit();}else if(['x','-x','y','-y','z','-z'].includes(v)){theta=v==='x'?Math.PI/2:v==='-x'?-Math.PI/2:v==='-z'?Math.PI:0;phi=v==='y'?.08:v==='-y'?Math.PI-.08:Math.PI/2;cam();}}));let viewerVisible=false,renderFrame=0;function renderLoop(){renderFrame=0;if(viewerVisible)renderer.render(scene,camera);}function requestRender(){if(viewerVisible&&!renderFrame)renderFrame=requestAnimationFrame(renderLoop);}function setViewerVisible(visible){viewerVisible=visible;if(viewerVisible){resize();requestRender();}else if(renderFrame){cancelAnimationFrame(renderFrame);renderFrame=0;}}new ResizeObserver(resize).observe(root);new IntersectionObserver(entries=>setViewerVisible(entries.some(entry=>entry.isIntersecting))).observe(root);resize();cam();function getDiagnostics(){let visibleMeshes=0,triangles=0;if(model)model.traverse(node=>{if(!node.isMesh||!node.geometry)return;let visible=true;for(let parent=node;parent;parent=parent.parent)if(!parent.visible){visible=false;break;}if(!visible)return;visibleMeshes+=1;const index=node.geometry.index;triangles+=(index?index.count:node.geometry.getAttribute('position')?.count||0)/3;});return{scene_nodes:allNodes.length,visible_meshes:visibleMeshes,triangles:Math.floor(triangles),render_calls:renderer.info.render.calls,rendered_triangles:renderer.info.render.triangles,geometries:renderer.info.memory.geometries,textures:renderer.info.memory.textures,canvas_width:canvas.width,canvas_height:canvas.height};}window.BirdsCadViewer={load,clear,fit,select,selectByIndex,setEngineeringReviewData,setViewerVisible,getDiagnostics};renderReviewPanel();
