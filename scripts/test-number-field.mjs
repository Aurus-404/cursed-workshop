import assert from 'node:assert/strict';
import {shown} from '../frontend/number-field.js';
// Field reports 12/11: the gizmo left -6.18245068359375 in a 60 px field.
assert.equal(shown(-6.18245068359375),'-6.18');
assert.equal(shown(90.21312312421),'90.21');
assert.equal(shown(0),'0');
assert.equal(shown(-0.004),'0');
assert.equal(shown('12.5'),'12.5');
assert.equal(shown(''),'0');
console.log('number field ok');
