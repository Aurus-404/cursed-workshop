import assert from 'node:assert/strict';
import * as THREE from 'three';
import {clampAngle,jointTree,jointDeltas} from '../frontend/weapon-arcs.js';

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

console.log('Weapon arcs: limits, joint order and turret/gun turning passed.');
