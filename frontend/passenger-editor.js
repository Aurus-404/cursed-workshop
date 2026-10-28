import {applyPose} from './pivot-editor.js';
import * as THREE from 'three';
import {t} from './i18n.js';

// Passenger seats: new ones anywhere (each a soldier the player places like a
// smoke launcher), and the vehicle's own ones with another pose or spot.
// No cap: a whole row is one click, so a T-34 full of riders takes seconds.
export function passengerEditor({state,$,groups,scene,job,loading,remember,selectEdit,refreshCheck,reportError,applyShow,makeSeat,reposeSeat,active=()=>state.edit==='passenger_seats'}){
  const group=groups.passenger_seats=new THREE.Group();scene.add(group);
  let hosts=[],native=[],poses=null,selected='',revision=0,opened=false;
  const spec=()=>state.transform.passenger_seats||{};
  const rows=()=>spec().added||[];
  const changes=()=>spec().native||{};
  const ensureSpec=()=>{state.transform.passenger_seats??={};state.transform.passenger_seats.added??=[];state.transform.passenger_seats.native??={};return state.transform.passenger_seats;};
  const index=()=>selected.startsWith('a:')?Number(selected.slice(2)):-1;
  const place=()=>selected.startsWith('n:')?selected.slice(2):null;
  const pose=()=>rows()[index()]||{position:[0,0,0],rotation:[0,0,0]};
  const target=()=>group.children.find(n=>n.userData.seatIndex===index())?.children[0];
  const matrix=value=>new THREE.Matrix4().set(...value.flat());
  const upright=[[1,0,0],[0,1,0],[0,0,1]];
  function tidy(){const value=state.transform.passenger_seats;if(!value)return;
    if(!value.added?.length)delete value.added;if(value.native&&!Object.keys(value.native).length)delete value.native;
    if(!value.added&&!value.native)delete state.transform.passenger_seats;}
  function dispose(node){node.traverse(n=>{if(!n.userData.keepGeometry)n.geometry?.dispose();for(const m of [n.material].flat())if(m&&!m.userData?.shared)m.dispose();});node.removeFromParent();}
  function sync(){for(const host of group.children){const row=rows()[host.userData.seatIndex];if(!row)continue;
    const parent=hosts.find(p=>p.name===row.parent);host.visible=!!parent;if(parent)host.matrix.copy(matrix(parent.matrix));applyPose(host.children[0],row);}
    group.updateMatrixWorld(true);}
  function draw(){for(const n of [...group.children])dispose(n);
    rows().forEach((row,i)=>{const host=new THREE.Group(),fitting=new THREE.Group();host.matrixAutoUpdate=false;host.userData={seatIndex:i};host.add(fitting);
      fitting.add(makeSeat({place:'mw_seat'+(i+1),group:'passenger',position:[0,0,0],rotation:upright,pose:row.pose}));group.add(host);});
    for(const seat of native){const change=changes()[seat.place]||{};reposeSeat(seat.place,change.pose||seat.pose,change.offset||[0,0,0]);}
    sync();applyShow();}
  function controls(){const ready=!!(state.hull&&state.turret),busy=state.busy,isAdded=index()>=0,isNative=!!place();
    $('passenger-load').disabled=busy||!ready;$('passenger-fields').classList.toggle('hidden',!opened||!ready||!poses);
    for(const id of ['passenger-list','passenger-add','passenger-row-add','passenger-row-count','passenger-row-gap','passenger-row-axis'])$(id).disabled=busy||!ready;
    $('passenger-pose').disabled=busy||!(isAdded||isNative);$('passenger-parent').disabled=busy||!isAdded;
    $('passenger-parent-field').classList.toggle('hidden',!isAdded);$('passenger-offset-field').classList.toggle('hidden',!isNative);
    for(const id of ['passenger-duplicate','passenger-remove','passenger-edit'])$(id).disabled=busy||!isAdded;
    $('passenger-reset').disabled=busy||!isNative||!changes()[place()];
  }
  function label(seat){return seat.place+' · '+(changes()[seat.place]?.pose||seat.pose||'?');}
  function render(){const list=$('passenger-list');list.replaceChildren();
    for(const seat of native)list.add(new Option(t('Original')+': '+label(seat)+(changes()[seat.place]?' *':''),'n:'+seat.place));
    rows().forEach((row,i)=>list.add(new Option(t('Nuevo')+' '+(i+1)+': '+row.pose,'a:'+i)));
    if(![...list.options].some(o=>o.value===selected))selected=list.options[0]?.value||'';list.value=selected;
    $('passenger-count').textContent=t('Asientos de pasajeros')+': '+(native.length+rows().length);
    const current=index()>=0?rows()[index()].pose:place()?(changes()[place()]?.pose||native.find(s=>s.place===place())?.pose):'';
    if(current&&[...$('passenger-pose').options].some(o=>o.value===current))$('passenger-pose').value=current;
    const parent=index()>=0?rows()[index()].parent:'';$('passenger-parent').replaceChildren(...hosts.map(h=>new Option(h.name,h.name)));if(parent)$('passenger-parent').value=parent;
    const offset=(place()&&changes()[place()]?.offset)||[0,0,0];for(let i=0;i<3;i++)$('passenger-offset-'+i).value=offset[i];
    controls();}
  async function ensureCatalog(){if(poses)return;poses=(await job('/api/passenger-catalog',{})).poses;
    $('passenger-pose').replaceChildren(...poses.map(p=>new Option(p.id+' ('+p.source+')',p.id)));
    // Riders sitting on the armor, like the game's own tank riders, unless the player picks another.
    if(poses.some(p=>p.id==='pose_seat_armor_1'))$('passenger-pose').value='pose_seat_armor_1';}
  async function restore(){const version=++revision;if(!rows().length&&!Object.keys(changes()).length&&!opened){draw();render();return;}
    try{await ensureCatalog();if(version!==revision)return;opened=true;draw();render();}catch(error){reportError(error);}}
  function defaultSeat(){const fallback=poses.find(p=>p.id==='pose_seat_armor_1')?.id||poses[0].id;
    const parent=hosts.find(h=>h.name==='body')||hosts[0];let position=[0,0,0];
    // On top of the hull's deck, where the game's own riders sit, not above the turret.
    const box=new THREE.Box3();groups.hull.updateWorldMatrix(true,true);box.union(new THREE.Box3().setFromObject(groups.hull));
    if(parent&&!box.isEmpty()){const p=box.getCenter(new THREE.Vector3());p.z=box.max.z;scene.updateWorldMatrix(true,false);position=scene.worldToLocal(p).applyMatrix4(matrix(parent.matrix).invert()).toArray();}
    return {pose:$('passenger-pose').value||fallback,parent:parent?.name,position,rotation:[0,0,0]};}
  function capture(){if(state.edit!=='passenger_seats'||index()<0)return false;const node=target();if(!node)return false;
    const row=rows()[index()];row.position=node.position.toArray().map(v=>Number(v.toFixed(3)));
    const e=new THREE.Euler().setFromQuaternion(node.quaternion,'ZYX');row.rotation=[e.x,e.y,e.z].map(v=>Number(THREE.MathUtils.radToDeg(v).toFixed(3)));return true;}
  const run=fn=>async()=>{if(state.busy)return;try{await fn();}catch(error){reportError(error);}};
  function after(){draw();render();if(index()>=0)selectEdit('passenger_seats');refreshCheck();}
  $('passenger-load').onclick=run(async()=>{await loading('Leer poses de pasajeros',async()=>{await refreshCheck();await ensureCatalog();});opened=true;draw();render();});
  $('passenger-list').onchange=()=>{selected=$('passenger-list').value;render();if(index()>=0)selectEdit('passenger_seats');};
  $('passenger-list').onclick=$('passenger-list').onchange;
  $('passenger-add').onclick=run(async()=>{if(!hosts.length)return;remember();const seat=defaultSeat();ensureSpec().added.push(seat);selected='a:'+(rows().length-1);after();});
  // A row: copies of the selected new seat (or a fresh one) every "gap" units along one axis of its part.
  $('passenger-row-add').onclick=run(async()=>{const count=Math.round(Number($('passenger-row-count').value)),gap=Number($('passenger-row-gap').value),axis=Number($('passenger-row-axis').value);
    if(!(count>=1)||!Number.isFinite(gap)||!hosts.length)return;remember();const added=ensureSpec().added;
    const base=index()>=0?structuredClone(added[index()]):defaultSeat();const start=index()>=0?1:0;
    for(let i=start;i<count+start;i++){const seat=structuredClone(base);seat.position[axis]+=gap*i;added.push(seat);}
    selected='a:'+(added.length-1);after();});
  $('passenger-duplicate').onclick=run(async()=>{if(index()<0)return;remember();const copy=structuredClone(rows()[index()]);copy.position[1]+=20;ensureSpec().added.push(copy);selected='a:'+(rows().length-1);after();});
  $('passenger-remove').onclick=run(async()=>{if(index()<0)return;remember();rows().splice(index(),1);tidy();selected='';after();if(!rows().length)selectEdit('hull');});
  $('passenger-edit').onclick=()=>{if(index()>=0)selectEdit('passenger_seats');};
  $('passenger-pose').onchange=run(async()=>{const value=$('passenger-pose').value;remember();
    if(index()>=0)rows()[index()].pose=value;else if(place()){const seat=native.find(s=>s.place===place());const change=ensureSpec().native[place()]??={};
      if(value===seat?.pose)delete change.pose;else change.pose=value;if(!Object.keys(change).length)delete ensureSpec().native[place()];tidy();}
    after();});
  $('passenger-parent').onchange=run(async()=>{if(index()<0)return;remember();rows()[index()].parent=$('passenger-parent').value;sync();refreshCheck();});
  for(let i=0;i<3;i++)$('passenger-offset-'+i).onchange=run(async()=>{const name=place(),value=Number($('passenger-offset-'+i).value);if(!name||!Number.isFinite(value))return;remember();
    const change=ensureSpec().native[name]??={};change.offset=[...(change.offset||[0,0,0])];change.offset[i]=value;if(change.offset.every(v=>!v))delete change.offset;
    if(!Object.keys(change).length)delete ensureSpec().native[name];tidy();after();});
  $('passenger-reset').onclick=run(async()=>{const name=place();if(!name)return;remember();delete ensureSpec().native[name];tidy();after();});
  function deactivate(){sync();controls();}
  controls();return {sync,restore,deactivate,controls,capture,target,pose,receiveCheck(check){hosts=check.armor_hosts||check.hosts||[];
    native=(check.passengers||[]).filter(s=>!s.added);if(opened){draw();render();if(index()>=0&&active())selectEdit('passenger_seats');}else controls();}};
}
