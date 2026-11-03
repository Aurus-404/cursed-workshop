import assert from 'node:assert/strict';
import {mobilityEditor} from '../frontend/mobility-editor.js';
import {translate} from '../frontend/i18n.js';

const elements=new Map();
const $=id=>{if(!elements.has(id)){const label={classes:new Set(),classList:{toggle(name,on){on?label.classes.add(name):label.classes.delete(name);}}};
  elements.set(id,{value:'',disabled:false,textContent:'',label,closest(){return this.label;},
    classes:new Set(),classList:{toggle(name,on){const set=elements.get(id).classes;on?set.add(name):set.delete(name);}}});}return elements.get(id);};
let remembered=0,checks=0;
const state={previews:{},transform:{}};
const editor=mobilityEditor({state,$,remember(){remembered++;},refreshCheck(){checks++;}});

editor.refresh();
assert.ok($('mobility-field').classes.has('hidden'),'hidden until a chassis with a mobility macro is loaded');
state.previews.hull={mobility:{macro:'mobility_tank',values:{speed:46,reverse:6,traverse:24,power:700,track:5,fuel:720,range:200}}};
editor.refresh();
assert.ok(!$('mobility-field').classes.has('hidden'));
assert.equal($('mobility-speed').value,'46','native value shown');
assert.ok($('mobility-speedatmaxturn').label.classes.has('hidden'),'a tracked chassis has no tight-turn speed');
assert.ok(!$('mobility-traverse').label.classes.has('hidden'));

$('mobility-speed').value='60';$('mobility-speed').onchange();
assert.deepEqual(state.transform.mobility,{speed:60});assert.equal(checks,1);assert.equal(remembered,1);
$('mobility-traverse').value='40';$('mobility-traverse').onchange();
assert.deepEqual(state.transform.mobility,{speed:60,traverse:40});
$('mobility-speed').value='46';$('mobility-speed').onchange();
assert.deepEqual(state.transform.mobility,{traverse:40},'back to the native value saves nothing for it');
$('mobility-power').value='99999';$('mobility-power').onchange();
assert.deepEqual(state.transform.mobility,{traverse:40},'out of range is refused');
assert.match($('mobility-message').textContent,/1.*5000/);assert.equal($('mobility-power').value,'700');
$('mobility-reset').onclick();assert.equal(state.transform.mobility,undefined,'reset restores every native value');
assert.equal($('mobility-traverse').value,'24');

state.previews.hull={mobility:{macro:'mobility_car',values:{speed:60,speedatmaxturn:18,reverse:15,power:100}}};editor.refresh();
assert.ok($('mobility-traverse').label.classes.has('hidden'),'a wheeled chassis has no pivot turn');
assert.ok(!$('mobility-speedatmaxturn').label.classes.has('hidden'));
for(const locale of ['en','zh-CN'])for(const text of ['Movilidad del chasis','Velocidad máxima (km/h)','Restablecer movilidad nativa'])
  assert.notEqual(translate(text,locale),text,text+' '+locale);
console.log('Mobility editor: native values, only changed values saved, ranges, reset, tracked and wheeled chassis, translations passed.');
