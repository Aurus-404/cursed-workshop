import {t} from './i18n.js';

// Design templates (Discord: "apply the same changes to the other 3 Bren
// carriers"). A template is a saved design for one chassis model. Applying it
// to another vehicle of that model copies every change (armor, commander MG,
// weapons, mobility, limits, crew) and picks this campaign's own donor units
// for the weapons and turrets it takes from other vehicles; building then
// consumes them like any design.
const ZERO=()=>({position:[0,0,0],rotation:[0,0,0]});
const donorUnit=unit=>unit?.eid&&unit.kind!=='human'&&unit.kind!=='infantry'&&!unit.stored;

export function templateTransform(template,{hull,campaign,seed}={}){
  if(!hull?.eid)throw Error(t('Elegí como chasis un vehículo de tu partida para aplicar la plantilla.'));
  if(hull.template!==template.chassis)
    throw Error(t('Esta plantilla es para otro modelo de chasis:')+' '+(template.label||template.chassis));
  const transform=structuredClone(template.transform),draft=transform.assembly_draft;
  // The vehicle's own starting recipe, never the one the template came from.
  if(seed)draft.seed=structuredClone(seed);else delete draft.seed;
  transform.hull=ZERO();transform.turret=ZERO();
  const units=(campaign?.units||[]).filter(u=>donorUnit(u)&&u.eid!==hull.eid),depot=campaign?.depot||[];
  const chosen=new Map(),used=new Set(),usedDepot=new Set(),missing=new Map();
  for(const row of [...(draft.turrets||[]),...(draft.guns||[])]){
    if(row.native||row.donor_catalog)continue;
    if(row.donor_depot){
      const item=depot.find(d=>d.template===row.donor_template&&d.part==='turret'&&(!d.mount||d.mount===row.donor_mount)&&!usedDepot.has(d.id));
      if(item){usedDepot.add(item.id);row.donor_depot=item.id;continue;}
      delete row.donor_depot;
    }
    // Rows that shared one donor share one donor again: it is consumed once.
    const key=row.donor_eid||`${row.donor_template}#${chosen.size}`;
    if(chosen.has(key)){const eid=chosen.get(key);if(eid)row.donor_eid=eid;continue;}
    const unit=units.find(u=>u.template===row.donor_template&&!used.has(u.eid));
    if(!unit){chosen.set(key,null);missing.set(row.donor_template,(missing.get(row.donor_template)||0)+1);continue;}
    used.add(unit.eid);chosen.set(key,unit.eid);row.donor_eid=unit.eid;
  }
  return {transform,missing:[...missing].map(([template,count])=>({template,count})),donors:[...used]};
}

const empty=value=>value==null||value===false||(Array.isArray(value)?!value.length:typeof value==='object'&&!Object.keys(value).length);
// A vehicle opened with Edit (or combined without the design tools) keeps a
// plain recipe: its own turret, moved or not, or one donor's. As a template it
// becomes the same design, so it applies with the same rules.
const moved=pose=>[...(pose?.position||[]),...(pose?.rotation||[]),...(pose?.pivot||[])].some(Boolean);
const sized=value=>value&&Object.keys(value).length;
export function designFromRecipe(transform,{recipient,donor}){
  if(transform.assembly_draft){
    const design=structuredClone(transform);
    if(design.assembly_draft.seed)throw Error(t('Este vehículo se armó sobre otro ya modificado: guardá la plantilla desde el diseño, eligiendo el vehículo con CHASIS.'));
    return design;
  }
  if(transform.gun_swap||transform.gun_swaps?.length||transform.support_vehicle)
    throw Error(t('Los cambios de cañón y los vehículos de apoyo todavía no se guardan como plantilla desde Editar.'));
  const {hull,turret,recipient_mount,donor_mount,joint_limits,commander_only,source_recipe,...rest}=structuredClone(transform);
  const limits=sized(joint_limits?.turret)?{joint_limits:joint_limits.turret}:{},rows=[];
  if(donor&&donor!==recipient)rows.push({recipient_mount,donor_mount,donor_template:donor,donor_eid:'template-donor',pose:turret||ZERO(),...limits});
  else if(!commander_only&&recipient_mount&&(moved(turret)||limits.joint_limits))rows.push({native:true,recipient_mount,donor_template:recipient,pose:turret,...limits});
  const design={...rest,hull:hull||ZERO(),turret:ZERO(),assembly_draft:{schema:1,guns:[],turrets:rows}};
  if(sized(joint_limits?.hull))design.joint_limits={hull:joint_limits.hull};
  return design;
}
// Back to a plain recipe, for a vehicle being edited: only its own parts.
export function recipeFromDesign(transform,{recipient_mount,donor_mount}){
  const {assembly_draft:draft,...rest}=structuredClone(transform),turrets=draft?.turrets||[];
  if(draft?.guns?.length||draft?.removals?.length||turrets.some(r=>!r.native))
    throw Error(t('Esta plantilla usa vehículos donantes: elegí el vehículo con CHASIS (no con EDITAR) para aplicarla.'));
  const own=turrets.find(r=>r.recipient_mount===recipient_mount)||turrets[0],recipe={...rest,turret:own?.pose||ZERO(),recipient_mount,donor_mount};
  if(sized(own?.joint_limits))recipe.joint_limits={...(rest.joint_limits||{}),turret:own.joint_limits};
  return recipe;
}

