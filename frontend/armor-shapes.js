import * as THREE from 'three';
import {paintUV} from './paint-uv.js';

export function plateCorners(plate){
  if(plate.corners)return plate.corners;
  if(plate.shape==='triangle')return [[0,0],[plate.width,0],[plate.width/2,plate.height]];
  if(['circle','cylinder'].includes(plate.shape))return Array.from({length:64},(_,i)=>[plate.width/2+plate.width/2*Math.cos(i*Math.PI/32),plate.width/2+plate.width/2*Math.sin(i*Math.PI/32)]);
  return plate.corners||[[0,0],[plate.width,0],[plate.width,plate.height],[0,plate.height]];
}
export function plateOutline(plate){
  return plateCorners(plate);
}
export function insetPolygon(polygon,distance){
  const lines=polygon.map((a,i)=>{
    const b=polygon[(i+1)%polygon.length],dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);
    return {p:[a[0]-dy/length*distance,a[1]+dx/length*distance],v:[dx,dy]};
  });
  return lines.map((line,i)=>{
    const {p:a,v:u}=lines[(i+lines.length-1)%lines.length],{p:b,v}=line,denominator=u[0]*v[1]-u[1]*v[0];
    const t=((b[0]-a[0])*v[1]-(b[1]-a[1])*v[0])/denominator;
    return [a[0]+u[0]*t,a[1]+u[1]*t];
  });
}
export function validCorners(corners){
  return corners.every((b,i)=>{
    const a=corners[(i+corners.length-1)%corners.length],c=corners[(i+1)%corners.length];
    return b.every(n=>Number.isFinite(n)&&Math.abs(n)<=16000)&&(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])>.0001;
  });
}
export function validShape(plate){
  const polygon=plateOutline(plate);
  if(!validCorners(polygon))return false;
  if(plate.shape==='cylinder'){
    if(plate.thickness*2>=plate.width)return false;
    if(!plate.corners)return true;
    return insetPolygon(polygon,plate.thickness).every(p=>p.every(Number.isFinite)&&polygon.every((a,i)=>{
      const b=polygon[(i+1)%polygon.length];return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])>0;
    }));
  }
  if(!plate.hole)return true;
  const {x,y,diameter}=plate.hole;
  return [x,y,diameter].every(Number.isFinite)&&diameter>=10&&polygon.every((a,i)=>{
    const b=polygon[(i+1)%polygon.length];
    return ((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]))/Math.hypot(b[0]-a[0],b[1]-a[1])>diameter/2+.1;
  });
}
export function armorSections(plate){
  if(plate.shape==='cylinder'){
    if(plate.corners){
      const outer=plateOutline(plate),inner=insetPolygon(outer,plate.thickness),center=plate.width/2;
      return outer.map((_,i)=>[inner[i],outer[i],outer[(i+1)%outer.length],inner[(i+1)%outer.length]].map(p=>[p[0]-center,p[1]-center]));
    }
    const radius=plate.width/2,inner=radius-plate.thickness;
    return Array.from({length:64},(_,i)=>{
      const a=i*Math.PI/32,b=(i+1)*Math.PI/32;
      return [[inner,a],[radius,a],[radius,b],[inner,b]].map(([r,t])=>[r*Math.cos(t),r*Math.sin(t)]);
    });
  }
  const polygon=plateOutline(plate);
  if(!plate.hole)return [polygon];
  const {x:cx,y:cy,diameter}=plate.hole,radius=diameter/2;
  const angles=[...new Set([...Array.from({length:64},(_,i)=>i*Math.PI/32),...polygon.map(([x,y])=>(Math.atan2(y-cy,x-cx)+Math.PI*2)%(Math.PI*2))].map(a=>Number(a.toFixed(10))))].sort((a,b)=>a-b);
  const inner=[],outer=[];
  for(const angle of angles){
    const dx=Math.cos(angle),dy=Math.sin(angle),distances=[];
    polygon.forEach((a,i)=>{
      const b=polygon[(i+1)%polygon.length],ex=b[0]-a[0],ey=b[1]-a[1],denominator=dx*ey-dy*ex;
      if(Math.abs(denominator)<1e-10)return;
      const distance=((a[0]-cx)*ey-(a[1]-cy)*ex)/denominator,along=((a[0]-cx)*dy-(a[1]-cy)*dx)/denominator;
      if(distance>0&&along>=-1e-8&&along<=1+1e-8)distances.push(distance);
    });
    const reach=Math.min(...distances);
    inner.push([cx+radius*dx,cy+radius*dy]);outer.push([cx+reach*dx,cy+reach*dy]);
  }
  return angles.map((_,i)=>[inner[i],outer[i],outer[(i+1)%angles.length],inner[(i+1)%angles.length]]);
}
export function armorSurface(plate){
  const faces=new Map();
  for(const polygon of armorSections(plate)){
    const cylinder=plate.shape==='cylinder',depth=cylinder?plate.height:plate.thickness,n=polygon.length;
    const points=[-1,1].flatMap(side=>polygon.map(([x,y])=>cylinder?[x/50,y/50,side*depth/100]:[side*depth/100,(x-plate.width/2)/50,(y-plate.height/2)/50]));
    const boundary=[Array.from({length:n},(_,i)=>n-1-i),Array.from({length:n},(_,i)=>n+i)];
    for(let i=0;i<n;i++)boundary.push([i,(i+1)%n,(i+1)%n+n,i+n]);
    for(const face of boundary){
      const coordinates=face.map(i=>points[i]);
      const key=JSON.stringify(coordinates.map(p=>p.map(v=>Number(v.toFixed(8)))).sort((a,b)=>a[0]-b[0]||a[1]-b[1]||a[2]-b[2]));
      if(faces.has(key))faces.delete(key);else faces.set(key,coordinates);
    }
  }
  const points=[],triangles=[];
  for(const face of faces.values()){
    const base=points.length;points.push(...face);
    for(let i=1;i<face.length-1;i++)triangles.push([base,base+i,base+i+1]);
  }
  return {points,triangles};
}
export function shapedGeometry(plate,tile=20){
  const positions=[],uv=[],normals=[],{points,triangles}=armorSurface(plate);
  const bounds=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))).concat([0,1,2].map(i=>Math.max(...points.map(p=>p[i]))));
  for(const triangle of triangles){
      const [a,b,c]=triangle.map(i=>new THREE.Vector3(...points[i])),normal=b.clone().sub(a).cross(c.clone().sub(a)).normalize();
      const mapped=paintUV(plate,triangle.map(i=>points[i]),normal.toArray(),bounds,tile);
      for(const [at,i] of triangle.entries()){
        const p=points[i];positions.push(...p);uv.push(...mapped[at]);
        if(plate.shape==='cylinder'&&!plate.corners&&Math.abs(normal.z)<.5){
          const radius=Math.hypot(p[0],p[1]),sign=normal.x*p[0]+normal.y*p[1]>0?1:-1;
          normals.push(sign*p[0]/radius,sign*p[1]/radius,0);
        }else normals.push(...normal.toArray());
      }
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));
  geometry.setAttribute('normal',new THREE.Float32BufferAttribute(normals,3));
  return geometry;
}
