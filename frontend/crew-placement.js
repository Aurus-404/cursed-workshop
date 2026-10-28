import * as THREE from 'three';

// Aiming makes a worker mandatory, but never prevents moving their seat.
export function canPlaceCrew(figure, excluded=[]) {
  return !!figure?.userData?.weapon && !excluded.includes(figure.name);
}

export function saveCrewPlacement(figure, module) {
  module.updateWorldMatrix(true,true);
  const local=module.worldToLocal(figure.getWorldPosition(new THREE.Vector3()));
  const rotation=[figure.rotation.x,figure.rotation.y,figure.rotation.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));
  return {position:local.toArray().map(v=>Number(v.toFixed(3))),space:'module',...(rotation.some(v=>v)?{rotation}:{})};
}

export function placeCrewFigure(figure,module,manual,fixed,box) {
  figure.position.set(...figure.userData.origin);
  figure.rotation.set(...(manual?.rotation||[0,0,0]).map(THREE.MathUtils.degToRad),'ZYX');
  if(manual?.position) {
    // Before schema space was added, positions were final-model coordinates.
    const position=new THREE.Vector3(...manual.position);
    figure.position.copy(manual.space==='module'?position:module.worldToLocal(position));
    return;
  }
  if(!fixed||!figure.userData.staticSeat||box.isEmpty())return;
  const size=box.getSize(new THREE.Vector3());
  const padX=Math.min(Math.max(size.x*.06,6),size.x*.45),padY=Math.min(Math.max(size.y*.06,6),size.y*.45);
  const world=module.localToWorld(new THREE.Vector3(...figure.userData.origin));
  world.x=THREE.MathUtils.clamp(world.x,box.min.x+padX,box.max.x-padX);
  world.y=THREE.MathUtils.clamp(world.y,box.min.y+padY,box.max.y-padY);
  figure.position.copy(module.worldToLocal(world));
}
