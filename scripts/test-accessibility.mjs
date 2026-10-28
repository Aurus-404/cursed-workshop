import assert from 'node:assert/strict';
import fs from 'node:fs';
globalThis.localStorage={getItem:()=> 'es'};
const {EN,translate,englishText}=await import('../frontend/i18n.js');
const {ZH}=await import('../frontend/zh-cn.js');
const {EXTRA_ZH}=await import('../frontend/ui-copy.js');
const {SMOKE_ZH}=await import('../frontend/smoke-copy.js');
const {RIDER_ZH}=await import('../frontend/rider-copy.js');
const {PASSENGER_ZH}=await import('../frontend/passenger-copy.js');
const {THEMES,preferences,readPreferences}=await import('../frontend/preferences.js');
const {accessibility,isTextEntry}=await import('../frontend/accessibility.js');
for(const key of Object.keys(EN))assert.ok(ZH[key]||EXTRA_ZH[key]||SMOKE_ZH[key]||RIDER_ZH[key]||PASSENGER_ZH[key],`Missing Chinese: ${key}`);
const html=fs.readFileSync('frontend/index.html','utf8');
const strings=[...html.matchAll(/>([^<>]+)</g),...html.matchAll(/(?:title|placeholder|aria-label)="([^"]+)"/g)].map(m=>m[1].trim()).filter(s=>s&&!s.startsWith('import '));
const unchanged=new Set(['3D','X','Y','Z','v','CURSED WORKSHOP','CW','Cursed Workshop','English','Español','F','L','S','XYZ','Normal']);
for(const value of strings)if(/[A-Za-zÁÉÍÓÚáéíóúñ]/.test(value)&&!unchanged.has(value)){
  for(const language of ['en','zh-CN'])assert.notEqual(translate(value,language),value,`${language} static string missing: ${value}`);
}
for(const [source,en,zh] of [
  ['Tiempo total: 05:30','Total time: 05:30','总用时：05:30'],
  ['15 vehículos · 59 soldados · ger','15 vehicles · 59 soldiers · ger','15 辆载具 · 59 名士兵 · ger'],
  ['Vehículo de tu partida · 5 tripulantes','Vehicle from your save · 5 crew','存档中的载具 · 5 名乘员'],
]){assert.equal(translate(source,'en'),en);assert.equal(translate(source,'zh-CN'),zh);}
const source='Este ensamblaje no tiene un puesto de comandante. No se agregan tripulantes.';
assert.equal(englishText(translate(source,'zh-CN')),EN[source],'diagnostics remain English in Chinese UI');
assert.equal(translate('panzer3e / cmdmg_yaw / MACE','zh-CN'),'panzer3e / cmdmg_yaw / MACE');
assert.equal(translate('Escribe este nombre','es'),'Escribe este nombre');
assert.match(translate('Detalle técnico: GunAssembly: El arma no tiene un enlace de modelo inequívoco.','en'),/^Technical detail: GunAssembly: weapon has no unambiguous model link\.$/);
assert.notEqual(translate('Detalle técnico: GunAssembly: El arma no tiene un enlace de modelo inequívoco.','zh-CN'),'Detalle técnico: GunAssembly: El arma no tiene un enlace de modelo inequívoco.');

function luminance(hex){const c=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return .2126*c[0]+.7152*c[1]+.0722*c[2];}
function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
for(const [id,theme] of Object.entries(THEMES)){
  for(const foreground of ['text','muted','accent'])for(const background of ['bg','panel','input','control','selected'])assert.ok(contrast(theme[foreground],theme[background])>=4.5,`${id} ${foreground}/${background}: ${contrast(theme[foreground],theme[background])}`);
  assert.ok(contrast(theme.dark?'#171717':'#ffffff',theme.accent)>=4.5,`${id} button text`);
}
assert.equal(readPreferences({getItem:()=>'{invalid'}).theme,'graphite');
assert.equal(readPreferences({getItem:()=>'{"theme":"__proto__","size":"999"}'}).theme,'graphite');
assert.equal(readPreferences({getItem(){throw Error('unavailable');}}).help,true);

