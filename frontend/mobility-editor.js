import {t} from './i18n.js';

// Chassis mobility: the parameters of the hull's own mobility macro
// (("mobility_tank" speed(40) reverse(8) traverse(30) ...)). Only changed
// values are saved; the game recomputes normal and sprint speed, acceleration
// and turning from them. A chassis shows only the parameters it has.
export const MOBILITY={speed:[1,200],reverse:[1,100],traverse:[1,360],speedatmaxturn:[1,200],power:[1,5000],track:[0.1,10],fuel:[1,10000],range:[1,10000]};

export function mobilityEditor({state,$,remember,refreshCheck}){
  const native=()=>state.previews.hull?.mobility?.values||null;
  function refresh(){
    const values=native(),field=$('mobility-field');
    field.classList.toggle('hidden',!values);if(!values)return;
    for(const name of Object.keys(MOBILITY)){
      const input=$('mobility-'+name),label=input.closest?.('label');
      label?.classList.toggle('hidden',!(name in values));
      if(name in values)input.value=String(state.transform.mobility?.[name]??values[name]);
    }
    $('mobility-reset').disabled=!state.transform.mobility;
  }
  function change(name){
    const values=native(),input=$('mobility-'+name);if(!values||!(name in values))return;
    const value=Number(input.value),[low,high]=MOBILITY[name];
    if(!Number.isFinite(value)||value<low||value>high){
      $('mobility-message').textContent=t('Usá un valor entre ')+low+t(' y ')+high+'.';refresh();return;
    }
    $('mobility-message').textContent='';remember();
    const spec={...state.transform.mobility};
    if(Math.abs(value-values[name])<1e-9)delete spec[name];else spec[name]=value;
    if(Object.keys(spec).length)state.transform.mobility=spec;else delete state.transform.mobility;
    refresh();refreshCheck();
  }
  for(const name of Object.keys(MOBILITY))$('mobility-'+name).onchange=()=>change(name);
  $('mobility-reset').onclick=()=>{if(!state.transform.mobility)return;remember();delete state.transform.mobility;
    $('mobility-message').textContent='';refresh();refreshCheck();};
  return {refresh};
}
