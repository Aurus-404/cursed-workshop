import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from 'three';
import {TransformControls} from 'three/addons/controls/TransformControls.js';
import {extraCrewEditor,addExtraCrew,removeExtraCrew,setReloadBonus,extraCrewParent,reloadFactor} from '../frontend/extra-crew-editor.js';
globalThis.localStorage={getItem:()=> 'es'};
globalThis.Option=class {constructor(text,value){this.text=text;this.value=value;}};

// The recipe: rows on the part being edited, a reload bonus clamped like the compiler's.
const transform={};
assert.equal(addExtraCrew(transform,{role:'charger',parent:'turret',position:[0,0,0],rotation:[0,0,0]}),0);
assert.equal(transform.extra_crew.reload_bonus,15);
assert.ok(setReloadBonus(transform,80));assert.equal(transform.extra_crew.reload_bonus,50);
assert.ok(removeExtraCrew(transform,0));assert.equal(transform.extra_crew,undefined,'an empty list leaves no recipe key');
const hosts=[{name:'basis'},{name:'body'},{name:'turret'}];
assert.equal(extraCrewParent(hosts,'turret'),'turret');
assert.equal(extraCrewParent(hosts,'@hull'),'body');
assert.equal(extraCrewParent(hosts,'gone'),'body');
assert.equal(reloadFactor(20,2),.6);assert.equal(reloadFactor(30,5),.25);

// The editor: Add puts a crewman on the edited part and the gizmo on him; moving him writes the row.
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',options:[],disabled:false,textContent:'',classList:{toggle(){}},
  replaceChildren(...options){this.options=options;this.value=options[0]?.value||'';},add(o){this.options.push(o);}});return nodes.get(id);};
const camera=new THREE.PerspectiveCamera(),control=new TransformControls(camera),scene=new THREE.Group();
const state={hull:{},turret:{},transform:{},busy:false};let remembered=0,checks=0;
const identity=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]];
const editor=extraCrewEditor({state,$,scene,control,remember:()=>remembered++,refreshCheck:()=>checks++,applyShow:()=>{},
  makeSeat:()=>new THREE.Group(),part:()=>'turret',reference:()=>new THREE.Vector3(5,0,10)});
editor.receiveCheck({armor_hosts:[{name:'body',matrix:identity},{name:'turret',matrix:[[1,0,0,0],[0,1,0,0],[0,0,1,10],[0,0,0,1]]}]});
$('extra-crew-role').value='charger';$('extra-crew-add').onclick();
assert.deepEqual(state.transform.extra_crew.added,[{role:'charger',parent:'turret',position:[5,12,0],rotation:[0,0,0]}]);
assert.ok(editor.placing(),'the new crewman is ready to place');assert.equal(remembered,1);assert.ok(checks>=1);
editor.target().position.set(1,2,3);assert.ok(editor.capture());
assert.deepEqual(state.transform.extra_crew.added[0].position,[1,2,3]);
assert.equal($('extra-crew-list').options.length,1);
$('extra-crew-remove').onclick();assert.equal(state.transform.extra_crew,undefined);assert.equal(editor.placing(),false);

// The panel sits in the crew tool, above the weapon crew, with every string translated.
const html=fs.readFileSync('frontend/index.html','utf8');
assert.ok(html.indexOf('id="extra-crew-field"')<html.indexOf('id="crew-field"'));
const copy=fs.readFileSync('frontend/ui-copy.js','utf8'),ru=fs.readFileSync('frontend/ru.js','utf8');
for(const text of ['Tripulantes extra','Agregar tripulante','Quitar tripulante','Recarga más rápida por cargador extra (%)','Recarga del arma','Fijar tripulante al vehículo'])
  assert.ok(copy.includes(`"${text}"`)&&ru.includes(`"${text}"`),text);
console.log('Extra crew: recipe rows, placement on the edited part, reload bonus and translations passed.');
