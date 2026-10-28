import {applyPose} from './pivot-editor.js';
import {t} from './i18n.js';

export function supportEditor({state,$,THREE,groups,job,displayPart,updateSelection,selectEdit,remember,refreshCheck,reportError}){
  let cached=null,catalogKey='',selected=0,revision=0;
  const active=()=>$('operation-select').value==='support';
  const spec=()=>state.transform.support_vehicle;
  const key=()=>JSON.stringify([state.hull?.template,spec()?.remove_mount,(spec()?.cargo||[]).map(r=>r.asset)]);
  const row=()=>spec()?.cargo?.[selected];
  const target=()=>groups.turret.children.find(n=>n.userData.cargoIndex===selected);
  const pose=()=>row()||state.transform.turret;
  async function catalog(){const signature=JSON.stringify(state.config?.settings||{});
    if(!cached||signature!==catalogKey){cached=await job('/api/support-catalog',{});catalogKey=signature;}
    return cached;}
  function controls(){
    $('support-fields').classList.toggle('hidden',!active());
    $('pick-turret').disabled=active();$('commander-field').classList.toggle('hidden',active());
    $('operation-count').textContent=active()?t('1 vehículo + carga → 1 vehículo de apoyo'):t('2 piezas')+' → '+t('1 vehículo');
    $('module-inheritance').textContent=t(active()?'Suministro nativo y accesorios visuales elegidos. La cantidad de cajas no cambia la capacidad.':'Armas, munición, giro, miras, tripulantes de la torreta y su blindaje.');
    if(!active())return;
    for(const id of ['support-kind','support-profile','cargo-asset','cargo-list','cargo-add','cargo-duplicate','cargo-remove'])
      $(id).disabled=state.busy||!state.hull;
    $('gun-fields').classList.add('hidden');$('module-kind').textContent=t('CARGA');
    $('pick-hull').textContent=t('Chasis');$('pick-turret').disabled=true;

    $('commander-field').classList.add('hidden');$('mount-select').parentElement.classList.add('hidden');
  }
  function render(){
    controls();if(!active()||!cached)return;
    const current=spec();
    $('support-profile').replaceChildren();
    for(const p of cached.profiles.filter(p=>p.kind===$('support-kind').value))
      $('support-profile').add(new Option(p.label+' · '+p.capacity+' '+t(p.kind==='fuel'?'litros':'unidades de suministro'),p.id));
    if(current)$('support-profile').value=current.profile;
    const profile=cached.profiles.find(p=>p.id===current?.profile);
    $('support-summary').textContent=profile?t('Perfil nativo')+': '+profile.label+' · '+profile.radius+' m · '+profile.source:'';
    const previousAsset=$('cargo-asset').value;$('cargo-asset').replaceChildren();
    for(const asset of cached.cargo)$('cargo-asset').add(new Option(t(({ammo:'Caja de munición',fuel:'Combustible',engineer:'Herramientas'})[asset.kind])+' · '+asset.label,asset.id));
    if(cached.cargo.some(a=>a.id===previousAsset))$('cargo-asset').value=previousAsset;
    const list=$('cargo-list');list.replaceChildren();
    for(const [i,item] of (current?.cargo||[]).entries()){
      const asset=cached.cargo.find(a=>a.id===item.asset);
      list.add(new Option((i+1)+'. '+(asset?.label||item.asset),String(i)));
    }
    selected=Math.max(0,Math.min(selected,(current?.cargo.length||1)-1));list.value=String(selected);
    $('cargo-add').disabled=state.busy||!state.hull||(current?.cargo.length||0)>=24;
    $('cargo-duplicate').disabled=state.busy||!row()||(current?.cargo.length||0)>=24;
    $('cargo-remove').disabled=state.busy||!row();
  }
  async function refresh(){
    state.supportPreviewKey=null;
    render();if(!state.hull||!spec()?.cargo?.length){state.supportPreviewKey=null;
      displayPart('turret',{meshes:[],seats:[],mounts:[],diagnostics:[],support_cargo:true});updateSelection();return;}
    const stamp=++revision,previewKey=key();
    const data=await job('/api/support-preview',{template:state.hull.template,support_vehicle:spec()});
    if(stamp!==revision||!active()||previewKey!==key())return;
    state.supportPreviewKey=previewKey;
    state.mounts={hull:'@full',turret:'@full'};
    data.cargo.support_cargo=true;
    displayPart('hull',data.hull);displayPart('turret',data.cargo);
    updateSelection();selectEdit('turret');render();
  }
  async function select(unit){
    if(!unit.eid||unit.depot||unit.kind!=='vehicle'||state.tab!=='campaign')throw Error(t('Elegí un vehículo de tu campaña para convertirlo en apoyo.'));
    const data=await catalog();
    if(unit.template.startsWith('mw_')){
      const source=await job('/api/edit-source',{eid:unit.eid});
      if(source.transform?.support_vehicle){state.hull={...unit,template:source.recipient};
        state.turret={...unit,template:source.donor,label:t('Carga de apoyo')};
        state.editing={eid:unit.eid,recipe:source.recipe};state.transform=structuredClone(source.transform);
        state.nameEdited=true;$('vehicle-name').value=unit.label||unit.template;
        state.transform.hull??={position:[0,0,0],rotation:[0,0,0]};state.transform.turret??={position:[0,0,0],rotation:[0,0,0]};
        await restore();return;}
    }
    const profile=data.profiles.find(p=>p.kind==='ammo'&&p.macro==='supply_ammo')||data.profiles[0];
    if(!profile||!data.cargo.length)throw Error(t('No se encontraron equipos de apoyo compatibles.'));
    const hull=await job('/api/preview',{template:unit.template,part:'hull'});
    const mount=hull.anchor_name&&!hull.anchor_name.startsWith('@')?hull.anchor_name:'';
    const position=[...(hull.anchor||[0,0,0])];
    state.hull=unit;state.turret={...unit,label:t('Carga de apoyo')};state.editing=null;
    state.transform={hull:{position:[0,0,0],rotation:[0,0,0]},turret:{position:[0,0,0],rotation:[0,0,0]},
      support_vehicle:{schema:1,profile:profile.id,remove_mount:mount,cargo:[{asset:data.cargo[0].id,position,rotation:[0,0,0]}]}};
    state.crewVisibility=null;selected=0;$('support-kind').value=profile.kind;
    await refresh();
  }
  async function restore(){
    if(!spec())return;
    $('operation-select').value='support';await catalog();
    const profile=cached.profiles.find(p=>p.id===spec().profile);
    if(profile)$('support-kind').value=profile.kind;
    await refresh();
  }
  function addMesh(mesh,object){
    if(!active()||!Number.isInteger(mesh.cargo_index))return false;
    const index=mesh.cargo_index,item=spec()?.cargo?.[index];if(!item)return false;
    let host=groups.turret.children.find(n=>n.userData.cargoIndex===index);
    if(!host){host=new THREE.Group();host.userData.cargoIndex=index;applyPose(host,item);host.updateMatrix();groups.turret.add(host);}
    // Backend geometry is in assembled world coordinates; retain one editable
    // local fitting group so changing one crate never moves the other crates.
    object.geometry.applyMatrix4(host.matrix.clone().invert());host.add(object);return true;
  }
  function capture(){const node=target(),item=row();if(!active()||state.edit!=='turret'||!node||!item)return false;
    item.position=node.position.toArray().map(n=>Number(n.toFixed(3)));
    item.rotation=[node.rotation.x,node.rotation.y,node.rotation.z].map(n=>Number(THREE.MathUtils.radToDeg(n).toFixed(3)));
    return true;
  }
  const run=fn=>async()=>{if(state.busy)return;try{remember();await fn();}catch(error){reportError(error);}};
  $('support-kind').onchange=run(async()=>{await catalog();const p=cached.profiles.find(p=>p.kind===$('support-kind').value);
    if(p&&spec()){spec().profile=p.id;render();refreshCheck();}});
  $('support-profile').onchange=()=>{if(state.busy||!spec())return;remember();spec().profile=$('support-profile').value;render();refreshCheck();};
  $('cargo-list').onchange=()=>{selected=Number($('cargo-list').value);selectEdit('turret');};
  $('cargo-add').onclick=run(async()=>{if(!spec())return;
    const previous=row();spec().cargo.push({asset:$('cargo-asset').value,position:previous?[previous.position[0],previous.position[1]+20,previous.position[2]]:[0,0,0],rotation:[0,0,0]});selected=spec().cargo.length-1;await refresh();});
  $('cargo-duplicate').onclick=run(async()=>{if(!row())return;
    const copy=structuredClone(row());copy.position[1]+=20;spec().cargo.push(copy);selected=spec().cargo.length-1;await refresh();});
  $('cargo-remove').onclick=run(async()=>{if(!row())return;spec().cargo.splice(selected,1);await refresh();});
  controls();return {active,select,restore,refresh,controls,render,target,pose,capture,addMesh,key};
}
