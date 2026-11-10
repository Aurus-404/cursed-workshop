import {applyPose,poseMatrix,fittingParent} from './pivot-editor.js';
import * as THREE from 'three';
import {t} from './i18n.js';
import {previewMesh} from './model-geometry.js';

const CATEGORY={mantlet:'Manteletes',skirts:'Faldones laterales',shields:'Escudos y chapas',plates:'Placas',
  tracks:'Orugas de repuesto',sandbags:'Sacos de arena',wood:'Madera',other:'Accesorios'};
const category=item=>item.category||(item.kind==='plates'?'plates':item.kind);
const thickest=item=>Math.max(0,...(item.thickness||[]));
// One entry per group of identical pieces (a part repeated on every variant of
// a vehicle), test vehicles and ships left out, the most useful first. Every
// piece stays in the catalog: placed armor from older recipes keeps its name.
export function armorListing(items,{kind='',kinds=null,nation='',order='useful'}={},translate=t){
  // kinds: the checked protection types (a checklist); kind: a single one.
  const wanted=kinds?new Set(kinds):kind?new Set([kind]):null;
  const shown=items.filter(item=>item.listed!==false&&item.primary!==false&&(!wanted||wanted.has(category(item)))&&(!nation||item.nation===nation));
  kind=wanted?.size===1?[...wanted][0]:'';
  const name=item=>item.label.toLocaleLowerCase();
  const sorts={useful:(a,b)=>(b.score??0)-(a.score??0)||name(a).localeCompare(name(b)),
    thick:(a,b)=>thickest(b)-thickest(a)||name(a).localeCompare(name(b)),az:(a,b)=>name(a).localeCompare(name(b))};
  return shown.sort(sorts[order]||sorts.useful).map(item=>({id:item.id,
    label:(kind?'':translate(CATEGORY[category(item)]||'')+' · ')+item.label+' · '+thickest(item)+' mm'
      +((item.variants||1)>1?' · ×'+item.variants+' '+translate('variantes iguales'):'')}));
}

// A plate of the player's own size: drawn here, built by the tool on compile.
// Millimetres; the model unit is 50 mm. As deep (local X) as it is thick.
export const PLATE='custom:plate';
export const PLATE_LIMITS={width:[50,8000],height:[50,8000],thickness:[1,500]};
export const plateSizes=plate=>[plate.thickness/50,plate.width/50,plate.height/50];
export function clampPlate(plate){const result={};
  for(const [key,[low,high]] of Object.entries(PLATE_LIMITS)){const value=Number(plate?.[key]);result[key]=Math.round(Math.min(high,Math.max(low,Number.isFinite(value)?value:low)));}
  return result;}
// Stretching with the handles: each axis scales its own measure.
export const stretchPlate=(plate,scale)=>clampPlate({thickness:plate.thickness*Math.abs(scale[0]),width:plate.width*Math.abs(scale[1]),height:plate.height*Math.abs(scale[2])});
// The box with its paint repeating every `tile` model units, as the built mesh does.
export function plateGeometry(plate,tile=20){const geometry=new THREE.BoxGeometry(...plateSizes(plate)),p=geometry.attributes.position,n=geometry.attributes.normal,uv=geometry.attributes.uv;
  for(let i=0;i<p.count;i++){const at=[p.getX(i),p.getY(i),p.getZ(i)],normal=[n.getX(i),n.getY(i),n.getZ(i)],flat=at.filter((_,axis)=>Math.abs(normal[axis])<.5);uv.setXY(i,flat[0]/tile,flat[1]/tile);}
  uv.needsUpdate=true;return geometry;}
export const plateLabel=(plate,translate=t)=>translate('Placa a medida')+' · '+plate.width+' × '+plate.height+' · '+plate.thickness+' mm';

