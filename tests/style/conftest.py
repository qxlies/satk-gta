"""Synthetic meshes for satk.style tests (generated here; no game data in the repository)."""

from __future__ import annotations

import math

import numpy as np


def uv_sphere(seg: int = 16, rings: int = 8, r: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """A closed UV sphere with shared vertices: ``(pos (V,3), tris (T,3))``, outward winding."""
    pos = [(0.0, 0.0, r)]
    for i in range(1, rings):
        th = math.pi * i / rings
        for j in range(seg):
            ph = 2 * math.pi * j / seg
            pos.append((r * math.sin(th) * math.cos(ph), r * math.sin(th) * math.sin(ph), r * math.cos(th)))
    pos.append((0.0, 0.0, -r))
    south = len(pos) - 1

    def ring(i: int, j: int) -> int:
        return 1 + (i - 1) * seg + j % seg

    tris = []
    for j in range(seg):
        tris.append((0, ring(1, j), ring(1, j + 1)))
    for i in range(1, rings - 1):
        for j in range(seg):
            a, b, c, d = ring(i, j), ring(i, j + 1), ring(i + 1, j), ring(i + 1, j + 1)
            tris += [(a, c, d), (a, d, b)]
    for j in range(seg):
        tris.append((south, ring(rings - 1, j + 1), ring(rings - 1, j)))
    P, T = np.array(pos), np.array(tris)
    # make the winding outward (cross product along the radius)
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    if (np.einsum("ij,ij->i", np.cross(b - a, c - a), (a + b + c) / 3) < 0).mean() > 0.5:
        T = T[:, [0, 2, 1]]
    return P, T


def face_normals(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Per-corner flat normals ``(T, 3, 3)``."""
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    return np.repeat(n[:, None, :], 3, axis=1)


def cube(half: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """A closed cube with 8 shared vertices and 12 outward triangles."""
    h = half
    P = np.array([(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)], dtype=float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    T = []
    for a, b, c, d in quads:
        T += [(a, b, c), (a, c, d)]
    T = np.array(T)
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    out = np.einsum("ij,ij->i", np.cross(B - A, C - A), (A + B + C) / 3) < 0
    T[out] = T[out][:, [0, 2, 1]]
    return P, T


def split_corners(P: np.ndarray, T: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The same mesh with one vertex per corner (as a DFF with every edge split)."""
    return P[T.reshape(-1)], np.arange(3 * len(T)).reshape(-1, 3)
