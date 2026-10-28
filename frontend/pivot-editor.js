import * as THREE from 'three';

export function fittingParent(row, commanderBones={}){
  const ref=row?.parent_ref;
  return ref?.kind==='commander_mg'&&ref.name===row.parent?
    commanderBones?.[ref.bone]||row.parent:row?.parent;
}

export function poseMatrix(pose,base=[0,0,0]){
  const pivot=new THREE.Vector3(...(pose.pivot||[0,0,0]));
  const q=new THREE.Quaternion().setFromEuler(new THREE.Euler(...pose.rotation.map(THREE.MathUtils.degToRad),'ZYX'));
  const position=new THREE.Vector3(...pose.position).add(new THREE.Vector3(...base)).add(pivot).sub(pivot.clone().applyQuaternion(q));
  return new THREE.Matrix4().compose(position,q,new THREE.Vector3(1,1,1));
}
export function applyPose(object,pose,base){poseMatrix(pose,base).decompose(object.position,object.quaternion,object.scale);object.updateMatrixWorld(true);}
export function changePivot(pose,value){
  const old=new THREE.Vector3(...(pose.pivot||[0,0,0])),next=new THREE.Vector3(...value);
  const q=new THREE.Quaternion().setFromEuler(new THREE.Euler(...pose.rotation.map(THREE.MathUtils.degToRad),'ZYX'));
  pose.position=new THREE.Vector3(...pose.position).add(old).sub(old.clone().applyQuaternion(q)).sub(next).add(next.clone().applyQuaternion(q)).toArray();
  pose.pivot=[...value];
}

export function pivotEditor({scene,control,state,$,pose,target,base=()=>[0,0,0],remember,apply,check,select}){
  const proxy=new THREE.Group();proxy.name='editor_pivot';scene.add(proxy);let capturing=false;
  function sync(){
    const row=pose(),object=target();if(!row||!object||capturing)return;
    object.updateWorldMatrix(true,false);
    const matrix=object.matrixWorld.clone().multiply(new THREE.Matrix4().makeTranslation(...(row.pivot||[0,0,0])));
    matrix.decompose(proxy.position,proxy.quaternion,proxy.scale);proxy.updateMatrixWorld(true);
    for(let i=0;i<3;i++)$('pivot-'+i).value=(row.pivot||[0,0,0])[i];
    $('pose-pivot').setAttribute('aria-pressed',String(!!state.editPivot));
    $('pose-pivot').classList.toggle('active',!!state.editPivot);
    for(const [id,mode] of [['pose-move','translate'],['pose-rotate','rotate']]){
      const active=!state.editPivot&&control.getMode()===mode;
      $(id).classList.toggle('active',active);$(id).setAttribute('aria-pressed',String(active));
    }
  }
  function attach(){sync();control.attach(proxy);if(state.editPivot)control.setMode('translate');}
  function capture(){
    if(control.object!==proxy)return false;
    const row=pose(),object=target();if(!row||!object)return false;
    capturing=true;
    try{
      proxy.updateMatrixWorld(true);object.updateWorldMatrix(true,false);
      if(state.editPivot){changePivot(row,object.worldToLocal(proxy.getWorldPosition(new THREE.Vector3())).toArray());}
      else{
        const p=row.pivot||[0,0,0],matrix=object.parent.matrixWorld.clone().invert().multiply(proxy.matrixWorld).multiply(new THREE.Matrix4().makeTranslation(...p.map(v=>-v)));
        const position=new THREE.Vector3(),q=new THREE.Quaternion(),scale=new THREE.Vector3();matrix.decompose(position,q,scale);
        row.rotation=new THREE.Euler().setFromQuaternion(q,'ZYX').toArray().slice(0,3).map(THREE.MathUtils.radToDeg);
        const pivot=new THREE.Vector3(...p);row.position=position.sub(new THREE.Vector3(...base())).sub(pivot).add(pivot.applyQuaternion(q)).toArray();
      }
      apply();
    }finally{capturing=false;}sync();return true;
  }
  $('pose-pivot').onclick=()=>{if(state.busy||state.inspectorPending)return;state.editPivot=true;select(state.edit);};
  $('pivot-reset').onclick=()=>{if(!pose()||state.busy||state.inspectorPending)return;remember();changePivot(pose(),[0,0,0]);apply();sync();check();};
  for(let i=0;i<3;i++)$('pivot-'+i).onchange=()=>{
    if(!pose()||state.busy||state.inspectorPending)return;
    const value=[0,1,2].map(axis=>Number($('pivot-'+axis).value));
    if(value.some(v=>!Number.isFinite(v)||Math.abs(v)>100000)){sync();return;}
    remember();changePivot(pose(),value);apply();sync();check();
  };
  return {attach,sync,capture,proxy};
}
