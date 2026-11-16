import assert from 'node:assert/strict';
globalThis.localStorage={getItem:()=> 'es'};
const {cacheSettings}=await import('../frontend/cache-settings.js');
const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:false});return nodes.get(id);};
let confirmed=false,active=false,failRead=false,failClear=false,clears=0,skip=0;
const ui=cacheSettings({$,busy:()=>active,confirm:()=>confirmed,
  api:async path=>{assert.equal(path,'/api/cache');if(failRead)throw Error('fixture');return {bytes:1048576,files:3};},
  operation:async(path,body)=>{assert.equal(path,'/api/clear-cache');assert.deepEqual(body,{confirm:true});clears++;if(failClear)throw Error('fixture');return {removed_bytes:1048576,removed_files:3,skipped:skip};}});
await ui.refresh();assert.match($('cache-status').textContent,/1.0 MiB/);assert.match($('cache-status').textContent,/3 archivos/);
await $('clear-cache').onclick();assert.equal(clears,0,'cancel does not send a destructive request');
confirmed=true;active=true;await $('clear-cache').onclick();assert.equal(clears,0,'active work cannot start cleanup');
active=false;await $('clear-cache').onclick();assert.equal(clears,1);assert.match($('cache-status').textContent,/3 archivos eliminados/);
skip=1;await $('clear-cache').onclick();assert.match($('cache-status').textContent,/se conservaron/);
failClear=true;await $('clear-cache').onclick();assert.match($('cache-status').textContent,/reintentar/);assert.equal($('clear-cache').disabled,false);
failRead=true;await ui.refresh();assert.match($('cache-status').textContent,/No se pudo calcular/);assert.equal($('clear-cache').disabled,false);
console.log('Cache settings: size, confirmation, busy guard, cleanup result, partial cleanup and failed requests passed.');
