import assert from 'node:assert/strict';
import * as THREE from 'three';
import {clampAngle,jointTree,jointDeltas,jointLocalPose,ownsFitting,weaponArcs} from '../frontend/weapon-arcs.js';

const near=(a,b,message)=>assert.ok(a.distanceTo(b)<1e-6,`${message}: ${a.toArray()} != ${b.toArray()}`);

// Limits: inside stays, outside goes to the nearest bound (also across ±180), a full circle never clamps.
assert.equal(clampAngle(10,[-5,25]),10);
assert.equal(clampAngle(40,[-5,25]),25);
assert.equal(clampAngle(-30,[-5,25]),-5);
assert.equal(clampAngle(175,[-170,-150]),-170);
assert.equal(clampAngle(-100,[-180,180]),-100);

// A turret about the vertical axis at (10,0,5); a gun pivot on it at (20,0,8), turning about the side axis
// with the barrel along +X; a mesh bone below the gun that is not a joint.
const turret=new THREE.Matrix4().makeTranslation(10,0,5);
const gun=new THREE.Matrix4().makeTranslation(20,0,8).multiply(new THREE.Matrix4().makeRotationX(-Math.PI/2));
const parents={gun_rot:'turret_body',turret_body:'turret',turret:'body',barrel:'gun_rot'};
const tree=jointTree([{key:'gun_rot',matrix:gun},{key:'turret',matrix:turret}],bone=>parents[bone]);
assert.deepEqual(tree.map(j=>[j.key,j.above]),[['turret',null],['gun_rot','turret']]);

// Rest: nothing moves.
const rest=jointDeltas(tree,{});
near(new THREE.Vector3(40,0,8).applyMatrix4(rest.gun_rot),new THREE.Vector3(40,0,8),'rest');

// Elevation alone raises the muzzle about the gun pivot, keeping its length.
const raised=jointDeltas(tree,{gun_rot:30});
const muzzle=new THREE.Vector3(40,0,8).applyMatrix4(raised.gun_rot);
assert.ok(Math.abs(muzzle.distanceTo(new THREE.Vector3(20,0,8))-20)<1e-6);
assert.ok(Math.abs(Math.abs(muzzle.z-8)-10)<1e-6,'30 degrees of elevation move the muzzle 10 units vertically');

// Traverse carries the gun and its pivot around the turret axis; the gun keeps its own elevation.
const both=jointDeltas(tree,{turret:90,gun_rot:30});
near(new THREE.Vector3(20,0,8).applyMatrix4(both.turret),new THREE.Vector3(10,10,8),'gun pivot after 90 degrees of traverse');
const turned=new THREE.Vector3(40,0,8).applyMatrix4(both.gun_rot);
assert.ok(Math.abs(turned.distanceTo(new THREE.Vector3(10,10,8))-20)<1e-6);
assert.ok(Math.abs(turned.z-muzzle.z)<1e-6,'traverse does not change the elevation height');
assert.ok(Math.abs(turned.x-10)<1e-6,'after 90 degrees the barrel points along +Y');

// A module hanging from the turret (a turret on a turret) goes around with it, from its holder at rest.
const holder=jointLocalPose(both.turret,new THREE.Matrix4(),new THREE.Matrix4());
near(new THREE.Vector3(20,0,30).applyMatrix4(holder),new THREE.Vector3(10,10,30),'module on the turret after 90 degrees');
near(new THREE.Vector3(10,0,30).applyMatrix4(holder),new THREE.Vector3(10,0,30),'a module on the turret axis stays there');

