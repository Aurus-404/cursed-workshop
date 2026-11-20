import * as THREE from 'three';
import {t} from './i18n.js';
import {applyPose,poseMatrix,poseFromMatrix} from './pivot-editor.js';
import {previewMesh} from './model-geometry.js';
import {dlcCatalogNote} from './dlc-catalog.js';

// Weapons without a turret (transform.mounted_weapons.added): a weapon place on
// any part of the vehicle, hull, turret or a wheel, like the Sherman's bow MG.
// It turns on two joints of its own; past its traverse it turns the whole
// vehicle, as a casemate ({rotate}), unless told not to. And swappable slots
// (transform.mounted_weapons.slots): a weapon place of the vehicle takes an
// infantry weapon, as the Universal Carrier's front slot, and the crew can
// change it in battle. Only weapons the game marks mountable go in a slot.
export const MOUNT_TYPES=['hull','aa'];
const ELEVATION=[[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]];
const zero=()=>({position:[0,0,0],rotation:[0,0,0]});

export function mountedRows(transform){return transform?.mounted_weapons?.added||[];}
export function mountedSlots(transform){return transform?.mounted_weapons?.slots||[];}
function tidy(transform){const value=transform.mounted_weapons;if(!value)return;
  if(!value.added?.length)delete value.added;if(!value.slots?.length)delete value.slots;
  if(!value.added&&!value.slots)delete transform.mounted_weapons;}
export function addMounted(transform,row){transform.mounted_weapons??={};transform.mounted_weapons.added??=[];
  transform.mounted_weapons.added.push(row);return transform.mounted_weapons.added.length-1;}
export function removeMounted(transform,index){const rows=mountedRows(transform);if(index<0||index>=rows.length)return false;
  rows.splice(index,1);tidy(transform);return true;}
// item '' takes the place back to its own weapon.
export function setSlot(transform,place,item){
  const rows=mountedSlots(transform).filter(row=>row.place!==place),old=mountedSlots(transform).find(row=>row.place===place);
  if(item)rows.push({place,item,link:old?.link||zero()});
  transform.mounted_weapons??={};transform.mounted_weapons.slots=rows;tidy(transform);}
export function slotItem(transform,place){return mountedSlots(transform).find(row=>row.place===place)?.item||'';}
// The part a new weapon hangs from: the hull's body unless another part is chosen.
export function mountedParent(hosts,wanted){const names=hosts.map(h=>h.name);
  return names.includes(wanted)?wanted:names.includes('body')?'body':names.find(n=>n!=='basis')||names[0]||'';}
export function validLimits([low,high]){return Number.isFinite(low)&&Number.isFinite(high)&&low<high;}
// The weapon list: machine guns first, then anti-tank rifles.
export function weaponGroups(weapons){return [['mgun',t('Ametralladoras')],['at_rifle',t('Fusiles antitanque')]]
  .map(([kind,label])=>[label,weapons.filter(w=>w.kind===kind)]).filter(([,rows])=>rows.length);}

