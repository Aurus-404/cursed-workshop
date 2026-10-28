import assert from 'node:assert/strict';
import {runJob} from '../frontend/job-client.js';
import {operationProgress} from '../frontend/operation-progress.js';
import {commanderProblem} from '../frontend/commander-mg.js';
const controller=new AbortController(),calls=[];let status='running';
const api=async(path,body)=>{
  calls.push(path);
  if(path==='/read'){controller.abort();return {job_id:'owned'};}
  if(path==='/api/cancel-job'){assert.equal(body.id,'owned');status='cancelled';return {accepted:true};}
  return {status,cancellable:true};
};
await assert.rejects(()=>runJob(api,'/read',{}, {signal:controller.signal,wait:async()=>{}}),e=>e.name==='AbortError');
assert.ok(calls.includes('/api/cancel-job'),'cancel reaches backend even when requested during POST');
const before=calls.length;await assert.rejects(()=>runJob(api,'/read',{}, {signal:controller.signal}),e=>e.name==='AbortError');assert.equal(calls.length,before);
const progress=operationProgress({busy(){},render(){},schedule:()=>1,unschedule(){}});let release;
const job=progress.run([{label:'read',run:()=>new Promise(r=>release=r)}]);
assert.equal(progress.cancel(),false,'publication/non-cancellable stages stay protected');
progress.setCancellable(true);assert.equal(progress.cancel(),true);assert.ok(progress.signal.aborted);release();await assert.rejects(()=>job,e=>e.name==='AbortError');
assert.equal(commanderProblem({status:'fail',message:'Missing chassis assembly recipe: mw_example'}),'Missing chassis assembly recipe: mw_example');
assert.equal(commanderProblem({status:'ok',crew:['commander']}),'');
assert.equal(commanderProblem({status:'ok',crew:['commander'],commander_problem:'Commander already operates a gun'}),'Commander already operates a gun');
assert.match(commanderProblem({status:'ok',crew:['driver']}),/comandante/);
assert.match(commanderProblem({status:'pending'}),/Comprobando/);
console.log('Job client: server cancellation, POST race, protected stages and accurate commander errors passed.');

for(const protectedJob of [false,true]){
  const requests=[],finished=[];
  const failedPoll=async(path,body)=>{
    requests.push([path,body]);
    if(path==='/preview')return {job_id:'poll-failure'};
    if(path==='/api/cancel-job')return {accepted:!protectedJob};
    throw new TypeError('Network unavailable');
  };
  await assert.rejects(()=>runJob(failedPoll,'/preview',{}, {onStatus:job=>finished.push(job)}),/Network unavailable/);
  assert.equal(requests.filter(([p])=>p==='/api/cancel-job').length,1,'a failed poll releases read-only backend work');
  assert.deepEqual(finished.at(-1),{status:'finished',cancellable:false});
}
