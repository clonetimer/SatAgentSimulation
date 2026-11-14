"""Compare the production JS orientation against official Basilisk DCMs."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE_QUERY = r"""
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = vm.createContext({
  THREE: { MathUtils: { degToRad: d => d*Math.PI/180, radToDeg: r => r*180/Math.PI } },
  window: {}, document: { addEventListener() {} }
});
vm.runInContext(source.replace(/^import \* as THREE from 'three';\s*/, ''), context);
const samples = JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(samples.map(({q,epoch,t}) =>
  context.window.SatScene.attitudeToGlobeQuaternion(q,epoch,t))));
"""


def _active_matrix(q):
    w, x, y, z = q
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)],
        [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)],
        [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)],
    ])


def test_globe_body_axes_match_official_basilisk_passive_dcm_transpose():
    pytest.importorskip('Basilisk')
    from Basilisk.utilities import RigidBodyKinematics as rbk

    node = shutil.which('node')
    assert node, 'Node is required for the cross-runtime physical-frame gate'
    tangent = math.tan(math.pi / 8)
    sigmas = [[0., 0., 0.], [.1, -.2, .05], [1., 0., 0.], [2., -.5, .25]]
    for axis in range(3):
        for sign in (-1, 1):
            sigma = [0., 0., 0.]
            sigma[axis] = sign * tangent
            sigmas.append(sigma)
    quaternions = [np.asarray(rbk.MRP2EP(sigma), dtype=float) for sigma in sigmas]
    quaternions += [-q for q in quaternions]
    samples = [{'q': q.tolist(), 'epoch': '2000-01-01T12:00:00Z', 't': 0.} for q in quaternions]
    result = subprocess.run(
        [node, '-e', NODE_QUERY, str(ROOT / 'src/sat_sim/web/satellite-scene.js')],
        input=json.dumps(samples), text=True, capture_output=True, check=True, timeout=20,
    )
    actual = json.loads(result.stdout)
    assert len(actual) == len(quaternions)
    theta = math.radians(280.46061837)  # GMST at J2000, independent fixed reference.
    c, s = math.cos(theta), math.sin(theta)
    c_en = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])
    c_ge = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]])
    for q_bn, q_globe in zip(quaternions, actual, strict=True):
        expected = c_ge @ c_en @ np.asarray(rbk.EP2C(q_bn)).T
        assert q_globe is not None
        np.testing.assert_allclose(_active_matrix(q_globe), expected, atol=1e-12, rtol=0)
