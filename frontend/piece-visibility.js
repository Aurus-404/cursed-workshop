import {t} from './i18n.js';
// View-only masks: neither recipes nor exports receive hidden/locked flags.
// Pieces removed for the game are the exception: their bone names live in the
// recipe (removed_meshes), read through removed()/setRemoved(), and they are
// masked here too so the editor shows the vehicle as it will be built.
export function pieceVisibility({groups,data=()=>({}),changed=()=>{},removed=()=>[],setRemoved=()=>{},removablePart=()=>false}){
  const hidden=new Set(),locked=new Set();let isolated=null,sequence=0;const bases=new WeakMap(),identities=new WeakMap();
  function identity(object){if(!identities.has(object))identities.set(object,++sequence);return identities.get(object);}
  const seated=node=>{for(let n=node;n;n=n.parent)if(n.userData?.seat)return true;return false;};
  // What the vehicle's own model draws for a bone: not crew figures, not a
  // weapon carried on it (those keep their own tools).
  const drawn=node=>!!node.userData?.mesh?.path&&!node.userData.mesh.attached_weapon&&!seated(node);
  function rows(){
    const result=[],names={hull:'Chasis',turret:'Torreta',extra_armor:'Blindaje adicional',smoke_launchers:'Lanzadores de humo',rider_mgs:'Armas de pasajeros',mounted_weapons:'Armas sin torreta',passenger_seats:'Pasajeros',towing:'Remolque',charge:'Carga explosiva',commander_mg:'Arma del comandante'};
    const gone=new Set(removed()||[]);
    for(const [part,group] of Object.entries(groups)){
      if(['hull','turret'].includes(part)){
        const bones=new Map();group.traverse(node=>{if(node.userData?.mesh){const bone=node.userData.mesh.bone||node.name;if(!bones.has(bone))bones.set(bone,[]);bones.get(bone).push(node);}});
        for(const [bone,objects] of bones){
          const model=objects.filter(drawn),own=removablePart(part);
          const removable=own&&model.length>0;
          result.push({key:part+':'+bone,label:t(names[part]||part)+' · '+bone,objects,part,bone,model,removable,
            removed:removable&&gone.has(bone),reason:removable?null:model.length&&!own?'donor':'fitting'});
        }
      }else for(const [index,object] of group.children.entries()){
        const value=data()[part],slot=object.userData.armorIndex??object.userData.smokeIndex??object.userData.riderIndex??object.userData.mountedIndex??object.userData.passengerIndex??index;
        const row=object.userData.candidate?null:Array.isArray(value)?value[slot]:value;
        result.push({key:part+':'+identity(row&&typeof row==='object'?row:object),label:t(names[part]||part)+' · '+(index+1),objects:[object],part,model:[],removable:false,removed:false,reason:'fitting'});
      }
    }
    return result;
  }
  const under=(object,parent)=>{for(let n=object;n;n=n.parent)if(n===parent)return true;return false;};
  function keysFor(object){return rows().filter(row=>row.objects.some(n=>under(n,object)||under(object,n))).map(row=>row.key);}
  function restore(){for(const row of rows())for(const object of row.objects){if(bases.has(object)){object.visible=bases.get(object);bases.delete(object);}}}
  function apply(){for(const row of rows()){const gone=row.removed?new Set(row.model):null;
    for(const object of row.objects){if(!bases.has(object))bases.set(object,object.visible);object.visible=bases.get(object)&&!hidden.has(row.key)&&!gone?.has(object)&&(!isolated||isolated.has(row.key));}}}
  function toggle(set,keys){if(!keys.length)return;const remove=keys.every(k=>set.has(k));for(const key of keys)remove?set.delete(key):set.add(key);changed();}
  return {rows,restore,apply,hidden,locked,
    canEdit(object){return !object||!keysFor(object).some(k=>locked.has(k)||hidden.has(k)||(isolated&&!isolated.has(k)));},
    hide(object){toggle(hidden,keysFor(object));},lock(object){toggle(locked,keysFor(object));},
    toggleHidden(key){toggle(hidden,[key]);},toggleLocked(key){toggle(locked,[key]);},
    // Takes a piece off the built vehicle, or puts it back. Only pieces of the
    // vehicle's own model whose names the built vehicle keeps.
    toggleRemoved(key){const row=rows().find(r=>r.key===key);if(!row?.removable)return false;
      const list=(removed()||[]).filter(name=>name!==row.bone);if(!row.removed)list.push(row.bone);
      setRemoved(list);changed();return true;},
    isolate(object){const keys=keysFor(object);if(!keys.length)return;isolated=isolated?null:new Set(keys);changed();},
    showAll(){hidden.clear();isolated=null;changed();},
    reset(){restore();hidden.clear();locked.clear();isolated=null;changed();},
  };
}
