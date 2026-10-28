import assert from 'node:assert/strict';
import {panelWidths,scrollThumb} from '../frontend/workspace-layout.js';

for(const priority of ['library','inspector']){
  for(const width of [910,1024,1280,1920]){
    const result=panelWidths(width,9999,9999,priority);
    assert.ok(result.left>=248&&result.left<=520);
    assert.ok(result.right>=230&&result.right<=460);
    assert.ok(width-result.left-result.right>=360,'the viewport must stay usable after resizing');
  }
}
assert.deepEqual(panelWidths(1440,350,310),{left:350,right:310});
assert.deepEqual(panelWidths(1440,-100,-100),{left:248,right:230});
for(const length of [800,80000]){
  const middle=scrollThumb(400,length,394,(length-400)/2);
  assert.equal(middle.height,20,'the handle must not grow with the visible fraction');
  assert.equal(middle.percent,50);
  assert.equal(middle.top,middle.travel/2);
  const end=scrollThumb(400,length,394,Number.MAX_SAFE_INTEGER);
  assert.equal(end.top,end.travel);assert.equal(end.percent,100);
}
assert.equal(scrollThumb(400,300,394,0).visible,false);
assert.equal(scrollThumb(400,800,394,-20).top,0);
assert.equal(scrollThumb(0,800,0,0).height,0);
console.log('Workspace layout: resize bounds, minimum viewport, fixed handle size, scroll mapping and empty lists passed.');
