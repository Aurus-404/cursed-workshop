import {applyPose} from './pivot-editor.js';
import * as THREE from 'three';
import {t} from './i18n.js';
import {previewMesh} from './model-geometry.js';
import {latestTask} from './latest-task.js';
import {dlcCatalogNote} from './dlc-catalog.js';

// A third editable part, using the same transform/history and compilation
// contract as the chassis and module. Coordinates are local to the host bone.
export function commanderProblem(check){
  if(!check||check.status==='pending')return 'Comprobando compatibilidad…';
  if(check.status!=='ok')return check.message||'No se pudo comprobar la compatibilidad del vehículo.';
  if(!check.crew?.includes('commander'))return 'Este ensamblaje no tiene un puesto de comandante. No se agregan tripulantes.';
  if(check.commander_problem)return check.commander_problem;
  return '';
}
export function commanderEditor({state,groups,scene,api,quickJob,remember,selectEdit,refreshCheck,reportError,addSeats,applyShow=()=>{},active=()=>state.edit==='commander_mg',catalogJob=()=>api('/api/commander-mg')}) {
  const $=id=>document.getElementById(id), group=groups.commander_mg;
  const host=new THREE.Group();host.matrixAutoUpdate=false;scene.add(host);host.add(group);
  let catalog=null, catalogRevision, hosts=[], revision=0, arcs=new THREE.Group(), pitchFrame=null, meshes=0;
  const previewTask=latestTask((signal,spec)=>quickJob('/api/commander-mg-preview',spec,{signal}),180);
  group.add(arcs);
  function matrix(rows){return new THREE.Matrix4().set(...rows.flat());}
  function controls(){const spec=state.transform.commander_mg;
    $('cmd-enabled').checked=!!spec;$('cmd-fields').classList.toggle('hidden',!spec);
    $('edit-commander').disabled=!spec;
    $('cmd-add').classList.toggle('hidden',!!spec);$('cmd-remove').classList.toggle('hidden',!spec);
    $('cmd-availability').textContent=t(commanderProblem(state.check)||state.check?.commander_warning||'');$('cmd-add').disabled=state.busy||!state.hull||!state.turret||!!commanderProblem(state.check);$('cmd-remove').disabled=state.busy;
    if(!spec)return;
    $('cmd-support').value=spec.support;$('cmd-item').value=spec.item;$('cmd-parent').value=spec.parent;
    for(const type of ['yaw','pitch'])for(let i=0;i<2;i++)$(`cmd-${type}-${i}`).value=spec[type][i];
  }
  function drawArcs(){for(const child of [...arcs.children]){child.geometry.dispose();child.material.dispose();arcs.remove(child);}
    const spec=state.transform.commander_mg;if(!spec)return;
    for(const type of ['yaw','pitch']){const [min,max]=spec[type],radius=type==='yaw'?21:16,origin=new THREE.Vector3(...(type==='yaw'?(spec.pivot||[0,0,0]):[0,0,0]));if(type==='pitch'&&pitchFrame)origin.applyMatrix4(matrix(pitchFrame));const points=[origin];
      for(let i=0;i<=48;i++){const a=THREE.MathUtils.degToRad(min+(max-min)*i/48);let p=new THREE.Vector3(Math.cos(a)*radius,Math.sin(a)*radius,0);if(type==='yaw')p.add(origin);if(type==='pitch'){if(pitchFrame)p.applyMatrix4(matrix(pitchFrame));else p.set(Math.cos(a)*radius,0,Math.sin(a)*radius);}points.push(p);}
      points.push(origin.clone());
      const geometry=new THREE.BufferGeometry().setFromPoints(points),line=new THREE.Line(geometry,new THREE.LineBasicMaterial({color:type==='yaw'?0x68d5b5:0xe6b76d,transparent:true,opacity:.85}));arcs.add(line);
      const triangles=[];for(let i=1;i<points.length-2;i++)for(const p of [points[0],points[i],points[i+1]])triangles.push(...p.toArray());
      const area=new THREE.BufferGeometry();area.setAttribute('position',new THREE.Float32BufferAttribute(triangles,3));arcs.add(new THREE.Mesh(area,new THREE.MeshBasicMaterial({color:type==='yaw'?0x68d5b5:0xe6b76d,transparent:true,opacity:.12,side:THREE.DoubleSide,depthWrite:false})));
    }
  }
  function sync(){const spec=state.transform.commander_mg;group.visible=!!spec;
    if(!spec)return;
    const parent=hosts.find(h=>h.name===spec.parent);
    if(parent)host.matrix.copy(matrix(parent.matrix));
    applyPose(group,spec);
    group.updateMatrixWorld(true);arcs.visible=active();
  }
  function clearMeshes(){for(const child of [...group.children]){if(child===arcs)continue;group.remove(child);child.traverse(n=>{if(!n.userData.keepGeometry)n.geometry?.dispose();for(const m of [n.material].flat())if(m&&!m.userData?.shared)m.dispose();});}}
  async function preview(){const spec=state.transform.commander_mg,version=++revision;if(!spec){previewTask.cancel();clearMeshes();sync();return;}
    try{const data=await previewTask.run(structuredClone(spec));if(version!==revision)return;
      clearMeshes();for(const mesh of data.meshes){
        const material=new THREE.MeshStandardMaterial({color:0xc9ad74,roughness:.8,side:THREE.DoubleSide,wireframe:state.wire});group.add(previewMesh(mesh,material));}
      addSeats('commander_mg',data);pitchFrame=data.pitch_matrix||null;meshes++;
      drawArcs();sync();applyShow();
    }catch(error){if(error.name!=='AbortError'&&version===revision){$('cmd-warning').textContent=error.message;reportError(error);}}
  }
  async function ensureCatalog(){if(catalog&&catalogRevision===state.artRevision)return;catalog=await catalogJob();catalogRevision=state.artRevision;
    $('cmd-dlc-note').textContent=dlcCatalogNote(catalog);
    $('cmd-support').replaceChildren(...catalog.supports.map(s=>new Option(s.label||s.id,s.id)));
    $('cmd-item').replaceChildren(...catalog.weapons.map(w=>new Option(w.label,w.id)));
  }
  function receiveCheck(check){hosts=check.hosts||hosts;$('cmd-parent').replaceChildren(...hosts.map(h=>new Option(h.name,h.name)));controls();sync();}
  async function restore(){controls();if(!state.transform.commander_mg){++revision;previewTask.cancel();clearMeshes();sync();applyShow();return;}
    try{await ensureCatalog();controls();await preview();}catch(error){reportError(error);}}
  $('cmd-enabled').onchange=async()=>{if(state.busy){controls();return;}remember();
    if(!$('cmd-enabled').checked){delete state.transform.commander_mg;await restore();selectEdit('turret');refreshCheck();return;}
    try{await refreshCheck();if(state.toolController?.signal.aborted)throw new DOMException('Operation cancelled','AbortError');let check=state.check;
      if(commanderProblem(check))throw new Error(t(commanderProblem(check)));
      await ensureCatalog();
      // The player may change the assembly while the first catalog is loading.
      await refreshCheck();check=state.check;
      if(commanderProblem(check))throw new Error(t(commanderProblem(check)));
      receiveCheck(check);if(!hosts.length||!catalog.supports.length||!catalog.weapons.length)throw new Error(t('No hay monturas o armas base verificadas disponibles.'));
      const parent=hosts.find(h=>h.name==='turret')||hosts[0];
      const position=new THREE.Vector3(...(check.commander_position||[0,0,0])).applyMatrix4(matrix(parent.matrix).invert()).toArray();
      state.transform.commander_mg={parent:parent.name,position,rotation:[0,0,0],yaw:[-155,55],pitch:[-20,45],item:catalog.weapons.find(w=>w.id==='mg34_drum')?.id||catalog.weapons[0].id,support:catalog.supports[0].id,operator:'commander'};
      if(state.show)state.show.crew=true;
      $('cmd-warning').textContent=t('Experimental: la escotilla, el intercambio de armas y la IA se probarán en batalla.');
      controls();await preview();selectEdit('commander_mg');refreshCheck();
    }catch(error){delete state.transform.commander_mg;controls();reportError(error);}}
  for(const [id,field] of [['cmd-support','support'],['cmd-item','item'],['cmd-parent','parent']])$(id).onchange=()=>{const spec=state.transform.commander_mg;if(!spec)return;remember();
    if(field==='parent'){
      // Reparent without teleporting the player's carefully placed support.
      const next=hosts.find(h=>h.name===$(id).value);if(!next)return;sync();group.updateWorldMatrix(true,false);
      const local=matrix(next.matrix).invert().multiply(group.matrixWorld),position=new THREE.Vector3(),rotation=new THREE.Quaternion(),scale=new THREE.Vector3();
      local.decompose(position,rotation,scale);
      if(scale.toArray().some(v=>Math.abs(v-1)>.002)){controls();reportError(new Error(t('Ese padre cambia la escala o refleja la montura. Elegí otro hueso.')));return;}
      const pivot=new THREE.Vector3(...(spec.pivot||[0,0,0]));spec.position=position.sub(pivot).add(pivot.clone().applyQuaternion(rotation)).toArray();spec.rotation=new THREE.Euler().setFromQuaternion(rotation,'ZYX').toArray().slice(0,3).map(THREE.MathUtils.radToDeg);
    }
    spec[field]=$(id).value;sync();if(field!=='parent')return preview().then(refreshCheck);selectEdit('commander_mg');refreshCheck();};
  for(const type of ['yaw','pitch'])for(let i=0;i<2;i++)$(`cmd-${type}-${i}`).onchange=()=>{const spec=state.transform.commander_mg;if(!spec)return;remember();spec[type][i]=Number($(`cmd-${type}-${i}`).value);drawArcs();refreshCheck();};
  $('edit-commander').onclick=()=>{if(state.transform.commander_mg)selectEdit('commander_mg');};
  $('cmd-add').onclick=()=>{if(state.transform.commander_mg)return; $('cmd-enabled').checked=true;return $('cmd-enabled').onchange();};
  $('cmd-remove').onclick=()=>{if(!state.transform.commander_mg)return; $('cmd-enabled').checked=false;return $('cmd-enabled').onchange();};
  controls();sync();
  // The weapon-angle view turns the mount about its yaw pivot and the weapon
  // about the pitch frame, within the limits being edited.
  function weaponRig(){const spec=state.transform.commander_mg;if(!spec||!pitchFrame)return null;
    return {key:'commander_mg',group,revision:meshes,parentOf:key=>key==='pitch'?'yaw':null,
      jointOf:object=>object.userData.seat?'yaw':object.userData.mesh?.rig_joint||null,
      joints:[{key:'yaw',kind:'traverse',weapons:[t('MG de comandante')],matrix:new THREE.Matrix4().makeTranslation(...(spec.pivot||[0,0,0])),limits:()=>spec.yaw},
              {key:'pitch',kind:'elevation',weapons:[t('MG de comandante')],matrix:matrix(pitchFrame),limits:()=>spec.pitch}]};}
  return {sync,restore,receiveCheck,controls,weaponRig};
}
