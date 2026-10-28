import {t} from './i18n.js';

// Navigation owns editing focus only. Recipes remain owned by the existing editors.
export const INSPECTOR_TOOLS={
  chassis:{label:'Posición del chasis',fields:[],part:'hull',pose:true},
  position:{label:'Posición y montaje',fields:['design-turret-pos-field','mount-select'],pose:true},
  joints:{label:'Límites de torreta y cañón',fields:['design-turret-limits-field','weapon-joints']},
  crew:{label:'Tripulantes',fields:['design-module-crew-field','crew-pending-field','crew-static-field','crew-field','crew-visibility-field']},
  commander:{label:'Arma del comandante',fields:['commander-field'],part:'commander_mg',pose:true},
  armor:{label:'Blindaje extra',fields:['armor-section'],part:'extra_armor',pose:true},
  smoke:{label:'Lanzadores de humo',fields:['smoke-section'],part:'smoke_launchers',pose:true},
  rider:{label:'MG de pasajero',fields:['rider-section'],part:'rider_mgs',pose:true},
  towing:{label:'Remolque',fields:['towing-section'],part:'towing',pose:true},
  charge:{label:'Carga explosiva',fields:['charge-section'],part:'charge',pose:true},
  passengers:{label:'Pasajeros',fields:['passenger-section'],part:'passenger_seats',pose:true},
  cargo:{label:'Servicio y carga',fields:['support-fields'],part:'turret',pose:true},
  operation:{label:'Operación y piezas',fields:['operation-controls'],selection:true},
};

export function inspectorNavigation({busy=()=>false,support=()=>false,gun=()=>false,movingMount=()=>false,focusPart=()=>null,ready=()=>true,onEnter=()=>{},onLeave=()=>{},onChange=()=>{}}={}){
  let section='hull',tool=null;
  const part=()=>INSPECTOR_TOOLS[tool]?.part||(['position','joints','crew'].includes(tool)&&focusPart())||((tool==='crew'||tool==='position'&&(gun()||movingMount()))?'turret':section);
  const available=name=>!!INSPECTOR_TOOLS[name]&&(name!=='chassis'||section==='hull')&&(!['commander','cargo'].includes(name)||
    (name==='commander'?section==='turret'&&!support():section==='hull'&&support()));
  return {
    get section(){return section;},get tool(){return tool;},part,available,
    accepts(value){return tool&&tool!=='operation'?value===part():['hull','turret'].includes(value);},
    pose(){return !!INSPECTOR_TOOLS[tool]?.pose;},
    select(value){if(busy()||tool||!['hull','turret'].includes(value))return false;section=value;onChange();return true;},
    open(value){if(busy()||tool||(value!=='operation'&&!ready())||!available(value))return false;tool=value;onChange();onEnter(value,part());return true;},
    finish(){if(busy()||!tool)return false;const previous=tool;tool=null;onLeave(previous);onChange();return true;},
    reset(){if(tool){const previous=tool;tool=null;onLeave(previous);}onChange();},
  };
}

