import assert from 'node:assert/strict';
globalThis.document={documentElement:{lang:'es'}};
globalThis.localStorage={getItem:()=> 'es'};
const {lang,englishText,translate}=await import('../frontend/i18n.js');
const {cleanText,selectionData,diagnosticReport,reportParts,copyPart,diagnosticUI}=await import('../frontend/diagnostics.js');
assert.equal(lang,'es');
assert.equal(englishText('Abrí una campaña y sus recursos primero.'),'Open a campaign and its resources first.');
assert.equal(englishText('Cargar diseño del vehículo'),'Load vehicle design');
assert.equal(englishText('Leer armas del diseño'),'Read design weapons');
const state={token:'SESSION_PRIVATE',config:{version:'test',settings:'PRIVATE_SETTINGS'},
  campaign:{name:'PRIVATE_CAMPAIGN'},hull:{template:'t26',eid:'PRIVATE_EID',label:'PRIVATE_LABEL'},turret:{template:'flak'},
  mounts:{hull:'turret',turret:'basis'},transform:{hull:{position:[1,2,3],rotation:[0,0,0]},
    commander_mg:{parent:'turret',item:'mg34',position:[0,0,1],yaw:[-90,90],pitch:[-10,30],private:'PRIVATE_EXTRA'}}};
const data={version:'test',campaign_loaded:true,resources_indexed:true,sources:[{id:'base',version:'1',root:'PRIVATE_ROOT'}],
  build:{revision:'a'.repeat(40),source_sha256:'b'.repeat(64),built_utc:'2026-10-18T00:00:00+00:00',private:'PRIVATE_EXTRA'},
  recent_phases:[{time_utc:'2026-10-15T12:00Z',operation:'Install: copy new addon',status:'done',elapsed_ms:123.4,private:'PRIVATE_EXTRA'}],
  recent_operations:[{time_utc:'2026-10-15T12:00Z',operation:'Crear mod y campaña',status:'done',elapsed_ms:54880.4,queued_ms:2.9,extra:'PRIVATE_EXTRA'}],
  errors:[{operation:'Abrir campaña',type:'ValueError',message:'Abrí una campaña y sus recursos primero.',frames:['module:fn:42']}]};
