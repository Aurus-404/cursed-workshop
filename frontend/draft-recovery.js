// Serialized writes prevent a slower autosave from replacing a newer draft.
export function draftRecovery({api,snapshot,enabled=()=>true,ready=()=>true,onStatus=()=>{}}){
  let last='',tail=Promise.resolve(),suspended=true;
  return {
    async offer(){const value=await api('/api/editor-draft');last=JSON.stringify(value.draft);return value;},
    resume(){suspended=false;},pause(){suspended=true;},
    save(force=false){
      if(suspended||!ready()||(!force&&!enabled()))return tail;
      const value=snapshot();if(!value)return tail;const draft=structuredClone(value);
      const key=JSON.stringify(draft);if(!force&&key===last)return tail;
      last=key;
      const write=tail.catch(()=>{}).then(()=>api('/api/editor-draft',{draft}));
      tail=write.then(()=>onStatus('Borrador guardado automáticamente.')).catch(error=>{if(last===key)last='';onStatus('No se pudo guardar el borrador.');throw error;});
      return tail;
    },
    async discard(){await tail.catch(()=>{});await api('/api/editor-draft',{draft:null});last='null';},
  };
}
