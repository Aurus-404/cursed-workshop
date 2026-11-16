import {t} from './i18n.js';
import {DEPOT_TYPES} from './depot-view.js';

export function dismantling({$,state,loading,pollJob,readSave,status,reportError}){
  return {async open(unit){
    if(state.busy||!state.campaign)return;
    if(!unit.eid||unit.kind!=='vehicle'){reportError(new Error(t('Elegí un vehículo propio para desmantelar.')));return;}
    const campaign=state.campaign,request={eid:unit.eid,source_path:campaign.source_path,source_hash:campaign.source_hash};
    try{
      const plan=await loading('Revisando componentes',()=>pollJob('/api/dismantle-preview',request));
      if(state.campaign!==campaign)return;
      $('dismantle-name').textContent=unit.label||unit.template;
      $('dismantle-parts').replaceChildren(...plan.parts.map(part=>{
        const row=document.createElement('li');row.textContent=t(DEPOT_TYPES[part.component_type||part.part])+' · '+part.label;return row;
      }));
      $('dismantle-integrated').textContent=plan.integrated.length?t('Las armas que no se pueden separar conservan su pieza de montaje.') : '';
      $('dismantle-confirm').onclick=async()=>{
        if(state.busy)return;
        if(state.campaign!==campaign){reportError(new Error(t('La campaña cambió. Volvé a abrirla antes de trasladar el vehículo.')));return;}
        $('dismantle-dialog').close();
        try{await loading('Desmantelando vehículo',async()=>{await pollJob('/api/dismantle-vehicle',request);await readSave(campaign.source_path);});status(t('Componentes guardados en el Depósito.'));}
        catch(error){reportError(error);}
      };
      $('dismantle-dialog').showModal();
    }catch(error){reportError(error);}
  }};
}
