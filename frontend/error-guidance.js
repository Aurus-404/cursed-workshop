export function errorGuidance(error){
  const detail=error?.detail||error?.message||String(error);
  if(/NetworkError|Failed to fetch|Local request failed|programa se cerró|server.*closed/i.test(detail))return {summary:'Se perdió la conexión con el editor.',action:'Volvé a abrir Cursed Workshop. Tu último borrador guardado se puede recuperar al iniciar.',detail};
  if(/memory|memoria|allocation/i.test(detail))return {summary:'No hay suficiente memoria para completar la operación.',action:'Activá el modo de bajo uso de RAM en Configuración y reducí la resolución de las texturas. Después reintentá.',detail};
  if(/game.*running|juego.*(abierto|cerr)|menú principal/i.test(detail))return {summary:'El juego está usando los archivos que se necesitan actualizar.',action:'Volvé al menú principal del juego o cerralo y reintentá.',detail};
  if(/missing|not found|no se encontr|faltan|requires.*mod/i.test(detail))return {summary:'Falta un recurso necesario para esta operación.',action:'Revisá la carpeta del juego y los mods activos en Configuración. Si persiste, copiá el diagnóstico.',detail};
  return {summary:'No se pudo completar la operación.',action:'Revisá el detalle. Podés corregir la selección y reintentar; si el problema continúa, copiá el diagnóstico para reportarlo.',detail};
}
