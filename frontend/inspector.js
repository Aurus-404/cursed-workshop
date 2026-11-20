import {t} from './i18n.js';

// Navigation owns editing focus only. Recipes remain owned by the existing editors.
export const INSPECTOR_TOOLS={
  position:{hint:'Colocá la torreta o el arma y elegí con qué parte gira.',label:'Posición y montaje',fields:['design-turret-pos-field','mount-select','module-host-field'],pose:true},
  joints:{hint:'Ajustá cuánto giran y se elevan la torreta y el cañón, y a qué velocidad.',label:'Límites de torreta y cañón',fields:['design-turret-limits-field','weapon-joints']},
  mobility:{hint:'Cambiá la velocidad, el giro y la potencia del chasis.',label:'Movilidad del chasis',fields:['mobility-field'],part:'hull'},
  sight:{hint:'Colocá la mira invisible y elegí qué pieza acompaña.',label:'Mira del vehículo',fields:['sight-field'],part:'sight',pose:true},
  weapons:{hint:'Cambiá recarga, cadencia, alcance, munición, penetración y daño de cada arma del vehículo.',label:'Atributos de armas',fields:['weapons-field']},
  crew:{hint:'Agregá, mové, ocultá o fijá a los tripulantes del vehículo.',label:'Tripulantes',fields:['design-module-crew-field','crew-pending-field','extra-crew-field','crew-static-field','crew-field','weapon-operators-field','crew-visibility-field']},
  commander:{hint:'Agregá un arma que usa el comandante desde su escotilla.',label:'Arma del comandante',fields:['commander-field'],part:'commander_mg',pose:true},
  armor:{hint:'Agregá piezas de protección: del juego o una placa a medida.',label:'Blindaje extra',fields:['armor-section'],part:'extra_armor',pose:true},
  smoke:{hint:'Agregá tubos lanzahumo al vehículo.',label:'Lanzadores de humo',fields:['smoke-section'],part:'smoke_launchers',pose:true},
  rider:{hint:'Agregá una ametralladora que usa quien suba al vehículo.',label:'MG de pasajero',fields:['rider-section'],part:'rider_mgs',pose:true},
  mounted:{hint:'Agregá un arma en cualquier parte del vehículo, sin torreta.',label:'Armas sin torreta',fields:['mounted-section'],part:'mounted_weapons',pose:true},
  towing:{hint:'Permití que el vehículo remolque cañones y elegí dónde va el enganche.',label:'Remolque',fields:['towing-section'],part:'towing',pose:true},
  charge:{hint:'Convertí el vehículo en una carga explosiva que detona el conductor.',label:'Carga explosiva',fields:['charge-section'],part:'charge',pose:true},
  passengers:{hint:'Agregá o mové asientos para pasajeros.',label:'Pasajeros',fields:['passenger-section'],part:'passenger_seats',pose:true},
  cargo:{hint:'Elegí el servicio de apoyo y la carga visible.',label:'Servicio y carga',fields:['support-fields'],part:'turret',pose:true},
  operation:{hint:'Elegí qué operación hacer y con qué piezas.',label:'Operación y piezas',fields:['operation-controls'],selection:true},
};

// The menu groups the tools by what they change, so the list stays short: each
// category folds open or shut, and remembers it.
export const INSPECTOR_CATEGORIES=[
  ['weapons','Armas y piezas',['operation','position','joints','mounted','weapons','commander','rider','sight']],
  ['crew','Tripulación y pasajeros',['crew','passengers']],
  ['chassis','Chasis y accesorios',['mobility','armor','smoke','towing','charge','cargo']],
];
const CATEGORY_KEY='cw.inspector-categories';
function foldedCategories(){try{const value=JSON.parse(globalThis.localStorage?.getItem(CATEGORY_KEY)||'[]');return new Set(Array.isArray(value)?value:[]);}catch{return new Set();}}
function saveFolded(folded){try{globalThis.localStorage?.setItem(CATEGORY_KEY,JSON.stringify([...folded]));}catch{}}

export function inspectorNavigation({busy=()=>false,support=()=>false,gun=()=>false,movingMount=()=>false,focusPart=()=>null,ready=()=>true,onEnter=()=>{},onLeave=()=>{},onChange=()=>{}}={}){
  // One menu for the whole vehicle: no Chassis/Turret tab. A tool on a part
  // names it; Position, Limits and Crew pick theirs in their own selector,
  // the turret (or the changed gun) until another part is chosen there.
  let tool=null;
  const part=()=>INSPECTOR_TOOLS[tool]?.part||(['position','joints','crew'].includes(tool)&&focusPart())||(['position','joints','crew'].includes(tool)?'turret':'hull');
  const available=name=>!!INSPECTOR_TOOLS[name]&&(!['commander','cargo'].includes(name)||(name==='commander'?!support():support()));
  return {
    get tool(){return tool;},part,available,
    accepts(value){return tool&&tool!=='operation'?value===part():['hull','turret'].includes(value);},
    pose(){return !!INSPECTOR_TOOLS[tool]?.pose;},
    open(value){if(busy()||tool||(value!=='operation'&&!ready())||!available(value))return false;tool=value;onChange();onEnter(value,part());return true;},
    finish(){if(busy()||!tool)return false;const previous=tool;tool=null;onLeave(previous);onChange();return true;},
    reset(){if(tool){const previous=tool;tool=null;onLeave(previous);}onChange();},
  };
}

