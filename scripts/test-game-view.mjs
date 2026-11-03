import assert from 'node:assert/strict';
import * as THREE from 'three';
import {gameCamera,gameRenderer,gameOrbit} from '../frontend/game-view.js';

// Gates of Hell's frame is left-handed (+X nose, +Y right, +Z up): seen from
// behind, the right side must appear on the screen's right, never mirrored.
const handedness=camera=>{camera.updateMatrixWorld(true);
  const at=v=>new THREE.Vector3(...v).project(camera),o=at([0,0,0]),x=at([1,0,0]).sub(o),y=at([0,1,0]).sub(o),z=at([0,0,1]).sub(o);
  return new THREE.Matrix3().set(x.x,y.x,z.x,x.y,y.y,z.y,x.z,y.z,z.z).determinant();};
const place=camera=>{camera.up.set(0,0,1);camera.position.set(-200,0,40);camera.lookAt(0,0,20);return camera;};
const plain=place(new THREE.PerspectiveCamera(40,1.5,.1,1000));
const game=place(gameCamera(new THREE.PerspectiveCamera(40,1.5,.1,1000)));
assert.ok(handedness(plain)<0&&handedness(game)>0,'the view draws the game frame without mirroring it');
const right=new THREE.Vector3(0,30,20).project(game),left=new THREE.Vector3(0,-30,20).project(game);
assert.ok(right.x>left.x,'from behind, the vehicle right side is on the screen right');
game.aspect=.5;game.updateProjectionMatrix();assert.ok(handedness(game)>0,'resizing keeps the game frame');
assert.equal(gameCamera(game),game);game.updateProjectionMatrix();assert.ok(handedness(game)>0,'never mirrored twice');
// Picking still finds the point drawn under the cursor.
const target=new THREE.Vector3(10,25,22),ndc=target.clone().project(game),ray=new THREE.Raycaster();
ray.setFromCamera(new THREE.Vector2(ndc.x,ndc.y),game);assert.ok(ray.ray.distanceToPoint(target)<1e-6,'a click hits what is drawn there');
// A mirrored screen reverses winding, so one-sided materials flip their front face.
const calls=[],renderer={state:{setMaterial:(material,cw)=>calls.push(cw)}};
gameRenderer(renderer);gameRenderer(renderer);renderer.state.setMaterial({},false);renderer.state.setMaterial({},true);
assert.deepEqual(calls,[true,false]);
// Dragging sideways turns and pans the way the cursor goes.
const seen=[],orbit={_rotateLeft:a=>seen.push(['rotate',a]),_panLeft:(d,m)=>seen.push(['pan',d,m])};
gameOrbit(orbit);orbit._rotateLeft(.2);orbit._panLeft(3,'matrix');
assert.deepEqual(seen,[['rotate',-.2],['pan',-3,'matrix']]);
console.log('Game view: left-handed frame drawn unmirrored, resize, picking, winding and orbit direction passed.');
