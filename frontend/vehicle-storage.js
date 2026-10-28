/** Whole-unit transfers. Capture campaign identity when opening the confirmation. */
export function vehicleStorage({$,state,t,pollJob,loading,readSave,status,reportError}){
  function transfer(button,dialog,route,body,label,done){
    const campaign=state.campaign;
    const request={...body,source_hash:campaign.source_hash,source_path:campaign.source_path};
    button.onclick=async()=>{
      if(state.busy)return;
      if(state.campaign!==campaign){reportError(new Error(t('La campaña cambió. Volvé a abrirla antes de trasladar el vehículo.')));return;}
      $(dialog).close();
      try{
        await loading(label,async()=>{await pollJob(route,request);await readSave(campaign.source_path);},done);
        status(t(done));
      }catch(error){reportError(error);}
    };
  }
  return {
    store(unit){
      if(state.busy||!state.campaign)return;
      const template=String(unit.template||'').replace(/\([^()]*\)$/,'');
      if(unit.kind!=='vehicle'){
        reportError(new Error(t('Elegí un vehículo de la campaña para guardarlo en el Almacén.')));return;
      }
      $('delete-name').textContent=unit.label||template;
      transfer($('delete-confirm'),'delete-dialog','/api/store-vehicle',{eid:unit.eid},'Guardando en el Almacén','Vehículo y tripulación guardados en el Almacén.');
      $('delete-dialog').showModal();
    },
    open(unit){
      if(state.busy||!state.campaign)return;
      $('storage-name').textContent=unit.label||unit.template;
      $('storage-crew-count').textContent=String(unit.crew.length);
      $('storage-origin').textContent=[unit.origin_campaign,unit.origin_army].filter(Boolean).join(' · ');
      $('storage-destination').textContent=[state.campaign.campaign_name,state.campaign.army].filter(Boolean).join(' · ')||state.campaign.source_path;
      $('storage-recovery-campaign').textContent=unit.pending_campaign||'';
      $('storage-other-campaign').classList.toggle('hidden',!unit.pending||unit.recoverable!==false);
      $('storage-pending').classList.toggle('hidden',!unit.pending);
      $('storage-restore').classList.toggle('hidden',!!unit.pending);
      $('storage-recover').classList.toggle('hidden',!unit.pending);
      $('storage-recover').disabled=!!unit.pending&&unit.recoverable===false;
      transfer($('storage-restore'),'storage-dialog','/api/restore-stored-vehicle',{id:unit.id},'Devolviendo a la campaña','Vehículo y tripulación devueltos a la reserva de la campaña.');
      transfer($('storage-recover'),'storage-dialog','/api/recover-stored-vehicle',{id:unit.id},'Recuperando traslado','Traslado recuperado.');
      $('storage-dialog').showModal();
    }
  };
}
