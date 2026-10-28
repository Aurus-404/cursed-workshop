import {t} from './i18n.js';

const zero=()=>({position:[0,0,0],rotation:[0,0,0]});
const mountOf=row=>row?.recipient_mount||'turret';
export function vehicleDesign({state,$,job,displayPart,updateSelection,remember,reportError,mountLabel=name=>name,confirm=globalThis.confirm,receiveCrew=()=>{},onFocus=()=>{}}){
  let previewRevision=0,chassis={template:'',mounts:[],preferred:''},loading=null,changing=false;
  const active=()=>!!state.transform.assembly_draft;
  const draft=()=>state.transform.assembly_draft;
  const removals=()=>draft()?.removals||[];
  // One saved change per chassis turret, like Replace gun. Designs from before
  // several turrets kept a single "turret" row.
  function turrets(){const d=draft();if(!d)return [];
    if(d.turret!==undefined){if(!d.turrets)d.turrets=d.turret?[d.turret]:[];delete d.turret;}
    return d.turrets??=[];}
  const placeholder=()=>{state.turret=state.hull?{...state.hull,designPlaceholder:true}:null;};
  function ensure(){
    if(active()&&!state.editing)return;
    const old=structuredClone(state.transform),hull=state.hull,donor=state.turret;
    const origin=state.designOrigin?.eid===state.editing?.eid?state.designOrigin:null;
    const seed=state.editing?{recipient:origin?.recipient||hull.template,donor:origin?.donor||donor.template,transform:old}:null;
    const actual=hull?.eid&&(state.campaign?.units||[]).find(u=>u.eid===hull.eid)||hull;
    state.hull=actual;state.editing=null;
    state.transform={...old,hull:zero(),turret:zero(),assembly_draft:{schema:1,guns:[],turrets:[],...(seed?{seed}:{})}};
    if(seed)for(const k of ['crew_seats','crew_exclude','joint_limits','source_recipe'])delete state.transform[k];
    delete state.transform.gun_swap;delete state.transform.gun_swaps;delete state.transform.support_vehicle;
    placeholder();
  }
  function transform(){const result=structuredClone(state.transform);if($('crew-static').checked!==undefined)result.crew_static=$('crew-static').checked;delete result.gun_swap;delete result.gun_swaps;delete result.gun_edit;
    const d=result.assembly_draft;if(d){d.turrets=d.turrets||(d.turret?[d.turret]:[]);delete d.turret;}
    if(d&&state.pendingTurret?.native){const row=nativeRow();d.turrets=[...d.turrets.filter(r=>mountOf(r)!==mountOf(row)),row];}
    result.turret=zero();return result;}
  const nativeRow=()=>({native:true,recipient_mount:state.pendingTurret.recipient_mount,
    donor_template:state.hull.template,pose:structuredClone(state.transform.turret),
    ...(state.transform.joint_limits?.turret?{joint_limits:structuredClone(state.transform.joint_limits.turret)}:{})});
  function settleNative(){if(!state.pendingTurret?.native)return false;const row=nativeRow();
    draft().turrets=[...turrets().filter(r=>mountOf(r)!==mountOf(row)),row];delete state.pendingTurret;
    if(state.transform.joint_limits)delete state.transform.joint_limits.turret;
    state.transform.turret=zero();placeholder();return true;}
  const body=()=>({hull:state.hull.template,transform:transform()});
  const key=()=>JSON.stringify(body());
  // The chassis' own turrets name the targets: they keep their names however
  // many of them the design already replaced.
  function loadChassis(){
    const template=state.hull?.template;if(!template||chassis.template===template)return Promise.resolve();
    if(loading?.template===template)return loading.promise;
    const promise=job('/api/preview',{template,part:'hull'}).then(data=>{
      const parents=new Map((data?.bones||[]).map(b=>[b.name,b.parent])),rings=(data?.mounts||[]).filter(m=>!String(m).startsWith('@'));
      const usable=rings.filter(m=>parents.get(m)!==null);
      const top=usable.filter(m=>{let parent=parents.get(m);while(parent){if(usable.includes(parent))return false;parent=parents.get(parent);}return true;});
      if(state.hull?.template===template)chassis={template,mounts:[...top,...(data?.mounts||[]).filter(m=>String(m).startsWith('@free'))],preferred:data.anchor_name||''};
      controls();
    }).finally(()=>{if(loading?.promise===promise)loading=null;});
    loading={template,promise};return promise;
  }
  const target=()=>{const unused=chassis.mounts.filter(m=>!turrets().some(r=>mountOf(r)===m));
    return state.designTarget&&chassis.mounts.includes(state.designTarget)?state.designTarget:
      unused.includes(chassis.preferred)?chassis.preferred:unused[0]||chassis.mounts[0]||'@free';};
  function label(name){
    const aux=/^aux_turret(\d+)$/.exec(name||'');
    return name==='turret'?t('Torreta principal'):aux?t('Torreta auxiliar')+' '+aux[1]:t(mountLabel(name));
  }
  async function refresh(pending){
    if(!state.hull)return;
    loadChassis().catch(reportError);
    const version=++previewRevision,stamp=key(),data=await job('/api/design-preview',{...body(),...(pending?{pending}:{})});
    if(version!==previewRevision||stamp!==key())return;
    state.designWeapons=data.saved_weapons||[];
    state.mounts={hull:data.hull.anchor_name||'',turret:data.turret.anchor_name||''};
    if(!pending)receiveCrew(data.crew_visibility||[]);
    state.designPreviewKey=stamp;displayPart('hull',data.hull);displayPart('turret',data.turret);updateSelection();
  }
  async function switchMode(next){
    if(!state.hull)return;
    settleNative();
    if(!state.editing&&(state.pendingTurret||state.transform.gun_swap)){
      if(!confirm(t('Cambiar de herramienta descarta el cambio pendiente sin guardar. Los cambios guardados se conservan. ¿Continuar?')))return false;
    }
    ensure();if(state.transform.gun_edit){draft().guns.splice(state.transform.gun_edit.index,0,state.transform.gun_edit.row);delete state.transform.gun_edit;}
    delete state.pendingTurret;delete state.transform.gun_swap;state.transform.turret=zero();placeholder();
    state.pick='turret';await refresh();return true;
  }
  async function select(unit,part){
    // Starting again after a build (or clearing both parts) must choose a
    // chassis first, even if the previous selection mode was Turret.
    if(!state.hull){part='hull';state.pick='hull';}
    if(part==='hull'){
      if(active()&&state.hull?.eid!==unit.eid){
        if(!confirm(t('Elegir otro vehículo descarta este diseño sin construir. ¿Continuar?')))return;
        state.transform={hull:zero(),turret:zero()};
      }
      state.hull=unit;state.designTarget=null;ensure();placeholder();await refresh();return;
    }
    ensure();if(unit.eid===state.hull.eid)throw Error(t('Elegí otro vehículo como donante.'));
    if(!unit.eid&&!unit.depot)throw Error(t('Este diseño requiere donantes que sean vehículos de tu campaña.'));
    await loadChassis();
    const original=state.pendingTurret?.original;
    state.turret=unit;state.pendingTurret={kind:'turret',donor_template:unit.template,donor_eid:unit.eid,recipient_mount:target(),
      ...(unit.creative?{donor_catalog:true}:unit.depot?{donor_depot:unit.depot}:{}),...(unit.mount?{donor_mount:unit.mount}:{}),...(original?{original}:{})};
    state.transform.turret=zero();await refresh(state.pendingTurret);
  }
  async function saveTurret(){
    if(!state.pendingTurret)return;
    if(state.pendingTurret.native){settleNative();await refresh();return;}
    const pending=state.pendingTurret,mount=mountOf(pending),rows=turrets();
    const previous=rows.find(r=>mountOf(r)===(pending.original||mount));
    const same=previous&&mountOf(previous)===mount&&['donor_template','donor_eid','donor_depot','donor_catalog','assembly_scope'].every(k=>previous[k]===pending[k]);
    if(draft().guns.length&&!same&&!confirm(t('Cambiar de torreta requiere quitar todos los cambios de arma guardados en este diseño. ¿Quitarlos y guardar la torreta?')))return;
    remember();
    const {kind,original,...row}=pending;
    row.recipient_mount=mount;row.donor_mount=state.mounts.turret||row.donor_mount||'turret';row.pose=structuredClone(state.transform.turret);
    const limits=state.transform.joint_limits?.turret;
    if(limits&&Object.keys(limits).length)row.joint_limits=structuredClone(limits);else delete row.joint_limits;
    if(state.transform.joint_limits)delete state.transform.joint_limits.turret;
    draft().turrets=[...rows.filter(r=>![mount,original].includes(mountOf(r))),row];
    draft().removals=removals().filter(r=>r.mount!==mount);
    if(!same)draft().guns=[];delete state.pendingTurret;state.designTarget=null;placeholder();state.transform.turret=zero();await refresh();
  }
  async function editTurret(mount){
    // Opening Position without a choice moves the chosen turret if it was
    // changed, else the last one saved.
    const rows=turrets();
    mount??=rows.some(r=>mountOf(r)===state.designTarget)?state.designTarget:rows.length?mountOf(rows.at(-1)):null;
    const row=rows.find(r=>mountOf(r)===mount);
    if(row?.native||!row){
      await loadChassis();mount??=state.designTarget||chassis.preferred;
      if(!chassis.mounts.includes(mount)||String(mount).startsWith('@')||removals().some(r=>r.mount===mount))return false;
      if(state.pendingTurret?.native)settleNative();
      const own=turrets().find(r=>mountOf(r)===mount&&r.native);
      state.designTarget=mount;placeholder();state.pendingTurret={kind:'native',native:true,recipient_mount:mount,original:mount};
      state.inspectorFocusPart='turret';onFocus('turret');
      state.transform.turret=structuredClone(own?.pose||zero());
      state.transform.joint_limits={...state.transform.joint_limits,turret:structuredClone(own?.joint_limits||{})};
      await refresh(state.pendingTurret);return true;
    }
    state.designTarget=mount;
    state.inspectorFocusPart='turret';onFocus('turret');
    state.turret=(state.campaign?.units||[]).find(u=>u.eid===row.donor_eid)||{...row,eid:row.donor_eid,template:row.donor_template,label:row.donor_template,...(row.donor_catalog?{creative:true,depot:'creative'}:{})};
    state.pendingTurret={...structuredClone(row),kind:'turret',original:mount};
    state.transform.turret=structuredClone(row.pose||zero());
    if(row.joint_limits)state.transform.joint_limits={...state.transform.joint_limits,turret:structuredClone(row.joint_limits)};
    await refresh(state.pendingTurret);return true;
  }
  async function discard(){
    // Drops the change being adjusted; saved changes stay (a saved gun being edited goes back).
    remember();
    if(state.transform.gun_edit){draft().guns.splice(state.transform.gun_edit.index,0,state.transform.gun_edit.row);delete state.transform.gun_edit;}
    delete state.pendingTurret;delete state.transform.gun_swap;
    if(state.transform.joint_limits)delete state.transform.joint_limits.turret;
    state.transform.turret=zero();state.designTarget=null;placeholder();await refresh();
  }
  async function removeTurret(mount){
    const rows=turrets();if(!rows.some(r=>mountOf(r)===mount))return;
    if(draft().guns.length&&!confirm(t('Quitar una torreta requiere quitar todos los cambios de arma guardados en este diseño. ¿Continuar?')))return;
    remember();draft().turrets=rows.filter(r=>mountOf(r)!==mount);draft().guns=[];
    if(state.pendingTurret?.original===mount)delete state.pendingTurret;
    placeholder();state.transform.turret=zero();await refresh();
  }
  async function chooseTarget(mount){
    if(state.pendingTurret?.native)settleNative();
    state.designTarget=mount;
    if(state.pendingTurret){state.pendingTurret.recipient_mount=mount;state.transform.turret=zero();await refresh(state.pendingTurret);}
    else if(!await editTurret(mount))controls();
  }
  async function retireTurret(){
    if(!state.hull||state.busy)return;
    await loadChassis();const mount=target();
    if(!chassis.mounts.includes(mount)||mount.startsWith('@'))throw Error(t('Elegí una torreta separable para retirar.'));
    if(!confirm(t('La torreta y todas sus armas instaladas se guardarán en el depósito al construir. Los tripulantes sobrantes quedarán en reserva. ¿Continuar?')))return;
    const before=structuredClone(state.transform),pending=state.pendingTurret,unit=state.turret;
    remember();ensure();settleNative();
    draft().removals=[...removals().filter(r=>r.mount!==mount),{mount}];
    delete state.pendingTurret;delete state.transform.gun_swap;placeholder();
    try{await refresh();}catch(error){state.transform=before;state.pendingTurret=pending;state.turret=unit;controls();throw error;}
  }
  async function undoRemoval(){
    const mount=$('turret-removed').value;if(!mount)return;
    remember();draft().removals=removals().filter(r=>r.mount!==mount);await refresh();
  }
  async function chooseScope(){
    if(!state.pendingTurret)return;
    const old=structuredClone(state.pendingTurret);
    delete state.pendingTurret.donor_mount;
    if($('assembly-scope').value==='complete')state.pendingTurret.assembly_scope='complete';
    else delete state.pendingTurret.assembly_scope;
    try{await refresh(state.pendingTurret);}catch(error){state.pendingTurret=old;controls();throw error;}
  }
  function controls(){
    const shown=active()&&$('operation-select').value==='turret';
    $('turret-fields').classList.toggle('hidden',!shown);
    $('turret-saved-fields').classList.toggle('hidden',!shown||!turrets().length);
    $('design-save-turret').classList.toggle('hidden',!shown);
    $('design-save-turret').disabled=state.busy||!state.pendingTurret||!!state.pendingTurret.native;
    $('turret-retire').disabled=state.busy||changing||!shown||target().startsWith('@')||removals().some(r=>r.mount===target());
    $('turret-removal-actions').classList.toggle('hidden',!shown);
    $('assembly-scope-field').classList.toggle('hidden',!shown||!state.pendingTurret||!!state.pendingTurret.native);
    $('assembly-scope').value=state.pendingTurret?.assembly_scope||'module';
    $('assembly-scope').disabled=state.busy||changing||!state.pendingTurret;
    $('turret-removed-fields').classList.toggle('hidden',!shown||!removals().length);
    $('turret-removed').replaceChildren(...removals().map(r=>new Option(label(r.mount),r.mount)));
    $('turret-undo-removal').disabled=state.busy||changing||!removals().length;
    if(shown){
      const rows=turrets(),current=state.pendingTurret?mountOf(state.pendingTurret):target();
      $('turret-target').replaceChildren(...chassis.mounts.map(m=>new Option(label(m)+(rows.some(r=>mountOf(r)===m)?' · '+t('cambiada'):''),m)));
      if(chassis.mounts.includes(current))$('turret-target').value=current;
      $('turret-target').disabled=state.busy||!chassis.mounts.length;
      const chosen=$('turret-saved').value;
      const donor=r=>(state.campaign?.units||[]).find(u=>u.eid===r.donor_eid)?.label||(state.campaign?.depot||[]).find(d=>d.id===r.donor_depot)?.label||r.donor_template;
      $('turret-saved').replaceChildren(...rows.map(r=>new Option(label(mountOf(r))+' ← '+donor(r),mountOf(r))));
      if(rows.some(r=>mountOf(r)===chosen))$('turret-saved').value=chosen;
      $('turret-saved').disabled=state.busy||!rows.length;
      $('design-edit-turret').classList.toggle('hidden',!rows.length);
      $('design-edit-turret').disabled=state.busy||!!state.pendingTurret||!$('turret-saved').value;
      $('turret-remove-change').disabled=state.busy||!$('turret-saved').value;
    }else $('design-edit-turret').classList.toggle('hidden',true);
    // Position and limits pick which design turret they adjust.
    const rows=active()?turrets():[],pending=state.pendingTurret,focus=[...new Set([...chassis.mounts.filter(m=>!m.startsWith('@')&&!removals().some(r=>r.mount===m)),...rows.map(mountOf).filter(m=>!removals().some(r=>r.mount===m))])];
    if(pending&&!focus.includes(mountOf(pending)))focus.push(mountOf(pending));
    for(const id of ['design-turret-pos','design-turret-limits','design-module-crew']){
      $(id+'-field').classList.toggle('hidden',!active()||!focus.length);
      const options=focus;
      const heading=$(id).labels?.[0];if(heading)heading.textContent=t('Parte que estás editando');
      // The list only names the part that is loaded. With nothing loaded it asks
      // for a choice, so picking any part (even the one shown before) loads it.
      const loaded=pending?mountOf(pending):state.inspectorFocusPart==='hull'?'@hull':'';
      $(id).replaceChildren(...(loaded?[]:[new Option(t('Elegí qué parte ajustar…'),'')]),new Option(t('Chasis completo'),'@hull'),...options.map(m=>new Option(label(m)+(rows.some(r=>mountOf(r)===m&&!r.native)?' · '+t('cambiada'):''),m)));
      $(id).value=loaded;
      $(id).disabled=state.busy;
    }
    if(active()&&state.turret?.designPlaceholder){$('turret-name').textContent=t('Elegí un donante para el próximo cambio');$('turret-detail').textContent=t('El diseño guardado se conserva.');}
  }
  const run=fn=>async()=>{try{await fn();}catch(e){reportError(e);}};
  const change=fn=>run(async()=>{if(changing||state.busy)return;changing=true;try{const task=fn();controls();await task;}finally{changing=false;controls();}});
  $('design-save-turret').onclick=run(saveTurret);
  $('design-edit-turret').onclick=run(()=>editTurret($('turret-saved').value));
  $('turret-remove-change').onclick=run(()=>removeTurret($('turret-saved').value));
  $('turret-target').onchange=run(()=>chooseTarget($('turret-target').value));
  $('turret-retire').onclick=change(retireTurret);
  $('turret-undo-removal').onclick=change(undoRemoval);
  $('assembly-scope').onchange=change(chooseScope);
  for(const id of ['design-turret-pos','design-turret-limits','design-module-crew'])$(id).onchange=run(async()=>{
    const mount=$(id).value;if(!mount||state.pendingTurret&&mountOf(state.pendingTurret)===mount)return;
    if(mount==='@hull'){settleNative();state.inspectorFocusPart='hull';onFocus('hull');await refresh(state.pendingTurret);return;}
    if(state.pendingTurret&&!state.pendingTurret.native){
      if(!confirm(t('Cambiar de herramienta descarta el cambio pendiente sin guardar. Los cambios guardados se conservan. ¿Continuar?')))return;
      delete state.pendingTurret;state.transform.turret=zero();placeholder();
    }
    if(!await editTurret(mount))controls();});
  $('turret-saved').onchange=()=>{if(!state.pendingTurret&&$('turret-saved').value)state.designTarget=$('turret-saved').value;controls();};
  return {active,draft,turrets,ensure,placeholder,body,key,transform,refresh,switchMode,select,saveTurret,editTurret,removeTurret,discard,controls,settleNative,loadChassis,
    moduleChoices:()=>chassis.mounts.filter(m=>!m.startsWith('@')&&!removals().some(r=>r.mount===m)).map(m=>({value:m,label:label(m)}))};
}
