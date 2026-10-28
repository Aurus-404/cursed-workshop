import assert from 'node:assert/strict';
import * as THREE from 'three';
import {canPlaceCrew,saveCrewPlacement,placeCrewFigure} from '../frontend/crew-placement.js';

const module=new THREE.Group(),seats=new THREE.Group();module.add(seats);
module.position.set(5,7,20);module.rotation.set(.2,-.1,.7,'ZYX');
const box=new THREE.Box3(new THREE.Vector3(-20,-10,0),new THREE.Vector3(20,10,30));
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
console.log('Crew placement: gunners/loaders, fixed on/off, exclusions, module transforms, recipe restore, legacy coordinates, reflected seats and reset passed.');
