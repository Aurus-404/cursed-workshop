// Coalesce read-only previews. Never use for installation or save writes.
export function latestTask(task, delay=200) {
  let pending=null;
  const cancel=()=>{if(!pending)return;const old=pending;pending=null;clearTimeout(old.timer);old.controller.abort();old.reject(new DOMException('Preview superseded','AbortError'));};
  const run=(...args)=>{cancel();return new Promise((resolve,reject)=>{
    const item={controller:new AbortController(),reject,timer:null};pending=item;
    item.timer=setTimeout(async()=>{try{const value=await task(item.controller.signal,...args);if(pending===item){pending=null;resolve(value);}}
      catch(error){if(pending===item){pending=null;reject(error);}}},delay);
  });};
  return {run,cancel};
}
