import assert from 'node:assert/strict';
import * as THREE from 'three';
import {previewMesh,applyModelTextures,seatFrame,setSeatPoseOrigin,updateSeatFrames,configureModelTexture} from '../frontend/model-geometry.js';
import {drawJointArc,previewJointFrame} from '../frontend/joint-arcs.js';
import {weaponJointEditor} from '../frontend/weapon-joints.js';
const configured=configureModelTexture(new THREE.Texture());
assert.equal(configured.wrapS,THREE.RepeatWrapping);assert.equal(configured.wrapT,THREE.RepeatWrapping);
assert.equal(configured.flipY,false);assert.equal(configured.colorSpace,THREE.SRGBColorSpace);

globalThis.Option=class {constructor(label,value){this.text=label;this.value=value;}};
const reflected=[[1,0,0],[0,-1,0],[0,0,1]],frame=seatFrame(reflected);
frame.updateMatrixWorld(true);
assert.equal(frame.matrixWorld.determinant(),1,'a reflected vehicle seat must not mirror human anatomy');
assert.deepEqual(new THREE.Vector3(1,2,3).applyMatrix4(frame.matrixWorld).toArray(),[1,2,3]);
assert.deepEqual(reflected,[[1,0,0],[0,-1,0],[0,0,1]],'native seat data stays intact');
// Asymmetric points distinguish an anatomical reflection from a heading change.
// The same actor must retain its left/right sides on yawed, tilted and scaled
// seats; forward and up must remain precisely those of the source seat.
for(const angles of [[0,0,0],[.3,.5,1.2],[-.7,1.1,-2]])for(const scale of [[1,1,1],[2,3,4]]){
  const proper=new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...angles)).scale(new THREE.Vector3(...scale));
  for(const mirrored of [false,true]){
    const source=proper.clone();if(mirrored)source.multiply(new THREE.Matrix4().makeScale(1,-1,1));
    const input=Array.from({length:3},(_,r)=>Array.from({length:3},(_,c)=>source.elements[c*4+r]));
    const actor=seatFrame(input);actor.updateMatrixWorld(true);
    assert.ok(actor.matrixWorld.determinant()>0);
    for(const position of [[4,0,0],[0,0,5],[1,2,3],[-2,-3,1]]){
      const expected=new THREE.Vector3(...position).applyMatrix4(proper);
      assert.ok(new THREE.Vector3(...position).applyMatrix4(actor.matrixWorld).distanceTo(expected)<1e-10);
    }
  }
}
// Added passengers/mount operators can acquire the reflection through a host.
// Two native reflections already cancel; changing hosts must not accumulate it.
const host=new THREE.Group(),sibling=new THREE.Group();host.add(sibling);
const nativeSeat=seatFrame(reflected);host.add(nativeSeat);host.scale.y=-1;
updateSeatFrames({passenger_seats:host});
assert.equal(nativeSeat.matrixWorld.determinant(),1);
assert.deepEqual(new THREE.Vector3(1,2,3).applyMatrix4(nativeSeat.matrixWorld).toArray(),[1,2,3]);
assert.equal(sibling.matrixWorld.determinant(),-1,'the host and its equipment retain their native geometry');
host.scale.y=1;updateSeatFrames({passenger_seats:host});updateSeatFrames({passenger_seats:host});
assert.deepEqual(new THREE.Vector3(1,2,3).applyMatrix4(nativeSeat.matrixWorld).toArray(),[1,2,3]);
host.scale.y=-1;updateSeatFrames({passenger_seats:host});
assert.equal(nativeSeat.matrixWorld.determinant(),1);
const actorFigure=new THREE.Group();actorFigure.position.set(3,4,5);actorFigure.add(nativeSeat);host.add(actorFigure);
host.rotation.set(.4,-.6,1.1);
host.updateWorldMatrix(true,true);
const expectedActor=host.matrixWorld.clone().multiply(new THREE.Matrix4().makeTranslation(3,4,5)).multiply(new THREE.Matrix4().makeScale(1,-1,1));
updateSeatFrames({passenger_seats:host});
for(const point of [[0,0,0],[4,0,0],[0,0,7],[1,2,3]]){
  assert.ok(new THREE.Vector3(...point).applyMatrix4(nativeSeat.matrixWorld).distanceTo(new THREE.Vector3(...point).applyMatrix4(expectedActor))<1e-10);
}
const mesh={positions:[0,0,0,1,0,0,0,1,0],indices:[0,1,2],uv:[0,0,1,0,0,1],submeshes:[{start:0,count:3,mtl:'skin.mtl'}]};
const groups={hull:new THREE.Group(),turret:new THREE.Group(),commander_mg:new THREE.Group()},flat=new THREE.MeshBasicMaterial();
const weapon=previewMesh(mesh,flat),soldier=previewMesh(mesh,flat);frame.add(soldier);groups.commander_mg.add(frame,weapon);
const textured=new THREE.MeshBasicMaterial();let reads=0;
applyModelTextures(groups,true,mtl=>{reads++;assert.equal(mtl,'skin.mtl');return textured;},false);
assert.equal(reads,2);assert.equal(weapon.material[0],textured);assert.equal(soldier.material[0],textured);
assert.equal(weapon.geometry.getAttribute('uv').count,3);
applyModelTextures(groups,false,()=>{throw Error('unexpected texture request');},true);
assert.equal(weapon.material,flat);assert.equal(soldier.material,flat);assert.equal(flat.wireframe,true);
const paint=new THREE.MeshStandardMaterial({map:new THREE.DataTexture(new Uint8Array([112,112,112,255]),1,1)});
const smoke=previewMesh({...mesh,submeshes:[]},paint),fittings={smoke_launchers:new THREE.Group()};fittings.smoke_launchers.add(smoke);
applyModelTextures(fittings,false,()=>{throw Error('procedural paint has no external material');},false);
assert.equal(smoke.material.map,null);assert.equal(smoke.material.color.getHex(),0xc5a36b);
applyModelTextures(fittings,true,()=>{throw Error('procedural paint already owns its texture');},false);
assert.equal(smoke.material,paint);assert.ok(smoke.material.map);
const matrix=[[1,0,0,4],[0,0,-1,5],[0,1,0,6],[0,0,0,1]],arcs=new THREE.Group();
drawJointArc(arcs,matrix,[0,90],10);
const vertices=arcs.children[0].geometry.getAttribute('position');
assert.deepEqual([vertices.getX(1),vertices.getY(1),vertices.getZ(1)],[14,5,6]);
assert.deepEqual([vertices.getX(65),vertices.getY(65),vertices.getZ(65)],[4,5,16]);
class Element {value='';textContent='';classList={toggle(){}};replaceChildren(...options){this.options=options;}}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const state={edit:'turret',previews:{turret:{joints:[{bone:'gun',limits:[-10,45],matrix,weapons:['gun']}]},hull:{}},transform:{}};
let remembered=0,checked=0;
const editor=weaponJointEditor({state,groups,$,remember(){remembered++;},refreshCheck(){checked++;}});
editor.refresh();assert.equal($('joint-min').value,-10);
$('joint-min').value='-20';$('joint-max').value='30';$('joint-min').onchange();
assert.deepEqual(state.transform.joint_limits,{turret:{gun:[-20,30]}});assert.equal(remembered,1);assert.equal(checked,1);
$('joint-min').value='100';$('joint-min').onchange();assert.deepEqual(state.transform.joint_limits.turret.gun,[-20,30]);
// Serialized recipes restore native-key overrides, and changing edit part hides its arc.
state.transform=JSON.parse(JSON.stringify(state.transform));editor.refresh();assert.equal($('joint-max').value,30);
state.edit='hull';editor.refresh();assert.ok(groups.turret.children.every(c=>c.visible===false));
state.edit='turret';editor.refresh();$('joint-reset').onclick();assert.equal($('joint-min').value,-10);
assert.equal(state.transform.joint_limits.turret.gun,undefined);
console.log('Model editor: anatomical handedness on reflected seats/hosts, UV materials, native-axis arcs, limits, invalid input, recipe restore and reset passed.');
// Live pivot changes alter the selected joint centre, not its axes or a
// separate turret traverse. Display and limits use this exact shared frame.
const nativeJoint={bone:'gun_pitch',matrix:[[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]};
const moved=previewJointFrame(nativeJoint,'gun_pitch',[4.8,0,1.89]);
assert.deepEqual(moved.map(r=>r[3]).slice(0,3),[4.8,0,1.89]);
assert.deepEqual(moved.map(r=>r.slice(0,3)),nativeJoint.matrix.map(r=>r.slice(0,3)));
assert.deepEqual(previewJointFrame(nativeJoint,'turret_yaw',[4.8,0,1.89]),nativeJoint.matrix);
assert.equal(nativeJoint.matrix[0][3],0,'moving preview pivot never edits the native preview payload');

// Reflected native seats must keep the animated body anchor at its authored
// position, even when the pose is offset from its boarding socket.
for(const parentSign of [1,-1]){
  const parent=new THREE.Group();parent.scale.y=parentSign;parent.rotation.z=.7;
  const rotation=[[.8,.6,0],[.6,-.8,0],[0,0,1]],actor=seatFrame(rotation);parent.add(actor);
  const original=new THREE.Matrix4().set(.8,.6,0,0,.6,-.8,0,0,0,0,1,0,0,0,0,1);
  for(const origin of [[4,40,15],[22,-20,8],[0,0,0],[4,40,15]]){
    setSeatPoseOrigin(actor,origin);updateSeatFrames({hull:parent});parent.updateWorldMatrix(true,true);
    const expected=new THREE.Vector3(...origin).applyMatrix4(original).applyMatrix4(parent.matrixWorld);
    const actual=new THREE.Vector3(...origin).applyMatrix4(actor.matrixWorld);
    assert.ok(actual.distanceTo(expected)<1e-10,'pose anchor cannot jump when correcting handedness');
    assert.ok(actor.matrixWorld.determinant()>0,'anatomy stays unreflected');
  }
}
