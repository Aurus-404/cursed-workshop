import assert from 'node:assert/strict';
import * as THREE from 'three';
import {armorEditor} from '../frontend/armor-editor.js';
import {selectionData} from '../frontend/diagnostics.js';
import {translate} from '../frontend/i18n.js';

globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',options:[],disabled:false,textContent:'',classList:{toggle(){}},
  replaceChildren(){this.options=[];this.value='';},add(o){this.options.push(o);if(this.options.length===1)this.value=o.value;}});return elements.get(id);};
// Protection types are a checklist: fake boxes with only the requested ones checked.
const boxes=['skirts','shields','plates','mantlet','tracks','sandbags','wood','other'].map(value=>({value,checked:true,disabled:false}));
const only=(...kinds)=>{for(const box of boxes)box.checked=kinds.includes(box.value);};
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
$('armor-kinds').querySelectorAll=()=>boxes;
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
only('sandbags');await $('armor-kinds').onchange();assert.equal($('armor-asset').value,'bags');
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
// Listing: one entry per identical group, test vehicles hidden, filters and orders.
const items=[
  {id:'skirt_g',materials:['steel'],label:'Pz.Kpfw IV G · shieldl',category:'skirts',nation:'germany',thickness:[5],score:900,variants:7,listed:true,primary:true},
  {id:'skirt_h',materials:['steel'],label:'Pz.Kpfw IV H · shieldl',category:'skirts',nation:'germany',thickness:[5],score:900,variants:7,listed:true,primary:false},
  {id:'cruiser',materials:['steel'],label:'Cruiser Mk.III · shieldr3',category:'skirts',nation:'eng',thickness:[14],score:1800,variants:1,listed:true,primary:true},
  {id:'mantlet',materials:['steel'],label:'Pz.Kpfw IV D · mantlet',category:'mantlet',nation:'germany',thickness:[35],score:5000,variants:2,listed:true,primary:true},
  {id:'crate',materials:['steel'],label:'Matilda · basket',category:'other',nation:'eng',thickness:[22],score:20,variants:1,listed:true,primary:true},
  {id:'test',materials:['steel'],label:'Test hull · plate',category:'plates',nation:'other',thickness:[90],score:99999,variants:1,listed:false,primary:true},
  {id:'legacy',materials:['steel'],label:'Old spare track',kind:'tracks',thickness:[20]}];
const {armorListing}=await import('../frontend/armor-editor.js');
const ids=rows=>rows.map(r=>r.id);
assert.deepEqual(ids(armorListing(items)),['mantlet','cruiser','skirt_g','crate','legacy'],'Most useful first; duplicates and test pieces hidden');
assert.deepEqual(ids(armorListing(items,{kind:'skirts'})),['cruiser','skirt_g']);
assert.deepEqual(ids(armorListing(items,{nation:'germany',order:'az'})),['mantlet','skirt_g']);
assert.deepEqual(ids(armorListing(items,{order:'thick'})).slice(0,2),['mantlet','crate']);
assert.deepEqual(ids(armorListing(items,{kind:'tracks'})),['legacy'],'Catalogs saved before categories still list by kind');
assert.deepEqual(ids(armorListing(items,{kinds:['skirts','mantlet']})),['mantlet','cruiser','skirt_g'],'checked types combine');
assert.deepEqual(ids(armorListing(items,{kinds:[]})),[],'nothing checked, nothing listed');
assert.ok(!armorListing(items,{kinds:['skirts']},value=>value)[0].label.startsWith('Faldones'),'one type checked: no type prefix');
const label=armorListing(items,{kind:'skirts'},value=>value)[1].label;
assert.ok(label.includes('5 mm')&&label.includes('×7'),label);
// A mantlet is proposed on the vehicle's own gun elevation.
defer=false;catalog.items=items;catalog.items.push({id:'mantlet2',label:'T-34 · mantlet',category:'mantlet',nation:'ussr',thickness:[15],score:10,listed:true,primary:true,materials:['steel']});
hosts.push({name:'gun_rot',matrix:[[1,0,0,0],[0,1,0,0],[0,0,1,12],[0,0,0,1]]});
state.check={gun_elevation:'gun_rot'};editor.receiveCheck({armor_hosts:hosts});
$('armor-parent').value='body';only('mantlet');await $('armor-kinds').onchange();
assert.equal($('armor-parent').value,'gun_rot','a mantlet goes on the gun elevation');
// A new piece starts upright: the host bone's turn and the piece bone's axes cancel out.
const {naturalRotation}=await import('../frontend/armor-editor.js');
const turned=[[0,0,1,0],[1,0,0,0],[0,1,0,0],[0,0,0,1]],plain=[[1,0,0,5],[0,1,0,0],[0,0,1,2],[0,0,0,1]];
const check=(host,native)=>{const r=naturalRotation({matrix:host},native);
  const pose=new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...r.map(THREE.MathUtils.degToRad),'ZYX'));
  const shown=new THREE.Matrix4().set(...host.flat()).setPosition(0,0,0).multiply(pose).multiply(new THREE.Matrix4().set(...native.flat()).setPosition(0,0,0).invert());
  shown.elements.forEach((v,i)=>assert.ok(Math.abs(v-new THREE.Matrix4().elements[i])<1e-6,'piece shown in vehicle axes'));};
check(plain,turned);check(turned,plain);check(turned,turned);
const mirror=m=>m.map((row,i)=>i===1?row.map(v=>-v):row);
check(mirror(turned),mirror(plain));
assert.equal(naturalRotation({matrix:mirror(plain)},plain),null,'a reflecting host never gets a fake rotation');
assert.equal(naturalRotation(null,plain),null);
for(const locale of ['en','zh-CN'])for(const text of ['Manteletes','Faldones laterales','Todas las naciones','Más útiles primero'])
  assert.notEqual(translate(text,locale),text,text+' '+locale);
console.log('Armor editor: unplaced preview, native catalog filters, independent poses, parent following, recreated socket editing, recipes, diagnostics, grouped listing, nations, orders and mantlet placement passed.');
