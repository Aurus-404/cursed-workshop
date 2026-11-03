import assert from 'node:assert/strict';
globalThis.localStorage={getItem:()=> 'es'};
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.value='';this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  addEventListener(event,fn){this.listeners[event]=fn;}
  showModal(){this.open=true;}
  close(){this.open=false;this.listeners.close?.();}
  focus(){this.focused=true;}
}
globalThis.document={documentElement:{lang:'es'},createElement:tag=>new Element(tag)};
const {shopEditor}=await import('../frontend/shop-editor.js');
const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);};
const calls=[];let refreshes=0;
const vehicles=[{id:'mw_a',name:'A',on:true,cost:400,suggested_cost:400,parts:['native']},
  {id:'mw_b',name:'B',on:false,cost:0,suggested_cost:0,parts:['unpriced']}];
const editor=shopEditor({state:{busy:false},$,api:async()=>({vehicles}),
  operation:async(path,body)=>{calls.push({path,body});return {offers:{mw_a:{cost:body.changes.mw_a?.cost??400},mw_b:{cost:0}},reload_steps:[]};},
  artwork:{clear(){},attach(){}},svgVehicle:'',reportError:error=>{throw error;},refreshConfig:async()=>refreshes++,reloadText:()=>''});
await editor.open();
let rows=$('shop-list').children;const price=row=>row.children[3].children[1];
assert.equal($('shop-apply').disabled,true);
price(rows[0]).value='79';price(rows[0]).oninput();
assert.equal($('shop-apply').disabled,false);
await $('shop-apply').onclick();
assert.deepEqual(calls.pop(),{path:'/api/shop',body:{changes:{mw_a:{on:true,cost:79}}}});
assert.equal(refreshes,1);
await editor.open();rows=$('shop-list').children;
price(rows[0]).value='-1';price(rows[0]).oninput();await $('shop-apply').onclick();
assert.equal(calls.length,0);assert.equal(price(rows[0]).focused,true);
rows[0].children[0].checked=false;rows[0].children[0].onchange();
await $('shop-apply').onclick();
assert.deepEqual(calls.pop().body.changes,{mw_a:{on:false,cost:null}});
await editor.open();rows=$('shop-list').children;
rows[1].children[0].checked=true;rows[1].children[0].onchange();await $('shop-apply').onclick();
assert.deepEqual(calls.pop().body.changes,{mw_b:{on:true,cost:null}});
vehicles[0].cost=79;await editor.open();rows=$('shop-list').children;
rows[0].children[4].onclick();await $('shop-apply').onclick();
assert.deepEqual(calls.pop().body.changes,{mw_a:{on:true,cost:null}});
assert.ok(!$('error-message').textContent.includes('etapa'));
vehicles[0].needs_update=true;vehicles[0].cost=400;await editor.open();
assert.equal($('shop-apply').disabled,false,'existing enabled offers can be upgraded without toggling them');
await $('shop-apply').onclick();
assert.deepEqual(calls.pop().body.changes,{mw_a:{on:true,cost:null}});
console.log('Shop: price-only edits, zero/unpriced vehicles, removal, invalid values and restore-suggested passed.');
