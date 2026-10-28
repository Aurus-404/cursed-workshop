import {t} from './i18n.js';

export const THEMES={
  graphite:{name:'Grafito',bg:'#151512',panel:'#1e1e1a',input:'#151512',control:'#2b2a23',hover:'#39352a',line:'#776747',text:'#e8e3d8',muted:'#b9ad97',accent:'#d1ad68',selected:'#443a27',viewport:'#20211e',dark:true},
  paper:{name:'Papel',bg:'#ece9e1',panel:'#faf8f2',input:'#ffffff',control:'#e5e0d5',hover:'#d5cebf',line:'#81796b',text:'#292720',muted:'#555044',accent:'#6b4613',selected:'#e8d8b8',viewport:'#d8d4c9',dark:false},
  olive:{name:'Oliva',bg:'#25281f',panel:'#303528',input:'#20241b',control:'#3d4432',hover:'#4b533f',line:'#929c81',text:'#f0f0df',muted:'#c4c8b4',accent:'#d6c378',selected:'#505039',viewport:'#2b3025',dark:true},
  earth:{name:'Tierra',bg:'#2d2521',panel:'#392f29',input:'#241e1b',control:'#493c32',hover:'#594a3d',line:'#a18a78',text:'#f4eade',muted:'#d1bdab',accent:'#edbe8b',selected:'#584232',viewport:'#322a25',dark:true},
  contrast:{name:'Alto contraste',bg:'#000000',panel:'#080808',input:'#000000',control:'#191919',hover:'#333333',line:'#bdbdbd',text:'#ffffff',muted:'#ededed',accent:'#ffe680',selected:'#393300',viewport:'#080808',dark:true},
  classic:{name:'Clásico',bg:'#111619',panel:'#1a2227',input:'#141e24',control:'#222b30',hover:'#303c43',line:'#647b88',text:'#dde5e7',muted:'#a9bac3',accent:'#dbb77a',selected:'#3b3425',viewport:'#19272e',dark:true},
};
const DEFAULTS={theme:'graphite',size:'normal',motion:false,help:true};
export function readPreferences(storage=globalThis.localStorage){
  let saved={};try{saved=JSON.parse(storage?.getItem('cw.appearance')||'{}')||{};}catch{}
  return {theme:Object.hasOwn(THEMES,saved.theme)?saved.theme:DEFAULTS.theme,size:saved.size==='large'?'large':'normal',motion:saved.motion===true,help:saved.help!==false};
}
export function preferences({document=globalThis.document,storage=globalThis.localStorage,onTheme=()=>{},onChange=()=>{}}={}){
  let value=readPreferences(storage);const $=id=>document.getElementById(id);
  const select=$('theme-select');
  for(const [id,theme] of Object.entries(THEMES))select.add(new Option(t(theme.name),id));
  function apply(changed=true){
    const theme=THEMES[value.theme],root=document.documentElement;
    root.dataset.theme=value.theme;root.dataset.textSize=value.size;root.dataset.reduceMotion=String(value.motion);
    root.style.colorScheme=theme.dark?'dark':'light';
    for(const key of ['bg','panel','input','control','hover','line','text','muted','accent','selected','viewport'])root.style.setProperty('--'+key,theme[key]);
    root.style.setProperty('--on-accent',theme.dark?'#171717':'#ffffff');
    root.style.setProperty('--danger',theme.dark?'#ffb3a3':'#8e250f');
    root.style.setProperty('--success',theme.dark?'#b7d2b2':'#285c2b');
    root.style.setProperty('--overlay',theme.dark?'#141414ed':'#f2efe8ed');
    select.value=value.theme;$('text-size').value=value.size;$('reduce-motion').checked=value.motion;$('show-help').checked=value.help;
    document.body.dataset.helpEnabled=String(value.help);
    try{storage?.setItem('cw.appearance',JSON.stringify(value));}catch{}
    onTheme(theme,value);
    if(changed)onChange({...value});
  }
  select.onchange=()=>{value.theme=Object.hasOwn(THEMES,select.value)?select.value:'graphite';apply();};
  $('text-size').onchange=()=>{value.size=$('text-size').value==='large'?'large':'normal';apply();};
  $('reduce-motion').onchange=()=>{value.motion=$('reduce-motion').checked;apply();};
  $('show-help').onchange=()=>{value.help=$('show-help').checked;apply();};
  $('appearance-reset').onclick=()=>{value={...DEFAULTS};apply();};
  apply(false);return {get value(){return {...value};}};
}
