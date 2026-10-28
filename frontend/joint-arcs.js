import * as THREE from 'three';

export function clearArcs(group){for(const child of [...group.children]){child.geometry?.dispose();child.material?.dispose();group.remove(child);}}

// Rebasing the selected live mount moves that joint's origin only. Its native
// axes and all descendant rest frames stay intact, as in pivots.rebase_joint.
// `level` (optional) maps the mounted joint's frame to the axes the built
// vehicle gives it: a tilted or rolled gun keeps a level elevation joint.
export function previewJointFrame(joint,anchor,pivot,level){
  let frame=joint.matrix.map(row=>[...row]);
  if(pivot&&joint.bone===anchor)for(let axis=0;axis<3;axis++)frame[axis][3]=pivot[axis];
  if(level&&joint.bone===anchor)frame=level(frame);
  return frame;
}

export function drawJointArc(group, frame, limits, radius=24, color=0x68d5b5) {
  const matrix=new THREE.Matrix4().set(...frame.flat()),origin=new THREE.Vector3().applyMatrix4(matrix),points=[origin];
  for(let i=0;i<=64;i++){
    const a=THREE.MathUtils.degToRad(limits[0]+(limits[1]-limits[0])*i/64);
    points.push(new THREE.Vector3(Math.cos(a)*radius,Math.sin(a)*radius,0).applyMatrix4(matrix));
  }
  points.push(origin.clone());
  group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color,transparent:true,opacity:.85})));
  const triangles=[];for(let i=1;i<points.length-2;i++)for(const p of [origin,points[i],points[i+1]])triangles.push(...p.toArray());
  const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(triangles,3));
  group.add(new THREE.Mesh(geo,new THREE.MeshBasicMaterial({color,transparent:true,opacity:.12,side:THREE.DoubleSide,depthWrite:false})));
}
