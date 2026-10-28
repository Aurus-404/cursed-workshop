import * as THREE from 'three';
import {t} from './i18n.js';
import {drawJointArc,clearArcs,previewJointFrame} from './joint-arcs.js';

export function weaponJointEditor({state,groups,$,remember,refreshCheck,active=()=>true,level=()=>null,limitsOf=null}) {
  const arcs={hull:new THREE.Group(),turret:new THREE.Group()};let selected={hull:'',turret:''};
  function rows(part){return state.previews[part]?.joints||[];}
  function current(){return rows(state.edit).find(j=>j.bone===selected[state.edit]);}
  function limits(part,joint){return limitsOf?limitsOf(part,joint):state.transform.joint_limits?.[part]?.[joint.bone]||joint.limits;}
  function sync(){
    for(const part of ['hull','turret']){
      clearArcs(arcs[part]);if(arcs[part].parent!==groups[part])groups[part].add(arcs[part]);
      arcs[part].visible=active()&&state.edit===part;
      const joint=rows(part).find(j=>j.bone===selected[part]);
      if(joint)drawJointArc(arcs[part],previewJointFrame(joint,state.previews[part]?.anchor_name,part==='turret'?state.transform.turret?.pivot:null,level(part)),limits(part,joint));
    }
  }
  function refresh(){
    const part=state.edit,list=rows(part),field=$('weapon-joints');
    // A loaded part without weapon joints says so instead of leaving the tool empty.
    const loaded=!!state.previews[part]?.meshes?.length,none=t('Esta pieza no tiene articulaciones de arma con límites para ajustar.');
    field.classList.toggle('hidden',part==='commander_mg'||(!list.length&&!loaded));
    for(const node of field.querySelectorAll?.('.ss-button,.joint-limits,#joint-reset,small:not(#joint-message)')||[])node.classList.toggle('hidden',!list.length);
    if(!list.length)$('joint-message').textContent=none;else if($('joint-message').textContent===none)$('joint-message').textContent='';
    if(part==='commander_mg'){sync();return;}
    if(!list.some(j=>j.bone===selected[part]))selected[part]=list[0]?.bone||'';
    $('joint-select').replaceChildren(...list.map(j=>new Option(`${j.bone} · ${[...new Set(j.weapons)].join(', ')}`,j.bone)));
    $('joint-select').value=selected[part];
    const joint=current();if(joint){const values=limits(part,joint);$('joint-min').value=values[0];$('joint-max').value=values[1];}
    sync();
  }
  $('joint-select').onchange=()=>{selected[state.edit]=$('joint-select').value;refresh();};
  const change=()=>{
    const joint=current();if(!joint)return;
    const values=[$('joint-min').valueAsNumber??Number($('joint-min').value),$('joint-max').valueAsNumber??Number($('joint-max').value)];
    if(values.some(v=>!Number.isFinite(v)||v < -180||v >180)||values[0]>values[1]){
      $('joint-message').textContent=t('Usá límites entre -180 y 180 grados, con el mínimo primero.');refresh();return;
    }
    $('joint-message').textContent='';remember();
    state.transform.joint_limits={...state.transform.joint_limits,[state.edit]:{...state.transform.joint_limits?.[state.edit],[joint.bone]:values}};
    sync();refreshCheck();
  };
  $('joint-min').onchange=change;$('joint-max').onchange=change;
  $('joint-reset').onclick=()=>{
    const joint=current();if(!joint)return;remember();
    const spec=structuredClone(state.transform.joint_limits||{});delete spec[state.edit]?.[joint.bone];state.transform.joint_limits=spec;
    $('joint-message').textContent='';refresh();refreshCheck();
  };
  return {refresh,sync};
}
