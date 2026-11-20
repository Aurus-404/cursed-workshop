import * as THREE from 'three';
import {t} from './i18n.js';
import {drawJointArc,clearArcs,previewJointFrame} from './joint-arcs.js';

// One list for the whole vehicle: the turret's joints, then the chassis' (a
// hull MG's), each tagged with its part. There is no Chassis/Turret tab to pick
// which part's joints to show.
const PARTS=['turret','hull'];
const PART_LABELS={hull:'Chasis',turret:'Torreta'};
export function jointKey(part,bone){return part+':'+bone;}

export function weaponJointEditor({state,groups,$,remember,refreshCheck,active=()=>true,level=()=>null,limitsOf=null}) {
  const arcs={hull:new THREE.Group(),turret:new THREE.Group()};let selected='';
  function rows(part){return state.previews[part]?.joints||[];}
  function entries(){return PARTS.flatMap(part=>rows(part).map(joint=>({part,joint,key:jointKey(part,joint.bone)})));}
  function current(){return entries().find(e=>e.key===selected);}
  function limits(part,joint){return limitsOf?limitsOf(part,joint):state.transform.joint_limits?.[part]?.[joint.bone]||joint.limits;}
  function sync(){
    const chosen=current();
    for(const part of PARTS){
      clearArcs(arcs[part]);if(arcs[part].parent!==groups[part])groups[part].add(arcs[part]);
      arcs[part].visible=active()&&chosen?.part===part;
      if(chosen?.part===part)drawJointArc(arcs[part],previewJointFrame(chosen.joint,state.previews[part]?.anchor_name,part==='turret'?state.transform.turret?.pivot:null,level(part)),limits(part,chosen.joint));
    }
  }
  function refresh(){
    const list=entries(),field=$('weapon-joints');
    // A loaded vehicle without weapon joints says so instead of leaving the tool empty.
    const loaded=PARTS.some(part=>state.previews[part]?.meshes?.length),none=t('Esta pieza no tiene articulaciones de arma con límites para ajustar.');
    field.classList.toggle('hidden',state.edit==='commander_mg'||(!list.length&&!loaded));
    for(const node of field.querySelectorAll?.('.ss-button,.joint-limits,#joint-reset,small:not(#joint-message)')||[])node.classList.toggle('hidden',!list.length);
    if(!list.length)$('joint-message').textContent=none;else if($('joint-message').textContent===none)$('joint-message').textContent='';
    if(state.edit==='commander_mg'){sync();return;}
    // The part being edited leads when nothing was picked yet.
    if(!list.some(e=>e.key===selected))selected=(list.find(e=>e.part===state.edit)||list[0])?.key||'';
    const both=new Set(list.map(e=>e.part)).size>1;
    $('joint-select').replaceChildren(...list.map(({part,joint,key})=>new Option(`${both?t(PART_LABELS[part])+' · ':''}${joint.bone} · ${[...new Set(joint.weapons)].join(', ')}`,key)));
    $('joint-select').value=selected;
    const chosen=current();if(chosen){const values=limits(chosen.part,chosen.joint);$('joint-min').value=values[0];$('joint-max').value=values[1];
      // Traverse speed: the saved one, else the vehicle's own ("turret_heavy": 4 + power/3).
      const speed=values[2]??chosen.joint.speed;$('joint-speed').value=speed!=null?String(Math.round(speed*10)/10):'';$('joint-speed').placeholder=t('nativa');}
    sync();
  }
  $('joint-select').onchange=()=>{selected=$('joint-select').value;refresh();};
  const change=()=>{
    const chosen=current();if(!chosen)return;const {part,joint}=chosen;
    const values=[$('joint-min').valueAsNumber??Number($('joint-min').value),$('joint-max').valueAsNumber??Number($('joint-max').value)];
    if(values.some(v=>!Number.isFinite(v)||v < -180||v >180)||values[0]>values[1]){
      $('joint-message').textContent=t('Usá límites entre -180 y 180 grados, con el mínimo primero.');refresh();return;
    }
    const raw=String($('joint-speed').value??'').trim();
    if(raw!==''){const speed=Number(raw);
      // Keep the model-loader-safe minimum aligned with articulation.MIN_JOINT_SPEED.
      if(!(speed>=1&&speed<=3600)){$('joint-message').textContent=t('La velocidad de giro tiene que ser de al menos 1 grado por segundo.');refresh();return;}
      if(joint.speed==null||Math.abs(speed-joint.speed)>.05)values.push(speed);}
    $('joint-message').textContent='';remember();
    state.transform.joint_limits={...state.transform.joint_limits,[part]:{...state.transform.joint_limits?.[part],[joint.bone]:values}};
    sync();refreshCheck();
  };
  $('joint-min').onchange=change;$('joint-max').onchange=change;$('joint-speed').onchange=change;
  $('joint-reset').onclick=()=>{
    const chosen=current();if(!chosen)return;remember();
    const spec=structuredClone(state.transform.joint_limits||{});delete spec[chosen.part]?.[chosen.joint.bone];state.transform.joint_limits=spec;
    $('joint-message').textContent='';refresh();refreshCheck();
  };
  return {refresh,sync,selected:()=>current()};
}
