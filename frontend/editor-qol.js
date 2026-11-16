import {t} from './i18n.js';
import {uiStorage,saveUISettings} from './ui-settings.js';
import {DEFAULT_BINDINGS,editorOptions,chord,snapping} from './editor-options.js';
import {isTextEntry} from './accessibility.js';
export function editorQoL({$,document=globalThis.document,control,controls=[control],actions,visibility,target,reselect,reportError,status,recovery,restore}){
  const forControls=(...args)=>controls.filter(Boolean).forEach(c=>snapping(c,...args));
  let options;try{options=editorOptions(JSON.parse(uiStorage.getItem('cw.editorOptions')||'{}'));}catch{options=editorOptions();}
  let viewSelection=null,offered=null;
  const current=()=>viewSelection?.parent?viewSelection:target();
  const labels={move:'Mover',rotate:'Girar',space:'Local / Global',frame:'Encuadrar',hide:'Ocultar selección',isolate:'Aislar selección',show:'Mostrar todo',lock:'Bloquear selección',undo:'Deshacer',redo:'Rehacer',save:'Guardar borrador',cancel:'Cancelar',search:'Buscar vehículos',help:'Ayuda',snap_hold:'Ajustar mientras se mantiene',fine_hold:'Movimiento fino mientras se mantiene'};
  const modifierKeys=['Control','Shift','Alt','Meta'];
  function saved(){uiStorage.setItem('cw.editorOptions',JSON.stringify(options));forControls(options);saveUISettings({editor_options:options}).catch(reportError);}
  function renderOptions(){
    for(const key of ['autosave','snap'])$('qol-'+key).checked=options[key];
    for(const key of ['move_step','angle_step','fine_factor'])$('qol-'+key).value=options[key];
    $('qol-snap-toggle').classList.toggle('active',options.snap);$('qol-snap-toggle').setAttribute('aria-pressed',String(options.snap));
    const list=$('qol-bindings');list.replaceChildren();
    for(const [action,label] of Object.entries(labels)){
      const row=document.createElement('label');row.textContent=t(label);const input=document.createElement('input');input.value=options.bindings[action];input.readOnly=true;input.setAttribute('aria-label',t(label));input.dataset.binding=action;
      input.onkeydown=event=>{event.preventDefault();event.stopPropagation();const value=action.endsWith('_hold')?(modifierKeys.includes(event.key)?event.key:null):event.key==='Backspace'?'':chord(event);if(value===null)return;
        const duplicate=Object.entries(options.bindings).some(([k,v])=>k!==action&&k.endsWith('_hold')===action.endsWith('_hold')&&v&&v===value);
        if(duplicate){$('qol-key-message').textContent=t('Ese atajo ya está asignado.');return;}
        $('qol-key-message').textContent='';options.bindings[action]=value;input.value=value;saved();};row.append(input);list.append(row);
    }
    for(const [id,action] of [['mode-move','move'],['mode-rotate','rotate'],['fit-view','frame'],['qol-hide','hide'],['qol-isolate','isolate'],['qol-show','show'],['qol-lock','lock']])$(id).dataset.help=t(labels[action])+' · '+options.bindings[action];
  }
  for(const key of ['autosave','snap','move_step','angle_step','fine_factor'])$('qol-'+key).onchange=()=>{const input=$('qol-'+key),value=input.type==='checkbox'?input.checked:Number(input.value);if(input.type!=='checkbox'&&(!Number.isFinite(value)||value<.001||value>({angle_step:360,fine_factor:1}[key]||10000))){renderOptions();return;}options[key]=value;saved();renderOptions();};
  $('qol-reset').onclick=()=>{options=editorOptions();saved();renderOptions();};
  $('qol-snap-toggle').onclick=()=>{options.snap=!options.snap;saved();renderOptions();};
  const viewActions={hide:()=>visibility.hide(current()),isolate:()=>visibility.isolate(current()),show:()=>visibility.showAll(),lock:()=>visibility.lock(current())};
  for(const action of Object.keys(viewActions))$('qol-'+action).onclick=()=>{viewActions[action]();if(visibility.canEdit(target()))reselect();renderPieces();};
  function renderPieces(){
    const list=$('qol-pieces'),selected=list.value;list.replaceChildren();
    for(const row of visibility.rows().filter(row=>row.label.toLocaleLowerCase().includes($('qol-piece-search').value.toLocaleLowerCase()))){const option=document.createElement('option');option.value=row.key;option.textContent=(visibility.hidden.has(row.key)?'◌ ':'')+(visibility.locked.has(row.key)?'🔒 ':'')+row.label;list.append(option);}
    list.value=selected;
  }
  $('qol-piece-search').oninput=renderPieces;
  $('qol-pieces').onchange=()=>{viewSelection=visibility.rows().find(r=>r.key===$('qol-pieces').value)?.objects[0]||null;};
  $('qol-pieces-toggle').onclick=()=>{const panel=$('qol-pieces-panel');panel.classList.toggle('hidden');renderPieces();};
  document.addEventListener('keydown',event=>{
    if(document.querySelector('dialog[open]')||event.defaultPrevented)return;
    const action=Object.entries(options.bindings).find(([key,value])=>!key.endsWith('_hold')&&value&&value===chord(event))?.[0];
    if(isTextEntry(event.target)&&!['save','search'].includes(action))return;
    forControls(options,event);
    if(!action||event.repeat)return;
    if(actions.blocked()&&action!=='cancel')return;
    event.preventDefault();event.stopImmediatePropagation();
    const run=viewActions[action]||actions[action];if(run)Promise.resolve(run()).catch(reportError);
    renderPieces();
  },true);
  document.addEventListener('keyup',event=>forControls(options,event));
  document.addEventListener('pointerdown',event=>forControls(options,event),true);
  globalThis.addEventListener?.('blur',()=>forControls(options));
  $('qol-recover').onclick=async()=>{if(!offered)return;recovery.pause();try{await restore(offered);$('qol-recovery').classList.add('hidden');offered=null;recovery.resume();status(t('Borrador recuperado.'));}catch(error){reportError(error);}};
  $('qol-discard').onclick=async()=>{try{await recovery.discard();offered=null;$('qol-recovery').classList.add('hidden');recovery.resume();}catch(error){reportError(error);}};
  renderOptions();forControls(options);
  return {get options(){return options;},renderPieces,
    select(){viewSelection=null;},
    async offer(){const saved=await recovery.offer();offered=saved.draft;if(offered){$('qol-recovery').classList.remove('hidden');$('qol-recovery-time').textContent=saved.saved_utc?new Date(saved.saved_utc).toLocaleString():'';}else recovery.resume();},
  };
}
