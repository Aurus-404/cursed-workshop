import assert from 'node:assert/strict';
import {toolProgress} from '../frontend/tool-progress.js';
let clock=0,tick;const frames=[],stopped=[];
const progress=toolProgress({render:f=>frames.push(f),now:()=>clock,schedule:fn=>(tick=fn,42),unschedule:id=>stopped.push(id)});
progress.start('save');assert.equal(frames.at(-1).active,true);
clock=5600;tick();assert.equal(frames.at(-1).elapsedMs,5600);assert.equal(frames.at(-1).progress,null);
progress.stage('preview');assert.equal(frames.at(-1).label,'preview');assert.equal(frames.at(-1).elapsedMs,5600);
progress.observe({progress:{completed:2,total:7}});assert.equal(frames.at(-1).progress.completed,2);
progress.stage('recovery');assert.equal(frames.at(-1).progress,null,'unknown work never displays a made-up percentage');
progress.finish();assert.equal(frames.at(-1).active,false);assert.deepEqual(stopped,[42]);
clock=8000;progress.start('second');assert.equal(frames.at(-1).elapsedMs,0);progress.finish();
assert.deepEqual(stopped,[42,42]);
console.log('Inspector progress: immediate visibility, elapsed clock, stages, measured/unknown work, cleanup and restart passed.');

// Background work must stay a strip that lets clicks through; without these rules it covered the whole 3D view.
{const {readFileSync}=await import('node:fs');const css=readFileSync(new URL('../frontend/style.css',import.meta.url),'utf8');
  const strip=css.match(/\.busy-overlay\.passive\{[^}]*\}/)?.[0]||'';
  if(!/pointer-events:none/.test(strip)||!/top:auto/.test(strip))throw Error('the background progress strip lost its style');
  for(const rule of ['.shop-list{','.creative-bar{','.check-list{','#operation-controls>#check-again{display:none}'])if(!css.includes(rule))throw Error('missing style: '+rule);}