// Degrees (ZYX, as captured poses) that undo the host bone's turn and restore
// the piece's native one; null when either frame reflects or is unknown.
export function naturalRotation(host,native){
  if(!host?.matrix||!native)return null;
  // Linear parts only. Native skeletons often reflect an axis (basis): both
  // frames may, and their relative turn is then a plain rotation.
  const linear=m=>{const r=new THREE.Matrix4().set(...m.flat());r.setPosition(0,0,0);return r;};
  const local=linear(host.matrix).invert().multiply(linear(native));
  if(!(local.determinant()>0.99&&local.determinant()<1.01))return null;
  const e=new THREE.Euler().setFromRotationMatrix(local,'ZYX');
  return [e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));
}

// Poses are local to actual compiled bones, exactly like commander mounts.
export function armorEditor({state,$,groups,scene,job,loading,remember,selectEdit,refreshCheck,reportError,applyShow,stretchMode=()=>{}}){
  const group=groups.extra_armor=new THREE.Group();scene.add(group);
  let catalog=null,signature='',hosts=[],selected=-1,revision=0,candidate=null,candidateRevision=0;
  const meshes=new Map();
  const rows=()=>state.transform.extra_armor||[];
  const kindBoxes=()=>[...($('armor-kinds')?.querySelectorAll?.('input[type="checkbox"]')||[])];
  const checkedKinds=()=>kindBoxes().length?kindBoxes().filter(box=>box.checked).map(box=>box.value):null;
  // The fitting being chosen is moved before it is added; added fittings stay
  // put (no handles) until their name is picked again in the placed list.
  const pose=()=>candidate||rows()[selected]||{position:[0,0,0],rotation:[0,0,0]};
  const current=()=>candidate||rows()[selected];
  const target=()=>group.children.find(n=>candidate?n.userData.candidate:n.userData.armorIndex===selected)?.children[0];
  const parentName=row=>fittingParent(row,state.check?.commander_bones);
  const matrix=rows=>new THREE.Matrix4().set(...rows.flat());
  function dispose(node){node.traverse(n=>{n.geometry?.dispose();for(const m of new Set([n.userData?.flat,...[n.material].flat()]))if(m&&!m.userData?.shared)m.dispose();});node.removeFromParent();}
  function clear(){for(const n of [...group.children])dispose(n);candidate=null;}
  function sync(){for(const host of group.children){const item=host.userData.candidate?candidate:rows()[host.userData.armorIndex];
    if(!item)continue;const native=hosts.find(h=>h.name===parentName(item));host.visible=!!native;host.userData.hostBone=native?.name;
    if(native)host.matrix.copy(matrix(native.matrix));
    const fitting=host.children[0];applyPose(fitting,item);
  }group.updateMatrixWorld(true);}
  function controls(){
    const ready=!!(state.hull&&state.turret),busy=state.busy;
    $('armor-load').disabled=busy||!ready;$('armor-fields').classList.toggle('hidden',!catalog||!ready);
    for(const id of ['armor-nation','armor-order','armor-asset','armor-parent','armor-list'])$(id).disabled=busy||!ready;
    for(const box of kindBoxes())box.disabled=busy||!ready;
    $('armor-add').disabled=busy||!ready||!candidate||(candidate.asset!==PLATE&&candidate.asset!==$('armor-asset').value);
    const plate=current()?.plate;$('armor-custom').disabled=busy||!ready;
    $('armor-plate').classList.toggle('hidden',!plate);
    for(const key of ['width','height','thickness']){const input=$('armor-plate-'+key);input.disabled=busy||!plate;if(plate&&Number(input.value)!==plate[key])input.value=plate[key];}
    $('armor-plate-stretch').disabled=busy||!plate;
    $('armor-duplicate').disabled=busy||!rows()[selected];
    $('armor-remove').disabled=busy||!rows()[selected];
    $('armor-edit').disabled=busy||!rows()[selected];
  }
  function render(){
    selected=Math.min(selected,rows().length-1);
    $('armor-kinds-count').textContent=kindBoxes().length?kindBoxes().filter(box=>box.checked).length+'/'+kindBoxes().length:'';
    const old=$('armor-asset').value;$('armor-asset').replaceChildren();
    for(const row of armorListing(catalog?.items||[],{kinds:checkedKinds(),nation:$('armor-nation').value,order:$('armor-order').value}))
      $('armor-asset').add(new Option(row.label,row.id));
    if((catalog?.items||[]).some(p=>p.id===old)&&$('armor-asset').options&&[...$('armor-asset').options].some(o=>o.value===old))$('armor-asset').value=old;
    $('armor-list').replaceChildren();rows().forEach((r,i)=>$('armor-list').add(new Option((i+1)+'. '+(r.asset===PLATE?plateLabel(r.plate):catalog?.items.find(p=>p.id===r.asset)?.label||r.asset),String(i))));
    $('armor-list').value=String(selected);if(selected<0)$('armor-list').selectedIndex=-1;
    const parent=parentName(candidate)||parentName(rows()[selected])||$('armor-parent').value;
    $('armor-parent').replaceChildren();for(const h of hosts)$('armor-parent').add(new Option(h.name,h.name));
    if(hosts.some(h=>h.name===parent))$('armor-parent').value=parent;
    const item=current()?.plate?null:catalog?.items.find(p=>p.id===$('armor-asset').value);
    $('armor-info').textContent=current()?.plate?t('Acero. El grosor es también el fondo de la placa: más gruesa, más protege.'):item?[t('Material nativo')+': '+item.materials.join(', '),t('Espesor nativo')+': '+item.thickness.join(' / '),item.cast_steel?t('Conserva el factor nativo de resistencia de la oruga.'):'',
      category(item)==='mantlet'?t('Se fija a la elevación del cañón y sube y baja con él. Para cambiar el mantelete, quitá el original en Cambiar cañón.'):''].filter(Boolean).join(' · '):'';
    controls();
  }
  async function ensureCatalog(){const key=JSON.stringify(state.config?.settings||{});
    if(!catalog||key!==signature){catalog=await job('/api/armor-catalog',{});signature=key;meshes.clear();}render();}
  async function geometry(asset){if(!meshes.has(asset))meshes.set(asset,await job('/api/armor-preview',{asset}));return meshes.get(asset);}
  // The plate's own paint, read once and shared by every plate shown.
  let steel=null,tile=20;
  function paint(ghost,data){
    if(!steel&&data?.texture){steel=new THREE.DataTexture(new Uint8Array(data.texture.rgba),data.texture.width,data.texture.height,THREE.RGBAFormat);
      steel.colorSpace=THREE.SRGBColorSpace;steel.wrapS=steel.wrapT=THREE.RepeatWrapping;steel.needsUpdate=true;tile=data.tile||tile;}
    const material=new THREE.MeshStandardMaterial({color:steel?(ghost?0xb8e6c8:0xffffff):(ghost?0x77bf94:0x4b4947),map:steel,roughness:.85,side:THREE.DoubleSide,wireframe:state.wire,transparent:ghost,opacity:ghost?0.75:1});
    return material;}
  function reshape(){const row=current(),mesh=target()?.children[0];if(!row?.plate||!mesh)return;
    mesh.geometry.dispose();mesh.geometry=plateGeometry(row.plate,tile);}
  async function show(row,index,ghost=false){const host=new THREE.Group(),fitting=new THREE.Group();
    host.matrixAutoUpdate=false;host.userData=ghost?{candidate:true}:{armorIndex:index};host.add(fitting);
    if(row.asset===PLATE){const material=paint(ghost,await geometry(PLATE)),mesh=new THREE.Mesh(plateGeometry(row.plate,tile),material);mesh.userData={mesh:{},flat:material};fitting.add(mesh);return host;}
    const data=await geometry(row.asset);
    for(const mesh of data.meshes){const material=new THREE.MeshStandardMaterial({color:ghost?0x77bf94:0xb69b70,roughness:.85,side:THREE.DoubleSide,wireframe:state.wire,transparent:ghost,opacity:ghost?0.55:1});
      fitting.add(previewMesh(mesh,material));}return host;}
  async function restore(){const version=++revision;clear();
    if(!rows().length){render();return;}
    try{await ensureCatalog();const value=JSON.stringify(rows()),objects=[];
      for(const [i,row] of rows().entries())objects.push(await show(row,i));
      if(version!==revision||value!==JSON.stringify(rows())){objects.forEach(dispose);return;}
      objects.forEach(n=>group.add(n));sync();render();applyShow();
      if(state.edit==='extra_armor')selectEdit('extra_armor');
    }catch(error){if(version===revision)reportError(error);}
  }
  function receiveCheck(check){hosts=check.armor_hosts||check.hosts||[];render();sync();}
  async function choose(custom=false){const session=candidateRevision;const asset=custom===true?PLATE:$('armor-asset').value;
    // A mantlet goes on the vehicle's own gun elevation, so it rises with the gun.
    const elevation=state.check?.gun_elevation,picked=catalog?.items.find(p=>p.id===asset);
    if(picked&&category(picked)==='mantlet'&&hosts.some(h=>h.name===elevation))$('armor-parent').value=elevation;
    const parent=$('armor-parent').value;
    const version=++revision;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;
    controls();
    if(!asset||!parent)return;
    const near=rows()[selected]||rows().at(-1)||{position:[0,0,0],rotation:[0,0,0]};
    let position=[...near.position];
    // A new fitting appears above the vehicle and its placed armor, never on top of a placed one.
    {const box=new THREE.Box3();for(const name of ['hull','turret','extra_armor']){groups[name].updateWorldMatrix(true,true);box.union(new THREE.Box3().setFromObject(groups[name]));}
      const host=hosts.find(h=>h.name===parent);
      if(!box.isEmpty()&&host){const p=box.getCenter(new THREE.Vector3());p.z=box.max.z+3;scene.updateWorldMatrix(true,false);position=scene.worldToLocal(p).applyMatrix4(matrix(host.matrix).invert()).toArray();}}
    // A new piece starts upright, turned as it sat on its own vehicle: the
    // attachment bone's turn and the piece bone's own axes cancel out.
    // A plate starts square to the vehicle, whatever way the attachment bone is turned.
    const data=asset===PLATE?{native_frame:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}:await geometry(asset);if(version!==revision||session!==candidateRevision)return;
    const value={asset,parent,position,rotation:naturalRotation(hosts.find(h=>h.name===parent),data.native_frame)??(asset===PLATE?[0,0,0]:[...near.rotation])};
    if(asset===PLATE)value.plate=clampPlate(rows()[selected]?.plate||{width:1000,height:1000,thickness:30});
    const object=await show(value,0,true);if(version!==revision||session!==candidateRevision){dispose(object);return;}
    candidate=value;selected=-1;group.add(object);sync();render();applyShow();selectEdit('extra_armor');
  }
  function capture(){const row=candidate||rows()[selected];if(state.edit!=='extra_armor'||!row)return false;
    const node=target();if(!node)return false;
    row.position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');row.rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;
  }
  const run=fn=>async()=>{if(state.busy)return;try{await fn();}catch(error){reportError(error);}};
  $('armor-load').onclick=run(async()=>{await loading('Leer blindajes disponibles',ensureCatalog);await refreshCheck();render();await choose();});
  const filtered=run(async()=>{render();await choose();});
  $('armor-kinds').onchange=$('armor-nation').oninput=$('armor-order').oninput=filtered;
  $('armor-asset').onchange=run(()=>choose());
  $('armor-custom').onclick=run(async()=>{if(!catalog)await loading('Leer blindajes disponibles',ensureCatalog);if(!hosts.length)await refreshCheck();render();await choose(true);});
  // Typed measures and the handles change the same plate; the model follows at once.
  let grabbed=null;
  function resized(){reshape();render();if(state.edit==='extra_armor')selectEdit('extra_armor');}
  for(const key of ['width','height','thickness']){const input=$('armor-plate-'+key);
    input.onfocus=()=>{if(!candidate&&current()?.plate)remember();};
    input.oninput=()=>{const row=current(),value=Number(input.value);if(!row?.plate||!Number.isFinite(value)||value<PLATE_LIMITS[key][0]||value>PLATE_LIMITS[key][1])return;
      row.plate={...row.plate,[key]:Math.round(value)};reshape();};
    input.onchange=()=>{const row=current();if(!row?.plate)return;row.plate=clampPlate({...row.plate,[key]:input.value});resized();if(!candidate)refreshCheck();};}
  $('armor-plate-stretch').onclick=()=>{if(current()?.plate){selectEdit('extra_armor');stretchMode();}};
  function grab(){grabbed=current()?.plate?{...current().plate}:null;}
  function stretch(scale){const row=current();if(state.edit!=='extra_armor'||!row?.plate||!grabbed)return false;
    row.plate=stretchPlate(grabbed,scale);reshape();controls();return true;}
  // A click also picks: the list keeps no visible selection while nothing is
  // being moved, so the same name may be clicked again to move it.
  $('armor-list').onchange=$('armor-list').onclick=()=>{if($('armor-list').selectedIndex<0)return;selected=Number($('armor-list').value);for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;render();selectEdit('extra_armor');};
  $('armor-parent').onchange=run(async()=>{const row=candidate||rows()[selected],parent=$('armor-parent').value;if(!row){await choose();return;}
    const old=hosts.find(h=>h.name===parentName(row)),next=hosts.find(h=>h.name===parent);if(!old||!next)return;
    const pose=poseMatrix(row);
    const local=matrix(next.matrix).invert().multiply(matrix(old.matrix)).multiply(pose),p=new THREE.Vector3(),q=new THREE.Quaternion(),s=new THREE.Vector3();local.decompose(p,q,s);
    if(s.toArray().some(v=>Math.abs(v-1)>.002)){render();throw Error(t('Ese punto de fijación cambia la escala o refleja la pieza. Elegí otro.'));}
    if(!candidate)remember();row.parent=parent;const pivot=new THREE.Vector3(...(row.pivot||[0,0,0]));row.position=p.sub(pivot).add(pivot.clone().applyQuaternion(q)).toArray();row.rotation=new THREE.Euler().setFromQuaternion(q,'ZYX').toArray().slice(0,3).map(THREE.MathUtils.radToDeg);
    sync();selectEdit('extra_armor');if(!candidate)refreshCheck();});
  $('armor-add').onclick=run(async()=>{const asset=$('armor-asset').value,parent=$('armor-parent').value;if(!candidate||(candidate.asset!==PLATE&&candidate.asset!==asset)||candidate.parent!==parent)return;
    remember();state.transform.extra_armor??=[];rows().push(structuredClone(candidate));selected=-1;
    await restore();selectEdit('extra_armor');refreshCheck();});
  $('armor-duplicate').onclick=run(async()=>{if(!rows()[selected])return;remember();const copy=structuredClone(rows()[selected]);copy.position[1]+=15;rows().push(copy);selected=rows().length-1;await restore();selectEdit('extra_armor');refreshCheck();});
  $('armor-remove').onclick=run(async()=>{if(!rows()[selected])return;remember();rows().splice(selected,1);selected=-1;await restore();selectEdit(rows().length?'extra_armor':'hull');refreshCheck();});
  $('armor-edit').onclick=()=>selectEdit('extra_armor');
  function deactivate(){candidateRevision++;for(const n of [...group.children])if(n.userData.candidate)dispose(n);candidate=null;sync();controls();}
  controls();return {sync,restore,deactivate,receiveCheck,controls,capture,target,pose,grab,stretch};
}
