import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from 'three';
globalThis.localStorage={getItem:()=> 'es'};
globalThis.Option=class {constructor(text,value){this.text=text;this.value=value;}};
globalThis.document={createElement:()=>({label:'',options:[],append(...o){this.options.push(...o);}})};
const {mountedWeaponEditor,addMounted,removeMounted,setSlot,slotItem,mountedParent,validLimits,weaponGroups,mountedRows}=await import('../frontend/mounted-weapon-editor.js');
const {translate}=await import('../frontend/i18n.js');

// The recipe: added weapons and slots share one key, which goes away when empty.
const transform={};
assert.equal(addMounted(transform,{parent:'body',item:'mg42_belt'}),0);
assert.ok(removeMounted(transform,0));assert.equal(transform.mounted_weapons,undefined);
setSlot(transform,'mgun1','mg42_belt');assert.equal(slotItem(transform,'mgun1'),'mg42_belt');
transform.mounted_weapons.slots[0].link={position:[1,0,0],rotation:[0,0,0]};
setSlot(transform,'mgun1','boys_mk1');
assert.deepEqual(transform.mounted_weapons.slots,[{place:'mgun1',item:'boys_mk1',link:{position:[1,0,0],rotation:[0,0,0]}}],'a new weapon keeps its spot on the mount');
setSlot(transform,'mgun1','');assert.equal(transform.mounted_weapons,undefined,'its own weapon again: no recipe key');
const hosts=[{name:'basis'},{name:'body'},{name:'turret'},{name:'wheel_l1'}];
assert.equal(mountedParent(hosts,'wheel_l1'),'wheel_l1','any part, even a wheel');
assert.equal(mountedParent(hosts,'gone'),'body');
assert.equal(mountedParent([{name:'basis'},{name:'hull'}],''),'hull');
assert.ok(validLimits([-15,15]));assert.ok(!validLimits([15,-15]));assert.ok(!validLimits([0,NaN]));
assert.deepEqual(weaponGroups([{id:'boys',kind:'at_rifle'},{id:'mg42',kind:'mgun'}]).map(([,rows])=>rows.map(r=>r.id)),[['mg42'],['boys']]);

// The editor: Add puts a weapon on the chosen part; the check's slots take an infantry weapon.
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',checked:true,options:[],disabled:false,textContent:'',classList:{toggle(){}},
  replaceChildren(...options){this.options=options;this.value=options[0]?.value||'';},add(o){this.options.push(o);},append(o){this.options.push(...(o.options||[o]));}});return nodes.get(id);};
const identity=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]];
const preview={meshes:[{positions:[0,0,0,1,0,0,0,1,0],indices:[0,1,2],rig_joint:'pitch'}],actor_frame:identity};
const calls=[],job=async(route,body)=>{calls.push([route,body]);return route.endsWith('catalog')?
  {weapons:[{id:'mg42_belt',label:'MG 42',kind:'mgun'},{id:'boys_mk1',label:'Boys',kind:'at_rifle'}],dlc:{}}:preview;};
const scene=new THREE.Group(),groups={hull:new THREE.Group(),turret:new THREE.Group()};
const state={hull:{},turret:{},transform:{},busy:false,edit:'mounted_weapons'};let remembered=0,checks=0,selected=[];
const editor=mountedWeaponEditor({state,$,scene,job,loading:async(label,fn)=>fn(),remember:()=>remembered++,selectEdit:part=>selected.push(part),
  refreshCheck:async()=>{checks++;},reportError:error=>{throw error;},applyShow:()=>{},groups});
assert.equal(groups.mounted_weapons.parent,scene);
editor.receiveCheck({armor_hosts:[{name:'body',matrix:identity},{name:'turret',matrix:[[1,0,0,0],[0,1,0,0],[0,0,1,10],[0,0,0,1]]}],
  mount_slots:[{place:'mgun1',weapon:'browning_tank',mountable:false,host:'bow_pitch',matrix:identity,link_matrix:identity}]});
