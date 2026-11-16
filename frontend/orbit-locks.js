// Shared by viewport tools so releasing one interaction cannot unlock another.
const locks=new WeakMap();
export function setOrbitLock(orbit,owner,active){
  let state=locks.get(orbit);
  if(active){
    if(!state){state={owners:new Set(),enabled:orbit.enabled};locks.set(orbit,state);}
    state.owners.add(owner);orbit.enabled=false;
  }else if(state?.owners.delete(owner)){
    if(state.owners.size)orbit.enabled=false;
    else{orbit.enabled=state.enabled;locks.delete(orbit);}
  }
}
