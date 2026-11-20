import assert from 'node:assert/strict';
import * as THREE from 'three';
import {partKind,plainColor,viewColors,DEFAULT_PART_COLORS,PART_KINDS} from '../frontend/part-colors.js';
import {applyModelTextures} from '../frontend/model-geometry.js';

// Kinds come from the group, then from the bone a hull/turret piece hangs on.
assert.equal(partKind('hull',{bone:'body'}),'chassis');
assert.equal(partKind('hull',{bone:'track_l'}),'tracks');
assert.equal(partKind('hull',{bone:'wheel_r3'}),'tracks');
assert.equal(partKind('turret',{bone:'turret'}),'turret');
assert.equal(partKind('turret',{bone:'gun'}),'weapon');
assert.equal(partKind('turret',{bone:'mgun'}),'weapon');
assert.equal(partKind('turret',{bone:'turret',attached_weapon:true}),'weapon');
assert.equal(partKind('extra_armor',{bone:'gun'}),'armor');
assert.equal(partKind('smoke_launchers',{}),'accessory');
assert.equal(partKind('rider_mgs',{}),'weapon');
// A whole vehicle (Edit) draws its own turret with the chassis: still turret colored.
assert.equal(partKind('hull',{bone:'turret',turret_branch:true}),'turret');
assert.equal(partKind('hull',{bone:'hatch1',turret_branch:true}),'turret');
assert.equal(partKind('hull',{bone:'gun',turret_branch:true}),'weapon');
assert.equal(partKind('mounted_weapons',{}),'weapon');

// Saved values are cleaned; bad colors fall back to defaults.
const saved=viewColors({by_kind:false,colors:{weapon:'#ABCDEF',tracks:'red'}});
assert.equal(saved.by_kind,false);
assert.equal(saved.colors.weapon,'#abcdef');
assert.equal(saved.colors.tracks,DEFAULT_PART_COLORS.tracks);
assert.deepEqual(Object.keys(viewColors().colors),PART_KINDS);
// Off: the classic two colors.
assert.equal(plainColor('turret',{bone:'gun'},saved),saved.colors.turret);
assert.equal(plainColor('hull',{bone:'track_l'},saved),saved.colors.chassis);
assert.equal(plainColor('hull',{bone:'hatch1',turret_branch:true},saved),saved.colors.turret);
assert.equal(plainColor('extra_armor',{},saved),saved.colors.turret);

// A custom plate's painted material is plain when textures are off,
// and painted again when they are on.
const paint=new THREE.Texture();
const painted=new THREE.MeshStandardMaterial({map:paint});
const plate=new THREE.Mesh(new THREE.BoxGeometry(),painted);plate.userData={mesh:{},flat:painted,customArmor:true};
const hullMaterial=new THREE.MeshStandardMaterial({color:0x123456});
const track=new THREE.Mesh(new THREE.BoxGeometry(),hullMaterial);track.userData={mesh:{bone:'track_l'},flat:hullMaterial};
const seatMaterial=new THREE.MeshStandardMaterial({color:0x6fc3df});seatMaterial.userData.shared=true;
const seat=new THREE.Mesh(new THREE.BoxGeometry(),seatMaterial);seat.userData={mesh:{},flat:seatMaterial};
const groups={hull:new THREE.Group(),extra_armor:new THREE.Group()};groups.hull.add(track,seat);groups.extra_armor.add(plate);
const settings=viewColors();
applyModelTextures(groups,false,()=>null,false,(part,mesh)=>plainColor(part,mesh,settings));
assert.equal(plate.material.map,null,'armor shows no paint without textures');
assert.equal('#'+plate.material.color.getHexString(),settings.colors.armor);
assert.equal('#'+track.material.color.getHexString(),settings.colors.tracks);
assert.equal(seat.material,seatMaterial,'seat figures keep their own material');
applyModelTextures(groups,true,()=>null,true,(part,mesh)=>plainColor(part,mesh,settings));
assert.equal(plate.material,painted,'armor paint returns with textures');
assert.equal(plate.material.wireframe,true);
console.log('part colors ok');
