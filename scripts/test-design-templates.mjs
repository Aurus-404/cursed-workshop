import assert from 'node:assert/strict';
import {templateTransform} from '../frontend/design-templates.js';
import {translate} from '../frontend/i18n.js';

// A Pz IV with armor, a commander MG and a Pz III gun: the same design goes on
// another Pz IV, taking another Pz III of the campaign as donor.
const template={name:'Pz IV blindado',chassis:'panzer4h',label:'Pz.Kpfw IV Ausf.H',transform:{
  hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[1,0,0],rotation:[0,0,0]},
  extra_armor:[{asset:'panzer4h#shieldl@body',parent:'body',position:[1,2,3],rotation:[0,0,90]}],
  commander_mg:{support:'s',item:'mg34',parent:'turret'},mobility:{speed:44},joint_limits:{hull:{turret:[-90,90,12]}},
  assembly_draft:{schema:1,seed:{recipient:'old'},turrets:[{native:true,recipient_mount:'turret'}],
    guns:[{donor_template:'panzer3e',donor_eid:'7',recipient_place:'gun',donor_place:'gun'},
          {donor_template:'panzer3e',donor_eid:'7',recipient_place:'mgun',donor_place:'mgun2'},
          {donor_template:'opel',donor_catalog:true,recipient_place:'x'}]}}};
const campaign={units:[
  {eid:'1',template:'panzer4h',kind:'vehicle'},{eid:'2',template:'panzer4h',kind:'vehicle'},
  {eid:'9',template:'panzer3e',kind:'vehicle'},{eid:'10',template:'panzer3e',kind:'vehicle'},
  {eid:'h',template:'panzer3e',kind:'human'}],depot:[]};
const hull=campaign.units[1];
const result=templateTransform(template,{hull,campaign});
assert.deepEqual(result.missing,[]);
const guns=result.transform.assembly_draft.guns;
assert.equal(guns[0].donor_eid,'9','a Pz III of this campaign is the donor');
assert.equal(guns[1].donor_eid,'9','rows that shared one donor share it again, consumed once');
assert.equal(guns[2].donor_eid,undefined,'catalog parts need no unit');
assert.deepEqual(result.donors,['9']);
assert.deepEqual(result.transform.extra_armor,template.transform.extra_armor,'armor, MG, mobility and limits copy exactly');
assert.deepEqual(result.transform.commander_mg,template.transform.commander_mg);
assert.deepEqual(result.transform.joint_limits,template.transform.joint_limits);
assert.equal(result.transform.assembly_draft.seed,undefined,'never the recipe of the vehicle the template came from');
assert.deepEqual(result.transform.turret,{position:[0,0,0],rotation:[0,0,0]});
assert.equal(template.transform.assembly_draft.guns[0].donor_eid,'7','the saved template is untouched');
assert.deepEqual(templateTransform(template,{hull,campaign,seed:{recipient:'own'}}).transform.assembly_draft.seed,{recipient:'own'});
// The chassis itself is never its own donor, and a missing donor is named.
const poor={units:[campaign.units[1],{eid:'3',template:'panzer3e',kind:'vehicle',stored:true}],depot:[]};
assert.deepEqual(templateTransform(template,{hull,campaign:poor}).missing,[{template:'panzer3e',count:1}]);
// Two different donors of the same model need two units.
const two=structuredClone(template);two.transform.assembly_draft.guns[1].donor_eid='8';
assert.deepEqual(templateTransform(two,{hull,campaign}).donors,['9','10']);
assert.deepEqual(templateTransform(two,{hull,campaign:{units:campaign.units.slice(0,3)}}).missing,[{template:'panzer3e',count:1}]);
// A depot turret is matched by model, else a campaign unit stands in.
const depot=structuredClone(template);depot.transform.assembly_draft.guns=[{donor_template:'panzer3e',donor_depot:'old',donor_mount:'turret'}];
assert.equal(templateTransform(depot,{hull,campaign:{...campaign,depot:[{id:'d1',template:'panzer3e',part:'turret',mount:'turret'}]}}).transform.assembly_draft.guns[0].donor_depot,'d1');
assert.equal(templateTransform(depot,{hull,campaign}).transform.assembly_draft.guns[0].donor_eid,'9');
assert.throws(()=>templateTransform(template,{hull:{eid:'b',template:'bren'},campaign}),/otro modelo/);
assert.throws(()=>templateTransform(template,{hull:{template:'panzer4h'},campaign}),/partida/);
// A vehicle built with Edit (report: Pz IV D with skirts) keeps a plain recipe.
const {designFromRecipe,recipeFromDesign}=await import('../frontend/design-templates.js');
const built={hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,2],rotation:[0,0,0]},recipient_mount:'turret',donor_mount:'turret',
  extra_armor:[{asset:'panzer4h#shieldl@body',parent:'body',position:[1,2,3]}],joint_limits:{hull:{gun_rot:[-8,20]},turret:{turret:[-120,120,14]}},
  crew_seats:{driver:{position:[1,0,0]}},crew_exclude:[],crew_hidden:['loader'],crew_static:true,source_recipe:{x:1}};
