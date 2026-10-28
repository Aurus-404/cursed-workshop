import {t} from './i18n.js';

// A long <select> opens as a list with a search box fixed on top: typing
// filters the list at once. The native <select> stays in the page (hidden) as
// the single source of truth, so every editor keeps reading .value/.options and
// listening to 'change' exactly as before.
// The list stays open after a choice, so the player can try options one by one
// and watch each in the 3D view; Escape, the button or a click outside close it.
// The option under the pointer (or the keyboard) is announced with a bubbling
// 'part-hover' event ({detail:{value}}, null when it ends), which the 3D view
// uses to mark the part a list picks.
const LIMIT=400,WIDTH=340;
const fold=text=>String(text||'').normalize('NFD').replace(/[̀-ͯ]/g,'').toLocaleLowerCase();
const VALUE=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value');
const INDEX=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'selectedIndex');
let open=null;
let generatedId=0;
const widgets=new WeakMap();

// Move the visible control together with its native authority and label.
// Moving only the hidden SELECT leaves the working selector in the old panel.
export function moveSelectField(select,field){
  const widget=widgets.get(select),label=widget?.label||select.labels?.[0];
  if(label)field.append(label);
  field.append(select);
  if(widget)field.append(widget.button);
}

// Use the same control for all editor dropdowns, including fields added later.
// A visible multi-row list (placed fittings, saved changes, etc.) stays a list.
export function searchableSelects(root){
  const enhance=node=>{
    if(!node.querySelectorAll)return;
    const selects=[...(node.matches?.('select')?[node]:[]),...node.querySelectorAll('select')];
    for(const select of selects)if(!select.multiple&&select.size<=1)searchableSelect(select);
  };
  enhance(root);
  const observer=new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)enhance(node);});
  observer.observe(root,{childList:true,subtree:true});
  return ()=>observer.disconnect();
}

