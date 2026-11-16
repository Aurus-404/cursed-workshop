import {t} from './i18n.js';
// View-only masks: neither recipes nor exports receive hidden/locked flags.
export function pieceVisibility({groups,data=()=>({}),changed=()=>{}}){
  const hidden=new Set(),locked=new Set();let isolated=null,sequence=0;const bases=new WeakMap(),identities=new WeakMap();
  function identity(object){if(!identities.has(object))identities.set(object,++sequence);return identities.get(object);}
  function rows(){
    const result=[],names={hull:'Chasis',turret:'Torreta',extra_armor:'Blindaje adicional',smoke_launchers:'Lanzadores de humo',rider_mgs:'Armas de pasajeros',passenger_seats:'Pasajeros',towing:'Remolque',charge:'Carga explosiva',commander_mg:'Arma del comandante'};
    for(const [part,group] of Object.entries(groups)){
      if(['hull','turret'].includes(part)){
        const bones=new Map();group.traverse(node=>{if(node.userData?.mesh){const bone=node.userData.mesh.bone||node.name;if(!bones.has(bone))bones.set(bone,[]);bones.get(bone).push(node);}});
        for(const [bone,objects] of bones)result.push({key:part+':'+bone,label:t(names[part]||part)+' · '+bone,objects});
      }else for(const [index,object] of group.children.entries()){
        const value=data()[part],slot=object.userData.armorIndex??object.userData.smokeIndex??object.userData.riderIndex??object.userData.passengerIndex??index;
        const row=object.userData.candidate?null:Array.isArray(value)?value[slot]:value;
        result.push({key:part+':'+identity(row&&typeof row==='object'?row:object),label:t(names[part]||part)+' · '+(index+1),objects:[object]});
      }
    }
    return result;
  }
  const under=(object,parent)=>{for(let n=object;n;n=n.parent)if(n===parent)return true;return false;};
  function keysFor(object){return rows().filter(row=>row.objects.some(n=>under(n,object)||under(object,n))).map(row=>row.key);}
  function restore(){for(const row of rows())for(const object of row.objects){if(bases.has(object)){object.visible=bases.get(object);bases.delete(object);}}}
  function apply(){for(const row of rows())for(const object of row.objects){if(!bases.has(object))bases.set(object,object.visible);object.visible=bases.get(object)&&!hidden.has(row.key)&&(!isolated||isolated.has(row.key));}}
  function toggle(set,keys){if(!keys.length)return;const remove=keys.every(k=>set.has(k));for(const key of keys)remove?set.delete(key):set.add(key);changed();}
  return {rows,restore,apply,hidden,locked,
    canEdit(object){return !object||!keysFor(object).some(k=>locked.has(k)||hidden.has(k)||(isolated&&!isolated.has(k)));},
    hide(object){toggle(hidden,keysFor(object));},lock(object){toggle(locked,keysFor(object));},
    toggleHidden(key){toggle(hidden,[key]);},toggleLocked(key){toggle(locked,[key]);},
    isolate(object){const keys=keysFor(object);if(!keys.length)return;isolated=isolated?null:new Set(keys);changed();},
    showAll(){hidden.clear();isolated=null;changed();},
    reset(){restore();hidden.clear();locked.clear();isolated=null;changed();},
  };
}
