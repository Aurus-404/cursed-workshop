import * as THREE from 'three';

// Gates of Hell stores +X forward, +Y right and +Z up: a left-handed frame.
// THREE draws right-handed, so the same numbers showed every model as its
// mirror image (decals read backwards, a Pz IV driver on the right). The view
// mirrors its screen X once; scene coordinates, poses and recipes stay the
// game's own, and picking, gizmos and arcs keep working on them unchanged.
const MIRROR=new THREE.Matrix4().makeScale(-1,1,1);

export function gameCamera(camera){
  if(camera.userData.gameView)return camera;
  const update=camera.updateProjectionMatrix.bind(camera);
  camera.updateProjectionMatrix=()=>{update();camera.projectionMatrix.premultiply(MIRROR);camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();};
  camera.userData.gameView=true;camera.updateProjectionMatrix();return camera;
}

// A mirrored screen reverses every triangle's winding: one-sided materials
// (the gizmo's) must keep facing the camera.
export function gameRenderer(renderer){
  const state=renderer.state;if(state.gameView)return renderer;
  const setMaterial=state.setMaterial;state.setMaterial=(material,frontFaceCW,...rest)=>setMaterial(material,!frontFaceCW,...rest);
  state.gameView=true;return renderer;
}

// Dragging sideways must still turn and pan the view the way the cursor goes.
export function gameOrbit(orbit){
  for(const name of ['_rotateLeft','_panLeft']){const original=orbit[name].bind(orbit);orbit[name]=(value,...rest)=>original(-value,...rest);}
  return orbit;
}
