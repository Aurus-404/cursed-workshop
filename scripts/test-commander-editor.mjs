// Exercise editor state and real three.js transforms without a browser/game.
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {commanderEditor} from '../frontend/commander-mg.js';

class Element {
  value='';checked=false;disabled=false;children=[];textContent='';
  classList={toggle(){}};
  replaceChildren(...children){this.children=children;this.value=children[0]?.value||'';}
}
const elements=new Map();
globalThis.document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
globalThis.Option=class {constructor(label,value){this.text=label;this.value=value;}};
const $=id=>document.getElementById(id);
const hosts=[{name:'turret',matrix:[[0,-1,0,10],[1,0,0,20],[0,0,1,5],[0,0,0,1]]},
  {name:'body',matrix:[[1,0,0,4],[0,1,0,5],[0,0,1,6],[0,0,0,1]]},
  {name:'reflected',matrix:[[1,0,0,0],[0,-1,0,0],[0,0,1,0],[0,0,0,1]]}];
const state={transform:{},wire:false,check:{status:'ok',crew:['commander','driver'],hosts,commander_position:[10,22,9]},edit:'hull'};
const groups={commander_mg:new THREE.Group()},scene=new THREE.Scene();
let remembered=0,requests=0,checks=0,defer=false,pending=[],catalogReads=0;
const editor=commanderEditor({state,groups,scene,
  api:async()=>{catalogReads++;return {weapons:[{id:'base_rifle',label:'Base rifle'}],supports:[{id:'fixture:aa'}],dlc:{status:'last_game_run'}};},
  quickJob:async(path,spec)=>{requests++;assert.equal(path,'/api/commander-mg-preview');assert.equal(spec.operator,'commander');const data={meshes:[{positions:[0,0,0,1,0,0,0,1,0],indices:[0,1,2]}],seats:[]};return defer?new Promise(resolve=>pending.push(()=>resolve(data))):data;},
  remember(){remembered++;},selectEdit(part){state.edit=part;},refreshCheck:async()=>{checks++;},
  reportError(error){throw error;},addSeats(part){const seats=new THREE.Group();seats.name='seats';groups[part].add(seats);}});
state.hull={};state.turret={};state.check.commander_warning='Loader duty remains experimental';editor.controls();
assert.equal($('cmd-add').disabled,false,'a reload duty is a warning, not a second aiming duty');
assert.equal($('cmd-availability').textContent,'Loader duty remains experimental');
$('cmd-enabled').checked=true;await $('cmd-enabled').onchange();
assert.equal(state.transform.commander_mg.operator,'commander');
assert.equal(state.transform.commander_mg.item,'base_rifle');
assert.deepEqual(state.transform.commander_mg.position,[2,0,4]);
assert.equal(state.edit,'commander_mg');
assert.equal(requests,1);
assert.ok(remembered>0&&checks>0);
editor.sync();
const world=groups.commander_mg.getWorldPosition(new THREE.Vector3());
assert.deepEqual(world.toArray(),[10,22,9]);
assert.ok(groups.commander_mg.children.some(n=>n.isMesh));
$('cmd-yaw-0').value='-70';$('cmd-yaw-0').onchange();
assert.equal(state.transform.commander_mg.yaw[0],-70);
// Reopening a recipe restores the mounted weapon and its limits.
state.transform.commander_mg.rotation=[0,0,35];await editor.restore();
assert.equal(state.transform.commander_mg.rotation[2],35);
assert.equal($('cmd-yaw-0').value,-70);
// Changing the host preserves world position AND orientation, then recipe
// restoration preserves those local values (the old implementation jumped).
groups.commander_mg.updateWorldMatrix(true,false);
const before=groups.commander_mg.matrixWorld.clone();
$('cmd-parent').value='body';$('cmd-parent').onchange();
groups.commander_mg.updateWorldMatrix(true,false);
before.elements.forEach((v,i)=>assert.ok(Math.abs(v-groups.commander_mg.matrixWorld.elements[i])<1e-7));
const recipe=structuredClone(state.transform.commander_mg);await editor.restore();
assert.deepEqual(state.transform.commander_mg,recipe);
const previousCatalogReads=catalogReads;state.artRevision=1;await editor.restore();
assert.equal(catalogReads,previousCatalogReads+1,'reindexing must refresh the DLC weapon choices');
assert.match($('cmd-dlc-note').textContent,/DLC/);
$('cmd-parent').value='reflected';assert.throws(()=>$('cmd-parent').onchange(),/refleja/);
assert.deepEqual(state.transform.commander_mg,recipe,'invalid reparent must leave recipe unchanged');
// An earlier async preview must never resurrect removed geometry.
defer=true;$('cmd-item').value='base_rifle';$('cmd-item').onchange();
await new Promise(resolve=>setTimeout(resolve,220));assert.equal(pending.length,1);
$('cmd-enabled').checked=false;await $('cmd-enabled').onchange();
pending.shift()();await new Promise(resolve=>setImmediate(resolve));
assert.equal(state.transform.commander_mg,undefined);
assert.equal(groups.commander_mg.visible,false);
assert.equal($('edit-commander').disabled,true);
assert.equal(state.edit,'turret');
assert.ok(!groups.commander_mg.children.some(n=>n.isMesh),'stale response must not restore a removed mount');
state.check.crew=['driver'];$('cmd-enabled').checked=true;
await assert.rejects(()=>$('cmd-enabled').onchange(),/comandante/);
assert.equal(state.transform.commander_mg,undefined);
// Reject an absent commander before loading a potentially expensive catalog.
let invalidCatalogReads=0;
commanderEditor({state:{transform:{},check:{status:'ok',crew:['driver']}},groups:{commander_mg:new THREE.Group()},scene:new THREE.Scene(),
  api:async()=>{invalidCatalogReads++;throw Error('Catalog must not be loaded');},quickJob:async()=>{},remember(){},selectEdit(){},refreshCheck:async()=>{},
  reportError(error){throw error;},addSeats(){}});
$('cmd-enabled').checked=true;
await assert.rejects(()=>$('cmd-enabled').onchange(),/comandante/);
assert.equal(invalidCatalogReads,0);
let finishCatalog;
const changing={transform:{},check:{status:'ok',crew:['commander'],hosts}};
commanderEditor({state:changing,groups:{commander_mg:new THREE.Group()},scene:new THREE.Scene(),
  api:()=>new Promise(resolve=>{finishCatalog=resolve;}),quickJob:async()=>{throw Error('Stale assembly must not mount a weapon');},
  remember(){},selectEdit(){},refreshCheck:async()=>{},reportError(error){throw error;},addSeats(){}});
$('cmd-enabled').checked=true;
const enabling=$('cmd-enabled').onchange();
const rejected=assert.rejects(enabling,/comandante/);
await new Promise(resolve=>setImmediate(resolve));
changing.check={status:'ok',crew:['driver']};
finishCatalog({supports:[],weapons:[]});await rejected;
assert.equal(changing.transform.commander_mg,undefined);
console.log('Commander editor: exclusive role, local/world placement, limits, recipe restore, reparent, reflected frame rejection, removal and stale-response checks passed.');
