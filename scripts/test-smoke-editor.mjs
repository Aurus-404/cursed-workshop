import assert from 'node:assert/strict';
import * as THREE from 'three';
import {smokeEditor} from '../frontend/smoke-editor.js';
import {selectionData} from '../frontend/diagnostics.js';
import {translate} from '../frontend/i18n.js';
globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',options:[],disabled:false,textContent:'',classList:{toggle(){}},
  replaceChildren(){this.options=[];this.value='';},add(o){this.options.push(o);if(this.options.length===1)this.value=o.value;}});return elements.get(id);};
const scene=new THREE.Scene(),groups={hull:new THREE.Group(),turret:new THREE.Group()};scene.add(groups.hull,groups.turret);
groups.hull.add(new THREE.Mesh(new THREE.BoxGeometry(30,20,10),new THREE.MeshBasicMaterial()));
const state={hull:{template:'receiver'},turret:{template:'receiver'},transform:{},edit:'hull',wire:false};
const hosts=[{name:'body',matrix:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]},
  {name:'turret',matrix:[[0,-1,0,10],[1,0,0,20],[0,0,1,5],[0,0,0,1]]}];

$('smoke-tubes').value='1';
let editor,remembered=0,defer=false,release;
const calls=[];
const job=async(path,body)=>{
 assert.equal(path,'/api/smoke-preview');calls.push(body.tubes);
 if(defer)await new Promise(resolve=>release=resolve);
 return {meshes:[{positions:[0,0,0,1,0,0,0,1,0],indices:[0,1,2],uv:[0,0,1,0,0,1]}],texture:{width:1,height:1,rgba:[110,112,110,255]},muzzles:Array.from({length:body.tubes},()=>[1,0,1]),directions:Array.from({length:body.tubes},()=>[.707,0,.707])};
};
editor=smokeEditor({state,$,groups,scene,job,loading:async(label,fn)=>fn(),remember(){remembered++;},selectEdit(part){state.edit=part;},
 refreshCheck:async()=>editor.receiveCheck({armor_hosts:hosts}),reportError(error){throw error;},applyShow(){}});
defer=true;const pending=$('smoke-load').onclick();await new Promise(resolve=>setTimeout(resolve,0));
await editor.restore();release();await pending;
assert.equal(groups.smoke_launchers.children.length,0,'A stale unplaced preview cannot reappear after clearing');
defer=false;
await $('smoke-load').onclick();assert.equal(state.transform.smoke_launchers,undefined);
assert.equal(groups.smoke_launchers.children.length,1,'Unplaced preview is separate from recipe');
await $('smoke-add').onclick();assert.equal(state.transform.smoke_launchers[0].tubes,1);
$('smoke-tubes').value='2';await $('smoke-tubes').onchange();await $('smoke-add').onclick();
$('smoke-tubes').value='3';await $('smoke-tubes').onchange();await $('smoke-add').onclick();
assert.deepEqual(state.transform.smoke_launchers.map(r=>r.tubes),[1,2,3]);
assert.equal(state.edit,'smoke_launchers');
const first=structuredClone(state.transform.smoke_launchers[0]);editor.target().position.x=21;assert.ok(editor.capture());
assert.equal(state.transform.smoke_launchers[2].position[0],21);assert.deepEqual(state.transform.smoke_launchers[0],first);
const before=editor.target().getWorldPosition(new THREE.Vector3());$('smoke-parent').value='turret';await $('smoke-parent').onchange();
assert.ok(editor.target().getWorldPosition(new THREE.Vector3()).distanceTo(before)<1e-7);
hosts[1].matrix[0][3]+=10;editor.receiveCheck({armor_hosts:hosts});
assert.ok(Math.abs(editor.target().getWorldPosition(new THREE.Vector3()).x-before.x-10)<1e-7);
await $('smoke-duplicate').onclick();assert.equal(state.transform.smoke_launchers.length,4);
const recipe=structuredClone(state.transform);await editor.restore();assert.deepEqual(state.transform,recipe);
assert.equal(selectionData(state).smoke_launchers.length,4);
await $('smoke-remove').onclick();assert.equal(state.transform.smoke_launchers.length,3);
assert.ok(remembered>=5);
// Closing a tool drops only its unplaced ghost, even with a preview in flight.
const kept=structuredClone(state.transform),placed=groups.smoke_launchers.children.length;
const ghost=$('smoke-tubes').onchange();state.edit='hull';editor.deactivate();await ghost;
assert.deepEqual(state.transform,kept);assert.equal(groups.smoke_launchers.children.length,placed);
groups.smoke_launchers.traverse(node=>{if(node.userData.placementGuide)assert.equal(node.visible,false);});
state.edit='smoke_launchers';editor.sync();
groups.smoke_launchers.traverse(node=>{if(node.userData.placementGuide)assert.equal(node.visible,true);});
for(const locale of ['en','zh-CN'])for(const label of ['Lanzador de humo simple','Lanzador de humo doble','Lanzador de humo triple'])assert.notEqual(translate(label,locale),label);
state.transform.smoke_launchers=[];await editor.restore();
console.log('Smoke editor: all three models, unplaced preview, independent poses, reparenting, parent following, recipes, diagnostics and localization passed.');