export function inspectorMenus({document=globalThis.document,state,support,gun=()=>false,movingMount=()=>false,poseAvailable=()=>null,onEnter,onLeave,onChange,moveSelector=(select,field)=>{const label=select.labels?.[0];if(label)field.append(label);field.append(select);}}){
  const $=id=>document.getElementById(id),home=$('inspector-home'),content=$('inspector-tool-content');
  const panels={},buttons={},categories={},folded=foldedCategories();
  const context=$('working-target-slot'),selectors={};
  const categoryOf={};
  for(const [id,label,keys] of INSPECTOR_CATEGORIES){
    const group=document.createElement('details');group.className='inspector-category';group.dataset.category=id;group.open=!folded.has(id);
    const summary=document.createElement('summary');summary.textContent=t(label);group.append(summary);
    group.ontoggle=()=>{if(group.open)folded.delete(id);else folded.add(id);saveFolded(folded);};
    home.append(group);categories[id]=group;for(const key of keys)categoryOf[key]=group;
  }
  for(const [key,spec] of Object.entries(INSPECTOR_TOOLS)){
    const button=document.createElement('button');button.type='button';button.className='inspector-tool-button';
    button.textContent=t(spec.label);button.dataset.help=t('Abre únicamente esta herramienta. Volvé al menú para elegir otra.');
    button.setAttribute('aria-controls','inspector-panel-'+key);button.setAttribute('aria-expanded','false');
    (categoryOf[key]||home).append(button);buttons[key]=button;
    const panel=document.createElement('div');panel.id='inspector-panel-'+key;panel.className='hidden';
    panel.setAttribute('role','group');panel.setAttribute('aria-label',t(spec.label));content.append(panel);panels[key]=panel;
    for(const id of spec.fields)panel.append(id==='mount-select'?$(id).parentElement:$(id));
    button.onclick=()=>{if(navigation.open(key))$('inspector-tool-title').focus?.();};
  }
  // Within a category, its own order (Operation and parts first).
  for(const [id,,keys] of INSPECTOR_CATEGORIES)for(const key of keys)if(buttons[key])categories[id].append(buttons[key]);
  // Reparent the existing controls: the heading and the tool always share
  // the same selection, handlers and stable component identities.
  for(const id of ['turret-target','design-turret-pos','design-turret-limits','design-module-crew',
    'gun-recipient','armor-list','smoke-list','rider-list','passenger-list','cargo-list','cmd-parent','weapons-select']){
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
      passengers:'passenger-list',cargo:'cargo-list',commander:'cmd-parent',weapons:'weapons-select'})[navigation.tool];
    const choices=contextKey&&$(contextKey).options;
    const usable=editing&&contextKey&&!!state.hull&&(!choices||choices.length>0);
    // Change gun reads top to bottom: operation, the recipient gun, then the
    // donor gun right under it. The recipient gun leaves the heading slot for
    // the panel; any other tool takes it back.
    const inline=navigation.tool==='operation'&&gun(),recipient=selectors['gun-recipient'],donor=$('gun-donor-field')||$('gun-donor');
    // The donor field's own label (searchable lists drop the label's "for").
    let donorStart=donor;while(donorStart?.previousElementSibling&&donorStart.tagName!=='LABEL')donorStart=donorStart.previousElementSibling;
    if(inline&&donorStart&&recipient.nextElementSibling!==donorStart)donorStart.parentElement.insertBefore(recipient,donorStart);
    else if(!inline&&recipient.parentElement!==context)context.insertBefore(recipient,fixed);
    recipient.classList.toggle('working-target-field',!inline);
    context.classList.toggle('hidden',!editing||inline);context.inert=busy;
    // Placed armor is reset by removing it; a turret seat means nothing there.
    $('align-button').parentElement?.classList.toggle('hidden',navigation.tool==='armor');
    for(const [key,field] of Object.entries(selectors))field.classList.toggle('hidden',inline?key!=='gun-recipient':!usable||key!==contextKey);
    fixed.classList.toggle('hidden',!!usable);
    fixedSelect.textContent='';const option=document.createElement('option');option.textContent=t(navigation.tool==='chassis'?'Chasis completo':state.hull?'Elegí una pieza en esta herramienta':'Elegí un vehículo');fixedSelect.append(option);
    home.classList.toggle('hidden',editing);$('inspector-tool').classList.toggle('hidden',!editing);
    $('inspector-overview').classList.toggle('hidden',editing);$('inspector-build').classList.toggle('hidden',editing);
    const placed={commander:!!state.transform.commander_mg,armor:!!state.transform.extra_armor?.length,
      smoke:!!state.transform.smoke_launchers?.length,towing:!!state.transform.towing,charge:!!state.transform.charge,rider:!!state.transform.rider_mgs?.length,mounted:!!(state.transform.mounted_weapons?.added?.length||state.transform.mounted_weapons?.slots?.length),passengers:!!state.transform.passenger_seats?.added?.length,cargo:!!state.transform.support_vehicle?.cargo?.length};
    const gunReady=navigation.tool!=='position'||!gun()||!!(state.transform.gun_swap?.recipient_place&&state.transform.gun_swap?.donor_place);
    const pose=navigation.pose()&&(poseAvailable()??placed[navigation.tool]??true)&&gunReady;
    const actions=$('pose-actions'),slot=$(navigation.tool==='armor'?'armor-pose-slot':'pose-actions-home');
    if(actions.parentElement!==slot)slot.append(actions);
    actions.classList.toggle('hidden',!pose);actions.inert=busy;
    $('inspector-transforms').classList.toggle('hidden',!pose);
    // A weapon without a turret traverses on its own joint, where it is placed:
    // it has no separate pivot to move.
    const pivotless=navigation.tool==='mounted';if(pivotless)state.editPivot=false;
    $('pose-pivot').classList.toggle('hidden',pivotless);$('pivot-fields').parentElement?.classList.toggle('hidden',pivotless);
    $('inspector-tool').setAttribute('aria-busy',String(state.inspectorPending>0));content.inert=busy;$('inspector-transforms').inert=busy;$('inspector-history').inert=busy;
    for(const id of ['mode-move','mode-rotate'])$(id).disabled=busy||!(pose||navigation.tool==='crew');
    for(const id of ['pose-move','pose-rotate','mirror-0','mirror-1','mirror-2'])$(id).disabled=busy||!pose;
    $('inspector-history').classList.toggle('hidden',!editing);
    $('inspector-finish').disabled=busy;
    $('inspector-cancel').classList.toggle('hidden',!state.toolCancellable);
    $('inspector-tool-title').textContent=t(navigation.tool==='position'&&gun()?'Posición del cañón':INSPECTOR_TOOLS[navigation.tool]?.label||'Ajustes');
    // One plain sentence on what the open tool is for.
    const hint=$('inspector-tool-hint');if(hint)hint.textContent=INSPECTOR_TOOLS[navigation.tool]?.hint?t(INSPECTOR_TOOLS[navigation.tool].hint):'';
    const working=usable&&choices?choices[$(contextKey).selectedIndex]?.text:null;
    $('inspector-context').textContent=working||t(navigation.part()==='hull'?'Chasis':navigation.part()==='turret'?'Torreta':'Vehículo');
    for(const [key,button] of Object.entries(buttons)){
      if(key==='position')button.textContent=t(gun()?'Posición del cañón':'Posición y montaje');
      button.classList.toggle('hidden',!navigation.available(key));button.disabled=busy||(key!=='operation'&&(!state.hull||!state.turret));
      button.setAttribute('aria-expanded',String(navigation.tool===key));panels[key].classList.toggle('hidden',navigation.tool!==key);
    }
    // A category with nothing to offer here (Service and cargo only on support vehicles) is not shown.
    for(const [id,,keys] of INSPECTOR_CATEGORIES)categories[id].classList.toggle('hidden',!keys.some(key=>buttons[key]&&!buttons[key].classList.contains('hidden')));
    const locked=editing&&navigation.tool!=='operation';
    for(const id of ['operation-select','pick-hull','pick-turret','pick-edit','clear-hull','clear-turret','load-save','open-save','apply-settings'])$(id).disabled=busy||locked;
    for(const id of ['repair-button','portraits-button'])$(id).disabled=busy||editing;
    // The open tool's own working selector stays usable: Gun placement switches
    // between the changed guns with it.
    // An MG / AT rifle donor (state.gunMountable) needs no unit: the recipient stays open while editing.
    for(const [id,part] of [['gun-recipient','hull'],['gun-donor','turret']])$(id).disabled=busy||(locked&&id!==contextKey)||!!state.editing&&!(id==='gun-recipient'&&state.gunMountable)||!state[part];
    if(support())$('pick-turret').disabled=true;
    if(editing)$('build-button').disabled=true;
  }
  $('inspector-finish').onclick=()=>{const previous=navigation.tool;
    if(navigation.finish())buttons[previous]?.focus();};
  render();return {navigation,render};
}