class Node{
  constructor(id='',tag='DIV'){this.id=id;this.tagName=tag;this.nodeType=1;this.dataset={};this.attributes=new Map();this.options=[];this.children=[];this.textContent='';this.isConnected=true;this.style={setProperty:(key,value)=>this.style[key]=value};this.classes=new Set();this.classList={add:v=>this.classes.add(v),remove:v=>this.classes.delete(v),contains:v=>this.classes.has(v)};}
  setAttribute(k,v){this.attributes.set(k,String(v));}getAttribute(k){return this.attributes.get(k)||null;}removeAttribute(k){this.attributes.delete(k);}
  querySelectorAll(){return [];}querySelector(){return null;}add(option){this.options.push(option);}append(node){this.children.push(node);}
  matches(selector){return selector==='.unit-card'&&this.card;}
  closest(selector){return selector==='[data-help],[title]'&&(this.dataset.help||this.getAttribute('title'))?this:null;}
  contains(node){return node===this;}getBoundingClientRect(){return {left:480,top:100,bottom:120,width:280,height:50};}
  focus(){this.focused=true;}select(){this.selected=true;}showModal(){this.open=true;}
}
const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,new Node(id));return nodes.get(id);};
const events={};const doc={documentElement:new Node(),body:new Node('body'),getElementById:$,querySelector:()=>null,createElement:()=>new Node(),addEventListener:(name,fn)=>{(events[name]??=[]).push(fn);}};
globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
let saved='',changes=0;
preferences({document:doc,storage:{getItem:()=>null,setItem:(key,value)=>{saved=value;}},onTheme:()=>changes++});
assert.equal(doc.documentElement.dataset.theme,'graphite');
$('theme-select').value='paper';$('theme-select').onchange();assert.equal(doc.documentElement.style.colorScheme,'light');assert.equal(JSON.parse(saved).theme,'paper');
$('text-size').value='large';$('text-size').onchange();assert.equal(doc.documentElement.dataset.textSize,'large');
$('reduce-motion').checked=true;$('reduce-motion').onchange();assert.equal(doc.documentElement.dataset.reduceMotion,'true');
$('appearance-reset').onclick();assert.equal(doc.documentElement.dataset.theme,'graphite');assert.equal(doc.documentElement.dataset.textSize,'normal');assert.ok(changes>=5);

globalThis.MutationObserver=class{constructor(fn){this.callback=fn;}observe(){}};
const a11y=accessibility({document:doc,window:{innerWidth:600,innerHeight:500}});
const control=new Node('control');control.dataset.help='Copia información de esta sesión como texto, sin adjuntar tu partida.';control.setAttribute('aria-describedby','existing-description');
events.focusin[0]({target:control});const bubble=doc.body.children.at(-1);
assert.ok(!bubble.classList.contains('hidden'));assert.match(control.getAttribute('aria-describedby'),/existing-description control-help/);
assert.ok(parseFloat(bubble.style.left)>=8&&parseFloat(bubble.style.left)<=312,'tooltip remains within viewport');
a11y.hide();assert.equal(control.getAttribute('aria-describedby'),'existing-description');
doc.body.dataset.helpEnabled='false';events.focusin[0]({target:control});assert.ok(bubble.classList.contains('hidden'));
let prevented=false;events.keydown[0]({key:'f',ctrlKey:true,target:new Node(),preventDefault(){prevented=true;}});assert.ok(prevented&&$('unit-search').focused&&$('unit-search').selected);
events.keydown[0]({key:'?',target:new Node(),preventDefault(){}});assert.ok($('help-dialog').open);
assert.ok(isTextEntry({tagName:'TEXTAREA'}));assert.ok(isTextEntry({tagName:'INPUT',type:'number'}));assert.ok(isTextEntry({isContentEditable:true}));assert.ok(!isTextEntry({tagName:'BUTTON'}));
console.log(`Accessibility: ${Object.keys(EN).length} Chinese keys, static ES/EN/Chinese coverage, dynamic strings, English diagnostics, theme contrast/preferences, tooltips and keyboard passed.`);

doc.body.dataset.help='true';doc.body.dataset.helpEnabled='true';events.focusin[0]({target:doc.body});assert.ok(bubble.classList.contains('hidden'),'body preference is never a tooltip');
