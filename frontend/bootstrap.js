import {loadUISettings} from './ui-settings.js';

try{
  await loadUISettings();
  await import('./app.js');
}catch(error){
  // No game operation can run from a partially initialized interface.
  document.body.replaceChildren();
  const message=document.createElement('p'),retry=document.createElement('button');
  message.textContent='No se pudo iniciar la interfaz / Could not start the interface / 无法启动界面: '+error.message;
  retry.textContent='Reintentar / Retry / 重试';retry.onclick=()=>location.reload();
  document.body.append(message,retry);
}