await $('mounted-load').onclick();
assert.deepEqual($('mounted-item').options.map(o=>o.value),['mg42_belt','boys_mk1'],'machine guns first, then AT rifles');
$('mounted-parent').value='turret';$('mounted-item').value='boys_mk1';
await $('mounted-add').onclick();
const row=mountedRows(state.transform)[0];
assert.equal(row.parent,'turret');assert.equal(row.item,'boys_mk1');assert.equal(row.rotate,true,'a casemate weapon by default');
assert.deepEqual([row.yaw,row.pitch],[[-15,15],[-10,20]]);assert.equal(remembered,1);
assert.ok(selected.includes('mounted_weapons'));
// The gizmo moves the whole mount, or the weapon on its mount.
const mount=editor.target();assert.ok(mount);
mount.position.set(3,4,5);assert.ok(editor.capture());assert.deepEqual(row.position,[3,4,5]);
$('mounted-component').value='link';$('mounted-component').onchange();
const link=editor.target();assert.notEqual(link,mount);link.position.set(-2,0,1);editor.capture();
assert.deepEqual(row.link.position,[-2,0,1]);assert.deepEqual(row.position,[3,4,5]);
// Limits are checked before they reach the recipe; the arcs read them.
$('mounted-yaw-0').value='30';$('mounted-yaw-0').onchange();assert.deepEqual(row.yaw,[-15,15],'a minimum above the maximum is refused');
$('mounted-yaw-0').value='-40';$('mounted-yaw-0').onchange();assert.deepEqual(row.yaw,[-40,15]);
const [rig]=editor.weaponRigs();assert.deepEqual(rig.joints.map(j=>[j.key,j.limits()]),[['yaw',[-40,15]],['pitch',[-10,20]]]);
$('mounted-rotate').checked=false;$('mounted-rotate').onchange();assert.equal(row.rotate,false);
// A bow MG becomes a slot holding an MG 42.
assert.deepEqual(editor.slotPlaces().map(s=>s.place),['mgun1'],'Replace gun reads the slots from the check');
await editor.changeSlot('mgun1','mg42_belt');assert.ok(editor.placeable('mgun1'));
assert.equal(slotItem(state.transform,'mgun1'),'mg42_belt');
assert.ok(groups.mounted_weapons.children.some(n=>n.userData.slot==='mgun1'),'the slot weapon is drawn on its joint');
await $('mounted-remove').onclick();assert.equal(mountedRows(state.transform).length,0);
assert.ok(state.transform.mounted_weapons.slots.length,'removing the added weapon keeps the slot');
assert.ok(calls.some(([route,body])=>route==='/api/mounted-weapon-preview'&&body.item==='mg42_belt'));

// Wiring and copy: the tool, its panel, its translations.
const html=fs.readFileSync('frontend/index.html','utf8'),app=fs.readFileSync('frontend/app.js','utf8'),inspector=fs.readFileSync('frontend/inspector.js','utf8');
for(const id of ['mounted-section','mounted-load','mounted-add','mounted-list','mounted-rotate','gun-mountable','gun-mountable-item','gun-mountable-place'])assert.ok(html.includes(`id="${id}"`),id);
assert.ok(inspector.includes("part:'mounted_weapons'"));
for(const call of ['mountedEditor.restore()','mountedEditor.capture()','mountedEditor.receiveCheck(state.check)','mountedEditor.weaponRigs()','state.transform.mounted_weapons=structuredClone(tr.mounted_weapons)'])
  assert.ok(app.includes(call),call);
for(const language of ['en','zh-CN','ru'])for(const text of ['Armas sin torreta','Girar el vehículo al llegar al límite','Elegir MG / fusil AT','Usar otra unidad como donante'])
  assert.notEqual(translate(text,language),text,`${language}: ${text}`);
console.log('Mounted weapons: recipe, placement on any part, casemate turning, limits, arcs, mountable slots and translations passed.');
