import {vehicleStorage} from './vehicle-storage.js';
import {pivotEditor as createPivotEditor,applyPose,poseMatrix} from './pivot-editor.js';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { t, startI18n, lang, setLang } from './i18n.js';
import {showAddonReadiness,installMessage} from './addon-readiness.js';
import { portraitCamera, ICON_CAMERA } from './portrait-camera.js';
import { commanderEditor } from './commander-mg.js';
import { diagnosticUI } from './diagnostics.js';
import {applyModelTextures,seatFrame,setSeatPoseOrigin,updateSeatFrames,configureModelTexture} from './model-geometry.js';
import {weaponJointEditor} from './weapon-joints.js';
import {partHighlight} from './part-highlight.js';
import {armorView} from './armor-view.js';
import {weaponArcs} from './weapon-arcs.js';
import {previewJointFrame} from './joint-arcs.js';
import {latestTask} from './latest-task.js';
import {localRequest} from './local-request.js';
import {canPlaceCrew,saveCrewPlacement,placeCrewFigure} from './crew-placement.js';
import {crewVisibilityEditor,crewHidden,visibilityKey} from './crew-visibility.js';
import {operationProgress,elapsedTime,completionSound} from './operation-progress.js';
import {toolProgress} from './tool-progress.js';
import {buildVehicle} from './build-vehicle.js';
import {preferences} from './preferences.js';
import {accessibility,isTextEntry} from './accessibility.js';
import {gunEditor as createGunEditor} from './gun-editor.js';
import {supportEditor as createSupportEditor} from './support-editor.js';
import {armorEditor as createArmorEditor} from './armor-editor.js';
import {smokeEditor as createSmokeEditor} from './smoke-editor.js';
import {pointEditor} from './point-editor.js';
import {riderEditor as createRiderEditor} from './rider-editor.js';
import {passengerEditor as createPassengerEditor} from './passenger-editor.js';
import {uiStorage,saveUISettings} from './ui-settings.js';
import {workspaceLayout} from './workspace-layout.js';
import {inspectorMenus} from './inspector.js';
import {editorHistory} from './editor-history.js';
import {runJob} from './job-client.js';
import {unitArtwork} from './unit-artwork.js';
import {vehicleDesign} from './vehicle-design.js';
import {searchableSelects,moveSelectField} from './searchable-select.js';
let inspector=null;

const $=id=>document.getElementById(id);
const state={token:'',config:null,campaign:null,catalog:[],tab:'campaign',filter:'all',pick:'hull',edit:'hull',hull:null,turret:null,previews:{},busy:false,
  transform:{hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,0],rotation:[0,0,0]}},mounts:{hull:'',turret:''},wire:false,result:null,show:{textures:false,crew:false,passengers:false,arcs:false,hull:true,turret:true,grid:true}};
const artwork=unitArtwork({token:()=>state.token,root:$('unit-list')});
const shopArtwork=unitArtwork({token:()=>state.token,root:$('shop-list')});
const partKey=u=>u&&!u.designPlaceholder?(u.eid?'eid:'+u.eid:u.depot?'depot:'+u.depot:''):'';
const partNames={hull:'chasis',turret:'torreta'};
const plainName=u=>(u.label||u.template.replace(/\([^()]*\)$/,'')).replace(/^(Torreta|Chasis) de /,'');
const names={part:'Depósito',vehicle:'Vehículo',human:'Infantería',tank:'Tanque',armoredcar:'Blindado',artillery:'Artillería',aircraft:'Aeronave',infantry:'Infantería'};
const svgVehicle='<svg viewBox="0 0 48 26" aria-hidden="true"><path fill="currentColor" d="M4 16h39l-4 7H8zm5-9h16l4 8H7zm9-6h13v7H17zm11 2h16v3H29z"/><circle cx="12" cy="22" r="2.5" fill="#18262e"/><circle cx="21" cy="22" r="2.5" fill="#18262e"/><circle cx="30" cy="22" r="2.5" fill="#18262e"/><circle cx="38" cy="22" r="2.5" fill="#18262e"/></svg>';

async function api(path,body,options){return localRequest(path,body,state.token,options);}
const supportDiagnostic=diagnosticUI({state,api,$,selection:()=>({...state,crewStatic:$('crew-static').checked})});
function reportError(error){if(error?.name==='AbortError'){status('Operación cancelada.');return;}supportDiagnostic.record(error);$('error-title').textContent='No se pudo completar';$('error-message').textContent=error.message||String(error);$('error-dialog').showModal();status(error.message||String(error));}
function displayText(id,message){const node=$(id),text=t(message);if(node.textContent!==text)node.textContent=text;}
function status(message){displayText('status-message',message);}
function setBusy(busy,label='Cargando…'){state.busy=busy;$('busy-overlay').classList.toggle('hidden',!busy);$('busy-overlay').classList.remove('passive');$('busy-label').textContent=label;updateBuild();gunEditor?.controls();for(const id of ['load-save','apply-settings','install-button','repair-button','portraits-button','lang-select','operation-select','pick-delete'])$(id).disabled=busy;inspector?.render();}
const sound=completionSound({storage:uiStorage});
$('completion-sound').checked=sound.enabled;
$('completion-sound').onchange=()=>{sound.setEnabled($('completion-sound').checked);sound.arm();saveUISettings({sound:sound.enabled}).catch(reportError);};
document.addEventListener('pointerdown',()=>sound.arm(),{capture:true});
document.addEventListener('keydown',()=>sound.arm(),{capture:true});
let noticeTimer;
function completionNotice(message,elapsedMs){
  const text=t(message)+' · '+t('Tiempo total: '+elapsedTime(elapsedMs));
  $('completion-notice').textContent=text;$('completion-notice').classList.remove('hidden');
  clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('completion-notice').classList.add('hidden'),10000);
  status(text);sound.play();
}
const progress=operationProgress({busy:setBusy,notify:completionNotice,render:({label,completed,total,elapsedMs,cancellable})=>{
  $('cancel-operation').classList.toggle('hidden',!cancellable);
  displayText('busy-label',label);
  displayText('busy-elapsed','Tiempo transcurrido: '+elapsedTime(elapsedMs));
  const bar=$('busy-progress');
  if(state.resourceProgress?.total){bar.max=state.resourceProgress.total;bar.value=state.resourceProgress.completed;$('busy-detail').textContent=t('Recursos revisados')+': '+bar.value+'/'+bar.max;}
  else if(total>1){bar.max=total;bar.value=completed;displayText('busy-detail','Etapas completadas: '+completed+'/'+total);}
  else{bar.removeAttribute('value');displayText('busy-detail','La operación sigue en curso.');}
}});
const toolActivity=toolProgress({render:({active,label,elapsedMs,progress:measured})=>{
  $('inspector-progress').classList.add('hidden');
  if(state.busy)return;
  // Background work (previews, catalogs) never blocks the view: the same bar, passive.
  $('busy-overlay').classList.toggle('hidden',!active);$('busy-overlay').classList.add('passive');if(!active)return;
  $('cancel-operation').classList.toggle('hidden',!state.toolCancellable);
  if($('busy-label').textContent!==t(label))displayText('busy-label',label);
  displayText('busy-elapsed','Tiempo transcurrido: '+elapsedTime(elapsedMs));
  const bar=$('busy-progress');
  if(measured?.total>0){bar.max=measured.total;bar.value=measured.completed;displayText('busy-detail','Etapas completadas: '+measured.completed+'/'+measured.total);}
  else{bar.removeAttribute('value');displayText('busy-detail','La operación sigue en curso.');}
}});
function observeJob(signal,job){
  const available=!!job.cancellable&&['queued','running'].includes(job.status);
  if(signal&&signal===progress.signal){progress.setCancellable(available);state.resourceProgress=job.progress||null;}
  if(signal&&signal===state.toolController?.signal){state.toolCancellable=available;toolActivity.observe(job);inspector?.render();}
}
async function pollJob(path,body){const signal=progress.signal;return runJob(api,path,body,{signal,onStatus:job=>observeJob(signal,job)});}
$('cancel-operation').onclick=()=>{state.userCancelled=true;if(state.busy)progress.cancel();else if(state.toolCancellable){state.toolController?.abort();state.toolCancellable=false;inspector?.render();}};

async function loading(label,run,notice){if(state.busy)throw new Error('Esperá a que termine la operación actual.');status(label);const result=await progress.run([{label,run}],{notice});if(!notice)status('Operación completada');return result;}
async function operation(path,body,label){return loading(label,()=>pollJob(path,body));}

const viewport=$('viewport');
const scene=new THREE.Scene();scene.fog=new THREE.FogExp2(0x17252d,0.0012);
const camera=new THREE.PerspectiveCamera(40,1,0.1,10000);camera.up.set(0,0,1);camera.position.set(240,-300,230);
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setClearColor(0x19272e,0);renderer.outputColorSpace=THREE.SRGBColorSpace;
viewport.prepend(renderer.domElement);
const orbit=new OrbitControls(camera,renderer.domElement);orbit.enableDamping=true;orbit.dampingFactor=.07;orbit.target.set(0,0,25);
const light=new THREE.HemisphereLight(0xe1edf4,0x28301f,2.4);scene.add(light);const sun=new THREE.DirectionalLight(0xffedd5,3.1);sun.position.set(-120,-120,350);scene.add(sun);const fill=new THREE.DirectionalLight(0x86b8d5,1.1);fill.position.set(200,120,130);scene.add(fill);
const grid=new THREE.GridHelper(1600,80,0x445b67,0x2b424f);grid.rotation.x=Math.PI/2;grid.position.z=-.15;grid.material.transparent=true;grid.material.opacity=.42;scene.add(grid);
const originAxes=new THREE.AxesHelper(60);originAxes.visible=false;scene.add(originAxes);
const designFrame=new THREE.Group();scene.add(designFrame);
const groups={hull:new THREE.Group(),turret:new THREE.Group(),commander_mg:new THREE.Group()};scene.add(groups.hull,groups.turret);
const transformControl=new TransformControls(camera,renderer.domElement);transformControl.setSpace('world');transformControl.setSize(.8);scene.add(transformControl.getHelper());
const crewVisibility=crewVisibilityEditor({state,$,remember,applyShow,refreshCheck,roleName,detachHidden:()=>{const figure=state.editSeat&&seatFigure(state.editSeat);if(figure&&crewHidden(state,crewPart(),figure.name)){state.editSeat=null;selectEdit(state.edit);}renderCrewList();}});
const cmdEditor=commanderEditor({state,groups,scene:designFrame,api,quickJob:(path,body,options)=>quickJob(path,body,{signal:state.toolController?.signal||options?.signal}),catalogJob:()=>quickJob('/api/commander-catalog',{}),remember,selectEdit,refreshCheck,reportError,addSeats,applyShow,active:()=>inspector?.navigation.tool==='commander'});
const previewIds=new WeakMap();let previewSeq=0;
function previewId(data){if(!previewIds.has(data))previewIds.set(data,++previewSeq);return previewIds.get(data);}
// A gun being placed: the built joint follows only its horizontal turn (see
// gun_assembly level_joint), so its arc must not tilt or roll with the model.
function levelJointFrame(part){
  const pose=state.transform.turret;if(part!=='turret'||!state.transform.gun_swap||!pose)return null;
  const rotation=pose.rotation||[0,0,0];if(Math.abs(rotation[0])<1e-9&&Math.abs(rotation[1])<1e-9)return null;
  const base=moduleBase(),full=poseMatrix(pose,base),level=poseMatrix({...pose,rotation:[0,0,rotation[2]]},base);
  return frame=>{const local=new THREE.Matrix4().set(...frame.flat());
    const target=level.clone().multiply(local),origin=new THREE.Vector3().setFromMatrixPosition(full.clone().multiply(local));
    target.setPosition(origin);const result=full.clone().invert().multiply(target).elements;
    return [0,1,2,3].map(r=>[0,1,2,3].map(c=>result[c*4+r]));};}
// A gun turned over by hand (a half turn about its barrel) keeps the level
// joint the other way round: its own limits read reversed, as the compiler writes them.
function jointReversed(part,joint){const pose=state.transform.turret;if(part!=='turret'||!state.transform.gun_swap||!pose||!joint?.matrix||joint.bone!==state.previews.turret?.anchor_name)return false;
  const r=pose.rotation||[0,0,0];if(Math.abs(r[0])<1e-9&&Math.abs(r[1])<1e-9)return false;
  const base=moduleBase(),local=new THREE.Matrix4().set(...joint.matrix.flat()),axis=m=>new THREE.Vector3().setFromMatrixColumn(m.multiply(local),2);
  return axis(poseMatrix(pose,base)).dot(axis(poseMatrix({...pose,rotation:[0,0,r[2]]},base)))<0;}
function jointLimits(part,joint){const own=state.transform.joint_limits?.[part]?.[joint.bone];if(own)return own;return jointReversed(part,joint)?[-joint.limits[1],-joint.limits[0]]:joint.limits;}
function partRig(part){const data=state.previews[part];if(!data?.joints?.length)return null;
  const parents=new Map((data.bones||[]).map(bone=>[bone.name,bone.parent])),keys=new Set(data.joints.map(joint=>joint.bone));
  const nearest=bone=>{const seen=new Set();while(bone&&!keys.has(bone)&&!seen.has(bone)){seen.add(bone);bone=parents.get(bone);}return keys.has(bone)?bone:null;};
  return {key:part,group:groups[part],revision:previewId(data),parentOf:bone=>parents.get(bone),jointOf:object=>nearest(object.userData.seat?object.userData.bone:object.userData.mesh?.bone),
    joints:data.joints.map(joint=>({key:joint.bone,weapons:joint.weapons||[],matrix:new THREE.Matrix4().set(...previewJointFrame(joint,data.anchor_name,part==='turret'?state.transform.turret?.pivot:null,levelJointFrame(part)).flat()),limits:()=>jointLimits(part,joint)}))};}
var weaponView=weaponArcs({state,groups,camera,viewport,orbit,rigs:()=>[partRig('hull'),partRig('turret'),cmdEditor.weaponRig(),...riderEditor.weaponRigs()],
  describe:(joint,kind)=>`${t(kind==='traverse'?'Giro':'Elevación')}: ${[...new Set(joint.weapons||[])].join(', ')} (${joint.key})`});
