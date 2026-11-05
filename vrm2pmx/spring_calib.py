# -*- coding: utf-8 -*-
"""Numerical bridge between VRM spring-bone behaviour and MMD joint springs.

The VRM reference implementation is a verlet point-mass on a stick:

    inertia   = (tail - prev) * (1 - dragForce)
    stiffness = dt * restDir * stiffnessForce
    external  = dt * gravityDir * gravityPower
    next      = constrain_length(tail + inertia + stiffness + external)

There is no closed form for the resulting period, so we run the simulation
and measure it. The measured angular frequency then gives the MMD joint
spring constant through K = I * omega^2, where I is the real moment of
inertia of the MMD rigid body. Doing this per bone keeps the mapping honest
across bones of very different length.
"""
from __future__ import annotations

import math

DT = 1.0 / 60.0
_CACHE: dict[tuple, float] = {}


def _simulate(stiffness: float, drag: float, length_m: float, gravity: float,
              steps: int = 420, init_deg: float = 25.0):
    rest_x, rest_y = 0.0, -1.0
    th = math.radians(init_deg)
    tx, ty = math.sin(th) * length_m, -math.cos(th) * length_m
    px, py = tx, ty
    angles = []
    for _ in range(steps):
        ix = (tx - px) * (1.0 - drag)
        iy = (ty - py) * (1.0 - drag)
        sx = DT * stiffness * rest_x
        sy = DT * stiffness * rest_y
        ey = DT * (-1.0) * gravity
        nx = tx + ix + sx
        ny = ty + iy + sy + ey
        n = math.hypot(nx, ny)
        if n < 1e-12:
            break
        nx = nx / n * length_m
        ny = ny / n * length_m
        px, py = tx, ty
        tx, ty = nx, ny
        vx, vy = tx / length_m, ty / length_m
        angles.append(math.acos(max(-1.0, min(1.0, vx * rest_x + vy * rest_y))))
    return angles


def _measure(angles: list[float]):
    peaks = []
    for i in range(1, len(angles) - 1):
        if angles[i] > angles[i - 1] and angles[i] > angles[i + 1] and angles[i] > 1e-5:
            peaks.append((i, angles[i]))
    if len(peaks) < 2:
        return None, None
    period = (peaks[-1][0] - peaks[0][0]) * DT / (len(peaks) - 1)
    a0, a1 = peaks[0][1], peaks[1][1]
    if a0 <= 1e-9 or a1 <= 1e-9 or period <= 1e-6:
        return period, 0.15
    delta = math.log(a0 / a1)
    zeta = delta / math.sqrt(4 * math.pi * math.pi + delta * delta)
    return period, min(max(zeta, 0.01), 0.99)


def angular_frequency(stiffness: float, drag: float, length_m: float, gravity: float) -> float:
    """Measured omega (rad/s) of the VRM spring for this bone."""
    key = (round(stiffness, 4), round(drag, 4), round(length_m, 5), round(gravity, 4))
    if key in _CACHE:
        return _CACHE[key]
    angles = _simulate(stiffness, drag, length_m, gravity)
    period, _ = _measure(angles)
    if period and period > 1e-4:
        w = 2.0 * math.pi / period
    else:
        # over-damped / no oscillation: fall back to the analytic stiffness term
        w = math.sqrt(max(stiffness, 1e-4) * 144.0 / max(length_m, 1e-5))
    _CACHE[key] = w
    return w


def damping_ratio(stiffness: float, drag: float, length_m: float, gravity: float) -> float:
    angles = _simulate(stiffness, drag, length_m, gravity)
    _, zeta = _measure(angles)
    return zeta if zeta is not None else 0.15
