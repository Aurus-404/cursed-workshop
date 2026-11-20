// One rule for the 3D view: anything in a menu that names a part or a crewman
// of the vehicle on screen marks it while the player points at it, or reaches it
// with the keyboard, so nobody has to guess what a choice is.
//
// Three sources, strongest first:
//  1. a list: the option under the pointer or the keyboard in an open list, or
//     the current choice of a closed field under the pointer (searchable-select
//     announces them with 'part-hover'); an <option> of a visible list (placed
//     armour, launchers, seats...) under the pointer;
//  2. a row that names one thing: an element with data-mark (JSON), such as
//     {"crew":"gunner3"} or {"weapon":"gun1","bone":"gun1"};
//  3. the keyboard focus on a field or a marked row.
// A list mark is {select, value}: what the value stands for depends on the list
// (data-pick on the <select>: "crew", "fitting:<group>", else a part or bone).
// Leaving a field inside a marked row marks the row again (Who works each
// weapon: the row is the weapon, its list the crewman).
export function readMark(element){
  try{return JSON.parse(element.dataset.mark);}catch{return null;}
}

// selectOf: the native list a visible field stands for (searchable-select's selectOfButton).
export function hoverMarks({document=globalThis.document,show,selectOf=()=>null}){
  let list=null,row=null,rowElement=null,focus=null,shown=null;
  const current=()=>list||row||focus;
  function update(force=false){const next=current();if(!force&&next===shown)return;shown=next;show(next);}
  document.addEventListener('part-hover',event=>{
    const select=event.target,value=event.detail?.value;
    list=value===null||value===undefined?null:{select,value:String(value)};update();});
  document.addEventListener('mouseover',event=>{
    const target=event.target;
    // A visible list's own rows: the option under the pointer.
    if(target?.tagName==='OPTION'){const select=target.parentElement?.closest?.('select')||target.closest?.('select');
      if(select?.dataset.pick&&!target.disabled){rowElement=target;row={select,value:target.value};update();return;}}
    const element=target?.closest?.('[data-mark]')||null;
    if(element===rowElement)return;
    rowElement=element;row=element?readMark(element):null;update();});
  document.addEventListener('mouseout',event=>{if(!event.relatedTarget){rowElement=null;row=null;update();}});
  document.addEventListener('focusin',event=>{
    const target=event.target;let next=null;
    // Only the keyboard: a click leaves the focus on a checkbox while the
    // player goes on working in the view, and the mark would stay over it.
    if(target?.matches?.(':focus-visible')){
      const element=target.closest?.('[data-mark]'),select=selectOf(target);
      if(element)next=readMark(element);
      else if(select&&select.value!=='')next={select,value:select.value};
    }
    focus=next;update();});
  document.addEventListener('focusout',event=>{if(!event.relatedTarget){focus=null;update();}});
  // The view rebuilt its parts (a new preview, a part shown or hidden): mark again.
  return {refresh(){if(shown)update(true);},current};
}
