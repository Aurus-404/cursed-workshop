import assert from 'node:assert/strict';
globalThis.localStorage={getItem:()=> 'es'};
const {showAddonReadiness,installMessage}=await import('../frontend/addon-readiness.js');
const {translate}=await import('../frontend/i18n.js');
const {UI_COPY}=await import('../frontend/ui-copy.js');
const nodes=new Map();
const $=id=>{if(!nodes.has(id)){const classes=new Set(),attrs=new Map();nodes.set(id,{textContent:'',classes,classList:{toggle(k,v){v?classes.add(k):classes.delete(k);}},getAttribute:k=>attrs.get(k),setAttribute:(k,v)=>attrs.set(k,v)});}return nodes.get(id);};
const classes=$('addon-readiness').classes;
showAddonReadiness($,{required:true,ready:false,message:'not loaded'});
assert.equal(classes.has('verified'),false);assert.equal(classes.has('hidden'),false);
showAddonReadiness($,{required:true,ready:true,message:'loaded'});assert.equal(classes.has('verified'),true);assert.equal(classes.has('hidden'),true,'confirmed addon frees the viewport');
showAddonReadiness($,{required:true,ready:false,message:'disabled'});assert.equal(classes.has('verified'),false);
showAddonReadiness($,{required:false});assert.equal(classes.has('hidden'),true);
showAddonReadiness($,{required:true,ready:false,status:'waiting_for_game',message:'long instructions',recovery_backup:'saved-copy.sav'});
assert.doesNotMatch($('addon-readiness-summary').textContent,/long instructions|saved-copy/);
$('addon-readiness-toggle').onclick();assert.equal($('addon-readiness-toggle').getAttribute('aria-expanded'),'true');
assert.match($('addon-readiness-message').textContent,/long instructions[\s\S]*saved-copy/);
showAddonReadiness($,{required:true,ready:false,status:'waiting_for_game',message:'long instructions'});
assert.equal($('addon-readiness-toggle').getAttribute('aria-expanded'),'true','polling does not collapse opened help');
$('addon-readiness-toggle').onclick();assert.equal($('addon-readiness-details').classes.has('hidden'),true);
$('addon-readiness-toggle').onclick();showAddonReadiness($,{required:true,ready:true,status:'loaded'});
assert.equal($('addon-readiness-toggle').getAttribute('aria-expanded'),'false','the next build starts compact after confirmation');
assert.match(installMessage({instructions:'check the local mod'}),/todavía no confirma/);
for(const language of ['en','zh-CN']){
  for(const source of ['Comprobar carga del mod','No cargues ni guardes la campaña todavía: falta comprobar que el juego cargó el mod de vehículos.'])assert.notEqual(translate(source,language),source);
  const orderHelp=Object.keys(UI_COPY).find(s=>s.startsWith('En Extras → Mods, activá'));
  assert.match(orderHelp,/al final de la lista/);assert.notEqual(translate(orderHelp,language),orderHelp);
}
console.log('Addon readiness: pending, verified, disabled, hidden and translated; saving never implies live activation.');
