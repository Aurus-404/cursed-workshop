import assert from 'node:assert/strict';
// The one rule of the 3D view's marks: whatever a menu names (a part, a weapon,
// a crewman) is marked while pointed at or reached with the keyboard.
globalThis.localStorage={getItem:()=>null,setItem(){}};
globalThis.document={addEventListener(){}};globalThis.addEventListener=()=>{};
class Node{
  constructor(tag,{mark,pick,parent,focusVisible=false}={}){this.tagName=tag.toUpperCase();this.dataset={};this.parentElement=parent||null;this.focusVisible=focusVisible;
    if(mark)this.dataset.mark=JSON.stringify(mark);if(pick)this.dataset.pick=pick;}
  closest(selector){for(let n=this;n;n=n.parentElement){
    if(selector==='[data-mark]'&&n.dataset.mark!==undefined)return n;
    if(selector==='select'&&n.tagName==='SELECT')return n;}return null;}
  matches(selector){return selector===':focus-visible'&&this.focusVisible;}
}
const {hoverMarks,readMark}=await import('../frontend/hover-marks.js');
const handlers={},doc={addEventListener:(type,fn)=>{(handlers[type]??=[]).push(fn);}};
const fire=(type,event)=>{for(const fn of handlers[type]||[])fn(event);};
const shown=[];const marks=hoverMarks({document:doc,show:mark=>shown.push(mark)});
const last=()=>shown.at(-1);

// Who works each weapon: the row is the weapon, its list the crewman.
const row=new Node('div',{mark:{weapon:'gun1',bone:'gun1'}}),label=new Node('label',{parent:row});
const select=new Node('select',{parent:row,pick:'crew'});
fire('mouseover',{target:label});assert.deepEqual(last(),{weapon:'gun1',bone:'gun1'});
fire('part-hover',{target:select,detail:{value:'gunner3'}});assert.deepEqual(last(),{select,value:'gunner3'});
fire('mouseover',{target:label});assert.deepEqual(last(),{select,value:'gunner3'},'an open list stays stronger than the row under it');
fire('part-hover',{target:select,detail:{value:null}});assert.deepEqual(last(),{weapon:'gun1',bone:'gun1'},'leaving the field marks the row again');
// Out of every menu: nothing.
fire('mouseover',{target:new Node('canvas')});assert.equal(last(),null);
const count=shown.length;fire('mouseover',{target:new Node('div')});assert.equal(shown.length,count,'no repeated clears');
// A visible list's rows (placed armour, seats...): the option under the pointer.
const list=new Node('select',{pick:'fitting:extra_armor'}),option=new Node('option',{parent:list});option.value='2';
fire('mouseover',{target:option});assert.deepEqual(last(),{select:list,value:'2'});
const plain=new Node('select'),loose=new Node('option',{parent:plain});loose.value='x';
fire('mouseover',{target:loose});assert.equal(last(),null,'a list that names nothing in the vehicle marks nothing');
// Keyboard: a marked row reached with Tab; a click leaves no mark behind.
const crewRow=new Node('div',{mark:{crew:'charger3',part:'hull'}});
fire('focusin',{target:new Node('input',{parent:crewRow,focusVisible:true})});assert.deepEqual(last(),{crew:'charger3',part:'hull'});
fire('focusin',{target:new Node('input',{parent:crewRow,focusVisible:false})});assert.equal(last(),null);
// A rebuilt view (new preview) marks the same thing again.
fire('mouseover',{target:label});const before=shown.length;marks.refresh();
assert.equal(shown.length,before+1);assert.deepEqual(last(),{weapon:'gun1',bone:'gun1'});
fire('mouseout',{relatedTarget:null});assert.equal(last(),null,'the pointer left the window');
assert.equal(readMark(new Node('div')),null);
console.log('Hover marks: lists over rows, rows again after a field, visible lists, keyboard only focus, refresh and exits passed.');
