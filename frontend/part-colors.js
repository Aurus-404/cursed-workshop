// Colors of the plain (untextured) 3D view, one per kind of part.
export const PART_KINDS=['chassis','turret','weapon','tracks','armor','accessory','crew','passenger'];
export const PART_KIND_LABELS={chassis:'Chasis',turret:'Torreta',weapon:'Armas',tracks:'Orugas y ruedas',armor:'Blindaje adicional',accessory:'Accesorios',crew:'Tripulación',passenger:'Pasajeros'};
export const DEFAULT_PART_COLORS={chassis:'#7b9dab',turret:'#c5a36b',weapon:'#c0605a',tracks:'#4f5458',armor:'#8a6fb0',accessory:'#5fa8a0',crew:'#6fc3df',passenger:'#9ccf7a'};
// Off: the classic two colors, chassis and turret, for every piece.
export const DEFAULT_VIEW_COLORS={by_kind:true,colors:{...DEFAULT_PART_COLORS}};
const HEX=/^#[0-9a-f]{6}$/i;

export function viewColors(value){
  const saved=value&&typeof value==='object'?value:{},colors={};
  for(const kind of PART_KINDS){const color=saved.colors?.[kind];colors[kind]=typeof color==='string'&&HEX.test(color)?color.toLowerCase():DEFAULT_PART_COLORS[kind];}
  return {by_kind:saved.by_kind!==false,colors};
}

// Groups other editors add to the scene, by what they hold.
const GROUP_KINDS={hull:'chassis',turret:'turret',commander_mg:'weapon',rider_mgs:'weapon',mounted_weapons:'weapon',extra_armor:'armor',smoke_launchers:'accessory',passenger_seats:'passenger'};
const TRACKS=/track|wheel|roller|sprocket|idler|susp|caterp|gus[lr]?\d*$|^w\d/i;
const WEAPON=/gun|barrel|cannon|breech|muzzle|mortar|rocket|launch|shooter|weapon|mantlet|flame|^mg|mg\d*$|mg_/i;

export function partKind(part,mesh={}){
  let base=GROUP_KINDS[part]||'accessory';
  if(base!=='chassis'&&base!=='turret')return base;
  // A whole vehicle's own turret (Edit) is drawn with the chassis group.
  if(mesh.turret_branch)base='turret';
  if(mesh.attached_weapon)return 'weapon';
  const name=[mesh.bone,mesh.name].filter(Boolean).join(' ');
  if(TRACKS.test(name))return 'tracks';
  if(WEAPON.test(name))return 'weapon';
  return base;
}

// The color a piece is drawn with when textures are off.
export function plainColor(part,mesh,settings){
  if(!settings.by_kind)return partKind(part,{turret_branch:mesh?.turret_branch})==='chassis'?settings.colors.chassis:settings.colors.turret;
  return settings.colors[partKind(part,mesh)];
}
