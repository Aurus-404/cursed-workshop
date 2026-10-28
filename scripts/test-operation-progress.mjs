import assert from 'node:assert/strict';
import {operationProgress,elapsedTime,completionSound} from '../frontend/operation-progress.js';
import {buildVehicle} from '../frontend/build-vehicle.js';
globalThis.localStorage={getItem:()=> 'es'};
const {englishText}=await import('../frontend/i18n.js');

function fixture(){
  let time=0,tick;const frames=[],locks=[],notices=[],cleared=[];
  const progress=operationProgress({now:()=>time,render:s=>frames.push({...s}),busy:b=>locks.push(b),
    schedule:fn=>{tick=fn;return 17;},unschedule:id=>cleared.push(id),notify:(...args)=>notices.push(args)});
  return {progress,frames,locks,notices,cleared,advance:ms=>{time+=ms;tick();}};
}
const f=fixture(),order=[];
const result=await buildVehicle(f.progress,{
  generate:async()=>{order.push('generate');f.advance(41000);return {new_template:'mw_fixture'};},
  portrait:async id=>{assert.equal(id,'mw_fixture');order.push('portrait');f.advance(7000);return {warnings:[]};},
  install:async()=>{order.push('install');f.advance(19000);return {name:'fixture'};},
  reload:async()=>{order.push('reload');f.advance(3000);},
});
assert.deepEqual(order,['generate','portrait','install','reload']);
assert.equal(result.elapsedMs,70000,'whole build includes portrait, install and reload');
assert.deepEqual(f.locks,[true,false],'no unlocks or clock resets between stages');
assert.deepEqual(f.notices,[['Vehículo construido',70000]],'one chime after successful reload');
assert.equal(f.frames.at(-1).completed,4);assert.equal(f.frames.at(-1).active,false);
assert.ok(f.frames.every(s=>s.total===4));
for(let i=1;i<f.frames.length;i++){
  assert.ok(f.frames[i].elapsedMs>=f.frames[i-1].elapsedMs);
  assert.ok(f.frames[i].completed>=f.frames[i-1].completed);
}
assert.deepEqual(f.cleared,[17]);
assert.equal(elapsedTime(0),'00:00');assert.equal(elapsedTime(330999),'05:30');assert.equal(elapsedTime(3661000),'61:01');

// The clock keeps ticking while an operation waits; overlapping user operations are rejected.
const waiting=fixture();let release;
const running=waiting.progress.run([{label:'Cargando vehículo',run:()=>new Promise(r=>release=r)}],{notice:'Vehículo cargado'});
waiting.advance(5500);assert.equal(waiting.frames.at(-1).elapsedMs,5500);assert.equal(waiting.frames.at(-1).completed,0);
await assert.rejects(()=>waiting.progress.run([{label:'overlap',run:()=>assert.fail()}]),/Esperá/);
release('model');assert.equal(await running,'model');assert.equal(waiting.notices.length,1);

for(const failedStage of ['generate','install','reload']){
  const fail=fixture(),visited=[];
  const actions=Object.fromEntries(['generate','portrait','install','reload'].map(key=>[key,async()=>{
    visited.push(key);fail.advance(1000);if(key===failedStage)throw Error('fixture failure');
    return key==='generate'?{new_template:'mw_fixture'}:key==='portrait'?{warnings:[]}:{};
  }]));
  await assert.rejects(()=>buildVehicle(fail.progress,actions),failedStage==='reload'?/ya se guardó/:/fixture failure/);
  assert.equal(fail.progress.active,false);assert.deepEqual(fail.locks,[true,false]);
  assert.equal(fail.notices.length,0,'failures must not announce success');assert.deepEqual(fail.cleared,[17]);
  if(failedStage==='install')assert.ok(!visited.includes('reload'));
  await fail.progress.run([{label:'Retry',run:async()=>{fail.advance(2000);}}]);
  assert.equal(fail.progress.elapsedMs,2000,'next operation starts its own clock');
}
const fallback=fixture();let installed=false;
const warning=await buildVehicle(fallback.progress,{
  generate:async()=>({new_template:'mw_fixture'}),portrait:async()=>{throw Error('no WebGL');},
  install:async()=>{installed=true;return {};},reload:async()=>{},
});
assert.ok(installed);assert.match(warning.portraitWarning,/respaldo/);assert.equal(fallback.notices.length,1);
const brokenNotice=operationProgress({busy:()=>{},render:()=>{},notify:()=>{throw Error('audio unavailable');}});
assert.equal(await brokenNotice.run([{label:'Done',run:async()=>42}],{notice:'Ready'}),42);

// Sound is optional, local and armed by a gesture. Audio failures cannot fail a build.
let tones=0,created=0,resumes=0;const saved=[];
const context={state:'suspended',currentTime:1,destination:{},resume(){resumes++;this.state='running';return Promise.resolve();},
  createOscillator(){tones++;return {frequency:{},connect(){},disconnect(){},start(){},stop(){}};},
  createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){}},connect(){},disconnect(){}};}};
const sound=completionSound({contextFactory:()=>{created++;return context;},storage:{getItem:()=>null,setItem:(...a)=>saved.push(a)}});
assert.equal(sound.play(),false);assert.equal(created,0,'no unsolicited audio context before a gesture');
sound.arm();assert.equal(created,1);assert.equal(resumes,1);assert.equal(sound.play(),true);assert.equal(tones,2);
sound.setEnabled(false);assert.equal(sound.play(),false);assert.equal(tones,2);sound.arm();assert.equal(created,1);
assert.deepEqual(saved.at(-1),['cw.completionSound','off']);
const muted=completionSound({contextFactory:()=>assert.fail(),storage:{getItem:()=> 'off'}});muted.arm();assert.equal(muted.play(),false);
const unavailable=completionSound({contextFactory:()=>{throw Error('no device');},storage:{getItem(){throw Error();}}});
unavailable.arm();assert.equal(unavailable.play(),false);unavailable.setEnabled(false);
for(const [es,en] of [
  ['Tiempo transcurrido: 05:30','Elapsed time: 05:30'],['Tiempo total: 61:01','Total time: 61:01'],
  ['Etapas completadas: 2/4','Completed stages: 2/4'],['Recargando campaña y unidades','Reloading campaign and units'],
  ['Vehículo cargado','Vehicle loaded'],['Progreso por etapas','Progress by stage'],
])assert.equal(englishText(es),en);
console.log('Operation progress: full build clock, real stages, pending jobs, concurrency, failures/retry, portrait fallback, optional sound and ES/EN passed.');
