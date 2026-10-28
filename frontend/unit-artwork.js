// Local authenticated artwork, requested only as a card becomes visible.
export function unitArtwork({document=globalThis.document,fetch=globalThis.fetch,token,root,Observer=globalThis.IntersectionObserver,URL=globalThis.URL}){
  const cache=new Map(),loaders=new WeakMap();
  const observer=Observer?new Observer(entries=>{for(const row of entries)if(row.isIntersecting){observer.unobserve(row.target);loaders.get(row.target)?.();}},{root}):null;
  function clear(){observer?.disconnect();}
  function attach(icon,unit,revision=0){
    const image=document.createElement('img');image.alt='';icon.append(image);
    const template=unit.template||unit.id||'',key=template+'|'+revision;
    const load=async()=>{
      if(image.isConnected===false)return;
      try{
        if(!cache.has(key)){
          if(cache.size>=128)cache.delete(cache.keys().next().value);
          const request=fetch('/api/unit-image?template='+encodeURIComponent(template)+'&v='+revision,{headers:{'X-Workshop-Token':token()}})
            .then(response=>{if(!response.ok)throw Error('No artwork');return response.blob();});
          cache.set(key,request);
        }
        const blob=await cache.get(key);if(image.isConnected===false)return;
        const url=URL.createObjectURL(blob);
        image.onload=()=>{icon.classList.add('has-image');URL.revokeObjectURL(url);};
        image.onerror=()=>{image.remove();URL.revokeObjectURL(url);};image.src=url;
      }catch{image.remove();}
    };
    loaders.set(image,load);if(observer)observer.observe(image);else load();
  }
  return {attach,clear};
}
