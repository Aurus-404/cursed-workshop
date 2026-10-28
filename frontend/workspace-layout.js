import {t} from './i18n.js';

const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));

export function panelWidths(total,left,right,priority='library'){
  left=clamp(left,248,520);right=clamp(right,230,460);
  const budget=Math.max(478,total-360);
  if(left+right>budget){
    if(priority==='library')left=Math.max(248,budget-right);
    else right=Math.max(230,budget-left);
    if(left+right>budget){left=Math.max(248,budget-right);right=Math.max(230,budget-left);}
  }
  return {left:Math.round(left),right:Math.round(right)};
}

export function scrollThumb(viewport,content,track,offset){
  const max=Math.max(0,content-viewport),height=Math.min(20,Math.max(0,track));
  const travel=Math.max(0,track-height),ratio=max?clamp(offset/max,0,1):0;
  return {visible:max>1,height,travel,max,top:ratio*travel,percent:Math.round(ratio*100)};
}

// Presentation only: resize panes and map a fixed-size handle to native scrollTop.
export function workspaceLayout({document=globalThis.document,window=globalThis.window}={}){
  const workspace=document.querySelector('.workspace'),library=document.querySelector('.library'),inspector=document.querySelector('.inspector');
  let widths={left:library.getBoundingClientRect().width,right:inspector.getBoundingClientRect().width};
  const separators=[];
  const apply=(left,right,priority)=>{
    if(workspace.clientWidth<=900)return;
    widths=panelWidths(workspace.clientWidth,left,right,priority);
    workspace.style.setProperty('--library-width',widths.left+'px');
    workspace.style.setProperty('--inspector-width',widths.right+'px');
    for(const node of separators)node.setAttribute('aria-valuenow',String(node.dataset.panel==='library'?widths.left:widths.right));
  };
  for(const [panel,label] of [['library','Ancho de la biblioteca'],['inspector','Ancho del panel de ajustes']]){
    const handle=document.createElement('div');handle.className='panel-resizer';handle.dataset.panel=panel;
    handle.tabIndex=0;handle.setAttribute('role','separator');handle.setAttribute('aria-orientation','vertical');
    handle.setAttribute('aria-label',t(label));handle.setAttribute('aria-valuemin',panel==='library'?'248':'230');handle.setAttribute('aria-valuemax',panel==='library'?'520':'460');
    workspace.append(handle);separators.push(handle);
    let drag=null;
    handle.onpointerdown=event=>{
      if(event.button!==0||workspace.clientWidth<=900)return;
      event.preventDefault();drag={x:event.clientX,...widths};handle.setPointerCapture(event.pointerId);
      workspace.classList.add('resizing-panels');
    };
    handle.onpointermove=event=>{
      if(!drag)return;
      const delta=event.clientX-drag.x;
      apply(drag.left+(panel==='library'?delta:0),drag.right+(panel==='inspector'?-delta:0),panel);
    };
    const finish=()=>{drag=null;workspace.classList.remove('resizing-panels');};
    handle.onpointerup=finish;handle.onpointercancel=finish;handle.onlostpointercapture=finish;
    handle.ondblclick=()=>apply(panel==='library'?Math.min(334,workspace.clientWidth*.23):widths.left,panel==='inspector'?284:widths.right,panel);
    handle.onkeydown=event=>{
      if(!['ArrowLeft','ArrowRight','Home'].includes(event.key))return;
      event.preventDefault();const step=(event.shiftKey?30:10)*(event.key==='ArrowRight'?1:-1);
      if(event.key==='Home')handle.ondblclick();
      else apply(widths.left+(panel==='library'?step:0),widths.right+(panel==='inspector'?-step:0),panel);
    };
  }
  const paneObserver=new window.ResizeObserver(()=>apply(widths.left,widths.right));paneObserver.observe(workspace);
  apply(widths.left,widths.right);

  for(const [id,label] of [['unit-list','Desplazamiento de la biblioteca'],['inspector-scroll','Desplazamiento de los ajustes']]){
    const content=id==='unit-list'?document.getElementById(id):document.querySelector('.inspector-scroll');
    if(!content)continue;
    if(!content.id)content.id=id;
    const shell=document.createElement('div');shell.className='scroll-shell';
    content.parentElement.insertBefore(shell,content);shell.append(content);content.classList.add('fixed-scroll-content');
    const rail=document.createElement('div'),thumb=document.createElement('div');
    rail.className='fixed-scrollbar';thumb.className='fixed-scroll-thumb';rail.append(thumb);shell.append(rail);
    rail.tabIndex=0;rail.setAttribute('role','scrollbar');rail.setAttribute('aria-label',t(label));
    rail.setAttribute('aria-controls',id);rail.setAttribute('aria-orientation','vertical');rail.setAttribute('aria-valuemin','0');rail.setAttribute('aria-valuemax','100');
    let metrics=null,drag=null,frame=0;
    const update=()=>{
      frame=0;metrics=scrollThumb(content.clientHeight,content.scrollHeight,Math.max(0,shell.clientHeight-6),content.scrollTop);
      rail.classList.toggle('hidden',!metrics.visible);rail.setAttribute('aria-valuenow',String(metrics.percent));
      thumb.style.height=metrics.height+'px';thumb.style.transform='translateY('+metrics.top+'px)';
    };
    const schedule=()=>{if(!frame)frame=window.requestAnimationFrame(update);};
    const setPosition=(y,grab)=>{
      if(!metrics?.travel)return;
      content.scrollTop=clamp((y-rail.getBoundingClientRect().top-grab)/metrics.travel,0,1)*metrics.max;
      schedule();
    };
    rail.onpointerdown=event=>{
      if(event.button!==0||!metrics?.visible)return;
      event.preventDefault();drag=event.target===thumb?event.clientY-thumb.getBoundingClientRect().top:metrics.height/2;
      rail.setPointerCapture(event.pointerId);setPosition(event.clientY,drag);
    };
    rail.onpointermove=event=>{if(drag!==null)setPosition(event.clientY,drag);};
    rail.onpointerup=rail.onpointercancel=rail.onlostpointercapture=()=>{drag=null;};
    rail.addEventListener('wheel',event=>{event.preventDefault();content.scrollTop+=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?content.clientHeight:1);},{passive:false});
    rail.onkeydown=event=>{
      const steps={ArrowDown:40,ArrowUp:-40,PageDown:content.clientHeight,PageUp:-content.clientHeight};
      if(event.key==='Home'||event.key==='End'||event.key in steps){event.preventDefault();content.scrollTop=event.key==='Home'?0:event.key==='End'?metrics.max:content.scrollTop+steps[event.key];}
    };
    content.addEventListener('scroll',schedule,{passive:true});
    const observer=new window.ResizeObserver(schedule);observer.observe(content);observer.observe(shell);
    new window.MutationObserver(schedule).observe(content,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['class','hidden','style','open']});
    schedule();
  }
}