// Plates on the turret turn with the chassis' joint while an installed module on it is open:
// the module's preview lists the whole model, but only its own branch is the module's.
const model=[{name:'body',parent:null},{name:'turret',parent:'body'},{name:'gun_rot',parent:'turret'},{name:'small',parent:'turret'},{name:'small_gun',parent:'small'}];
const native={native_module:true,anchor_name:'small',bones:model};
assert.equal(ownsFitting('hull','turret',model,native),true,'the plate on the big turret goes with the big turret');
assert.equal(ownsFitting('turret','turret',model,native),false);
assert.equal(ownsFitting('turret','small_gun',model,native),true,'a plate on the module stays the module\'s');
assert.equal(ownsFitting('hull','small_gun',model,native),false);
// A donor is another model: the same bone name is still its own.
const donor={anchor_name:'turret',bones:[{name:'turret',parent:null}]};
assert.equal(ownsFitting('hull','turret',model,donor),false);assert.equal(ownsFitting('turret','turret',[{name:'turret',parent:null}],donor),true);
assert.equal(ownsFitting('hull','body',model,donor),true);assert.equal(ownsFitting('hull','body',model,null),true);

console.log('Weapon arcs: limits, joint order, turret/gun turning and a module on a turret passed.');
for(const scale of [1,-1]){
  const parent=new THREE.Matrix4().makeTranslation(5,7,9).multiply(new THREE.Matrix4().makeRotationX(.7)).scale(new THREE.Vector3(1,scale,1));
  const local=new THREE.Matrix4().makeTranslation(2,3,4),delta=both.gun_rot;
  const actual=parent.clone().multiply(jointLocalPose(delta,parent,local));
  const expected=delta.clone().multiply(parent).multiply(local);
  actual.elements.forEach((v,i)=>assert.ok(Math.abs(v-expected.elements[i])<1e-7,'nested and reflected fitting frames retain the same aiming delta'));
}

