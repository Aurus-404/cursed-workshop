import * as THREE from 'three';
import {t} from './i18n.js';

// View > Armour: the game's armour volumes over the model. Pointing at the
// vehicle tells the volume and face under the cursor, its nominal thickness,
// the thickness along this line of sight (slope) and its rolled-steel (RHA)
// equivalent when the definition gives a cast-steel factor, plus every layer
// the line crosses (spaced and added armour). Faces are tinted by thickness.
const FACES=['','front','top','left','right','rear','bottom'];
const FACE_NAMES={front:'frente',top:'techo',left:'lateral izquierdo',right:'lateral derecho',rear:'trasera',bottom:'fondo',default:'espesor general'};
const LAYERS=4;

export function thicknessOf(volume,code){const face=FACES[code]||'';return volume.thickness[face]??volume.thickness.default;}
// Line-of-sight thickness through a plate hit at `cos` (|cos| of the angle to its normal).
export function lineOfSight(thickness,cos){return thickness/Math.max(Math.abs(cos),0.05);}
export function rhaOf(volume,millimetres){return volume.material==='steel'?millimetres*(volume.cast??1):null;}

// A volume's corners moved outwards by `distance`. Each corner moves once,
// along the mean outward normal of every face that shares it (corners at the
// same place count as one), so neighbouring triangles stay joined: moving each
// triangle on its own opened seams on curved plates. Volumes are convex
// plates, so "outwards" is away from the volume's centre, whatever the winding.
export function liftedPositions(positions,indices,distance){
  const count=positions.length/3,key=i=>`${positions[3*i].toFixed(4)},${positions[3*i+1].toFixed(4)},${positions[3*i+2].toFixed(4)}`;
  const centre=new THREE.Vector3(),point=i=>new THREE.Vector3(positions[3*i],positions[3*i+1],positions[3*i+2]);
  for(let i=0;i<count;i++)centre.add(point(i));
  centre.divideScalar(Math.max(count,1));
  const sums=new Map();
  for(let t=0;t+2<indices.length;t+=3){
    const [a,b,c]=[indices[t],indices[t+1],indices[t+2]].map(point);
    const normal=new THREE.Vector3().subVectors(c,b).cross(new THREE.Vector3().subVectors(a,b));
    if(normal.lengthSq()===0)continue;
    normal.normalize();
    if(normal.dot(a.clone().add(b).add(c).divideScalar(3).sub(centre))<0)normal.negate();
    for(const i of [indices[t],indices[t+1],indices[t+2]]){const k=key(i);sums.set(k,(sums.get(k)||new THREE.Vector3()).add(normal));}
  }
  const result=Float32Array.from(positions);
  for(let i=0;i<count;i++){
    const normal=sums.get(key(i));if(!normal||normal.lengthSq()===0)continue;
    normal.clone().normalize().toArray(result,3*i);
    for(let k=0;k<3;k++)result[3*i+k]=positions[3*i+k]+result[3*i+k]*distance;
  }
  return result;
}

// Preview coordinates use native model units. Only lift enough to separate
// coincident surfaces: at most .02 units, also bounded by this volume's own size.
// A vehicle-wide diagonal and increasing lift per list entry inflated late
// volumes visibly, especially on vehicles with many armour volumes.
export function armorPositions(volume){
  const extent=new THREE.Box3();
  for(let i=0;i<volume.positions.length;i+=3)extent.expandByPoint(new THREE.Vector3(...volume.positions.slice(i,i+3)));
  const size=extent.isEmpty()?0:extent.getSize(new THREE.Vector3()).length();
  return liftedPositions(volume.positions,volume.indices,Math.min(.02,size*.0001));
}

function tint(mm){const k=Math.min(mm/120,1);return new THREE.Color().setHSL(0.33*(1-k),0.85,0.5);}

