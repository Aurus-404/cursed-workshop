import assert from 'node:assert/strict';
import fs from 'node:fs';
import {INSPECTOR_TOOLS,inspectorNavigation,inspectorMenus} from '../frontend/inspector.js';
import {editorHistory} from '../frontend/editor-history.js';

let busy=false,support=false,ready=true;
const events=[],recipe={commander_mg:{item:'base_weapon',position:[1,2,3]},smoke_launchers:[{tubes:3}]};
const nav=inspectorNavigation({busy:()=>busy,support:()=>support,ready:()=>ready,
  onEnter:(tool,part)=>events.push(['enter',tool,part]),onLeave:tool=>events.push(['leave',tool])});
assert.equal(nav.open('commander'),false,'commander is reached through Turret');
assert.ok(nav.select('turret'));assert.ok(nav.open('commander'));
assert.equal(nav.part(),'commander_mg');assert.ok(nav.pose());
assert.equal(nav.select('hull'),false);assert.equal(nav.open('smoke'),false);
assert.equal(nav.accepts('smoke_launchers'),false,'late callbacks cannot select another tool');
busy=true;assert.equal(nav.finish(),false);busy=false;
const before=structuredClone(recipe);assert.ok(nav.finish());assert.deepEqual(recipe,before);
assert.deepEqual(events,[['enter','commander','commander_mg'],['leave','commander']]);
assert.ok(nav.open('joints'));assert.equal(nav.pose(),false);assert.ok(nav.accepts('turret'));
nav.reset();assert.equal(nav.tool,null);
ready=false;assert.equal(nav.open('position'),false);ready=true;
support=true;assert.equal(nav.open('commander'),false);assert.ok(nav.select('hull'));
assert.ok(nav.open('cargo'));assert.equal(nav.part(),'turret');nav.finish();
assert.ok(nav.open('crew'));assert.equal(nav.part(),'turret');nav.finish();
for(const tool of ['armor','smoke']){assert.ok(nav.open(tool));assert.equal(nav.open('position'),false);nav.finish();}
const gunNav=inspectorNavigation({gun:()=>true});
assert.ok(gunNav.open('position'));assert.equal(gunNav.part(),'turret','gun placement always targets the new gun, including from Chassis');

// Exercise the actual DOM controller, including reparenting and disabled controls.
class Node{
  constructor(id=''){this.id=id;this.children=[];this.dataset={};this.attributes={};this.classes=new Set();this.disabled=false;
    this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name),contains:name=>this.classes.has(name)};}
  append(child){if(child.parentElement)child.parentElement.children=child.parentElement.children.filter(n=>n!==child);this.children.push(child);child.parentElement=this;}
  setAttribute(key,value){this.attributes[key]=value;}
  focus(){this.focused=true;}
}
const elements=new Map(),root=new Node();
const find=(node,id)=>node.id===id?node:node.children.map(child=>find(child,id)).find(Boolean);
const $=id=>{const existing=find(root,id);if(existing)return existing;if(!elements.has(id)){const node=new Node(id);elements.set(id,node);root.append(node);}return elements.get(id);};
const document={getElementById:$,createElement:()=>new Node()};
const mountField=new Node();root.append(mountField);mountField.append($('mount-select'));
const state={transform:structuredClone(recipe),hull:{},turret:{},busy:false};
let attached=null,cleared=0;
const menus=inspectorMenus({document,state,support:()=>false,onEnter:(_,part)=>attached=part,
  onLeave(){attached=null;cleared++;},onChange(){if(!menus.navigation.tool)$('build-button').disabled=false;}});
