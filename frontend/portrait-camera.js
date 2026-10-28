import * as THREE from 'three';

// +X is the nose. Viewing from +Y projects it toward the LEFT. Favor +X
// to hide more of the rear, without mirroring textures or geometry.
export const PORTRAIT_CAMERA=Object.freeze({direction:Object.freeze([1.35,1,.62]),fov:26,margin:.06});
export const ICON_CAMERA=Object.freeze({direction:Object.freeze([1.35,1,.32]),margin:.04});

export function portraitCamera(model,width,height,margin=PORTRAIT_CAMERA.margin,direction=PORTRAIT_CAMERA.direction){
  model.updateMatrixWorld(true);const box=new THREE.Box3().setFromObject(model);
  if(box.isEmpty())throw new Error('El modelo armado no tiene mallas para fotografiar.');
  const center=box.getCenter(new THREE.Vector3()),view=new THREE.Vector3(...direction).normalize();
  const camera=new THREE.PerspectiveCamera(PORTRAIT_CAMERA.fov,width/height,.01,1e6);
  camera.up.set(0,0,1);camera.position.copy(center).add(view);camera.lookAt(center);camera.updateMatrixWorld(true);
  const right=new THREE.Vector3(1,0,0).applyQuaternion(camera.quaternion),up=new THREE.Vector3(0,1,0).applyQuaternion(camera.quaternion);
  const tanV=Math.tan(THREE.MathUtils.degToRad(PORTRAIT_CAMERA.fov/2)),tanH=tanV*width/height,usable=1-2*margin;
  let distance=1;const point=new THREE.Vector3();
  // Fitting projected mesh vertices keeps even long guns and off-centre parts
  // inside both frame dimensions, without per-vehicle scale adjustments.
  model.traverse(object=>{if(!object.isMesh)return;const positions=object.geometry.attributes.position;
    for(let i=0;i<positions.count;i++){
      point.fromBufferAttribute(positions,i).applyMatrix4(object.matrixWorld).sub(center);const depth=point.dot(view);
      distance=Math.max(distance,depth+Math.abs(point.dot(right))/(tanH*usable),depth+Math.abs(point.dot(up))/(tanV*usable));
    }
  });
  camera.position.copy(center).addScaledVector(view,distance);camera.lookAt(center);
  camera.near=Math.max(.01,distance/10000);camera.far=Math.max(10000,distance*20);
  camera.updateProjectionMatrix();camera.updateMatrixWorld(true);return camera;
}