export function inspectorMenus({document=globalThis.document,state,support,gun=()=>false,movingMount=()=>false,onEnter,onLeave,onChange,moveSelector=(select,field)=>{const label=select.labels?.[0];if(label)field.append(label);field.append(select);}}){
  const $=id=>document.getElementById(id),home=$('inspector-home'),content=$('inspector-tool-content');
  const panels={},buttons={};
  const context=$('working-target-slot'),selectors={};
  for(const [key,spec] of Object.entries(INSPECTOR_TOOLS)){
    const button=document.createElement('button');button.type='button';button.className='inspector-tool-button';
    button.textContent=t(spec.label);button.dataset.help=t('Abre únicamente esta herramienta. Volvé al menú para elegir otra.');
    button.setAttribute('aria-controls','inspector-panel-'+key);button.setAttribute('aria-expanded','false');
    home.append(button);buttons[key]=button;
    const panel=document.createElement('div');panel.id='inspector-panel-'+key;panel.className='hidden';
    panel.setAttribute('role','group');panel.setAttribute('aria-label',t(spec.label));content.append(panel);panels[key]=panel;
    for(const id of spec.fields)panel.append(id==='mount-select'?$(id).parentElement:$(id));
    button.onclick=()=>{if(navigation.open(key))$('inspector-tool-title').focus?.();};
  }
  // Reparent the existing controls: the heading and the tool always share
  // the same selection, handlers and stable component identities.
  for(const id of ['turret-target','design-turret-pos','design-turret-limits','design-module-crew',
    'gun-recipient','armor-list','smoke-list','rider-list','passenger-list','cargo-list','cmd-parent']){
    const select=$(id),field=document.createElement('div');field.className='field working-target-field';
    moveSelector(select,field);context.append(field);selectors[id]=field;
  }
  const fixed=document.createElement('div');fixed.className='field';const fixedLabel=document.createElement('label');
  fixedLabel.textContent=t('Parte que estás editando');const fixedSelect=document.createElement('select');fixedSelect.disabled=true;
  fixed.append(fixedLabel);fixed.append(fixedSelect);context.append(fixed);
  const navigation=inspectorNavigation({busy:()=>state.busy||state.inspectorPending>0,support,gun,movingMount,focusPart:()=>state.inspectorFocusPart,
    ready:()=>!!(state.hull&&state.turret),onEnter,onLeave,onChange(){render();onChange?.();}});
  function render(){
    const editing=!!navigation.tool,busy=state.busy||state.inspectorPending>0;
    const contextKey=({operation:gun()?'gun-recipient':'turret-target',position:gun()?'gun-recipient':'design-turret-pos',
      joints:'design-turret-limits',crew:'design-module-crew',armor:'armor-list',smoke:'smoke-list',rider:'rider-list',
      passengers:'passenger-list',cargo:'cargo-list',commander:'cmd-parent'})[navigation.tool];
    const choices=contextKey&&$(contextKey).options;
    const usable=editing&&contextKey&&!!state.hull&&(!choices||choices.length>0);
    // Change gun reads top to bottom: operation, the recipient gun, then the
    // donor gun right under it. The recipient gun leaves the heading slot for
    // the panel; any other tool takes it back.
    const inline=navigation.tool==='operation'&&gun(),recipient=selectors['gun-recipient'],donor=$('gun-donor');
    // The donor field's own label (searchable lists drop the label's "for").
    let donorStart=donor;while(donorStart?.previousElementSibling&&donorStart.tagName!=='LABEL')donorStart=donorStart.previousElementSibling;
    if(inline&&donorStart&&recipient.nextElementSibling!==donorStart)donorStart.parentElement.insertBefore(recipient,donorStart);
    else if(!inline&&recipient.parentElement!==context)context.insertBefore(recipient,fixed);
    recipient.classList.toggle('working-target-field',!inline);
    context.classList.toggle('hidden',!editing||inline);context.inert=busy;
    for(const [key,field] of Object.entries(selectors))field.classList.toggle('hidden',inline?key!=='gun-recipient':!usable||key!==contextKey);
    fixed.classList.toggle('hidden',!!usable);
    fixedSelect.textContent='';const option=document.createElement('option');option.textContent=t(navigation.tool==='chassis'?'Chasis completo':state.hull?'Elegí una pieza en esta herramienta':'Elegí un vehículo');fixedSelect.append(option);
    home.classList.toggle('hidden',editing);$('inspector-tool').classList.toggle('hidden',!editing);
    $('inspector-overview').classList.toggle('hidden',editing);$('inspector-build').classList.toggle('hidden',editing);
    const placed={commander:!!state.transform.commander_mg,armor:!!state.transform.extra_armor?.length,
      smoke:!!state.transform.smoke_launchers?.length,towing:!!state.transform.towing,charge:!!state.transform.charge,rider:!!state.transform.rider_mgs?.length,passengers:!!state.transform.passenger_seats?.added?.length,cargo:!!state.transform.support_vehicle?.cargo?.length};
    const gunReady=navigation.tool!=='position'||!gun()||!!(state.transform.gun_swap?.recipient_place&&state.transform.gun_swap?.donor_place);
    const pose=navigation.pose()&&(placed[navigation.tool]??true)&&gunReady;
    $('inspector-transforms').classList.toggle('hidden',!pose);
    $('inspector-tool').setAttribute('aria-busy',String(state.inspectorPending>0));content.inert=busy;$('inspector-transforms').inert=busy;$('inspector-history').inert=busy;
    for(const id of ['mode-move','mode-rotate'])$(id).disabled=busy||!(pose||navigation.tool==='crew');
    for(const id of ['pose-move','pose-rotate'])$(id).disabled=busy||!pose;
    $('inspector-history').classList.toggle('hidden',!editing);
    $('inspector-finish').disabled=busy;
    $('inspector-cancel').classList.toggle('hidden',!state.toolCancellable);
    $('inspector-tool-title').textContent=t(navigation.tool==='position'&&gun()?'Posición del cañón':INSPECTOR_TOOLS[navigation.tool]?.label||'Ajustes');
    const working=usable&&choices?choices[$(contextKey).selectedIndex]?.text:null;
    $('inspector-context').textContent=working||t(navigation.section==='hull'?'Chasis':'Torreta');
    for(const section of ['hull','turret']){
      const button=$('edit-'+section);button.disabled=busy||editing;
      button.classList.toggle('active',section===navigation.section);button.setAttribute('aria-pressed',String(section===navigation.section));
    }
    for(const [key,button] of Object.entries(buttons)){
      if(key==='position')button.textContent=t(gun()?'Posición del cañón':'Posición y montaje');
      button.classList.toggle('hidden',!navigation.available(key));button.disabled=busy||(key!=='operation'&&(!state.hull||!state.turret));
      button.setAttribute('aria-expanded',String(navigation.tool===key));panels[key].classList.toggle('hidden',navigation.tool!==key);
    }
    const locked=editing&&navigation.tool!=='operation';
    for(const id of ['operation-select','pick-hull','pick-turret','pick-edit','clear-hull','clear-turret','load-save','open-save','apply-settings'])$(id).disabled=busy||locked;
    for(const id of ['repair-button','portraits-button'])$(id).disabled=busy||editing;
    // The open tool's own working selector stays usable: Gun placement switches
    // between the changed guns with it.
    for(const [id,part] of [['gun-recipient','hull'],['gun-donor','turret']])$(id).disabled=busy||(locked&&id!==contextKey)||!!state.editing||!state[part];
    if(support())$('pick-turret').disabled=true;
    if(editing)$('build-button').disabled=true;
  }
  for(const section of ['hull','turret'])$('edit-'+section).onclick=()=>{state.inspectorFocusPart=null;navigation.select(section);};
  $('inspector-finish').onclick=()=>{const previous=navigation.tool;
    if(navigation.finish())buttons[previous]?.focus();};
  render();return {navigation,render};
}
