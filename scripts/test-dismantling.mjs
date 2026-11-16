import assert from 'node:assert/strict';
import {dismantling} from '../frontend/dismantling.js';
globalThis.document={createElement(){return {textContent:''};}};
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',shown:false,showModal(){this.shown=true;},close(){this.shown=false;},replaceChildren(...rows){this.rows=rows;}});return nodes.get(id);};
const campaign={source_path:'campaign.sav',source_hash:'original'},state={campaign,busy:false};
const requests=[],errors=[];let reopened;
const editor=dismantling({$,state,loading:async(label,run)=>run(),pollJob:async(path,body)=>{
  requests.push([path,body]);return {parts:[{part:'hull',label:'Tank'},{part:'turret',label:'Tank turret'},{part:'gun',label:'75 mm'}],integrated:[]};
},readSave:async path=>{reopened=path;},status(){},reportError:e=>errors.push(e)});
await editor.open({eid:'0x1',kind:'vehicle',label:'Tank'});
assert.equal(requests.length,1,'review does not dismantle anything');assert.equal($('dismantle-dialog').shown,true);
assert.equal($('dismantle-parts').rows.length,3);
state.campaign={...campaign,source_hash:'changed'};await $('dismantle-confirm').onclick();
assert.equal(requests.length,1,'changing campaign invalidates the confirmation');assert.equal(errors.length,1);
state.campaign=campaign;await $('dismantle-confirm').onclick();assert.equal(requests.length,2);
assert.equal(requests[1][0],'/api/dismantle-vehicle');assert.deepEqual(requests[1][1],{eid:'0x1',source_path:'campaign.sav',source_hash:'original'});
assert.equal(reopened,'campaign.sav');assert.equal($('dismantle-dialog').shown,false);
console.log('Dismantling: typed component review, explicit confirmation, campaign identity guard and refreshed campaign passed.');
