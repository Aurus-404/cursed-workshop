import assert from 'node:assert/strict';
globalThis.localStorage={getItem:()=> 'es'};
class Element{
  children=[];classList={toggle(){}};dataset={};
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];}
  setAttribute(key,value){this[key]=value;}
}
globalThis.document={createElement:()=>new Element()};
const {crewHidden,visibilityKey,visibilityRows,crewVisibilityEditor}=await import('../frontend/crew-visibility.js');
const {translate}=await import('../frontend/i18n.js');
const {selectionData}=await import('../frontend/diagnostics.js');
const state={hull:{template:'tank'},turret:{template:'pak'},mounts:{hull:'@free',turret:'pivot'},transform:{crew_hidden:[]}};
const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
let remembered=0,checked=0,shown=0,detached=0;
const editor=crewVisibilityEditor({state,$,remember:()=>remembered++,refreshCheck:()=>checked++,applyShow:()=>shown++,roleName:r=>r,detachHidden:()=>detached++});
editor.receive([
  {place:'gunner',part:'hull',source_place:'gunner',can_hide:true},
  {place:'gunner2',part:'turret',source_place:'gunner',can_hide:true},
  {place:'driver',part:'hull',source_place:'driver',can_hide:false,native_internal:true},
  {place:'charger',part:'turret',source_place:'charger',can_hide:false},
  {place:'commander',part:'hull',source_place:'commander',can_hide:true},
]);
assert.equal(visibilityRows(state).length,5);
const row=$('crew-visibility-list').children[1],box=row.children[0];
box.checked=true;box.onchange();
assert.deepEqual(state.transform.crew_hidden,['gunner2']);
assert.ok(crewHidden(state,'turret','gunner'));assert.ok(!crewHidden(state,'hull','gunner'));
assert.equal(remembered,1);assert.equal(checked,1);assert.equal(detached,1);
assert.ok($('crew-visibility-list').children[3].children[0].disabled);
assert.ok($('crew-visibility-list').children[4].children[0].disabled);
const recipe=JSON.stringify(state.transform);state.transform={crew_hidden:[]};editor.render();
assert.ok(!crewHidden(state,'turret','gunner'),'undo/unhide restores original representation');
state.transform=JSON.parse(recipe);editor.render();assert.ok(crewHidden(state,'turret','gunner'),'recipe and redo');
state.transform.crew_hidden.push('commander');assert.ok(crewHidden(state,'commander_mg','commander'));
assert.deepEqual(selectionData(state).crew_hidden,['gunner2','commander']);
state.busy=true;editor.render();assert.ok($('crew-visibility-list').children[1].children[0].disabled);
const key=visibilityKey(state);state.turret={template:'other'};
assert.notEqual(visibilityKey(state),key);assert.deepEqual(visibilityRows(state),[]);
assert.ok(!crewHidden(state,'turret','gunner'),'stale role collision mappings cannot hide a different unit');
for(const text of ['Ocultar tripulantes al embarcar','Ocultar al embarcar','Interno','Oculto',
  'No se pudo validar un puesto de tripulación: LinkBone necesita un texto, aunque esté vacío. Se evitó un cierre del juego (String expected!).',
  'Este puesto ya es interno; no tiene un modelo visible.',
  'La lista de tripulantes ocultos no es válida.','El puesto a ocultar no existe en este ensamblaje.']){
  for(const lang of ['en','zh-CN'])assert.notEqual(translate(text,lang),text);
}
state.busy=false;editor.render();$('crew-visibility-list').children[0].onclick();assert.ok(!state.transform.crew_hidden,'reset remains available even with a stale or rejected seat');
assert.ok(shown>=1);

// A design: crew settings name the finished vehicle's places. The compiled chassis
// and a saved turret reopened unchanged use those names; a donor still being
// tried keeps its own, so a name it only shares (charger) hides nobody there.
{const {vehiclePlaceParts}=await import('../frontend/crew-visibility.js');
  const design={hull:{template:'tank'},turret:{template:'tank'},mounts:{},transform:{assembly_draft:{schema:1,turrets:[]},crew_hidden:['charger3']},
    previews:{hull:{vehicle_places:true},turret:{}}};
  const rows=[{place:'charger3',part:'hull',source_place:'charger3',can_hide:true}];
  const own=crewVisibilityEditor({state:design,$,remember(){},refreshCheck(){},applyShow(){},roleName:r=>r,detachHidden(){}});own.receive(rows);
  assert.deepEqual(vehiclePlaceParts(design),['hull']);
  assert.ok(crewHidden(design,'hull','charger3'));assert.ok(!crewHidden(design,'turret','charger3'),'a donor being tried keeps its own seat names');
  design.pendingTurret={kind:'turret',original:'@free',recipient_mount:'@free'};design.previews.turret={vehicle_places:true};
  assert.deepEqual(vehiclePlaceParts(design),['hull','turret']);assert.ok(crewHidden(design,'turret','charger3'),'a reopened saved turret shows its crew by their places');
  assert.deepEqual(JSON.parse($('crew-visibility-list').children.at(-1).dataset.mark),{crew:'charger3'},'pointing at the row marks that crewman');
  assert.equal(vehiclePlaceParts({transform:{}}),null,'a plain combination keeps its own rule');}
console.log('Crew visibility: individual final roles, preview origins, commander, internal/unsupported seats, history/recipe, stale selection, busy state, diagnostics and translations passed.');
