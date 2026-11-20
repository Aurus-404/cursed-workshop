import assert from 'node:assert/strict';
globalThis.localStorage={getItem:()=> 'es'};
globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
const {gunEditor}=await import('../frontend/gun-editor.js');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',add(value){(this.options??=[]).push(value);},replaceChildren(...options){this.options=options;},classList:{toggle(){}},textContent:''});return elements.get(id);};
$('operation-select').value='gun';
const state={tab:'campaign',transform:{hull:{position:[0,0,0]},turret:{position:[0,0,0]}},pick:'hull'};
const calls=[],shown=[];let remembered=0;
const preview={hull:{anchor_name:'pitch',meshes:[{bone:'tower'}]},turret:{anchor_name:'elevation',meshes:[{bone:'barrel'}]}};
const job=async(path,body)=>{
  calls.push([path,body]);
  if(path==='/api/gun-catalog')return {kind:body.template==='pak'?'towed':'vehicle',source:'fixture',weapons:[{place:'main',weapon:'native_cannon',available:true},{place:'broken',weapon:'bad',available:false,reason:'missing socket'},{place:'coax',weapon:'machinegun',available:true}]};
  if(path==='/api/preview')return preview.hull;
  if(path==='/api/gun-preview')return structuredClone(preview);
  throw Error('Unexpected route '+path);
};
const controller=gunEditor({state,$,job,displayPart:(part,data)=>shown.push([part,data]),updateSelection(){},selectEdit(){},remember(){remembered++;},reportError(error){throw error;}});
assert.ok(controller.active());
await controller.select({eid:'0x1',template:'tank'},'hull');
await controller.select({eid:'0x2',template:'pak'},'turret');
assert.equal(state.transform.gun_swap.recipient_place,'main');assert.equal(state.transform.gun_swap.donor_place,'main');
assert.ok(state.gunPreviewKey);assert.equal(state.mounts.hull,'pitch');assert.equal(shown.at(-1)[0],'turret');
assert.ok($('gun-donor').options[1].disabled);assert.match($('gun-note').textContent,/artillería/);
assert.equal($('pick-turret').textContent,'Donante');
state.editing={eid:'0x1'};controller.controls();assert.equal($('pick-turret').textContent,'Torreta');assert.equal($('pick-hull').textContent,'Chasis');state.editing=null;controller.controls();
state.transform.turret.position=[2,3,4];
await controller.restore();assert.deepEqual(state.transform.turret.position,[2,3,4]);
$('gun-donor').value='main';await $('gun-donor').onchange();assert.equal(remembered,1);
await controller.save();
assert.deepEqual(state.transform.gun_swaps[0].pose.position,[2,3,4]);assert.equal(state.transform.gun_swap,undefined);
assert.equal($('gun-donor').value,'','saving must clear the previous donor cannon selection');
$('gun-recipient').value='coax';await $('gun-recipient').onchange();
assert.equal(state.transform.gun_swap.donor_place,'','choosing the coax must not move the previous cannon into it');
$('gun-donor').value='coax';await $('gun-donor').onchange();await controller.save();
assert.equal(state.transform.gun_swaps.length,2);assert.deepEqual(state.transform.gun_swaps[0].pose.position,[2,3,4]);
assert.equal(state.transform.gun_swaps[1].donor_place,'coax');
await controller.restore();assert.ok(controller.active());assert.equal(state.gunPreviewKey,controller.key());
await assert.rejects(controller.select({eid:'0x3',template:'other'},'turret'),/cambios guardados/);
await assert.rejects(controller.select({depot:'x',template:'pak'},'turret'),/torreta del depósito/);
$('gun-saved').value='0';await $('gun-remove-change').onclick();
assert.equal(state.transform.gun_swaps.length,1);
assert.equal($('gun-recipient').options.find(o=>o.value==='main').disabled,false,'removed slots become selectable again');
await $('gun-clear-changes').onclick();
assert.equal(state.transform.gun_swaps.length,0);assert.equal(state.transform.gun_swap,undefined);
assert.equal(calls.at(-1)[0],'/api/gun-preview','clearing changes preserves the mounted actor preview route');
delete state.transform.gun_swaps;delete state.transform.gun_swap;await controller.restore();assert.ok(!controller.active());
state.turret=null;controller.controls();assert.ok($('gun-donor').disabled);
assert.equal($('gun-donor').options[0].value,'','a previous donor weapon cannot remain selected');
assert.equal($('mount-select').disabled,false);
// A turret kept in the depot gives its gun; the row says which part it uses up.
{
  const seen=[],guns=[];
  const stored={tab:'depot',hull:{eid:'0x1',template:'tank'},turret:null,
    campaign:{units:[],depot:[{id:'kept',part:'turret',template:'lvt',label:'Torreta de LVT'}]},
    transform:{hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,0],rotation:[0,0,0]}}};
  const fields=new Map(),$$=id=>{if(!fields.has(id))fields.set(id,{value:'',checked:false,classList:{toggle(){}},add(o){(this.options??=[]).push(o);},replaceChildren(...o){this.options=o;},setAttribute(){}});return fields.get(id);};
  $$('operation-select').value='gun';
  const fake={active:()=>true,draft:()=>({guns}),ensure(){},body:()=>({}),refresh:async()=>{},placeholder(){stored.turret={designPlaceholder:true};}};
  const ask=async(path,body)=>{seen.push([path,body]);return {kind:'vehicle',source:'x',weapons:[{place:'main',weapon:'cannon',available:true}]};};
  const editor=gunEditor({state:stored,$:$$,job:ask,displayPart(){},updateSelection(){},selectEdit(){},remember(){},reportError(error){throw error;},design:fake});
  await editor.select({depot:'kept',part:'turret',template:'lvt',label:'Torreta de LVT',kind:'part'},'turret');
  assert.deepEqual(seen.find(([path])=>path==='/api/gun-catalog')[1],{template:'lvt',module:true},'only the weapons on that turret');
  stored.transform.gun_swap.recipient_place='mg';stored.transform.gun_swap.donor_place='main';
  await editor.save();
  assert.equal(guns[0].donor_depot,'kept');assert.equal(guns[0].donor_template,'lvt');assert.equal(guns[0].donor_eid,undefined);
  $$('gun-saved').value='0';await $$('gun-edit-change').onclick();
  assert.equal(stored.turret.depot,'kept','reopening the change goes back to the same part');assert.equal(stored.turret.part,'turret');
  await assert.rejects(editor.select({depot:'chassis',part:'hull',template:'lvt'},'turret'),/torreta del depósito/,'a chassis gives no gun');
  stored.tab='catalog';
  await assert.rejects(editor.select({depot:'kept',part:'turret',template:'lvt'},'turret'),/torreta del depósito/);
}
// "Choose MG / AT rifle": the donor is an infantry weapon and the recipient gun becomes its slot.
{
  globalThis.document={createElement:()=>({label:'',options:[],append(...o){this.options.push(...o);}})};
  const {setSlot}=await import('../frontend/mounted-weapon-editor.js');
  const guns=[],hidden=new Set(),changes=[];
  const kept={tab:'campaign',hull:{eid:'0x1',template:'tank'},turret:{designPlaceholder:true},transform:{hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,0],rotation:[0,0,0]}}};
  const fields=new Map(),$$=id=>{if(!fields.has(id))fields.set(id,{value:'',checked:false,disabled:false,textContent:'',
    classList:{toggle(name,on){if(on)hidden.add(id);else hidden.delete(id);}},add(o){(this.options??=[]).push(o);},append(...o){(this.options??=[]).push(...o.flatMap(g=>g.options||[g]));},replaceChildren(...o){this.options=o;}});return fields.get(id);};
  $$('operation-select').value='gun';
  const fake={active:()=>true,draft:()=>({guns}),ensure(){},body:()=>({}),refresh:async()=>{},placeholder(){}};
  const tool={slotPlaces:()=>[{place:'mgun',weapon:'browning',mountable:false}],weapons:async()=>[{id:'mg42_belt',label:'MG 42',kind:'mgun'},{id:'boys_mk1',label:'Boys',kind:'at_rifle'}],
    weaponLabel:id=>id==='mg42_belt'?'MG 42':id,placeable:()=>true,restore:async()=>{},placeSlot:place=>changes.push(['place',place]),
    changeSlot:async(place,item)=>{changes.push([place,item]);setSlot(kept.transform,place,item);}};
  const ask=async()=>({kind:'vehicle',source:'x',weapons:[{place:'mgun',weapon:'browning',label:'Browning',available:false}]});
  const editor=gunEditor({state:kept,$:$$,job:ask,displayPart(){},updateSelection(){},selectEdit(){},remember(){},reportError(error){throw error;},design:fake,mounted:()=>tool});
  await editor.restore();
  assert.ok(hidden.has('gun-mountable-field'),'the unit donor list shows first');
  await $$('gun-mountable').onclick();
  assert.ok(hidden.has('gun-donor-field')&&hidden.has('gun-barrel-only-field')&&!hidden.has('gun-mountable-field'));
  assert.deepEqual($$('gun-recipient').options.map(o=>o.value),['mgun'],'only guns that can hold a slot');
  assert.equal($$('gun-recipient').value,'mgun');
  assert.deepEqual($$('gun-mountable-item').options.map(o=>o.value),['','mg42_belt','boys_mk1'],'MGs first, then AT rifles');
  assert.ok($$('gun-save').disabled,'nothing to save before a weapon is chosen');
  $$('gun-mountable-item').value='mg42_belt';$$('gun-mountable-item').onchange();
  assert.ok(!$$('gun-save').disabled);
  await $$('gun-save').onclick();
  assert.deepEqual(kept.transform.mounted_weapons.slots.map(r=>[r.place,r.item]),[['mgun','mg42_belt']]);
  assert.equal(kept.transform.gun_swap,undefined,'no unit donor is involved');
  assert.match($$('gun-saved').options.at(-1).text,/mgun ← MG 42/);assert.equal($$('gun-saved').options.at(-1).value,'slot:mgun');
  await $$('gun-mountable-place').onclick();assert.deepEqual(changes.at(-1),['place','mgun']);
  $$('gun-saved').value='slot:mgun';await $$('gun-remove-change').onclick();
  assert.equal(kept.transform.mounted_weapons,undefined,'removing the change gives the gun back');
  await $$('gun-mountable').onclick();assert.ok(hidden.has('gun-mountable-field'),'back to a unit donor');
}
console.log('Gun editor: operation, owned-unit selection, blocked donors, preview, recipe restore, history and MG / AT rifle donors passed.');
