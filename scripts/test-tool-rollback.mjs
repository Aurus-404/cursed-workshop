import assert from 'node:assert/strict';
import fs from 'node:fs';
const app=fs.readFileSync('frontend/app.js','utf8');
const source=app.slice(app.indexOf('async function toolOperation('),app.indexOf('const history=editorHistory()'));
const original={gun_swap:{schema:1},smoke_launchers:[{tubes:3}],extra_armor:[{asset:'native'}]};
const state={transform:structuredClone(original),busy:false,inspectorPending:0};
const restored=[],events=[];let fail=false;const activity={start:()=>events.push('start'),stage:()=>events.push('recovery'),finish:()=>events.push('finish')};
const editor=name=>({restore:async()=>{restored.push(name);if(fail&&name==='gun')throw Error('Preview unavailable');}});
const operation=new Function('state','inspector','cmdEditor','armorEditor','smokeEditor','towingEditor','chargeEditor','riderEditor','passengerEditor','gunEditor','supportEditor','applyTransforms','status','refreshCheck','toolActivity','renderCrewList',source+'return toolOperation;')(
  state,{render:()=>events.push('render')},editor('commander'),editor('armor'),editor('smoke'),editor('towing'),editor('charge'),editor('rider'),editor('passengers'),editor('gun'),editor('support'),
  ()=>events.push('transforms'),()=>{},()=>events.push('check'),activity,()=>{assert.equal(state.inspectorPending,0);events.push('crew');});
await operation(async()=>{
  state.transform.smoke_launchers.push({tubes:1});delete state.transform.gun_swap;
  assert.equal(state.inspectorPending,1);state.toolController.abort();
});
assert.deepEqual(state.transform,original);
assert.deepEqual(restored,['commander','armor','smoke','towing','charge','rider','passengers','gun','support']);
assert.equal(state.inspectorPending,0);assert.equal(state.toolController,null);assert.equal(state.toolCancellable,false);
fail=true;await assert.rejects(()=>operation(async()=>state.toolController.abort()),/Preview unavailable/);
assert.equal(events.filter(e=>e==='start').length,events.filter(e=>e==='finish').length,'activity is cleared after success, cancellation and failed recovery');
assert.equal(state.inspectorPending,0,'even a failed recovery preview must release the tool lock');
fail=false;await operation(async()=>{});
assert.equal(state.inspectorPending,0);await assert.rejects(()=>operation(async()=>{throw Error('Save failed');}),/Save failed/);
assert.equal(state.inspectorPending,0);assert.equal(events.filter(e=>e==='start').length,events.filter(e=>e==='finish').length);
state.busy=true;let ran=false;await operation(async()=>ran=true);assert.equal(ran,false);
console.log('Tool rollback: composite recipe recovery, gun preview, lock cleanup and busy guard passed.');
