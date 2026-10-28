import {applyPose,poseMatrix,fittingParent} from './pivot-editor.js';
import * as THREE from 'three';
import {t} from './i18n.js';
import {previewMesh} from './model-geometry.js';
import {dlcCatalogNote} from './dlc-catalog.js';

// Extra MGs manned by whoever climbs on the vehicle (like the Stuart's .50).
// Same placement flow as the smoke launchers: a green preview, then Add, then
// Move/Rotate/XYZ. The mount, the operator's pose and the gun are chosen apart.
export function riderEditor({state,$,groups,scene,job,loading,remember,selectEdit,refreshCheck,reportError,applyShow,makeSeat,active=()=>state.edit==='rider_mgs'}){
  const group=groups.rider_mgs=new THREE.Group();scene.add(group);
  let hosts=[],selected=0,revision=0,candidate=null,opened=false,candidateRevision=0,catalog=null,catalogRevision;
  const cache=new Map(),rows=()=>state.transform.rider_mgs||[];
  const pose=()=>candidate||rows()[selected]||{position:[0,0,0],rotation:[0,0,0]};
  const target=()=>group.children.find(n=>candidate?n.userData.candidate:n.userData.riderIndex===selected)?.children[0];
  const parentName=row=>fittingParent(row,state.check?.commander_bones);
  const matrix=value=>new THREE.Matrix4().set(...value.flat());
  const editing=()=>candidate||rows()[selected];
  const support=id=>catalog?.supports.find(s=>s.id===id);
  const label=row=>(support(row.support)?.label||row.support)+' · '+(catalog?.weapons.find(w=>w.id===row.item)?.label||row.item);
  function dispose(node){node.traverse(n=>{if(!n.userData.keepGeometry)n.geometry?.dispose();for(const m of [n.material].flat())if(m&&!m.userData?.shared)m.dispose();});node.removeFromParent();}
  function clear(){for(const n of [...group.children])dispose(n);candidate=null;}
  function sync(){for(const host of group.children){const row=host.userData.candidate?candidate:rows()[host.userData.riderIndex];
    if(!row)continue;const parent=hosts.find(p=>p.name===parentName(row));host.visible=!!parent;
    if(parent)host.matrix.copy(matrix(parent.matrix));
    applyPose(host.children[0],row);
  }group.updateMatrixWorld(true);}
  function controls(){const ready=!!(state.hull&&state.turret),busy=state.busy,row=editing();
    $('rider-load').disabled=busy||!ready;$('rider-fields').classList.toggle('hidden',!opened||!ready||!catalog);
    for(const id of ['rider-support','rider-pose','rider-item','rider-parent','rider-list'])$(id).disabled=busy||!ready;
    for(const id of ['rider-seat-0','rider-seat-1','rider-seat-2','rider-yaw-0','rider-yaw-1','rider-pitch-0','rider-pitch-1'])$(id).disabled=busy||!row;
    $('rider-add').disabled=busy||!ready||!candidate;
    for(const id of ['rider-remove','rider-edit','rider-duplicate'])$(id).disabled=busy||!rows()[selected];
  }
  function fields(){const row=editing();if(!row)return;
    for(const [id,key] of [['rider-support','support'],['rider-pose','pose'],['rider-item','item']])if([...$(id).options].some(o=>o.value===row[key]))$(id).value=row[key];
    for(let i=0;i<3;i++)$('rider-seat-'+i).value=(row.seat||[0,0,0])[i];
    for(const type of ['yaw','pitch'])for(let i=0;i<2;i++)$(`rider-${type}-${i}`).value=row[type][i];
  }
  function render(){selected=Math.max(0,Math.min(selected,rows().length-1));
    $('rider-list').replaceChildren();rows().forEach((r,i)=>$('rider-list').add(new Option((i+1)+'. '+label(r),String(i))));$('rider-list').value=String(selected);
    const parent=parentName(editing())||(hosts.some(h=>h.name===$('rider-parent').value)&&$('rider-parent').value!=='basis'?$('rider-parent').value:
      ['turret','body'].find(name=>hosts.some(h=>h.name===name)));
    $('rider-parent').replaceChildren();for(const h of hosts)$('rider-parent').add(new Option(h.name,h.name));
    if(hosts.some(p=>p.name===parent))$('rider-parent').value=parent;fields();controls();}
  async function ensureCatalog(){if(catalog&&catalogRevision===state.artRevision)return;catalog=await job('/api/rider-catalog',{});catalogRevision=state.artRevision;
    $('rider-dlc-note').textContent=dlcCatalogNote(catalog);
    // Mounts the operator turns with first, then the rest; a German MG first.
    catalog.supports.sort((a,b)=>b.turns-a.turns);
    catalog.weapons.sort((a,b)=>(['mg42_belt','mg34_belt'].indexOf(b.id)+1||0)-(['mg42_belt','mg34_belt'].indexOf(a.id)+1||0));
    $('rider-support').replaceChildren(...catalog.supports.map(s=>new Option(s.label+(s.turns?'':' · '+t('operador fijo')),s.id)));
    $('rider-pose').replaceChildren(...catalog.poses.map(p=>new Option(p.id+' ('+p.source+')',p.id)));
    $('rider-item').replaceChildren(...catalog.weapons.map(w=>new Option(w.label+' ('+w.id+')',w.id)));
  }
  const key=row=>JSON.stringify([row.support,row.pose,row.item,row.seat||[0,0,0]]);
  async function show(row,index,ghost=false){const id=key(row);
    if(!cache.has(id))cache.set(id,await job('/api/rider-preview',{...row,seat:row.seat||[0,0,0]}));
    const data=cache.get(id),host=new THREE.Group(),fitting=new THREE.Group();host.matrixAutoUpdate=false;
    host.userData=ghost?{candidate:true}:{riderIndex:index};host.userData.pitchFrame=data.pitch_matrix;host.add(fitting);
    for(const mesh of data.meshes)fitting.add(previewMesh(mesh,new THREE.MeshStandardMaterial({color:ghost?0x77bf94:0xc9ad74,
      roughness:.8,side:THREE.DoubleSide,wireframe:state.wire,transparent:ghost,opacity:ghost?.55:1})));
    for(const seat of data.seats||[])fitting.add(makeSeat(seat));
    return host;
  }
  // The old MGs stay drawn until the new models arrive: a slow model load must
  // not leave the gizmo on an empty spot (ttrand25).
  async function restore(){const version=++revision;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;
    if(!rows().length){clear();render();applyShow();return;}
    opened=true;const value=JSON.stringify(rows()),objects=[];
    try{await ensureCatalog();for(const [i,row] of rows().entries())objects.push(await show(row,i));
      if(version!==revision||value!==JSON.stringify(rows())){objects.forEach(dispose);return;}
      clear();objects.forEach(n=>group.add(n));sync();render();applyShow();if(state.edit==='rider_mgs')selectEdit('rider_mgs');
    }catch(error){objects.forEach(dispose);if(version===revision)reportError(error);}}
  function defaults(row,id){const s=support(id);row.support=id;row.pose=s.pose;row.yaw=[...s.yaw_limits];row.pitch=[...s.pitch_limits];}
  async function choose(){const session=candidateRevision;const version=++revision;const old=group.children.filter(n=>n.userData.candidate);candidate=null;
    const parent=$('rider-parent').value,id=$('rider-support').value;if(!parent||!support(id)){old.forEach(dispose);controls();return;}
    let position=[...pose().position];
    if(!rows().length){const box=new THREE.Box3();for(const p of ['hull','turret']){groups[p].updateWorldMatrix(true,true);box.union(new THREE.Box3().setFromObject(groups[p]));}
      const host=hosts.find(p=>p.name===parent);if(host&&!box.isEmpty()){const p=box.getCenter(new THREE.Vector3());p.z=box.max.z;scene.updateWorldMatrix(true,false);position=scene.worldToLocal(p).applyMatrix4(matrix(host.matrix).invert()).toArray();}}
    const row={parent,position,rotation:[...pose().rotation],item:$('rider-item').value||catalog.weapons[0].id,seat:[0,0,0]};defaults(row,id);
    if([...$('rider-pose').options].some(o=>o.value===$('rider-pose').dataset.chosen))row.pose=$('rider-pose').dataset.chosen;
    let object;try{object=await show(row,0,true);}catch(error){old.forEach(n=>n.parent&&dispose(n));throw error;}
    if(version!==revision||session!==candidateRevision){dispose(object);return;}old.forEach(n=>n.parent&&dispose(n));candidate=row;group.add(object);sync();render();applyShow();selectEdit('rider_mgs');}
  function capture(){const row=candidate||rows()[selected];if(state.edit!=='rider_mgs'||!row)return false;const node=target();if(!node)return false;
    row.position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');row.rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;}
  const run=fn=>async()=>{if(state.busy)return;try{await fn();}catch(error){reportError(error);}};
  // A change of mount, pose, gun or operator spot redraws that MG; a placed one is a design change.
  async function change(edit){const row=editing();if(!row)return;if(!candidate)remember();edit(row);
    if(candidate)return choose();await restore();selectEdit('rider_mgs');refreshCheck();}
  $('rider-load').onclick=run(async()=>{opened=true;await loading('Leer MG de pasajero disponibles',async()=>{await refreshCheck();await ensureCatalog();});
    if(!catalog.supports.length||!catalog.weapons.length)throw Error(t('No hay soportes o ametralladoras base disponibles.'));render();await choose();});
  $('rider-support').onchange=run(()=>candidate?choose():change(row=>defaults(row,$('rider-support').value)));
  $('rider-pose').onchange=run(()=>{$('rider-pose').dataset.chosen=$('rider-pose').value;return candidate?choose():change(row=>{row.pose=$('rider-pose').value;});});
  $('rider-item').onchange=run(()=>candidate?choose():change(row=>{row.item=$('rider-item').value;}));
  for(let i=0;i<3;i++)$('rider-seat-'+i).onchange=run(()=>{const value=Number($('rider-seat-'+i).value);if(!Number.isFinite(value)||Math.abs(value)>200){fields();return;}
    return change(row=>{row.seat=[...(row.seat||[0,0,0])];row.seat[i]=value;});});
  for(const type of ['yaw','pitch'])for(let i=0;i<2;i++)$(`rider-${type}-${i}`).onchange=()=>{const row=editing(),value=Number($(`rider-${type}-${i}`).value);
    if(!row||!Number.isFinite(value)){fields();return;}if(!candidate)remember();row[type][i]=value;if(!candidate)refreshCheck();};
  $('rider-list').onchange=()=>{selected=Number($('rider-list').value);for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;render();selectEdit('rider_mgs');};
  $('rider-list').onclick=$('rider-list').onchange;
  $('rider-parent').onchange=run(async()=>{const row=candidate||rows()[selected],next=hosts.find(p=>p.name===$('rider-parent').value),old=hosts.find(p=>p.name===parentName(row));if(!row||!next||!old)return;
    const local=poseMatrix(row);
    local.premultiply(matrix(old.matrix)).premultiply(matrix(next.matrix).invert());const p=new THREE.Vector3(),q=new THREE.Quaternion(),scale=new THREE.Vector3();local.decompose(p,q,scale);
    if(scale.toArray().some(v=>Math.abs(v-1)>.002)){render();throw Error(t('Ese punto de fijación cambia la escala o refleja la pieza. Elegí otro.'));}
    if(!candidate)remember();row.parent=next.name;const pivot=new THREE.Vector3(...(row.pivot||[0,0,0]));row.position=p.sub(pivot).add(pivot.clone().applyQuaternion(q)).toArray();row.rotation=new THREE.Euler().setFromQuaternion(q,'ZYX').toArray().slice(0,3).map(THREE.MathUtils.radToDeg);
    sync();if(!candidate){selectEdit('rider_mgs');refreshCheck();}});
  $('rider-add').onclick=run(async()=>{if(!candidate)return;remember();state.transform.rider_mgs??=[];rows().push(structuredClone(candidate));selected=rows().length-1;await restore();selectEdit('rider_mgs');refreshCheck();});
  $('rider-duplicate').onclick=run(async()=>{if(!rows()[selected])return;remember();const copy=structuredClone(rows()[selected]);copy.position[1]+=20;rows().push(copy);selected=rows().length-1;await restore();selectEdit('rider_mgs');refreshCheck();});
  $('rider-remove').onclick=run(async()=>{if(!rows()[selected])return;remember();rows().splice(selected,1);if(!rows().length)delete state.transform.rider_mgs;await restore();selectEdit(rows().length?'rider_mgs':'hull');refreshCheck();});
  $('rider-edit').onclick=()=>{candidate=null;for(const n of [...group.children])if(n.userData.candidate)dispose(n);render();selectEdit('rider_mgs');};
  function deactivate(){candidateRevision++;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;sync();controls();}
  function weaponRigs(){return group.children.flatMap(host=>{
    const row=host.userData.candidate?candidate:rows()[host.userData.riderIndex],pitch=host.userData.pitchFrame;
    if(!row||!pitch)return [];
    return [{key:'rider_mg_'+(host.userData.candidate?'preview':host.userData.riderIndex),group:host.children[0],revision:host.uuid,
      parentOf:key=>key==='pitch'?'yaw':null,jointOf:object=>object.userData.seat?'yaw':object.userData.mesh?.rig_joint||null,
      joints:[{key:'yaw',kind:'traverse',weapons:[t('MG de pasajero')],matrix:new THREE.Matrix4().makeTranslation(...(row.pivot||[0,0,0])),limits:()=>row.yaw},
        {key:'pitch',kind:'elevation',weapons:[t('MG de pasajero')],matrix:matrix(pitch),limits:()=>row.pitch}]}];
  });}
  controls();return {sync,restore,deactivate,controls,capture,target,pose,weaponRigs,receiveCheck(check){hosts=check.armor_hosts||check.hosts||[];render();sync();}};
}
