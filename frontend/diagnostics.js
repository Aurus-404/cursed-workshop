// Support reports are text only. Never serialize application state or render it as HTML.
import { englishText, t } from './i18n.js';

export function cleanText(value, secret='') {
  let s=String(value??'').slice(0,16000);
  if(secret)s=s.split(secret).join('[redacted]');
  return s.replace(/(?:https?|file):\/\/[^\s<>"]+/gi,'[link removed]')
    .replace(/(?:[a-z]:[\\/]|\\\\)[^\r\n"<>|]*/gi,'[local path]')
    .replace(/(^|[^\w/])\/(?:home|Users|tmp|var|opt)\/[^\s"<>]*/g,'$1[local path]')
    .replace(/\b(token|password|secret|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi,'$1=[redacted]')
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g,'')
    .replace(/`/g,"'").replace(/@/g,'[at]').slice(0,1400);
}

export function selectionData(state) {
  const id=value=>cleanText(value,state.token).slice(0,150);
  const vector=value=>Array.isArray(value)&&value.length<=3&&value.every(Number.isFinite)?value:null;
  const pose=value=>({position:vector(value?.position),rotation:vector(value?.rotation),...(value?.pivot?{pivot:vector(value.pivot)}:{})});
  const tr=state.transform||{},cmd=tr.commander_mg;
  const result={mode:state.editing?'edit':'combine',hull:id(state.hull?.template),module:id(state.turret?.template),
    hull_mount:id(state.mounts?.hull),module_mount:id(state.mounts?.turret),hull_pose:pose(tr.hull),module_pose:pose(tr.turret)};
  result.crew_static=typeof state.crewStatic==='boolean'?state.crewStatic:null;
  result.commander_only=tr.commander_only===true;
  result.editor_operation=id(state.operation||'');result.selection_target=id(state.pick||'');
  if(state.lastSelection)result.last_selection={part:id(state.lastSelection.part),eid:id(state.lastSelection.eid),template:id(state.lastSelection.template)};
  if(tr.assembly_draft){const d=tr.assembly_draft;result.mode=state.editing?'edit_design':'design';
    const row=r=>({recipient_place:id(r.recipient_place),donor_place:id(r.donor_place),donor_template:id(r.donor_template),donor_eid:id(r.donor_eid),...pose(r.pose)});
    result.design={schema:d.schema,seed_recipe:!!d.seed,turrets:(d.turrets||(d.turret?[d.turret]:[])).map(r=>({...row(r),recipient_mount:id(r.recipient_mount||'turret')})),guns:(d.guns||[]).map(row)};}
  if(tr.gun_swap)result.gun_swap={schema:tr.gun_swap.schema,recipient_place:id(tr.gun_swap.recipient_place),donor_place:id(tr.gun_swap.donor_place)};
  if(Array.isArray(tr.gun_swaps))result.gun_swaps=tr.gun_swaps.map(row=>({schema:row.schema,recipient_place:id(row.recipient_place),donor_place:id(row.donor_place),pose:pose(row.pose)}));
  if(Array.isArray(tr.smoke_launchers))result.smoke_launchers=tr.smoke_launchers.map(row=>({tubes:row.tubes,parent:id(row.parent),...pose(row)}));
  if(tr.passenger_seats&&typeof tr.passenger_seats==='object')result.passenger_seats={added:(tr.passenger_seats.added||[]).map(row=>({pose:id(row.pose),parent:id(row.parent),...pose(row)})),
    native:Object.fromEntries(Object.entries(tr.passenger_seats.native||{}).map(([k,v])=>[id(k),{pose:id(v.pose),offset:Array.isArray(v.offset)?v.offset:undefined}]))};
  if(Array.isArray(tr.rider_mgs))result.rider_mgs=tr.rider_mgs.map(row=>({support:id(row.support),pose:id(row.pose),item:id(row.item),parent:id(row.parent),...pose(row)}));
  if(Array.isArray(tr.extra_armor))result.extra_armor=tr.extra_armor.map(row=>({asset:id(row.asset),parent:id(row.parent),...pose(row)}));
  if(tr.support_vehicle){const s=tr.support_vehicle;result.mode=state.editing?'edit_support':'support';
    result.support_vehicle={schema:s.schema,profile:id(s.profile),remove_mount:id(s.remove_mount),
      cargo:(Array.isArray(s.cargo)?s.cargo:[]).map(row=>({asset:id(row.asset),...pose(row)}))};}
  result.crew_hidden=(Array.isArray(tr.crew_hidden)?tr.crew_hidden:[]).slice(0,128).map(id);
  result.crew_excluded=(Array.isArray(tr.crew_exclude)?tr.crew_exclude:[]).slice(0,24).map(id);
  result.crew_poses=Object.entries(tr.crew_seats||{}).slice(0,24).map(([place,value])=>({place:id(place),...pose(value),space:value?.space==='module'?'module':'world'}));
  if(cmd)result.commander_mount={parent:id(cmd.parent),item:id(cmd.item),support:id(cmd.support),...pose(cmd),yaw:vector(cmd.yaw),pitch:vector(cmd.pitch)};
  return result;
}

function englishError(value) {
  return englishText(String(value??'').replace(/Detalle técnico:\s*/g,'\nTechnical detail:\n'));
}

export function diagnosticReport(data,state,lastError='',now=new Date()) {
  const text=value=>cleanText(value,state.token);
  const lines=['Cursed Workshop diagnostic / schema 1',`Generated (UTC): ${now.toISOString()}`,
    `App version: ${text(data.version||state.config?.version||'unknown')}`,
    `Build: ${text(data.build?.revision||data.build?.kind||'unknown')}${data.build?.dirty_source?' (modified source)':''}`,
    ...(data.build?.source_sha256?[`Build source SHA-256: ${text(data.build.source_sha256)}`]:[]),
    ...(data.build?.built_utc?[`Build generated (UTC): ${text(data.build.built_utc)}`]:[]),
    `Runtime: Python ${text(data.python||'unknown')} / ${text(data.platform||'unknown')}`,
    `UI language: ${text(document.documentElement.lang)}`,
    `Campaign loaded: ${data.unavailable?'unknown':data.campaign_loaded===true}`,`Resources indexed: ${data.unavailable?'unknown':data.resources_indexed===true}`,
    'This text: the editor as it is now. The diagnostic .zip adds the open campaign save, the game log, crash logs, the build history and every workshop vehicle, kept across sessions.',
    'Resource identifiers and external error details are literal data.', '', 'Active sources (load order):'];
  for(const source of (data.sources||[]).slice(0,60))lines.push(`- ${text(source.id)} / ${text(source.version)}`);
  if(data.sources_omitted)lines.push('Additional sources omitted.');
  lines.push('', 'Editor selection:',JSON.stringify(selectionData(state),null,2),'','Recent errors:');
  for(const error of (data.errors||[]).slice(-12)){
    lines.push(`${text(error.time_utc)} | ${text(englishText(error.operation))} | ${text(error.type)}`,
      text(englishError(error.message)));
    for(const frame of (error.frames||[]).slice(-8))lines.push('  at '+text(frame));
  }
  if(!(data.errors||[]).length)lines.push(data.unavailable?'Backend error history unavailable.':'No backend errors recorded in this session.');
  if(data.recent_operations?.length){
    lines.push('', 'Recent operation timings (milliseconds):');
    const ms=value=>Number.isFinite(value)&&value>=0?String(Math.round(value)):'unknown';
    for(const row of data.recent_operations.slice(-12))lines.push(`${text(row.time_utc)} | ${text(englishText(row.operation))} | ${text(row.status)} | ${ms(row.elapsed_ms)} ms (queued ${ms(row.queued_ms)} ms)`);
  }
  if(data.recent_phases?.length){
    lines.push('', 'Recent phase timings (milliseconds; included in operation totals):',
      'Phases may overlap; do not sum them.');
    for(const row of data.recent_phases.slice(-24)){
      const duration=Number.isFinite(row.elapsed_ms)&&row.elapsed_ms>=0?String(Math.round(row.elapsed_ms)):'unknown';
      lines.push(`${text(row.time_utc)} | ${text(englishText(row.operation))} | ${text(row.status)} | ${duration} ms`);
    }
  }
  if(lastError)lines.push('', 'Latest UI error:',text(englishError(lastError)));
  if(data.unavailable)lines.push('', 'Backend diagnostic unavailable; editor information only.');
  return lines.join('\n');
}

export function reportParts(report) {
  // Count UTF-16 units conservatively; never split surrogate pairs or exceed 2000.
  const chunks=[];let chunk='';
  for(const char of report){if(chunk.length+char.length>1700){chunks.push(chunk);chunk='';}chunk+=char;}
  if(chunk||!chunks.length)chunks.push(chunk);
  return chunks.map((body,i)=>`Cursed Workshop diagnostic ${i+1}/${chunks.length}\n\`\`\`text\n${body}\n\`\`\``);
}

export async function copyPart(area,clipboard) {
  try{if(!clipboard?.writeText)throw new Error('Clipboard unavailable');await clipboard.writeText(area.value);return true;}
  catch{area.focus();area.select();return false;}
}

export function diagnosticUI({state,api,$,selection=()=>state}) {
  let lastError='',loading=false;
  const requireCampaign=()=>{
    if(state.campaign)return true;
    $('diagnostic-text').value='';
    $('diagnostic-save').disabled=true;$('diagnostic-copy').disabled=true;
    $('diagnostic-status').textContent=t('Seleccioná primero una campaña.');
    return false;
  };
  const open=async()=>{
    if(loading)return;loading=true;
    if(!requireCampaign()){loading=false;$('diagnostic-dialog').showModal();return;}
    let data;
    try{data=await api('/api/diagnostic');}catch{data={unavailable:true};}
    finally{loading=false;}
    if(data.campaign_loaded===false||!state.campaign){
      $('diagnostic-text').value='';$('diagnostic-save').disabled=true;$('diagnostic-copy').disabled=true;
      $('diagnostic-status').textContent=t('Seleccioná primero una campaña.');
      $('diagnostic-dialog').showModal();return;
    }
    $('diagnostic-save').disabled=false;$('diagnostic-copy').disabled=false;
    $('diagnostic-text').value=diagnosticReport(data,selection(),lastError);
    $('diagnostic-copy').textContent=t('Copiar todo');$('diagnostic-status').textContent='';$('diagnostic-dialog').showModal();
  };
  $('diagnostic-button').onclick=open;$('error-diagnostic').onclick=open;
  $('diagnostic-copy').onclick=async()=>{
    if(!requireCampaign())return;
    const ok=await copyPart($('diagnostic-text'),navigator.clipboard);
    $('diagnostic-status').textContent=t(ok?'Informe completo copiado. Podés pegarlo como texto.':'El portapapeles no está disponible. El texto quedó seleccionado: usá Ctrl+C para copiarlo.');
  };
  // The zip adds what text cannot: the game's whole game.log and crash logs.
  $('diagnostic-save').onclick=async()=>{
    if(!requireCampaign())return;
    $('diagnostic-save').disabled=true;
    try{
      const result=await api('/api/error-report',{diagnostic:$('diagnostic-text').value});
      $('diagnostic-status').textContent=t(`Reporte guardado: ${result.path}. Subilo a #bug-reports en el Discord del mod.`);
    }catch(error){$('diagnostic-status').textContent=t('No se pudo guardar el reporte: ')+cleanText(error?.message||error,state.token);}
    finally{$('diagnostic-save').disabled=!state.campaign;}
  };
  return {record:error=>{lastError=cleanText(error?.message||error,state.token);}};
}