export function armorView({state,groups,camera,canvas,viewport}){
  const layers={},tip=document.createElement('div');tip.className='armor-tip hidden';viewport.append(tip);
  const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();let hover=null,built={};
  function build(part){
    layers[part]?.removeFromParent();layers[part]?.traverse(n=>{n.geometry?.dispose();n.material?.dispose?.();});
    const data=state.previews[part],group=new THREE.Group();group.name='armor_layer';layers[part]=group;built[part]=data;
    // Armour volumes lie on the model's own surfaces and on each other (skirts on
    // the hull side): they z-fought and flickered with the camera angle and zoom.
    // A small bounded lift separates the model; depth bias separates coincident
    // armour layers without growing their geometry with the volume count.
    // Plates write depth: the nearest wins.
    let index=0;
    for(const volume of data?.armor||[]){
      const geometry=new THREE.BufferGeometry();
      geometry.setAttribute('position',new THREE.Float32BufferAttribute(armorPositions(volume),3));
      geometry.setIndex(volume.indices);
      // Non-indexed per-face colours: one colour per triangle by its face thickness.
      const flat=geometry.toNonIndexed(),colors=[];
      for(const code of volume.faces){const c=tint(thicknessOf(volume,code));for(let k=0;k<3;k++)colors.push(c.r,c.g,c.b);}
      flat.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));flat.computeVertexNormals();geometry.dispose();      const mesh=new THREE.Mesh(flat,new THREE.MeshBasicMaterial({vertexColors:true,transparent:true,opacity:.38,side:THREE.DoubleSide,depthWrite:true,
        polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-4-index}));
      mesh.userData.armor=volume;mesh.renderOrder=5+index++;group.add(mesh);
    }
    groups[part]?.add(group);
  }
  function sync(){
    const on=!!state.show.armor;
    for(const part of ['hull','turret']){if(on&&built[part]!==state.previews[part])build(part);if(layers[part])layers[part].visible=on&&groups[part]?.visible!==false;}
    for(const part of Object.keys(groups))if(groups[part]&&layers[part]&&layers[part].parent!==groups[part])groups[part].add(layers[part]);
    if(!on)hide();
  }
  function hide(){tip.classList.add('hidden');if(hover){hover.material.opacity=.38;hover=null;}}
  function hits(event){
    const box=canvas.getBoundingClientRect();pointer.set((event.clientX-box.left)/box.width*2-1,-((event.clientY-box.top)/box.height)*2+1);
    ray.setFromCamera(pointer,camera);
    const meshes=Object.values(layers).filter(l=>l?.visible).flatMap(l=>l.children);
    return ray.intersectObjects(meshes,false);
  }
  function describe(hit){
    const volume=hit.object.userData.armor,code=volume.faces[hit.faceIndex]??0,mm=thicknessOf(volume,code);
    // Name the face only when the definition gives it its own value; otherwise the general thickness applies.
    const face=volume.thickness[FACES[code]]!=null?FACES[code]:'default';
    const normal=hit.face.normal.clone().transformDirection(hit.object.matrixWorld),cos=normal.dot(ray.ray.direction);
    const line=lineOfSight(mm,cos),rha=rhaOf(volume,line),angle=Math.round(THREE.MathUtils.radToDeg(Math.acos(Math.min(1,Math.abs(cos)))));
    return {volume,face,mm,line,rha,angle};
  }
  function move(event){
    if(!state.show.armor||event.buttons){hide();return;}
    const list=hits(event);if(!list.length){hide();return;}
    const seen=new Set(),rows=[];
    for(const hit of list){const name=hit.object.userData.armor.name;if(seen.has(name))continue;seen.add(name);rows.push(describe(hit));if(rows.length>=LAYERS)break;}
    const first=rows[0];
    if(hover!==list[0].object){if(hover)hover.material.opacity=.38;hover=list[0].object;hover.material.opacity=.75;}
    const mm=v=>`${Math.round(v)} mm`,material=v=>v.material==='steel'?(v.cast?t('acero fundido')+` ×${v.cast}`:t('acero')):v.material;
    tip.replaceChildren();
    const head=document.createElement('strong');head.textContent=`${first.volume.name} · ${t(FACE_NAMES[first.face]||first.face)}`;tip.append(head);
    const lines=[`${t('Espesor')}: ${mm(first.mm)}`,`${t('Efectivo a')} ${first.angle}°: ${mm(first.line)}`,
      first.rha!=null?`RHA: ${mm(first.rha)}`:t('No es acero: no equivale a RHA'),`${t('Material')}: ${material(first.volume)}${first.volume.high_hardness?' · '+t('alta dureza'):''}`];
    for(const text of lines){const row=document.createElement('span');row.textContent=text;tip.append(row);}
    if(rows.length>1){const more=document.createElement('small');
      more.textContent=t('Capas en esta línea')+': '+rows.map(r=>`${r.volume.name} ${mm(r.line)}`).join(' · ');tip.append(more);}
    const box=viewport.getBoundingClientRect();tip.style.left=Math.min(event.clientX-box.left+16,box.width-260)+'px';tip.style.top=Math.max(event.clientY-box.top+16,8)+'px';
    tip.classList.remove('hidden');
  }
  canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerleave',hide);
  return {sync};
}
