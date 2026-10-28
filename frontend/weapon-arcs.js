// "Ángulos de las armas" view: every gun joint's traverse disc and elevation
// slice at once, each with a handle that turns the real 3D parts within the
// joint's limits. View only: it never touches the design, limits or undo.
//
// A joint turns about its own Z axis and rests with the weapon along its X
// axis (the convention of the game's revolute bones and of joint-arcs.js).
// A part follows every joint above it: its matrix is the product, outermost
// first, of M·Rz(angle)·M⁻¹ over its joints (M = joint frame at rest).
// Crew figures follow the joint above their seat bone, like the parts, except
// while one is being placed by hand (its own transform is then the edit).
import * as THREE from 'three';

const TRAVERSE=0x68d5b5, ELEVATION=0xe6b76d;
const Z=new THREE.Vector3(0,0,1);

export function clampAngle(angle,[min,max]){
  if(max-min>=360)return angle;
  if(angle>=min&&angle<=max)return angle;
  const gap=bound=>Math.abs(((angle-bound)%360+540)%360-180);
  return gap(min)<=gap(max)?min:max;
}

// Joints sorted outermost first; each with the nearest joint above it.
export function jointTree(joints,parentOf){
  const keys=new Set(joints.map(j=>j.key)),above={};
  for(const joint of joints){
    let bone=parentOf(joint.key);const seen=new Set();
    while(bone&&!keys.has(bone)&&!seen.has(bone)){seen.add(bone);bone=parentOf(bone);}
    above[joint.key]=keys.has(bone)?bone:null;
  }
  const depth=key=>{let d=0,k=above[key];while(k&&d<64){d++;k=above[k];}return d;};
  return [...joints].sort((a,b)=>depth(a.key)-depth(b.key)).map(j=>({...j,above:above[j.key]}));
}

// Cumulative part matrices for the current angles (degrees, by joint key).
export function jointDeltas(tree,angles){
  const deltas={};
  for(const joint of tree){
    const parent=joint.above?deltas[joint.above]:new THREE.Matrix4();
    const turn=new THREE.Matrix4().makeRotationAxis(Z,THREE.MathUtils.degToRad(angles[joint.key]||0));
    deltas[joint.key]=parent.clone().multiply(joint.matrix).multiply(turn).multiply(joint.matrix.clone().invert());
  }
  return deltas;
}

function sector(radius,[min,max],color){
  const full=max-min>=360,steps=full?96:Math.max(8,Math.ceil((max-min)/3.75)),points=[];
  for(let i=0;i<=steps;i++){const a=THREE.MathUtils.degToRad(min+(max-min)*i/steps);points.push(new THREE.Vector3(Math.cos(a)*radius,Math.sin(a)*radius,0));}
  const outline=full?points:[new THREE.Vector3(),...points,new THREE.Vector3()];
  const group=new THREE.Group();
  const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(outline),
    new THREE.LineBasicMaterial({color,transparent:true,opacity:.5,depthTest:false}));
  const triangles=[];for(let i=0;i<points.length-1;i++)triangles.push(0,0,0,...points[i].toArray(),...points[i+1].toArray());
  const area=new THREE.BufferGeometry();area.setAttribute('position',new THREE.Float32BufferAttribute(triangles,3));
  const fill=new THREE.Mesh(area,new THREE.MeshBasicMaterial({color,transparent:true,opacity:.06,side:THREE.DoubleSide,depthWrite:false,depthTest:false}));
  line.renderOrder=fill.renderOrder=20;group.add(fill,line);return group;
}

// Reach of a joint's parts at rest, in its own frame: the disc covers the
// turret's footprint, the elevation slice ends at the muzzle; arcs are
// drawn at 70 % of that so they cover less of the view.
function reach(joint,objects){
  const inverse=joint.matrix.clone().invert(),p=new THREE.Vector3();let planar=0,forward=0;
  for(const object of objects){
    const positions=object.geometry?.attributes?.position;if(!positions)continue;
    const rest=object.userData.weaponRest||object.matrix;
    for(let i=0;i<positions.count;i+=Math.max(1,Math.floor(positions.count/4000))){
      p.fromBufferAttribute(positions,i).applyMatrix4(rest).applyMatrix4(inverse);
      planar=Math.max(planar,Math.hypot(p.x,p.y));forward=Math.max(forward,p.x);
    }
  }
  return {planar,forward};
}

// Handles keep the same size on screen at any zoom (radius in pixels),
// a little bigger while hovered or dragged.
const HANDLE_PIXELS=5,HOVER_PIXELS=8;
function screenSize(handle,view,renderer){
  const height=renderer.domElement.clientHeight||1,world=new THREE.Vector3().setFromMatrixPosition(handle.matrixWorld);
  const perPixel=view.isOrthographicCamera?(view.top-view.bottom)/view.zoom/height
    :2*world.distanceTo(view.position)*Math.tan(THREE.MathUtils.degToRad(view.fov)/2)/height;
  const parentScale=new THREE.Vector3().setFromMatrixScale(handle.parent.matrixWorld).x||1;
  handle.scale.setScalar((handle.userData.hover?HOVER_PIXELS:HANDLE_PIXELS)*perPixel/parentScale);
  handle.updateMatrixWorld(true);
}

