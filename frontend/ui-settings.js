import {localRequest} from './local-request.js';

// Memory fallback also works when a webview disables browser storage.
const cache=new Map();
export const uiStorage={
  getItem(key){if(cache.has(key))return cache.get(key);try{return globalThis.localStorage?.getItem(key)??null;}catch{return null;}},
  setItem(key,value){cache.set(key,String(value));try{globalThis.localStorage?.setItem(key,String(value));}catch{}},
};
let token='',tail=Promise.resolve();
export async function loadUISettings(){
  token=(await localRequest('/api/session',undefined,'')).token;
  const saved=await localRequest('/api/ui-preferences',undefined,token);
  if(saved.language)uiStorage.setItem('cw.lang',saved.language);
  // Windows display language, only a default until the player chooses one.
  if(saved.system_language)uiStorage.setItem('cw.systemLang',saved.system_language);
  // Server is authoritative; missing values use defaults, not another origin's cache.
  uiStorage.setItem('cw.appearance',JSON.stringify({theme:saved.theme,size:saved.size,motion:saved.motion,help:saved.help}));
  uiStorage.setItem('cw.completionSound',saved.sound===false?'off':'on');
}
export function saveUISettings(patch){
  // Keep rapid changes ordered, and let the next user action recover after an error.
  const result=tail.catch(()=>{}).then(()=>localRequest('/api/ui-preferences',patch,token));
  tail=result;return result;
}
