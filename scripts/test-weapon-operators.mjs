import assert from 'node:assert/strict';
import fs from 'node:fs';
import {weaponOperatorsEditor,operatorOf,setOperator,aimsWeapon,crewLabel,weaponLabel,weaponName,forgetOperator} from '../frontend/weapon-operators.js';
globalThis.localStorage={getItem:()=> 'es'};
globalThis.Option=class {constructor(text,value){this.text=text;this.value=value;}};

// The recipe: {weapon place: crewman}; an empty list leaves no key.
const transform={};
setOperator(transform,'gun','gunner2');assert.deepEqual(transform.weapon_operators,{gun:'gunner2'});
assert.equal(operatorOf(transform,{place:'gun',gunner:'gunner'}),'gunner2');
assert.equal(operatorOf(transform,{place:'mgun',gunner:'gunner2'}),'gunner2');
setOperator(transform,'gun','');assert.equal(transform.weapon_operators,undefined);
const weapons=[{place:'mgun',weapon:'20mm_kwk38',type:'',gunner:'gunner2',bone:'mgun_barrel'},{place:'gun',weapon:'panzershreck_54_x6',type:'',gunner:'gunner'}];
assert.ok(aimsWeapon(weapons,{},'gunner'));
assert.ok(!aimsWeapon(weapons,{weapon_operators:{gun:'gunner2'}},'gunner'),'a seat whose weapon went to another crewman aims nothing');
assert.equal(crewLabel(r=>r.startsWith('gunner')?'Artillero':r,'gunner2'),'Artillero 2');
// Rows show the game's weapon name, never its id (Aurus: "mgun · 20mm_kwk38" read letter by letter).
const kwk={place:'mgun',weapon:'20mm_kwk38',type:'main',names:{default:'2cm KwK 38 L/55',es:'KwK 38 L/55 de 2 cm'}};
assert.equal(weaponName(kwk,'es'),'KwK 38 L/55 de 2 cm');assert.equal(weaponName(kwk,'en'),'2cm KwK 38 L/55');
assert.equal(weaponName({place:'gun',weapon:'weapon panzershreck_54_x6',names:{}},'en'),'panzershreck 54 x6');
assert.equal(weaponLabel(kwk,'es'),'KwK 38 L/55 de 2 cm · Arma principal');
const assigned={weapon_operators:{gun:'gunner',mgun:'gunner2'}};forgetOperator(assigned,'gunner');
assert.deepEqual(assigned.weapon_operators,{mgun:'gunner2'},'someone left in reserve works nothing');

// The editor: one row per weapon; picking a crewman writes the recipe and frees the old seat.
const make=tag=>({tag,children:[],value:'',disabled:false,textContent:'',attributes:{},dataset:{},classList:{hidden:false,toggle(name,on){this.hidden=!!on;}},
  append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},setAttribute(k,v){this.attributes[k]=v;}});
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,make(id));return nodes.get(id);};
const document={createElement:make};
const state={transform:{},busy:false,check:{status:'ok',crew:['driver','gunner','gunner2'],operators:weapons}};
let remembered=0,checks=0;
const editor=weaponOperatorsEditor({state,$,remember:()=>remembered++,refreshCheck:()=>checks++,roleName:r=>r,document});
editor.render();
assert.equal($('weapon-operators-field').classList.hidden,false);
assert.equal($('weapon-operators-list').children.length,2);
assert.equal(editor.aims('gunner'),true);
// Pointing at a row marks its weapon in 3D; its list marks each crewman it offers.
assert.deepEqual(JSON.parse($('weapon-operators-list').children[0].dataset.mark),{weapon:'mgun',bone:'mgun_barrel'});
assert.deepEqual(JSON.parse($('weapon-operators-list').children[1].dataset.mark),{weapon:'gun',bone:null});
assert.equal($('weapon-operators-list').children[0].children[1].dataset.pick,'crew');
const select=$('weapon-operators-list').children[1].children[1];
assert.equal(select.value,'gunner');assert.deepEqual(select.children.map(o=>o.value),['driver','gunner','gunner2']);
select.value='gunner2';select.onchange();
assert.deepEqual(state.transform.weapon_operators,{gun:'gunner2'});assert.equal(remembered,1);assert.equal(checks,1);
assert.equal(editor.aims('gunner'),false,'the Wanze seat can now be left in reserve');
// While the next check runs, the last list stays and cannot be changed.
state.check={status:'pending'};editor.render();
assert.equal($('weapon-operators-list').children[1].children[1].disabled,true);
assert.equal(editor.aims('gunner2'),true);
// Someone left in reserve works nothing.
state.check={status:'ok',crew:['driver','gunner','gunner2'],operators:weapons};state.transform.crew_exclude=['gunner'];editor.render();
assert.deepEqual($('weapon-operators-list').children[0].children[1].children.map(o=>o.value),['driver','gunner2']);

// The field sits after the weapon crew, with every string translated.
const html=fs.readFileSync('frontend/index.html','utf8');
assert.ok(html.indexOf('id="crew-field"')<html.indexOf('id="weapon-operators-field"'));
// Its own list: the crew list's grid squeezed the weapon name into the checkbox column.
assert.ok(html.includes('id="weapon-operators-list" class="operator-list"'));
assert.equal($('weapon-operators-list').children[0].className,'operator-row');
const css=fs.readFileSync('frontend/style.css','utf8');assert.ok(css.includes('.operator-row{display:flex;flex-direction:column'));
// The Crew tool carries no long help paragraphs (Aurus: "quita ese texto").
const crewTool=html.slice(html.indexOf('id="crew-visibility-field"'),html.indexOf('id="weapon-operators-field"')+400);
assert.ok(!/<small>[^<]{120,}<\/small>/.test(crewTool));
const copy=fs.readFileSync('frontend/ui-copy.js','utf8'),ru=fs.readFileSync('frontend/ru.js','utf8');
for(const text of ['Quién maneja cada arma','Lo maneja'])assert.ok(copy.includes(`"${text}"`)&&ru.includes(`"${text}"`),text);
console.log('Weapon operators: recipe, crew choices, freed seats and translations passed.');
