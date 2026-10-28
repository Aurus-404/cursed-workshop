import assert from 'node:assert/strict';
import {vehicleStorage} from '../frontend/vehicle-storage.js';
import {translate} from '../frontend/i18n.js';
import {UI_COPY} from '../frontend/ui-copy.js';
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:false,classList:{toggle(){}},showModal(){this.open=true;},close(){this.open=false;}});return nodes.get(id);};
const campaign={source_path:'isolated.sav',source_hash:'original'},state={campaign,busy:false},calls=[],errors=[];
const editor=vehicleStorage({$,state,t:s=>s,pollJob:async(route,body)=>calls.push({route,body}),loading:async(label,action)=>action(),readSave:async path=>calls.push({path}),status(){},reportError:e=>errors.push(e)});
editor.store({kind:'vehicle',template:'mw_test',eid:'0x9000',label:'Veteran vehicle'});
assert.equal($('delete-name').textContent,'Veteran vehicle');assert.equal($('delete-dialog').open,true);assert.equal(calls.length,0);
await $('delete-confirm').onclick();assert.deepEqual(calls[0],{route:'/api/store-vehicle',body:{eid:'0x9000',source_hash:'original',source_path:'isolated.sav'}});
assert.equal(calls[1].path,'isolated.sav');assert.equal($('delete-dialog').open,false);
editor.open({id:'saved',template:'mw_test',crew:[{},{}],pending:false});
assert.equal($('storage-crew-count').textContent,'2');await $('storage-restore').onclick();
assert.equal(calls[2].route,'/api/restore-stored-vehicle');assert.equal(calls[2].body.id,'saved');
editor.open({id:'saved',template:'mw_test',crew:[],pending:true});await $('storage-recover').onclick();assert.equal(calls[4].route,'/api/recover-stored-vehicle');
editor.store({kind:'vehicle',template:'mw_test',eid:'0x9000'});state.campaign={...campaign,source_path:'other.sav'};await $('delete-confirm').onclick();assert.equal(calls.length,6);assert.match(errors.at(-1).message,/campaña cambió/);
state.campaign=campaign;state.busy=true;editor.open({});state.busy=false;
editor.store({kind:'vehicle',template:'native',eid:'0x9000'});assert.equal($('delete-dialog').open,true);
editor.store({kind:'human',template:'tankman',eid:'0x9001'});assert.match(errors.at(-1).message,/vehículo de la campaña/);
state.campaign={source_path:'destination.sav',source_hash:'destination',campaign_name:'Destination',army:'ger'};
editor.open({id:'foreign',template:'t28',crew:[],origin_campaign:'Soviet campaign',origin_army:'rus',pending:false});
assert.equal($('storage-origin').textContent,'Soviet campaign · rus');assert.equal($('storage-destination').textContent,'Destination · ger');
editor.open({id:'foreign',template:'t28',crew:[],pending:true,recoverable:false,pending_campaign:'Affected campaign'});
assert.equal($('storage-recover').disabled,true);assert.equal($('storage-recovery-campaign').textContent,'Affected campaign');
for(const source of ['Almacén','Quitar de la campaña','Guardar en el Almacén','Devolver a la campaña','Recuperar traslado','Vehículo y tripulación guardados en el Almacén.'])for(const language of ['en','zh-CN'])assert.notEqual(translate(source,language),source);
// Every storage error and UI paragraph has translations in both supported languages.
const {readFileSync}=await import('node:fs');
for(const file of ['workshop/vehicle_storage.py','frontend/vehicle-storage.js']){
  const source=readFileSync(file,'utf8');
  for(const match of source.matchAll(/(?:StorageError|CampaignError|t)\('([^']*[áéíóúñÁÉÍÓÚÑ][^']*)'\)/g))assert.ok(UI_COPY[match[1]],match[1]);
}
console.log('Storage: confirmation, exact campaign binding, return, interrupted transfer, busy guard and translations passed.');
