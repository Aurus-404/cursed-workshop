// Local authenticated artwork, requested only as a card becomes visible.
export function unitArtwork({document=globalThis.document,fetch=globalThis.fetch,token,root,Observer=globalThis.IntersectionObserver,URL=globalThis.URL}){
  const cache=new Map(),pending=new Map(),loaders=new WeakMap(),queue=[];
  let bytes=0,active=0;
  const observer=Observer?new Observer(entries=>{for(const row of entries)if(row.isIntersecting){observer.unobserve(row.target);schedule(row.target);}},{root,rootMargin:'48px 0px'}):null;
  function schedule(image){
    queue.push(image);pump();
  }
  function pump(){
    while(active<3&&queue.length){
      const image=queue.shift();
      if(image.isConnected===false)continue;
      active++;
      loaders.get(image)().finally(()=>{active--;pump();});
    }
  }
  function clear(){observer?.disconnect();queue.length=0;}
  async function artwork(key,url){
    if(cache.has(key)){
      const blob=cache.get(key);cache.delete(key);cache.set(key,blob);return blob;
    }
    if(pending.has(key))return pending.get(key);
    const request=fetch(url,{headers:{'X-Workshop-Token':token()}})
      .then(response=>{if(!response.ok)throw Error('No artwork');return response.blob();})
      .then(blob=>{
        cache.set(key,blob);bytes+=blob.size||0;
        while(bytes>16*1024*1024||cache.size>128){
          const oldest=cache.keys().next().value;bytes-=cache.get(oldest).size||0;cache.delete(oldest);
        }
        return blob;
      }).finally(()=>pending.delete(key));
    pending.set(key,request);return request;
  }
  function attach(icon,unit,revision=0){
    const image=document.createElement('img');image.alt='';icon.append(image);
    const template=unit.template||unit.id||'',key=template+'|'+revision;
    const load=async()=>{
      if(image.isConnected===false)return;
      try{
        const blob=await artwork(key,'/api/unit-image?template='+encodeURIComponent(template)+'&v='+revision);
        if(image.isConnected===false)return;
        const url=URL.createObjectURL(blob);
        image.onload=()=>{icon.classList.add('has-image');URL.revokeObjectURL(url);};
        image.onerror=()=>{image.remove();URL.revokeObjectURL(url);};image.src=url;
      }catch{image.remove();}
    };
    loaders.set(image,load);if(observer)observer.observe(image);else schedule(image);
  }
  return {attach,clear};
}
