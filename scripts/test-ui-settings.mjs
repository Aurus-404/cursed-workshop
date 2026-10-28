import assert from 'node:assert/strict';
// Fresh browser origins have no local storage. Workspace settings remain authoritative.
globalThis.localStorage={getItem(){throw Error('disabled');},setItem(){throw Error('disabled');}};
let saved={language:'zh-CN',theme:'olive',size:'large',motion:true,help:false,sound:false};
let port=8765,active=0,maxActive=0,fail=false;const writes=[];
globalThis.fetch=async(path,options)=>{
  assert.ok(port===8765||port===9876);
  if(path==='/api/session')return {ok:true,json:async()=>({token:'fixture-'+port})};
  assert.equal(options.headers['X-Workshop-Token'],'fixture-'+port);
  if(options.method==='POST'){
    active++;maxActive=Math.max(active,maxActive);
    await new Promise(resolve=>setTimeout(resolve,5));active--;
    if(fail){fail=false;return {ok:false,json:async()=>({error:'fixture write failure'})};}
    const patch=JSON.parse(options.body);writes.push(patch);saved={...saved,...patch};
  }
  return {ok:true,json:async()=>({...saved})};
};
const first=await import('../frontend/ui-settings.js?first');
await first.loadUISettings();
assert.equal(first.uiStorage.getItem('cw.lang'),'zh-CN');
assert.equal(first.uiStorage.getItem('cw.completionSound'),'off');
assert.equal(JSON.parse(first.uiStorage.getItem('cw.appearance')).theme,'olive');
await Promise.all([first.saveUISettings({theme:'paper'}),first.saveUISettings({theme:'earth'}),first.saveUISettings({language:'en'})]);
assert.equal(maxActive,1);assert.equal(saved.theme,'earth');
port=9876;
const restarted=await import('../frontend/ui-settings.js?restart');
await restarted.loadUISettings();
assert.equal(restarted.uiStorage.getItem('cw.lang'),'en');
assert.equal(JSON.parse(restarted.uiStorage.getItem('cw.appearance')).theme,'earth');
fail=true;await assert.rejects(restarted.saveUISettings({help:true}),/fixture write failure/);
await restarted.saveUISettings({sound:true});
assert.equal(saved.help,false);assert.equal(saved.sound,true);
console.log('UI preferences: cross-origin restart, disabled storage, ordered writes and failure recovery passed.');
