import assert from 'node:assert/strict';
import fs from 'node:fs';
globalThis.localStorage={getItem:()=>null,setItem(){}};
const {EN,CHINESE,LANGUAGES,translate,englishText,initialLang,setLang}=await import('../frontend/i18n.js');
const {RU}=await import('../frontend/ru.js');
const {FINISH_NAMES}=await import('../frontend/locale-extra.js');
const {uiStorage}=await import('../frontend/ui-settings.js');
const placeholders=value=>(value.match(/\{[a-z_]+\}/g)||[]).sort();
for(const [language,dict] of [['zh-CN',CHINESE],['ru',RU]]){
  assert.deepEqual(Object.keys(dict).filter(key=>!Object.hasOwn(EN,key)),[],`${language}: obsolete keys`);
  for(const [key,value] of Object.entries(EN)){
    assert.ok(typeof dict[key]==='string'&&dict[key].trim(),`${language}: missing ${key}`);
    assert.deepEqual(placeholders(dict[key]),placeholders(value),`${language}: damaged placeholder ${key}`);
    assert.equal(translate(key,language),dict[key],`${language}: lookup ${key}`);
    assert.equal(translate(dict[key],language),dict[key],`${language}: repeated DOM translation ${key}`);
  }
}
const html=fs.readFileSync('frontend/index.html','utf8');
// Check actual source call sites as well as dictionary-to-dictionary coverage.
// A table can be complete while a newly added editor label is absent from it.
const tables=new Set(['i18n.js','ru.js','zh-cn.js','ui-copy.js','armor-copy.js','support-copy.js','smoke-copy.js','rider-copy.js','passenger-copy.js','locale-extra.js']);
for(const file of fs.readdirSync('frontend').filter(name=>name.endsWith('.js')&&!tables.has(name))){
  const code=fs.readFileSync('frontend/'+file,'utf8');
  for(const m of code.matchAll(/\bt\(\s*(['"])((?:\\.|(?!\1)[^\\])*?)\1\s*\)/g)){
    const value=m[2].replace(/\\([\\'"nrt])/g,(match,c)=>({n:'\n',r:'\r',t:'\t'}[c]||c));
    assert.ok(Object.hasOwn(EN,value)||translate(value,'ru')!==value,`Untranslated editor call in ${file}: ${value}`);
  }
}
for(const language of LANGUAGES)assert.ok(html.includes(`value="${language}"`),`selector: ${language}`);
const invariant=new Set(['3D','X','Y','Z','v','CURSED WORKSHOP','CW','Cursed Workshop','English','Español','简体中文','Русский','F','L','S','XYZ','Normal','Play','Global','Local']);
for(const m of [...html.matchAll(/>([^<>]+)</g),...html.matchAll(/(?:title|placeholder|aria-label)="([^"]+)"/g)]){
  const value=m[1].trim();if(!value||value.startsWith('import ')||invariant.has(value)||!/[A-Za-zÁÉÍÓÚáéíóúñ]/.test(value))continue;
  assert.notEqual(translate(value,'ru'),value,`Russian static label: ${value}`);
}
// Names, paths and numbers captured by dynamic messages are literal, not labels.
for(const [source,expected] of [
  ['Tiempo total: 05:30','Общее время: 05:30'],
  ['15 vehículos · 59 soldados · ger','Машин: 15 · солдат: 59 · ger'],
  ['Vehículo de tu partida · 5 tripulantes','Машина из сохранения · экипаж: 5'],
  ['71 piezas · 39.655 triángulos','Деталей: 71 · треугольников: 39.655'],
  ['Torreta de MACE T-55A','Башня от MACE T-55A'],
  ['Guardada en C:\\Users\\Chuck\\save.sav','Сохранено в C:\\Users\\Chuck\\save.sav'],
  ['Etapas completadas: 2/7','Завершено этапов: 2/7'],
])assert.equal(translate(source,'ru'),expected);
const paragraph='¿Eliminar «{name}» de la lista de la Tienda de la Conquista?\n\nDeja de venderse y no vuelve a aparecer en esta lista. Los vehículos que ya tenés en tus partidas se conservan.';
for(const language of ['en','zh-CN','ru']){
  const text=translate(paragraph,language);
  assert.notEqual(text,paragraph);assert.ok(text.includes('\n\n')&&text.includes('{name}'));
  const detail='Este ensamblaje no tiene un puesto de comandante. No se agregan tripulantes.';
  assert.equal(englishText(translate(detail,language)),EN[detail],`${language}: English support reports`);
  const nested=translate('Esta combinación no se puede construir: '+detail,language);
  assert.ok(!nested.includes(detail),`${language}: nested reason`);
  assert.equal(translate('panzer3e / cmdmg_yaw / MACE',language),'panzer3e / cmdmg_yaw / MACE');
}
for(const name of Object.keys(FINISH_NAMES))for(const language of LANGUAGES){
  const text=translate(name,language);assert.ok(text.trim());
  if(language!=='en')assert.notEqual(text,name,`${language}: paint ${name}`);
}
// Persisted preference wins over Windows, which wins over browser fallback.
uiStorage.setItem('cw.lang','');uiStorage.setItem('cw.systemLang','');
for(const [locale,expected] of [['ru-RU','ru'],['ru-BY','ru'],['es-AR','es'],['zh-Hans','zh-CN'],['de-DE','en']]){
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{language:locale}});
  assert.equal(initialLang(),expected);
}
uiStorage.setItem('cw.systemLang','ru');assert.equal(initialLang(),'ru');
uiStorage.setItem('cw.lang','es');assert.equal(initialLang(),'es');
await assert.rejects(setLang('unknown'),RangeError);
console.log(`Localization: ${Object.keys(EN).length} keys, ES/EN/Chinese/Russian labels, placeholders, dynamic messages, multiline text, literal resource IDs, English reports and locale selection passed.`);
