import {t} from './i18n.js';
import {PART_KINDS,PART_KIND_LABELS,DEFAULT_PART_COLORS,DEFAULT_VIEW_COLORS,viewColors} from './part-colors.js';

// Settings: the plain colors of each kind of part. Changes apply at once.
export function viewColorSettings({$,document=globalThis.document,storage,save,onChange}){
  let value;try{value=viewColors(JSON.parse(storage.getItem('cw.viewColors')||'{}'));}catch{value=viewColors();}
  const grid=$('view-color-grid'),toggle=$('view-colors-by-kind');
  const inputs={};
  for(const kind of PART_KINDS){
    const label=document.createElement('label'),input=document.createElement('input');
    input.type='color';input.id='view-color-'+kind;input.dataset.kind=kind;
    label.htmlFor=input.id;label.append(input,document.createTextNode(' '+t(PART_KIND_LABELS[kind])));
    input.oninput=()=>{value.colors[kind]=input.value;apply(false);};
    input.onchange=()=>{value.colors[kind]=input.value;apply();};
    inputs[kind]=input;grid.append(label);
  }
  function apply(persist=true){
    value=viewColors(value);toggle.checked=value.by_kind;
    for(const kind of PART_KINDS){inputs[kind].value=value.colors[kind];
      // Without "by kind", only the chassis and turret colors are used.
      inputs[kind].closest('label').classList.toggle('muted-setting',!value.by_kind&&!['chassis','turret','crew','passenger'].includes(kind));}
    // Only the colors the player changed, so better defaults still reach the rest.
    if(persist){const saved={by_kind:value.by_kind,colors:Object.fromEntries(Object.entries(value.colors).filter(([kind,color])=>color!==DEFAULT_PART_COLORS[kind]))};
      storage.setItem('cw.viewColors',JSON.stringify(saved));save({view_colors:saved});}
    onChange(value);
  }
  toggle.onchange=()=>{value.by_kind=toggle.checked;apply();};
  $('view-colors-reset').onclick=()=>{value=viewColors(structuredClone(DEFAULT_VIEW_COLORS));apply();};
  apply(false);
  return {get value(){return value;}};
}
