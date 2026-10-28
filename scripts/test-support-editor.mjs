import assert from 'node:assert/strict';
import * as THREE from 'three';
globalThis.localStorage={getItem:()=> 'es'};
globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
const {supportEditor}=await import('../frontend/support-editor.js');
const {translate}=await import('../frontend/i18n.js');
const {selectionData}=await import('../frontend/diagnostics.js');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',options:[],disabled:false,parentElement:{classList:{toggle(){},add(){}}},classList:{toggle(){},add(){}},
  add(o){this.options.push(o);if(this.options.length===1)this.value=o.value;},replaceChildren(){this.options=[];this.value='';}});return elements.get(id);};
$('operation-select').value='support';
const state={tab:'campaign',transform:{},edit:'turret'},groups={hull:new THREE.Group(),turret:new THREE.Group()};
let editor,remembered=0,checks=0,previewGate=null,displayed=0;
const job=async(path,body)=>{
  if(path==='/api/support-preview'&&previewGate)await previewGate;
  if(path==='/api/support-catalog')return {profiles:[{id:'ammo250',label:'Crate',kind:'ammo',capacity:250,radius:10,macro:'supply_ammo'},
    {id:'fuel',label:'Fuel',kind:'fuel',capacity:1000,radius:12}],cargo:[{id:'crate',label:'Crate',kind:'ammo'},{id:'can',label:'Can',kind:'fuel'}]};
  if(path==='/api/preview')return {anchor_name:'turret',anchor:[0,0,15]};
  if(path==='/api/support-preview')return {hull:{meshes:[]},cargo:{meshes:body.support_vehicle.cargo.map((row,i)=>{
    const m=new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...row.rotation.map(THREE.MathUtils.degToRad),'ZYX'));
    m.setPosition(...row.position);
    return {cargo_index:i,positions:[[0,0,0],[1,0,0],[0,1,0]].flatMap(v=>new THREE.Vector3(...v).applyMatrix4(m).toArray())};})}};
  throw Error(path);
};
function displayPart(part,data){displayed++;groups[part].clear();for(const mesh of data.meshes){const object=new THREE.Mesh(new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute(mesh.positions,3)),new THREE.MeshBasicMaterial());editor.addMesh(mesh,object);}}
editor=supportEditor({state,$,THREE,groups,job,displayPart,updateSelection(){},selectEdit(){state.edit='turret';},remember(){remembered++;},refreshCheck(){checks++;},reportError(error){throw error;}});
await editor.select({eid:'0x1',template:'tank',kind:'vehicle'});
assert.equal(state.transform.support_vehicle.profile,'ammo250');
assert.equal(state.transform.support_vehicle.remove_mount,'turret');
assert.equal(groups.turret.children.length,1);
await $('cargo-duplicate').onclick();
assert.equal(state.transform.support_vehicle.cargo.length,2);
assert.equal(state.transform.support_vehicle.profile,'ammo250');
const first=structuredClone(state.transform.support_vehicle.cargo[0]);
editor.target().position.x=37;assert.equal(editor.capture(),true);
assert.equal(state.transform.support_vehicle.cargo[1].position[0],37);
assert.deepEqual(state.transform.support_vehicle.cargo[0],first);
$('support-kind').value='fuel';await $('support-kind').onchange();
assert.equal(state.transform.support_vehicle.profile,'fuel');
const recipe=structuredClone(state.transform);await editor.restore();assert.deepEqual(state.transform,recipe);
await $('cargo-remove').onclick();assert.equal(state.transform.support_vehicle.cargo.length,1);
assert.ok(remembered>=3);assert.ok(checks);
const report=selectionData(state);assert.equal(report.mode,'support');assert.equal(report.support_vehicle.cargo.length,1);
await assert.rejects(editor.select({depot:'x',template:'tank',kind:'part'}),/campaña/);
for(const language of ['en','zh-CN']){
  assert.notEqual(translate('Convertir a vehículo de apoyo',language),'Convertir a vehículo de apoyo');
  assert.notEqual(translate('Paquete de suministro',language),'Paquete de suministro');
}
let release;previewGate=new Promise(resolve=>{release=resolve;});
const pending=editor.refresh(),before=displayed;
$('operation-select').value='turret';editor.controls();release();await pending;
assert.equal(displayed,before,'A stale support response must not replace another operation preview');
assert.equal($('pick-turret').disabled,false);
console.log('Support editor: native packages, cargo duplication, independent gizmo poses, recipe, languages and diagnostic passed.');
