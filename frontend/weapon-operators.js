import {t,lang} from './i18n.js';

const KINDS={main:'Arma principal',coaxial:'Coaxial',hull:'De casco',aa:'Antiaérea',turret:'De torreta',rear:'Trasera'};
// The weapon's own name in the game's language (its id only when the game names it nowhere), and its kind.
export function weaponName(weapon,language=lang){const names=weapon?.names||{};
  return names[language]||names[String(language).split('-')[0]]||names.default||String(weapon?.weapon||weapon?.place||'').replace(/^weapon /,'').replaceAll('_',' ');}
export function weaponLabel(weapon,language=lang){const kind=KINDS[weapon?.type];return weaponName(weapon,language)+(kind?' · '+t(kind):'');}

// Who aims and fires each weapon: transform.weapon_operators = {weapon place: crew place}.
// One crewman can work several weapons (a turret's gunner also fires the launcher
// on its roof); a seat that aims nothing any more can be left in reserve.
// The compatibility check lists the weapons with the operator each one ends up with.
export function operatorOf(transform,weapon){return transform?.weapon_operators?.[weapon.place]||weapon.gunner;}
export function setOperator(transform,place,role){
  const next={...(transform.weapon_operators||{})};
  if(role)next[place]=role;else delete next[place];
  if(Object.keys(next).length)transform.weapon_operators=next;else delete transform.weapon_operators;}
// Someone left in reserve works nothing: the build gives his weapons to someone still aboard.
export function forgetOperator(transform,place){
  for(const [weapon,role] of Object.entries(transform.weapon_operators||{}))if(role===place)setOperator(transform,weapon,'');}
// Whether a crew seat aims one of `weapons`, with the operators picked here.
export function aimsWeapon(weapons,transform,place){return weapons.some(weapon=>operatorOf(transform,weapon)===place);}
// Two gunners are "Gunner" and "Gunner 2", as the crew bar shows them.
export function crewLabel(roleName,place){const number=String(place).match(/(\d+)$/)?.[1];return roleName(place)+(number&&number!=='1'?' '+number:'');}

export function weaponOperatorsEditor({state,$,remember,refreshCheck,roleName,enabled=()=>true,document=globalThis.document}){
  // The last finished check: while the next one runs its list stays (disabled), without flicker.
  let last=null;
  function known(){const check=state.check;
    if(check?.status!=='pending'&&Array.isArray(check?.operators))last={weapons:check.operators,crew:check.crew||[]};
    else if(check&&check.status!=='pending')last=null;
    return last;}
  // null when no check has listed the weapons yet: the caller keeps the seat's own flag.
  function aims(place){const data=known();return data&&enabled()?aimsWeapon(data.weapons,state.transform,place):null;}
  function render(){
    const field=$('weapon-operators-field'),list=$('weapon-operators-list');if(!field||!list)return;
    // Weapons added in Weapons without a turret pick their crewman there.
    const data=known(),weapons=(data?.weapons||[]).filter(weapon=>!weapon.added),pending=state.check?.status==='pending';
    const excluded=new Set(state.transform.crew_exclude||[]);
    const crew=(data?.crew||[]).filter(place=>!excluded.has(place));
    const shown=enabled()&&!state.pendingTurret&&weapons.length>0&&crew.length>0;
    field.classList.toggle('hidden',!shown);list.replaceChildren();if(!shown)return;
    for(const weapon of weapons){
      const row=document.createElement('div');row.className='operator-row';
      // Pointing at the row marks the weapon; its list marks each crewman it offers.
      row.dataset.mark=JSON.stringify({weapon:weapon.place,bone:weapon.bone||null});
      const label=document.createElement('label');label.textContent=weaponLabel(weapon);
      const select=document.createElement('select');select.setAttribute('aria-label',t('Lo maneja')+' · '+weaponLabel(weapon));select.dataset.pick='crew';
      const current=operatorOf(state.transform,weapon);
      select.replaceChildren(...[...new Set([...crew,current])].map(place=>new Option(crewLabel(roleName,place),place)));
      select.value=current;select.disabled=!!state.busy||pending;
      select.id='weapon-operator-'+weapon.place;label.htmlFor=select.id;
      select.onchange=()=>{remember();setOperator(state.transform,weapon.place,select.value);render();refreshCheck();};
      row.append(label,select);list.append(row);}
  }
  return {render,aims};
}
