import * as THREE from 'three';
import {saveCrewPlacement} from './crew-placement.js';
import {shown} from './number-field.js';

const AXES=['X','Y','Z'];
const turn=degrees=>new THREE.Quaternion().setFromEuler(new THREE.Euler(...degrees.map(THREE.MathUtils.degToRad),'ZYX'));

// A seat keeps the place of its figure's origin and its turn, which is all the
// compiler reads. The pivot only says where the editor turns it around: a new turn
// about it moves the origin too, so the pivot stays where it is.
export function turnSeatAbout(row,rotation){
  const pivot=new THREE.Vector3(...(row.pivot||[0,0,0]));
  const held=new THREE.Vector3(...row.position).add(pivot.clone().applyQuaternion(turn(row.rotation||[0,0,0])));
  const position=held.sub(pivot.applyQuaternion(turn(rotation))).toArray().map(v=>Number(v.toFixed(3)));
  return {...row,position,rotation};
}

export function crewPoseEditor({control,state,$,figure,module,remember,applied,check}){
  const proxy=new THREE.Group();proxy.name='crew_pivot';
  let mode='translate';
  const row=()=>state.transform.crew_seats?.[state.editSeat];
  const pivotOf=()=>new THREE.Vector3(...(row()?.pivot||[0,0,0]));
  const write=next=>{state.transform.crew_seats={...(state.transform.crew_seats||{}),[state.editSeat]:next};};
  // What the seat is now, also before its first manual move.
  const current=()=>{const value=saveCrewPlacement(figure(),module());return {rotation:[0,0,0],...value,...(row()?.pivot?{pivot:row().pivot}:{})};};
  function buttons(){
    for(const [id,value] of [['crew-pose-move','translate'],['crew-pose-rotate','rotate'],['crew-pose-pivot','pivot']]){
      $(id).classList.toggle('active',mode===value);$(id).setAttribute('aria-pressed',String(mode===value));}
    for(const [id,value] of [['mode-move','translate'],['mode-rotate','rotate']]){
      $(id).classList.toggle('active',mode===value);$(id).setAttribute('aria-pressed',String(mode===value));}
  }
  // In the figure's parent, a plain move and turn even where a skeleton reflects an axis.
  function sync(){
    const seat=figure();if(!seat)return;
    if(proxy.parent!==seat.parent)seat.parent.add(proxy);
    proxy.quaternion.copy(seat.quaternion);
    proxy.position.copy(seat.position).add(pivotOf().applyQuaternion(seat.quaternion));
    proxy.updateMatrixWorld(true);
    const value=current();
    for(let axis=0;axis<3;axis++){
      $('crew-position-'+axis).value=shown(value.position[axis]);
      $('crew-rotation-'+axis).value=shown(value.rotation[axis]);
      $('crew-pivot-'+axis).value=shown((value.pivot||[0,0,0])[axis]);}
    buttons();
  }
  function attach(){
    const seat=figure();if(!seat)return;
    sync();control.detach();control.attach(mode==='translate'?seat:proxy);
    control.setMode(mode==='rotate'?'rotate':'translate');
  }
  function setMode(value){mode=value;attach();}
  function capture(){
    const seat=figure();if(!seat)return;
    if(mode==='pivot'){
      const pivot=proxy.position.clone().sub(seat.position).applyQuaternion(seat.quaternion.clone().invert());
      write({...current(),pivot:pivot.toArray().map(v=>Number(v.toFixed(3)))});
    }else{
      if(mode==='rotate'){seat.quaternion.copy(proxy.quaternion);seat.position.copy(proxy.position).sub(pivotOf().applyQuaternion(proxy.quaternion));}
      write(current());
    }
    sync();
  }
  function change(type,axis,text){
    const value=Number(text);if(!figure()||!Number.isFinite(value)||Math.abs(value)>100000){sync();return;}
    remember();const base=current();
    const vector=[...(base[type]||[0,0,0])];vector[axis]=value;
    let next=type==='rotation'?turnSeatAbout(base,vector):{...base,[type]:vector};
    if(type==='pivot'&&!vector.some(Boolean)){const {pivot,...rest}=next;next=rest;}
    write(next);applied();sync();check();
  }
  function reset(){if(!row()?.pivot)return;remember();const {pivot,...rest}=row();write(rest);sync();check();}
  // The numeric fields, like the other tools': one per axis, wide enough for any value.
  for(const [id,type] of [['crew-position-fields','position'],['crew-rotation-fields','rotation'],['crew-pivot-fields','pivot']]){
    for(let axis=0;axis<3;axis++){
      const field=document.createElement('div');field.className='axis-field';
      const label=document.createElement('label');label.textContent=AXES[axis];
      const input=document.createElement('input');input.type='number';input.step=type==='rotation'?'1':'0.1';input.value=0;input.id=`crew-${type}-${axis}`;
      input.setAttribute('aria-label',`${type} ${AXES[axis]}`);label.htmlFor=input.id;
      input.onchange=()=>change(type,axis,input.value);
      field.append(label,input);$(id).append(field);}
  }
  $('crew-pose-move').onclick=()=>setMode('translate');
  $('crew-pose-rotate').onclick=()=>setMode('rotate');
  $('crew-pose-pivot').onclick=()=>setMode('pivot');
  $('crew-pivot-reset').onclick=reset;
  return {attach,sync,setMode,capture};
}