const buttons=$('inspector-home').children;
const visibleTools=()=>Object.entries(INSPECTOR_TOOLS).filter(([key])=>!$('inspector-panel-'+key).classList.contains('hidden'));
assert.equal(visibleTools().length,0);assert.ok($('inspector-tool').classList.contains('hidden'));
$('edit-turret').onclick();buttons[Object.keys(INSPECTOR_TOOLS).indexOf('commander')].onclick();
assert.equal(menus.navigation.tool,'commander');assert.equal(attached,'commander_mg');
assert.deepEqual(visibleTools().map(([key])=>key),['commander']);
assert.ok($('inspector-build').classList.contains('hidden'));assert.ok($('build-button').disabled);
assert.ok($('edit-hull').disabled&&$('operation-select').disabled&&$('pick-edit').disabled);
assert.equal($('cmd-parent').parentElement.parentElement,$('working-target-slot'),'commander context appears above the tool');
assert.equal($('armor-list').parentElement.parentElement,$('working-target-slot'),'armor uses the existing placed-part selector at the top');
assert.equal($('design-turret-pos').parentElement.parentElement,$('working-target-slot'),'position has a visible native-module context');
assert.equal($('commander-field').parentElement.id,'inspector-panel-commander');
assert.equal($('smoke-section').parentElement.id,'inspector-panel-smoke');
state.inspectorPending=1;menus.render();assert.ok($('inspector-finish').disabled);
assert.equal($('inspector-tool-content').inert,true);$('inspector-finish').onclick();assert.equal(menus.navigation.tool,'commander');
state.inspectorPending=0;menus.render();$('inspector-finish').onclick();
assert.equal(cleared,1);assert.equal(attached,null);assert.deepEqual(state.transform,recipe);
assert.equal(visibleTools().length,0);assert.equal($('build-button').disabled,false);
assert.ok(buttons[Object.keys(INSPECTOR_TOOLS).indexOf('commander')].focused,'keyboard focus returns to the tool entry');
buttons[Object.keys(INSPECTOR_TOOLS).indexOf('smoke')].onclick();assert.deepEqual(visibleTools().map(([key])=>key),['smoke']);
$('inspector-finish').onclick();assert.equal(menus.navigation.section,'turret');
buttons[Object.keys(INSPECTOR_TOOLS).indexOf('joints')].onclick();assert.ok($('inspector-transforms').classList.contains('hidden'),'joint tool cannot move the entire turret');
menus.navigation.reset();assert.equal(visibleTools().length,0);
state.editing={eid:1};menus.render();assert.ok($('gun-donor').disabled,'recipe edit must retain its existing donor lock');
state.editing=null;state.hull=null;state.turret=null;
assert.ok(menus.navigation.open('operation'),'operation selection is available before units');
assert.equal($('operation-select').disabled,false);assert.equal($('pick-hull').disabled,false);
assert.ok($('gun-donor').disabled,'empty donor selector remains disabled');
assert.ok(menus.navigation.accepts('hull')&&menus.navigation.accepts('turret'));
assert.equal(menus.navigation.open('commander'),false);menus.navigation.finish();
const html=fs.readFileSync('frontend/index.html','utf8'),app=fs.readFileSync('frontend/app.js','utf8');
// Exercise the production build gate without creating a WebGL renderer.
const hintSource=app.slice(app.indexOf('function checkHint()'),app.indexOf('function updateBuild()'));
const hint=check=>new Function('state','checkKey','t','roleName',hintSource+'return checkHint();')(
  {check:{key:'fixture',...check}},()=> 'fixture',value=>value,value=>value);
assert.equal(hint({status:'pending'}).blocked,true,'cannot build while compatibility is unresolved');
assert.equal(hint({status:'unknown',message:'Operation cancelled.'}).blocked,true,'canceling check must not approve construction');
assert.equal(hint({status:'fail',message:'Fixture rejection'}).blocked,true);
assert.equal(hint({status:'ok',crew:['driver','commander']}).blocked,false);
assert.equal([...html.matchAll(/id="inspector-finish"/g)].length,1);
assert.match(html,/id="cmd-enabled" class="hidden"/);
assert.match(app,/inspector\.navigation\.accepts\(part\)/);
assert.match(app,/inspector\?\.navigation\.tool!=='crew'/);
// An adjustment to the gun must not undo armor finalized in a prior tool.
const history=editorHistory(3),initial=JSON.stringify({armor:0,gun:0}),armored=JSON.stringify({armor:1,gun:0}),armed=JSON.stringify({armor:1,gun:1});
history.begin();history.remember(initial);assert.equal(history.step('undo',armored),initial);
assert.equal(history.step('redo',initial),armored);
history.begin();assert.equal(history.step('undo',armored),null);
history.remember(armored);assert.equal(history.step('undo',armed),armored);
assert.equal(history.step('undo',armored),null,'cannot cross the tool boundary');
assert.equal(history.step('redo',armored),armed);
history.remember(armed);history.remember('third');history.remember('fourth');history.remember('fifth');
assert.equal(history.step('undo','sixth'),'fifth');assert.equal(history.step('undo','fifth'),'fourth');
history.begin();assert.equal(history.available('redo'),false);history.clear();assert.equal(history.available('undo'),false);
console.log('Inspector menus: exclusive ownership, async lock, draft preservation, support tools, DOM isolation, finish/focus and build recovery passed.');
