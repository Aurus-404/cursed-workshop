export function paintUV(plate,points,normal,bounds,tile=20){
  const axis=normal.map(Math.abs).indexOf(Math.max(...normal.map(Math.abs)));
  if(!plate.paint||plate.paint==='steel')return points.map(p=>p.filter((_,i)=>i!==axis).map(v=>v/tile));
  if(plate.shape==='cylinder'&&Math.abs(normal[2])<.5){
    let uv=points.map(p=>[((Math.atan2(p[1],p[0])+Math.PI*2)%(Math.PI*2))/(Math.PI*2),1-(p[2]-bounds[2])/(bounds[5]-bounds[2])]);
    if(Math.max(...uv.map(p=>p[0]))-Math.min(...uv.map(p=>p[0]))>.5)uv=uv.map(([u,v])=>[u<.5?u+1:u,v]);
    return uv;
  }
  const axes=[0,1,2].filter(i=>i!==axis);
  return points.map(p=>[(p[axes[0]]-bounds[axes[0]])/Math.max(bounds[axes[0]+3]-bounds[axes[0]],1e-8),
    1-(p[axes[1]]-bounds[axes[1]])/Math.max(bounds[axes[1]+3]-bounds[axes[1]],1e-8)]);
}