$('reset-weapon-pose').onclick=()=>weaponView.reset();
const draftEditor=vehicleDesign({state,$,job:quickJob,displayPart,updateSelection,remember,reportError,mountLabel,receiveCrew:rows=>crewVisibility.receive(rows),onFocus:part=>selectEdit(part)});
state.restoreDesign=()=>draftEditor.refresh(state.pendingTurret);
const gunEditor=createGunEditor({state,$,job:quickJob,displayPart,updateSelection,selectEdit,remember,reportError,design:draftEditor});
const supportEditor=createSupportEditor({state,$,THREE,groups,job:quickJob,displayPart,updateSelection,selectEdit,remember,refreshCheck,reportError});
const armorEditor=createArmorEditor({state,$,groups,scene:designFrame,job:quickJob,loading,remember,selectEdit,refreshCheck,reportError,applyShow});
const smokeEditor=createSmokeEditor({state,$,groups,scene:designFrame,job:quickJob,loading,remember,selectEdit,refreshCheck,reportError,applyShow,active:()=>inspector?.navigation.tool==='smoke'});
const towingEditor=pointEditor({key:'towing',field:'tier',prefix:'towing',front:false,color:0x7fb6ff,state,$,groups,scene:designFrame,remember,selectEdit,refreshCheck,reportError,applyShow,active:()=>inspector?.navigation.tool==='towing'});
const chargeEditor=pointEditor({key:'charge',field:'charge',prefix:'charge',front:true,color:0xff6a3d,state,$,groups,scene:designFrame,remember,selectEdit,refreshCheck,reportError,applyShow,active:()=>inspector?.navigation.tool==='charge'});
const riderEditor=createRiderEditor({state,$,groups,scene:designFrame,job:quickJob,loading,remember,selectEdit,refreshCheck,reportError,applyShow,makeSeat:seat=>makeSeatFigure(seat,'rider_mgs'),active:()=>inspector?.navigation.tool==='rider'});
const passengerEditor=createPassengerEditor({state,$,groups,scene:designFrame,job:quickJob,loading,remember,selectEdit,refreshCheck,reportError,applyShow,makeSeat:seat=>makeSeatFigure(seat,'passenger_seats'),reposeSeat,active:()=>inspector?.navigation.tool==='passengers'});
$('operation-select').onchange=async()=>{
  if(state.busy)return;
  const next=$('operation-select').value,previous=state.operation||'turret';
  if(next==='support'){
    if(state.hull&&!confirm(t('Convertir a apoyo descarta este diseño y retira la torreta. ¿Continuar?'))){$('operation-select').value=previous;return;}
    clearPart('hull',{keepOperation:true});clearPart('turret',{keepOperation:true});
    state.transform={hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,0],rotation:[0,0,0]}};state.pick='hull';
  }else if(state.hull?.depot){$('operation-select').value=previous;reportError(Error(t('Cambiar cañón requiere dos unidades de tu campaña.')));return;
  }else if(state.hull){
    try{await loading('Cargando diseño del vehículo',async()=>{
      if(await draftEditor.switchMode(next)===false){$('operation-select').value=previous;return;}
      if(next==='gun')await gunEditor.restore();
    });}catch(error){reportError(error);}
  }
  state.operation=$('operation-select').value;gunEditor.controls();supportEditor.controls();updatePick();updateSelection();inspector?.render();
};
const pivotEditor=createPivotEditor({scene,control:transformControl,state,$,pose:editablePose,target:poseTarget,base:()=>state.edit==='turret'&&!supportEditor.active()?moduleBase():[0,0,0],remember,apply:()=>{applyTransforms();updateFields();},check:refreshCheck,select:selectEdit});
// Pointing at a list entry that picks a part of the vehicle marks that part in
// 3D. Any list whose value names a bone or a mount works without registering;
// the cases below only say which model, or which bones, a value stands for.
const highlight=partHighlight({scene,groups,previews:()=>state.previews});
const armorLayer=armorView({state,groups,camera,canvas:renderer.domElement,viewport});
const HOST_PICKERS=['armor-parent','smoke-parent','rider-parent','passenger-parent','cmd-parent'];
const CHASSIS_PICKERS=['turret-target','design-turret-pos','design-turret-limits','design-module-crew','turret-saved','turret-removed'];
function hostPoint(name){const host=[...(state.check?.armor_hosts||[]),...(state.check?.hosts||[])].find(h=>h.name===name);if(!host)return null;
  const m=host.matrix;designFrame.updateWorldMatrix(true,false);return designFrame.localToWorld(new THREE.Vector3(m[0][3],m[1][3],m[2][3]));}
function partSpec(select,value){
  if(value===null||value===undefined||value==='')return select.id==='mount-select'&&state[state.edit]?{whole:state.edit}:null;
  if(value==='@hull')return {whole:'hull'};
  if(select.id==='gun-recipient'||select.id==='gun-donor'){const part=select.id==='gun-recipient'?'hull':'turret',bones=gunEditor.bones(part,value);return bones?{bones,parts:[part],subtree:false}:null;}
  // A free deck mount has no bone: point at the deck spot once it is known.
  if(value==='@free'){if(!Array.isArray(state.freeAnchor))return null;groups.hull.updateWorldMatrix(true,false);return {bones:[],parts:['hull'],point:groups.hull.localToWorld(new THREE.Vector3(...state.freeAnchor))};}
  const bone=String(value).replace(/^@free:/,'');
  if(bone.startsWith('@'))return null;
  // Lists of the chassis' mounts look only at the chassis: a donor part may share the name.
  // Mount and joint lists belong to the part being edited.
  const parts=CHASSIS_PICKERS.includes(select.id)?['hull']:['mount-select','joint-select'].includes(select.id)&&['hull','turret'].includes(state.edit)?[state.edit]:['hull','turret'];
  const known=parts.some(part=>state.previews[part]?.bones?.some(b=>b.name===bone));
  if(HOST_PICKERS.includes(select.id))return {bones:[bone],parts,point:known?null:hostPoint(bone)};
  return known?{bones:[bone],parts}:null;
}
document.addEventListener('part-hover',event=>{const select=event.target;if(!(select instanceof HTMLSelectElement))return;
  const value=event.detail?.value;if(value===null||value===undefined){highlight.clear();return;}
  try{highlight.show(partSpec(select,value));}catch(error){console.warn('part highlight',error);highlight.clear();}});
const jointEditor=weaponJointEditor({state,groups,$,remember,refreshCheck,active:()=>inspector?.navigation.tool==='joints',level:part=>levelJointFrame(part),limitsOf:jointLimits});
transformControl.addEventListener('dragging-changed',event=>{orbit.enabled=!event.value;if(event.value)remember();else{if(state.editSeat)renderCrewList();refreshCheck();}});
let syncing=false;
transformControl.addEventListener('objectChange',()=>{if(syncing||state.busy||state.inspectorPending>0||!inspector?.navigation.tool)return;if(!state.editSeat&&pivotEditor.capture()){updateFields();return;}if(state.editSeat){const figure=seatFigure(state.editSeat);if(figure)state.transform.crew_seats={...(state.transform.crew_seats||{}),[state.editSeat]:saveCrewPlacement(figure,crewGroup())};return;}if(smokeEditor.capture()||towingEditor.capture()||chargeEditor.capture()||riderEditor.capture()||passengerEditor.capture()||armorEditor.capture()||supportEditor.capture()){updateFields();return;}const part=state.edit,g=groups[part],base=part==='turret'?moduleBase():[0,0,0];state.transform[part].position=g.position.toArray().map((v,i)=>Number((v-base[i]).toFixed(3)));const e=new THREE.Euler().setFromQuaternion(g.quaternion,'ZYX');state.transform[part].rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));updateFields();});
function resize(){const width=viewport.clientWidth,height=viewport.clientHeight;renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix();}new ResizeObserver(resize).observe(viewport);
// The near plane follows the orbit distance: a fixed tiny one wasted the depth
// buffer, so close surfaces (armour plates on the hull) flickered when zoomed in.
function fitClipPlanes(){const near=Math.max(.01,camera.position.distanceTo(orbit.target)*.01);if(Math.abs(near-camera.near)>camera.near*.05){camera.near=near;camera.updateProjectionMatrix();}}
function animate(){requestAnimationFrame(animate);orbit.update();fitClipPlanes();updateSeatFrames(groups);renderer.render(scene,camera);}animate();

function disposeGroup(group){while(group.children.length){const child=group.children[0];group.remove(child);child.traverse(node=>{if(!node.userData?.keepGeometry)node.geometry?.dispose();const flat=node.userData?.flat;if(flat&&flat!==node.material)flat.dispose();for(const m of [node.material].flat())if(m&&!m.userData?.shared)m.dispose();});}}
function displayPart(part,data){disposeGroup(groups[part]);for(const mesh of data.meshes){const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(mesh.positions,3));if(mesh.normals?.length)geometry.setAttribute('normal',new THREE.Float32BufferAttribute(mesh.normals,3));if(mesh.uv?.length)geometry.setAttribute('uv',new THREE.Float32BufferAttribute(mesh.uv,2));if(mesh.indices?.length)geometry.setIndex(mesh.indices);if(!mesh.normals?.length)geometry.computeVertexNormals();const material=new THREE.MeshStandardMaterial({color:mesh.color==='#d2a150'?0xc5a36b:mesh.color==='#7696a8'?0x7b9dab:part==='hull'?0x7b9dab:0xc5a36b,metalness:.16,roughness:.84,side:THREE.DoubleSide,wireframe:state.wire});const object=new THREE.Mesh(geometry,material);object.name=mesh.bone||mesh.name||part;object.userData={mesh,flat:material};if(!(part==='turret'&&supportEditor.addMesh(mesh,object)))groups[part].add(object);}state.previews[part]=data;addSeats(part,data);applyShow();applyTransforms();refreshFreeAnchor();refreshCheck();$('crew-static-field').classList.toggle('hidden',!state.previews.turret?.carried);renderCrewList();$('viewport-empty').classList.add('hidden');updateDiagnostics();updateMeshInfo();updateMounts();fitView();selectEdit(state.edit);}
function seatOffset(){const h=state.previews.hull?.seat,t=state.previews.turret?.seat;return Number.isFinite(h)&&Number.isFinite(t)?h-t:0;}
function hullIsFree(){return String(state.mounts.hull||state.previews.hull?.anchor_name||'').startsWith('@free');}
async function quickJob(path,body,{signal=progress.signal||state.toolController?.signal}={}){
  if(signal&&signal===state.toolController?.signal)toolActivity.stage(path==='/api/design-catalog'||path==='/api/gun-catalog'?'Leyendo armas disponibles…':path==='/api/design-preview'?'Actualizando vista previa del diseño…':'Procesando ajustes…');
  return runJob(api,path,body,{signal,onStatus:job=>observeJob(signal,job)});
}