export function mountedWeaponEditor({state,$,scene,job,loading,remember,selectEdit,refreshCheck,reportError,applyShow,groups,roleName=place=>place,active=()=>state.edit==='mounted_weapons',openPlacement=()=>{},onSlots=()=>{}}){
  const group=groups.mounted_weapons=new THREE.Group();group.name='mounted_weapons';scene.add(group);
  let hosts=[],slots=[],selected=-1,slotSelected='',component='mount',catalog=null,catalogRevision,revision=0,opened=false;
  const cache=new Map(),rows=()=>mountedRows(state.transform);
  const matrix=value=>new THREE.Matrix4().set(...value.flat());
  const editingSlot=()=>!!slotSelected&&component==='slot';
  const row=()=>editingSlot()?mountedSlots(state.transform).find(r=>r.place===slotSelected):rows()[selected];
  const pose=()=>{const value=row();if(!value)return null;return editingSlot()||component==='link'?(value.link??=zero()):value;};
  const hostOf=value=>group.children.find(n=>editingSlot()?n.userData.slot===value?.place:n.userData.mountedIndex===selected);
  const target=()=>{const host=hostOf(row());if(!host)return null;return editingSlot()||component==='link'?host.userData.link:host.children[0];};
  function dispose(node){node.traverse(n=>{if(!n.userData.keepGeometry)n.geometry?.dispose();for(const m of new Set([n.userData?.flat,n.userData?.untextured,...[n.material].flat()]))if(m&&!m.userData?.shared)m.dispose();});node.removeFromParent();}
  function clear(){for(const n of [...group.children])dispose(n);}
  async function weapon(item){if(!cache.has(item))cache.set(item,await job('/api/mounted-weapon-preview',{item}));return cache.get(item);}
  function model(data,color,keepColor=false){const actor=new THREE.Group();actor.matrixAutoUpdate=false;actor.matrix.copy(matrix(data.actor_frame));
    for(const mesh of data.meshes){const material=new THREE.MeshStandardMaterial({color,roughness:.8,side:THREE.DoubleSide,wireframe:!!state.wire});material.userData.keepColor=keepColor;actor.add(previewMesh(mesh,material));}
    return actor;}
  // Traverse joint (the row's pose), elevation joint, the weapon's link: the bones the build writes.
  async function showRow(value,index){const host=new THREE.Group(),fitting=new THREE.Group(),pitch=new THREE.Group(),link=new THREE.Group();
    host.matrixAutoUpdate=false;pitch.matrixAutoUpdate=false;pitch.matrix.copy(matrix(ELEVATION));
    host.add(fitting);fitting.add(pitch);pitch.add(link);link.add(model(await weapon(value.item),0xc9ad74));
    host.userData={mountedIndex:index,link};return host;}
  async function showSlot(value){const slot=slots.find(s=>s.place===value.place);if(!slot)return null;
    const host=new THREE.Group(),link=new THREE.Group();host.matrixAutoUpdate=false;host.add(link);
    link.add(model(await weapon(value.item),0x9fc2d8,true));host.userData={slot:value.place,link,native:slot.mountable};return host;}
  function sync(){for(const host of group.children){
      if(host.userData.slot){const slot=slots.find(s=>s.place===host.userData.slot),value=mountedSlots(state.transform).find(r=>r.place===host.userData.slot);
        host.visible=!!slot&&!!value;if(!slot||!value)continue;host.matrix.copy(matrix(slot.mountable?slot.link_matrix||slot.matrix:slot.matrix));
        applyPose(host.userData.link,slot.mountable?zero():value.link||zero());continue;}
      const value=rows()[host.userData.mountedIndex];if(!value)continue;const parent=hosts.find(h=>h.name===value.parent);
      host.visible=!!parent;if(parent)host.matrix.copy(matrix(parent.matrix));applyPose(host.children[0],value);applyPose(host.userData.link,value.link||zero());}
    group.updateMatrixWorld(true);}
  async function restore(){const version=++revision,value=JSON.stringify(state.transform.mounted_weapons||{}),objects=[];
    if(!rows().length&&!mountedSlots(state.transform).length){clear();render();applyShow?.();return;}
    opened=true;
    try{await ensureCatalog();for(const [i,r] of rows().entries())objects.push(await showRow(r,i));
      for(const r of mountedSlots(state.transform)){const node=await showSlot(r);if(node)objects.push(node);}
      if(version!==revision||value!==JSON.stringify(state.transform.mounted_weapons||{})){objects.forEach(dispose);return;}
      clear();objects.forEach(n=>group.add(n));sync();render();applyShow?.();if(active())selectEdit('mounted_weapons');
    }catch(error){objects.forEach(dispose);if(version===revision)reportError(error);}}
  async function ensureCatalog(){if(catalog&&catalogRevision===state.artRevision)return;
    catalog=await job('/api/mounted-weapon-catalog',{});catalogRevision=state.artRevision;
    $('mounted-dlc-note').textContent=dlcCatalogNote(catalog);
    for(const id of ['mounted-item']){const select=$(id);select.replaceChildren();
      for(const [label,list] of weaponGroups(catalog.weapons)){const optgroup=document.createElement('optgroup');optgroup.label=label;
        for(const w of list)optgroup.append(new Option(w.label+' ('+w.id+')',w.id));select.append(optgroup);}}}
  const crewName=place=>{const number=String(place).match(/(\d+)$/)?.[1];return roleName(place)+(number&&number!=='1'?' '+number:'');};
  const label=r=>(catalog?.weapons.find(w=>w.id===r.item)?.label||r.item)+' · '+r.parent;
  function controls(){const ready=!!(state.hull&&state.turret),busy=state.busy,current=rows()[selected];
    $('mounted-load').disabled=busy||!ready;$('mounted-fields').classList.toggle('hidden',!opened||!ready||!catalog);
    for(const id of ['mounted-item','mounted-parent','mounted-type','mounted-add'])$(id).disabled=busy||!ready||!hosts.length;
    $('mounted-list').disabled=busy||!rows().length;$('mounted-list').classList.toggle('hidden',!rows().length);
    for(const id of ['mounted-place','mounted-remove','mounted-component','mounted-rotate','mounted-operator','mounted-yaw-0','mounted-yaw-1','mounted-pitch-0','mounted-pitch-1'])$(id).disabled=busy||!current;}
  function fields(){const current=rows()[selected];
    if(current){for(const [id,key] of [['mounted-item','item'],['mounted-type','type']])if([...$(id).options].some(o=>o.value===(current[key]??'hull')))$(id).value=current[key]??'hull';
      $('mounted-rotate').checked=current.rotate!==false;
      // Who works it: someone aboard, or left to the build (a crewman with no weapon yet).
      const excluded=new Set(state.transform.crew_exclude||[]),crew=(state.check?.crew||[]).filter(place=>!excluded.has(place));
      $('mounted-operator').replaceChildren(new Option(t('Automático'),''),...[...new Set([...crew,...(current.operator?[current.operator]:[])])].map(place=>new Option(crewName(place),place)));
      $('mounted-operator').value=current.operator||'';
      for(const type of ['yaw','pitch'])for(let i=0;i<2;i++)$(`mounted-${type}-${i}`).value=current[type][i];}}
  function render(){if(selected>=rows().length)selected=rows().length-1;
    const list=$('mounted-list');list.replaceChildren();rows().forEach((r,i)=>list.add(new Option((i+1)+'. '+label(r),String(i))));
    list.value=selected>=0?String(selected):'';
    const parent=rows()[selected]?.parent??$('mounted-parent').value;
    $('mounted-parent').replaceChildren(...hosts.map(h=>new Option(h.name,h.name)));$('mounted-parent').value=mountedParent(hosts,parent);
    if(!slots.some(s=>s.place===slotSelected))slotSelected=slots[0]?.place||'';    fields();controls();}
  // On top of the vehicle's middle, in the chosen part's frame.
  function defaultRow(){const parent=mountedParent(hosts,$('mounted-parent').value),host=hosts.find(h=>h.name===parent);let position=[0,0,0];
    const box=new THREE.Box3();for(const p of ['hull','turret'])if(groups[p]){groups[p].updateWorldMatrix(true,true);box.union(new THREE.Box3().setFromObject(groups[p]));}
    if(host&&!box.isEmpty()){const p=box.getCenter(new THREE.Vector3());p.z=box.max.z;scene.updateWorldMatrix(true,false);
      position=scene.worldToLocal(p).applyMatrix4(matrix(host.matrix).invert()).toArray().map(v=>Number(v.toFixed(3)));}
    return {parent,position,rotation:[0,0,0],link:zero(),item:$('mounted-item').value||catalog.weapons[0]?.id,type:$('mounted-type').value||'hull',
      rotate:true,yaw:[-15,15],pitch:[-10,20]};}
  function place(){const node=target();if(!node)return;selectEdit('mounted_weapons');}
  function capture(){if(!active())return false;const node=target(),value=pose();if(!node||!value)return false;
    value.position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');value.rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;}
  const run=fn=>async()=>{if(state.busy)return;try{await fn();}catch(error){reportError(error);}};
  async function after(){await restore();if(rows().length||mountedSlots(state.transform).length)selectEdit('mounted_weapons');refreshCheck();}
  $('mounted-load').onclick=run(async()=>{opened=true;await loading('Leer armas montables',async()=>{await refreshCheck();await ensureCatalog();});
    if(!catalog.weapons.length)throw Error(t('No hay armas montables disponibles.'));render();});
  $('mounted-add').onclick=run(async()=>{if(!hosts.length||!catalog?.weapons.length)return;remember();selected=addMounted(state.transform,defaultRow());component='mount';$('mounted-component').value='mount';await after();});
  $('mounted-list').onchange=()=>{selected=Number($('mounted-list').value);if(component==='slot')component='mount';$('mounted-component').value=component;render();place();};
  $('mounted-list').onclick=$('mounted-list').onchange;
  $('mounted-place').onclick=run(()=>{if(component==='slot')component='mount';place();});
  $('mounted-remove').onclick=run(async()=>{if(selected<0)return;remember();removeMounted(state.transform,selected);selected=Math.min(selected,rows().length-1);await after();if(!rows().length)selectEdit('hull');});
  $('mounted-component').onchange=()=>{component=$('mounted-component').value==='link'?'link':'mount';place();};
  $('mounted-item').onchange=run(async()=>{const current=rows()[selected];if(!current)return;remember();current.item=$('mounted-item').value;await after();});
  $('mounted-type').onchange=run(()=>{const current=rows()[selected];if(!current)return;remember();current.type=$('mounted-type').value;refreshCheck();});
  $('mounted-operator').onchange=run(()=>{const current=rows()[selected];if(!current)return;remember();
    if($('mounted-operator').value)current.operator=$('mounted-operator').value;else delete current.operator;refreshCheck();});
  $('mounted-rotate').onchange=run(()=>{const current=rows()[selected];if(!current)return;remember();current.rotate=$('mounted-rotate').checked;refreshCheck();});
  $('mounted-parent').onchange=run(()=>{const current=rows()[selected];if(!current)return;
    const next=hosts.find(h=>h.name===$('mounted-parent').value),old=hosts.find(h=>h.name===current.parent);if(!next||!old)return;
    remember();const local=poseMatrix(current);local.premultiply(matrix(old.matrix)).premultiply(matrix(next.matrix).invert());
    Object.assign(current,poseFromMatrix(local,current),{parent:next.name});sync();place();refreshCheck();});
  for(const type of ['yaw','pitch'])for(let i=0;i<2;i++)$(`mounted-${type}-${i}`).onchange=()=>{const current=rows()[selected],value=Number($(`mounted-${type}-${i}`).value);
    if(!current||!Number.isFinite(value)){fields();return;}const next=[...current[type]];next[i]=value;if(!validLimits(next)){fields();return;}
    remember();current[type]=next;refreshCheck();};
  // Replace gun names the slot (its recipient gun) and the MG or AT rifle in it.
  async function weapons(){await ensureCatalog();return catalog.weapons;}
  const weaponLabel=item=>catalog?.weapons.find(w=>w.id===item)?.label||item;
  async function changeSlot(name,item){remember();setSlot(state.transform,name,item);await restore();refreshCheck();}
  // The weapon is moved on its mount in this tool, which has the gizmo.
  const placeable=name=>slots.some(s=>s.place===name&&!s.mountable)&&!!slotItem(state.transform,name);
  function placeSlot(name){if(!placeable(name))return false;openPlacement();slotSelected=name;component='slot';place();return true;}
  function deactivate(){if(component==='slot')component='mount';sync();controls();}
  // Traverse and elevation arcs of each new weapon, as the passenger MGs'.
  function weaponRigs(){return group.children.filter(h=>h.userData.mountedIndex!==undefined).flatMap(host=>{
    const value=rows()[host.userData.mountedIndex];if(!value)return [];
    return [{key:'mounted_weapon_'+host.userData.mountedIndex,group:host.children[0],revision:host.uuid,
      parentOf:key=>key==='pitch'?'yaw':null,jointOf:object=>object.userData.mesh?.rig_joint||null,
      joints:[{key:'yaw',kind:'traverse',weapons:[t('Arma sin torreta')],matrix:new THREE.Matrix4(),limits:()=>value.yaw},
        {key:'pitch',kind:'elevation',weapons:[t('Arma sin torreta')],matrix:matrix(ELEVATION),limits:()=>value.pitch}]}];});}
  controls();
  return {sync,restore,deactivate,controls,capture,target,pose,weaponRigs,render,weapons,weaponLabel,changeSlot,placeSlot,placeable,slotPlaces:()=>slots,
    receiveCheck(check){hosts=check.armor_hosts||check.hosts||[];slots=check.mount_slots||[];render();onSlots();
      // Slots are drawn on the joints the check lists: the first one draws them.
      const drawn=new Set(group.children.map(n=>n.userData.slot).filter(Boolean));
      if(mountedSlots(state.transform).some(r=>slots.some(s=>s.place===r.place)&&!drawn.has(r.place)))restore();else sync();}};
}
