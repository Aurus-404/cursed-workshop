import assert from 'node:assert/strict';
import * as THREE from 'three';
import {liftedPositions,armorPositions,armorView} from '../frontend/armor-view.js';

// A bent plate: two faces meeting at an edge, each with its own copy of the
// shared corners (as volumes store them). Lifted, the copies must stay together:
// moving each triangle on its own opened seams on curved plates.
const positions=[0,0,0, 1,0,0, 0,1,0,  1,0,0, 0,1,0, 1,1,1, 0.5,0.5,-2];
const indices=[0,1,2, 3,4,5, 0,1,6];
const lifted=liftedPositions(positions,indices,0.1);
const at=i=>Array.from(lifted.slice(3*i,3*i+3));
assert.deepEqual(at(1),at(3),'a shared corner moves to one place');
assert.deepEqual(at(2),at(4),'a shared corner moves to one place');
const moved=i=>Math.hypot(...at(i).map((v,k)=>v-positions[3*i+k]));
for(let i=0;i<7;i++)assert.ok(Math.abs(moved(i)-0.1)<1e-6,'every corner moves by the lift distance');
// Outwards, away from the centre of the volume.
const centre=[0,1,2].map(k=>positions.filter((_,j)=>j%3===k).reduce((a,b)=>a+b)/7);
const dist=(p)=>Math.hypot(...p.map((v,k)=>v-centre[k]));
assert.ok(dist(at(5))>dist(positions.slice(15,18)),'corners move away from the centre');

// Rendering, not just the lift helper: a late volume must have the same shape
// as the first, even beside a very long gun. Keep the native thickness data.
const box=new THREE.BoxGeometry(400,200,100);
const volume={positions:Array.from(box.attributes.position.array),indices:Array.from(box.index.array),
  faces:Array(12).fill(1),thickness:{default:50},name:'hull',material:'steel'};
const longGun={...volume,name:'gun',positions:volume.positions.map((v,i)=>i%3===0?v*10:v)};
const classes={add(){},remove(){}};
globalThis.document={createElement:()=>({className:'',classList:classes})};
const groups={hull:new THREE.Group(),turret:new THREE.Group()};
const state={show:{armor:true},previews:{hull:{armor:[volume,...Array(60).fill(longGun),volume]}}};
const layer=armorView({state,groups,camera:new THREE.PerspectiveCamera(),canvas:{addEventListener(){}},viewport:{append(){}}});
layer.sync();
let meshes=groups.hull.getObjectByName('armor_layer').children;
assert.deepEqual(Array.from(meshes[0].geometry.attributes.position.array),Array.from(meshes.at(-1).geometry.attributes.position.array),
  'number and size of other volumes do not inflate a plate');
const original=Float32Array.from(volume.indices.flatMap(i=>volume.positions.slice(3*i,3*i+3)));
for(const mesh of [meshes[0],meshes.at(-1)]){
  const vertices=mesh.geometry.attributes.position.array;
  for(let i=0;i<vertices.length;i+=3)assert.ok(Math.hypot(...Array.from(vertices.slice(i,i+3),(v,k)=>v-original[i+k]))<=.0201,
    'visual lift is at most .02 native model units');
  assert.equal(mesh.userData.armor.thickness.default,50);
  assert.equal(mesh.material.depthWrite,true);
  assert.equal(mesh.material.polygonOffset,true);
}
state.previews.hull={armor:[volume]};layer.sync();
assert.deepEqual(Array.from(groups.hull.getObjectByName('armor_layer').children[0].geometry.attributes.position.array),
  Array.from(meshes[0].geometry.attributes.position.array),'removing unrelated volumes preserves the surface');
assert.deepEqual(armorPositions({positions:[],indices:[]}),new Float32Array());
const tiny={positions,indices};
const tinyLift=armorPositions(tiny);
assert.ok(Math.hypot(...Array.from(tinyLift.slice(0,3),(v,k)=>v-positions[k]))<.001,'small parts get a proportional lift');
box.dispose();
console.log('armor view: ok');
