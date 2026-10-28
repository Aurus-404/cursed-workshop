// Counts completed stages, not estimated work or remaining time.
export function elapsedTime(milliseconds){
  const seconds=Math.floor(Math.max(0,milliseconds)/1000),minutes=Math.floor(seconds/60);
  return `${String(minutes).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
}

export function operationProgress({render,busy,notify=()=>{},now=()=>performance.now(),schedule=setInterval,unschedule=clearInterval}){
  let active=false,lastElapsedMs=0,controller=null,cancellable=false;
  return {
    get active(){return active;},
    get elapsedMs(){return lastElapsedMs;},
    get signal(){return active?controller?.signal:undefined;},
    get cancellable(){return active&&cancellable&&!controller.signal.aborted;},
    setCancellable(value){cancellable=!!value;},
    cancel(){if(!active||!cancellable)return false;controller.abort();return true;},
    async run(stages,{notice}={}){
      if(active)throw new Error('Esperá a que termine la operación actual.');
      active=true;lastElapsedMs=0;controller=new AbortController();cancellable=false;
      const started=now();let completed=0,label=stages[0]?.label||'Cargando…',timer;
      const update=()=>{
        lastElapsedMs=Math.max(0,now()-started);
        render({label,completed,total:stages.length,elapsedMs:lastElapsedMs,active,cancellable:active&&cancellable&&!controller.signal.aborted});
      };
      try{
        busy(true,label);update();timer=schedule(update,250);
        let result;
        for(const stage of stages){
          if(controller.signal.aborted)throw new DOMException('Operation cancelled','AbortError');
          label=stage.label;cancellable=false;update();result=await stage.run(controller.signal);
          if(controller.signal.aborted)throw new DOMException('Operation cancelled','AbortError');
          completed++;update();
        }
        active=false;update();
        // A blocked audio device or a notification must never turn a saved vehicle into an error.
        if(notice){try{notify(notice,lastElapsedMs);}catch{}}
        return result;
      }finally{
        if(timer!==undefined)unschedule(timer);
        active=false;lastElapsedMs=Math.max(0,now()-started);busy(false);
      }
    },
  };
}

// Local synthesized chime: no sound files, network, OS permissions or external dependencies.
export function completionSound({contextFactory=()=>new (globalThis.AudioContext||globalThis.webkitAudioContext)(),storage=globalThis.localStorage}={}){
  let context,enabled=true;
  try{enabled=storage?.getItem('cw.completionSound')!=='off';}catch{}
  return {
    get enabled(){return enabled;},
    setEnabled(value){enabled=!!value;try{storage?.setItem('cw.completionSound',enabled?'on':'off');}catch{}},
    arm(){
      if(!enabled)return;
      try{context??=contextFactory();if(context.state==='suspended')Promise.resolve(context.resume()).catch(()=>{});}catch{}
    },
    play(){
      if(!enabled||context?.state!=='running')return false;
      try{
        for(const [offset,frequency] of [[0,660],[.18,880]]){
          const oscillator=context.createOscillator(),gain=context.createGain(),start=context.currentTime+offset;
          oscillator.type='sine';oscillator.frequency.value=frequency;
          gain.gain.setValueAtTime(0,start);gain.gain.linearRampToValueAtTime(.08,start+.015);gain.gain.linearRampToValueAtTime(0,start+.16);
          oscillator.connect(gain);gain.connect(context.destination);
          oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
          oscillator.start(start);oscillator.stop(start+.17);
        }
        return true;
      }catch{return false;}
    },
  };
}
