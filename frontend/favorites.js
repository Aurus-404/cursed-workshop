import {uiStorage,saveUISettings} from './ui-settings.js';

// Starred options of every list, keyed by the list's id and saved with the
// workspace preferences, so they survive restarts and a new HTTP port.
const KEY='cw.favorites';
function all(){
  try{const value=JSON.parse(uiStorage.getItem(KEY)||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}
  catch{return {};}
}
export function favorites(list){return new Set(all()[list]||[]);}
export function toggleFavorite(list,value,save=saveUISettings){
  const every=all(),starred=new Set(every[list]||[]),on=!starred.has(value);
  if(on)starred.add(value);else starred.delete(value);
  if(starred.size)every[list]=[...starred];else delete every[list];
  uiStorage.setItem(KEY,JSON.stringify(every));
  // Saving is best effort: the star stays for this session if the write fails.
  Promise.resolve().then(()=>save({favorites:every})).catch(()=>{});
  return on;
}
