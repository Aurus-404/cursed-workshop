import assert from 'node:assert/strict';
import * as THREE from 'three';
import {TransformControls} from 'three/addons/controls/TransformControls.js';
import {poseMatrix,applyPose,changePivot,pivotEditor} from '../frontend/pivot-editor.js';
const equal=(a,b)=>a.elements.forEach((v,i)=>assert.ok(Math.abs(v-b.elements[i])<1e-7,`${i}: ${v} != ${b.elements[i]}`));
for(const rotation of [[0,0,0],[20,-30,65]]){
 const pose={position:[4,5,6],rotation},before=poseMatrix(pose);changePivot(pose,[12,3,-2]);equal(before,poseMatrix(pose));
 const point=new THREE.Vector3(...pose.pivot).applyMatrix4(poseMatrix(pose));
 assert.ok(point.distanceTo(new THREE.Vector3(...pose.position).add(new THREE.Vector3(...pose.pivot)))<1e-7);
 changePivot(pose,[0,0,0]);equal(before,poseMatrix(pose));
}
const scene=new THREE.Scene(),parent=new THREE.Group(),object=new THREE.Group();scene.add(parent);parent.add(object);
parent.position.set(10,20,30);parent.rotation.z=.4;parent.scale.y=-1;
const row={position:[2,3,4],rotation:[0,10,20]},state={edit:'turret'};let remembered=0;
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',attributes:{},classes:new Set(),classList:{toggle(name,on){on?nodes.get(id).classes.add(name):nodes.get(id).classes.delete(name);}},setAttribute(name,value){this.attributes[name]=value;}});return nodes.get(id);};
const control=new TransformControls(new THREE.PerspectiveCamera());
const editor=pivotEditor({scene,control,state,$,pose:()=>row,target:()=>object,remember:()=>remembered++,apply:()=>applyPose(object,row),check(){},select:()=>editor.attach()});
applyPose(object,row);scene.updateMatrixWorld(true);editor.attach();const original=object.matrixWorld.clone();
$('pose-pivot').onclick();editor.proxy.position.x+=8;editor.capture();equal(object.matrixWorld,original);
assert.ok($('pose-pivot').classes.has('active'));assert.equal($('pose-pivot').attributes['aria-pressed'],'true');
assert.equal($('pose-move').classes.has('active'),false,'moving the pivot must not label Move part as selected');
assert.ok(row.pivot.some(v=>Math.abs(v)>1));assert.equal(control.getMode(),'translate');
state.editPivot=false;editor.sync();editor.proxy.position.z+=5;editor.capture();
assert.equal($('pose-pivot').classes.has('active'),false);assert.ok($('pose-move').classes.has('active'));
assert.ok(Math.abs(object.getWorldPosition(new THREE.Vector3()).z-original.elements[14]-5)<1e-7);
const moved=object.matrixWorld.clone();$('pivot-reset').onclick();equal(object.matrixWorld,moved);assert.deepEqual(row.pivot,[0,0,0]);
assert.equal(remembered,1);
console.log('Pivot editor: no-jump rebase/reset, rotations, reflected parents, native gizmo, independent placement and world translation passed.');

row.pivot=[3,4,5];applyPose(object,row);editor.sync();const pivotBefore=editor.proxy.position.clone();
editor.proxy.rotateZ(.4);editor.capture();editor.sync();assert.ok(editor.proxy.position.distanceTo(pivotBefore)<1e-7,'rotation keeps the custom centre fixed under a reflected host');

// Automatic fitting placement starts from a world-space box and must return
// through the design frame before applying a canonical host inverse.
const designFrame=new THREE.Group();designFrame.position.set(100,20,0);scene.add(designFrame);designFrame.updateWorldMatrix(true,false);
const canonical=designFrame.worldToLocal(new THREE.Vector3(100,20,5));assert.deepEqual(canonical.toArray(),[0,0,5]);
