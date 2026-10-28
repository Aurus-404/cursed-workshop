import * as THREE from 'three';
import {applyPose,poseMatrix} from './pivot-editor.js';
import {t} from './i18n.js';

// One point on the vehicle plus the game's own data: the towing hook (its
// weight class) and the demolition charge (its size). Pick the option, the part
// it hangs from, then place the marker with Move, Rotate or XYZ. The arrow
// shows where it faces: the hook backwards, the charge forwards.
// Recipe: state.transform[key] = {[field], parent, position, rotation}.
export function pointEditor({key,field,prefix,front,color,state,$,groups,scene,remember,selectEdit,refreshCheck,reportError,applyShow,active=()=>state.edit===key}){
  const group=groups[key]=new THREE.Group();scene.add(group);
  let hosts=[];
  const row=()=>state.transform[key]||null;
  const matrix=value=>new THREE.Matrix4().set(...value.flat());
  const target=()=>group.children[0]?.children[0];
  function marker(){
    const host=new THREE.Group(),fitting=new THREE.Group();host.matrixAutoUpdate=false;host.add(fitting);
    const ball=new THREE.Mesh(new THREE.SphereGeometry(2.2,16,12),new THREE.MeshBasicMaterial({color,depthTest:false,transparent:true,opacity:.85}));
    ball.renderOrder=1000;fitting.add(ball);
    const arrow=new THREE.ArrowHelper(new THREE.Vector3(1,0,0),new THREE.Vector3(),10,color,2.4,1.4);arrow.renderOrder=1000;fitting.add(arrow);
    return host;}
  function clear(){for(const node of [...group.children]){node.traverse(n=>{n.geometry?.dispose();n.material?.dispose?.();});node.removeFromParent();}}
  function sync(){const value=row(),host=group.children[0];if(!host)return;const parent=value&&hosts.find(p=>p.name===value.parent);
    host.visible=!!parent;if(parent){host.matrix.copy(matrix(parent.matrix));applyPose(host.children[0],value);}group.updateMatrixWorld(true);}
  function controls(){const ready=!!(state.hull&&state.turret),busy=state.busy;
    for(const id of ['option','parent'])$(prefix+'-'+id).disabled=busy||!ready;
    for(const id of ['remove','edit'])$(prefix+'-'+id).disabled=busy||!row();}
  function render(){const value=row();$(prefix+'-option').value=value?.[field]||'';
    $(prefix+'-parent').replaceChildren(...hosts.map(h=>new Option(h.name,h.name)));
    if(value&&hosts.some(h=>h.name===value.parent))$(prefix+'-parent').value=value.parent;
    $(prefix+'-parent-field').classList.toggle('hidden',!value);controls();}
  function restore(){clear();if(row())group.add(marker());sync();render();applyShow?.();}
  // A new point starts at the back (hook) or the front (charge) of the vehicle, at mid height.
  function start(parentName){const host=hosts.find(h=>h.name===parentName);const box=new THREE.Box3();
    for(const part of ['hull','turret']){groups[part]?.updateWorldMatrix(true,true);if(groups[part])box.union(new THREE.Box3().setFromObject(groups[part]));}
    if(!host||box.isEmpty())return [0,0,0];const centre=box.getCenter(new THREE.Vector3());
    const point=new THREE.Vector3(front?box.max.x+1:box.min.x-1,centre.y,box.min.z+(box.max.z-box.min.z)*(front?.55:.3));
    scene.updateWorldMatrix(true,false);return scene.worldToLocal(point).applyMatrix4(matrix(host.matrix).invert()).toArray().map(v=>Number(v.toFixed(3)));}
  async function choose(){const option=$(prefix+'-option').value;remember();
    if(!option){delete state.transform[key];restore();selectEdit('hull');refreshCheck();return;}
    if(!hosts.length)await refreshCheck();
    if(row()){row()[field]=option;}
    else{const parent=hosts.some(h=>h.name==='body')?'body':hosts[0]?.name;if(!parent)throw Error(t('No hay dónde fijar el punto en este vehículo.'));
      state.transform[key]={[field]:option,parent,position:start(parent),rotation:front?[0,0,0]:[0,0,180]};}
    restore();selectEdit(key);refreshCheck();}
  function capture(){if(state.edit!==key||!row())return false;const node=target();if(!node)return false;
    row().position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');row().rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;}
  const run=fn=>async()=>{if(state.busy)return;try{await fn();}catch(error){reportError(error);}};
  $(prefix+'-option').onchange=run(choose);
  // Another part keeps the point where it is in the vehicle, as the smoke launchers do.
  $(prefix+'-parent').onchange=run(async()=>{const value=row(),next=hosts.find(h=>h.name===$(prefix+'-parent').value),old=value&&hosts.find(h=>h.name===value.parent);
    if(!value||!next||!old)return;remember();const local=poseMatrix(value).premultiply(matrix(old.matrix)).premultiply(matrix(next.matrix).invert());
    const p=new THREE.Vector3(),q=new THREE.Quaternion(),s=new THREE.Vector3();local.decompose(p,q,s);
    value.parent=next.name;value.position=p.toArray();value.rotation=new THREE.Euler().setFromQuaternion(q,'ZYX').toArray().slice(0,3).map(THREE.MathUtils.radToDeg);
    sync();selectEdit(key);refreshCheck();});
  $(prefix+'-remove').onclick=run(async()=>{if(!row())return;remember();delete state.transform[key];restore();selectEdit('hull');refreshCheck();});
  $(prefix+'-edit').onclick=()=>{if(row())selectEdit(key);};
  controls();
  return {sync,restore,controls,capture,target,pose:()=>row()||{position:[0,0,0],rotation:[0,0,0]},deactivate(){sync();controls();},
    receiveCheck(check){hosts=check?.armor_hosts||check?.hosts||[];render();sync();},active};
}
