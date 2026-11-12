import {t} from './i18n.js';
import {shown} from './number-field.js';

// Weapon attributes: each weapon of the vehicle (one per weapon place) with
// its native values from the compatibility check. Only changed values are
// saved, in transform.weapon_attributes; the build gives that place its own
// copy of the weapon, so other vehicles and the AI keep the native one.
export const WEAPON_FIELDS={
  reload_time:{label:'Tiempo de recarga (s)',min:0.05,max:120,step:0.1},
  fire_rate:{label:'Cadencia (disparos por minuto)',min:1,max:6000,step:10},
  magazine:{label:'Munición por cargador',min:1,max:100000,step:1},
  range:{label:'Alcance máximo (m)',min:1,max:2000,step:5},
  burst_pause:{label:'Pausa entre ráfagas (s)',min:0,max:60,step:0.05},
  burst_short:{label:'Ráfaga corta (disparos)',min:1,max:1000,step:1},
  burst_long:{label:'Ráfaga larga (disparos)',min:1,max:1000,step:1},
  dispersion:{label:'Dispersión (1 = original, menos = más preciso)',min:0.05,max:20,step:0.05},
};
export const SHELL_FIELDS={
  velocity:{label:'Velocidad de salida (m/s)',min:1,max:5000,step:10},
  armor_damage:{label:'Daño al blindaje',min:0,max:100000,step:1},
  infantry_damage:{label:'Daño a la infantería',min:0,max:1000000,step:10},
};
const GROUPS=[
  {id:'fire',label:'Disparo',fields:['reload_time','fire_rate','magazine','range']},
  {id:'burst',label:'Ráfagas',fields:['burst_pause','burst_short','burst_long']},
  {id:'accuracy',label:'Precisión y dispersión',fields:['dispersion']},
];
const PLACE_TYPES={coaxial:'Coaxial',hull:'De casco',commander:'Del comandante',turret:'De torreta'};

export function placeLabel(weapon){
  const kind=PLACE_TYPES[weapon.type]?t(PLACE_TYPES[weapon.type]):weapon.place==='gun'?t('Cañón principal'):weapon.place;
  return kind+' · '+weapon.weapon;
}

// Writes one value into a spec, dropping it (and empty parents) when it is
// the native one again, or null. Returns the new spec or undefined when empty.
export function setValue(spec,path,value,native){
  const result=structuredClone(spec||{});let node=result;const parents=[];
  for(const key of path.slice(0,-1)){parents.push([node,key]);node=node[key]??={};}
  const last=path.at(-1);
  const same=value===null||(typeof value==='number'&&typeof native==='number'&&Math.abs(value-native)<1e-9);
  if(same)delete node[last];else node[last]=value;
  for(const [parent,key] of parents.reverse())if(!Object.keys(parent[key]).length)delete parent[key];
  return Object.keys(result).length?result:undefined;
}

