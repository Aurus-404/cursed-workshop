// Keep one clock and one busy lock across the complete existing transaction flow.
export async function buildVehicle(progress,{generate,portrait,install,reload}){
  let built,done,portraitWarning='';
  await progress.run([
    {label:'Construyendo vehículo',run:async()=>{built=await generate();}},
    {label:'Fotografiando el modelo armado',run:async()=>{
      try{const rendered=await portrait(built.new_template);portraitWarning=rendered.warnings.join('\n');}
      catch(error){portraitWarning='No se pudo fotografiar el modelo armado. Se conserva el retrato de respaldo del vehículo original.'+'\n'+error.message;}
    }},
    {label:'Guardando en tu partida',run:async()=>{done=await install();}},
    {label:'Recargando campaña y unidades',run:async()=>{
      try{await reload();}
      catch(error){throw new Error('El vehículo ya se guardó, pero no se pudo recargar la campaña. Volvé a abrirla con «Abrir».'+'\n'+error.message);}
    }},
  ],{notice:'Vehículo construido'});
  return {done,portraitWarning,elapsedMs:progress.elapsedMs};
}
