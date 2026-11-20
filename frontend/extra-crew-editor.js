import * as THREE from 'three';
import {t} from './i18n.js';
import {applyPose} from './pivot-editor.js';

// Extra crew: more soldiers than the vehicle's native seats, on the part the
// crew tool is editing. A loader joins the weapon of that part and shortens
// its reload by `reload_bonus` % each; the other jobs are reserves who take
// the place of whoever falls. Passengers belong to the Passengers tool.
export const EXTRA_CREW_ROLES=['charger','gunner','commander','driver'];
export const DEFAULT_RELOAD_BONUS=15;
const ROLE_LABELS={charger:'Cargador',gunner:'Artillero',commander:'Comandante',driver:'Conductor'};
export const extraCrewRoleLabel=role=>t(ROLE_LABELS[role]||role);

export function extraCrewRows(transform){return transform?.extra_crew?.added||[];}
export function addExtraCrew(transform,row){
  transform.extra_crew??={added:[],reload_bonus:DEFAULT_RELOAD_BONUS};transform.extra_crew.added??=[];
  transform.extra_crew.added.push(row);return transform.extra_crew.added.length-1;}
export function removeExtraCrew(transform,index){const rows=extraCrewRows(transform);if(index<0||index>=rows.length)return false;
  rows.splice(index,1);if(!rows.length)delete transform.extra_crew;return true;}
export function setReloadBonus(transform,value){const number=Number(value);if(!transform.extra_crew||!Number.isFinite(number))return false;
  transform.extra_crew.reload_bonus=Math.min(50,Math.max(0,Math.round(number)));return true;}
// The bone a new crewman hangs from: the edited mount, else the hull's body.
export function extraCrewParent(hosts,part){const names=hosts.map(h=>h.name);
  if(part&&part!=='@hull'&&names.includes(part))return part;
  return names.includes('body')?'body':names[0]||'';}
// Reload multiplier the build gives a weapon with `count` extra loaders.
export function reloadFactor(bonus,count){return Math.max(.25,1-(Number(bonus)||0)/100*count);}

export function extraCrewEditor({state,$,scene,control,remember,refreshCheck,makeSeat,applyShow,part,reference}){
  const group=new THREE.Group();group.name='extra_crew';scene.add(group);
  let hosts=[],selected=-1;
  const rows=()=>extraCrewRows(state.transform);
  const matrix=value=>new THREE.Matrix4().set(...value.flat());
  const upright=[[1,0,0],[0,1,0],[0,0,1]];
  const target=()=>group.children.find(n=>n.userData.crewIndex===selected)?.children[0];
  const placing=()=>!!control.object&&control.object===target();
  function dispose(node){node.traverse(n=>{if(!n.userData.keepGeometry)n.geometry?.dispose();for(const m of [n.material].flat())if(m&&!m.userData?.shared)m.dispose();});node.removeFromParent();}
  function sync(){for(const host of group.children){const row=rows()[host.userData.crewIndex];if(!row)continue;
    const parent=hosts.find(h=>h.name===row.parent);host.visible=!!parent;if(parent)host.matrix.copy(matrix(parent.matrix));applyPose(host.children[0],row);}
    group.updateMatrixWorld(true);}
  function draw(){const keep=placing();for(const n of [...group.children])dispose(n);
    rows().forEach((row,i)=>{const host=new THREE.Group(),fitting=new THREE.Group();host.matrixAutoUpdate=false;host.userData={crewIndex:i};host.add(fitting);
      fitting.add(makeSeat({place:'extra_crew_'+(i+1),group:'crew',position:[0,0,0],rotation:upright}));group.add(host);});
    sync();applyShow?.();if(keep&&target())control.attach(target());}
  function controls(){const ready=!!(state.hull&&state.turret)&&!(state.pendingTurret&&!state.pendingTurret.native),busy=state.busy;
    $('extra-crew-field').classList.toggle('hidden',!ready);
    for(const id of ['extra-crew-role','extra-crew-add'])$(id).disabled=busy||!ready||!hosts.length;
    $('extra-crew-list').disabled=busy||!rows().length;
    for(const id of ['extra-crew-place','extra-crew-remove'])$(id).disabled=busy||selected<0;
    $('extra-crew-parent').disabled=busy||selected<0;
    $('extra-crew-bonus').disabled=busy||!rows().length;
    $('extra-crew-bonus-field').classList.toggle('hidden',!rows().some(r=>r.role==='charger'));}
  function render(){const list=$('extra-crew-list');list.replaceChildren();
    rows().forEach((row,i)=>list.add(new Option(`${i+1}. ${extraCrewRoleLabel(row.role)} · ${row.parent}`,String(i))));
    if(selected>=rows().length)selected=rows().length-1;list.value=selected>=0?String(selected):'';
    list.classList.toggle('hidden',!rows().length);
    $('extra-crew-parent').replaceChildren(...hosts.map(h=>new Option(h.name,h.name)));
    if(selected>=0)$('extra-crew-parent').value=rows()[selected].parent;
    $('extra-crew-bonus').value=String(state.transform.extra_crew?.reload_bonus??DEFAULT_RELOAD_BONUS);
    const loaders=rows().filter(r=>r.role==='charger').length;
    $('extra-crew-bonus-result').textContent=loaders?t('Recarga del arma')+': '+Math.round(reloadFactor(state.transform.extra_crew?.reload_bonus??DEFAULT_RELOAD_BONUS,loaders)*100)+' %':'';
    controls();}
  // Beside a native seat of the same job, in the chosen part's frame.
  function defaultRow(role){const parent=extraCrewParent(hosts,part());let position=[0,0,0];
    const host=hosts.find(h=>h.name===parent),world=reference(role);
    if(host&&world){scene.updateWorldMatrix(true,false);position=scene.worldToLocal(world.clone()).applyMatrix4(matrix(host.matrix).invert()).toArray().map(v=>Number(v.toFixed(3)));position[1]+=12;}
    return {role,parent,position,rotation:[0,0,0]};}
  function after(){draw();render();refreshCheck();}
  function place(){const node=target();if(!node)return;state.editSeat=null;control.detach();control.attach(node);control.setMode(control.getMode?.()==='rotate'?'rotate':'translate');}
  function capture(){if(!placing())return false;const node=target(),row=rows()[selected];
    row.position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');row.rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;}
  const run=fn=>()=>{if(state.busy)return;fn();};
  $('extra-crew-add').onclick=run(()=>{if(!hosts.length)return;remember();selected=addExtraCrew(state.transform,defaultRow($('extra-crew-role').value));after();place();});
  $('extra-crew-list').onchange=()=>{selected=Number($('extra-crew-list').value);render();place();};
  $('extra-crew-place').onclick=run(place);
  $('extra-crew-remove').onclick=run(()=>{if(selected<0)return;remember();if(placing())control.detach();removeExtraCrew(state.transform,selected);selected=Math.min(selected,rows().length-1);after();});
  $('extra-crew-parent').onchange=run(()=>{if(selected<0)return;remember();rows()[selected].parent=$('extra-crew-parent').value;sync();render();refreshCheck();});
  $('extra-crew-bonus').onchange=run(()=>{remember();setReloadBonus(state.transform,$('extra-crew-bonus').value);render();refreshCheck();});
  function restore(){if(selected>=rows().length)selected=rows().length-1;draw();render();}
  function setMode(mode){if(placing())control.setMode(mode);}
  return {restore,sync,render,controls,capture,placing,setMode,target,group,
    stop(){if(placing())control.detach();},
    receiveCheck(check){hosts=check.armor_hosts||check.hosts||[];draw();render();}};
}
