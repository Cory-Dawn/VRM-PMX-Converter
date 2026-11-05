# -*- coding: utf-8 -*-
"""VRM 0.x SpringBone reference (port of UniVRM VRMSpringBoneLogic).

Runs the VRM spring bones on a rigid whole-model test motion (still -> fast
turn -> hold -> turn back -> smooth sway -> still) and reports where each PMX
rigid body would be.  Used by the universal physics preset to give every chain
the amount of sway it has in VRM, and by dev/physbench for comparisons.
"""
from __future__ import annotations

import numpy as np

SECONDS = 12.0
VRM_HZ = 60.0
MOTION_WINDOW = (3.0, 10.0)   # seconds with motion, used for sway statistics


def _ss(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _rot(yaw, pitch):
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return Ry @ Rx


def motion_at(t: float, pivot: np.ndarray):
    """Rigid transform of the whole model at time t: (R, translation)."""
    D = np.pi / 180.0
    yaw = pitch = tx = 0.0
    if 3.0 <= t < 4.5:
        yaw = 70 * D * _ss((t - 3.0) / 0.25)
    elif 4.5 <= t < 6.0:
        yaw = 70 * D * (1 - _ss((t - 4.5) / 0.25))
    elif 6.0 <= t < 10.0:
        u = t - 6.0
        env = _ss(u / 0.5) * _ss((4.0 - u) / 0.5)
        yaw = env * 25 * D * np.sin(2 * np.pi * 0.8 * u)
        pitch = env * 10 * D * np.sin(2 * np.pi * 1.1 * u)
        tx = env * 2.0 * np.sin(2 * np.pi * 0.5 * u)
    R = _rot(yaw, pitch)
    pv = np.array([0.0, pivot[1], 0.0])
    tr = pv + np.array([tx, 0, 0]) - R @ pv
    return R, tr


def _from_to(a, b):
    """Rotation matrix taking direction a to direction b (Quaternion.FromToRotation)."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -0.999999:
        axis = np.cross(a, [1.0, 0, 0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def simulate(vrm, node_rot, bodies, bone_to_node, scale, pivot, fps,
             seconds=SECONDS, motion=None, follow_mode=0):
    """UniVRM 0.x VRMSpringBone on a rigid whole-model motion.

    Returns {body index: [frames, 3]} positions (PMX units, model frame) of
    the point each dynamic rigid body sits on, sampled at `fps`.
    """
    motion = motion or motion_at
    v, S = vrm, scale
    parents = {}
    for i, nd in enumerate(v.nodes):
        for c in nd.get("children", []):
            parents[c] = i
    wpos = lambda n: v.world[n][:3, 3].astype(np.float64)  # posed, mirrored, metres  # noqa: E731

    # colliders (metres, rest frame) - same placement as PhysicsBuilder
    cols = []
    for cg in v.collider_groups:
        if cg.node is None or cg.node < 0:
            continue
        M = v.world[cg.node]
        Rp = node_rot.get(cg.node)
        for c in cg.colliders:
            off = M[:3, :3] @ c.offset
            if Rp is not None:
                off = Rp @ off
            cols.append((M[:3, 3] + off, c.radius))
    cpos0 = np.array([c[0] for c in cols]) if cols else np.zeros((0, 3))
    crad = np.array([c[1] for c in cols]) if cols else np.zeros(0)

    # verlet logic list in UniVRM setup order (pre-order per root)
    logic = []   # dict(node, parent, rest_dir(world), length, group)
    seen = set()
    for gi, g in enumerate(v.spring_groups):
        for root in g.bones:
            stack = [root]
            while stack:
                n = stack.pop()
                if n in seen:
                    continue
                seen.add(n)
                ch = v.nodes[n].get("children", [])
                head = wpos(n)
                if ch:
                    tail = wpos(ch[0])
                else:
                    p = parents.get(n)
                    d = head - wpos(p) if p is not None else np.array([0, -1.0, 0])
                    tail = head + d / max(np.linalg.norm(d), 1e-9) * 0.07
                logic.append(dict(node=n, gi=gi, head0=head, tail0=tail,
                                  length=float(np.linalg.norm(tail - head))))
                stack.extend(reversed(ch))
    idx_of = {L["node"]: i for i, L in enumerate(logic)}

    dt = 1.0 / VRM_HZ
    n_steps = int(round(seconds * VRM_HZ))
    # per node current world rotation delta D (relative to rest), world head/tail (metres)
    cur = np.array([L["tail0"] for L in logic])
    prev = cur.copy()

    # bodies -> (logic index, offset from head in rest, metres)
    targets = {}
    for bi, b in enumerate(bodies):
        if b.mode == follow_mode:
            continue
        node = bone_to_node.get(b.bone)
        if node in idx_of:
            targets[bi] = (idx_of[node], np.asarray(b.position) / S - logic[idx_of[node]]["head0"])
    out = {bi: np.zeros((n_steps, 3)) for bi in targets}

    pivot_m = np.asarray(pivot, dtype=np.float64) / S
    for k in range(n_steps):
        R, tr = motion((k + 1) * dt, pivot_m * S)
        tr = tr / S
        Dn = {}      # node -> world delta rotation this step
        heads = {}
        for i, L in enumerate(logic):
            n = L["node"]
            p = parents.get(n)
            if p in Dn:
                Dp = Dn[p]
                head = heads[p] + Dp @ (L["head0"] - logic[idx_of[p]]["head0"])
            else:
                Dp = R
                head = R @ L["head0"] + tr
            g = v.spring_groups[L["gi"]]
            rest_dir = Dp @ (L["tail0"] - L["head0"]) / max(L["length"], 1e-9)
            nxt = (cur[i] + (cur[i] - prev[i]) * (1.0 - g.drag_force)
                   + rest_dir * g.stiffiness * dt
                   + g.gravity_dir * g.gravity_power * dt)
            d = nxt - head
            nxt = head + d / max(np.linalg.norm(d), 1e-12) * L["length"]
            if len(crad):
                cp = (R @ cpos0.T).T + tr
                rr = crad + g.hit_radius
                dv = nxt - cp
                dist = np.linalg.norm(dv, axis=1)
                for ci in np.nonzero(dist < rr)[0]:
                    nrm = dv[ci] / max(dist[ci], 1e-12)
                    nxt = cp[ci] + nrm * rr[ci]
                    d = nxt - head
                    nxt = head + d / max(np.linalg.norm(d), 1e-12) * L["length"]
            prev[i] = cur[i]
            cur[i] = nxt
            Dn[n] = _from_to(Dp @ (L["tail0"] - L["head0"]), nxt - head) @ Dp
            heads[n] = head
        Ri = R.T
        for bi, (li, off) in targets.items():
            n = logic[li]["node"]
            pw = heads[n] + Dn[n] @ off
            out[bi][k] = Ri @ (pw - tr) * S
    # resample to fps
    t_v = (np.arange(n_steps) + 1) / VRM_HZ
    n_f = int(round(seconds * fps))
    t_f = (np.arange(n_f) + 1) / fps
    res = {}
    for bi, arr in out.items():
        res[bi] = np.stack([np.interp(t_f, t_v, arr[:, c]) for c in range(3)], 1)
    return res


def tip_sway(vrm, node_rot, bodies, bone_to_node, scale, pivot, fps=30, follow_mode=0):
    """Mean distance (PMX units) of every dynamic body from its rest position during the motion."""
    traj = simulate(vrm, node_rot, bodies, bone_to_node, scale, pivot, fps, follow_mode=follow_mode)
    t = (np.arange(int(round(SECONDS * fps))) + 1) / fps
    win = (t >= MOTION_WINDOW[0]) & (t < MOTION_WINDOW[1])
    out = {}
    for bi, x in traj.items():
        rest = np.asarray(bodies[bi].position, dtype=np.float64)
        out[bi] = float(np.linalg.norm(x[win] - rest, axis=1).mean())
    return out
