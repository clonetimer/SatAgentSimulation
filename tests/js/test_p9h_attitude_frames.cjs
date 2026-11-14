'use strict';
// Independent basis-vector oracle: never call the production conversion to
// manufacture the expected orientation. J2000 GMST is a fixed reference.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/satellite-scene.js'), 'utf8');
const context = vm.createContext({
  THREE: { MathUtils: { degToRad: d => d * Math.PI / 180, radToDeg: r => r * 180 / Math.PI } },
  window: {}, document: { addEventListener() {} }
});
vm.runInContext(source.replace(/^import \* as THREE from 'three';\s*/, ''), context);
const convert = context.window.SatScene.attitudeToGlobeQuaternion;
const epoch = '2000-01-01T12:00:00Z';
const theta = 280.46061837 * Math.PI / 180;
const c = Math.cos(theta), s = Math.sin(theta);
const eToG = [[0,1,0],[0,0,1],[1,0,0]];
const nToE = [[c,s,0],[-s,c,0],[0,0,1]];
function multiply(a,b) {
  return a.map(row => b[0].map((_,j) => row.reduce((sum,v,k) => sum + v*b[k][j],0)));
}
function hamiltonMatrix(q) {
  const [w,x,y,z] = q;
  return [[1-2*(y*y+z*z),2*(x*y-w*z),2*(x*z+w*y)],
    [2*(x*y+w*z),1-2*(x*x+z*z),2*(y*z-w*x)],
    [2*(x*z-w*y),2*(y*z+w*x),1-2*(x*x+y*y)]];
}
function assertMatrix(actual,expected,label) {
  actual.forEach((row,i) => row.forEach((v,j) => assert.ok(
    Math.abs(v-expected[i][j]) < 1e-10, `${label} matrix[${i}][${j}]: ${v} != ${expected[i][j]}`)));
}
const root = Math.SQRT1_2;
const cases = [
  ['identity', [1,0,0,0], [[1,0,0],[0,1,0],[0,0,1]]],
  ['body +X 90deg', [root,root,0,0], [[1,0,0],[0,0,-1],[0,1,0]]],
  ['body -X 90deg', [root,-root,0,0], [[1,0,0],[0,0,1],[0,-1,0]]],
  ['body +Y 90deg', [root,0,root,0], [[0,0,1],[0,1,0],[-1,0,0]]],
  ['body -Y 90deg', [root,0,-root,0], [[0,0,-1],[0,1,0],[1,0,0]]],
  ['body +Z 90deg', [root,0,0,root], [[0,-1,0],[1,0,0],[0,0,1]]],
  ['body -Z 90deg', [root,0,0,-root], [[0,1,0],[-1,0,0],[0,0,1]]],
];
for (const [name,q,bodyToN] of cases) {
  test(`Basilisk body basis maps correctly to Globe: ${name}`, () => {
    const result = convert(q,epoch,0);
    assert.ok(result);
    assertMatrix(hamiltonMatrix(result), multiply(multiply(eToG,nToE),bodyToN),name);
    assertMatrix(hamiltonMatrix(convert(q.map(v=>-v),epoch,0)),hamiltonMatrix(result),'q/-q');
  });
}
