import {applyPose,poseMatrix,fittingParent} from './pivot-editor.js';
import * as THREE from 'three';
import {t} from './i18n.js';
import {previewMesh} from './model-geometry.js';

const labels={1:'Lanzador de humo simple',2:'Lanzador de humo doble',3:'Lanzador de humo triple'};
export function smokeEditor({state,$,groups,scene,job,loading,remember,selectEdit,refreshCheck,reportError,applyShow,active=()=>state.edit==='smoke_launchers'}){
  const group=groups.smoke_launchers=new THREE.Group();scene.add(group);
  let hosts=[],selected=0,revision=0,candidate=null,opened=false,candidateRevision=0;
  const cache=new Map(),rows=()=>state.transform.smoke_launchers||[];
  const pose=()=>rows()[selected]||{position:[0,0,0],rotation:[0,0,0]};
  const target=()=>group.children.find(n=>n.userData.smokeIndex===selected)?.children[0];
  const parentName=row=>fittingParent(row,state.check?.commander_bones);
  const matrix=value=>new THREE.Matrix4().set(...value.flat());
  function dispose(node){node.traverse(n=>{n.geometry?.dispose();for(const m of new Set([...([n.material].flat()),n.userData?.flat,n.userData?.untextured]))if(m&&!m.userData?.shared){m.map?.dispose();m.dispose();}});node.removeFromParent();}
  function clear(){for(const n of [...group.children])dispose(n);candidate=null;}
  function sync(){for(const host of group.children){const row=host.userData.candidate?candidate:rows()[host.userData.smokeIndex];
    if(!row)continue;const parent=hosts.find(p=>p.name===parentName(row));host.visible=!!parent;
    if(parent)host.matrix.copy(matrix(parent.matrix));
    applyPose(host.children[0],row);
    host.traverse(node=>{if(node.userData.placementGuide)node.visible=active();});
  }group.updateMatrixWorld(true);}
  function controls(){const ready=!!(state.hull&&state.turret),busy=state.busy;
    $('smoke-load').disabled=busy||!ready;$('smoke-fields').classList.toggle('hidden',!opened||!ready);
    for(const id of ['smoke-tubes','smoke-parent','smoke-list'])$(id).disabled=busy||!ready;
    $('smoke-add').disabled=busy||!ready||!candidate;
    for(const id of ['smoke-remove','smoke-edit','smoke-duplicate'])$(id).disabled=busy||!rows()[selected];
  }
  function render(){selected=Math.max(0,Math.min(selected,rows().length-1));
    $('smoke-list').replaceChildren();rows().forEach((r,i)=>$('smoke-list').add(new Option((i+1)+'. '+t(labels[r.tubes]),String(i))));$('smoke-list').value=String(selected);
    const parent=parentName(candidate)||parentName(rows()[selected])||$('smoke-parent').value;
    $('smoke-parent').replaceChildren();for(const h of hosts)$('smoke-parent').add(new Option(h.name,h.name));
    if(hosts.some(p=>p.name===parent))$('smoke-parent').value=parent;controls();}
  async function show(row,index,ghost=false){if(!cache.has(row.tubes))cache.set(row.tubes,await job('/api/smoke-preview',{tubes:row.tubes}));
    const data=cache.get(row.tubes),host=new THREE.Group(),fitting=new THREE.Group();host.matrixAutoUpdate=false;
    host.userData=ghost?{candidate:true}:{smokeIndex:index};host.add(fitting);
    for(const mesh of data.meshes){const texture=new THREE.DataTexture(new Uint8Array(data.texture.rgba),data.texture.width,data.texture.height,THREE.RGBAFormat);
      texture.colorSpace=THREE.SRGBColorSpace;texture.flipY=true;texture.needsUpdate=true;
      fitting.add(previewMesh(mesh,new THREE.MeshStandardMaterial({color:ghost?0x77bf94:0xffffff,map:texture,
        roughness:.9,metalness:.08,wireframe:state.wire,transparent:ghost,opacity:ghost ? .55 : 1})));}
    // Directions are placement aids only; portraits come from the compiled model.
    data.muzzles.forEach((p,i)=>{const arrow=new THREE.ArrowHelper(new THREE.Vector3(...data.directions[i]),new THREE.Vector3(...p),8,0xd8d8b0,1.4,.6);arrow.userData.placementGuide=true;fitting.add(arrow);});
    return host;
  }
  async function restore(){const version=++revision;clear();if(!rows().length){render();return;}
    opened=true;const value=JSON.stringify(rows()),objects=[];
    try{for(const [i,row] of rows().entries())objects.push(await show(row,i));
      if(version!==revision||value!==JSON.stringify(rows())){objects.forEach(dispose);return;}
      objects.forEach(n=>group.add(n));sync();render();applyShow();if(state.edit==='smoke_launchers')selectEdit('smoke_launchers');
    }catch(error){objects.forEach(dispose);if(version===revision)reportError(error);}}
  async function choose(){const session=candidateRevision;const version=++revision;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;
    const parent=$('smoke-parent').value,tubes=Number($('smoke-tubes').value);if(!parent||!labels[tubes]){controls();return;}
    let position=[...pose().position];
    if(!rows().length){const box=new THREE.Box3();for(const p of ['hull','turret']){groups[p].updateWorldMatrix(true,true);box.union(new THREE.Box3().setFromObject(groups[p]));}
      const host=hosts.find(p=>p.name===parent);if(host&&!box.isEmpty()){const p=box.getCenter(new THREE.Vector3());p.z=box.max.z+1;scene.updateWorldMatrix(true,false);position=scene.worldToLocal(p).applyMatrix4(matrix(host.matrix).invert()).toArray();}}
    const row={tubes,parent,position,rotation:[...pose().rotation]},object=await show(row,0,true);
    if(version!==revision||session!==candidateRevision){dispose(object);return;}candidate=row;group.add(object);sync();render();applyShow();}
  function capture(){if(state.edit!=='smoke_launchers'||!rows()[selected])return false;const node=target();if(!node)return false;
    rows()[selected].position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');rows()[selected].rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;}
  const run=fn=>async()=>{if(state.busy)return;try{await fn();}catch(error){reportError(error);}};
  $('smoke-load').onclick=run(async()=>{opened=true;await loading('Leer fijaciones del vehículo',refreshCheck);render();await choose();});
  $('smoke-tubes').onchange=run(choose);
  $('smoke-list').onchange=()=>{selected=Number($('smoke-list').value);for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;render();selectEdit('smoke_launchers');};
  $('smoke-parent').onchange=run(async()=>{const row=candidate||rows()[selected],next=hosts.find(p=>p.name===$('smoke-parent').value),old=hosts.find(p=>p.name===parentName(row));if(!row||!next||!old)return;
    const local=poseMatrix(row);
    local.premultiply(matrix(old.matrix)).premultiply(matrix(next.matrix).invert());const p=new THREE.Vector3(),q=new THREE.Quaternion(),scale=new THREE.Vector3();local.decompose(p,q,scale);
    if(scale.toArray().some(v=>Math.abs(v-1)>.002)){render();throw Error(t('Ese punto de fijación cambia la escala o refleja la pieza. Elegí otro.'));}
    if(!candidate)remember();row.parent=next.name;const pivot=new THREE.Vector3(...(row.pivot||[0,0,0]));row.position=p.sub(pivot).add(pivot.clone().applyQuaternion(q)).toArray();row.rotation=new THREE.Euler().setFromQuaternion(q,'ZYX').toArray().slice(0,3).map(THREE.MathUtils.radToDeg);
    sync();if(!candidate){selectEdit('smoke_launchers');refreshCheck();}});
  $('smoke-add').onclick=run(async()=>{if(!candidate)return;remember();state.transform.smoke_launchers??=[];rows().push(structuredClone(candidate));selected=rows().length-1;await restore();selectEdit('smoke_launchers');refreshCheck();});
  $('smoke-duplicate').onclick=run(async()=>{if(!rows()[selected])return;remember();const copy=structuredClone(rows()[selected]);copy.position[1]+=8;rows().push(copy);selected=rows().length-1;await restore();selectEdit('smoke_launchers');refreshCheck();});
  $('smoke-remove').onclick=run(async()=>{if(!rows()[selected])return;remember();rows().splice(selected,1);await restore();selectEdit(rows().length?'smoke_launchers':'hull');refreshCheck();});
  $('smoke-edit').onclick=()=>selectEdit('smoke_launchers');
  function deactivate(){candidateRevision++;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;sync();controls();}
  controls();return {sync,restore,deactivate,controls,capture,target,pose,receiveCheck(check){hosts=check.armor_hosts||check.hosts||[];render();sync();}};
}
