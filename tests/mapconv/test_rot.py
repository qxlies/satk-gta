"""Rotation math of satk.mapconv.rot: Euler <-> matrix <-> quaternion <-> IPL (M2-04 acceptance 3)."""

from __future__ import annotations

import math
import random

import pytest

from satk.formats.ipl import rz_deg, world_quat
from satk.index.api import rotate
from satk.mapconv import rot as R

S = math.sqrt(0.5)


def _close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _mat_close(a, b, tol=1e-9):
    return all(_close(u, v, tol) for u, v in zip(a, b))


def _q_close(a, b, tol=1e-9):
    return _close(a, b, tol) or _close(a, [-c for c in b], tol)


# ---------------------------------------------------------------------------- fixed angles 0 / 90 / 180

@pytest.mark.parametrize("euler,quat", [
    ((0, 0, 0), (0, 0, 0, 1)),
    ((90, 0, 0), (S, 0, 0, S)),
    ((0, 90, 0), (0, S, 0, S)),
    ((0, 0, 90), (0, 0, S, S)),
    ((180, 0, 0), (1, 0, 0, 0)),
    ((0, 180, 0), (0, 1, 0, 0)),
    ((0, 0, 180), (0, 0, 1, 0)),
    ((0, 0, -90), (0, 0, -S, S)),
])
def test_euler_quat_at_right_angles(euler, quat):
    assert _q_close(R.euler_quat(*euler), quat)
    assert R.same_rotation(R.quat_euler(quat), euler, 1e-6)


@pytest.mark.parametrize("euler,expect", [
    ((0, 0, 0), (0, 0, 0)), ((90, 0, 0), (90, 0, 0)), ((0, 90, 0), (0, 90, 0)), ((0, 0, 90), (0, 0, 90)),
    ((0, 0, 180), (0, 0, 180)), ((0, 180, 0), (0, 180, 0)), ((-45, 30, 270), (-45, 30, -90)),
])
def test_quat_euler_returns_the_same_angles_in_the_canonical_range(euler, expect):
    assert _close(R.quat_euler(R.euler_quat(*euler)), expect, 1e-7)


def test_180_x_decomposes_to_an_equivalent_triple():
    back = R.quat_euler(R.euler_quat(180, 0, 0))
    assert R.same_rotation(back, (180, 0, 0), 1e-6)
    assert _close(back, (0, 180, 180), 1e-7)  # rx is kept in [-90, 90]


