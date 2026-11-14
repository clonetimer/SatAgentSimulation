import * as THREE from 'three';
import { GLTFLoader } from '/assets/vendor/globe/loaders/GLTFLoader.js';
const root=document.getElementById('birdsCadViewer');
const canvas=root.querySelector('canvas'),input=root.querySelector('#birdsCadFile'),status=root.querySelector('#birdsCadStatus'),tree=root.querySelector('#birdsCadTree'),info=root.querySelector('#birdsCadInspector');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;
const scene=new THREE.Scene();scene.background=new THREE.Color(0x101722);const camera=new THREE.PerspectiveCamera(42,1,.0001,1000);scene.add(new THREE.HemisphereLight(0xd9edff,0x303947,2));const key=new THREE.DirectionalLight(0xffffff,3);key.position.set(3,5,4);scene.add(key);const fill=new THREE.DirectionalLight(0x83b7e8,1.4);fill.position.set(-4,1,-3);scene.add(fill);const grid=new THREE.GridHelper(2,20,0x46576d,0x293849);scene.add(grid);const axes=new THREE.AxesHelper(.18);axes.visible=false;scene.add(axes);
let model=null,drag=null,selectionHelper=null,selectedNode=null,allNodes=[],radius=3,theta=.78,phi=1.05,target=new THREE.Vector3();const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();
function say(t,k){status.textContent=t;status.dataset.kind=k||'info';}function cam(){camera.position.set(target.x+radius*Math.sin(phi)*Math.sin(theta),target.y+radius*Math.cos(phi),target.z+radius*Math.sin(phi)*Math.cos(theta));camera.lookAt(target);}function fit(){if(!model)return;const b=new THREE.Box3().setFromObject(model),s=b.getSize(new THREE.Vector3());target.copy(b.getCenter(new THREE.Vector3()));const m=Math.max(s.x,s.y,s.z)||1;radius=m*2.4;camera.near=m/10000;camera.far=m*100;camera.updateProjectionMatrix();cam();grid.scale.setScalar(Math.max(m,.1));grid.position.y=b.min.y;}function resize(){const r=canvas.getBoundingClientRect();if(!r.width||!r.height)return;renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();}
function clear(){selectedNode=null;allNodes=[];if(selectionHelper){scene.remove(selectionHelper);selectionHelper.geometry.dispose();selectionHelper.material.dispose();selectionHelper=null;}if(model){scene.remove(model);const geometries=new Set(),materials=new Set(),textures=new Set();model.traverse(n=>{if(n.geometry)geometries.add(n.geometry);const list=Array.isArray(n.material)?n.material:n.material?[n.material]:[];list.forEach(m=>{materials.add(m);Object.values(m).forEach(v=>{if(v&&v.isTexture)textures.add(v);});});});geometries.forEach(x=>x.dispose());textures.forEach(x=>{if(x.image&&typeof x.image.close==='function')x.image.close();x.dispose();});materials.forEach(x=>x.dispose());}model=null;tree.replaceChildren();info.textContent='选择部件查看原始节点名称与层级。';}
const gltfLoader=new GLTFLoader();
async function parseGlb(buffer){
  const gltf=await gltfLoader.parseAsync(buffer,'');
  if(!gltf.scene)throw Error('GLB 中没有可显示的默认场景');
  return gltf;
}
function focusNode(n){const b=new THREE.Box3().setFromObject(n),s=b.getSize(new THREE.Vector3());target.copy(b.getCenter(new THREE.Vector3()));radius=Math.max(s.x,s.y,s.z,.001)*2.8;cam();}function select(n,focus=false){selectedNode=n;if(selectionHelper){scene.remove(selectionHelper);selectionHelper.geometry.dispose();selectionHelper.material.dispose();}const b=new THREE.Box3().setFromObject(n),s=b.getSize(new THREE.Vector3());selectionHelper=new THREE.Box3Helper(b,0x55c7ff);scene.add(selectionHelper);tree.querySelectorAll('.birds-cad-tree-node').forEach(x=>x.classList.toggle('is-selected',Number(x.dataset.nodeIndex)===n.userData.cadTreeIndex));if(focus)focusNode(n);info.textContent=(n.name||n.type)+' · '+s.x.toFixed(4)+' × '+s.y.toFixed(4)+' × '+s.z.toFixed(4)+' m';say('已选择 '+(n.name||n.type)+'。BIRDS-4 CAD 参考模型，不代表当前 Basilisk 仿真卫星实装配置。');}function makeThreeTree(modelRoot,query=''){
  tree.replaceChildren();
  allNodes=[];
  let nodeIndex=0;
  modelRoot.traverse(node=>{node.userData.cadTreeIndex=nodeIndex++;allNodes.push(node);});
  const normalized=query.trim().toLocaleLowerCase();
  const matching=new Set();
  const included=new Set();
  if(normalized){
    allNodes.forEach(node=>{if((node.name||node.type||'').toLocaleLowerCase().includes(normalized)){matching.add(node);let p=node;while(p&&p!==modelRoot){included.add(p);p=p.parent;}}});
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
    button.textContent=node.name||node.type;
    button.dataset.nodeIndex=node.userData.cadTreeIndex;
    button.addEventListener('click',()=>select(node));
    button.addEventListener('dblclick',()=>select(node,true));
    const visibility=document.createElement('button');
    visibility.type='button';visibility.className='birds-cad-visibility';
    visibility.textContent=node.visible?'隐藏':'显示';
    visibility.setAttribute('aria-pressed',String(!node.visible));
    visibility.addEventListener('click',()=>{node.visible=!node.visible;visibility.textContent=node.visible?'隐藏':'显示';visibility.setAttribute('aria-pressed',String(!node.visible));});
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
  modelRoot.children.filter(node=>!normalized||included.has(node)).forEach(node=>appendNode(node,0,tree,Boolean(normalized)));
}
async function load(buf,name){const gltf=await parseGlb(buf);clear();model=gltf.scene;model.name=name;scene.add(model);model.traverse(n=>{n.userData.cadNode=true;});makeThreeTree(model);fit();await renderer.compileAsync(scene,camera);say('已加载 '+name+' · '+allNodes.length+' 个场景节点 · 参考 CAD');}
function readFileWithProgress(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onprogress=event=>{if(event.lengthComputable)say('正在读取模型 '+Math.round(event.loaded/event.total*100)+'% · '+(event.loaded/1048576).toFixed(1)+' / '+(event.total/1048576).toFixed(1)+' MB');};reader.onerror=()=>reject(reader.error||new Error('文件读取失败'));reader.onabort=()=>reject(new Error('用户取消了文件读取'));reader.onload=()=>resolve(reader.result);reader.readAsArrayBuffer(file);});}input.addEventListener('change',async()=>{const f=input.files&&input.files[0];if(!f)return;try{if(f.size>250*1024*1024)throw Error('模型超过 250 MB 安全加载上限');say('准备读取 '+f.name+' · '+(f.size/1048576).toFixed(1)+' MB');const buffer=await readFileWithProgress(f);say('正在解析几何与装配层级…');await load(buffer,f.name);}catch(e){say('模型加载失败：'+e.message,'error');}});canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,sx:e.clientX,sy:e.clientY,button:e.button};canvas.setPointerCapture(e.pointerId);});canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.button===2){const right=new THREE.Vector3().setFromMatrixColumn(camera.matrix,0),up=new THREE.Vector3().setFromMatrixColumn(camera.matrix,1);target.addScaledVector(right,-dx*radius*.0015).addScaledVector(up,dy*radius*.0015);}else{theta-=dx*.008;phi=THREE.MathUtils.clamp(phi+dy*.008,.08,Math.PI-.08);}cam();});canvas.addEventListener('pointerup',e=>{if(!drag)return;const moved=Math.abs(e.clientX-drag.sx)+Math.abs(e.clientY-drag.sy);drag=null;if(moved<4&&model){const r=canvas.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);const hit=ray.intersectObject(model,true)[0];if(hit){let n=hit.object;while(n&&n.userData.cadTreeIndex===undefined)n=n.parent;if(n)select(n);}}});canvas.addEventListener('wheel',e=>{e.preventDefault();radius=THREE.MathUtils.clamp(radius*Math.exp(e.deltaY*.001),.002,10000);cam();},{passive:false});canvas.addEventListener('contextmenu',e=>e.preventDefault());const search=root.querySelector('#birdsCadSearch');let searchTimer=0;search.addEventListener('input',()=>{window.clearTimeout(searchTimer);searchTimer=window.setTimeout(()=>{if(model)makeThreeTree(model,search.value);},120);});root.querySelectorAll('[data-cad-tree-action]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.cadTreeAction==='restore'){allNodes.forEach(n=>n.visible=true);tree.querySelectorAll('.birds-cad-visibility').forEach(v=>{v.textContent='隐藏';v.setAttribute('aria-pressed','false');});say('已恢复全部装配节点。');}else if(selectedNode){allNodes.forEach(n=>n.visible=isDescendant(n,selectedNode)||isDescendant(selectedNode,n));say('已隔离选中部件及其子装配。');tree.querySelectorAll('.birds-cad-visibility').forEach(v=>{const idx=Number(v.parentElement.querySelector('.birds-cad-tree-node').dataset.nodeIndex);v.textContent=allNodes[idx].visible?'隐藏':'显示';v.setAttribute('aria-pressed',String(!allNodes[idx].visible));});}}));function isDescendant(node,ancestor){let p=node;while(p){if(p===ancestor)return true;p=p.parent;}return false;}root.querySelectorAll('[data-cad-view]').forEach(b=>b.addEventListener('click',()=>{const v=b.dataset.cadView;if(v==='fit')fit();else if(v==='axes')axes.visible=!axes.visible;else if(v==='home'){theta=.78;phi=1.05;fit();}else if(['x','-x','y','-y','z','-z'].includes(v)){theta=v==='x'?Math.PI/2:v==='-x'?-Math.PI/2:0;phi=v==='y'?.08:v==='-y'?Math.PI-.08:Math.PI/2;cam();}}));let viewerVisible=false,renderFrame=0;function renderLoop(){if(!viewerVisible)return;renderer.render(scene,camera);renderFrame=requestAnimationFrame(renderLoop);}function setViewerVisible(visible){viewerVisible=visible;if(viewerVisible&&!renderFrame){resize();renderFrame=requestAnimationFrame(renderLoop);}else if(!viewerVisible&&renderFrame){cancelAnimationFrame(renderFrame);renderFrame=0;}}new ResizeObserver(resize).observe(root);new IntersectionObserver(entries=>setViewerVisible(entries.some(entry=>entry.isIntersecting))).observe(root);resize();cam();window.BirdsCadViewer={load,clear,fit,select,setViewerVisible};
