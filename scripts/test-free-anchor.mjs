import assert from 'node:assert/strict';
import fs from 'node:fs';
import {latestTask} from '../frontend/latest-task.js';

// Run the production handlers without starting a WebGL renderer.
const app=fs.readFileSync('frontend/app.js','utf8');
const source=app.slice(app.indexOf('const freeAnchorTask='),app.indexOf('function moduleBase()'));
const state={hull:{template:'one'},turret:{template:'donor'},mounts:{hull:'@free',turret:'first'}};
const reads=[],errors=[];let applied=0;
const quickJob=(path,body,{signal})=>new Promise((resolve,reject)=>reads.push({body,signal,resolve,reject}));
const refresh=new Function('latestTask','quickJob','state','hullIsFree','applyTransforms','status',source+'return refreshFreeAnchor;')(
  latestTask,quickJob,state,()=>state.mounts.hull.startsWith('@free'),()=>applied++,e=>errors.push(e));
const started=async count=>{for(let n=0;reads.length<count&&n<100;n++)await new Promise(r=>setTimeout(r,1));assert.equal(reads.length,count);};
const old=refresh();await started(1);
state.mounts.turret='second';const current=refresh();await started(2);
assert.ok(reads[0].signal.aborted);
reads[1].resolve({anchor:[4,5,6]});await current;
reads[0].resolve({anchor:[90,90,90]});await old;
assert.deepEqual(state.freeAnchor,[4,5,6]);assert.equal(applied,1);
const leaving=refresh();await started(3);state.mounts.hull='turret';await refresh();
reads[2].reject(new Error('Stale failure'));await leaving;
assert.equal(state.freeAnchor,null);assert.deepEqual(errors,[]);
assert.match(app,/\['smoke-tubes','onchange'\]/,'tube model changes use the exclusive cancellable tool wrapper');
console.log('Free anchor: superseded reads, mount changes, leaving free mode and stale errors passed.');