def test_rotations_by_hand():
    # Rz(90) turns +X into +Y; Rx(90) turns +Y into +Z; Ry(90) turns +Z into +X (right-handed)
    right, forward, up = R.euler_matrix(0, 0, 90)
    assert _close(right, (0, 1, 0)) and _close(forward, (-1, 0, 0))
    right, forward, up = R.euler_matrix(90, 0, 0)
    assert _close(forward, (0, 0, 1))
    right, forward, up = R.euler_matrix(0, 90, 0)
    assert _close(up, (1, 0, 0))
    # SetRotate composes R = Rz * Rx * Ry
    m = R.euler_matrix(30, 40, 50)
    rz, rx, ry = R.euler_matrix(0, 0, 50), R.euler_matrix(30, 0, 0), R.euler_matrix(0, 40, 0)

    def mul(a, b):  # columns
        def ap(m, v):
            return tuple(m[0][i] * v[0] + m[1][i] * v[1] + m[2][i] * v[2] for i in range(3))
        return tuple(ap(a, ap(b, col)) for col in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
    assert _mat_close(m, mul(rz, mul(rx, ry)), 1e-12)


# ---------------------------------------------------------------------------- arbitrary angles

def _angles(n=500, seed=4):
    rnd = random.Random(seed)
    for _ in range(n):
        yield (rnd.uniform(-180, 180), rnd.uniform(-180, 180), rnd.uniform(-180, 180))


@pytest.mark.parametrize("e", list(_angles(60)))
def test_quat_matrix_equals_set_rotate(e):
    assert _mat_close(R.quat_matrix(R.euler_quat(*e)), R.euler_matrix(*e), 1e-12)
    assert _q_close(R.matrix_quat(R.euler_matrix(*e)), R.euler_quat(*e), 1e-12)


def test_round_trip_arbitrary_angles():
    worst = 0.0
    for e in _angles():
        back = R.quat_euler(R.euler_quat(*e))
        worst = max(worst, R.rotation_delta_deg(back, e))
        if abs(e[0]) < 89.0:  # inside the canonical range the angles come back themselves
            assert _close(back, [R.norm_angle(a) for a in e], 1e-7), (e, back)
        assert -90.0 <= back[0] <= 90.0
    assert worst < 1e-6


def test_quaternion_rotates_vectors_like_the_matrix():
    for e in _angles(50):
        q = R.euler_quat(*e)
        right, forward, up = R.euler_matrix(*e)
        assert _close(rotate(q, (1, 0, 0)), right, 1e-12)
        assert _close(rotate(q, (0, 1, 0)), forward, 1e-12)
        assert _close(rotate(q, (0, 0, 1)), up, 1e-12)


def test_gimbal_lock():
    for e in ((90, 0, 30), (90, 20, 10), (-90, 15, 40)):
        back = R.quat_euler(R.euler_quat(*e))
        assert back[1] == 0.0
        assert R.same_rotation(back, e, 1e-5)


def test_norm_angle():
    assert R.norm_angle(180) == 180 and R.norm_angle(-180) == 180 and R.norm_angle(540) == 180
    assert R.norm_angle(-0.0) == 0.0 and str(R.norm_angle(-0.0)) == "0.0"
    assert R.norm_angle(270) == -90 and R.norm_angle(-270) == 90


# ---------------------------------------------------------------------------- IPL

def test_ipl_quat_is_the_conjugate_and_the_engine_reads_it_back():
    for e in _angles(100):
        qf = R.ipl_quat(*e)
        assert _q_close(world_quat(qf), R.euler_quat(*e), 1e-12)  # formats.ipl.world_quat = the loader's negation
        if not R.ipl_heading_only(qf):
            back, dropped = R.ipl_euler(qf)
            assert not dropped and R.same_rotation(back, e, 1e-6)


@pytest.mark.parametrize("rz", [0, 30, 90, 179, 180, -90, 270, 359.5])
def test_heading_only_matches_the_engine_rule(rz):
    qf = R.ipl_quat(0, 0, rz)
    assert R.ipl_heading_only(qf)
    (x, y, z), dropped = R.ipl_euler(qf)
    assert (x, y, dropped) == (0.0, 0.0, False)
    assert abs(R.norm_angle(z - rz)) < 1e-9
    assert abs(R.norm_angle(z - rz_deg(qf))) < 1e-9  # same as satk.formats.ipl.rz_deg (the loader formula)


def test_small_tilt_is_dropped_by_the_engine():
    qf = R.ipl_quat(3.0, -2.0, 120.0)  # |qx|,|qy| < 0.05
    assert R.ipl_heading_only(qf)
    (x, y, z), dropped = R.ipl_euler(qf)
    assert dropped and x == 0.0 and y == 0.0
    assert R.ipl_heading_only(qf, iflags=0) and not R.ipl_heading_only(qf, iflags=R.DONT_STREAM)
    big = R.ipl_quat(10.0, 0.0, 120.0)  # sin(5 deg) > 0.05: full rotation
    assert not R.ipl_heading_only(big)
    assert R.same_rotation(R.ipl_euler(big)[0], (10, 0, 120), 1e-6)


def test_engine_rule_threshold():
    t = math.degrees(2 * math.asin(R.TILT_EPS))  # 5.73 deg
    assert R.ipl_heading_only(R.ipl_quat(t - 0.01, 0, 0))
    assert not R.ipl_heading_only(R.ipl_quat(t + 0.01, 0, 0))
