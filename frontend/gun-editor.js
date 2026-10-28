import {t} from './i18n.js';

export function gunEditor({state,$,job,displayPart,updateSelection,selectEdit,remember,reportError,design}){
  let revision=0;const catalogs={};
  const active=()=>$('operation-select').value==='gun';
  const saved=()=>design?.active()?design.draft().guns:state.transform.gun_swaps||[];
  const key=()=>JSON.stringify([state.hull?.template,state.turret?.template,state.transform.gun_swap||null,saved()]);
  function controls(){
    $('gun-fields').classList.toggle('hidden',!active());
    $('module-kind').textContent=t(active()?'ARMA DONANTE':'TORRETA');$('hull-kind').textContent=t(active()?'RECEPTOR':'CHASIS');
    $('pick-turret').textContent=t(active()&&!state.editing?'Donante':'Torreta');
    $('pick-hull').textContent=t(active()&&!state.editing?'Receptor':'Chasis');
    $('mount-select').disabled=active();
    $('gun-save').disabled=!!(state.busy||!state.transform.gun_swap?.recipient_place||!state.transform.gun_swap?.donor_place||state.turret?.designPlaceholder);
    $('gun-save-position').classList.toggle('hidden',!active()||!state.transform.gun_swap);
    $('gun-save-position').disabled=$('gun-save').disabled;
    for(const id of ['gun-edit-change','gun-remove-change','gun-clear-changes'])$(id).disabled=state.busy||!saved().length;
    $('gun-discard').disabled=state.busy||!state.transform.gun_swap;
    $('gun-barrel-only').checked=!!state.transform.gun_swap?.barrel_only;$('gun-barrel-only').disabled=state.busy||!state.transform.gun_swap?.donor_place;
    $('gun-keep-mantlet').checked=!state.transform.gun_swap?.drop_mantlet;$('gun-keep-mantlet').disabled=state.busy||!state.transform.gun_swap?.donor_place;
    if(state.transform.gun_swap)$('gun-edit-change').disabled=true;
    const label=(part,id)=>{const row=catalogs[part]?.weapons.find(w=>w.place===id);return row?.label||row?.weapon?.replaceAll('_',' ')||id;};
    const selectedChange=$('gun-saved').value;
    $('gun-saved').replaceChildren(...saved().map((row,i)=>{
      const verified=state.designWeapons?.find(w=>w.recipient_place===row.recipient_place&&w.donor_template===row.donor_template&&w.donor_place===row.donor_place);
      const weapon=verified?.label||label('turret',row.donor_place),source=(state.campaign?.units||[]).find(u=>u.eid===row.donor_eid)?.label||row.donor_template||'';
      return new Option(`${i+1}. ${row.recipient_place} ← ${weapon}${source?' · '+source:''}`,String(i));}));
    $('gun-saved').value=selectedChange!==''&&saved()[Number(selectedChange)]?selectedChange:saved().length?'0':'';
    if(active())$('operation-count').textContent=t('2 vehículos → 1 vehículo');
    for(const [part,id] of [['hull','gun-recipient'],['turret','gun-donor']])if(!state[part]){delete catalogs[part];$(id).replaceChildren(new Option(t(part==='hull'?'Elegí primero el receptor':'Elegí primero el donante'),''));$(id).disabled=true;}
    $('assembly-step').textContent=t(active()?(!state.hull?'Elegí el vehículo receptor.':!state.turret||state.turret.designPlaceholder?'Elegí Donante y seleccioná otra unidad.':!state.transform.gun_swap?.recipient_place||!state.transform.gun_swap?.donor_place?'Elegí dos cañones compatibles para continuar.':'Ajustá el cañón desde Posición y montaje. Volvé al menú para construir.'):$('operation-select').value==='support'?'Elegí un vehículo y después Servicio y carga.':'Elegí el chasis y la torreta en la lista.');
  }
  async function catalog(part){
    const unit=state[part];if(!unit)return;
    if(unit.designPlaceholder&&part==='turret'){selectEmpty();return;}
    function selectEmpty(){const select=$('gun-donor');select.replaceChildren(new Option(t('Elegí primero el donante'),''));select.disabled=true;}
    const operation=$('operation-select').value;const data=await job(design?.active()&&part==='hull'?'/api/design-catalog':'/api/gun-catalog',design?.active()&&part==='hull'?design.body():{template:unit.template});
    if(state[part]!==unit||$('operation-select').value!==operation)return;
    catalogs[part]=data;const select=$(part==='hull'?'gun-recipient':'gun-donor');select.replaceChildren();
    // Same-named weapons (three "MG 34" on a Nb.Fz) say where they are.
    const name=row=>row.label||row.weapon.replaceAll('_',' '),count=new Map();
    for(const row of data.weapons)count.set(name(row),(count.get(name(row))||0)+1);
    const where=row=>{const yaw=row.yaw||'',aux=/^aux_turret(\d+)$/.exec(yaw);
      return yaw==='turret'?t('Torreta principal'):aux?t('Torreta auxiliar')+' '+aux[1]:(yaw||row.place).replaceAll('_',' ');};
    for(const row of data.weapons){
      const option=new Option(`${name(row)}${count.get(name(row))>1?' · '+where(row):''}${row.available?'':' · '+t('No compatible')}`,row.place);
      option.disabled=!row.available;option.title=[row.weapon,row.place,row.reason].filter(Boolean).join(' · ');select.add(option);
    }
    select.add(new Option(t('Elegí un arma'),''));
    const key=part==='hull'?'recipient_place':'donor_place',spec=state.transform.gun_swap;
    if(!spec){select.value='';return;}
    const eligible=row=>row.available;
    const chosen=data.weapons.find(row=>eligible(row)&&row.place===spec[key])||
      data.weapons.find(row=>eligible(row)&&(part!=='hull'||!saved().some(r=>r.recipient_place===row.place)));
    spec[key]=chosen?.place||'';select.value=spec[key];
    $('gun-note').textContent=t(data.kind==='towed'?'Cañón de artillería remolcada':'Cañón de vehículo')+' · '+data.source+
      (chosen?'':' · '+(data.weapons.map(row=>row.reason).join(' / ')||t('No tiene un cañón desmontable.')));
    $('gun-blocked').textContent=Object.values(catalogs).flatMap(c=>c.weapons.filter(w=>!w.available).map(w=>`${w.weapon} · ${w.place}: ${t(w.reason)}`)).join('\n');
  }
  async function refresh(){
    const spec=state.transform.gun_swap,stamp=++revision,requestKey=key();
    state.gunPreviewKey=null;
    const complete=!!(spec?.recipient_place&&spec?.donor_place);
    if(!state.hull||!state.turret){updateSelection();return;}
    if(design?.active()){
      await design.refresh(complete&&!state.turret.designPlaceholder?{...spec,kind:'gun',donor_template:state.turret.template}:undefined);
      if(stamp===revision){state.gunPreviewKey=key();controls();selectEdit('turret');}return;
    }
    let data;try{data=await job('/api/gun-preview',{hull:state.hull.template,donor:state.turret.template,
      ...(complete?{gun_swap:spec}:{}),...(saved().length?{gun_swaps:saved()}: {})});}
    catch(error){if(error.name==='AbortError'&&stamp!==revision)return;throw error;}
    if(stamp!==revision||requestKey!==key()||!active())return;
    state.gunPreviewKey=key();
    state.mounts={hull:data.hull.anchor_name,turret:data.turret.anchor_name};
    displayPart('hull',data.hull);displayPart('turret',data.turret);updateSelection();selectEdit('turret');
  }
  async function select(unit,part){
    // A save vehicle as recipient is designed in the campaign (a catalog gun
    // joins it costing no unit); a catalog recipient (creative mode) builds
    // for the shop, where every part only names its model.
    const owned=u=>!!u?.eid&&!u.depot&&!u.designPlaceholder;
    const designed=!!design&&(part==='hull'?owned(unit):owned(state.hull));
    if(designed&&!(unit.creative&&part!=='hull')&&(!owned(unit)||state.tab!=='campaign'))throw Error(t('Cambiar cañón requiere dos unidades de tu campaña.'));
    if(!designed&&!(unit.creative||state.hull?.creative)&&(design||!owned(unit)||state.tab!=='campaign'))throw Error(t('Cambiar cañón requiere dos unidades de tu campaña.'));
    if(!designed&&design?.active()){delete state.transform.assembly_draft;delete state.pendingTurret;if(state.turret?.designPlaceholder)state.turret=null;}
    if(designed){
      if(part==='hull'){await design.select(unit,part);await catalog('hull');controls();return;}
      design.ensure();if(unit.eid===state.hull.eid)throw Error(t('Elegí otro vehículo como donante.'));
      const recipient=state.transform.gun_swap?.recipient_place||'';
      state.turret=unit;state.transform.gun_swap={schema:1,recipient_place:recipient,donor_place:'',upright:true};
      if(!state.transform.gun_edit)state.transform.turret={position:[0,0,0],rotation:[0,0,0]};
      await catalog('hull');await catalog('turret');await refresh();controls();return;
    }
    const identity=u=>u?.eid||u?.template;
    if(saved().length&&identity(state[part])!==identity(unit))throw Error(t('Quitá los cambios guardados antes de cambiar el receptor o el donante.'));
    state[part]=unit;state.editing=null;state.transform.gun_swap??={schema:1,upright:true};
    state.transform.turret={position:[0,0,0],rotation:[0,0,0]};
    state.transform.hull={position:[0,0,0],rotation:[0,0,0]};
    delete state.transform.crew_hidden;state.crewVisibility=null;delete state.transform.commander_only;delete state.transform.crew_seats;delete state.transform.crew_exclude;delete state.transform.joint_limits;
    await catalog(part);
    if(part==='hull'&&!state.turret){const data=await job('/api/preview',{template:unit.template,part:'full'});displayPart('hull',data);}
    await refresh();controls();
  }
  async function restore(){
    if(!design?.active())$('operation-select').value=(state.transform.gun_swap||Array.isArray(state.transform.gun_swaps))?'gun':'turret';controls();
    if(!active())return;
    await catalog('hull');await catalog('turret');await refresh();
  }
  for(const [id,key] of [['gun-recipient','recipient_place'],['gun-donor','donor_place']]){
    $(id).onchange=async()=>{const value=$(id).value;remember();try{
      if(key==='recipient_place'&&value!==state.transform.gun_swap?.recipient_place){
        // Reopening a saved slot restores its own donor, placement and pivot.
        // Discarding this edit must be able to restore the previous saved row.
        restoreEdited();
        const index=saved().findIndex(row=>row.recipient_place===value);
        if(index>=0){await editChange(index);return;}
        state.transform.turret={position:[0,0,0],rotation:[0,0,0]};
      }
      state.transform.gun_swap??={schema:1,recipient_place:'',donor_place:'',upright:true};
      state.transform.gun_swap[key]=value;await refresh();controls();
    }catch(error){if(error.name!=='AbortError')reportError(error);}};
  }
  function restoreEdited(){
    if(design?.active()&&state.transform.gun_edit){
      saved().splice(state.transform.gun_edit.index,0,state.transform.gun_edit.row);delete state.transform.gun_edit;
    }
  }
  async function editChange(index){
    const row=saved()[index];if(!row)return;
    if(design?.active())state.transform.gun_edit={index,row:structuredClone(row)};
    saved().splice(index,1);
    if(design?.active())state.turret=(state.campaign?.units||[]).find(u=>u.eid===row.donor_eid)||{eid:row.donor_eid,template:row.donor_template,...(row.donor_catalog?{creative:true,depot:'creative'}:{})};
    state.transform.gun_swap={schema:1,recipient_place:row.recipient_place,donor_place:row.donor_place,...(row.upright?{upright:true}:{}),...(row.barrel_only?{barrel_only:true}:{}),...(row.drop_mantlet?{drop_mantlet:true}:{})};
    state.transform.turret=structuredClone(row.pose);await catalog('hull');await catalog('turret');await refresh();controls();
  }
  async function save(){
    const spec=state.transform.gun_swap;if(!spec?.recipient_place||!spec?.donor_place)return;
    remember();if(!design?.active())state.transform.gun_swaps??=[];
    const row={...structuredClone(spec),pose:structuredClone(state.transform.turret),...(design?.active()?{donor_template:state.turret.template,donor_eid:state.turret.eid,...(state.turret.creative?{donor_catalog:true}:{})}:{})};
    const existing=saved().findIndex(r=>r.recipient_place===row.recipient_place);
    if(existing>=0)saved().splice(existing,1,row);
    else if(design?.active()&&state.transform.gun_edit)saved().splice(state.transform.gun_edit.index,0,row);else saved().push(row);
    delete state.transform.gun_edit;
    delete state.transform.gun_swap;state.transform.turret={position:[0,0,0],rotation:[0,0,0]};
    if(design?.active())design.placeholder();
    await catalog('hull');await catalog('turret');await refresh();controls();
  }
  const run=fn=>async()=>{try{await fn();}catch(error){if(error.name!=='AbortError')reportError(error);}};
  $('gun-save').onclick=run(save);$('gun-save-position').onclick=run(save);
  // Barrel only: an emplacement's breech, cradle and shield stay out of the turret.
  $('gun-keep-mantlet').onchange=run(async()=>{if(!state.transform.gun_swap)return;remember();
    if($('gun-keep-mantlet').checked)delete state.transform.gun_swap.drop_mantlet;else state.transform.gun_swap.drop_mantlet=true;await refresh();controls();});
  $('gun-barrel-only').onchange=run(async()=>{if(!state.transform.gun_swap)return;remember();
    if($('gun-barrel-only').checked)state.transform.gun_swap.barrel_only=true;else delete state.transform.gun_swap.barrel_only;await refresh();controls();});
  $('gun-discard').onclick=run(async()=>{remember();delete state.transform.gun_swap;
    if(design?.active()&&state.transform.gun_edit){saved().splice(state.transform.gun_edit.index,0,state.transform.gun_edit.row);delete state.transform.gun_edit;design.placeholder();}
    state.transform.turret={position:[0,0,0],rotation:[0,0,0]};await catalog('hull');await catalog('turret');await refresh();controls();});
  $('gun-edit-change').onclick=run(async()=>{remember();await editChange(Number($('gun-saved').value));});
  $('gun-remove-change').onclick=run(async()=>{remember();saved().splice(Number($('gun-saved').value),1);await catalog('hull');await catalog('turret');await refresh();controls();});
  $('gun-clear-changes').onclick=run(async()=>{remember();if(design?.active())design.draft().guns=[];else state.transform.gun_swaps=[];delete state.transform.gun_swap;delete state.transform.gun_edit;
    state.transform.turret={position:[0,0,0],rotation:[0,0,0]};await catalog('hull');await catalog('turret');await refresh();controls();});
  // The bones a weapon place takes with it, for marking it in the 3D view.
  const bones=(part,place)=>catalogs[part]?.weapons?.find(row=>row.place===place)?.bones||null;
  controls();return {active,select,restore,controls,key,save,bones};
}
