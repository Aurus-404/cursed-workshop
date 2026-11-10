import assert from 'node:assert/strict';
import * as THREE from 'three';
import {canPlaceCrew,saveCrewPlacement,placeCrewFigure} from '../frontend/crew-placement.js';
import {turnSeatAbout} from '../frontend/crew-pose.js';

const module=new THREE.Group(),seats=new THREE.Group();module.add(seats);
module.position.set(5,7,20);module.rotation.set(.2,-.1,.7,'ZYX');
const box=new THREE.Box3(new THREE.Vector3(-20,-10,0),new THREE.Vector3(20,10,30));
{ // A seat the source keeps in its hull: placed in the hull with or without "fix crew", clamped inside it.
  const hull=new THREE.Group();hull.updateMatrixWorld(true);module.updateMatrixWorld(true);
  const seat=new THREE.Group();seat.userData={weapon:true,aims:false,staticSeat:true,origin:[54,8,-36],hullSeat:{position:[4,2,17]}};seats.add(seat);
  for(const fixed of [true,false]){placeCrewFigure(seat,module,null,fixed,box,hull);seats.updateMatrixWorld(true);
    assert.ok(seat.getWorldPosition(new THREE.Vector3()).distanceTo(new THREE.Vector3(4,2,17))<1e-9,'hull seat keeps its hull position');}
  seat.userData.hullSeat={position:[90,2,17]};placeCrewFigure(seat,module,null,false,box,hull);seats.updateMatrixWorld(true);
  assert.ok(seat.getWorldPosition(new THREE.Vector3()).x<20,'a seat beyond a shorter hull is pulled inside it');
  seat.userData.hullSeat={position:[4,2,1]};placeCrewFigure(seat,module,null,false,box,hull,8);seats.updateMatrixWorld(true);
  assert.ok(Math.abs(seat.getWorldPosition(new THREE.Vector3()).z-8)<1e-9,'never under the floor of the hull');
  placeCrewFigure(seat,module,{position:[1,2,9],space:'module'},true,box,hull,8);assert.deepEqual(seat.position.toArray(),[1,2,9],'a hand-placed seat wins');
  seats.remove(seat);
}
for(const aims of [true,false])for(const fixed of [true,false]) {
  const figure=new THREE.Group();figure.name=aims?'gunner':'charger';
  figure.userData={weapon:true,aims,staticSeat:!aims,origin:[3,40,2]};seats.add(figure);
  assert.ok(canPlaceCrew(figure),'both aiming and non-aiming workers can be placed');
  assert.equal(canPlaceCrew(figure,[figure.name]),false,'excluded workers cannot be moved');
  figure.position.set(1,2,9);const saved=saveCrewPlacement(figure,module);
  assert.deepEqual(saved,{position:[1,2,9],space:'module'});
  const restored=JSON.parse(JSON.stringify(saved));
  placeCrewFigure(figure,module,restored,fixed,box);
  assert.deepEqual(figure.position.toArray(),[1,2,9],'manual positions survive either fixed state');
  module.rotation.z+=.3;module.updateMatrixWorld(true);
  placeCrewFigure(figure,module,restored,!fixed,box);
  assert.deepEqual(figure.position.toArray(),[1,2,9],'moving the entire module carries its stored seat offsets');
  const frame=new THREE.Group();frame.scale.y=-1;figure.add(frame);figure.updateMatrixWorld(true);
  const determinant=frame.matrixWorld.determinant();
  placeCrewFigure(figure,module,{position:[4,-6,30]},fixed,box);module.updateMatrixWorld(true);
  assert.ok(figure.getWorldPosition(new THREE.Vector3()).distanceTo(new THREE.Vector3(4,-6,30))<1e-9,'legacy world recipes');
  assert.equal(frame.matrixWorld.determinant(),determinant,'reflection retained');
  placeCrewFigure(figure,module,null,false,box);assert.deepEqual(figure.position.toArray(),[3,40,2],'reset restores native position');
  figure.rotation.set(.2,-.3,.7,'ZYX');figure.position.set(4,5,6);
  const turned=saveCrewPlacement(figure,module);
  assert.ok(turned.rotation.every(Number.isFinite));
  placeCrewFigure(figure,module,JSON.parse(JSON.stringify(turned)),fixed,box);
  assert.ok(Math.abs(figure.rotation.z-.7)<1e-5,'rotation survives JSON and fixed state');
  assert.ok(frame.matrixWorld.determinant()<0,'turning does not discard the native reflected basis');
  placeCrewFigure(figure,module,null,false,box);assert.deepEqual(figure.rotation.toArray().slice(0,3),[0,0,0],'reset restores native orientation');
  seats.remove(figure);
}
// A turn about the pivot leaves the pivot where it was, and the numbers are what the compiler reads.
{
  const row={position:[10,0,0],rotation:[0,0,0],pivot:[2,0,0],space:'module'};
  const turned=turnSeatAbout(row,[0,0,90]);
  assert.deepEqual(turned.position,[12,-2,0]);assert.deepEqual(turned.rotation,[0,0,90]);assert.deepEqual(turned.pivot,[2,0,0]);
  const q=new THREE.Quaternion().setFromEuler(new THREE.Euler(0,0,Math.PI/2,'ZYX'));
  const held=new THREE.Vector3(...turned.position).add(new THREE.Vector3(2,0,0).applyQuaternion(q));
  assert.ok(held.distanceTo(new THREE.Vector3(12,0,0))<1e-9,'the pivot did not move');
  assert.deepEqual(turnSeatAbout({position:[1,2,3],rotation:[0,0,0]},[0,0,45]).position,[1,2,3],'with no pivot it turns in place');
  assert.deepEqual(turnSeatAbout(turned,[0,0,0]).position,[10,0,0],'back to where it started');
}
console.log('Crew placement: gunners/loaders, fixed on/off, exclusions, module transforms, recipe restore, legacy coordinates, reflected seats and reset passed.');
