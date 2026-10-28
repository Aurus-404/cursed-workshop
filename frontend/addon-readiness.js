import {t} from './i18n.js';

export function showAddonReadiness($, result){
  const panel=$('addon-readiness');
  if(!panel)return;
  panel.classList.toggle('hidden',!result?.required||!!result?.ready);
  panel.classList.toggle('verified',!!result?.ready);
  const summaries={waiting_for_game:'Vehículo guardado · comprobación pendiente al abrir el juego.',publication_pending:'Instalación pendiente de completar.',missing:'Falta el mod local de vehículos.'};
  $('addon-readiness-summary').textContent=t(summaries[result?.status]||'Mod de vehículos pendiente de comprobar.');
  $('addon-readiness-message').textContent=t(result?.message||'')+(result?.recovery_backup?'\n'+t('Copia de recuperación:')+' '+result.recovery_backup:'');
  const toggle=$('addon-readiness-toggle'),details=$('addon-readiness-details');
  if(!result?.required||result?.ready){toggle.setAttribute('aria-expanded','false');details.classList.toggle('hidden',true);}
  toggle.onclick=()=>{const open=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(open));details.classList.toggle('hidden',!open);};
}

export function installMessage(done){
  return t('El vehículo se guardó en la partida. Eso todavía no confirma que el juego cargó su mod.')+'\n\n'+t(done.instructions||'')+(done.recovery_backup?'\n\n'+t('Copia de recuperación:')+' '+done.recovery_backup:'');
}