export function searchableSelect(select){
  if(!select||select.dataset.searchable)return;
  select.dataset.searchable='1';
  const button=document.createElement('button');button.type='button';button.className='ss-button';
  button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');
  const text=document.createElement('span');button.append(text);
  select.after(button);select.classList.add('ss-native');select.tabIndex=-1;
  const label=select.labels?.[0];
  if(label){label.htmlFor='';label.onclick=()=>button.focus();}
  const pop=document.createElement('div');pop.className='ss-pop hidden';pop.setAttribute('role','dialog');
  const search=document.createElement('input');search.type='search';search.className='ss-search';search.autocomplete='off';
  const list=document.createElement('ul');list.className='ss-list';list.setAttribute('role','listbox');list.id=(select.id||`ss-generated-${++generatedId}`)+'-ss-list';
  const more=document.createElement('div');more.className='ss-more';
  pop.append(search,list,more);document.body.append(pop);
  let rows=[],active=-1,hovered;
  const hover=value=>{if(value===hovered)return;hovered=value;select.dispatchEvent(new CustomEvent('part-hover',{bubbles:true,detail:{value}}));};

  function refresh(){
    const option=select.options[INDEX.get.call(select)];
    text.textContent=option?option.text:'';button.title=option?option.text:'';
    button.disabled=select.disabled||!select.options.length;
    const name=label?.textContent||select.getAttribute('aria-label')||select.title;
    if(name)button.setAttribute('aria-label',name);
    // A choice may disable the list while its tool works: keep it open, inert.
    pop.classList.toggle('ss-busy',button.disabled);
  }
  function paint(){
    const query=fold(search.value).split(/\s+/).filter(Boolean);
    const matches=[...select.options].filter(o=>!o.disabled||o.value===VALUE.get.call(select))
      .filter(o=>{const hay=fold(o.text+' '+o.value);return query.every(q=>hay.includes(q));});
    rows=matches.slice(0,LIMIT);
    list.replaceChildren(...rows.map((o,i)=>{const li=document.createElement('li');li.textContent=o.text;li.id=`${list.id}-${i}`;
      li.setAttribute('role','option');li.setAttribute('aria-selected',String(o.selected));if(o.selected)li.classList.add('selected');
      li.onmousedown=event=>event.preventDefault();li.onclick=()=>choose(i);li.onmouseenter=()=>hover(o.value);return li;}));
    more.textContent=matches.length>LIMIT?t('Hay más resultados: seguí escribiendo para acotar.'):matches.length?'':t('Sin resultados.');
    activate(Math.max(0,rows.findIndex(o=>o.selected)));
  }
  function activate(index){
    active=rows.length?Math.max(0,Math.min(index,rows.length-1)):-1;
    for(const [i,li] of [...list.children].entries())li.classList.toggle('active',i===active);
    const li=list.children[active];
    if(rows[active])hover(rows[active].value);
    if(li){
      search.setAttribute('aria-activedescendant',li.id);
      // Only scroll the choices, never the inspector/page behind the popup.
      const itemBox=li.getBoundingClientRect(),box=list.getBoundingClientRect();
      if(itemBox.top<box.top)list.scrollTop+=itemBox.top-box.top;
      else if(itemBox.bottom>box.bottom)list.scrollTop+=itemBox.bottom-box.bottom;
    }else search.removeAttribute('aria-activedescendant');
  }
  function place(){
    const box=button.getBoundingClientRect(),below=innerHeight-box.bottom-8,above=box.top-8;
    const down=below>=Math.min(260,above),width=Math.min(Math.max(box.width,WIDTH),innerWidth-16);
    pop.style.width=width+'px';pop.style.left=Math.max(8,Math.min(box.left,innerWidth-width-8))+'px';
    pop.style.maxHeight=Math.min(420,down?below:above)+'px';
    if(down){pop.style.top=box.bottom+2+'px';pop.style.bottom='';}
    else{pop.style.top='';pop.style.bottom=innerHeight-box.top+2+'px';}
  }
  function show(){
    if(button.disabled)return;
    if(open&&open!==api)open.close();
    open=api;pop.classList.remove('hidden');button.setAttribute('aria-expanded','true');
    search.placeholder=t('Buscar…');search.setAttribute('aria-controls',list.id);
    place();paint();search.focus();search.select();
  }
  function close(focus=false){
    if(open!==api)return;
    open=null;pop.classList.add('hidden');button.setAttribute('aria-expanded','false');hover(null);
    if(focus)button.focus();
  }
  function choose(index){
    const option=rows[index];if(!option||button.disabled)return;
    const changed=!option.selected;
    VALUE.set.call(select,option.value);refresh();
    for(const [i,li] of [...list.children].entries()){const on=rows[i]===option;li.classList.toggle('selected',on);li.setAttribute('aria-selected',String(on));}
    activate(index);search.focus();
    if(changed){select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));}
  }
  button.onclick=()=>open===api?close():show();
  // Pointing at the closed list marks its current choice.
  button.onmouseenter=()=>{if(open!==api&&select.value)hover(select.value);};
  button.onmouseleave=()=>{if(open!==api)hover(null);};
  list.onmouseleave=()=>hover(rows[active]?.value??null);
  button.onkeydown=event=>{if(['ArrowDown','ArrowUp','Enter',' '].includes(event.key)){event.preventDefault();show();}
    else if(event.key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey){show();search.value=event.key;paint();event.preventDefault();}};
  search.oninput=paint;
  search.onkeydown=event=>{
    if(event.key==='ArrowDown'){event.preventDefault();activate(active+1);}
    else if(event.key==='ArrowUp'){event.preventDefault();activate(active-1);}
    else if(event.key==='PageDown'){event.preventDefault();activate(active+10);}
    else if(event.key==='PageUp'){event.preventDefault();activate(active-10);}
    else if(event.key==='Enter'){event.preventDefault();choose(active);}
    else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);}
    else if(event.key==='Tab')close();
  };
  // Code sets .value/.selectedIndex directly and rebuilds the options: keep the button in step.
  Object.defineProperty(select,'value',{configurable:true,get(){return VALUE.get.call(this);},set(v){VALUE.set.call(this,v);refresh();}});
  Object.defineProperty(select,'selectedIndex',{configurable:true,get(){return INDEX.get.call(this);},set(v){INDEX.set.call(this,v);refresh();}});
  select.addEventListener('change',refresh);
  new MutationObserver(()=>{refresh();if(open===api)paint();}).observe(select,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['disabled']});
  const api={close,show,refresh};
  widgets.set(select,{button,label});
  refresh();return api;
}

document.addEventListener('mousedown',event=>{if(open&&!event.target.closest?.('.ss-pop,.ss-button'))open.close();},true);
addEventListener('resize',()=>open?.close());
document.addEventListener('scroll',event=>{if(open&&!event.target.closest?.('.ss-pop'))open.close();},true);
