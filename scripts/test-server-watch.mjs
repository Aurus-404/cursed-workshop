import assert from 'node:assert/strict';
import {serverWatch} from '../frontend/server-watch.js';

const notices=[];let up=false,probes=0;
const watch=serverWatch({probe:async()=>{probes++;if(!up)throw new TypeError('NetworkError');},notify:lost=>notices.push(lost),wait:async()=>{}});
assert.equal(await watch.confirm(),true,'gone after two failed probes');
assert.equal(probes,2);assert.deepEqual(notices,[true]);
assert.equal(await watch.confirm(),true);assert.deepEqual(notices,[true],'said once');
up=true;probes=0;
assert.equal(await watch.confirm(),false,'one unlucky call is no closed server');
assert.equal(probes,1);assert.deepEqual(notices,[true,false],'the notice goes away');
up=false;probes=0;let attempt=0;
const flaky=serverWatch({probe:async()=>{if(attempt++===0)throw new TypeError('NetworkError');},notify:lost=>notices.push(lost),wait:async()=>{}});
assert.equal(await flaky.confirm(),false,'a second chance before saying it closed');
watch.alive();console.log('Server watch: confirmed before notifying, once, and cleared when it answers again passed.');
