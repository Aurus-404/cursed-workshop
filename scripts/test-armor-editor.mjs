import assert from 'node:assert/strict';
import * as THREE from 'three';
import {armorEditor} from '../frontend/armor-editor.js';
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
let editor,remembered=0,checks=0,defer=false,release;
const catalog={items:[{id:'tracks',label:'Native spare track',kind:'tracks',materials:['steel'],thickness:[20],cast_steel:true},
  {id:'bags',label:'Native sandbag',kind:'sandbags',materials:['sand'],thickness:[60],cast_steel:false}]};
const job=async(path,body)=>{
  if(path==='/api/armor-catalog')return catalog;
  if(path==='/api/armor-preview'){
    if(defer)await new Promise(resolve=>{release=resolve;});
    return {meshes:[{positions:[0,0,0,1,0,0,0,1,0],indices:[0,1,2]}]};
  }throw Error(path);
};
editor=armorEditor({state,$,groups,scene,job,loading:async(label,run)=>run(),remember(){remembered++;},selectEdit(part){state.edit=part;},
  refreshCheck:async()=>{checks++;editor.receiveCheck({armor_hosts:hosts});},reportError(error){throw error;},applyShow(){}});
await $('armor-load').onclick();
assert.equal(state.transform.extra_armor,undefined,'Browsing adds no physical armor');
assert.equal(groups.extra_armor.children.length,1,'An unplaced preview is visible');
await $('armor-add').onclick();assert.equal(state.transform.extra_armor.length,1);assert.equal(state.edit,'extra_armor');
assert.ok(state.transform.extra_armor[0].position[2]>=8,'New fitting starts above the roof, not inside it');
assert.equal(editor.target(),undefined,'An added fitting stays put until its name is picked again');
$('armor-list').value='0';$('armor-list').selectedIndex=0;$('armor-list').onchange();assert.ok(editor.target(),'Picking its name gives the handles back');
await $('armor-duplicate').onclick();assert.equal(state.transform.extra_armor.length,2);
const first=structuredClone(state.transform.extra_armor[0]);editor.target().position.x=44;
assert.equal(editor.capture(),true);assert.equal(state.transform.extra_armor[1].position[0],44);
assert.deepEqual(state.transform.extra_armor[0],first);
const parentBefore=editor.target().getWorldPosition(new THREE.Vector3());
$('armor-parent').value='turret';await $('armor-parent').onchange();
assert.ok(editor.target().getWorldPosition(new THREE.Vector3()).distanceTo(parentBefore)<1e-7,'Reparenting preserves world pose');
assert.equal(state.transform.extra_armor[1].parent,'turret');
editor.sync();const fitting=editor.target(),before=fitting.getWorldPosition(new THREE.Vector3());
hosts[1].matrix[0][3]+=10;editor.receiveCheck({armor_hosts:hosts});
assert.ok(Math.abs(fitting.getWorldPosition(new THREE.Vector3()).x-before.x-10)<1e-7,'Fitting follows its real parent bone');
const recipe=structuredClone(state.transform);await editor.restore();assert.deepEqual(state.transform,recipe);
assert.equal(selectionData(state).extra_armor.length,2);
await $('armor-remove').onclick();assert.equal(state.transform.extra_armor.length,1);
$('armor-kind').value='sandbags';await $('armor-kind').oninput();assert.equal($('armor-asset').value,'bags');
assert.equal(state.transform.extra_armor.length,1,'Searching keeps placed armor untouched');
assert.ok(remembered>=4&&checks>=3);
for(const locale of ['en','zh-CN'])assert.notEqual(translate('Sacos de arena',locale),'Sacos de arena');
const placed=groups.extra_armor.children.filter(n=>!n.userData.candidate).length,kept=structuredClone(state.transform);
const closing=$('armor-asset').onchange();editor.deactivate();await closing;
assert.equal(groups.extra_armor.children.length,placed,'Leaving drops ghosts, including cached previews in flight');
assert.deepEqual(state.transform,kept,'Leaving preserves placed armor and recipe');
catalog.items.push({id:'wood',label:'Native wood',kind:'wood',materials:['wood'],thickness:[10]});
defer=true;$('armor-asset').value='wood';const pending=$('armor-asset').onchange();
await new Promise(resolve=>setTimeout(resolve,0));
state.transform.extra_armor=[];await editor.restore();release();await pending;
assert.equal(groups.extra_armor.children.length,0,'A stale unplaced preview cannot reappear after clearing the assembly');
state.check={commander_bones:{pitch:'turret'}};
state.transform.extra_armor=[{...first,parent:'old_pitch',parent_ref:{kind:'commander_mg',name:'old_pitch',bone:'pitch'}}];
await editor.restore();editor.receiveCheck({armor_hosts:hosts});
$('armor-list').value='0';$('armor-list').selectedIndex=0;$('armor-list').onchange();
assert.equal(editor.target().parent.visible,true,'recreated socket remains editable in the preview');
assert.equal($('armor-parent').value,'turret','parent picker follows the recreated socket');
assert.deepEqual(editor.target().parent.matrix.elements,new THREE.Matrix4().set(...hosts[1].matrix.flat()).elements);
state.transform.extra_armor[0].parent='body';editor.sync();
assert.deepEqual(editor.target().parent.matrix.elements,new THREE.Matrix4().set(...hosts[0].matrix.flat()).elements,'a manually chosen parent supersedes the old socket reference');
console.log('Armor editor: unplaced preview, native catalog filters, independent poses, parent following, recreated socket editing, recipes and diagnostics passed.');
