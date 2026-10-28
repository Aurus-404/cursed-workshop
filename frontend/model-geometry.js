import * as THREE from 'three';

export function configureModelTexture(texture) {
  texture.flipY=false;
  texture.colorSpace=THREE.SRGBColorSpace;
  texture.wrapS=texture.wrapT=THREE.RepeatWrapping;
  texture.anisotropy=4;
  return texture;
}

export function previewMesh(mesh, material) {
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(mesh.positions,3));
  if(mesh.indices?.length)geometry.setIndex(mesh.indices);
  if(mesh.normals?.length)geometry.setAttribute('normal',new THREE.Float32BufferAttribute(mesh.normals,3));
  else geometry.computeVertexNormals();
  if(mesh.uv?.length)geometry.setAttribute('uv',new THREE.Float32BufferAttribute(mesh.uv,2));
  const object=new THREE.Mesh(geometry,material);object.userData={mesh,flat:material};return object;
}

export function applyModelTextures(groups, enabled, materialFor, wire) {
  for(const [part,group] of Object.entries(groups))group.traverse(child=>{
    const mesh=child.userData?.mesh;if(!mesh)return;
    if(enabled&&mesh.uv?.length&&mesh.submeshes?.some(s=>s.mtl)){
      child.geometry.clearGroups();
      child.material=mesh.submeshes.map((s,i)=>{child.geometry.addGroup(s.start,s.count,i);return s.mtl?materialFor(s.mtl,s.texture_token):child.userData.flat;});
    }else{child.geometry.clearGroups();
      if(!enabled&&child.userData.flat.map){child.userData.untextured??=child.userData.flat.clone();child.userData.untextured.map=null;if(['smoke_launchers','extra_armor'].includes(part))child.userData.untextured.color.set(0xc5a36b);child.userData.untextured.needsUpdate=true;}
      child.material=!enabled&&child.userData.untextured||child.userData.flat;}
    for(const material of [child.material].flat())material.wireframe=wire;
  });
}

const seatFrames=new WeakMap();
function reverseLateral(matrix){for(const index of [4,5,6])matrix.elements[index]*=-1;}

function applySeatFrame(frame,saved){
  frame.matrix.copy(saved.proper);
  if(saved.parentSign<0)reverseLateral(frame.matrix);
  // Correct anatomy around the animated body anchor, not the socket's origin.
  // Otherwise a pose's lateral offset is reflected to the other side as well.
  for(let r=0;r<3;r++)frame.matrix.elements[12+r]=saved.origin.reduce(
    (sum,value,c)=>sum+(saved.native.elements[c*4+r]-frame.matrix.elements[c*4+r])*value,0);
  frame.matrixWorldNeedsUpdate=true;
}

export function setSeatPoseOrigin(frame,origin){
  const saved=seatFrames.get(frame);if(!saved)return;
  saved.origin=Array.isArray(origin)&&origin.length===3&&origin.every(Number.isFinite)?[...origin]:[0,0,0];
  applySeatFrame(frame,saved);
}

export function seatFrame(rotation) {
  const frame=new THREE.Group();
  if(Array.isArray(rotation)){
    // A seat's native model frame can be reflected. It supplies the actor's
    // forward (X) and up (Z), but its negative lateral axis must not mirror the
    // human's anatomy. Correct only Y; preserve facing, tilt and native scale.
    frame.matrix.set(rotation[0][0],rotation[0][1],rotation[0][2],0,rotation[1][0],rotation[1][1],rotation[1][2],0,
      rotation[2][0],rotation[2][1],rotation[2][2],0,0,0,0,1);
  }
  const native=frame.matrix.clone();
  if(frame.matrix.determinant()<0)reverseLateral(frame.matrix);
  frame.matrixAutoUpdate=false;
  seatFrames.set(frame,{native,proper:frame.matrix.clone(),parentSign:1,origin:[0,0,0]});
  return frame;
}

export function updateSeatFrames(groups){
  for(const group of Object.values(groups)){
    group.updateWorldMatrix(true,true);
    group.traverse(frame=>{
      const saved=seatFrames.get(frame);if(!saved)return;
      // Fittings can inherit a reflected host too. Preserve the full world
      // forward/up, with one lateral correction for the complete actor frame.
      const sign=frame.parent?.matrixWorld.determinant()<0?-1:1;
      if(sign===saved.parentSign)return;
      saved.parentSign=sign;applySeatFrame(frame,saved);frame.updateWorldMatrix(false,true);
    });
  }
}
