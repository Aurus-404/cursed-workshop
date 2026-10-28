import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from 'three';
import {TransformControls} from 'three/addons/controls/TransformControls.js';
const app=fs.readFileSync('frontend/app.js','utf8');
const source=app.slice(app.indexOf('function setTransformMode('),app.indexOf("$('wireframe').onclick"));
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{classList:{toggle(){}},setAttribute(){}});return elements.get(id);};
const camera=new THREE.PerspectiveCamera();camera.position.set(10,10,10);
const controls=new TransformControls(camera),targets={turret:new THREE.Group(),extra_armor:new THREE.Group(),smoke_launchers:new THREE.Group()};
let part='turret';const state={busy:false,inspectorPending:0};
const inspector={navigation:{pose:()=>true,part:()=>part}};
new Function('state','inspector','selectEdit','transformControl','$',source)(state,inspector,p=>controls.attach(targets[p]),controls,$);
for(part of ['turret','extra_armor','smoke_launchers']){
  $('pose-rotate').onclick();assert.equal(controls.getMode(),'rotate');assert.equal(controls.object,targets[part]);
  const colors=new Set();controls.getHelper().traverse(n=>{if(n.material?.color)colors.add(n.material.color.getHex());});
  assert.ok(colors.has(0xff0000)&&colors.has(0x00ff00)&&colors.has(0x0000ff),'native color rings are present');
  $('pose-move').onclick();assert.equal(controls.getMode(),'translate');
}
state.inspectorPending=1;$('mode-rotate').onclick();assert.equal(controls.getMode(),'translate');
console.log('Gizmo modes: real Three.js controls rotate guns, armor and smoke, retaining async locks.');
