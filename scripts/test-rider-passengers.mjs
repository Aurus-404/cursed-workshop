import assert from 'node:assert/strict';
import * as THREE from 'three';
import {riderEditor} from '../frontend/rider-editor.js';
import {passengerEditor} from '../frontend/passenger-editor.js';
globalThis.localStorage={getItem:()=> 'es'};
globalThis.Option=class {constructor(text,value){this.text=text;this.value=value;}};
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',options:[],dataset:{},classList:{toggle(){}},
  replaceChildren(...options){this.options=options;this.value=options[0]?.value||'';},add(o){this.options.push(o);if(this.options.length===1)this.value=o.value;}});return nodes.get(id);};
const scene=new THREE.Scene(),groups={hull:new THREE.Group(),turret:new THREE.Group()};scene.add(groups.hull,groups.turret);
const identity=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],hosts=[{name:'body',matrix:identity}];
const row={support:'fixture:mg',item:'mg42_belt',pose:'pose',parent:'body',position:[1,2,3],rotation:[0,0,0],yaw:[-90,90],pitch:[-10,40]};
const state={wire:false,hull:{template:'h'},turret:{template:'t'},transform:{rider_mgs:[structuredClone(row),{...structuredClone(row),position:[8,9,10]}]},edit:'rider_mgs'};
const makeSeat=seat=>{const g=new THREE.Group();g.name=seat.place;g.userData.seat='passenger';g.userData.bone='rider';return g;};
let jobs=0,attached=null,passenger;
const common={state,$,groups,scene,loading:async(label,run)=>run(),remember(){},refreshCheck:async()=>{},reportError(e){throw e;},applyShow(){},makeSeat,
  selectEdit(part){state.edit=part;attached=part==='passenger_seats'?passenger.target():rider.target();}};
const rider=riderEditor({...common,job:async path=>{jobs++;
  if(path==='/api/rider-catalog')return {supports:[{id:'fixture:mg',label:'MG',turns:true,pose:'pose',yaw_limits:[-90,90],pitch_limits:[-10,40]}],weapons:[{id:'mg42_belt',label:'MG42'}],poses:[{id:'pose',source:'fixture'},{id:'alternate',source:'fixture'}]};
  assert.equal(path,'/api/rider-preview');return {pitch_matrix:identity,meshes:[{positions:[0,0,0,10,0,0,0,1,0],indices:[0,1,2],rig_joint:'pitch'}],seats:[{place:'rider'}]};
}});
rider.receiveCheck({armor_hosts:hosts});await rider.restore();
const beforeReindex=jobs;state.artRevision=1;await rider.restore();
assert.equal(jobs,beforeReindex+2,'reindexing must refresh the passenger MG catalog and geometry');
assert.match($('rider-dlc-note').textContent,/DLC/);
const rigs=rider.weaponRigs();assert.equal(rigs.length,2,'all rider mounts participate in weapon angles');
assert.notEqual(rigs[0].key,rigs[1].key);assert.deepEqual(rigs[0].joints.map(j=>j.kind),['traverse','elevation']);
assert.equal(rigs[0].jointOf(rigs[0].group.children[0].children[0]),'pitch');assert.equal(rigs[0].jointOf(rigs[0].group.children[2].children[0].children[0]),'yaw');
state.transform.rider_mgs[0].yaw=[-25,30];assert.deepEqual(rigs[0].joints[0].limits(),[-25,30]);
state.transform.passenger_seats={added:[{pose:'pose',parent:'body',position:[2,3,4],rotation:[0,0,0]}]};
passenger=passengerEditor({...common,reposeSeat(){},job:async path=>{jobs++;assert.equal(path,'/api/passenger-catalog');return {poses:[{id:'pose',source:'fixture'}]};}});
passenger.receiveCheck({armor_hosts:hosts});await passenger.restore();
$('passenger-list').value='a:0';$('passenger-list').onchange();const before=passenger.target();const count=jobs;
for(let i=0;i<50;i++){before.position.x=i;assert.ok(passenger.capture());passenger.sync();assert.equal(passenger.target().position.x,i);}
assert.equal(jobs,count,'movement is immediate and never waits on an API job');
passenger.receiveCheck({armor_hosts:hosts});assert.notEqual(passenger.target(),before,'compatibility redraw replaces seat objects');
assert.equal(attached,passenger.target(),'gizmo must follow the replacement object without selecting another seat');
assert.equal(attached.position.x,49);attached.position.x=55;passenger.capture();passenger.sync();assert.equal(passenger.target().position.x,55);
state.edit='rider_mgs';const other=structuredClone(state.transform.rider_mgs[1]);
rider.target().position.x=72;rider.capture();rider.sync();assert.equal(rider.target().position.x,72);assert.deepEqual(state.transform.rider_mgs[1],other);
const localJobs=jobs;
for(const component of ['mount_pose','gun_pose','operator_pose']){
  $('rider-component').value=component;$('rider-component').onchange();
  rider.target().position.set(4,5,6);rider.target().rotation.set(.1,.2,.3,'ZYX');rider.capture();rider.sync();
  assert.deepEqual(state.transform.rider_mgs[0][component].position,[4,5,6]);
  assert.ok(Math.abs(state.transform.rider_mgs[0][component].rotation[0]-THREE.MathUtils.radToDeg(.1))<.001);
}
assert.equal(jobs,localJobs,'independent component movement is local');
await $('rider-load').onclick();
$('rider-seat-0').value='17';await $('rider-seat-0').onchange();
$('rider-pose').value='alternate';await $('rider-pose').onchange();
$('rider-component').value='assembly';$('rider-component').onchange();
assert.equal(rider.pose().seat[0],17,'changing a preview pose must not reset operator displacement');
assert.deepEqual(rider.pose().operator_pose.position,[4,5,6]);
await $('rider-add').onclick();
assert.equal(state.transform.rider_mgs.at(-1).seat[0],17,'placement preserves the adjusted preview');
console.log('Rider angles: independent rigs and live limits. Passenger dragging: immediate updates, no API wait and selection survives compatibility redraw.');
