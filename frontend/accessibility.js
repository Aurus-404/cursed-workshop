import {t} from './i18n.js';

export function isTextEntry(target){return !!target&&(target.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(target.tagName));}

export function accessibility({document=globalThis.document,window=globalThis.window,busy=()=>false}={}){
  const $=id=>document.getElementById(id);
  const help={
    'help-button':'Abrí la ayuda para consultar los controles y atajos.',
    'theme-select':'Elegí una paleta; se aplica al instante sin perder el ensamblaje.',
    'text-size':'Aumenta el tamaño de textos y controles.', 'reduce-motion':'Desactiva transiciones y animaciones decorativas.',
    'show-help':'Muestra explicaciones al pasar el mouse y al usar Tab.',
    'appearance-reset':'Restaura el tema, tamaño, ayudas y movimiento; no toca la partida.',
    'diagnostic-button':'Copia información de esta sesión como texto, sin adjuntar tu partida.',
    'error-diagnostic':'Copiar diagnóstico al portapapeles',
    'unit-search':'Filtra la lista sin modificar la partida.', 'vehicle-name':'Escribí un nombre para el vehículo que vas a construir.',
    'build-button':'Revisá las unidades usadas y el backup antes de confirmar.',
    'edit-commander':'Ajusta el punto donde se fija la montura, sin agregar tripulantes.',
    'cmd-enabled':'Solo el comandante. No agrega tripulantes. Elegí Montura para mover su pivote y orientarla con las flechas.',
    'cmd-item':'El arma inicial solo puede ser del juego base y tener la categoría mountable.',
    'cmd-support':'Elegí el soporte y su pose nativa; después ajustá su posición.',
    'cmd-parent':'El padre determina si la montura sigue al casco o a la torreta.',
    'smoke-parent':'El padre determina si la montura sigue al casco o a la torreta.',
    'smoke-edit':'La pieza verde es una vista previa. Agregala y usá Mover, Girar o XYZ. Las flechas muestran la dirección de lanzamiento.',
    'joint-select':'Elegí una articulación para ver y modificar sus límites.', 'joint-reset':'Devuelve esta articulación a sus límites originales.',
    'sources-button':'Elegí los recursos que usa tu campaña, en su orden de carga.',
  };
  const bubble=document.createElement('div');bubble.id='control-help';bubble.className='control-help hidden';bubble.setAttribute('role','tooltip');
  let owner,described,timer;
  function hide(){
    clearTimeout(timer);bubble.classList.add('hidden');
    if(described){const ids=(described.getAttribute('aria-describedby')||'').split(' ').filter(id=>id&&id!==bubble.id);if(ids.length)described.setAttribute('aria-describedby',ids.join(' '));else described.removeAttribute('aria-describedby');}
    owner=null;described=null;
  }
  function show(target,instant=false){
    const control=target?.closest?.('[data-help],[title]');if(control===owner&&!instant)return;
    hide();if(!control||control===document.body||document.body.dataset.helpEnabled==='false')return;
    owner=control;described=target.matches?.('button,input,select,textarea')?target:control;
    const render=()=>{
      if(!owner||!owner.isConnected)return;
      const host=owner.closest('dialog[open]')||document.body;host.append(bubble);
      bubble.textContent=t(owner.dataset.help||owner.title)+(owner.disabled||described.disabled?' '+t('Este control todavía no está disponible. Revisá la selección y los avisos del panel.'):'');
      bubble.classList.remove('hidden');
      described.setAttribute('aria-describedby',[(described.getAttribute('aria-describedby')||''),bubble.id].filter(Boolean).join(' '));
      const rect=owner.getBoundingClientRect(),box=bubble.getBoundingClientRect();
      bubble.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-box.width-8))+'px';
      bubble.style.top=(rect.bottom+box.height+12<window.innerHeight?rect.bottom+8:Math.max(8,rect.top-box.height-8))+'px';
    };
    if(instant)render();else timer=setTimeout(render,350);
  }
  function decorate(root){
    for(const node of [root,...(root.querySelectorAll?.('button,input,select,[title],dialog')||[])]){
      if(node.nodeType!==1||node===bubble)continue;
      const text=help[node.id]||node.getAttribute('title');
      if(text){node.dataset.help=t(text);node.removeAttribute('title');}
      if(node.matches('input[type=number]')&&!node.dataset.help)node.dataset.help=node.dataset.transform?t((node.dataset.transform==='position'?'Posición':'Orientación')+' '+['X','Y','Z'][Number(node.dataset.axis)]):t('Define el arco permitido, en grados; no simula colisiones.');
      if(node.matches('[data-filter]'))node.dataset.help=t('Filtra la lista sin modificar la partida.');
      if(node.matches('[data-show]'))node.dataset.help=t('Opciones de visualización; no cambian los recursos del vehículo.');
      if(node.matches('[data-close]')){node.dataset.help=t('Cerrar diálogo');if(node.textContent.trim()==='×')node.setAttribute('aria-label',t('Cerrar diálogo'));}
      if(node.matches('button,input,select')&&!node.dataset.help){const label=node.getAttribute('aria-label')||node.labels?.[0]?.textContent||node.textContent.trim();if(label)node.dataset.help=t(label);}
      if(node.matches('[data-tab],[data-filter],.segmented button,[id^=pick-],.inspector-tabs button,#wireframe,#axes'))node.setAttribute('aria-pressed',String(node.classList.contains('active')));
      if(node.matches('dialog')){const heading=node.querySelector('h2');if(heading){heading.id||=node.id+'-heading';node.setAttribute('aria-labelledby',heading.id);}}
      if(node.matches('button')&&!node.getAttribute('aria-label')&&node.textContent.trim()==='↺')node.setAttribute('aria-label',t('Volver a la ubicación automática'));
      if(node.matches('.crew-row button')&&node.textContent.trim()===t('Ubicar'))node.dataset.help=t('Mueve el asiento sin cambiar el puesto ni el arma asignada.');
      if(node.matches('input,select')&&!node.getAttribute('aria-label')&&!node.labels?.length&&node.dataset.help)node.setAttribute('aria-label',node.dataset.help);
    }
  }
  decorate(document.body);
  const observer=new MutationObserver(records=>{for(const record of records){if(record.type==='attributes'){if(['title','class'].includes(record.attributeName))decorate(record.target);else if(record.target===document.body&&document.body.dataset.helpEnabled==='false')hide();}else for(const node of record.addedNodes)if(node.nodeType===1)decorate(node);}});
  observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['title','class','data-help','data-help-enabled']});
  document.addEventListener('pointerover',event=>show(event.target));
  document.addEventListener('pointerout',event=>{if(owner&&!owner.contains(event.relatedTarget))hide();});
  document.addEventListener('focusin',event=>show(event.target,true));
  document.addEventListener('focusout',hide);document.addEventListener('scroll',hide,true);
  $('help-button').onclick=()=>{hide();$('help-dialog').showModal();};
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'){hide();$('view-panel').classList.add('hidden');$('view-button').setAttribute('aria-expanded','false');}
    if(document.querySelector('dialog[open]')||busy())return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='f'){event.preventDefault();$('unit-search').focus();$('unit-search').select();}
    if(event.key==='?'&&!isTextEntry(event.target)){event.preventDefault();$('help-dialog').showModal();}
    if(event.target?.matches?.('.unit-card')&&['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      event.preventDefault();const rows=[...$('unit-list').querySelectorAll('.unit-card')],index=rows.indexOf(event.target);
      const nextRow=rows.findIndex(row=>row.offsetTop>rows[0].offsetTop),columns=nextRow>0?nextRow:Math.max(1,rows.length);
      const step=event.key==='ArrowDown'?columns:event.key==='ArrowUp'?-columns:event.key==='ArrowRight'?1:-1;
      rows[event.key==='Home'?0:event.key==='End'?rows.length-1:Math.max(0,Math.min(rows.length-1,index+step))]?.focus();
    }
  });
  return {hide,decorate};
}