const compatibilityTask=latestTask((signal,body)=>quickJob('/api/check',body,{signal}),250);
$('cancel-check').onclick=()=>{
  compatibilityTask.cancel();state.toolController?.abort();
  state.check={key:checkKey(),status:'unknown',message:t('Operación cancelada.')};updateBuild();status('Operación cancelada.');
};
$('check-again').onclick=()=>{state.check=null;refreshCheck();};
const freeAnchorTask=latestTask((signal,body)=>quickJob('/api/free-anchor',body,{signal}),0);
const freeAnchorKey=()=>JSON.stringify([state.hull?.template,state.turret?.template,state.mounts.hull,state.mounts.turret]);
async function refreshFreeAnchor(){
  freeAnchorTask.cancel();state.freeAnchor=null;
  if(!hullIsFree()||!state.hull||!state.turret)return;
  const designAnchor=state.previews?.hull?.free_anchor;
  if(Array.isArray(designAnchor)&&draftEditor.active()){state.freeAnchor=designAnchor;applyTransforms();return;}
  const key=freeAnchorKey();
  try{
    const r=await freeAnchorTask.run({hull:state.hull.template,donor:state.turret.template,donor_mount:state.mounts.turret||undefined,recipient_mount:state.mounts.hull||undefined});
    if(key!==freeAnchorKey()||!hullIsFree())return;
    state.freeAnchor=r.anchor;applyTransforms();
  }catch(error){if(error.name!=='AbortError'&&key===freeAnchorKey())status(error.message);}
}
function moduleBase(){const a=(hullIsFree()&&state.freeAnchor)||state.previews.hull?.anchor||[0,0,0];return [a[0],a[1],a[2]+seatOffset()];}
function applyTransforms(){queueMicrotask(updateStaticSeats);syncing=true;if(draftEditor.active()){if(groups.hull.parent!==designFrame)designFrame.add(groups.hull,groups.turret);applyPose(designFrame,state.transform.hull);}else{if(groups.hull.parent!==scene)scene.add(groups.hull,groups.turret);designFrame.position.set(0,0,0);designFrame.rotation.set(0,0,0);}if(supportEditor.active()){for(const part of ['hull','turret']){groups[part].position.set(0,0,0);groups[part].rotation.set(0,0,0);}const node=supportEditor.target(),pose=supportEditor.pose();if(node){applyPose(node,pose);}armorEditor.sync();smokeEditor.sync();towingEditor.sync();chargeEditor.sync();riderEditor.sync();passengerEditor.sync();pivotEditor.sync();syncing=false;return;}for(const part of ['hull','turret']){const t=state.transform[part],anchor=part==='turret'?moduleBase():[0,0,0];if(draftEditor.active()&&part==='hull'){groups.hull.position.set(0,0,0);groups.hull.rotation.set(0,0,0);}else applyPose(groups[part],t,anchor);}cmdEditor.sync();armorEditor.sync();smokeEditor.sync();towingEditor.sync();chargeEditor.sync();riderEditor.sync();passengerEditor.sync();pivotEditor.sync();syncing=false;}
function fitView(direction='perspective'){const box=new THREE.Box3();for(const group of Object.values(groups))if(group.children.length)box.union(new THREE.Box3().setFromObject(group));if(box.isEmpty())return;const center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3()),radius=Math.max(size.x,size.y,size.z,20)*1.65;const vectors={perspective:[1,-1.25,.8],front:[1,0,.15],side:[0,-1,.15],top:[0,-.001,1]};/* vehicles face +X */const vector=new THREE.Vector3(...(vectors[direction]||vectors.perspective)).normalize();camera.position.copy(center).addScaledVector(vector,radius);orbit.target.copy(center);camera.near=Math.max(.01,radius/1000);camera.far=Math.max(10000,radius*30);camera.updateProjectionMatrix();orbit.update();}
function updateMeshInfo(){const values=Object.values(state.previews),triangles=values.reduce((n,v)=>n+(v.stats?.triangles||0),0),meshes=values.reduce((n,v)=>n+(v.stats?.meshes||0),0);$('mesh-info').textContent=`${meshes} piezas · ${triangles.toLocaleString('es-AR')} triángulos`;}
function diagnosticText(d){const messages={mass_requires_component_data:'Se conserva el peso declarado del chasis. Los recursos no separan el peso de la torreta.',animation_rotation_gate:'La orientación del montaje necesita comprobarse en el juego.',wreck_runtime_gate:'Los estados de destrucción necesitan comprobarse en el juego.'};return messages[d.code]||d.message||d.code||String(d);}
function updateDiagnostics(){$('model-diagnostics').replaceChildren();for(const data of Object.values(state.previews))for(const diagnostic of data.diagnostics||[]){const p=document.createElement('div');p.textContent=diagnosticText(diagnostic);$('model-diagnostics').append(p);}}
function mountLabel(name){if(name==='@free')return'Libre: sobre la cubierta (no quita nada)';if(String(name).startsWith('@free:')){const spot=name.slice(6),anchor=state.previews.hull?.mounts?.find(m=>!String(m).startsWith('@free'));return spot===anchor||spot==='turret'?'Libre: sobre el techo de la torreta (gira con ella, no quita nada)':'Libre: sobre '+spot.replace(/_/g,' ')+' (escotilla, no quita nada)';}return name==='turret'?'Torreta: reemplazarla (turret)':'Reemplazar: '+String(name).replace(/_/g,' ');}
function updateMounts(){const data=state.previews[state.edit],mounts=data?.mounts||[];$('mount-select').replaceChildren();for(const name of mounts){const option=new Option(mountLabel(name),name);$('mount-select').add(option);}if(!mounts.length)$('mount-select').add(new Option('Pieza completa (sin punto de giro)',''));$('mount-select').value=state.mounts[state.edit]||data?.anchor_name||mounts[0]||'';$('mount-select').title=state.edit==='hull'?'Dónde va el arma: en lugar de la torreta o libre sobre la cubierta, conservando todo':'Parte del donante que se traslada';const heading=$('mount-select').labels?.[0];if(heading)heading.textContent=t(state.edit==='turret'?'Punto de montaje del donante':'Punto de montaje');}
function poseTarget(){const part=state.edit;if(draftEditor.active()&&part==='hull')return designFrame;return part==='towing'?towingEditor.target():part==='charge'?chargeEditor.target():part==='smoke_launchers'?smokeEditor.target():part==='rider_mgs'?riderEditor.target():part==='passenger_seats'?passengerEditor.target():part==='extra_armor'?armorEditor.target():supportEditor.active()&&part==='turret'?supportEditor.target():groups[part];}
function selectEdit(part){
  if(inspector&&!inspector.navigation.accepts(part))return;
  const crewPlace=inspector?.navigation.tool==='crew'&&part===state.edit?state.editSeat:null,crewMode=transformControl.getMode();
  state.edit=part;state.editSeat=null;renderCrewList();
  $('mount-select').parentElement.classList.toggle('hidden',!['hull','turret'].includes(part)||!!state.pendingTurret?.native);
  transformControl.detach();
  const target=poseTarget();
  if(inspector?.navigation.pose()&&target?.children.length&&editablePose())pivotEditor.attach();
  // A refreshed preview replaces figure objects, but keeps the player's seat
  // selection and move/rotate mode attached to the new object.
  const crewFigure=crewPlace&&seatFigure(crewPlace);
  if(canPlaceCrew(crewFigure,state.transform.crew_exclude||[])&&!crewHidden(state,crewPart(),crewPlace)){
    state.editSeat=crewPlace;transformControl.attach(crewFigure);transformControl.setMode(crewMode);renderCrewList();
  }
  updateFields();updateMounts();cmdEditor.sync();armorEditor.sync();smokeEditor.sync();towingEditor.sync();chargeEditor.sync();riderEditor.sync();passengerEditor.sync();jointEditor.refresh();
  if(supportEditor.active())supportEditor.controls();inspector?.render();
}
function requireFinishedTool(){if(!inspector?.navigation.tool||inspector.navigation.tool==='operation')return true;status(t('Terminá la herramienta actual antes de cambiar de vehículo u operación.'));return false;}
async function toolOperation(run){
  if(state.busy||state.inspectorPending>0)return;
  const before=structuredClone(state.transform),selection=state.transform.assembly_draft?structuredClone({turret:state.turret,pendingTurret:state.pendingTurret}):null;state.toolController=new AbortController();state.toolCancellable=false;
  state.inspectorPending=1;toolActivity.start('Guardando o actualizando ajustes…');inspector?.render();
  try{return await run();}
  finally{
    const cancelled=state.toolController.signal.aborted;state.toolController=null;state.toolCancellable=false;
    try{if(cancelled){toolActivity.stage('Restaurando el diseño anterior…');state.transform=before;if(selection){state.turret=selection.turret;state.pendingTurret=selection.pendingTurret;}await cmdEditor.restore();await armorEditor.restore();await smokeEditor.restore();towingEditor.restore();chargeEditor.restore();await riderEditor.restore();await passengerEditor.restore();await gunEditor.restore();await supportEditor.restore();if(selection)await state.restoreDesign?.();applyTransforms();status('Operación cancelada.');}}finally{state.inspectorPending=0;toolActivity.finish();inspector?.render();renderCrewList();if(cancelled)refreshCheck();}
  }
}

const history=editorHistory();
function snapshotTransform(){return JSON.stringify(state.transform.assembly_draft?{...state.transform,_editor_selection:{turret:state.turret,pendingTurret:state.pendingTurret}}:state.transform);}
function remember(){history.remember(snapshotTransform());updateHistoryButtons();}
function forgetHistory(){history.clear();updateHistoryButtons();}
function updateHistoryButtons(){
  $('undo-button').disabled=!history.available('undo');$('redo-button').disabled=!history.available('redo');
}
async function stepHistory(direction){
  if(state.busy||state.inspectorPending>0||!inspector?.navigation.tool)return;
  const value=history.step(direction,snapshotTransform());
  if(value===null){status(t(direction==='undo'?'Sin cambios para deshacer.':'Sin cambios para rehacer.'));updateHistoryButtons();return;}
  state.transform=JSON.parse(value);const selection=state.transform._editor_selection;delete state.transform._editor_selection;if(selection){state.turret=selection.turret;state.pendingTurret=selection.pendingTurret;}state.crewVisibility=null;updateFields();applyTransforms();
  await toolOperation(async()=>{await cmdEditor.restore();await armorEditor.restore();await smokeEditor.restore();towingEditor.restore();chargeEditor.restore();await riderEditor.restore();await passengerEditor.restore();await gunEditor.restore();await supportEditor.restore();if(draftEditor.active()&&!gunEditor.active())await draftEditor.refresh(state.pendingTurret);});
  selectEdit(inspector.navigation.part());jointEditor.refresh();renderCrewList();refreshCheck();updateBuild();updateHistoryButtons();
}

function editablePose(){if(draftEditor.active()&&state.edit==='turret'&&!state.pendingTurret&&!state.transform.gun_swap)return null;if(state.edit==='turret'&&(state.transform.gun_swap||Array.isArray(state.transform.gun_swaps))&&!(state.transform.gun_swap?.recipient_place&&state.transform.gun_swap?.donor_place))return null;return state.edit==='towing'?towingEditor.pose():state.edit==='charge'?chargeEditor.pose():state.edit==='smoke_launchers'?smokeEditor.pose():state.edit==='rider_mgs'?riderEditor.pose():state.edit==='passenger_seats'?passengerEditor.pose():state.edit==='extra_armor'?armorEditor.pose():supportEditor.active()&&state.edit==='turret'?supportEditor.pose():state.transform[state.edit];}
function updateFields(){const pose=editablePose();if(!pose)return;for(const type of ['position','rotation'])for(let axis=0;axis<3;axis++)document.querySelector(`[data-transform="${type}"][data-axis="${axis}"]`).value=pose[type][axis];}
for(const type of ['position','rotation'])for(let axis=0;axis<3;axis++){const div=document.createElement('div');div.className='axis-field';const label=document.createElement('label');label.textContent=['X','Y','Z'][axis];const input=document.createElement('input');input.type='number';input.step=type==='position'?'0.1':'1';input.value=0;input.dataset.transform=type;input.dataset.axis=axis;input.setAttribute('aria-label',`${type==='position'?'Posición':'Orientación'} ${label.textContent}`);input.addEventListener('focus',remember);input.addEventListener('input',()=>{const value=Number(input.value);if(inspector?.navigation.pose()&&!state.busy&&!state.inspectorPending&&editablePose()&&Number.isFinite(value)){editablePose()[type][axis]=value;applyTransforms();}});div.append(label,input);$(type+'-fields').append(div);}
document.querySelectorAll('[data-transform]').forEach(input=>input.addEventListener('change',refreshCheck));

const searchKey=value=>String(value).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();
// Units a saved change of the design already uses: they leave the list until the
// change is removed (and are consumed when the vehicle is built).
function designDonors(){const draft=state.transform.assembly_draft||{},used=new Set();
  for(const row of [...(draft.turrets||[]),...(draft.guns||[]),...(state.transform.gun_swaps||[]),state.transform.gun_edit?.row].filter(Boolean)){if(row.donor_eid)used.add('eid:'+row.donor_eid);if(row.donor_depot)used.add('depot:'+row.donor_depot);}
  return used;}
