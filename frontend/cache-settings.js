import {t} from './i18n.js';

export function cacheSettings({$,api,operation,busy=()=>false,confirm=globalThis.confirm}){
  let pending=false;
  const amount=bytes=>(Math.max(0,Number(bytes)||0)/1024/1024).toFixed(1)+' MiB';
  const size=result=>t('Caché en disco: {size} · {count} archivos').replace('{size}',amount(result.bytes)).replace('{count}',result.files);
  async function refresh(){
    if(pending)return;
    pending=true;$('clear-cache').disabled=true;
    $('cache-status').textContent=t('Calculando tamaño de la caché…');
    try{
      const result=await api('/api/cache');
      $('cache-status').textContent=size(result);
    }catch{
      $('cache-status').textContent=t('No se pudo calcular el tamaño de la caché.');
    }finally{pending=false;$('clear-cache').disabled=busy();}
  }
  $('clear-cache').onclick=async()=>{
    if(pending||busy())return;
    if(!confirm(t('¿Limpiar la caché del editor? Se borrarán texturas convertidas, catálogos e índices regenerables. Se conservan vehículos, partidas, copias de seguridad y ajustes. La próxima carga puede tardar más.')))return;
    pending=true;$('clear-cache').disabled=true;
    $('cache-status').textContent=t('Limpiando caché');
    try{
      const result=await operation('/api/clear-cache',{confirm:true},'Limpiando caché');
      $('cache-status').textContent=t('Caché limpiada: {size} · {count} archivos eliminados.').replace('{size}',amount(result.removed_bytes)).replace('{count}',result.removed_files)
        +(result.skipped?' '+t('Algunos archivos en uso o protegidos se conservaron.'): '');
    }catch{
      $('cache-status').textContent=t('No se pudo completar la limpieza. Podés reintentar.');
    }finally{pending=false;$('clear-cache').disabled=busy();}
  };
  return {refresh};
}