const asDesign=designFromRecipe(built,{recipient:'panzer4d',donor:'panzer4d'});
assert.deepEqual(asDesign.assembly_draft.turrets,[{native:true,recipient_mount:'turret',donor_template:'panzer4d',pose:built.turret,joint_limits:built.joint_limits.turret}],'its own moved turret, no donor');
assert.deepEqual(asDesign.joint_limits,{hull:built.joint_limits.hull});
assert.deepEqual(asDesign.extra_armor,built.extra_armor);assert.deepEqual(asDesign.crew_hidden,['loader']);
assert.equal(asDesign.recipient_mount,undefined);assert.equal(asDesign.source_recipe,undefined,'the history of the built vehicle stays out');
const plain=designFromRecipe({...built,turret:{position:[0,0,0],rotation:[0,0,0]},joint_limits:{}},{recipient:'panzer4d',donor:'panzer4d'});
assert.deepEqual(plain.assembly_draft.turrets,[],'an unmoved own turret is no change');
// The same template on another Pz IV D: as a new design, or opened with Edit.
const fresh={...campaign,units:[...campaign.units,{eid:'d1',template:'panzer4d',kind:'vehicle'},{eid:'d2',template:'panzer4d',kind:'vehicle'}]};
const saved={name:'Pz4 sg armored',chassis:'panzer4d',transform:asDesign};
assert.deepEqual(templateTransform(saved,{hull:fresh.units.at(-1),campaign:fresh}).missing,[]);
const back=recipeFromDesign(asDesign,{recipient_mount:'turret',donor_mount:'turret'});
assert.deepEqual(back.turret,built.turret);assert.deepEqual(back.joint_limits,{hull:built.joint_limits.hull,turret:built.joint_limits.turret});
assert.deepEqual(back.extra_armor,built.extra_armor);assert.equal(back.assembly_draft,undefined);
assert.throws(()=>recipeFromDesign(template.transform,{recipient_mount:'turret'}),/CHASIS/,'a template with donors needs the design tools');
// A combination with another vehicle's turret keeps that donor as a requirement.
const swap=designFromRecipe({...built,joint_limits:{}},{recipient:'panzer4d',donor:'panzer3e'});
assert.equal(swap.assembly_draft.turrets[0].donor_template,'panzer3e');
assert.equal(templateTransform({chassis:'panzer4d',transform:swap},{hull:fresh.units.at(-1),campaign:fresh}).transform.assembly_draft.turrets[0].donor_eid,'9');
assert.throws(()=>designFromRecipe({gun_swap:{}},{recipient:'a',donor:'a'}),/cañón/);
assert.throws(()=>designFromRecipe({assembly_draft:{seed:{}}},{}),/CHASIS/);
for(const locale of ['en','zh-CN'])for(const text of ['Plantillas de diseño','Aplicar plantilla','Guardar plantilla','Para aplicar esta plantilla faltan estos vehículos donantes en tu partida:'])
  assert.notEqual(translate(text,locale),text,text+' '+locale);
console.log('Design templates: exact copy, own donors, shared donors consumed once, missing donors, depot pieces, chassis model and translations passed.');