function currentRows(){const used=state.tab==='storage'?new Set():designDonors(),current=partKey(state.turret);let rows=state.tab==='storage'?(state.campaign?.stored_vehicles||[]):state.tab==='campaign'?(state.campaign?.units||[]):state.tab==='depot'?(state.campaign?.depot||[]).map(item=>({depot:item.id,part:item.part,template:item.template,label:item.label,kind:'part',...(item.mount?{mount:item.mount}:{})})):state.catalog;const q=searchKey($('unit-search').value);return rows.filter(u=>{const human=u.kind==='human'||u.kind==='infantry';if(human)return false;if(state.tab==='catalog'&&(($('catalog-nation').value&&nationOf(u)!==$('catalog-nation').value)||($('catalog-kind').value&&u.kind!==$('catalog-kind').value)))return false;if((used.has('eid:'+u.eid)||used.has('depot:'+u.depot))&&partKey(u)!==current)return false;return(state.filter==='all'||(state.filter==='human'?human:!human))&&(!q||searchKey(`${u.label||''} ${u.template||u.id} ${u.stage||''} ${u.origin||''} ${u.eid||''} ${t(names[u.kind]||u.kind)}`).includes(q));});}
// Creative mode (Geofisika): any catalog part, filtered by nation and type.
function nationOf(u){const m=/^entity\/-vehicle\/\+?([^/]+)\//.exec(u.definition_path||'');return m?m[1]:'';}
function catalogBar(){$('creative-bar').classList.toggle('hidden',state.tab!=='catalog');if(state.tab!=='catalog')return;
  for(const [id,values,all] of [['catalog-nation',state.catalog.map(nationOf).filter(n=>!['test','small_arms_test','placeholder','generic'].includes(n)),'Todas las naciones'],['catalog-kind',state.catalog.filter(u=>u.kind!=='infantry').map(u=>u.kind),'Todos los tipos']]){
    const select=$(id),current=select.value,list=[...new Set(values.filter(Boolean))].sort();
    if(select.options.length===list.length+1)continue;select.replaceChildren(new Option(t(all),''),...list.map(v=>new Option(id==='catalog-kind'?t(names[v]||v):v,v)));select.value=list.includes(current)?current:'';}}
function renderUnits(){catalogBar();artwork.clear();const rows=currentRows();$('unit-list').replaceChildren();$('unit-count').textContent=state.campaign?.units?.filter(u=>u.kind!=='human'&&u.kind!=='infantry').length||0;$('depot-count').textContent=state.campaign?.depot?.length||0;$('storage-count').textContent=state.campaign?.stored_vehicles?.length||0;if(!rows.length){const empty=document.createElement('div');empty.className='library-empty';empty.textContent=state.tab==='storage'?(state.campaign?.storage_problem||'El Almacén está vacío. Usá Quitar de la campaña para guardar un vehículo completo con sus tripulantes e inventarios.'):state.tab==='campaign'?(state.campaign?'No hay unidades para mostrar con este filtro.':'Abrí una partida para ver tus unidades.'):state.tab==='depot'?'Todavía no hay piezas guardadas. Cuando construyas un vehículo, la torreta que le sacás al chasis y el chasis que queda sin torreta se guardan acá para usarlos después.':'Leé los recursos para explorar el catálogo.';$('unit-list').append(empty);return;}for(const unit of (state.tab==='storage'?rows:rows.slice(0,1000))){const human=unit.kind==='human'||unit.kind==='infantry',key=partKey(unit),isHull=key&&partKey(state.hull)===key,isTurret=key&&partKey(state.turret)===key,card=document.createElement('div');card.className='unit-card'+(human?' human':'');card.tabIndex=0;card.setAttribute('role','button');card.setAttribute('aria-pressed',String(!!(isHull||isTurret)));card.dataset.help=unit.label||unit.template||unit.id||'';card.dataset.eid=unit.eid||'';if(isHull)card.classList.add('selected-hull');if(isTurret)card.classList.add('selected-turret');const icon=document.createElement('div');icon.className='unit-icon';icon.innerHTML=svgVehicle;artwork.attach(icon,unit,state.artRevision||0);const details=document.createElement('div');details.className='unit-details';const title=document.createElement('strong');title.textContent=unit.label||(unit.template||unit.id||'Unidad').replace(/\([^()]*\)$/,'').replaceAll('_',' ');const meta=document.createElement('div');meta.className='unit-meta';const type=document.createElement('span');type.textContent=names[unit.kind]||unit.kind;const other=document.createElement('em');other.textContent=unit.stored?(unit.pending?'Traslado pendiente':([unit.origin_campaign,unit.origin_army].filter(Boolean).join(' · ')||'Fuera de la campaña')):unit.depot?(unit.part==='hull'?'chasis guardado':'torreta guardada'):unit.eid?(unit.stage||'reserva'):(unit.origin||'');meta.append(type,other);details.append(title,meta);const badge=document.createElement('span');badge.className='unit-label';badge.textContent=isHull&&isTurret?(state.editing?'EDITANDO':'LAS DOS'):isHull?'CHASIS':isTurret?'TORRETA':'';card.append(icon,details,badge);const click=()=>selectUnit(unit);card.addEventListener('click',click);card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();click();}});$('unit-list').append(card);}}
async function selectUnit(unit){state.lastSelection={part:state.pick,eid:unit.eid||'',template:unit.template||unit.id||''};if(state.busy||!requireFinishedTool())return;if(state.tab==='storage'){storageEditor.open(unit);return;}if(state.pick==='delete'&&state.tab==='campaign'){storageEditor.store(unit);return;}if(state.tab==='catalog'&&state.creative&&state.pick!=='edit'){unit={template:unit.id||unit.template,label:unit.label,kind:unit.kind,creative:true,depot:'creative',part:state.pick==='turret'?'turret':'hull'};}else if(state.tab==='catalog'){try{await loading('Cargando vehículo',async()=>{const data=await pollJob('/api/preview',{template:unit.id||unit.template,part:'full'});disposeGroup(groups.turret);delete state.previews.turret;displayPart('hull',data);status('Vista de catálogo. Para construir, elegí vehículos de tu partida o piezas del depósito.');},'Vehículo cargado');}catch(error){reportError(error);}return;}if(supportEditor.active()&&state.pick!=='edit'){forgetHistory();try{await loading('Cargando vehículo de apoyo',()=>supportEditor.select(unit),'Vehículo cargado');}catch(error){reportError(error);}return;}if(gunEditor.active()&&state.pick!=='edit'){forgetHistory();try{await loading('Cargando cañón',()=>gunEditor.select(unit,state.pick),'Vehículo cargado');}catch(error){reportError(error);}return;}if(state.pick!=='edit'&&!state.hull?.depot&&!(unit.depot&&unit.part==='hull')&&unit.kind!=='human'&&unit.kind!=='infantry'){try{await loading('Cargando diseño del vehículo',()=>draftEditor.select(unit,state.pick),'Vehículo cargado');}catch(error){reportError(error);}return;}if(unit.kind==='human'||unit.kind==='infantry'){status(`${unit.label||unit.template} · veteranía ${unit.veterancy??unit.score??0}`);return;}if(state.pick==='edit'&&state.tab!=='catalog'){if(unit.depot){status(t('Las piezas del depósito no se editan: usalas para combinar.'));return;}return editVehicle(unit);}if(draftEditor.active()&&!unit.eid&&(unit.depot?unit.part:state.pick)==='hull'){const d=draftEditor.draft()||{};if((draftEditor.turrets().length||d.guns?.length||d.removals?.length||state.pendingTurret)&&!confirm(t('Elegir otro vehículo descarta este diseño sin construir. ¿Continuar?')))return;clearPart('hull');}try{await loading('Cargando vehículo',async()=>{const part=unit.depot?unit.part:state.pick;const data=await pollJob('/api/preview',{template:unit.template,part});state[part]=unit;state.mounts[part]=data.anchor_name||'';state.transform[part]={position:[0,0,0],rotation:[0,0,0]};if(state.transform.joint_limits)delete state.transform.joint_limits[part];if(part==='turret'){state.transform.crew_seats={};state.transform.crew_exclude=[];state.editSeat=null;}forgetHistory();resetAssemblyExtras();displayPart(part,data);updateSelection();selectEdit(part);if(unit.depot&&part!==state.pick)status(`Esa pieza del depósito solo sirve como ${partNames[part]}: se colocó ahí.`);},'Vehículo cargado');}catch(error){reportError(error);}}
function updatePick(){for(const mode of ['hull','turret','edit','delete']){$('pick-'+mode).classList.toggle('active',state.pick===mode);$('pick-'+mode).setAttribute('aria-pressed',String(state.pick===mode));}}
// Editar reuses the combination editor: the vehicle's own chassis and module
// fill both slots (its stored recipe for workshop vehicles) and only that unit
// is rebuilt. Leaving the mode empties the slots.
function setPick(mode){if(state.busy||!requireFinishedTool())return;const wasEdit=['edit','delete'].includes(state.pick);state.pick=mode;if(wasEdit!==['edit','delete'].includes(mode)&&(state.hull||state.turret)){state.editing=null;clearPart('hull');clearPart('turret');}updatePick();updateBuild();}
async function editVehicle(unit){if(state.tab!=='campaign'||!unit.eid){status(t('Para editar, elegí un vehículo de tu partida.'));return;}
  try{await loading('Abriendo vehículo',async()=>{const source=await pollJob('/api/edit-source',{eid:unit.eid});state.designOrigin={eid:unit.eid,recipient:source.recipient,donor:source.donor};const tr=source.transform||{};if(tr.assembly_draft){forgetHistory();resetAssemblyExtras();state.editing={eid:unit.eid,recipe:true};state.hull={...unit,template:source.recipient};state.transform=structuredClone(tr);draftEditor.placeholder();$('operation-select').value=tr.assembly_draft.guns.length?'gun':'turret';await draftEditor.refresh();await gunEditor.restore();await cmdEditor.restore();await armorEditor.restore();await smokeEditor.restore();towingEditor.restore();chargeEditor.restore();await riderEditor.restore();await passengerEditor.restore();state.nameEdited=true;$('vehicle-name').value=unit.label||plainName(unit);updateSelection();return;}if(tr.support_vehicle){forgetHistory();resetAssemblyExtras();state.editing={eid:unit.eid,recipe:source.recipe};state.hull={...unit,template:source.recipient};state.turret={...unit,template:source.donor,label:t('Carga de apoyo')};state.transform=structuredClone(tr);state.transform.hull??={position:[0,0,0],rotation:[0,0,0]};state.transform.turret??={position:[0,0,0],rotation:[0,0,0]};await supportEditor.restore();await armorEditor.restore();await smokeEditor.restore();towingEditor.restore();chargeEditor.restore();await riderEditor.restore();await passengerEditor.restore();state.nameEdited=true;$('vehicle-name').value=unit.label||plainName(unit);updateSelection();return;}const zero=()=>({position:[0,0,0],rotation:[0,0,0]});
    $('operation-select').value=(tr.gun_swap||tr.gun_swaps?.length)?'gun':'turret';gunEditor.controls();supportEditor.controls();
    const gunData=(tr.gun_swap||tr.gun_swaps?.length)?await pollJob('/api/gun-preview',{hull:source.recipient,donor:source.donor,gun_swap:tr.gun_swap,gun_swaps:tr.gun_swaps}):null;
    const hull=gunData?.hull||await pollJob('/api/preview',{template:source.recipient,part:tr.commander_only?'full':'hull',mount:tr.commander_only?undefined:tr.recipient_mount||undefined});
    const turret=gunData?.turret||(tr.commander_only?{meshes:[],seats:[],mounts:[],diagnostics:[]}:await pollJob('/api/preview',{template:source.donor,part:'turret',mount:tr.donor_mount||undefined}));
    forgetHistory();resetAssemblyExtras();state.editing={eid:unit.eid,recipe:source.recipe};
    state.hull={...unit,template:source.recipient};state.turret={...unit,template:source.donor};
    state.mounts={hull:tr.recipient_mount||hull.anchor_name||'',turret:tr.donor_mount||turret.anchor_name||''};
    state.transform={...(tr.gun_swap?{gun_swap:structuredClone(tr.gun_swap)}:{}),...(tr.gun_swaps?{gun_swaps:structuredClone(tr.gun_swaps)}:{}),hull:tr.hull||zero(),turret:tr.turret||zero(),crew_seats:tr.crew_seats||{},crew_exclude:tr.crew_exclude||[],crew_hidden:tr.crew_hidden||[],joint_limits:tr.joint_limits||{},...(tr.commander_only?{commander_only:true}:{}),...(tr.commander_mg?{commander_mg:structuredClone(tr.commander_mg)}:{})};state.editSeat=null;
    if(typeof tr.crew_static==='boolean')$('crew-static').checked=tr.crew_static;
    if(tr.source_recipe)state.transform.source_recipe=structuredClone(tr.source_recipe);
    if(tr.extra_armor)state.transform.extra_armor=structuredClone(tr.extra_armor);
    if(tr.smoke_launchers)state.transform.smoke_launchers=structuredClone(tr.smoke_launchers);
    if(tr.rider_mgs)state.transform.rider_mgs=structuredClone(tr.rider_mgs);
    if(tr.passenger_seats)state.transform.passenger_seats=structuredClone(tr.passenger_seats);
    displayPart('hull',hull);displayPart('turret',turret);await gunEditor.restore();await cmdEditor.restore();await armorEditor.restore();await smokeEditor.restore();towingEditor.restore();chargeEditor.restore();await riderEditor.restore();await passengerEditor.restore();state.nameEdited=true;$('vehicle-name').value=unit.label||plainName(unit);updateSelection();selectEdit(tr.commander_only?'hull':'turret');
    status(t('Editando: mové o rotá el montaje y sus tripulantes, después construí. Solo cambia esta unidad.'));},'Vehículo cargado');}
  catch(error){reportError(error);}}
// A saved design shows its donors, not the placeholder that stands for the next one.
function donorNames(){if(!state.turret.designPlaceholder)return plainName(state.turret);const d=draftEditor.draft()||{};
  return [...new Set([...(d.turrets||[]),...(d.guns||[])].map(r=>{const unit=(state.campaign?.units||[]).find(u=>u.eid===r.donor_eid);return unit?plainName(unit):String(r.donor_template||'').replace(/\([^()]*\)$/,'');}).filter(Boolean))].join(' + ');}
function updateSelection(){for(const part of ['hull','turret']){const unit=state[part];$(part+'-name').textContent=unit?(unit.label||unit.template.replace(/\([^()]*\)$/,'').replaceAll('_',' ')):'Elegí un vehículo';$(part+'-detail').textContent=unit?(state.editing?(part==='hull'?t('Vehículo que estás editando · chasis'):t('Vehículo que estás editando · montaje')):unit.depot?'Pieza del depósito':`Vehículo de tu partida · ${(unit.crew_eids||[]).length} tripulantes`):(part==='hull'?'Movilidad · protección del casco':'Armamento · protección de la torreta');}if(state.hull&&state.turret&&!state.nameEdited)$('vehicle-name').value=[plainName(state.hull),supportEditor.active()?t('Apoyo'):donorNames()].filter(Boolean).join(' + ');if(supportEditor.active()){$('turret-name').textContent=t('Carga de apoyo');$('turret-detail').textContent=t('La torreta se retira. Las cajas son accesorios visuales.');}if(gunEditor.active()){$('hull-detail').textContent=t('Conserva el casco, la torreta y sus tripulantes.');$('turret-detail').textContent=t('Aporta el cañón elegido; se consume esta unidad.');}
$('assembly-policy').textContent=t(gunEditor.active()?'Se consumen las dos unidades. Conserva la torreta receptora; no deposita piezas completas.':supportEditor.active()?'Se convierte una unidad. La torreta retirada va al depósito una sola vez.':'El vehículo nuevo reemplaza a los usados en esta partida. Los veteranos se conservan y las piezas que sobran van al Depósito.');
gunEditor.controls();draftEditor.controls();if(draftEditor.active()){$('assembly-policy').textContent=t('Los cambios guardados se construyen juntos. Cada unidad donante se usa una sola vez.');const d=draftEditor.draft();$('operation-count').textContent=t('Vehículos usados')+': '+(1+new Set([d.turret,...d.guns].filter(Boolean).map(r=>r.donor_eid).filter(Boolean)).size)+' → 1';}renderUnits();updateBuild();}
function buildBlocker(){if(draftEditor.active()&&state.campaign){if(state.gameOpen)return t('Volvé al menú principal del juego (o cerralo) para construir.');if(state.pendingTurret||state.transform.gun_swap)return t('Tocá «Aplicar cambios» para guardar el cambio de torreta o de arma antes de construir.');if(state.designPreviewKey!==draftEditor.key())return state.designRefreshInFlight?t('Esperá la vista previa del diseño.'):t('La vista previa del diseño se detuvo. Tocá «Reintentar vista previa».');return '';}if(!state.campaign&&!state.hull?.creative)return'Abrí una partida para empezar.';if(state.gameOpen)return t('Volvé al menú principal del juego (o cerralo) para construir.');if(!state.hull||!state.turret)return gunEditor.active()?t('Elegí un vehículo receptor y un vehículo donante.'):state.pick==='edit'?'Tocá un vehículo de tu partida para editarlo.':'Elegí un chasis y una torreta.';if(gunEditor.active()&&!state.editing&&state.transform.gun_swap)return t('Tocá «Aplicar cambios» para guardar el cambio de torreta o de arma antes de construir.');if(gunEditor.active()&&!state.transform.gun_swap&&!state.transform.gun_swaps?.length)return t('Guardá al menos un cambio de arma.');if((state.transform.gun_swap||state.transform.gun_swaps?.length)&&state.gunPreviewKey!==gunEditor.key())return t('Elegí dos cañones compatibles y esperá su vista previa.');if(state.transform.support_vehicle){if(state.supportPreviewKey!==supportEditor.key())return t('Esperá la vista previa de la carga de apoyo.');if(!state.transform.support_vehicle.cargo?.length)return t('Colocá al menos un accesorio de carga.');if(!state.transform.support_vehicle.profile)return t('Elegí un perfil de suministro nativo disponible.');return'';}if(state.editing)return'';if(state.hull.eid&&state.hull.eid===state.turret.eid)return'Elegiste el mismo vehículo para las dos partes: cambiá una de ellas.';if(!state.hull.eid&&!state.turret.eid&&!shopOnly())return'Usá al menos un vehículo de tu partida: dos piezas del depósito no tienen tripulación.';return'';}
const ROLE_NAMES={driver:'Conductor',gunner:'Artillero',commander:'Comandante',charger:'Cargador',loader:'Cargador',radioman:'Radio'};
function roleName(role){const base=String(role).replace(/\d+$/,'');return t(ROLE_NAMES[base]||role);}
function checkKey(){return state.hull&&state.turret?JSON.stringify([state.hull.template,state.turret.template,checkTransform()]):'';}
function checkTransform(){if(draftEditor.active())return {...draftEditor.transform(),crew_static:$('crew-static').checked};return{...state.transform,recipient_mount:state.mounts.hull||undefined,donor_mount:state.mounts.turret||undefined,crew_static:$('crew-static').checked};}
function resetAssemblyExtras(){delete state.transform.smoke_launchers;smokeEditor.restore();delete state.transform.towing;towingEditor.restore();delete state.transform.charge;chargeEditor.restore();delete state.transform.rider_mgs;riderEditor.restore();delete state.transform.passenger_seats;passengerEditor.restore();delete state.transform.extra_armor;armorEditor.restore();delete state.transform.crew_hidden;delete state.transform.crew_fixed;state.crewVisibility=null;crewVisibility.render();delete state.transform.commander_only;delete state.transform.commander_mg;cmdEditor.restore();if(['commander_mg','extra_armor','smoke_launchers','rider_mgs','passenger_seats'].includes(state.edit))selectEdit('hull');}
async function refreshCheck(){if(draftEditor.active()&&!state.pendingTurret&&!state.transform.gun_swap&&state.designPreviewKey!==draftEditor.key()){if(state.designRefreshInFlight)return;state.designRefreshInFlight=true;state.userCancelled=false;updateBuild();const shown=!toolActivity.active();if(shown)toolActivity.start('Actualizando vista previa del diseño…');try{await draftEditor.refresh();}catch(error){if(error?.name==='AbortError'&&!state.userCancelled){setTimeout(()=>refreshCheck(),250);return;}reportError(error);return;}finally{state.designRefreshInFlight=false;if(shown)toolActivity.finish();updateBuild();}return refreshCheck();}if(draftEditor.active()&&(state.pendingTurret||state.transform.gun_swap)){state.check={key:checkKey(),status:'unknown',message:t('Tocá «Aplicar cambios» para guardar el cambio de torreta o de arma antes de construir.')};updateBuild();return;}if(state.crewVisibility&&state.crewVisibility.key!==visibilityKey(state)){if(!draftEditor.active())delete state.transform.crew_hidden;state.crewVisibility=null;}const key=checkKey();if(!key||(state.check?.key===key&&state.crewVisibility?.key===visibilityKey(state)))return;state.check={key,status:'pending'};const visibilityContext=visibilityKey(state);updateBuild();
  try{const r=await compatibilityTask.run({hull:state.hull.template,donor:state.turret.template,transform:checkTransform()});if(state.check?.key!==key)return;state.check={...r,key,status:r.ok?'ok':'fail',crew:r.crew||[],message:r.message||''};if(r.ok){cmdEditor.receiveCheck(state.check);armorEditor.receiveCheck(state.check);smokeEditor.receiveCheck(state.check);towingEditor.receiveCheck(state.check);chargeEditor.receiveCheck(state.check);riderEditor.receiveCheck(state.check);passengerEditor.receiveCheck(state.check);if(visibilityContext===visibilityKey(state))crewVisibility.receive(r.crew_visibility);renderCrewList();}}
  catch(error){if(error.name==='AbortError')return;if(state.check?.key===key)state.check={key,status:'unknown',message:error.message};}updateBuild();}
