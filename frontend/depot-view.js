import {t} from './i18n.js';
export const DEPOT_TYPES={hull:'Chasis',turret:'Torreta',gun:'Cañón / arma',weapon_mount:'Montura de arma'};
export function depotIcon(type){
  const paths={hull:'M8 37h48v12H8z M12 49v5h40v-5 M15 36l5-9h24l7 9',
    turret:'M12 43h40v8H12z M19 43V24l8-6h17l5 7v18 M48 27h14v6H48',
    gun:'M5 22h45v7H5z M42 29l11 11 M40 29L27 45 M20 45h34 M32 46a7 7 0 1 0 .1 0 M54 46a7 7 0 1 0 .1 0',
    weapon_mount:'M28 25h23v6H28z M42 25V14h7v11 M35 31v20 M21 52h28 M27 40h16'};
  return '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round" aria-hidden="true"><path d="'+(paths[type]||paths.hull)+'"/></svg>';
}
export function depotRows(items){return (items||[]).map(item=>({...item,depot:item.id,kind:'part'}));}
export function matchesDepot(row,filters){return (!filters.type||(row.component_type||row.part)===filters.type)&&(!filters.nation||(row.nation||'unknown')===filters.nation)&&(!filters.origin||(row.origin||'unknown')===filters.origin);}
export function depotFilters($,items,visible){
  $('depot-bar').classList.toggle('hidden',!visible);if(!visible)return;
  for(const [id,values,all] of [
    ['depot-type',items.map(r=>r.component_type||r.part),'Todos los tipos'],
    ['depot-nation',items.map(r=>r.nation||'unknown'),'Todas las naciones'],
    ['depot-origin',items.map(r=>r.origin||'unknown'),'Todas las procedencias']]){
    const select=$(id),previous=select.value,keys=[...new Set(values)].sort();
    select.replaceChildren(new Option(t(all),''),...keys.map(v=>new Option(t(DEPOT_TYPES[v]||(v==='unknown'?'Desconocido':v)),v)));
    select.value=keys.includes(previous)?previous:'';
  }
}