export function designTemplates({state,$,api,draftEditor,recipe,applyDesign,applyRecipe,remember,reportError,status,labelOf=template=>template,confirm=(...ask)=>globalThis.confirm(...ask)}){
  let templates=[];
  const chosen=()=>templates.find(row=>row.name===$('template-select').value);
  const pending=()=>!!(state.pendingTurret&&!state.pendingTurret.native)||!!state.transform.gun_swap||!!state.transform.gun_edit;
  // Plain recipe of a vehicle opened with Edit, not a design of the editor.
  const plainEdit=()=>!!state.editing&&!draftEditor.active()&&!!state.hull&&!!state.turret;
  const savable=()=>!!state.hull&&(draftEditor.active()||!!state.turret);
  const appliable=()=>!!state.hull&&(draftEditor.active()&&!state.editing||plainEdit());
  function render(){
    const current=$('template-select').value,model=state.hull?.template;
    // Templates for the chosen chassis come first.
    const rows=[...templates].sort((a,b)=>(b.chassis===model)-(a.chassis===model));
    $('template-select').replaceChildren(new Option(templates.length?t('Elegí una plantilla…'):t('Todavía no hay plantillas guardadas'),''),
      ...rows.map(row=>new Option(`${row.name} · ${row.label||labelOf(row.chassis)}`,row.name)));
    $('template-select').value=rows.some(row=>row.name===current)?current:'';
    controls();
  }
  function controls(){
    const row=chosen();
    $('template-save').disabled=state.busy||!savable();
    $('template-apply').disabled=state.busy||!appliable()||!row||row.chassis!==state.hull?.template;
    $('template-delete').disabled=state.busy||!row;
    $('template-hint').textContent=!row?'':row.chassis===state.hull?.template?
      t('Sirve para este chasis.'):t('Esta plantilla es para otro modelo de chasis:')+' '+(row.label||labelOf(row.chassis));
  }
  async function load(){const data=await api('/api/design-templates');templates=data.templates||[];render();}
  async function save(){
    if(!savable())throw Error(t('Elegí un chasis y armá el diseño antes de guardarlo como plantilla.'));
    if(pending())throw Error(t('Guardá o descartá el cambio que estás ajustando antes de guardar la plantilla.'));
    const current=recipe();
    // A new design on a built vehicle starts from that vehicle's recipe: the
    // target keeps its own. A built vehicle being edited carries its history.
    if(current.draft&&!state.editing)delete current.transform.assembly_draft.seed;
    const transform=designFromRecipe(current.transform,current);
    const name=($('template-name').value||$('vehicle-name').value||labelOf(state.hull.template)).trim();
    if(templates.some(row=>row.name===name)&&!confirm(t('Ya hay una plantilla con ese nombre. ¿Reemplazarla?')))return;
    const data=await api('/api/design-templates',{action:'save',name,chassis:state.hull.template,label:labelOf(state.hull.template),transform});
    templates=data.templates||[];$('template-name').value='';render();$('template-select').value=name;controls();
    status(t('Plantilla guardada:')+' '+name);
  }
  async function apply(){
    const row=chosen();if(!row)return;
    if(!appliable())throw Error(t('Elegí el vehículo con CHASIS o con EDITAR para aplicarle la plantilla.'));
    if(plainEdit()){
      const changed=Object.entries(state.transform).some(([k,v])=>!['hull','turret','crew_static','source_recipe','commander_only'].includes(k)&&!empty(v))||moved(state.transform.turret)||moved(state.transform.hull);
      if(row.chassis!==state.hull.template)throw Error(t('Esta plantilla es para otro modelo de chasis:')+' '+(row.label||labelOf(row.chassis)));
      const transform=recipeFromDesign(row.transform,{recipient_mount:state.mounts.hull,donor_mount:state.mounts.turret});
      if(changed&&!confirm(t('Aplicar la plantilla reemplaza los cambios de este diseño. ¿Continuar?')))return;
      remember();await applyRecipe(transform);status(t('Plantilla aplicada:')+' '+row.name);return;
    }
    const result=templateTransform(row,{hull:state.hull,campaign:state.campaign,seed:draftEditor.draft()?.seed});
    if(result.missing.length)throw Error(t('Para aplicar esta plantilla faltan estos vehículos donantes en tu partida:')+'\n'+
      result.missing.map(m=>`· ${labelOf(m.template)} ×${m.count}`).join('\n'));
    const d=draftEditor.draft()||{},changed=d.guns?.length||d.turrets?.length||d.removals?.length||
      Object.entries(state.transform).some(([k,v])=>!['hull','turret','assembly_draft','crew_static'].includes(k)&&!empty(v));
    if(changed&&!confirm(t('Aplicar la plantilla reemplaza los cambios de este diseño. ¿Continuar?')))return;
    remember();await applyDesign(result.transform);
    status(t('Plantilla aplicada:')+' '+row.name+(result.donors.length?' · '+t('donantes elegidos:')+' '+result.donors.length:''));
  }
  async function remove(){
    const row=chosen();if(!row||!confirm(t('¿Borrar la plantilla?')+' '+row.name))return;
    const data=await api('/api/design-templates',{action:'delete',name:row.name});templates=data.templates||[];render();
  }
  const run=fn=>async()=>{try{await fn();}catch(error){reportError(error);}};
  $('template-save').onclick=run(save);$('template-apply').onclick=run(apply);$('template-delete').onclick=run(remove);
  $('template-select').onchange=controls;
  return {load,render,controls};
}