function checkHint(){const check=state.check;if(!check||check.key!==checkKey())return{text:'',blocked:false};
  if(check.status==='pending')return{text:'Revisando si esta combinación se puede construir…',blocked:true};
  if(check.status==='unknown')return{text:t(check.message||'Revisar compatibilidad'),blocked:true};
  if(check.status==='fail')return{text:'Esta combinación no se puede construir: '+t(check.message.split('\n\nDetalle técnico:')[0].replace(/^La operación no se pudo completar\..*$/s,'el taller encontró un problema'))+(check.message.includes('Detalle técnico:')?' ('+t(check.message.split('Detalle técnico:')[1].trim())+')':''),blocked:true};
  if(check.status==='ok'){const order=['driver','commander','gunner','charger','loader','radioman'],rank=r=>{const i=order.indexOf(String(r).replace(/\d+$/,''));return i<0?99:i;};const crew=[...check.crew].sort((a,b)=>rank(a)-rank(b)||String(a).localeCompare(b)).map(roleName);return{text:'Compatible ✓'+(crew.length?` · ${t('Tripulación')} (${crew.length}): ${crew.join(', ')}`:''),blocked:false};}
  return{text:'',blocked:false};}
function updateBuild(){draftEditor.controls();$('cancel-check').classList.toggle('hidden',state.check?.status!=='pending');$('check-again').disabled=state.busy||!state.hull||!state.turret||state.check?.status==='pending';crewVisibility.render();cmdEditor.controls();armorEditor.controls();smokeEditor.controls();towingEditor.controls();chargeEditor.controls();if(state.edit==='commander_mg'&&!state.transform.commander_mg)transformControl.detach();for(const id of ['gun-recipient','gun-donor'])$(id).disabled=state.busy||!!state.editing;const reason=buildBlocker(),hint=checkHint();$('build-button').disabled=state.busy||!!reason||hint.blocked;$('design-retry').classList.toggle('hidden',!(draftEditor.active()&&state.campaign&&!state.pendingTurret&&!state.transform.gun_swap&&state.designPreviewKey!==draftEditor.key()&&!state.designRefreshInFlight));
  // A turret or gun change still being adjusted blocks the build; save it from here (SturmDrachen).
  const pendingGun=state.transform.gun_swap,pending=!!(draftEditor.active()&&state.pendingTurret||pendingGun);$('apply-pending').classList.toggle('hidden',!pending);$('discard-pending').classList.toggle('hidden',!pending);$('discard-pending').disabled=state.busy;
  $('apply-pending').disabled=state.busy||!!(pendingGun&&(!pendingGun.recipient_place||!pendingGun.donor_place||state.turret?.designPlaceholder))&&!state.pendingTurret;
  $('cmd-enabled').disabled=state.busy||!state.hull||!state.turret||state.check?.status==='pending';
  const ready=reason?'':(hint.blocked?'':t('Antes de cambiar tu partida te vamos a pedir confirmación.'));
  $('build-hint').textContent=[hint.blocked?hint.text:t(reason),hint.blocked?'':hint.text,ready].filter(Boolean).join(' — ')||t('Listo. Antes de cambiar tu partida te vamos a pedir confirmación.');
  if(supportEditor.active()){supportEditor.controls();$('align-button').disabled=true;}else $('align-button').disabled=inspector?.navigation.tool!=='position'||state.edit!=='turret';$('build-hint').classList.toggle('hint-error',hint.blocked);$('build-button').dataset.help=$('build-hint').textContent;inspector?.render();}
function clearPart(part,{keepOperation=false}={}){if(draftEditor.active()&&part==='hull'){delete state.transform.assembly_draft;delete state.pendingTurret;state.turret=null;delete state.previews.turret;disposeGroup(groups.turret);}if(inspector?.navigation.tool!=='operation')inspector?.navigation.reset();forgetHistory();resetAssemblyExtras();state[part]=null;state.editing=null;if(!state.nameEdited)$('vehicle-name').value='';delete state.previews[part];if(part==='turret'){$('crew-static-field').classList.add('hidden');state.transform.crew_seats={};state.transform.crew_exclude=[];state.editSeat=null;renderCrewList();}disposeGroup(groups[part]);transformControl.detach();if(!keepOperation&&!state.hull&&!state.turret){delete state.transform.gun_swap;delete state.transform.gun_swaps;$('operation-select').value='turret';gunEditor.controls();supportEditor.controls();}updateSelection();if(!groups.hull.children.length&&!groups.turret.children.length)$('viewport-empty').classList.remove('hidden');}

function reloadText(steps){return steps&&steps.length?'\n\n'+t('Para verlo sin cerrar el juego:')+'\n'+steps.map((s,i)=>`${i+1}. ${s}`).join('\n'):'';}
function setGameStatus(game){game=game||{};state.gameRunning=!!game.running;state.gameOpen=!!game.running&&!game.ready;if(state.config?.linux&&!game.running){$('game-status').textContent=t('Linux: no puedo ver el juego · construí con el juego en el menú o cerrado');$('game-status').title=t('En Linux el juego corre en otro entorno de Proton y la herramienta no puede saber si estás en una batalla.');$('game-dot').classList.remove('blocked','menu');updateBuild();return;}$('game-status').textContent=!game.running?t('Juego cerrado'):game.ready?t('Juego abierto en el menú · podés construir'):t('Juego en una campaña o batalla · volvé al menú');$('game-status').title=game.message||(game.ready?t('Después de construir, recargá los mods desde Extras → Aplicar (te lo indico al terminar).'):'');$('game-dot').classList.toggle('blocked',state.gameOpen);$('game-dot').classList.toggle('menu',!!game.running&&!!game.ready);updateBuild();}
async function refreshConfig(){state.config=await api('/api/config');$('game-path').value=state.config.settings.game_root;setGameStatus(state.config.game);showAddonReadiness($,state.config.addon_readiness);const previous=$('save-select').value;$('save-select').replaceChildren(new Option('Elegir una partida…',''));for(const save of state.config.saves)$('save-select').add(new Option(save.name,save.path));if(previous)$('save-select').value=previous;renderMods();}
function renderMods(){const selected=state.config.settings.mod_roots,mods=[...state.config.installed_mods].sort((a,b)=>{const ai=selected.indexOf(a.root),bi=selected.indexOf(b.root);return(ai<0?999:ai)-(bi<0?999:bi);});$('mod-list').replaceChildren();for(const mod of mods){const row=document.createElement('div');row.className='mod-row';row.dataset.root=mod.root;const check=document.createElement('input');check.type='checkbox';check.id='mod-'+mod.id;check.checked=selected.includes(mod.root);const label=document.createElement('label');label.htmlFor=check.id;label.textContent=mod.name;const small=document.createElement('small');small.textContent=mod.id+' · '+(mod.description||mod.root);label.append(small);const up=document.createElement('button');up.textContent='↑';up.title='Mover antes';up.onclick=()=>{if(row.previousElementSibling)row.parentElement.insertBefore(row,row.previousElementSibling);};const down=document.createElement('button');down.textContent='↓';down.title='Mover después';down.onclick=()=>{if(row.nextElementSibling)row.parentElement.insertBefore(row.nextElementSibling,row);};row.append(check,label,up,down);$('mod-list').append(row);}}
function showStats(stats){state.artRevision=(state.artRevision||0)+1;$('source-count').textContent=`${stats.sources?.length||0} fuentes activas`;$('stats-message').textContent=`${(stats.resources||0).toLocaleString('es-AR')} recursos indexados`;}
const storageEditor=vehicleStorage({$,state,t,pollJob,loading,readSave,status,reportError});
async function readSave(path){const result=await pollJob('/api/load-save',{path});state.campaign=result.campaign;clearPart('hull');clearPart('turret');$('campaign-name').textContent=state.campaign.campaign_name||path.split(/[\\/]/).pop();const humans=state.campaign.units.filter(u=>u.kind==='human').length,vehicles=state.campaign.units.filter(u=>u.kind==='vehicle').length;$('campaign-summary').textContent=`${vehicles} vehículos · ${humans} soldados · ${state.campaign.army||''}`;showStats(result.stats);state.catalog=(await api('/api/catalog')).units;await refreshConfig();renderUnits();if(state.campaign.unresolved_mods?.length)throw new Error('La campaña requiere mods que no se encontraron: '+state.campaign.unresolved_mods.join(', '));if(state.campaign.disabled_mods?.length)status(t('El juego desactiva estos mods por la versión, y el taller también los ignora: ')+state.campaign.disabled_mods.map(m=>m.name).join(', '));if(state.campaign.repaired_save)status(`Se reparó esta partida: una versión anterior del taller la dejaba sin poder cargarse (${state.campaign.repaired_save.inventories} inventarios). Copia original: ${state.campaign.repaired_save.backup}`);if(state.campaign.crew_fitted?.length)status(t('Tripulantes que sobraban pasaron a la reserva (el vehículo ahora tiene menos puestos): ')+state.campaign.crew_fitted.map(row=>(state.campaign.units||[]).find(u=>String(u.template||'').startsWith(row.vehicle))?.label||row.vehicle).join(', '));if(state.campaign.storage_problem)status(state.campaign.storage_problem);if(state.campaign.mod_stack_problem)throw new Error(state.campaign.mod_stack_problem);}
async function loadSave(path){if(!path)return;try{await loading('Leyendo campaña y recursos',()=>readSave(path));}catch(error){reportError(error);}}
async function browse(kind){return(await api('/api/pick',{kind})).path;}