export function weaponArcs({state,groups,camera,viewport,orbit,rigs,describe=()=>''}){
  let built=[],signature='',drag=null,hoverText='';
  const label=document.createElement('div');label.className='weapon-hover-label';label.hidden=true;label.setAttribute('aria-hidden','true');
  if(getComputedStyle(viewport).position==='static')viewport.style.position='relative';
  viewport.append(label);
  const angles=new Map(),raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();

  function disposeOverlay(overlay){
    overlay.removeFromParent();
    overlay.traverse(node=>{node.geometry?.dispose();for(const m of [node.material].flat())m?.dispose();});
  }
  function restore(rig){
    for(const object of rig.objects){
      if(!object.userData.weaponRest)continue;
      object.matrix.copy(object.userData.weaponRest);delete object.userData.weaponRest;
      object.matrixAutoUpdate=object.userData.weaponAuto??true;delete object.userData.weaponAuto;
      object.matrix.decompose(object.position,object.quaternion,object.scale);
    }
  }
  function clear(){for(const rig of built){restore(rig);disposeOverlay(rig.overlay);}built=[];}

  function build(){
    clear();
    if(!state.show.arcs)return;
    for(const source of rigs()){
      if(!source?.group||!source.joints.length)continue;
      const tree=jointTree(source.joints,source.parentOf);
      const objects=[];
      const inSeat=node=>{for(let n=node.parent;n&&n!==source.group;n=n.parent)if(n.userData?.seat)return true;return false;};
      source.group.traverse(node=>{
        if(node.userData?.seat){if(!state.editSeat&&source.jointOf(node))objects.push(node);return;}
        if(node.isMesh&&node.userData?.mesh&&!inSeat(node)&&source.jointOf(node))objects.push(node);
      });
      for(const object of objects){
        object.updateMatrix();object.userData.weaponRest=object.matrix.clone();object.userData.weaponAuto=object.matrixAutoUpdate;object.matrixAutoUpdate=false;
      }
      const overlay=new THREE.Group();overlay.name='weapon_arcs';overlay.userData.keepGeometry=false;source.group.add(overlay);
      const joints=[],rows=[];
      for(const joint of tree){
        const own=objects.filter(object=>{let key=source.jointOf(object);while(key&&key!==joint.key)key=tree.find(j=>j.key===key)?.above;return key===joint.key;});
        if(!own.length)continue;
        const traverse=joint.kind?joint.kind==='traverse':Math.abs(new THREE.Vector3().setFromMatrixColumn(joint.matrix,2).normalize().z)>=.7;
        const size=reach(joint,own);rows.push({joint,traverse,radius:Math.max(4,(traverse?size.planar:size.forward)*.7)});
      }
      // Gun, coaxial and machine-gun joints sit close together: a handle that
      // would land on another one at rest moves outwards along its own arc.
      const gap=Math.max(4,Math.max(0,...rows.map(row=>row.radius))*.12),placed=[];
      for(const row of rows){
        const at=radius=>new THREE.Vector3(radius,0,0).applyMatrix4(row.joint.matrix);
        for(let tries=0;tries<8&&placed.some(p=>p.distanceTo(at(row.radius))<gap);tries++)row.radius+=gap;
        placed.push(at(row.radius));
      }
      for(const {joint,traverse,radius} of rows){
        const frame=new THREE.Group();frame.matrixAutoUpdate=false;overlay.add(frame);
        frame.add(sector(radius,joint.limits(),traverse?TRAVERSE:ELEVATION));
        const color=traverse?TRAVERSE:ELEVATION;
        const handle=new THREE.Mesh(new THREE.SphereGeometry(1,20,14),
          new THREE.MeshBasicMaterial({color,depthTest:false,transparent:true,opacity:.95}));
        handle.renderOrder=21;handle.userData.weaponHandle=true;handle.userData.kind=traverse?'traverse':'elevation';
        handle.onBeforeRender=(renderer,scene,view)=>screenSize(handle,view,renderer);
        const aim=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(),new THREE.Vector3(radius,0,0)]),
          new THREE.LineBasicMaterial({color,depthTest:false}));
        aim.renderOrder=21;frame.add(aim,handle);
        const entry={...joint,radius,frame,handle,aim,rig:source.key};handle.userData.joint=entry;joints.push(entry);
      }
      built.push({key:source.key,group:source.group,overlay,objects,joints,tree,jointOf:source.jointOf});
    }
    apply();
  }

  function angleKey(rig,joint){return rig+'|'+joint;}
  function apply(){
    for(const rig of built){
      const current={};for(const joint of rig.tree)current[joint.key]=clampAngle(angles.get(angleKey(rig.key,joint.key))||0,joint.limits());
      const deltas=jointDeltas(rig.tree,current);
      for(const object of rig.objects){
        const key=rig.jointOf(object);object.matrix.copy(deltas[key]||new THREE.Matrix4()).multiply(object.userData.weaponRest);
        object.matrixWorldNeedsUpdate=true;
      }
      for(const joint of rig.joints){
        const parent=joint.above?deltas[joint.above]:new THREE.Matrix4();
        joint.frame.matrix.copy(parent).multiply(joint.matrix);joint.frame.matrixWorldNeedsUpdate=true;
        const a=THREE.MathUtils.degToRad(current[joint.key]);
        joint.handle.position.set(Math.cos(a)*joint.radius,Math.sin(a)*joint.radius,0);
        joint.aim.geometry.setFromPoints([new THREE.Vector3(),joint.handle.position.clone()]);
      }
      rig.group.updateMatrixWorld(true);
    }
  }

  function reset(){angles.clear();apply();}

  function currentSignature(){
    if(!state.show.arcs)return 'off';
    return JSON.stringify([state.editSeat||'',state.transform.crew_seats||{},state.transform.crew_exclude||[],
      ...rigs().map(rig=>rig&&[rig.key,rig.revision,rig.joints.map(j=>[j.key,j.limits(),j.matrix.elements.map(v=>+v.toFixed(3))])])]);
  }
  function refresh(force=false){
    const next=currentSignature();
    if(!force&&next===signature&&built.every(rig=>rig.overlay.parent===rig.group))return;
    if(next==='off')angles.clear();  // turning the view off puts every weapon back
    signature=next;build();
  }
  setInterval(()=>refresh(),300);

  function hit(event){
    const box=viewport.getBoundingClientRect();
    pointer.set((event.clientX-box.left)/box.width*2-1,-(event.clientY-box.top)/box.height*2+1);
    raycaster.setFromCamera(pointer,camera);
    return raycaster;
  }
  function frameWorld(joint){return joint.frame.matrixWorld;}
  function angleAt(event,joint){
    const world=frameWorld(joint),origin=new THREE.Vector3().setFromMatrixPosition(world);
    const normal=new THREE.Vector3().setFromMatrixColumn(world,2).normalize();
    const point=hit(event).ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(normal,origin),new THREE.Vector3());
    if(!point)return null;
    const local=point.applyMatrix4(world.clone().invert());
    return THREE.MathUtils.radToDeg(Math.atan2(local.y,local.x));
  }
  viewport.addEventListener('pointerdown',event=>{
    if(!state.show.arcs||event.button!==0||state.busy)return;
    const handles=built.flatMap(rig=>rig.joints.map(j=>j.handle));if(!handles.length)return;
    const found=hit(event).intersectObjects(handles,false)[0];if(!found)return;
    event.preventDefault();event.stopImmediatePropagation();
    drag={joint:found.object.userData.joint,orbit:orbit.enabled};found.object.userData.hover=true;orbit.enabled=false;
    viewport.setPointerCapture?.(event.pointerId);viewport.style.cursor='grabbing';
  },true);
  viewport.addEventListener('pointermove',event=>{
    if(!drag){
      if(!state.show.arcs)return;
      const handles=built.flatMap(rig=>rig.joints.map(j=>j.handle));
      const over=handles.length?hit(event).intersectObjects(handles,false)[0]?.object:null;
      for(const handle of handles)handle.userData.hover=handle===over;
      viewport.style.cursor=over?'grab':'';
      // Which weapon and which movement, since several handles sit close together.
      const text=over?describe(over.userData.joint,over.userData.kind):'';
      if(text!==hoverText){hoverText=text;label.textContent=text;label.hidden=!text;}
      if(text){
        const box=viewport.getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top;
        // Next to the pointer, on the side where it fits inside the 3D view.
        const left=x+14+label.offsetWidth>box.width?x-14-label.offsetWidth:x+14;
        const top=y+14+label.offsetHeight>box.height?y-14-label.offsetHeight:y+14;
        label.style.left=`${Math.max(4,left)}px`;label.style.top=`${Math.max(4,top)}px`;
      }
      return;
    }
    event.stopImmediatePropagation();
    const angle=angleAt(event,drag.joint);if(angle===null)return;
    angles.set(angleKey(drag.joint.rig,drag.joint.key),clampAngle(angle,drag.joint.limits()));apply();
  },true);
  const release=event=>{if(!drag)return;event.stopImmediatePropagation();orbit.enabled=drag.orbit;drag.joint.handle.userData.hover=false;drag=null;viewport.style.cursor='';
    viewport.releasePointerCapture?.(event.pointerId);};
  viewport.addEventListener('pointerup',release,true);viewport.addEventListener('pointercancel',release,true);

  return {refresh:()=>refresh(),reset,rebuild:()=>refresh(true)};
}
