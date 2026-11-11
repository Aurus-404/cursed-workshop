import assert from 'node:assert/strict';
import fs from 'node:fs';
globalThis.localStorage={getItem:()=> 'es'};
globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
const {vehicleDesign}=await import('../frontend/vehicle-design.js');
const {gunEditor}=await import('../frontend/gun-editor.js');
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',classList:{toggle(){},add(){},remove(){}},replaceChildren(...o){this.options=o;},add(o){(this.options??=[]).push(o);}});return nodes.get(id);};
const base={eid:'1',template:'actual_vehicle'},a={eid:'2',template:'cannon_donor'},b={eid:'3',template:'mg_donor'};
const state={busy:false,tab:'campaign',campaign:{units:[base,a,b]},hull:{...base,template:'old_recipe_hull'},turret:{...base,template:'old_recipe_module'},editing:{eid:'1'},
  transform:{hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,0],rotation:[0,0,0]},extra_armor:[{asset:'kept'}]}};
const requests=[],shown=[];let allow=true;
const job=async(path,body)=>{requests.push([path,structuredClone(body)]);
 if(path==='/api/preview')return {anchor_name:'turret',mounts:['turret','@free'],bones:[{name:'body',parent:null},{name:'turret',parent:'body'}]};
 if(path.endsWith('catalog'))return {kind:'vehicle',source:'fixture',weapons:['gun','coax'].map(place=>({place,weapon:place,available:true}))};
 return {hull:{anchor_name:'turret',meshes:[{bone:'kept_vehicle'}]},turret:{anchor_name:body.pending?'pitch':'',meshes:body.pending?[{bone:'pending'}]:[]}};
};
const design=vehicleDesign({state,$,job,displayPart:(p,d)=>shown.push([p,d]),updateSelection(){},remember(){},reportError(e){throw e;},confirm:()=>allow});
const gun=gunEditor({state,$,job,design,displayPart(){},updateSelection(){},selectEdit(){},remember(){},reportError(e){throw e;}});
$('operation-select').value='gun';await design.switchMode('gun');
assert.equal(state.hull.template,'actual_vehicle');assert.equal(design.draft().seed.recipient,'old_recipe_hull');assert.ok(state.turret.designPlaceholder);
await gun.select(a,'turret');state.transform.turret.position=[-1.67,0,0];await gun.save();
assert.equal(design.draft().guns[0].donor_eid,'2');assert.ok(state.turret.designPlaceholder);
$('operation-select').value='turret';await design.switchMode('turret');
assert.equal(design.draft().guns.length,1);assert.equal(design.turrets().length,0,'opening turret tool cannot apply the cannon donor turret');
assert.ok(requests.at(-1)[1].pending===undefined);
$('operation-select').value='gun';await design.switchMode('gun');await gun.select(b,'turret');
$('gun-recipient').value='coax';await $('gun-recipient').onchange();$('gun-donor').value='coax';await $('gun-donor').onchange();
state.busy=true;gun.controls();assert.ok($('gun-save').disabled);
// Production's busy completion must refresh gun controls, unlike the old path.
const app=fs.readFileSync('frontend/app.js','utf8'),source=app.match(/^function setBusy\(.*$/m)[0];
const setBusy=new Function('state','$','updateBuild','inspector','gunEditor',source+';return setBusy;')(state,$,()=>{},{render(){}},gun);
setBusy(false);assert.equal($('gun-save').disabled,false);await gun.save();
assert.deepEqual(design.draft().guns.map(r=>r.donor_eid),['2','3']);assert.equal(design.draft().guns[0].pose.position[0],-1.67);
assert.equal(design.body().transform.gun_swap,undefined,'pending selectors never enter compatibility checks');
// A saved main cannon remains selectable and can be replaced by a new donor.
// Discard restores the exact saved row; changing slots cannot carry its pose.
const originalMain=structuredClone(design.draft().guns[0]),coax=structuredClone(design.draft().guns[1]);
assert.equal($('gun-recipient').options.find(o=>o.value==='gun').disabled,false);
$('gun-recipient').value='gun';await $('gun-recipient').onchange();
assert.deepEqual(state.transform.turret,originalMain.pose);
assert.equal(state.turret.eid,'2');assert.equal(design.draft().guns.length,1);
await gun.select(b,'turret');assert.equal(state.transform.gun_swap.recipient_place,'gun');
assert.deepEqual(state.transform.turret,originalMain.pose);
await $('gun-discard').onclick();assert.deepEqual(design.draft().guns,[originalMain,coax]);
$('gun-recipient').value='gun';await $('gun-recipient').onchange();
await gun.select(b,'turret');state.transform.turret.pivot=[4,0,0];await gun.save();
assert.equal(design.draft().guns.length,2);assert.equal(design.draft().guns[0].donor_eid,'3');
assert.deepEqual(design.draft().guns[0].pose.pivot,[4,0,0]);assert.deepEqual(design.draft().guns[1],coax);
$('gun-recipient').value='gun';await $('gun-recipient').onchange();
$('gun-recipient').value='coax';await $('gun-recipient').onchange();
assert.equal(state.transform.gun_swap.recipient_place,'coax');
assert.deepEqual(state.transform.turret,coax.pose,'switching saved slots restores each slot pose');
await $('gun-discard').onclick();design.draft().guns[0]=originalMain;
$('gun-saved').value='0';await $('gun-edit-change').onclick();assert.equal(design.draft().guns.length,1);
await $('gun-discard').onclick();assert.equal(design.draft().guns.length,2);assert.equal(design.draft().guns[0].pose.position[0],-1.67);
$('operation-select').value='turret';await design.switchMode('turret');await design.select(a,'turret');
assert.equal(design.draft().guns.length,2,'selecting a prospective turret remains a preview');
allow=false;await design.saveTurret();assert.equal(design.draft().guns.length,2);
allow=true;await design.saveTurret();assert.equal(design.draft().guns.length,0);assert.equal(design.turrets()[0].donor_eid,'2');
assert.deepEqual(state.transform.extra_armor,[{asset:'kept'}]);
console.log('Vehicle design: operation isolation, explicit saves/conflicts, independent donors, kept armor and busy completion passed.');

// Editing the pose of an already saved turret retains its saved weapon changes.
design.draft().guns=[{schema:1,recipient_place:'gun',donor_place:'gun',donor_template:'mg_donor',donor_eid:'3',pose:{position:[1,2,3],rotation:[0,0,0]}}];
$('turret-saved').value='turret';await $('design-edit-turret').onclick();state.transform.turret.rotation=[0,0,12];await design.saveTurret();
assert.equal(design.draft().guns.length,1);assert.equal(design.turrets()[0].pose.rotation[2],12);
// A late preview must never replace the result of a more recent tool selection.
const replies=[],lateState={hull:base,transform:{hull:{},turret:{},assembly_draft:{schema:1,guns:[]}}};
const lateShown=[],crewRows=[];
const late=vehicleDesign({state:lateState,$,job:path=>path==='/api/preview'?Promise.resolve({}):new Promise(r=>replies.push(r)),receiveCrew:rows=>crewRows.push(rows),displayPart:(p,d)=>{assert.equal(crewRows.at(-1)[0].place,'gunner_new');lateShown.push(d);},updateSelection(){},remember(){},reportError(e){throw e;}});
const first=late.refresh(),last=late.refresh();
replies[1]({hull:{anchor_name:'new'},turret:{},crew_visibility:[{place:'gunner_new'}]});await last;
replies[0]({hull:{anchor_name:'old'},turret:{},crew_visibility:[{place:'gunner_old'}]});await first;
assert.equal(lateShown.length,2);assert.equal(lateShown[0].anchor_name,'new');
assert.deepEqual(crewRows,[[{place:'gunner_new'}]],'only final-role controls from the latest preview arrive before drawing');
console.log('Saved turret pose keeps weapons; late responses cannot replace a newer preview.');

// Drawing invokes compatibility callbacks synchronously: the new key must
// already be published, otherwise they enqueue another identical preview.
let keyDuringDraw;
const view=vehicleDesign({state:lateState,$,job:async()=>({hull:{},turret:{}}),displayPart(){keyDuringDraw=lateState.designPreviewKey;},updateSelection(){},remember(){},reportError(e){throw e;}});
await view.refresh();assert.equal(keyDuringDraw,view.key());

// Several turrets: one saved change per chassis turret, like Replace gun.
const multiState={busy:false,campaign:{units:[base,a,b]},hull:{eid:'1',template:'nbfz'},transform:{hull:{},turret:{}}};
const multi=vehicleDesign({state:multiState,$,job:async(path,body)=>path==='/api/preview'
  ?{mounts:['turret','gun_rot','aux_turret1','gun_rot1','@free'],bones:[{name:'turret',parent:'body'},{name:'gun_rot',parent:'turret'},
    {name:'aux_turret1',parent:'body'},{name:'gun_rot1',parent:'aux_turret1'}]}
  :{hull:{anchor_name:body.pending?.recipient_mount||'turret'},turret:{anchor_name:body.pending?'turret':''}},
  displayPart(){},updateSelection(){},remember(){},reportError(e){throw e;},confirm:()=>true});
$('operation-select').value='turret';await multi.switchMode('turret');await multi.refresh();
multi.controls();assert.deepEqual($('turret-target').options.map(o=>o.value),['turret','aux_turret1','@free'],'only top turrets and free spots');
$('turret-target').value='aux_turret1';await $('turret-target').onchange();
await multi.select(a,'turret');assert.equal(multiState.pendingTurret.recipient_mount,'aux_turret1');await multi.saveTurret();
await multi.select(b,'turret');assert.equal(multiState.pendingTurret.recipient_mount,'turret','the next unchanged turret is offered');await multi.saveTurret();
assert.deepEqual(multi.turrets().map(r=>[r.recipient_mount,r.donor_eid]),[['aux_turret1','2'],['turret','3']]);
$('turret-target').value='aux_turret1';await $('turret-target').onchange();
assert.equal(multiState.pendingTurret?.original,'aux_turret1','choosing a changed turret opens it to move');await multi.saveTurret();
await multi.editTurret();assert.equal(multiState.pendingTurret.original,'aux_turret1','Position opens the last saved turret');
multiState.transform.turret.rotation=[0,0,30];await multi.saveTurret();
assert.deepEqual(multi.turrets().map(r=>r.recipient_mount).sort(),['aux_turret1','turret'],'editing one keeps the other');
multi.controls();assert.deepEqual($('design-turret-pos').options.map(o=>o.value).filter(Boolean).sort(),['@hull','aux_turret1','turret'],'Position lists the saved turrets');
$('design-turret-limits').value='turret';await $('design-turret-limits').onchange();assert.equal(multiState.pendingTurret.original,'turret','Limits open the chosen turret');
await multi.saveTurret();
await multi.removeTurret('turret');assert.deepEqual(multi.turrets().map(r=>r.recipient_mount),['aux_turret1']);
const legacy={busy:false,campaign:{units:[base]},hull:base,transform:{hull:{},turret:{},assembly_draft:{schema:1,guns:[],turret:{donor_template:'cannon_donor',donor_eid:'2'}}}};
const old=vehicleDesign({state:legacy,$,job:async()=>({hull:{},turret:{}}),displayPart(){},updateSelection(){},remember(){},reportError(e){throw e;}});
assert.equal(old.turrets()[0].donor_eid,'2');assert.equal(legacy.transform.assembly_draft.turret,undefined,'older single-turret designs read as a list');
console.log('Several turrets: chassis targets, one change each, edit and remove, and older designs passed.');

// A carrier's root rotation encloses the entire chassis. Selecting a mortar
// must retain the chassis and prefer its native free/deck mounting choice.
const carrier={eid:'carrier',template:'carrier_fixture'},mortar={eid:'mortar',template:'mortar_fixture'};
const carrierState={busy:false,campaign:{units:[carrier,mortar]},hull:carrier,transform:{hull:{},turret:{}}};
const carrierRequests=[];
const carrierDesign=vehicleDesign({state:carrierState,$,job:async(path,body)=>{
  carrierRequests.push([path,structuredClone(body)]);
  return path==='/api/preview'?{anchor_name:'@free',mounts:['frame','mg_yaw','mg_pitch','@free'],
    bones:[{name:'frame',parent:null},{name:'body',parent:'frame'},{name:'mg_yaw',parent:'body'},{name:'mg_pitch',parent:'mg_yaw'}]}:
    {hull:{anchor_name:body.pending?.recipient_mount||'@free'},turret:{anchor_name:body.pending?'turret':''}};
},displayPart(){},updateSelection(){},remember(){},reportError(e){throw e;}});
$('operation-select').value='turret';await carrierDesign.select(mortar,'turret');
carrierDesign.controls();
assert.equal(carrierState.pendingTurret.recipient_mount,'@free');
assert.deepEqual($('turret-target').options.map(o=>o.value),['mg_yaw','@free']);
assert.ok($('design-turret-pos').options.some(o=>o.value==='@free'),'deck mounting stays reachable from Position');
$('turret-target').value='mg_yaw';await $('turret-target').onchange();
assert.equal(carrierState.pendingTurret.recipient_mount,'mg_yaw');
$('turret-target').value='@free';await $('turret-target').onchange();
assert.equal(carrierState.pendingTurret.recipient_mount,'@free');
await carrierDesign.saveTurret();
// The Crew panel can apply a pending mount using the same design save route,
// retaining the distinct final role names rather than editing donor aliases.
await carrierDesign.editTurret();
assert.ok(carrierState.pendingTurret);
const crewApply=app.match(/^\$\('crew-apply-mount'\)\.onclick=.*$/m)[0];
let operations=0;
new Function('$','toolOperation','draftEditor','reportError',crewApply)($,async run=>{operations++;return run();},carrierDesign,e=>{throw e;});
await $('crew-apply-mount').onclick();
assert.equal(operations,1);assert.equal(carrierState.pendingTurret,undefined);
assert.equal(carrierDesign.turrets().length,1,'applying from Crew cannot duplicate or construct a vehicle');
assert.equal(carrierDesign.turrets()[0].recipient_mount,'@free');
assert.equal(carrierState.hull.eid,'carrier');
console.log('Carrier free mounting: structural roots excluded, deck preferred, Position can change chassis mount and save it.');
// A wrapped tank differs from a carrier: after discarding the structural
// ancestor it must still offer and prefer its real turret. The B1 Ter report
// exposed this in the public build; a saved hull gun must not hide that option.
const wrapped={eid:'wrapped',template:'wrapped_tank'},wrappedGun={recipient_place:'gun1',donor_template:'cannon_donor',donor_eid:'2'};
const wrappedState={busy:false,campaign:{units:[wrapped,a]},hull:wrapped,
  transform:{hull:{},turret:{},assembly_draft:{schema:1,turrets:[],guns:[wrappedGun]}}};
const wrappedDesign=vehicleDesign({state:wrappedState,$,job:async(path,body)=>path==='/api/preview'
  ?{anchor_name:'turret',mounts:['basis','turret','gun_rot4','mgun_rot1','@free','@free:turret'],
    bones:[{name:'basis',parent:null},{name:'body1',parent:'basis'},{name:'turret',parent:'body1'},
      {name:'gun_rot4',parent:'turret'},{name:'mgun_rot1',parent:'gun_rot4'}]}
  :{hull:{anchor_name:body.pending?.recipient_mount||'turret'},turret:{anchor_name:body.pending?'turret':''}},
  displayPart(){},updateSelection(){},remember(){},reportError(e){throw e;}});
await wrappedDesign.select(a,'turret');wrappedDesign.controls();
assert.deepEqual($('turret-target').options.map(o=>o.value),['turret','@free','@free:turret']);
assert.equal($('turret-target').value,'turret');
assert.equal(wrappedState.pendingTurret.recipient_mount,'turret');
assert.deepEqual(wrappedDesign.draft().guns,[wrappedGun],'previewing a turret must retain saved gun changes');
console.log('Wrapped tank: real turret remains selectable beneath a structural rotation root.');
// A built roof turret is nested beneath the main turret. Its independent
// module boundary comes from the backend; hierarchy filtering must not hide it.
const nestedState={busy:false,campaign:{units:[base,a,b]},hull:base,
  transform:{hull:{},turret:{},assembly_draft:{schema:1,guns:[],turrets:[]}}};
const nestedDesign=vehicleDesign({state:nestedState,$,job:async(path,body)=>path==='/api/preview'
  ?{anchor_name:'turret',mounts:['turret','gun_rot','module_turret','module_pitch','@free'],
    module_mounts:['turret','module_turret'],bones:[{name:'turret',parent:'body'},{name:'gun_rot',parent:'turret'},
      {name:'module_turret',parent:'turret'},{name:'module_pitch',parent:'module_turret'}]}
  :{hull:{anchor_name:body.pending?.recipient_mount||'turret'},turret:{anchor_name:body.pending?'module_turret':''}},
  displayPart(){},updateSelection(){},remember(){},reportError(e){throw e;},confirm:()=>true});
await nestedDesign.loadChassis();nestedDesign.controls();
assert.ok($('design-turret-pos').options.some(o=>o.value==='module_turret'));
assert.ok($('design-turret-limits').options.some(o=>o.value==='module_turret'));
await nestedDesign.editTurret('module_turret');
assert.equal(nestedState.pendingTurret.recipient_mount,'module_turret');
nestedState.transform.turret.position=[4,0,3];nestedState.transform.joint_limits.turret={module_pitch:[-5,20]};
await nestedDesign.saveTurret();
assert.deepEqual(nestedDesign.turrets()[0].pose.position,[4,0,3]);
// A saved donor with no overrides cannot inherit those of the previous donor.
nestedDesign.draft().turrets.push({recipient_mount:'@free',donor_template:'mg_donor',donor_eid:'3',pose:{position:[0,0,0],rotation:[0,0,0]}});
await nestedDesign.editTurret('module_turret');await nestedDesign.editTurret('@free');
assert.deepEqual(nestedState.transform.joint_limits.turret,{});
console.log('Nested turret selection, saved pose/limits, and independent limit state passed.');
// Post-build reload clears both selected units but used to keep Turret mode.
carrierState.hull=null;carrierState.turret=null;carrierState.pick='turret';carrierState.transform={hull:{},turret:{}};
await carrierDesign.select(carrier,'turret');
assert.equal(carrierState.pick,'hull');assert.equal(carrierState.hull.eid,carrier.eid);
await carrierDesign.select(mortar,'turret');await carrierDesign.saveTurret();
assert.equal(carrierDesign.turrets()[0].donor_eid,mortar.eid,'a second design starts without restarting the application');
const inheritedSource={template:'mw_old',recipient:'base',donor:'base',transform:{commander_only:true},parent:null};
const inheritedState={hull:carrier,turret:carrier,campaign:{units:[carrier]},editing:{eid:carrier.eid},
  designOrigin:{eid:carrier.eid,recipient:'original_hull',donor:'original_donor'},
  transform:{source_recipe:inheritedSource,commander_mg:{item:'kept'},smoke_launchers:[{tubes:2}]}};
// A built module can be made to turn with another part, and that choice is the recipe's.
{
  const nativeState={busy:false,tab:'campaign',campaign:{units:[base]},hull:{...base},turret:{...base},pendingTurret:null,
    transform:{hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,0],rotation:[0,0,0]},assembly_draft:{schema:1,guns:[],turrets:[]}}};
  const calls=[];
  const nativeJob=async(path,body)=>{calls.push([path,structuredClone(body)]);
    if(path==='/api/preview')return {anchor_name:'turret',mounts:['turret','module_turret','@free','@free:turret'],module_mounts:['turret','module_turret'],bones:[{name:'body',parent:null},{name:'turret',parent:'body'},{name:'module_turret',parent:'body'}]};
    return {hull:{anchor_name:'module_turret',mount_parent:body.pending?.mount_parent||'body',module_hosts:body.pending?['body','turret']:[],meshes:[]},turret:{anchor_name:'module_turret',meshes:[]}};};
  const native=vehicleDesign({state:nativeState,$,job:nativeJob,displayPart(){},updateSelection(){},remember(){},reportError(e){throw e;}});
  assert.equal(await native.editTurret('module_turret'),true);native.controls();
  assert.deepEqual($('module-host').options.map(o=>o.value),['body','turret']);
  assert.equal($('module-host').value,'body','it shows what the module hangs from now');
  $('module-host').value='turret';await $('module-host').onchange();
  assert.equal(calls.at(-1)[1].pending.mount_parent,'turret');
  native.settleNative();
  assert.equal(native.turrets()[0].mount_parent,'turret');
  await native.editTurret('module_turret');native.controls();
  assert.equal($('module-host').value,'turret','reopened, the choice is still there');
  assert.equal(native.transform().assembly_draft.turrets[0].mount_parent,'turret');
  // Only a free spot with nothing to put there says what choosing it really does.
  native.settleNative();nativeState.designTarget=null;
  const hint=[];$('turret-target-hint').classList.toggle=(name,on)=>hint.push(on);
  $('operation-select').value='turret';
  await $('turret-target').onchange.call(null,($('turret-target').value='@free:turret',null));native.controls();
  assert.equal(hint.at(-1),false,'the hint shows, it does not act as if it were done');
  assert.equal(native.turrets().length,1);assert.equal(native.turrets()[0].recipient_mount,'module_turret');
  $('turret-target-hint').classList.toggle=()=>{};
}
// Moving a crew member asks for no new preview: the server draws them over the same vehicle.
{
  const before=design.key();
  const saved=state.transform.crew_seats;state.transform.crew_seats={gunner:{position:[1,2,3],space:'module'}};
  assert.equal(design.key(),before);assert.ok(design.body().transform.crew_seats.gunner,'the seat still goes with the design');
  state.transform.crew_seats=saved;
}
const inheritedDesign=vehicleDesign({state:inheritedState,$,job,displayPart(){},updateSelection(){},remember(){},reportError(e){throw e;}});
inheritedDesign.ensure();
assert.equal(inheritedState.transform.source_recipe,undefined,'replayed source belongs only to the seed when starting another operation');
assert.deepEqual(inheritedState.transform.assembly_draft.seed.transform.source_recipe,inheritedSource);
assert.equal(inheritedState.transform.commander_mg.item,'kept');

