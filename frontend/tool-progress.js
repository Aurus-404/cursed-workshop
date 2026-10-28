// Local inspector work has its own lock; it must not silently look frozen.
export function toolProgress({render,now=()=>performance.now(),schedule=setInterval,unschedule=clearInterval}){
  let timer,started=0,label='',active=false,progress=null;
  const update=()=>render({active,label,elapsedMs:Math.max(0,now()-started),progress});
  return {
    start(text){if(timer!==undefined)unschedule(timer);started=now();label=text;progress=null;active=true;update();timer=schedule(update,250);},
    stage(text){if(!active)return;label=text;progress=null;update();},
    observe(job){if(!active)return;progress=job.progress||null;update();},
    finish(){if(timer!==undefined)unschedule(timer);timer=undefined;active=false;progress=null;update();},
    active(){return active;},
  };
}
