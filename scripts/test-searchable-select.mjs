import assert from 'node:assert/strict';
class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.style={};this.attributes={};this.handlers={};this.value='';this.textContent='';this.scrollTop=0;const flags=new Set();this.classList={add:v=>flags.add(v),remove:v=>flags.delete(v),contains:v=>flags.has(v),toggle(v,on){on??=!flags.has(v);on?flags.add(v):flags.delete(v);return on;}};}
  append(...children){this.children.push(...children);for(const child of children)child.parentElement=this;}
  replaceChildren(...children){this.children=[];this.append(...children);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  removeAttribute(key){delete this.attributes[key];}
  getBoundingClientRect(){return {left:10,right:260,top:10,bottom:40,width:250,height:30};}
  addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);}
  dispatchEvent(event){for(const fn of this.handlers[event.type]||[])fn(event);return true;}
  after(node){document.body.append(node);}
  focus(){this.focused=true;}select(){}contains(node){return node===this||this.children.some(c=>c.contains?.(node));}
  querySelector(){return null;}
}
class Select extends Element{
  constructor(){super('select');delete this.value;this._index=0;this.options=[];this.labels=[];this.id='gun-recipient';}
  get value(){return this.options?.[this._index]?.value||'';}
  set value(value){this._index=this.options?.findIndex(o=>o.value===value)??-1;}
  get selectedIndex(){return this._index;}
  set selectedIndex(value){this._index=value;}
}
globalThis.HTMLSelectElement=Select;
globalThis.document={body:new Element('body'),createElement:tag=>new Element(tag),addEventListener(){}};
globalThis.window={addEventListener(){}};globalThis.innerHeight=800;globalThis.innerWidth=1200;
globalThis.addEventListener=()=>{};
globalThis.MutationObserver=class{observe(){}};
const {searchableSelect}=await import('../frontend/searchable-select.js');
const select=new Select();
select.options=[{text:'Choose a weapon',value:''},{text:'Blocked gun',value:'blocked',disabled:true,title:'No detachable elevation joint'},{text:'Main gun',value:'main'}];
for(const option of select.options)Object.defineProperty(option,'selected',{get:()=>select.value===option.value});
let changes=0;select.addEventListener('change',()=>changes++);
const widget=searchableSelect(select);widget.show();
const pop=document.body.children.find(c=>c.children.some(n=>n.tagName==='UL'));
const list=pop.children.find(n=>n.tagName==='UL');
const blocked=list.children.find(n=>n.getAttribute('aria-disabled')==='true');
assert.ok(blocked,'unavailable choices remain visible');assert.equal(blocked.children[1].textContent,'No detachable elevation joint');
blocked.onclick();assert.equal(select.value,'');assert.equal(changes,0,'disabled mouse selection cannot dispatch a change');
const search=pop.children[0].children[0];
search.value='Blocked';search.oninput();assert.equal(list.children.length,1);
search.onkeydown({key:'Enter',preventDefault(){}});assert.equal(changes,0,'disabled keyboard selection cannot dispatch a change');
search.value='Main';search.oninput();list.children[0].onclick();assert.equal(select.value,'main');assert.equal(changes,1);
console.log('Searchable selects: blocked choices/reasons visible, disabled mouse/keyboard guarded, enabled choices selectable.');
