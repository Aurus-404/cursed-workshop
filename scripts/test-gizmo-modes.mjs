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
let cornersStopped=0;
const armorEditor={stopCorners(){cornersStopped++;}};
new Function('state','inspector','selectEdit','transformControl','$','armorEditor',source)(state,inspector,p=>controls.attach(targets[p]),controls,$,armorEditor);
for(part of ['turret','extra_armor','smoke_launchers']){
  $('pose-rotate').onclick();assert.equal(controls.getMode(),'rotate');assert.equal(controls.object,targets[part]);
  const colors=new Set();controls.getHelper().traverse(n=>{if(n.material?.color)colors.add(n.material.color.getHex());});
  assert.ok(colors.has(0xff0000)&&colors.has(0x00ff00)&&colors.has(0x0000ff),'native color rings are present');
  $('pose-move').onclick();assert.equal(controls.getMode(),'translate');
}
state.inspectorPending=1;$('mode-rotate').onclick();assert.equal(controls.getMode(),'translate');
assert.equal(cornersStopped,6,'Whole-piece movement and rotation leave corner editing');
console.log('Gizmo modes: real Three.js controls rotate guns, armor and smoke, retaining async locks.');

const html=fs.readFileSync('frontend/index.html','utf8');
assert.ok(html.indexOf('id="mode-rotate"')<html.indexOf('id="transform-space"'));
assert.ok(html.indexOf('id="transform-space"')<html.indexOf('id="fit-view"'));
let saved;
const selectorSource=app.slice(app.indexOf('function updateTransformSpace()'),app.indexOf('const crewVisibility='));
new Function('$','transformControl','state','saveUISettings','uiStorage','reportError',selectorSource)($,controls,state,async v=>{saved=v;},{setItem(){}},e=>{throw e;});
state.inspectorPending=0;
for(const [id,space] of [['space-local','local'],['space-global','world']]){await $(id).onclick();assert.equal(controls.space,space);assert.deepEqual(saved,{transform_space:space});}
state.busy=true;await $('space-local').onclick();assert.equal(controls.space,'world');
console.log('Compact global/local buttons, toolbar order, shared controls, persistence and busy lock passed.');