$('load-save').onclick=()=>loadSave($('save-select').value);$('open-save').onclick=async()=>{try{const path=await browse('file');if(path)await loadSave(path);}catch(error){reportError(error);}};
$('settings-button').onclick=$('sources-button').onclick=async()=>{if(state.busy)return;try{await refreshConfig();$('settings-dialog').showModal();}catch(error){reportError(error);}};
$('inspector-button').onclick=()=>document.querySelector('.inspector').classList.toggle('visible');
$('browse-game').onclick=async()=>{try{const path=await browse('directory');if(path)$('game-path').value=path;}catch(error){reportError(error);}};
$('apply-settings').onclick=async()=>{const body={game_root:$('game-path').value,mod_roots:[...document.querySelectorAll('.mod-row')].filter(row=>row.querySelector('input').checked).map(row=>row.dataset.root)};$('settings-dialog').close();try{const result=await operation('/api/configure',body,'Indexando recursos');if(result.campaign_invalidated){state.campaign=null;state.result=null;clearPart('hull');clearPart('turret');$('campaign-name').textContent='Volvé a abrir la campaña';$('campaign-summary').textContent='Los recursos activos cambiaron.';}showStats(result.stats);state.catalog=(await api('/api/catalog')).units;await refreshConfig();renderUnits();}catch(error){reportError(error);}};
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{if(state.busy)return;if(['catalog','storage'].includes(button.dataset.tab)){if(!requireFinishedTool())return;if((state.hull||state.turret)&&!confirm(t('Cambiar de vista descarta la selección y los ajustes sin construir. ¿Continuar?')))return;clearPart('hull');clearPart('turret');}state.tab=button.dataset.tab;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));renderUnits();updateBuild();});
document.querySelectorAll('[data-filter]').forEach(button=>button.onclick=()=>{state.filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b===button));renderUnits();});
$('unit-search').oninput=renderUnits;$('vehicle-name').oninput=()=>{state.nameEdited=$('vehicle-name').value.trim()!=='';};$('pick-hull').onclick=()=>setPick('hull');$('pick-turret').onclick=()=>setPick('turret');$('pick-edit').onclick=()=>setPick('edit');$('apply-pending').onclick=()=>{if(state.busy)return;['gun-save','design-save-turret'].map($).find(button=>!button.disabled)?.click();};$('discard-pending').onclick=()=>{if(!state.busy)(draftEditor.active()?draftEditor.discard():Promise.resolve($('gun-discard').onclick?.())).catch(reportError).finally(()=>{updateBuild();inspector?.render();});};$('pick-delete').onclick=()=>setPick('delete');$('clear-hull').onclick=()=>clearPart('hull');$('clear-turret').onclick=()=>clearPart('turret');
function setTransformMode(mode){state.editPivot=false;
  if(state.busy||state.inspectorPending>0||!(inspector?.navigation.pose()||inspector?.navigation.tool==='crew'))return;
  if(!state.editSeat)selectEdit(inspector.navigation.part());transformControl.setMode(mode);
  for(const [id,value] of [['mode-move','translate'],['mode-rotate','rotate'],['pose-move','translate'],['pose-rotate','rotate']]){
    $(id).classList.toggle('active',mode===value);$(id).setAttribute('aria-pressed',String(mode===value));
  }
}
for(const id of ['mode-move','pose-move'])$(id).onclick=()=>setTransformMode('translate');
for(const id of ['mode-rotate','pose-rotate'])$(id).onclick=()=>setTransformMode('rotate');
$('wireframe').onclick=()=>{state.wire=!state.wire;$('wireframe').classList.toggle('active',state.wire);for(const group of Object.values(groups))group.traverse(child=>{if(child.userData?.mesh){child.userData.flat.wireframe=state.wire;for(const m of [child.material].flat())m.wireframe=state.wire;}});};$('axes').onclick=()=>{originAxes.visible=!originAxes.visible;$('axes').classList.toggle('active',originAxes.visible);};$('fit-view').onclick=()=>fitView();document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>fitView(button.dataset.view));
$('reset-transform').onclick=()=>{if(!inspector?.navigation.pose()||!editablePose())return;remember();Object.assign(editablePose(),{position:[0,0,0],rotation:[0,0,0]});updateFields();applyTransforms();refreshCheck();};$('align-button').onclick=()=>{if(inspector?.navigation.tool!=='position'||state.edit!=='turret'||supportEditor.active()||state.edit==='extra_armor'||state.edit==='smoke_launchers')return;remember();state.transform.turret={position:[0,0,0],rotation:[0,0,0]};selectEdit('turret');applyTransforms();fitView();refreshCheck();};
$('mount-select').onchange=async()=>{if(state.busy)return;const part=state.edit,unit=state[part];if(!unit)return;const mount=$('mount-select').value;if(draftEditor.active()){if(!state.pendingTurret)return;state.mounts[part]=mount;state.pendingTurret[part==='hull'?'recipient_mount':'donor_mount']=mount;try{await loading('Cargando montaje',()=>draftEditor.refresh(state.pendingTurret),'Vehículo cargado');}catch(error){reportError(error);}return;}try{await loading('Cargando montaje',async()=>{const data=await pollJob('/api/preview',{template:unit.template,part:part==='hull'?'hull':'turret',mount});state.mounts[part]=mount;if(state.transform.joint_limits)delete state.transform.joint_limits[part];displayPart(part,data);},'Vehículo cargado');}catch(error){reportError(error);}};
function shopOnly(){return !!(state.hull?.creative||(state.turret?.creative&&!state.hull?.eid));}
function buildBody(){if(draftEditor.active()&&state.editing)return {name:$('vehicle-name').value.trim(),mode:'edit',eid:state.editing.eid,transform:draftEditor.transform()};if(draftEditor.active())return {name:$('vehicle-name').value.trim(),mode:'design',recipient_eid:state.hull.eid,transform:draftEditor.transform()};const h=state.hull,t=state.turret,body={name:$('vehicle-name').value.trim(),transform:{...state.transform,recipient_mount:state.mounts.hull,donor_mount:state.mounts.turret,crew_static:$('crew-static').checked}};if(state.editing)return{...body,mode:'edit',eid:state.editing.eid};if(state.transform.support_vehicle)return{...body,mode:'support',recipient_eid:h.eid};if(h.eid)body.recipient_eid=h.eid;else body.recipient_depot=h.depot;if(t.eid)body.donor_eid=t.eid;else body.donor_depot=t.depot;return body;}
function resultRow(label,value){const div=document.createElement('div');div.className='result-stat';const strong=document.createElement('strong');strong.textContent=label;const text=document.createElement('div');text.textContent=value;div.append(strong,text);return div;}
$('build-button').onclick=()=>{if(state.busy||!requireFinishedTool())return;$('build-shop').checked=false;$('build-shop').closest('label').classList.toggle('hidden',shopOnly());if(shopOnly()){$('result-content').replaceChildren(resultRow(t('Vehículo nuevo'),$('vehicle-name').value.trim()||plainName(state.hull)+' + '+plainName(state.turret)),resultRow(t('Se vende en'),t('La tienda de la Conquista del ejército del chasis')));const note=document.createElement('p');note.textContent=t('Modo creativo: no se usa ninguna unidad ni se cambia ninguna partida.');$('result-content').append(note);$('result-dialog').showModal();return;}if(draftEditor.active()&&!state.editing){const rows=[...draftEditor.turrets(),...draftEditor.draft().guns].filter(Boolean),ids=new Set(rows.map(r=>r.donor_eid).filter(Boolean)),catalog=[...new Set(rows.filter(r=>r.donor_catalog).map(r=>r.donor_template))];$('result-content').replaceChildren(resultRow(t('Vehículo nuevo'),$('vehicle-name').value.trim()||plainName(state.hull)),resultRow(t('Se usan de tu partida'),[plainName(state.hull),...[...ids].map(id=>plainName(state.campaign.units.find(u=>u.eid===id)))].join(', ')),...(catalog.length?[resultRow(t('Del catálogo, sin gastar unidades'),catalog.join(', '))]:[]),resultRow(t('Cambios de arma guardados'),String(rows.length)),resultRow(t('Torretas retiradas'),(draftEditor.draft().removals||[]).map(r=>r.mount).join(', ')||'—'));$('result-dialog').showModal();return;}if(state.transform.support_vehicle){$('result-content').replaceChildren(resultRow(t('Vehículo de apoyo'),$('vehicle-name').value.trim()||plainName(state.hull)),resultRow(t('Se reemplaza en tu partida'),plainName(state.hull)));const note=document.createElement('p');note.textContent=t('Se retira la torreta seleccionada y se conserva el casco. La torreta retirada va al depósito solo en la primera conversión. Los tripulantes sobrantes quedan en reserva. El inventario, combustible y daño conservado no se reponen. Se guarda una copia de seguridad.');$('result-content').append(note);$('result-dialog').showModal();return;}if(state.editing){$('result-content').replaceChildren(resultRow(t('Vehículo editado'),$('vehicle-name').value.trim()||plainName(state.hull)),resultRow(t('Se reemplaza en tu partida'),t('Solo esta unidad. No se usa otro vehículo y no van piezas al depósito.')));const note=document.createElement('p');note.textContent=t('El vehículo conserva su tripulación, veteranía, inventario, combustible y el daño de las piezas que siguen existiendo. Antes de cambiar la partida se guarda una copia de seguridad.');$('result-content').append(note);$('result-dialog').showModal();return;}const h=state.hull,mod=state.turret,name=x=>x.label||x.template,plain=plainName;const used=[h,mod].filter(x=>x.eid).map(name),stored=[h,mod].filter(x=>x.depot).map(name),leftovers=[];if(h.eid&&!(state.transform.gun_swap||state.transform.gun_swaps?.length))leftovers.push('Torreta de '+name(h));if(mod.eid&&!(state.transform.gun_swap||state.transform.gun_swaps?.length))leftovers.push('Chasis de '+name(mod));$('result-content').replaceChildren(resultRow('Vehículo nuevo',$('vehicle-name').value.trim()||plain(h)+' + '+plain(mod)),resultRow('Se usan de tu partida',used.join(t(' y '))||'—'));if(stored.length)$('result-content').append(resultRow('Del depósito',stored.join(t(' y '))));$('result-content').append(resultRow('Van al depósito',leftovers.join(t(' y '))||'—'));const note=document.createElement('p');note.textContent='Los vehículos usados desaparecen de tu partida y el nuevo ocupa su lugar con sus veteranos; los tripulantes que no entren quedan en reserva. Antes de cambiar la partida se guarda una copia de seguridad.'+(stored.length?' Como usás una pieza del depósito, el vehículo nuevo sale reparado y con munición completa.':'');if(state.transform.gun_swap||state.transform.gun_swaps?.length)note.textContent=t('Se conserva la torreta receptora. Se consumen las dos unidades y no se depositan torretas ni chasis completos. Los veteranos sobrantes quedan en reserva.');$('result-content').append(note);$('result-dialog').showModal();};
$('install-button').onclick=async()=>{
  if(state.busy)return;
  $('result-dialog').close();const path=state.campaign?.source_path;
  try{
    if(shopOnly()){const body=buildBody();
      const r=await operation('/api/creative',{recipient:state.hull.template,donor:state.turret.template,transform:body.transform,name:body.name},'Construyendo en modo creativo');
      $('error-title').textContent=t('Vehículo en la tienda');$('error-message').textContent=r.name+(r.offer?`\n${t('Precio')}: ${r.offer.cost} · ${t('etapa')} ${r.offer.stage}`:'')+reloadText(r.reload_steps);
      $('error-dialog').showModal();await refreshConfig();return;}
    const body={...buildBody(),shop:$('build-shop').checked};
    const {done,portraitWarning,elapsedMs}=await buildVehicle(progress,{
      generate:()=>pollJob('/api/generate',body),
      portrait:id=>saveModelPortrait(id,true),
      install:()=>pollJob('/api/install',{}),
      reload:async()=>{state.nameEdited=false;state.pick='hull';updatePick();$('vehicle-name').value='';await readSave(path);},
    });
    $('error-title').textContent='Vehículo construido';
    showAddonReadiness($,done.addon_readiness);
    $('error-message').textContent=installMessage(done)+reloadText(done.reload_steps)+(portraitWarning?'\n\n'+portraitWarning:'')+`\n\nPodés seguir armando otro vehículo: la lista y el depósito ya se actualizaron.`+'\n\n'+t('Tiempo total: '+elapsedTime(elapsedMs));
    $('error-dialog').showModal();
  }catch(error){reportError(error);}
};
$('repair-button').onclick=async()=>{if(state.busy)return;if(!state.campaign){reportError(new Error(t('Seleccioná primero una campaña.')));$('save-select').focus();return;}if(!confirm(t('Actualizar vehículos vuelve a armar únicamente los vehículos presentes en la partida abierta. Se guarda una copia del mod anterior. ¿Continuar?')))return;try{const r=await operation('/api/repair',{source_hash:state.campaign.source_hash},'Actualizando vehículos');const portraits=await regeneratePortraits(true,r.rebuilt);const label=id=>(r.labels||{})[id]||id;$('error-title').textContent='Vehículos actualizados';$('error-message').textContent=(r.rebuilt.length?`Actualizados (${r.rebuilt.length}):\n${r.rebuilt.map(id=>'· '+label(id)).join('\n')}`:'No se actualizó ningún vehículo.')+(r.skipped.length?`\n\nSe dejaron como estaban:\n${r.skipped.map(x=>'· '+label(x.id)+': '+x.reason).join('\n')}`:'')+'\n\n'+portraitSummary(portraits,false)+reloadText([...new Set([...(r.reload_steps||[]),...(portraits.reload_steps||[])])])+(r.crew_fitted?.length?'\n\n'+t('Tripulantes que sobraban pasaron a la reserva (el vehículo ahora tiene menos puestos): ')+r.crew_fitted.map(row=>label(row.vehicle)).join(', '):'')+(r.backup?`\n\nCopia del mod anterior: ${r.backup}`:'');$('error-dialog').showModal();await refreshConfig();}catch(error){reportError(error);}};
// Tienda: workshop vehicles sold in the Conquest shop. Only the addon changes.
async function openShop(){const status=$('shop-status');status.textContent=t('Calculando precios…');shopArtwork.clear();$('shop-list').replaceChildren();$('shop-apply').disabled=true;$('shop-dialog').showModal();
  try{const data=await api('/api/shop');const rows=data.vehicles||[];status.textContent='';
    if(!rows.length){status.textContent=t('Todavía no hay vehículos creados con el taller.');return;}
    for(const row of rows){const label=document.createElement('label');label.className='shop-row';const box=document.createElement('input');box.type='checkbox';box.checked=row.on;box.dataset.id=row.id;box.dataset.on=String(row.on);
      const icon=document.createElement('span');icon.className='shop-icon';icon.setAttribute('aria-hidden','true');icon.innerHTML=svgVehicle;shopArtwork.attach(icon,row,state.artRevision||0);
      const name=document.createElement('strong');name.textContent=row.name;name.title=(row.parts||[]).join(' + ');
      const price=document.createElement('small');price.textContent=row.problem?t('Sin precio: ninguna parte se vende en la tienda'):row.cost!=null?`${row.cost} · ${t('etapa')} ${row.stage}`:'';
      box.onchange=()=>{$('shop-apply').disabled=![...$('shop-list').querySelectorAll('input')].some(b=>String(b.checked)!==b.dataset.on);};
      label.append(box,icon,name,price);$('shop-list').append(label);}
    if(!data.priced)status.textContent=t('Leé los recursos para ver precio y etapa.');
  }catch(error){$('shop-dialog').close();reportError(error);}}
$('shop-button').onclick=()=>{if(!state.busy)openShop();};
$('shop-dialog').addEventListener('close',()=>shopArtwork.clear());
$('shop-apply').onclick=async()=>{if(state.busy)return;const changes={};for(const box of $('shop-list').querySelectorAll('input'))if(String(box.checked)!==box.dataset.on)changes[box.dataset.id]=box.checked;
  if(!Object.keys(changes).length)return;$('shop-dialog').close();
  try{const r=await operation('/api/shop',{changes},'Actualizando la tienda');const names=Object.fromEntries([...$('shop-list').querySelectorAll('.shop-row')].map(row=>[row.querySelector('input').dataset.id,row.querySelector('strong').textContent]));
    const lines=Object.entries(r.changes).map(([id,on])=>'· '+(names[id]||id)+': '+(on?(r.offers?.[id]?`${t('en venta por')} ${r.offers[id].cost} (${t('etapa')} ${r.offers[id].stage})`:t('en venta')):t('fuera de la tienda')));
    $('error-title').textContent=t('Tienda actualizada');$('error-message').textContent=lines.join('\n')+reloadText(r.reload_steps)+(r.backup?`\n\n${t('Copia del mod anterior')}: ${r.backup}`:'');$('error-dialog').showModal();await refreshConfig();
  }catch(error){reportError(error);}};
$('creative-mode').onchange=()=>{state.creative=$('creative-mode').checked;
  if(!state.creative){for(const part of ['turret','hull'])if(state[part]?.creative)clearPart(part);}
  status(t(state.creative?'Modo creativo: tocá vehículos del catálogo para usarlos como chasis, torreta o cañón. Con un chasis de tu partida, el vehículo cambia en tu partida.':'Modo creativo desactivado.'));};
for(const id of ['catalog-nation','catalog-kind'])$(id).onchange=()=>renderUnits();
$('design-retry').onclick=()=>{if(!state.busy)refreshCheck().catch(reportError);};
$('portraits-button').onclick=async()=>{try{const r=await regeneratePortraits();$('error-title').textContent='Retratos regenerados';$('error-message').textContent=portraitSummary(r);$('error-dialog').showModal();}catch(error){reportError(error);}};
$('close-button').onclick=async()=>{try{await api('/api/shutdown',{});status('Taller cerrado.');$('build-button').disabled=true;}catch(error){reportError(error);}};

async function start(){try{const session=await fetch('/api/session').then(r=>r.json());state.token=session.token;
  // The tool's own version, so players can tell which one they run when reporting a problem.
  if(session.version)document.querySelector('.version').textContent='v'+session.version;await refreshConfig();if(state.config.campaign_loaded){state.campaign=await api('/api/campaign');$('campaign-name').textContent=state.campaign.campaign_name;const humans=state.campaign.units.filter(u=>u.kind==='human').length,vehicles=state.campaign.units.filter(u=>u.kind==='vehicle').length;$('campaign-summary').textContent=`${vehicles} vehículos · ${humans} soldados · ${state.campaign.army||''}`;state.catalog=(await api('/api/catalog')).units;renderUnits();status('Campaña cargada.');}else status('Elegí una campaña para comenzar.');}catch(error){reportError(error);}}
