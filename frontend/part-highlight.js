import * as THREE from 'three';

// Marks a part of the vehicle in the 3D view while the player points at it in
// any list that picks a part (mount, bone, weapon place, attachment host...).
// The part's meshes and everything hanging from it get a see-through tint (no
// box: its lines cluttered the view); a part without geometry (dummy01, an empty locator) gets a marker on
// its position, so every choice is visible.
const COLOR=0xffc04d;

export function partHighlight({scene,groups,previews}){
  const layer=new THREE.Group();layer.name='part_highlight';scene.add(layer);
  const tint=new THREE.MeshBasicMaterial({color:COLOR,transparent:true,opacity:.42,depthTest:false,depthWrite:false,side:THREE.DoubleSide});
  tint.userData.shared=true;
  let overlays=[];

  function clear(){
    for(const node of overlays)node.removeFromParent();
    overlays=[];
    for(const node of [...layer.children]){node.removeFromParent();node.geometry?.dispose();if(node.material!==tint)node.material?.dispose?.();}
  }
  // Every bone under `names` in that part's skeleton, the names included.
  function subtree(part,names){
    const bones=previews()[part]?.bones||[],children=new Map();
    for(const bone of bones){if(!children.has(bone.parent))children.set(bone.parent,[]);children.get(bone.parent).push(bone.name);}
    const out=new Set(),stack=[...names];
    while(stack.length){const name=stack.pop();if(out.has(name))continue;out.add(name);stack.push(...(children.get(name)||[]));}
    return out;
  }
  function meshesOf(part,names){
    const found=[];
    groups[part]?.traverse(node=>{if(node.isMesh&&node.userData?.mesh&&names.has(node.userData.mesh.bone)&&node.visible!==false)found.push(node);});
    return found;
  }
  function marker(position){
    const ball=new THREE.Mesh(new THREE.SphereGeometry(4,16,12),new THREE.MeshBasicMaterial({color:COLOR,depthTest:false,transparent:true,opacity:.9}));
    ball.position.copy(position);ball.renderOrder=1000;layer.add(ball);
    const ring=new THREE.Mesh(new THREE.TorusGeometry(9,.8,8,32),new THREE.MeshBasicMaterial({color:COLOR,depthTest:false,transparent:true,opacity:.75}));
    ring.position.copy(position);ring.renderOrder=1000;layer.add(ring);
  }
  // spec: {bones:[names], parts?:['hull'|'turret'], subtree?:true, point?:THREE.Vector3 (world), whole?:part}
  function show(spec){
    clear();if(!spec)return false;
    const parts=spec.parts||['hull','turret'];
    let meshes=[];
    if(spec.whole){groups[spec.whole]?.traverse(node=>{if(node.isMesh&&node.userData?.mesh&&node.visible!==false)meshes.push(node);});}
    else for(const part of parts){
      const names=spec.subtree===false?new Set(spec.bones):subtree(part,spec.bones||[]);
      meshes.push(...meshesOf(part,names));
    }
    for(const mesh of meshes){
      const overlay=new THREE.Mesh(mesh.geometry,tint);overlay.renderOrder=999;overlay.userData.keepGeometry=true;overlay.raycast=()=>{};
      mesh.add(overlay);overlays.push(overlay);
    }
    if(meshes.length)return true;
    // No geometry: point at the bone itself.
    let point=spec.point||null;
    if(!point)for(const part of parts){
      const bone=(previews()[part]?.bones||[]).find(b=>(spec.bones||[]).includes(b.name));
      if(bone&&groups[part]){groups[part].updateWorldMatrix(true,false);point=groups[part].localToWorld(new THREE.Vector3(...bone.position));break;}
    }
    if(point){marker(point);return true;}
    return false;
  }
  return {show,clear};
}
