import {applyPose,poseMatrix,fittingParent} from './pivot-editor.js';
import * as THREE from 'three';
import {t} from './i18n.js';
import {previewMesh} from './model-geometry.js';

// Poses are local to actual compiled bones, exactly like commander mounts.
export function armorEditor({state,$,groups,scene,job,loading,remember,selectEdit,refreshCheck,reportError,applyShow}){
  const group=groups.extra_armor=new THREE.Group();scene.add(group);
  let catalog=null,signature='',hosts=[],selected=-1,revision=0,candidate=null,candidateRevision=0;
  const meshes=new Map();
  const rows=()=>state.transform.extra_armor||[];
  // The fitting being chosen is moved before it is added; added fittings stay
  // put (no handles) until their name is picked again in the placed list.
  const pose=()=>candidate||rows()[selected]||{position:[0,0,0],rotation:[0,0,0]};
  const target=()=>group.children.find(n=>candidate?n.userData.candidate:n.userData.armorIndex===selected)?.children[0];
  const parentName=row=>fittingParent(row,state.check?.commander_bones);
  const matrix=rows=>new THREE.Matrix4().set(...rows.flat());
  function dispose(node){node.traverse(n=>{n.geometry?.dispose();for(const m of new Set([n.userData?.flat,...[n.material].flat()]))if(m&&!m.userData?.shared)m.dispose();});node.removeFromParent();}
  function clear(){for(const n of [...group.children])dispose(n);candidate=null;}
  function sync(){for(const host of group.children){const item=host.userData.candidate?candidate:rows()[host.userData.armorIndex];
    if(!item)continue;const native=hosts.find(h=>h.name===parentName(item));host.visible=!!native;
    if(native)host.matrix.copy(matrix(native.matrix));
    const fitting=host.children[0];applyPose(fitting,item);
  }group.updateMatrixWorld(true);}
  function controls(){
    const ready=!!(state.hull&&state.turret),busy=state.busy;
    $('armor-load').disabled=busy||!ready;$('armor-fields').classList.toggle('hidden',!catalog||!ready);
    for(const id of ['armor-kind','armor-asset','armor-parent','armor-list'])$(id).disabled=busy||!ready;
    $('armor-add').disabled=busy||!ready||!candidate||candidate.asset!==$('armor-asset').value;
    $('armor-duplicate').disabled=busy||!rows()[selected];
    $('armor-remove').disabled=busy||!rows()[selected];
    $('armor-edit').disabled=busy||!rows()[selected];
  }
  function render(){
    selected=Math.min(selected,rows().length-1);
    const old=$('armor-asset').value;$('armor-asset').replaceChildren();
    const kind=$('armor-kind').value;
    for(const item of catalog?.items||[]){if(kind&&item.kind!==kind)continue;
      const label=t(({tracks:'Orugas de repuesto',sandbags:'Sacos de arena',plates:'Placas y faldones',wood:'Madera'})[item.kind])+' · '+item.label;
      $('armor-asset').add(new Option(label,item.id));}
    if((catalog?.items||[]).some(p=>p.id===old)&&$('armor-asset').options&&[...$('armor-asset').options].some(o=>o.value===old))$('armor-asset').value=old;
    $('armor-list').replaceChildren();rows().forEach((r,i)=>$('armor-list').add(new Option((i+1)+'. '+(catalog?.items.find(p=>p.id===r.asset)?.label||r.asset),String(i))));
    $('armor-list').value=String(selected);if(selected<0)$('armor-list').selectedIndex=-1;
    const parent=parentName(candidate)||parentName(rows()[selected])||$('armor-parent').value;
    $('armor-parent').replaceChildren();for(const h of hosts)$('armor-parent').add(new Option(h.name,h.name));
    if(hosts.some(h=>h.name===parent))$('armor-parent').value=parent;
    const item=catalog?.items.find(p=>p.id===$('armor-asset').value);
    $('armor-info').textContent=item?[t('Material nativo')+': '+item.materials.join(', '),t('Espesor nativo')+': '+item.thickness.join(' / '),item.cast_steel?t('Conserva el factor nativo de resistencia de la oruga.'):''].filter(Boolean).join(' · '):'';
    controls();
  }
  async function ensureCatalog(){const key=JSON.stringify(state.config?.settings||{});
    if(!catalog||key!==signature){catalog=await job('/api/armor-catalog',{});signature=key;meshes.clear();}render();}
  async function geometry(asset){if(!meshes.has(asset))meshes.set(asset,await job('/api/armor-preview',{asset}));return meshes.get(asset);}
  async function show(row,index,ghost=false){const data=await geometry(row.asset),host=new THREE.Group(),fitting=new THREE.Group();
    host.matrixAutoUpdate=false;host.userData=ghost?{candidate:true}:{armorIndex:index};host.add(fitting);
    for(const mesh of data.meshes){const material=new THREE.MeshStandardMaterial({color:ghost?0x77bf94:0xb69b70,roughness:.85,side:THREE.DoubleSide,wireframe:state.wire,transparent:ghost,opacity:ghost?0.55:1});
      fitting.add(previewMesh(mesh,material));}return host;}
  async function restore(){const version=++revision;clear();
    if(!rows().length){render();return;}
    try{await ensureCatalog();const value=JSON.stringify(rows()),objects=[];
      for(const [i,row] of rows().entries())objects.push(await show(row,i));
      if(version!==revision||value!==JSON.stringify(rows())){objects.forEach(dispose);return;}
      objects.forEach(n=>group.add(n));sync();render();applyShow();
      if(state.edit==='extra_armor')selectEdit('extra_armor');
    }catch(error){if(version===revision)reportError(error);}
  }
  function receiveCheck(check){hosts=check.armor_hosts||check.hosts||[];render();sync();}
  async function choose(){const session=candidateRevision;const asset=$('armor-asset').value,parent=$('armor-parent').value;
    const version=++revision;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;
    controls();
    if(!asset||!parent)return;
    const near=rows()[selected]||rows().at(-1)||{position:[0,0,0],rotation:[0,0,0]};
    let position=[...near.position];
    // A new fitting appears above the vehicle and its placed armor, never on top of a placed one.
    {const box=new THREE.Box3();for(const name of ['hull','turret','extra_armor']){groups[name].updateWorldMatrix(true,true);box.union(new THREE.Box3().setFromObject(groups[name]));}
      const host=hosts.find(h=>h.name===parent);
      if(!box.isEmpty()&&host){const p=box.getCenter(new THREE.Vector3());p.z=box.max.z+3;scene.updateWorldMatrix(true,false);position=scene.worldToLocal(p).applyMatrix4(matrix(host.matrix).invert()).toArray();}}
    const value={asset,parent,position,rotation:[...near.rotation]};
    const object=await show(value,0,true);if(version!==revision||session!==candidateRevision){dispose(object);return;}
    candidate=value;selected=-1;group.add(object);sync();render();applyShow();selectEdit('extra_armor');
  }
  function capture(){const row=candidate||rows()[selected];if(state.edit!=='extra_armor'||!row)return false;
    const node=target();if(!node)return false;
    row.position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');row.rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;
  }
  const run=fn=>async()=>{if(state.busy)return;try{await fn();}catch(error){reportError(error);}};
  $('armor-load').onclick=run(async()=>{await loading('Leer blindajes disponibles',ensureCatalog);await refreshCheck();render();await choose();});
  $('armor-kind').oninput=run(async()=>{render();await choose();});
  $('armor-asset').onchange=run(choose);
  // A click also picks: the list keeps no visible selection while nothing is
  // being moved, so the same name may be clicked again to move it.
  $('armor-list').onchange=$('armor-list').onclick=()=>{if($('armor-list').selectedIndex<0)return;selected=Number($('armor-list').value);for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;render();selectEdit('extra_armor');};
  $('armor-parent').onchange=run(async()=>{const row=candidate||rows()[selected],parent=$('armor-parent').value;if(!row){await choose();return;}
    const old=hosts.find(h=>h.name===parentName(row)),next=hosts.find(h=>h.name===parent);if(!old||!next)return;
    const pose=poseMatrix(row);
    const local=matrix(next.matrix).invert().multiply(matrix(old.matrix)).multiply(pose),p=new THREE.Vector3(),q=new THREE.Quaternion(),s=new THREE.Vector3();local.decompose(p,q,s);
    if(s.toArray().some(v=>Math.abs(v-1)>.002)){render();throw Error(t('Ese punto de fijación cambia la escala o refleja la pieza. Elegí otro.'));}
    if(!candidate)remember();row.parent=parent;const pivot=new THREE.Vector3(...(row.pivot||[0,0,0]));row.position=p.sub(pivot).add(pivot.clone().applyQuaternion(q)).toArray();row.rotation=new THREE.Euler().setFromQuaternion(q,'ZYX').toArray().slice(0,3).map(THREE.MathUtils.radToDeg);
    sync();selectEdit('extra_armor');if(!candidate)refreshCheck();});
  $('armor-add').onclick=run(async()=>{const asset=$('armor-asset').value,parent=$('armor-parent').value;if(!candidate||candidate.asset!==asset||candidate.parent!==parent)return;
    remember();state.transform.extra_armor??=[];rows().push(structuredClone(candidate));selected=-1;
    await restore();selectEdit('extra_armor');refreshCheck();});
  $('armor-duplicate').onclick=run(async()=>{if(!rows()[selected])return;remember();const copy=structuredClone(rows()[selected]);copy.position[1]+=15;rows().push(copy);selected=rows().length-1;await restore();selectEdit('extra_armor');refreshCheck();});
  $('armor-remove').onclick=run(async()=>{if(!rows()[selected])return;remember();rows().splice(selected,1);selected=-1;await restore();selectEdit(rows().length?'extra_armor':'hull');refreshCheck();});
  $('armor-edit').onclick=()=>selectEdit('extra_armor');
  function deactivate(){candidateRevision++;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;sync();controls();}
  controls();return {sync,restore,deactivate,receiveCheck,controls,capture,target,pose};
}
