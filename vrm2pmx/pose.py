# -*- coding: utf-8 -*-
"""Bake the arms down into an A-pose.

The reference models do this: their X range is smaller than VRM×14 but Z is
identical, which means the arm chain was rotated about Z only. Measured
angle in the references is 30.3°, so 30° is the default here.

Because PMX stores a single bind pose, both the bone positions and the mesh
vertices have to move. Vertices are deformed with linear blend skinning using
the same weights the renderer would use, so the shoulder blends smoothly.
"""
from __future__ import annotations

import numpy as np

DEFAULT_ANGLE_DEG = 30.0


def _rot_z(theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


class PoseBaker:
    def __init__(self, vrm, angle_deg: float = DEFAULT_ANGLE_DEG):
        self.vrm = vrm
        self.angle = np.radians(angle_deg)
        self.applied = False
        # node index -> world-space rotation applied by the A-pose bake.
        # Physics colliders attach to these nodes, so their local offsets must
        # be rotated by the same matrix; otherwise the arm colliders stay in
        # T-pose while the mesh and bones move down.
        self.node_rot: dict[int, np.ndarray] = {}
        # per mesh index -> (n,3,3) effective rotation, used to rotate morph deltas
        self.vertex_rot: dict[int, np.ndarray] = {}

    def apply(self) -> bool:
        v = self.vrm
        if abs(self.angle) < 1e-6:
            return False

        parents: dict[int, int] = {}
        for i, nd in enumerate(v.nodes):
            for c in nd.get("children", []):
                parents[c] = i

        def subtree(root: int) -> set[int]:
            out, stack = set(), [root]
            while stack:
                n = stack.pop()
                if n in out:
                    continue
                out.add(n)
                stack.extend(v.nodes[n].get("children", []))
            return out

        # bone index -> 3x3 rotation + pivot, in world space
        bone_rot: dict[int, np.ndarray] = {}
        bone_pivot: dict[int, np.ndarray] = {}
        node_pivot: dict[int, np.ndarray] = {}

        for prefix, sign in (("left", -1.0), ("right", 1.0)):
            raw = v.humanoid.get(prefix + "UpperArm")
            if raw is None:
                continue
            pivot = v.node_world_pos(raw)
            rot = _rot_z(self.angle * sign)
            for n in subtree(raw):
                node_pivot[n] = pivot
                bone_rot[n] = rot
                self.node_rot[n] = rot

        if not bone_rot:
            return False

        # ---- move the bone world positions
        for n, rot in bone_rot.items():
            p = v.world[n][:3, 3]
            v.world[n][:3, 3] = rot @ (p - node_pivot[n]) + node_pivot[n]

        # ---- deform vertices with LBS
        for mesh in v.meshes:
            T = np.zeros((mesh.count, 3, 3))
            t = np.zeros((mesh.count, 3))
            moved = np.zeros(mesh.count, dtype=bool)
            for k in range(4):
                w = mesh.weights[:, k]
                mask = w > 1e-6
                if not mask.any():
                    continue
                joints = mesh.joints[:, k]
                local_rot = np.zeros((mesh.count, 3, 3))
                local_t = np.zeros((mesh.count, 3))
                for j in np.unique(joints[mask]):
                    sel = mask & (joints == j)
                    if j in bone_rot:
                        R = bone_rot[j]
                        p = node_pivot[j]
                        local_rot[sel] = R
                        local_t[sel] = p - R @ p
                        moved |= sel
                    else:
                        local_rot[sel] = np.eye(3)
                        local_t[sel] = 0.0
                T += w[:, None, None] * local_rot
                t += w[:, None] * local_t
            if not moved.any():
                continue
            idx = np.where(moved)[0]
            p = mesh.positions[idx]
            newp = np.einsum("nij,nj->ni", T[idx], p) + t[idx]
            mesh.positions[idx] = newp
            # normals use the rotation part only
            nn = np.einsum("nij,nj->ni", T[idx], mesh.normals[idx])
            ln = np.linalg.norm(nn, axis=1, keepdims=True)
            mesh.normals[idx] = nn / np.maximum(ln, 1e-12)

            R = np.tile(np.eye(3), (mesh.count, 1, 1))
            R[idx] = T[idx]
            self.vertex_rot[mesh.index] = R

        self.applied = True
        return True
