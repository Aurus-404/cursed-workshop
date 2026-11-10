import {t} from './i18n.js';

export function shopEditor({state,$,api,operation,artwork,svgVehicle,reportError,refreshConfig,reloadText}) {
  let controls=[];
  const dirty=row=>row.archived!==row.originalArchived || row.box.checked!==row.originalOn || (row.box.checked && (row.needsUpdate || row.price.value!==row.originalPrice || row.reset));
  const refresh=()=>{$('shop-apply').disabled=!controls.some(dirty);};
  async function open() {
    const status=$('shop-status');status.textContent=t('Calculando precios…');
    artwork.clear();controls=[];$('shop-list').replaceChildren();$('shop-apply').disabled=true;$('shop-dialog').showModal();
    try {
      const data=await api('/api/shop');status.textContent='';
      if(!data.vehicles?.length){status.textContent=t('Todavía no hay vehículos creados con el taller.');return;}
      let archiveList;
      for(const vehicle of data.vehicles){
        const row=document.createElement('div');row.className='shop-row';
        const box=document.createElement('input');box.type='checkbox';box.checked=vehicle.on;box.setAttribute('aria-label',vehicle.name);
        const icon=document.createElement('span');icon.className='shop-icon';icon.setAttribute('aria-hidden','true');icon.innerHTML=svgVehicle;artwork.attach(icon,vehicle,state.artRevision||0);
        const name=document.createElement('strong');name.textContent=vehicle.name;name.title=(vehicle.parts||[]).join(' + ');
        const field=document.createElement('label');field.className='shop-price';
        const title=document.createElement('span');title.textContent=t('Precio');
        const price=document.createElement('input');price.type='number';price.min='0';price.step='1';price.value=String(vehicle.cost??0);price.disabled=!box.checked;
        price.setAttribute('aria-label',t('Precio')+' · '+vehicle.name);
        const suggested=vehicle.suggested_cost??vehicle.cost??0;
        const hint=document.createElement('small');hint.textContent=t('Sugerido')+': '+suggested;
        const reset=document.createElement('button');reset.type='button';reset.className='text-button';reset.textContent=t('Usar sugerido');
        const remove=document.createElement('button');remove.type='button';remove.className='text-button';
        field.append(title,price,hint);row.append(box,icon,name,field,reset,remove);
        if(vehicle.archived){
          if(!archiveList){
            archiveList=document.createElement('details');archiveList.className='shop-archive';
            const summary=document.createElement('summary');summary.textContent=t('Quitados de la lista');
            archiveList.append(summary);
          }
          archiveList.append(row);
        }else $('shop-list').append(row);
        const control={id:vehicle.id,name:vehicle.name,box,price,originalOn:box.checked,originalPrice:price.value,
          archived:!!vehicle.archived,originalArchived:!!vehicle.archived,
          customCost:vehicle.custom_cost,needsUpdate:vehicle.needs_update,reset:false};controls.push(control);
        const archiveState=()=>{
          box.disabled=control.archived;price.disabled=control.archived||!box.checked;reset.disabled=control.archived;
          remove.textContent=t(control.archived?'Restaurar en la lista':'Quitar de la lista');
          remove.title=t('Quitar de la lista también retira la venta. Los vehículos de las partidas y sus recursos se conservan.');
        };
        remove.onclick=()=>{control.archived=!control.archived;box.checked=control.archived?false:control.originalOn;archiveState();refresh();};
        archiveState();
        box.onchange=()=>{price.disabled=!box.checked;refresh();};
        price.oninput=()=>{control.reset=false;refresh();};
        reset.onclick=()=>{price.value=String(suggested);control.reset=true;refresh();};
      }
      if(archiveList)$('shop-list').append(archiveList);
      refresh();
    }catch(error){$('shop-dialog').close();reportError(error);}
  }
  $('shop-button').onclick=()=>{if(!state.busy)return open();};
  $('shop-dialog').addEventListener('close',()=>artwork.clear());
  $('shop-apply').onclick=async()=>{
    if(state.busy)return;
    const changes={};
    for(const row of controls.filter(dirty)){
      const cost=Number(row.price.value);
      if(row.box.checked && (!row.price.value.trim() || !Number.isInteger(cost) || cost<0 || cost>2147483647)){
        $('shop-status').textContent=t('El precio debe ser un número entero de 0 a 2147483647.');row.price.focus();return;
      }
      const automatic=row.reset || (row.price.value===row.originalPrice && row.customCost==null);
      changes[row.id]={on:row.box.checked,cost:automatic||!row.box.checked?null:cost};
      if(row.archived!==row.originalArchived)changes[row.id].archived=row.archived;
    }
    if(!Object.keys(changes).length)return;
    $('shop-dialog').close();
    try {
      const result=await operation('/api/shop',{changes},'Actualizando la tienda');
      const names=Object.fromEntries(controls.map(row=>[row.id,row.name]));
      const lines=Object.entries(changes).map(([id,value])=>'· '+names[id]+': '+(value.archived?t('quitado de la lista'):value.on?
        `${t('en venta por')} ${result.offers?.[id]?.cost??value.cost??0}`:t('fuera de la tienda')));
      $('error-title').textContent=t('Tienda actualizada');
      $('error-message').textContent=lines.join('\n')+reloadText(result.reload_steps)+(result.backup?`\n\n${t('Copia del mod anterior')}: ${result.backup}`:'');
      $('error-dialog').showModal();await refreshConfig();
    }catch(error){reportError(error);}
  };
  return {open};
}