// TheOppsAreOntoMe: loaded fittings own explicit matrices, while their position/
// quaternion fields can still be defaults. Turning the view on must preserve that
// pose before visiting the armor editor, also after a reload or an extra piece.
{
  const globals={document:globalThis.document,getComputedStyle:globalThis.getComputedStyle,setInterval:globalThis.setInterval};
  globalThis.document={createElement:()=>({className:'',hidden:true,style:{},setAttribute(){}})};
  globalThis.getComputedStyle=()=>({position:'relative'});
  globalThis.setInterval=()=>0;
  try {
    for(const reflected of [false,true]){
      const scene=new THREE.Scene(),body=new THREE.Group(),extras=new THREE.Group();scene.add(body,extras);
      body.position.set(7,3,4);body.rotation.z=.2;if(reflected)body.scale.y=-1;
      extras.position.set(-4,8,3);extras.rotation.x=.3;
      const geometry=new THREE.BoxGeometry(4,2,2),material=new THREE.MeshBasicMaterial();
      const barrel=new THREE.Mesh(geometry,material);barrel.position.set(40,0,8);barrel.userData.mesh={bone:'barrel'};body.add(barrel);
      let fittings=[];
      const loaded=(key,frame)=>{
        const host=new THREE.Group();host.matrixAutoUpdate=false;host.matrix.copy(frame);host.userData.hostBone=key;
        host.add(new THREE.Mesh(geometry,material));extras.add(host);return host;
      };
      const gunPlate=loaded('barrel',new THREE.Matrix4().makeTranslation(26,4,18));
      const turretTrack=loaded('turret_body',new THREE.Matrix4().makeTranslation(10,-12,20));
      fittings=[gunPlate,turretTrack];
      const state={show:{arcs:false},transform:{},busy:false},handlers={};
      const viewport={style:{},append(){},addEventListener(name,fn){handlers[name]=fn;},getBoundingClientRect(){return {left:0,top:0,width:800,height:600};}};
      const camera=new THREE.PerspectiveCamera(50,800/600,.1,1000);camera.position.set(90,-140,100);camera.up.set(0,0,1);camera.lookAt(20,0,10);camera.updateMatrixWorld();
      const orbit={enabled:true};
      const rig={key:'hull',group:body,revision:'one',parentOf:n=>parents[n],extras:()=>fittings,
        joints:tree.map(j=>({...j,limits:()=>j.key==='turret'?[-180,180]:[-45,60]})),
        jointOf:node=>node===barrel||node.userData.hostBone==='barrel'?'gun_rot':node.userData.hostBone==='turret_body'?'turret':null};
      const view=weaponArcs({state,groups:{hull:body},camera,viewport,orbit,rigs:()=>[rig]});
      scene.updateMatrixWorld(true);
      const initial=new Map(fittings.map(o=>[o,o.matrixWorld.clone()])),recipe=JSON.stringify(state.transform);
      const barrelRest=barrel.matrixWorld.clone();
      const same=(actual,expected,message)=>actual.elements.forEach((v,i)=>assert.ok(Math.abs(v-expected.elements[i])<1e-7,message));
      state.show.arcs=true;view.refresh();scene.updateMatrixWorld(true);
      for(const object of fittings)same(object.matrixWorld,initial.get(object),'enabling angles preserves loaded fitting matrices without opening the armor tool');
      same(barrel.matrixWorld,barrelRest,'ordinary TRS-driven model meshes retain their rest pose too');
      const handle=body.children.find(o=>o.name==='weapon_arcs').children.flatMap(o=>o.children).find(o=>o.userData.weaponHandle&&o.userData.joint.key==='gun_rot');
      const pointerAt=world=>{const p=world.clone().project(camera);return {button:0,clientX:(p.x+1)*400,clientY:(1-p.y)*300,pointerId:1,preventDefault(){},stopImmediatePropagation(){}};};
      handlers.pointerdown(pointerAt(handle.getWorldPosition(new THREE.Vector3())));
      assert.equal(orbit.enabled,false,'the real elevation handle starts the drag');
      const angle=30,entry=handle.userData.joint;
      const target=new THREE.Vector3(Math.cos(Math.PI/6)*entry.radius,Math.sin(Math.PI/6)*entry.radius,0).applyMatrix4(gun).applyMatrix4(body.matrixWorld);
      handlers.pointermove(pointerAt(target));handlers.pointerup(pointerAt(target));scene.updateMatrixWorld(true);
      const worldDelta=body.matrixWorld.clone().multiply(jointDeltas(tree,{gun_rot:angle}).gun_rot).multiply(body.matrixWorld.clone().invert());
      same(gunPlate.matrixWorld,worldDelta.multiply(initial.get(gunPlate)),'the plate follows elevation from its authored position');
      same(turretTrack.matrixWorld,initial.get(turretTrack),'the turret track does not move with elevation');
      state.show.arcs=false;view.refresh();scene.updateMatrixWorld(true);
      for(const object of fittings){same(object.matrixWorld,initial.get(object),'turning angles off restores the authored fitting');assert.equal(object.matrixAutoUpdate,false);}
      same(barrel.matrixWorld,barrelRest,'turning angles off restores ordinary model meshes');
      assert.equal(barrel.matrixAutoUpdate,true,'ordinary meshes recover their automatic matrix updates');
      // A newly loaded piece has fresh default TRS values again. Reproduce the
      // report's reload/add sequence, including a rebuild while angles are on.
      for(let step=0;step<3;step++){
        state.show.arcs=true;view.refresh();
        const piece=loaded('barrel',new THREE.Matrix4().makeTranslation(32+step,5,19));fittings.push(piece);
        scene.updateMatrixWorld(true);const before=piece.matrixWorld.clone();
        view.refresh();view.rebuild();scene.updateMatrixWorld(true);
        same(piece.matrixWorld,before,'adding/reloading a fitting while the view is on preserves its matrix');
        state.show.arcs=false;view.refresh();scene.updateMatrixWorld(true);
        same(piece.matrixWorld,before,'the reloaded fitting also restores without the armor panel');
      }
      assert.equal(JSON.stringify(state.transform),recipe,'the view never writes the saved design');
    }
  } finally {for(const [key,value] of Object.entries(globals)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}
}
console.log('Weapon angles lifecycle: loaded armor, turret tracks, drag, reload and view reset preserve authored matrices.');
