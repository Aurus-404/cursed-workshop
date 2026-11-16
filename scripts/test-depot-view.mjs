import assert from 'node:assert/strict';
import {depotRows,matchesDepot,depotFilters,DEPOT_TYPES} from '../frontend/depot-view.js';
globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
const stock=[{id:'a',part:'hull',template:'hull',nation:'eng',origin:'Base'},
  {id:'b',part:'turret',template:'turret',nation:'ger',origin:'MACE',mount:'aux'},
  {id:'c',part:'gun',template:'gun',nation:'ger',origin:'MACE',place:'gun2'},
  {id:'d',part:'turret',template:'old'}];
const rows=depotRows(stock);assert.equal(rows[2].place,'gun2');assert.equal(rows[2].depot,'c');
assert.deepEqual(rows.filter(r=>matchesDepot(r,{type:'gun',nation:'ger',origin:'MACE'})).map(r=>r.depot),['c']);
assert.deepEqual(rows.filter(r=>matchesDepot(r,{nation:'unknown'})).map(r=>r.depot),['d']);
assert.equal(new Set(Object.values(DEPOT_TYPES)).size,4);
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{value:'',classList:{toggle(){}},replaceChildren(...options){this.options=options;}});return elements.get(id);};
depotFilters($,stock,true);$('depot-type').value='gun';$('depot-nation').value='ger';depotFilters($,stock,true);
assert.equal($('depot-type').value,'gun');assert.equal($('depot-nation').value,'ger');
assert.deepEqual($('depot-type').options.map(o=>o.value),['','gun','hull','turret']);
depotFilters($,stock.filter(r=>r.part!=='gun'),true);assert.equal($('depot-type').value,'');
console.log('Depot view: distinct component types, standalone gun identity, combined type/nation/source filters, unknown legacy provenance and stable/reset selections passed.');
