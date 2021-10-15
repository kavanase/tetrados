import logging
from typing import List, Tuple

import numpy as np
from pymatgen.core.structure import Structure
from pymatgen.io.ase import AseAtomsAdaptor
from spglib import spglib

from tetrados.settings import ktol, symprec

logger = logging.getLogger(__name__)


def kpoints_to_first_bz(
    kpoints: np.ndarray, tol=ktol, negative_zone_boundary: bool = True
) -> np.ndarray:
    """Translate fractional k-points to the first Brillouin zone.

    I.e. all k-points will have fractional coordinates:
        -0.5 <= fractional coordinates < 0.5

    Args:
        kpoints: The k-points in fractional coordinates.
        tol: Fractional tolerance for evaluating zone boundary points.
        negative_zone_boundary: Whether to use -0.5 (spglib convention) or
            0.5 (VASP convention) for zone boundary points.

    Returns:
        The translated k-points.
    """
    kp = kpoints - np.round(kpoints)

    # account for small rounding errors for 0.5
    round_dp = int(np.log10(1 / tol))
    krounded = np.round(kp, round_dp)

    if negative_zone_boundary:
        kp[krounded == 0.5] = -0.5
    else:
        kp[krounded == -0.5] = 0.5
    return kp


def get_kpoints_tetrahedral(
    kpoint_mesh: List[int],
    structure: Structure,
    symprec: float = symprec,
    time_reversal_symmetry: bool = True,
) -> Tuple[np.ndarray, ...]:
    """Gets the symmetry inequivalent k-points from a k-point mesh.

    Follows the same process as SpacegroupAnalyzer.get_ir_reciprocal_mesh
    but is faster and allows returning of the full k-point mesh and mapping.

    Args:
        kpoint_mesh: The k-point mesh as a 1x3 array. E.g.,``[6, 6, 6]``.
        structure: A structure.
        symprec: Symmetry tolerance used when determining the symmetry
            inequivalent k-points on which to interpolate.
        time_reversal_symmetry: Whether the system has time reversal symmetry.

    Returns:
        The irreducible k-points and their weights as tuple, formatted as::

            (ir_kpoints, weights)

        If return_full_kpoints, the data will be returned as::

            (ir_kpoints, weights, kpoints, ir_kpoints_idx, ir_to_full_idx)

        Where ``ir_kpoints_idx`` is the index of the unique irreducible k-points
        in ``kpoints``. ``ir_to_full_idx`` is a list of indices that can be
        used to construct the full Brillouin zone from the ir_mesh. Note the
        ir -> full conversion will only work with calculated scalar properties
        such as energy (not vector properties such as velocity).
    """
    from tetrados.tetrahedron import get_tetrahedra

    atoms = AseAtomsAdaptor().get_atoms(structure)

    if not symprec:
        symprec = 1e-8

    grid_mapping, grid_address = spglib.get_ir_reciprocal_mesh(
        kpoint_mesh, atoms, symprec=symprec, is_time_reversal=time_reversal_symmetry
    )
    full_kpoints = grid_address / kpoint_mesh

    tetra, ir_tetrahedra_idx, ir_tetrahedra_to_full_idx, tet_weights = get_tetrahedra(
        structure.lattice.reciprocal_lattice.matrix,
        grid_address,
        kpoint_mesh,
        grid_mapping,
    )

    ir_kpoints_idx, ir_to_full_idx, weights = np.unique(
        grid_mapping, return_inverse=True, return_counts=True
    )
    ir_kpoints = full_kpoints[ir_kpoints_idx]

    return (
        ir_kpoints,
        weights,
        full_kpoints,
        ir_kpoints_idx,
        ir_to_full_idx,
        tetra,
        ir_tetrahedra_idx,
        ir_tetrahedra_to_full_idx,
        tet_weights,
    )


def get_mesh_from_kpoint_diff(kpoints, tol=5e-4):
    kpoints = np.array(kpoints)

    # whether the k-point mesh is shifted or Gamma centered mesh
    is_shifted = np.min(np.linalg.norm(kpoints, axis=1)) > 1e-6

    unique_a = np.unique(kpoints[:, 0])
    unique_b = np.unique(kpoints[:, 1])
    unique_c = np.unique(kpoints[:, 2])

    if len(unique_a) == 1:
        na = 1
    else:
        # filter very small changes, with a tol of 5e-4 this means k-point meshes
        # denser than 2000x2000x2000 will be treated as numerical noise. Meshes
        # this dense are extremely unlikely
        diff = np.diff(unique_a)
        diff = diff[diff > ktol]
        na = 1 / np.min(diff[diff > ktol])

    if len(unique_b) == 1:
        nb = 1
    else:
        diff = np.diff(unique_b)
        nb = 1 / np.min(diff[diff > ktol])

    if len(unique_c) == 1:
        nc = 1
    else:
        diff = np.diff(unique_c)
        nc = 1 / np.min(diff[diff > ktol])

    # due to limited precission of the input k-points, the mesh is returned as a float
    return np.array([na, nb, nc]), is_shifted


def get_kpoint_indices(kpoints, mesh, is_shifted=False):
    mesh = np.array(mesh)
    shift = np.array([1, 1, 1]) if is_shifted else np.array([0, 0, 0])
    min_kpoint = -np.floor(mesh / 2).round().astype(int)
    addresses = ((kpoints + shift / (mesh * 2)) * mesh).round().astype(int)
    shifted = addresses - min_kpoint
    nyz = mesh[1] * mesh[2]
    nz = mesh[2]
    indices = shifted[:, 0] * nyz + shifted[:, 1] * nz + shifted[:, 2]
    return indices.round().astype(int)


def get_kpoint_mapping(kpoints_true, kpoints_sort):
    assert len(kpoints_sort) == len(kpoints_sort), "number of k-points must be the same"

    kpoints_true = kpoints_to_first_bz(kpoints_true)
    kpoints_sort = kpoints_to_first_bz(kpoints_sort)

    mesh_true = get_mesh_from_kpoint_diff(kpoints_true)[0].round(0).astype(int)
    mesh_sort = get_mesh_from_kpoint_diff(kpoints_sort)[0].round(0).astype(int)

    assert tuple(mesh_true) == tuple(
        mesh_sort
    ), "k-points have different mesh dimensions"

    indices_true = get_kpoint_indices(kpoints_true, mesh_true)
    indices_sort = get_kpoint_indices(kpoints_sort, mesh_sort)

    sort_true_idx = np.arange(len(kpoints_true), dtype=int)[np.argsort(indices_true)]
    sort_sort_idx = np.argsort(indices_sort)

    mapping = sort_sort_idx[sort_true_idx]

    diff = np.linalg.norm(kpoints_true - kpoints_sort[mapping], axis=1)
    if np.any(diff > 1e-5):
        raise ValueError("Something went wrong with mapping.")

    return mapping


def sort_kpoints(kpoints: np.ndarray):
    k_round = np.array(kpoints).round(5)
    sort_idx = np.lexsort((k_round[:, 2], k_round[:, 1], k_round[:, 0]))
    return kpoints[sort_idx]


def get_kpoints_from_bandstructure(bandstructure, cartesian=False, sort=False):
    if cartesian:
        kpoints = np.array([k.cart_coords for k in bandstructure.kpoints])
    else:
        kpoints = np.array([k.frac_coords for k in bandstructure.kpoints])

    if sort:
        return sort_kpoints(kpoints)

    return kpoints