export function weaponAttributesEditor({state,$,remember,document=globalThis.document}){
  const open=new Set();let selected='',shownFor=null;
  const weapons=()=>state.check?.weapons||[];
  const spec=()=>state.transform.weapon_attributes;
  const current=()=>weapons().find(w=>w.place===selected)||weapons()[0];
  function message(text){$('weapons-message').textContent=text||'';}
  function store(next){
    remember();
    if(next)state.transform.weapon_attributes=next;else delete state.transform.weapon_attributes;
    render(true);
  }
  function input(id,{min,max,step},value,native,onValue){
    const field=document.createElement('input');field.type='number';field.id=id;field.min=min;field.max=max;field.step=step;
    field.value=shown(value);field.classList.toggle('changed',native!==undefined&&Math.abs(value-native)>1e-9);
    field.onchange=()=>{const number=Number(field.value);
      if(field.value.trim()===''||!Number.isFinite(number)||number<min||number>max){
        message(t('Usá un valor entre ')+min+t(' y ')+max+'.');field.value=shown(value);return;}
      message('');onValue(number);};
    return field;
  }
  function row(label,field){field.dataset.help=t(label);const wrap=document.createElement('label');wrap.className='weapon-value';wrap.append(document.createTextNode(t(label)),field);return wrap;}
  function group(id,label,children){
    const details=document.createElement('details');details.className='tool-group';details.dataset.group=id;details.open=open.has(id);
    details.ontoggle=()=>{if(details.open)open.add(id);else open.delete(id);};
    const summary=document.createElement('summary');summary.textContent=label;
    const grid=document.createElement('div');grid.className='tool-grid weapon-grid';grid.append(...children);
    details.append(summary,grid);return details;
  }
  function render(force=false){
    const field=$('weapons-field'),list=weapons();
    field.classList.toggle('hidden',!state.hull);
    const loading=state.check?.status==='pending';
    $('weapons-empty').textContent=loading?t('Esperá la comprobación del vehículo.'):list.length?'':t('Este vehículo no tiene armas editables.');
    if(!force&&shownFor===state.check?.weapons&&field.dataset.selected===selected)return;
    shownFor=state.check?.weapons;
    if(!list.some(w=>w.place===selected))selected=list[0]?.place||'';
    field.dataset.selected=selected;
    $('weapons-select').replaceChildren(...list.map(w=>new Option(placeLabel(w)+(spec()?.places?.[w.place]?' · '+t('cambiada'):''),w.place)));
    $('weapons-select').value=selected;
    const weapon=current(),sections=$('weapons-sections');
    sections.replaceChildren();$('weapons-reset').disabled=!weapon||!spec()?.places?.[weapon?.place];
    if(!weapon)return;
    const edits=spec()?.places?.[weapon.place]||{};
    for(const {id,label,fields} of GROUPS){
      const present=fields.filter(name=>name in weapon.fields);if(!present.length)continue;
      sections.append(group(id,t(label),present.map(name=>{
        const native=weapon.fields[name],value=edits[name]??native;
        return row(WEAPON_FIELDS[name].label,input('weapon-'+name,WEAPON_FIELDS[name],value,native,
          number=>store(setValue(spec(),['places',weapon.place,name],number,native))));
      })));
    }
    for(const shell of weapon.shells){
      const own=edits.shells?.[shell.key]||{},children=[];
      for(const name of Object.keys(SHELL_FIELDS))if(name in shell){
        const native=shell[name],value=own[name]??native;
        children.push(row(SHELL_FIELDS[name].label,input(`weapon-${shell.key||'shell'}-${name}`,SHELL_FIELDS[name],value,native,
          number=>store(setValue(spec(),['places',weapon.place,'shells',shell.key,name],number,native)))));
      }
      if(shell.penetration){
        const heading=document.createElement('strong');heading.className='weapon-subhead';heading.textContent=t('Penetración (mm) a cada distancia');children.push(heading);
        shell.penetration.forEach(([distance,native],index)=>{
          const values=own.penetration||shell.penetration.map(p=>p[1]),value=values[index];
          children.push(row(t('A ')+shown(distance)+' m',input(`weapon-${shell.key||'shell'}-pen-${index}`,{min:0,max:5000,step:1},value,native,number=>{
            // The table is saved whole, in the order of its distances.
            const next=values.slice();next[index]=number;
            const same=next.every((v,i)=>Math.abs(v-shell.penetration[i][1])<1e-9);
            store(setValue(spec(),['places',weapon.place,'shells',shell.key,'penetration'],same?null:next,null));
          })));
        });
      }
      if(children.length)sections.append(group('shell-'+shell.key,shell.key?t('Munición')+': '+shell.label:t('Munición'),children));
    }
    if(weapon.ammo.length){
      const children=weapon.ammo.map(item=>{
        const native=item.count,value=spec()?.ammo?.[item.item]??native;
        const wrap=row(item.item,input('weapon-ammo-'+item.item.replace(/\W+/g,'_'),{min:0,max:10000000,step:1},value,native,
          number=>store(setValue(spec(),['ammo',item.item],number,native))));
        if(item.shared?.length){const note=document.createElement('small');note.textContent=t('Compartida con: ')+item.shared.join(', ');wrap.append(note);}
        return wrap;
      });
      sections.append(group('ammo',t('Munición total del vehículo'),children));
    }
  }
  $('weapons-select').onchange=()=>{selected=$('weapons-select').value;message('');render(true);};
  $('weapons-reset').onclick=()=>{const weapon=current();if(!weapon||!spec()?.places?.[weapon.place])return;
    const next=structuredClone(spec());delete next.places[weapon.place];if(!Object.keys(next.places).length)delete next.places;
    store(Object.keys(next).length?next:undefined);message('');};
  return {refresh:render};
}
