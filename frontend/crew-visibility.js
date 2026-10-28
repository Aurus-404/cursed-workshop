import {t} from './i18n.js';

// Role IDs are the compiler's final crew places, not source bone names. Keep
// hull and donor origins separate when both sources started with "gunner".
export function visibilityKey(state){
  const tr=state.transform||{};
  return JSON.stringify([state.hull?.template,state.turret?.template,state.mounts,
    tr.assembly_draft,tr.gun_swap,tr.commander_only,!!tr.commander_mg,tr.crew_exclude||[],tr.crew_fixed||{}]);
}
export function visibilityRows(state){
  return state.crewVisibility?.key===visibilityKey(state)?state.crewVisibility.rows:[];
}
export function crewHidden(state,part,place){
  const hidden=new Set(state.transform.crew_hidden||[]);
  return visibilityRows(state).some(row=>hidden.has(row.place)&&
    (part==='commander_mg'?row.place==='commander':state.transform.assembly_draft&&!state.pendingTurret?row.place===place:row.part===part&&row.source_place===place));
}
export function crewVisibilityEditor({state,$,remember,applyShow,refreshCheck,roleName,detachHidden}){
  function render(){
    const field=$('crew-visibility-field'),list=$('crew-visibility-list');
    if(!field||!list)return;
    const rows=visibilityRows(state),hidden=new Set(state.transform.crew_hidden||[]);
    field.classList.toggle('hidden',!rows.length&&!hidden.size);list.replaceChildren();
    if(hidden.size){
      const reset=document.createElement('button');reset.className='small';
      reset.textContent=t('Restablecer visibilidad original');reset.disabled=state.busy;
      reset.onclick=()=>{remember();delete state.transform.crew_hidden;applyShow();render();refreshCheck();};
      list.append(reset);
    }
    for(const row of rows){
      const label=document.createElement('label');label.className='crew-row';
      const box=document.createElement('input');box.type='checkbox';box.checked=hidden.has(row.place);
      box.disabled=state.busy||!row.can_hide;
      box.title=t(row.native_internal?'Este puesto ya es interno; no tiene un modelo visible.':
        row.can_hide?'Oculta el modelo mientras ocupa el puesto. Conserva al tripulante y su función.':
        'No se pudo verificar el embarque de ese puesto.');
      box.setAttribute('aria-label',t('Ocultar al embarcar')+' · '+roleName(row.place)+' ('+row.place+')');
      const text=document.createElement('span');
      text.textContent=t(row.part==='turret'?'Módulo':'Chasis')+' · '+roleName(row.place)+' ('+row.place+')'+
        (row.native_internal?' · '+t('Interno'):hidden.has(row.place)?' · '+t('Oculto'):'');
      box.onchange=()=>{
        remember();const next=new Set(state.transform.crew_hidden||[]);
        if(box.checked)next.add(row.place);else next.delete(row.place);
        state.transform.crew_hidden=[...next].sort();
        detachHidden();applyShow();render();refreshCheck();
      };
      label.append(box,text);list.append(label);
    }
  }
  return {render,receive(rows){state.crewVisibility={key:visibilityKey(state),rows:rows||[]};applyShow();render();}};
}