// Moving a fitting the editor draws itself asks for no new preview; adding or removing one does.
{state.transform.extra_armor=[{asset:'a',parent:'body',position:[0,0,0],rotation:[0,0,0]}];const placed=design.key();
  state.transform.extra_armor[0].position=[5,6,7];state.transform.extra_armor[0].rotation=[0,0,90];
  if(design.key()!==placed)throw Error('moving armour must not change the preview key');
  state.transform.extra_armor.push({asset:'b',parent:'body',position:[0,0,0],rotation:[0,0,0]});
  if(design.key()===placed)throw Error('adding armour must change the preview key');
  delete state.transform.extra_armor;}

// The compatibility check ignores where loose fittings sit, but not what they hang from or mounted weapons' poses.
{const {withoutPlacement,LOOSE_FITTINGS}=await import('../frontend/vehicle-design.js');
  const base={extra_armor:[{asset:'a',parent:'body',position:[0,0,0],rotation:[0,0,0],plate:{width:1,height:1,thickness:1}}],commander_mg:{position:[1,2,3]},mobility:{speed:40}};
  const same=x=>JSON.stringify(withoutPlacement(x,LOOSE_FITTINGS));
  const moved=structuredClone(base);moved.extra_armor[0].position=[9,9,9];moved.extra_armor[0].plate.thickness=80;
  if(same(moved)!==same(base))throw Error('moving or resizing armour must not ask for a new check');
  const rehung=structuredClone(base);rehung.extra_armor[0].parent='turret';if(same(rehung)===same(base))throw Error('a new attachment point is a new check');
  const mount=structuredClone(base);mount.commander_mg.position=[0,0,0];if(same(mount)===same(base))throw Error('other fittings hang from the commander mount: it is checked');
  if(base.extra_armor[0].position.length!==3)throw Error('the design itself is left untouched');}
