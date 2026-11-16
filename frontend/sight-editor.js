import * as THREE from 'three';
import {applyPose,poseMatrix,poseFromMatrix} from './pivot-editor.js';
import {gameCamera} from './game-view.js';
import {t} from './i18n.js';

export function sightEditor({state,$,scene,remember,selectEdit,refreshCheck,reportError,active}){
  const host=new THREE.Group(),socket=new THREE.Group();scene.add(host);host.add(socket);host.matrixAutoUpdate=false;
  const axes=new THREE.AxesHelper(12);socket.add(axes);
  socket.add(new THREE.ArrowHelper(new THREE.Vector3(1,0,0),new THREE.Vector3(),20,0xffc44d));
  const camera=gameCamera(new THREE.PerspectiveCamera(40,1,.1,10000));camera.up.set(0,0,1);
  let hosts=[],initial=null,preview=false;
  const pose=()=>state.transform.sight||null;
  const matrix=value=>new THREE.Matrix4().set(...value.flat());
  function stop(){preview=false;$('sight-preview').textContent=t('Previsualizar mira');}
  function sync(){
    const value=pose(),parent=hosts.find(h=>h.name===value?.parent);
    host.visible=!!value&&!!parent&&active();
    for(const child of socket.children)child.visible=!preview;
    host.userData.hostBone=parent?.name;
    if(parent){host.matrix.copy(matrix(parent.matrix));applyPose(socket,value);host.updateMatrixWorld(true);}
    $('sight-enable').disabled=state.busy||!initial;
    $('sight-parent').disabled=state.busy||!value;$('sight-preview').disabled=state.busy||!parent;$('sight-reset').disabled=state.busy||!value;
  }
  function receiveCheck(check){hosts=check.sight_hosts||[];initial=check.sight_default||null;
    $('sight-parent').replaceChildren(...hosts.map(h=>new Option(h.name,h.name)));
    if(pose())$('sight-parent').value=pose().parent;sync();}
  $('sight-enable').onclick=()=>{if(state.busy||!initial)return;remember();state.transform.sight=structuredClone(pose()||initial);sync();selectEdit('sight');refreshCheck();};
  $('sight-parent').onchange=()=>{
    if(state.busy||!pose())return;
    const old=hosts.find(h=>h.name===pose().parent),next=hosts.find(h=>h.name===$('sight-parent').value);if(!old||!next)return;
    try{const value=poseFromMatrix(matrix(next.matrix).invert().multiply(matrix(old.matrix)).multiply(poseMatrix(pose())),pose());remember();state.transform.sight={...value,parent:next.name};sync();selectEdit('sight');refreshCheck();}catch(error){reportError(error);}
  };
  $('sight-preview').onclick=()=>{preview=!preview;$('sight-preview').textContent=t(preview?'Salir de la mira':'Previsualizar mira');sync();};
  $('sight-reset').onclick=()=>{remember();delete state.transform.sight;stop();sync();selectEdit('sight');refreshCheck();};
  function view(aspect){
    if(!preview||!pose()||!active())return null;
    host.updateWorldMatrix(true,true);camera.position.setFromMatrixPosition(socket.matrixWorld);
    const direction=new THREE.Vector3(1,0,0).transformDirection(socket.matrixWorld);
    camera.up.set(0,0,1).transformDirection(socket.matrixWorld);camera.lookAt(camera.position.clone().add(direction));
    camera.aspect=aspect;camera.updateProjectionMatrix();return camera;
  }
  return {host,pose,target:()=>pose()?socket:null,sync,receiveCheck,view,deactivate(){stop();sync();}};
}