async function refreshGameReadiness(){try{const result=await api('/api/status');setGameStatus(result.game);showAddonReadiness($,result.addon_readiness);return result;}catch(error){showAddonReadiness($,{required:true,ready:false,message:'No se pudo comprobar la carga del mod. No cargues la campaña hasta verificarlo.'});throw error;}}
$('check-addon').onclick=async()=>{try{await refreshGameReadiness();}catch(error){reportError(error);}};
setInterval(async()=>{try{if(state.token)await refreshGameReadiness();}catch{}},12000);
// Browser mode: the tool ends when its page closes (a reload calls again in time).
window.addEventListener('pagehide',()=>{if(state.token)navigator.sendBeacon('/api/bye',JSON.stringify({token:state.token}));});
window.addEventListener('error',event=>{supportDiagnostic.record(event.message);status(event.message);});
window.addEventListener('unhandledrejection',event=>supportDiagnostic.record(event.reason));
$('undo-button').onclick=()=>stepHistory('undo').catch(reportError);$('redo-button').onclick=()=>stepHistory('redo').catch(reportError);updateHistoryButtons();document.addEventListener('keydown',event=>{if(!(event.ctrlKey||event.metaKey)||event.altKey)return;const target=event.target;if(state.busy||state.inspectorPending>0||!inspector?.navigation.tool||document.querySelector('dialog[open]')||isTextEntry(target))return;const key=event.key.toLowerCase();if(key==='z'&&!event.shiftKey){event.preventDefault();stepHistory('undo').catch(reportError);}else if(key==='y'||(key==='z'&&event.shiftKey)){event.preventDefault();stepHistory('redo').catch(reportError);}});
preferences({storage:uiStorage,onChange:value=>saveUISettings(value).catch(reportError),onTheme:(theme,value)=>{scene.fog.color.set(theme.viewport);orbit.enableDamping=!value.motion&&!matchMedia('(prefers-reduced-motion: reduce)').matches;}});
accessibility({busy:()=>state.busy});
workspaceLayout();
new ResizeObserver(()=>document.documentElement.style.setProperty('--header-height',document.querySelector('.appbar').offsetHeight+'px')).observe(document.querySelector('.appbar'));
startI18n();
// Lists that can grow long open with a search box on top.
searchableSelects(document.querySelector('.workspace'));
$('lang-select').value=lang;$('lang-select').onchange=async event=>{if(state.busy||((state.hull||state.turret)&&!confirm(t('Cambiar el idioma recarga la interfaz y descarta los ajustes sin construir. ¿Continuar?')))){event.target.value=lang;return;}event.target.disabled=true;try{await setLang(event.target.value);}catch(error){event.target.disabled=false;event.target.value=lang;reportError(error);}};
start();

// ---- View options: original textures, crew/passenger seats, part visibility, capture ----
const textureMaterials=new Map();
const texturePending=new Set();
// Portraits always use the compiler's complete model, including vehicles made
// from other workshop vehicles. This scene owns its meshes/materials/textures:
// the editor's camera, visibility, gizmo, history and materials remain untouched.
async function portraitTexture(mtl,token){
  const control=new AbortController(),timer=setTimeout(()=>control.abort(),15000);let url;
  try{
    const response=await fetch('/api/texture?mtl='+encodeURIComponent(mtl)+'&portrait_token='+encodeURIComponent(token),{headers:{'X-Workshop-Token':state.token},signal:control.signal});
    if(!response.ok||response.status===204)throw new Error('texture');
    url=URL.createObjectURL(await response.blob());
    const texture=await new Promise((resolve,reject)=>{
      let settled=false;const decodeTimer=setTimeout(()=>{settled=true;reject(new Error('texture'));},10000);
      new THREE.TextureLoader().load(url,value=>{clearTimeout(decodeTimer);if(settled){value.dispose();return;}settled=true;resolve(value);},undefined,()=>{clearTimeout(decodeTimer);settled=true;reject(new Error('texture'));});
    });
    configureModelTexture(texture);return texture;
  }finally{clearTimeout(timer);if(url)URL.revokeObjectURL(url);}
}
async function renderPortrait(data,token){
  if(!data?.meshes?.length)throw new Error('El modelo armado no tiene mallas para fotografiar.');
  const photoScene=new THREE.Scene(),model=new THREE.Group(),materials=new Map(),textures=[],pending=[],warnings=[];let gl;
  photoScene.add(model,new THREE.HemisphereLight(0xe1edf4,0x28301f,2.4));
  const key=new THREE.DirectionalLight(0xffedd5,3.1);key.position.set(-120,-120,350);photoScene.add(key);
  const fillLight=new THREE.DirectionalLight(0x86b8d5,1.1);fillLight.position.set(200,120,130);photoScene.add(fillLight);
  const flat=new THREE.MeshStandardMaterial({color:0x5d605c,metalness:.1,roughness:.85,side:THREE.DoubleSide,alphaTest:.03});
  function materialFor(mtl){if(!mtl)return flat;if(materials.has(mtl))return materials.get(mtl);
    const material=flat.clone();materials.set(mtl,material);
    pending.push(portraitTexture(mtl,token).then(texture=>{textures.push(texture);material.color.set(0xffffff);material.map=texture;material.needsUpdate=true;}).catch(()=>warnings.push(mtl)));return material;}
  try{
    for(const mesh of data.meshes){const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(mesh.positions,3));
      if(mesh.normals?.length)geometry.setAttribute('normal',new THREE.Float32BufferAttribute(mesh.normals,3));
      if(mesh.uv?.length)geometry.setAttribute('uv',new THREE.Float32BufferAttribute(mesh.uv,2));
      if(mesh.indices?.length)geometry.setIndex(mesh.indices);if(!mesh.normals?.length)geometry.computeVertexNormals();
      let material=flat;if(mesh.uv?.length&&mesh.submeshes?.length){material=mesh.submeshes.map((sub,index)=>{geometry.addGroup(sub.start,sub.count,index);return materialFor(sub.mtl);});}
      model.add(new THREE.Mesh(geometry,material));
    }
    await Promise.all(pending);
    const W=448,H=560,cam=portraitCamera(model,W,H);
    gl=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});gl.setPixelRatio(1);gl.setSize(W,H,false);
    gl.outputColorSpace=THREE.SRGBColorSpace;gl.toneMapping=renderer.toneMapping;gl.toneMappingExposure=renderer.toneMappingExposure*1.08;
    gl.setClearColor(0x000000,0);gl.render(photoScene,cam);
    const base=document.createElement('canvas');base.width=112;base.height=140;const ctx=base.getContext('2d');
    if(!ctx)throw new Error('No se pudo crear la imagen del retrato.');
    const backdrop=ctx.createRadialGradient(46,52,8,56,70,98);backdrop.addColorStop(0,'#4a4a45');backdrop.addColorStop(.55,'#2a2a27');backdrop.addColorStop(1,'#121211');
    ctx.fillStyle=backdrop;ctx.fillRect(0,0,112,140);ctx.imageSmoothingQuality='high';ctx.drawImage(gl.domElement,0,0,112,140);
    const images=['none','brightness(1.22) contrast(1.05)','brightness(1.12) sepia(.25)','grayscale(.85) brightness(.38)'].map(filter=>{
      const out=document.createElement('canvas');out.width=112;out.height=140;const c=out.getContext('2d');if(!c)throw new Error('No se pudo crear la imagen del retrato.');c.filter=filter;c.drawImage(base,0,0);return out.toDataURL('image/png');});
    // Render at the texture's original coordinates. The server crops the
    // emblem strip and restores native frame alpha, including right padding.
    const icons={};
    for(const [kind,width,height] of [['unit_icon',144,72],['unit_icon_small',112,48]]){
      const scale=3,iconCam=portraitCamera(model,width*scale,height*scale,ICON_CAMERA.margin,ICON_CAMERA.direction);
      gl.setSize(width*scale,height*scale,false);gl.setClearColor(0x000000,0);gl.render(photoScene,iconCam);
      const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const c=canvas.getContext('2d');
      if(!c)throw new Error('No se pudo crear el icono.');c.imageSmoothingQuality='high';c.drawImage(gl.domElement,0,0,width,height);
      const pixels=c.getImageData(0,0,width,height).data;let binary='';for(let i=0;i<pixels.length;i+=8192)binary+=String.fromCharCode(...pixels.subarray(i,i+8192));
      icons[kind]='rgba:'+btoa(binary);
    }
    return {images,icons,warnings:warnings.length?['Algunas texturas no se pudieron cargar; el retrato muestra el modelo armado con colores lisos.']:[]};
  }finally{
    model.traverse(object=>object.geometry?.dispose());flat.dispose();materials.forEach(material=>material.dispose());textures.forEach(texture=>texture.dispose());
    if(gl){gl.dispose();gl.forceContextLoss();}
  }
}
async function saveModelPortrait(id,staged=false){
  const source=await quickJob('/api/portrait-preview',{id,staged});
  const portrait=await renderPortrait(source.preview,source.preview_token);
  const saved=await quickJob('/api/portrait',{id,staged,images:portrait.images,icons:portrait.icons,preview_token:source.preview_token});
  return {...saved,warnings:portrait.warnings};
}
async function regeneratePortraits(allowEmpty=false,identifiers=null){
  if(state.busy)throw new Error('Esperá a que termine la operación actual.');
  setBusy(true,'Regenerando retratos');const result={rendered:[],failed:[],warnings:[]};
  try{
    const catalog=await api('/api/generated-vehicles');
    const allowed=identifiers===null?null:new Set(identifiers);
    const vehicles=catalog.vehicles.filter(vehicle=>allowed===null||allowed.has(vehicle.id));
    const diagnostics=(catalog.diagnostics||[]).filter(row=>allowed===null||identifiers.some(id=>row.path===`assembly_${id}.json`));
    if(!vehicles.length&&!allowEmpty)throw new Error('Todavía no hay vehículos creados con el taller.');
    for(let i=0;i<vehicles.length;i++){const vehicle=vehicles[i];$('busy-label').textContent=`Fotografiando ${i+1}/${vehicles.length}: ${vehicle.name||vehicle.id}`;
      try{const saved=await saveModelPortrait(vehicle.id);result.rendered.push(vehicle.name||vehicle.id);result.reload_steps=saved.reload_steps||result.reload_steps;for(const warning of saved.warnings)if(!result.warnings.includes(warning))result.warnings.push(warning);}
      catch(error){result.failed.push({name:vehicle.name||vehicle.id,reason:error.message,source:vehicle.portrait_source});}
    }
    for(const diagnostic of diagnostics)result.warnings.push(diagnostic.message||diagnostic.code||String(diagnostic));
    status(result.failed.length?'Algunos retratos no se pudieron regenerar.':`Retratos regenerados: ${result.rendered.length}`);return result;
  }finally{setBusy(false);}
}
function portraitSummary(result,includeReload=true){
  const preservation=source=>source==='rendered_model'?'Se conserva el último retrato del modelo armado.':source==='outdated_model'?'Se conserva el retrato anterior; puede no reflejar el modelo actualizado.':['chassis_fallback','donor_fallback'].includes(source)?'Se conserva el retrato de respaldo del vehículo original.':source==='missing'?'No hay un retrato disponible para este vehículo.':'Se conserva el último retrato disponible.';
  return `Retratos del modelo armado: ${result.rendered.length}`+(result.failed.length?'\n\nNo se pudieron regenerar:\n'+result.failed.map(item=>'· '+item.name+': '+t(item.reason)+'\n  '+preservation(item.source)).join('\n'):'')+(result.warnings.length?'\n\n'+result.warnings.join('\n'):'')+(includeReload?reloadText(result.reload_steps):'');
}
function texturedMaterial(mtl,token=''){const key=token+'|'+mtl;if(textureMaterials.has(key))return textureMaterials.get(key);const material=new THREE.MeshStandardMaterial({color:0xffffff,metalness:.1,roughness:.85,side:THREE.DoubleSide,wireframe:state.wire});material.userData.shared=true;textureMaterials.set(key,material);
  const pending=fetch('/api/texture?mtl='+encodeURIComponent(mtl)+(token?'&portrait_token='+encodeURIComponent(token):''),{headers:{'X-Workshop-Token':state.token}}).then(r=>r.ok&&r.status!==204?r.blob():Promise.reject()).then(blob=>new Promise(done=>{const url=URL.createObjectURL(blob);const texture=new THREE.TextureLoader().load(url,()=>{URL.revokeObjectURL(url);done();},undefined,()=>done());configureModelTexture(texture);material.alphaTest=.03;material.map=texture;material.needsUpdate=true;})).catch(()=>{material.color.set(0x77756f);}).finally(()=>texturePending.delete(pending));texturePending.add(pending);
  return material;}
function applyTextures(){applyModelTextures(groups,state.show.textures,texturedMaterial,state.wire);}
const seatGeometry={body:new THREE.CylinderGeometry(3.2,3.6,15,10),head:new THREE.SphereGeometry(3.1,12,10)};
const seatMaterials={crew:new THREE.MeshStandardMaterial({color:0x6fc3df,roughness:.6,transparent:true,opacity:.92}),passenger:new THREE.MeshStandardMaterial({color:0x9ccf7a,roughness:.6,transparent:true,opacity:.92})};
for(const m of Object.values(seatMaterials))m.userData.shared=true;
// A real crew member (one generic soldier) in each seat's pose, replacing the
// placeholder capsules once the server has posed it.
const soldierModels=new Map();
function soldierGeometry(pose){const key=pose||'';if(!soldierModels.has(key))soldierModels.set(key,api('/api/crew-model?pose='+encodeURIComponent(key)).then(data=>({origin:data.pose_origin,parts:data.meshes.map(mesh=>{
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(mesh.positions,3));
  if(mesh.normals?.length)geometry.setAttribute('normal',new THREE.Float32BufferAttribute(mesh.normals,3));else geometry.computeVertexNormals();
  if(mesh.uv?.length)geometry.setAttribute('uv',new THREE.Float32BufferAttribute(mesh.uv,2));geometry.setIndex(mesh.indices);return {geometry,mesh};})})).catch(()=>null));return soldierModels.get(key);}
function attachSoldier(figure,pose,kind){const request=Symbol();figure.userData.soldierRequest=request;soldierGeometry(pose).then(data=>{if(!data||!figure.parent||figure.userData.soldierRequest!==request)return;
  const frame=figure.userData.poseFrame||figure;setSeatPoseOrigin(frame,data.origin);for(const child of [...frame.children])frame.remove(child);
  for(const {geometry,mesh} of data.parts){const object=new THREE.Mesh(geometry,seatMaterials[kind]);object.userData.keepGeometry=true;object.userData.mesh=mesh;object.userData.flat=seatMaterials[kind];frame.add(object);}
  if(state.show.textures)applyTextures();});}
