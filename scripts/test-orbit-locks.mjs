import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {setOrbitLock} from '../frontend/orbit-locks.js';

const camera=new THREE.PerspectiveCamera();camera.position.set(200,100,100);
const orbit=new OrbitControls(camera,null);orbit.enableDamping=true;
orbit._sphericalDelta.theta=.4; // A previous orbit still has pending damping.
const app=fs.readFileSync('frontend/app.js','utf8');
const start=app.indexOf('function animate(now)'),end=app.indexOf('requestAnimationFrame(animate);',start+60)+'requestAnimationFrame(animate);'.length;
const frameSource=app.slice(start,end);
let aiming=null,drawn=0;
const helper={visible:true},control={enabled:true,getHelper:()=>helper};
const frame=new Function('requestAnimationFrame','sight','camera','orbit','setOrbitLock','transformControl','document','fitClipPlanes','updateSeatFrames','groups','renderer','scene','viewPieces','armorEditor','state','poseTarget','seatFigure',
  'let activeUntil=100000,lastFrame=0;'+frameSource+';return animate;')(
    ()=>{}, {view:()=>aiming},camera,orbit,setOrbitLock,control,{hidden:false},()=>{},()=>{},{},{render(){drawn++;}},{},null,{}, {},()=>null,()=>null);
const original=camera.position.clone();
setOrbitLock(orbit,'transform',true);
for(let i=1;i<=30;i++)frame(i*16);
assert.equal(orbit.enabled,false);assert.deepEqual(camera.position.toArray(),original.toArray(),'drawing cannot unlock dragging or apply residual camera damping');
setOrbitLock(orbit,'armor-corners',true);
setOrbitLock(orbit,'transform',false);frame(600);assert.equal(orbit.enabled,false);
aiming=new THREE.PerspectiveCamera();frame(616);setOrbitLock(orbit,'armor-corners',false);frame(632);
assert.equal(orbit.enabled,false,'sight preview retains its independent lock');
assert.equal(control.enabled,false);
aiming=null;frame(648);assert.equal(orbit.enabled,true);assert.equal(control.enabled,true);
assert.ok(camera.position.distanceTo(original)>0,'normal orbit resumes after every lock releases');
orbit.enabled=false;setOrbitLock(orbit,'transform',true);setOrbitLock(orbit,'transform',false);
assert.equal(orbit.enabled,false,'a preexisting disabled camera stays disabled');
assert.ok(drawn>0);
console.log('Orbit locking: real frame loop, 30-frame drag, residual damping, overlapping gizmos/preview and camera restore passed.');
