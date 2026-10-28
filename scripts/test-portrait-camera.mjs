import assert from 'node:assert/strict';
import * as THREE from 'three';
import {portraitCamera,PORTRAIT_CAMERA,ICON_CAMERA} from '../frontend/portrait-camera.js';

// Test the actual projected image coordinates, not the fitting calculation.
// This catches cropped long weapons, tall modules and stale camera matrices.
const cases=[];
function box(model,size,position){const mesh=new THREE.Mesh(new THREE.BoxGeometry(...size));mesh.position.set(...position);model.add(mesh);return mesh;}
function checkFrame(name,model){
  const camera=portraitCamera(model,112,140),limit=1-2*PORTRAIT_CAMERA.margin,point=new THREE.Vector3();
  let vertices=0,largest=0;
  model.traverse(object=>{if(!object.isMesh)return;const positions=object.geometry.attributes.position;
    for(let i=0;i<positions.count;i++){
      point.fromBufferAttribute(positions,i).applyMatrix4(object.matrixWorld).project(camera);vertices++;
      assert.ok(Number.isFinite(point.x)&&Number.isFinite(point.y)&&Number.isFinite(point.z),`${name}: finite projection`);
      assert.ok(Math.abs(point.x)<=limit+1e-6,`${name}: weapon or hull cropped horizontally (${point.x})`);
      assert.ok(Math.abs(point.y)<=limit+1e-6,`${name}: tall module cropped vertically (${point.y})`);
      assert.ok(point.z>-1&&point.z<1,`${name}: geometry clipped by depth planes`);
      largest=Math.max(largest,Math.abs(point.x),Math.abs(point.y));
    }
  });
  assert.ok(vertices>0);assert.ok(largest>=limit-1e-5,`${name}: model should fill the usable frame`);
  const center=new THREE.Box3().setFromObject(model).getCenter(new THREE.Vector3()),view=camera.position.clone().sub(center);
  assert.ok(view.x>view.y&&view.y>0&&view.z>0,`${name}: favor the nose and hide more of the rear`);
  cases.push({name,vertices,largestProjectedCoordinate:Number(largest.toFixed(6))});
  model.traverse(object=>{object.geometry?.dispose();object.material?.dispose();});
}

const longGun=new THREE.Group();
box(longGun,[200,100,50],[0,0,25]);box(longGun,[90,70,40],[0,0,70]);box(longGun,[460,5,5],[245,18,72]);
checkFrame('long gun beyond hull',longGun);

const translatedRotated=new THREE.Group();
box(translatedRotated,[210,90,60],[0,0,30]);box(translatedRotated,[80,75,40],[5,0,80]);
box(translatedRotated,[410,4,4],[210,-12,92]);
translatedRotated.position.set(237,-59,115);translatedRotated.rotation.set(.3,-.22,.65,'ZYX');
checkFrame('translated and rotated assembly',translatedRotated);

const tallModule=new THREE.Group();
box(tallModule,[340,110,130],[0,0,65]);box(tallModule,[35,55,320],[125,48,300]);
checkFrame('truck with tall off-centre module',tallModule);

const smallVehicle=new THREE.Group();
box(smallVehicle,[62,29,22],[0,0,11]);box(smallVehicle,[27,5,4],[40,-21,23]);
smallVehicle.position.set(-400,620,-5);smallVehicle.scale.set(.03,.03,.03);
checkFrame('small translated light vehicle',smallVehicle);

assert.throws(()=>portraitCamera(new THREE.Group(),112,140),/no tiene mallas/);
// Check rendered direction, not just the sign of a camera constant. Both icon
// sizes frame the full texture; the emblem is overlaid/cropped without shifting.
for(const [width,height] of [[112,140],[144,72],[112,48]]){
  const model=new THREE.Group();box(model,[200,90,60],[0,0,30]);box(model,[380,5,5],[200,0,80]);
  const config=height===140?PORTRAIT_CAMERA:ICON_CAMERA;
  const camera=portraitCamera(model,width,height,config.margin,config.direction);
  const nose=new THREE.Vector3(100,0,30).project(camera),rear=new THREE.Vector3(-100,0,30).project(camera);
  assert.ok(nose.x<rear.x,'vehicle must face left on screen');
  const limit=1-2*config.margin;
  model.traverse(mesh=>{if(!mesh.isMesh)return;const points=mesh.geometry.attributes.position;
    for(let i=0;i<points.count;i++){const p=new THREE.Vector3().fromBufferAttribute(points,i).applyMatrix4(mesh.matrixWorld).project(camera);
      assert.ok(Math.abs(p.x)<=limit+1e-6&&Math.abs(p.y)<=limit+1e-6,'geometry exceeds full texture');}});
  model.traverse(n=>{n.geometry?.dispose();n.material?.dispose();});
}
console.log(JSON.stringify({status:'PASS',tests:cases.length+4,cases,leftFacingFormats:3,emptyModelRejected:true},null,2));