// A seat the vehicle already has, shown with the pose and spot the player chose.
function reposeSeat(place,pose,offset){for(const part of ['hull','turret'])groups[part].traverse(node=>{
  if(!node.userData?.seat||node.name!==place)return;attachSoldier(node,pose,node.userData.seat);
  const origin=node.userData.origin||[0,0,0];node.position.set(...origin.map((v,i)=>v+(offset[i]||0)));});}
function addSeats(part,data){const seats=new THREE.Group();seats.name='seats';for(const seat of data.seats||[])seats.add(makeSeatFigure(seat,part));groups[part].add(seats);}
function makeSeatFigure(seat,part){
  const kind=seat.group==='passenger'?'passenger':'crew',figure=new THREE.Group(),frame=seatFrame(seat.rotation);
  figure.add(frame);figure.userData.poseFrame=frame;
  const body=new THREE.Mesh(seatGeometry.body,seatMaterials[kind]);body.rotation.x=Math.PI/2;body.position.z=7.5;
  const head=new THREE.Mesh(seatGeometry.head,seatMaterials[kind]);head.position.z=18;body.userData.keepGeometry=head.userData.keepGeometry=true;
  frame.add(body,head);figure.position.set(...seat.position);attachSoldier(figure,seat.pose,kind);
  figure.userData.seat=kind;figure.userData.origin=seat.position;figure.userData.bone=seat.bone||null;figure.userData.staticSeat=part==='turret'&&seat.weapon&&!seat.aims;
  figure.userData.weapon=(part==='turret'||draftEditor.active())&&!!seat.weapon;figure.userData.fixed=!!seat.fixed;figure.userData.aims=!!seat.aims;figure.name=seat.place;return figure;
}
function owningSeat(node){while(node&&!node.userData?.seat)node=node.parent;return node;}

function applyShow(){updateStaticSeats();weaponView?.refresh();armorLayer?.sync();const show=state.show;groups.hull.visible=show.hull;groups.turret.visible=show.turret;grid.visible=show.grid;
  for(const part of ['hull','turret','commander_mg','rider_mgs','passenger_seats'])groups[part]?.traverse(node=>{if(node.userData?.seat)node.visible=(part==='rider_mgs'||part==='passenger_seats'||(node.userData.seat==='crew'?show.crew:show.passengers))&&!node.userData.excluded&&!crewHidden(state,part,node.name)&&!(part!=='commander_mg'&&node.name==='commander'&&state.transform.commander_mg);});
  applyTextures();const internal=Object.values(state.previews).reduce((n,p)=>n+(p.internal_crew||0),0);
  $('legend-crew').classList.toggle('hidden',!show.crew);$('legend-passengers').classList.toggle('hidden',!show.passengers);
  $('legend-internal').classList.toggle('hidden',!show.crew||!internal);$('legend-internal').textContent=internal?`${internal} dentro del vehículo (sin asiento visible)`:'';
  document.querySelectorAll('[data-show]').forEach(input=>input.checked=!!show[input.dataset.show]);}
$('view-button').onclick=event=>{event.stopPropagation();$('view-panel').classList.toggle('hidden');$('view-button').classList.toggle('active',!$('view-panel').classList.contains('hidden'));$('view-button').setAttribute('aria-expanded',String(!$('view-panel').classList.contains('hidden')));};
document.addEventListener('click',event=>{if(!event.target.closest('.view-menu')){$('view-panel').classList.add('hidden');$('view-button').classList.remove('active');$('view-button').setAttribute('aria-expanded','false');}});
document.querySelectorAll('[data-show]').forEach(input=>input.onchange=()=>{state.show[input.dataset.show]=input.checked;try{localStorage.setItem('mw-show',JSON.stringify(state.show));}catch{}applyShow();});
try{Object.assign(state.show,JSON.parse(localStorage.getItem('mw-show')||'{}'));}catch{}applyShow();
$('capture').onclick=async()=>{const helper=transformControl.getHelper(),helperVisible=helper.visible;helper.visible=false;renderer.setClearColor(0x19272e,1);updateSeatFrames(groups);renderer.render(scene,camera);const image=renderer.domElement.toDataURL('image/png');helper.visible=helperVisible;renderer.setClearColor(0x19272e,0);
  try{const r=await api('/api/screenshot',{image,name:$('vehicle-name').value.trim()||'vehiculo'});status('Captura guardada: '+r.path);}catch(error){reportError(error);}};
// With "Tripulantes fijos" a carried gun's loaders stand inside the chassis
// outline instead of beside the gun (same 6% margin as the compiler).
// Module crew: who rides along and where each fixed loader sits. The preview
// mannequins use the compiler's own rule, so what you see is what you get.
function crewGroup(){return draftEditor.active()&&!state.pendingTurret?groups.hull:groups.turret;}
function crewPart(){return crewGroup()===groups.hull?'hull':'turret';}
function seatFigure(place){let found=null;crewGroup().traverse(node=>{if(!found&&node.userData?.weapon&&node.name===place)found=node;});return found;}
function moduleCrew(){const rows=[];crewGroup().traverse(node=>{if(node.userData?.weapon&&node.userData.seat)rows.push(node);});return rows;}
function selectSeat(place){if((state.pendingTurret&&!state.pendingTurret.native)||state.busy||state.inspectorPending>0||inspector?.navigation.tool!=='crew')return;const figure=seatFigure(place);if(!canPlaceCrew(figure,state.transform.crew_exclude||[])||crewHidden(state,crewPart(),place))return;
  if(!state.show.crew){state.show.crew=true;try{localStorage.setItem('mw-show',JSON.stringify(state.show));}catch{}applyShow();}
  state.editSeat=place;transformControl.detach();transformControl.setMode('translate');transformControl.attach(figure);$('mode-move')?.classList.add('active');$('mode-rotate')?.classList.remove('active');renderCrewList();status(t('Mové al tripulante con las flechas o usá Girar para ajustar su orientación.'));}
function renderCrewList(){crewVisibility.render();const field=$('crew-field'),list=$('crew-list');if(!field||!list)return;const crew=moduleCrew(),pending=!!state.pendingTurret&&!state.pendingTurret.native;$('crew-pending-field').classList.toggle('hidden',!pending);$('crew-apply-mount').disabled=state.busy||state.inspectorPending>0;field.classList.toggle('hidden',!crew.length||pending);list.replaceChildren();
  const excluded=new Set(state.transform.crew_exclude||[]),manual=state.transform.crew_seats||{};
  for(const figure of crew){const place=figure.name,row=document.createElement('div');row.className='crew-row'+(state.editSeat===place?' active':'');
    const box=document.createElement('input');box.type='checkbox';box.checked=!excluded.has(place);box.disabled=figure.userData.aims;box.title=figure.userData.aims?t('El que apunta el arma siempre va.'):t('Destildalo para que se quede en reserva.');
    box.setAttribute('aria-label',roleName(place));box.onchange=()=>{remember();const set=new Set(state.transform.crew_exclude||[]);if(box.checked)set.delete(place);else{set.add(place);if(state.transform.crew_fixed)delete state.transform.crew_fixed[place];if(state.editSeat===place){state.editSeat=null;selectEdit(state.edit);}}state.transform.crew_exclude=[...set];updateStaticSeats();applyShow();renderCrewList();refreshCheck();};
    const pin=document.createElement('input');pin.type='checkbox';pin.checked=state.transform.crew_fixed?.[place]??figure.userData.fixed;pin.disabled=state.busy||excluded.has(place);pin.setAttribute('aria-label',t('Fijar al chasis')+' '+roleName(place));pin.title=t('Fijar este tripulante al chasis');pin.onchange=()=>{remember();state.transform.crew_fixed={...state.transform.crew_fixed,[place]:pin.checked};refreshCheck();};
    const pinLabel=document.createElement('label');pinLabel.className='crew-fixed-toggle';pinLabel.append(pin,document.createTextNode(t('Fijar al chasis')));
    const label=document.createElement('span');label.textContent=roleName(place)+(figure.userData.aims?' · '+t('apunta'):manual[place]?' · '+t('a mano'):'');
    const locate=document.createElement('button');locate.className='small';locate.textContent=t('Ubicar');locate.setAttribute('aria-label',t('Ubicar')+' '+roleName(place));locate.disabled=!canPlaceCrew(figure,[...excluded])||crewHidden(state,crewPart(),place);locate.onclick=()=>selectSeat(place);
    const reset=document.createElement('button');reset.className='small subtle';reset.textContent='↺';reset.title=t('Volver a la ubicación automática');reset.disabled=!manual[place];
    reset.onclick=()=>{remember();const next={...(state.transform.crew_seats||{})};delete next[place];state.transform.crew_seats=next;updateStaticSeats();renderCrewList();refreshCheck();};
    row.append(box,label,locate,reset,pinLabel);list.append(row);}
}
// Click a loader mannequin in the 3D view to place it.
{let down=null;renderer.domElement.addEventListener('pointerdown',event=>{down=[event.clientX,event.clientY];});
  renderer.domElement.addEventListener('pointerup',event=>{if(state.busy||inspector?.navigation.tool!=='crew'||!down||Math.hypot(event.clientX-down[0],event.clientY-down[1])>4||transformControl.dragging||transformControl.axis)return;down=null;
    const rect=renderer.domElement.getBoundingClientRect(),pointer=new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
    const ray=new THREE.Raycaster();ray.setFromCamera(pointer,camera);const targets=[];crewGroup().traverse(node=>{const figure=owningSeat(node);if(node.isMesh&&figure?.visible&&canPlaceCrew(figure,state.transform.crew_exclude||[]))targets.push(node);});
    const hit=ray.intersectObjects(targets,false)[0];if(hit)selectSeat(owningSeat(hit.object).name);});}
function updateStaticSeats(){const fixed=$('crew-static').checked&&state.previews.turret?.carried;groups.turret.updateMatrixWorld(true);groups.hull.updateMatrixWorld(true);
  const box=new THREE.Box3();groups.hull.traverse(node=>{if(node.userData?.mesh)box.expandByObject(node);});
  const excluded=new Set(state.transform.crew_exclude||[]),manual=state.transform.crew_seats||{};
  const frame=crewGroup();frame.traverse(node=>{if(!node.userData?.weapon)return;node.userData.excluded=excluded.has(node.name);if(node.userData.seat)node.visible=state.show.crew&&!node.userData.excluded&&!crewHidden(state,crewPart(),node.name);
    placeCrewFigure(node,frame,manual[node.name],typeof state.transform.crew_fixed?.[node.name]==='boolean'?false:fixed,box);});}
$('crew-static').addEventListener('change',()=>{updateStaticSeats();renderCrewList();refreshCheck();});
$('crew-apply-mount').onclick=()=>toolOperation(()=>draftEditor.saveTurret()).catch(reportError);
transformControl.addEventListener('objectChange',()=>updateStaticSeats());

// Tool panels own the gizmo; a late preview may redraw geometry but cannot steal focus.
inspector=inspectorMenus({state,support:()=>supportEditor.active(),gun:()=>gunEditor.active(),movingMount:()=>draftEditor.active()&&$('operation-select').value==='turret',moveSelector:moveSelectField,
  onEnter(tool,part){history.begin();updateHistoryButtons();selectEdit(part);if(tool==='crew'){state.show.crew=true;applyShow();}
    if(['operation','position','joints','crew'].includes(tool)&&state.hull?.eid&&!supportEditor.active()&&!gunEditor.active()&&!state.pendingTurret){
      draftEditor.ensure();
      toolOperation(async()=>{await draftEditor.loadChassis();if(tool==='operation')await draftEditor.refresh();
        // Without a choice it opens the turret last saved in the design (a free
        // mount included), else the chassis' own turret.
        else await draftEditor.editTurret(state.designTarget||undefined);}).catch(reportError);
    }
    if(tool==='position'&&gunEditor.active()&&!state.transform.gun_swap&&(state.transform.gun_swaps?.length||state.transform.assembly_draft?.guns.length))$('gun-edit-change').onclick()?.catch(reportError);
    updateBuild();},
  onLeave(){const native=draftEditor.settleNative();
    // Gun placement opens a saved gun change to move it. "Back to menu keeps the
    // design": put that change back (as adjusted, or as it was if left incomplete)
    // instead of leaving it out of the design, which showed the original gun again.
    if(gunEditor.active()&&state.transform.gun_edit)(!$('gun-save').disabled?gunEditor.save():$('gun-discard').onclick()).catch?.(reportError);state.editPivot=false;transformControl.detach();state.editSeat=null;armorEditor.deactivate();smokeEditor.deactivate();towingEditor.deactivate();chargeEditor.deactivate();riderEditor.deactivate();passengerEditor.deactivate();cmdEditor.sync();jointEditor.sync();renderCrewList();if(native)draftEditor.refresh().catch(reportError);refreshCheck();},
  onChange(){
    const editable=(inspector?.navigation.pose()&&!!editablePose())||inspector?.navigation.tool==='crew';
    for(const id of ['mode-move','mode-rotate'])$(id).disabled=state.busy||state.inspectorPending>0||!editable;
    if(!inspector?.navigation.tool)transformControl.detach();
    if(inspector)updateBuild();
  },
});
for(const [id,event] of [
  ['armor-load','onclick'],['armor-parent','onchange'],['armor-add','onclick'],['armor-duplicate','onclick'],['armor-remove','onclick'],
  ['design-save-turret','onclick'],['design-edit-turret','onclick'],['gun-save','onclick'],['gun-save-position','onclick'],['gun-discard','onclick'],['gun-edit-change','onclick'],['gun-remove-change','onclick'],['gun-clear-changes','onclick'],['gun-recipient','onchange'],['gun-donor','onchange'],
  ['design-turret-pos','onchange'],['design-turret-limits','onchange'],['design-module-crew','onchange'],['turret-target','onchange'],['turret-retire','onclick'],['turret-undo-removal','onclick'],
  ['smoke-load','onclick'],['smoke-tubes','onchange'],['smoke-parent','onchange'],['smoke-add','onclick'],['smoke-duplicate','onclick'],['smoke-remove','onclick'],
  ['cmd-add','onclick'],['cmd-remove','onclick'],['cmd-item','onchange'],['cmd-support','onchange'],['cmd-parent','onchange'],
  ['cargo-add','onclick'],['cargo-duplicate','onclick'],['cargo-remove','onclick'],['support-kind','onchange']
]){const run=$(id)[event];$(id)[event]=(...args)=>toolOperation(()=>run(...args));}

$('inspector-cancel').onclick=()=>{state.userCancelled=true;if(state.toolCancellable){state.toolController?.abort();state.toolCancellable=false;inspector.render();}};