const report=diagnosticReport(data,state,'La operación no se pudo completar. Usá «Copiar diagnóstico» para compartir el informe como texto.\n\nDetalle técnico: Need one interaction record',new Date('2026-10-15T12:00Z'));
for(const secret of ['PRIVATE','Abrí','Detalle técnico','La operación'])assert.ok(!report.includes(secret),secret);
assert.ok(report.includes('Open campaign | ValueError'));
assert.ok(report.includes('Build mod and campaign | done | 54880 ms (queued 3 ms)'));
assert.ok(report.includes('Install: copy new addon | done | 123 ms'));
assert.ok(report.includes('Phases may overlap; do not sum them.'));
assert.ok(report.includes('Build: '+'a'.repeat(40)));
assert.ok(report.includes('Build source SHA-256: '+'b'.repeat(64)));
assert.ok(report.includes('Technical detail:\nNeed one interaction record'));
assert.deepEqual(selectionData(state).commander_mount.yaw,[-90,90]);
state.transform.crew_seats={gunner:{position:[1,2,3],space:'module'},charger:{position:[4,5,6]}};
assert.deepEqual(selectionData(state).crew_poses.map(p=>p.space),['module','world']);
const bad='SESSION_PRIVATE https://evil.example/file C:\\Users\\Name\\file.exe\n@everyone ```\u202e\0<img onerror="evil()">';
const scrubbed=cleanText(bad,state.token);
for(const secret of ['SESSION_PRIVATE','evil.example','Users','@everyone','```','\u202e','\0'])assert.ok(!scrubbed.includes(secret),secret);
// It remains inert literal text; formatting is never fed to innerHTML or a parser.
assert.ok(scrubbed.includes('<img'));
const huge=report+'😀'.repeat(4000);
const parts=reportParts(huge);
assert.ok(parts.length>1);
assert.ok(parts.every(p=>p.length<2000));
assert.equal(parts.map(p=>p.slice(p.indexOf('```text\n')+8,-4)).join(''),huge);
let copied='',selected=0;
const area={value:parts[0],focus(){selected++;},select(){selected++;}};
assert.equal(await copyPart(area,{writeText:async text=>{copied=text;}}),true);
assert.equal(copied,parts[0]);
assert.equal(await copyPart(area,{writeText:async()=>{throw Error('denied');}}),false);
assert.equal(selected,2);
const nodes=new Map();
const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false,showModal(){this.open=true;},focus(){},select(){}});return nodes.get(id);};
let requests=0;
const ui=diagnosticUI({state,api:async path=>{requests++;assert.equal(path,'/api/diagnostic');return data;},$});
ui.record(Error(bad));
assert.equal(requests,0); // no automatic export or clipboard access
await $('diagnostic-button').onclick();
assert.equal(requests,1);
assert.ok($('diagnostic-dialog').open);
assert.ok($('diagnostic-text').value.includes('Cursed Workshop diagnostic'));
assert.equal($('diagnostic-text').innerHTML,undefined);
assert.ok($('diagnostic-text').value.length>2000);
assert.ok($('diagnostic-text').value.includes('Latest UI error:'));
Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async text=>{copied=text;}}},configurable:true});
await $('diagnostic-copy').onclick();
assert.equal(copied,$('diagnostic-text').value);
assert.ok($('diagnostic-status').textContent.includes('completo'));
assert.equal($('diagnostic-copy').textContent,'Copiar todo');
const fallback=diagnosticUI({state,api:async()=>{throw Error('offline');},$});
await $('error-diagnostic').onclick();
assert.ok($('diagnostic-text').value.includes('Backend diagnostic unavailable'));
assert.ok($('diagnostic-text').value.includes('Campaign loaded: unknown'));
assert.ok(!$('diagnostic-text').value.includes('No backend errors recorded'));
// The report zip is made only on request and carries the text shown to the player.
const posted=[];
diagnosticUI({state,api:async(path,body)=>{posted.push([path,body]);return path==='/api/diagnostic'?data:{path:'C:\\r\\report.zip'};},$});
await $('diagnostic-button').onclick();
assert.deepEqual(posted.map(([path])=>path),['/api/diagnostic']);
await $('diagnostic-save').onclick();
assert.deepEqual(posted[1],['/api/error-report',{diagnostic:$('diagnostic-text').value}]);
assert.ok($('diagnostic-status').textContent.includes('#bug-reports'));
assert.equal($('diagnostic-save').disabled,false);
// No campaign: neither diagnostic text nor zip may be generated by the UI.
const loadedCampaign=state.campaign;state.campaign=null;posted.length=0;
await $('diagnostic-button').onclick();
assert.deepEqual(posted,[]);assert.equal($('diagnostic-text').value,'');
assert.equal($('diagnostic-save').disabled,true);assert.equal($('diagnostic-copy').disabled,true);
assert.equal($('diagnostic-status').textContent,'Seleccioná primero una campaña.');
await $('diagnostic-save').onclick();await $('diagnostic-copy').onclick();assert.deepEqual(posted,[]);
// Reopening a campaign restores export; a stale UI must also honor the backend.
state.campaign=loadedCampaign;await $('diagnostic-button').onclick();
assert.equal($('diagnostic-save').disabled,false);
diagnosticUI({state,api:async()=>({...data,campaign_loaded:false}),$});
await $('diagnostic-button').onclick();
assert.equal($('diagnostic-save').disabled,true);assert.equal($('diagnostic-text').value,'');
for(const language of ['en','zh-CN'])assert.notEqual(translate('Seleccioná primero una campaña.',language),'Seleccioná primero una campaña.');
console.log('Diagnostics: English output in Spanish UI, privacy, bounded report parts, explicit copy, clipboard fallback, offline preview and report zip passed.');
