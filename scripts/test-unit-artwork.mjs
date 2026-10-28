import assert from 'node:assert/strict';
import {unitArtwork} from '../frontend/unit-artwork.js';
let calls=0,created=0,revoked=0;const children=[];
const icon={append:n=>children.push(n),classList:{add:name=>assert.equal(name,'has-image')}};
const art=unitArtwork({document:{createElement:()=>({isConnected:true,remove(){this.removed=true;}})},token:()=> 'session',
  fetch:async(path,options)=>{calls++;assert.ok(!path.includes('session'));assert.equal(options.headers['X-Workshop-Token'],'session');return {ok:true,blob:async()=>({image:true})};},
  URL:{createObjectURL(){created++;return 'blob:local';},revokeObjectURL(){revoked++;}}});
art.attach(icon,{template:'tank'});art.attach(icon,{template:'tank'});
await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,1);assert.equal(created,2);
for(const image of children){assert.equal(image.src,'blob:local');image.onload();}assert.equal(revoked,2);
art.attach(icon,{template:'tank'},1);await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,2,'resource revisions invalidate cache');
console.log('Unit artwork: authenticated local requests, deduplication, revision invalidation and object URL cleanup passed.');
