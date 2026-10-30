"""Compiled finite-Earth quadrature, identical nodes and physical equations.

No fastmath, parallel reduction, reduced precision or lower quadrature order.
The public thermal_environment API validates inputs before entering this kernel.
"""
import math
import numpy as np
from numba import njit


@njit(cache=True, fastmath=False, parallel=False, nogil=True)
def integrate_earth_disk(position, sun, normals, radius, ir, albedo, solar_reference,
                         au, radial_nodes, radial_weights, cos_phi, sin_phi):
    distance = math.sqrt(np.dot(position, position))
    centre = -position/distance
    axis = 0
    if abs(centre[1]) < abs(centre[axis]):
        axis = 1
    if abs(centre[2]) < abs(centre[axis]):
        axis = 2
    basis = np.zeros(3)
    basis[axis] = 1.
    tangent_x = np.cross(centre, basis)
    tangent_x /= math.sqrt(np.dot(tangent_x, tangent_x))
    tangent_y = np.cross(centre, tangent_x)
    mu_min = math.sqrt(max(0., 1.-(radius/distance)**2))
    views = np.zeros(len(normals))
    reflected = np.zeros(len(normals))
    for i in range(len(radial_nodes)):
        mu = mu_min+(radial_nodes[i]+1.)*((1.-mu_min)/2.)
        weight = radial_weights[i]*((1.-mu_min)/2.)*(2.*math.pi/len(cos_phi))
        tangent_scale = math.sqrt(max(0., 1.-mu*mu))
        ray_distance = distance*(mu-math.sqrt(max(0., mu*mu-mu_min*mu_min)))
        for j in range(len(cos_phi)):
            dx = mu*centre[0]+tangent_scale*(cos_phi[j]*tangent_x[0]+sin_phi[j]*tangent_y[0])
            dy = mu*centre[1]+tangent_scale*(cos_phi[j]*tangent_x[1]+sin_phi[j]*tangent_y[1])
            dz = mu*centre[2]+tangent_scale*(cos_phi[j]*tangent_x[2]+sin_phi[j]*tangent_y[2])
            px = position[0]+ray_distance*dx
            py = position[1]+ray_distance*dy
            pz = position[2]+ray_distance*dz
            plen = math.sqrt(px*px+py*py+pz*pz)
            sx, sy, sz = sun[0]-px, sun[1]-py, sun[2]-pz
            slen = math.sqrt(sx*sx+sy*sy+sz*sz)
            illumination = max(0., (px*sx+py*sy+pz*sz)/(plen*slen))
            radiance = albedo*solar_reference*(au/slen)**2*illumination/math.pi
            for n in range(len(normals)):
                reception = max(0., normals[n, 0]*dx+normals[n, 1]*dy+normals[n, 2]*dz)
                views[n] += reception*weight/math.pi
                reflected[n] += reception*weight*radiance
    return reflected, ir*views, views


def prepare_earth_disk_kernel():
    """Compile the validated runtime signature during preparation, without physics."""
    from numba import types
    vector = types.Array(types.float64, 1, 'C')
    normals = types.Array(types.float64, 2, 'C')
    quadrature = types.Array(types.float64, 1, 'C', readonly=True)
    integrate_earth_disk.compile((vector, vector, normals,
        types.float64, types.float64, types.float64, types.float64, types.float64,
        quadrature, quadrature, quadrature, quadrature))
