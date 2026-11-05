# -*- coding: utf-8 -*-
"""VRM SpringBone → MMD rigid bodies + 6DOF spring joints.

Two different physics models have to be bridged here:

  VRM   : verlet-integrated point mass on a stick, per-joint stiffness /
          dragForce / gravityPower, spherical colliders.
  MMD   : Bullet rigid bodies connected by 6DOF spring constraints, 16
          collision groups plus a mask.

The mapping below keeps the *shape* of the VRM behaviour (which chain swings,
how much it damps, which bones collide with what) and converts stiffness into
a joint spring constant scaled by the body's moment of inertia, so long bones
do not end up stiffer than short ones.

The reference models could not be used as a source of truth for physics:
their mass values contain 2^n overflow garbage (3.4e9) and every joint has
zero spring and zero limits, i.e. the physics in them is effectively dead.
"""
from __future__ import annotations

import math

import numpy as np

from .pmx import PMXRigidBody, PMXJoint
from .spring_calib import angular_frequency

SHAPE_SPHERE = 0
SHAPE_BOX = 1
SHAPE_CAPSULE = 2

MODE_FOLLOW = 0
MODE_PHYSICS = 1
MODE_PHYSICS_BONE = 2

GROUP_COLLIDER = 1
GROUP_SPRING = 0
# PMX stores an ALLOWED-COLLISION mask: bit i = 1 means "collide with group i".
# It is commonly mislabelled as a "non-collision mask", but mmd_tools imports
# it as `mask & (1 << i) == 0` -> disabled, i.e. 0 means ignore. The previous
# values here (0x0001 for springs) therefore did the exact opposite of what we
# wanted: springs collided only with their own group and ignored the body.
MASK_COLLIDE_ALL = 0xFFFF            # collide with every group
MASK_COLLIDE_NONE = 0x0000           # collide with nothing
MASK_COLLIDE_ALL_BUT_SELF = 0xFFFE   # ignore group 0, collide with 1..15

# Final preset safeguards for hair trees with many branches/segments. A plain
# "total mass 0.13 per connected component" rule would drive a 23-body tree's
# tips down to 0.0003, where Bullet becomes unstable.
FINAL_LONG_BASE_TOTAL = 0.13
FINAL_LONG_REF_SEGMENTS = 4.0
FINAL_MIN_BODY_MASS = 0.005

# PMX/Bullet joint springs are much weaker per unit than the VRM verlet
# point-mass stiffness.  This gain was calibrated against the observed MMD
# settling time; it is applied only by the universal preset.
UNIVERSAL_SPRING_GAIN = 50.0
UNIVERSAL_MAX_K = 200.0

# Universal v6 (see _apply_universal_v6). Derived with offline benchmark, an
# offline Bullet harness that reproduces the v5 30 FPS flicker and the final
# preset's single-chain twitch, and compares every body against a VRM
# SpringBone reference running the same motion.
#
# Findings that shaped v6:
#  * MMD/PE ship Bullet <= 2.81, which damps velocity AFTER adding gravity, so
#    damping = 1 cancels gravity but also wipes all momentum every step.
#  * In that regime the joint springs barely move the bodies; what you see is
#    the constraint error correction.  A strong spring (v5: K/I ~ 2e4) only
#    overshoots each step, i.e. flickers.  Locked joints give the smoothest
#    result (high-frequency jitter at VRM level, no flicker) at any FPS.
#  * Momentum (damping < 1) is needed for a springy VRM-like rebound, but then
#    MMD's global gravity makes strands sag; that mode needs scene gravity off.
#    Its springs must use the body's OWN inertia: Bullet solves each joint row
#    against the two adjacent bodies only, so a K sized for the whole subtree
#    overshoots at chain roots.
V6_MASS = 0.1                 # tip body mass; only mass ratios matter
V6_MASS_RATIO = 3.0           # locked mode: mass x ratio per segment above the tip
                              # (heavy root, light tip - measured: less lag and
                              # overshoot after fast turns than uniform mass)
V6_SOFT_DAMPING = 0.99        # soft mode: keeps momentum (needs MMD gravity off)
V6_SOFT_OMEGA_DT_MAX = 0.6    # soft mode: omega*dt cap at 30 FPS (flicker from ~1.4)
V6_SOFT_LIMIT_DEG = 45.0      # soft mode: rotation limit (safety net only)
V6_REF_FPS = 30.0

# Universal v7 (default "universal").  From two MMD 9.26 calibration rounds
# (offline calibration, calibration notes):
#  * MMD physics runs on a fixed internal step, independent of the output
#    frame rate; damping < 1 is unusable (chains get pinned at their limits).
#  * At damping 1 a joint spring moves the joint by K*dt^2/I per step, where I
#    is its own body's inertia; with dt = 1/60 the result is clean at a gain of
#    ~0.06, jitters slightly at 0.16 (the final preset's single chains) and
#    clearly from ~0.3.  Springs strong enough to pull a long chain back
#    always jitter; locked long chains are the only clean ones.
V7_MASS_TIP = 0.1             # tip mass of every chain
V7_MASS_RATIO = 1.4           # x per segment towards the root (final's tail gradient; x3 felt hard)
V7_SINGLE_GAIN = 0.05         # single-body chains: K*dt^2/I at dt = 1/60 (clean below ~0.06)
V7_SINGLE_LIMIT_DEG = 30.0
V7_MMD_DT = 1.0 / 60.0

# Universal v8 (default "universal"): v7 plus per-chain sway matched to VRM.
# User check of v7 in MMD: tail too stiff, 4-segment front hair (TouFa6) twice
# too floppy, ears barely move - exactly the ranking of the VRM SpringBone
# reference (vrm2pmx/spring_ref.py).  In a locked chain the visible sway is the
# solver lag, which grows with segment count and length and shrinks with body
# radius and with the root->tip mass ratio.  Fitted on 140 chains x 5 ratios
# (model A, model B; MMD-calibrated harness), rms error 0.26 in log:
#   log sway = c0 + c1 lr + c2 n + c3 log r + c4 log L + c5 lr n
#   (lr = log mass ratio, n = segments, r = body radius, L = chain length)
V8_SWAY_FIT = [-6.1793, -0.3228, 0.309, -2.0816, 2.5306, -0.1416]
V8_TARGET_SCALE = 1.0         # harness sway per unit of VRM sway, with bodies on the joints
                              # (mid placement: 1.15 too soft, 0.85 / 0.65 still returned too slowly in MMD)
V8_RATIO_RANGE = (1.3, 4.0)   # tip never heavier than root; >= 1.3 so long chains return in reasonable time
V8_PLACEMENT = "joint"        # locked multi-body chains: bodies on their joints ("joint", like final)
                              # or half-way along the bone ("mid", v7/v8 up to a63042c).  MMD real-tail
                              # tests: mid placement + thick bodies flickers more (rows couple)
# Locked chains with bodies on their joints sway much less, and the sway falls
# off with the mass ratio roughly linearly in log, faster for more segments.
# Fitted on 110 chains x 5 ratios (model A, model B), rms error 0.38 in log:
#   log sway = c0 + c1 (ratio-1) n + c2 n + c3 log r + c4 log L
V8_SWAY_FIT_JOINT = [-9.1494, -0.2591, 0.7302, -2.5959, 2.7136]
V8_FORCE_RATIO = None         # diagnostics / fitting: use this mass ratio for every locked chain
V8_SOFTEST_LOCKED_RATIO = 0.7 # "can this chain sway enough while locked?" is judged at this ratio
V8_MIN_SEGMENTS = 3           # 1-2 segment chains keep the v7 rules
V10_DEFAULT_SOFTNESS = 0.75   # user pick from the MMD renders (1.0 = v9 hardness, 0.55 too stiff)
# Thick short chains (ears): locked like everything else, with a smaller physics
# radius so the solver lag gives them their VRM sway.  Harness (MMD joint model):
# locked sway ~ radius^-1.75; ears at radius 0.4 sway like VRM with 1-2 jumps,
# the v9/v10 root spring (+-8 deg) gave the same sway with 55-74 jumps.
V10_SOFT_AS_LOCKED = True
V10_SOFT_REF_RADIUS = 0.4     # locked thick-short chains sway ~0.6 at this radius
V10_SOFT_REF_SWAY = 0.6
V10_SOFT_RADIUS_EXP = 1.75
V8_SOFT_ROOT_ONLY = True      # soft chains: only the root joint swings, the rest stays locked
                              # (every joint at its limit made the model A ears twitch in MMD)
V8_SOFT_LIMIT_DEG = 8.0       # chains that cannot reach their sway locked (thick and short, e.g. ears)
# v11: the tail "twitches" were not solver noise but a Bullet 6DOF corner case.
# A locked axis (lower == upper limit) is a bilateral row only while the
# measured angle / offset differs from the limit; when it is EXACTLY equal,
# btRotationalLimitMotor::testLimitValue reports "no violation" and the axis
# gets no row at all for that substep.  With lower = upper = 0 this happens all
# the time in single precision (a joint whose two bodies have the same
# orientation gives an exact 0), and in a bent chain the freed joint snaps open
# by up to ~12 deg in one substep - the visible twitch.  Instrumented harness
# (offline simulator ROWLOG): every tail kink lined up with such a step.
# Locking at a tiny non-zero value keeps the row active (lower == upper is
# still the bilateral case); smaller is better because an exact hit on the
# value itself gets rarer: 1e-7 -> 437 free axis-steps, 1e-9 -> 27, 1e-12 -> 0
# (model A, 12 s).  Sway is unchanged.
V11_LOCK_EPS = 1e-12
# v12: hardness per body region, from the user's check of the v11 MMD renders
# (f920eb4): hair 2x harder, ears 1.5x harder, tail fine, single-bone strands
# too floaty and flickering.  "x harder" = the chain's sway divided by x.
# Harness sweep of the per-segment mass ratio q (bodies on joints, locked):
# for 3-4 segment chains sway ~ q^-1.1 (model A 1.0-1.3, model B 0.9-1.2), so a
# hardness H multiplies q by H^(1/1.1).  Whiskers already sit at q = 4, hence
# the higher cap for these chains.
V12_HEAD_HARDNESS = 2.0       # multi-segment chains hanging from the head (hair, whiskers)
V12_RATIO_EXP = 1.1
V12_HEAD_RATIO_MAX = 8.0
# Thick short chains (ears, "soft" above): sway ~ radius^-1.4 (model A) to ^-1.75
# (model B) around the v10 radius; 1.55 splits the difference.
V12_SOFT_HARDNESS = 1.5
V12_SOFT_RADIUS_EXP = 1.55
# Single-bone chains (root = tip): the clean v7 spring still swung 4x the VRM
# sway and flickered (high-frequency share ~12 % of the sway); +-10 deg limits
# halved the sway but doubled the flicker.  Locked like every other chain they
# keep ~0.17x the VRM sway, like the 2-segment chains, and stop flickering.
V12_LOCK_SINGLES = False      # v12 (2f93aba): locked looked completely static in MMD
# v13: single-bone chains swing again, without the flicker.  Harness (model B,
# 27 strands; angle of the strand vs its rest, 30 fps): the v7 spring swung
# 33 deg on average with 53-58 back-and-forth frames, almost all from hitting
# the +-30 deg limits (no limit: ~10, like the multi-bone hair tips).  With a
# wide limit that is never reached, a stiffer spring and a larger physics
# sphere (these bodies do not collide, the radius only sets the inertia):
# 13-15 deg on average, 8-9 back-and-forth frames, high-frequency part 1.1 deg
# instead of 2.6 (model A the same).
V13_SINGLE_GAIN = 0.2
V13_SINGLE_RADIUS_SCALE = 3.0
V13_SINGLE_LIMIT_DEG = 80.0
# v14: under a real dance (CH4NGE_Light.vmd, first quarter, head turning up to
# ~400 deg/s; offline motion evaluation) the v13 strands still flickered: 88 / 55
# out-and-back jumps > 3 deg (model B / model A, median per strand; final's +-20 deg
# springs: 268 / 274; multi-bone hair tips 98 / 90).  A larger sphere smooths
# the drag far more than a stiffer spring, and a softer spring gives the swing
# back: radius x6 with gain 0.08 swings like v13 in physturn (mean 14.9 / 14.4
# deg vs 14.8 / 13.0) and jumps 13 / 8 times in the dance.
V13_SINGLE_GAIN = 0.08
V13_SINGLE_RADIUS_SCALE = 6.0
# Single-bone root chains in the final preset need a small spring, otherwise
# damping=1 freezes them completely.
SINGLE_CHAIN_PERIOD = 0.28
SINGLE_CHAIN_ANG_DAMP = 1.0
SINGLE_CHAIN_MIN_MASS = 0.5

SPRING_BASE = 500.0             # joint spring constant per unit inertia
ROT_LIMIT = np.radians(45.0)

# Angular damping per chain depth, read straight out of the shipped
# a converted model (VRM2PMX output). Values approach 1.0 near the root.
ANG_DAMP_TABLE = [1.0, 0.92, 0.856, 0.8048, 0.7638, 0.7311]


def _quat_to_euler_xyz(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    rx = np.arctan2(sinr, cosr)
    sinp = 2.0 * (w * y - z * x)
    ry = np.arcsin(np.clip(sinp, -1.0, 1.0))
    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    rz = np.arctan2(siny, cosy)
    return np.array([rx, ry, rz])


def align_y_euler(direction: np.ndarray) -> np.ndarray:
    d = np.asarray(direction, dtype=np.float64)
    n = np.linalg.norm(d)
    if n < 1e-9:
        return np.zeros(3)
    d = d / n
    y = np.array([0.0, 1.0, 0.0])
    c = float(np.dot(y, d))
    if c > 0.999999:
        return np.zeros(3)
    if c < -0.999999:
        return np.array([np.pi, 0.0, 0.0])
    axis = np.cross(y, d)
    axis /= np.linalg.norm(axis)
    angle = float(np.arccos(np.clip(c, -1.0, 1.0)))
    q = np.concatenate([axis * np.sin(angle / 2.0), [np.cos(angle / 2.0)]])
    return _quat_to_euler_xyz(q)


class PhysicsBuilder:
    def __init__(self, vrm, skel, scale: float, strength: float = 1.0,
                 preset: str = "default", pose_rot: dict[int, np.ndarray] | None = None,
                 softness: float | None = None):
        self.vrm = vrm
        softness = V10_DEFAULT_SOFTNESS if softness is None else softness
        # universal preset: multiplier on every chain's target sway (1.0 = v9 hardness).
        # Up to v10 a locked chain's jitter looked proportional to its sway (~0.7 %);
        # v11 traced it to exactly-zero lock limits (V11_LOCK_EPS), so this is now
        # only the hardness knob.
        self.softness = float(np.clip(softness, 0.2, 2.0))
        self.skel = skel
        self.scale = scale
        self.strength = max(0.05, float(strength))
        self.preset = preset or "default"
        # "final" is the shipped preset. It reuses the zero-rotation baseline
        # and then applies the parameters validated in the Z20 test build.
        # "final_lockfix" was the MMD A/B preset for the lock fix (f920eb4); since
        # 2026-11-02 "final" includes the fix, so it is just another name for final
        if self.preset == "final_lockfix":
            self.preset = "final"
        # diagnostic: final with the v14 single-bone strands (MMD A/B before adopting it)
        self.final_v14_singles = self.preset == "final_v14singles"
        if self.final_v14_singles:
            self.preset = "final"
        self.requested_preset = self.preset
        if self.preset in ("final", "universal", "universal_nocol", "universal_v7", "universal_v6",
                           "universal_soft", "universal_v5"):
            self.preset = "zero"
        # node -> A-pose world rotation. Colliders are children of these nodes,
        # so their local offsets need the same rotation as the bones/mesh.
        self.pose_rot = pose_rot or {}
        self.bodies: list[PMXRigidBody] = []
        self.joints: list[PMXJoint] = []
        self.warnings: list[str] = []
        self._smooth_k: dict[int, float] = {}
        self._collider_indices: list[int] = []
        self._body_spring_group: dict[int, int] = {}
        self._parents: dict[int, int] = {}
        for i, nd in enumerate(vrm.nodes):
            for c in nd.get("children", []):
                self._parents[c] = i

        # Reserve PMX group 0 for the swinging spring bodies and map the VRM
        # collider groups to 1..15. Left/right copies of the same body part
        # share one PMX group; PMX has only 16 slots and one is already used by
        # the springs, so `upper_arm.L` and `upper_arm.R` etc. must be merged.
        self._collider_group_of: list[int] = []
        group_by_key: dict[str, int] = {}
        for cg in vrm.collider_groups:
            name = vrm.node_names[cg.node] if 0 <= cg.node < len(vrm.node_names) else ""
            key = name
            for suffix in (".L", ".R", "_L", "_R"):
                if key.endswith(suffix):
                    key = key[:-len(suffix)]
                    break
            if key.startswith("left"):
                key = key[4:]
            elif key.startswith("right"):
                key = key[5:]
            key = key.lower()
            if key not in group_by_key:
                # 15 body groups available (1..15); extras share the last one.
                group_by_key[key] = min(len(group_by_key) + 1, 15)
            self._collider_group_of.append(group_by_key[key])

    # ------------------------------------------------------------------- main
    def build(self):
        self._build_colliders()
        self._build_springs()
        self._offset_single_body_chains()
        if self.requested_preset == "final":
            self._apply_final_preset()
            self._apply_single_chain_springs()
            # lock fix (V11_LOCK_EPS), confirmed in MMD 9.32 (f920eb4 renders) and
            # adopted for final by the user on 2026-11-02
            self._lock_at_eps()
            if self.final_v14_singles:
                n = self._final_singles_v14()
                self.warnings.append(f"诊断预设 final_v14singles：final 参数，{n} 根单段链改用通用版 v14 的弹簧"
                                     f"（每步增益 {V13_SINGLE_GAIN:g}、±{V13_SINGLE_LIMIT_DEG:g}°、物理半径 ×{V13_SINGLE_RADIUS_SCALE:g}）")
        elif self.requested_preset == "universal":
            self._apply_universal_v8()
        elif self.requested_preset == "universal_nocol":
            # diagnostic: v8 with every dynamic body excluded from collisions,
            # to tell collision hitches from solver twitches in MMD recordings
            self._apply_universal_v8()
            for b in self.bodies:
                if b.mode == MODE_PHYSICS_BONE:
                    b.no_collide = MASK_COLLIDE_NONE
            self.warnings.append("诊断预设 universal_nocol：universal 参数，所有动态刚体不参与碰撞")
        elif self.requested_preset == "universal_v7":
            self._apply_universal_v7()
        elif self.requested_preset == "universal_v6":
            self._apply_universal_v6()
        elif self.requested_preset == "universal_soft":
            self._apply_universal_v6(soft=True)
        elif self.requested_preset == "universal_v5":
            self._apply_universal_preset()
        return self.bodies, self.joints, self.warnings

    def _offset_single_body_chains(self):
        """A chain made of one root bone needs a lever arm.

        The rigid body used to sit exactly at its joint, so a root-only chain
        had zero lever and could not visibly swing.  Move only those bodies
        half-way along the PMX bone tail vector; joints stay at the bone origin.
        """
        roots = {
            j.body_b for j in self.joints
            if self.bodies[j.body_a].mode != MODE_PHYSICS_BONE
        }
        has_dynamic_child = {
            j.body_a for j in self.joints
            if self.bodies[j.body_b].mode == MODE_PHYSICS_BONE
        }
        count = 0
        for body_i in roots:
            if body_i in has_dynamic_child:
                continue
            body = self.bodies[body_i]
            if body.bone < 0 or body.bone >= len(self.skel.bones):
                continue
            bone = self.skel.bones[body.bone]
            tail = np.array(bone.tail_offset, dtype=np.float64)
            if np.linalg.norm(tail) < 1e-9 and 0 <= bone.tail_bone < len(self.skel.bones):
                tail = (self.skel.bones[bone.tail_bone].position
                        - bone.position)
            if np.linalg.norm(tail) < 1e-9:
                tail = np.array([0.0, max(float(body.size[0]) * 2.0, 0.1), 0.0])
            # The stable preset uses a shorter lever to keep short strands
            # visible but not floppy; the experimental universal preset keeps
            # the full half-bone lever.
            factor = 0.35 if self.requested_preset == "final" else 0.5
            body.position = body.position + tail * factor
            count += 1
        if count:
            self.warnings.append(f"单段链力臂修正：{count} 根末端骨骼已移到骨骼中点")

    # --------------------------------------------------------------- colliders
    def _build_colliders(self):
        v, skel, S = self.vrm, self.skel, self.scale
        count = 0
        for gi, cg in enumerate(v.collider_groups):
            node = cg.node
            if node is None or node < 0 or node >= len(v.nodes):
                continue
            M = v.world[node]
            R_pose = self.pose_rot.get(node)
            for c in cg.colliders:
                # The node's translation was moved by the A-pose bake, but its
                # rotation part is still the rest (T-pose) matrix. Rotate the
                # local offset by R_pose and add it to the already-moved node
                # position; otherwise arm/hand colliders stay horizontal.
                off_world = M[:3, :3] @ np.array([c.offset[0], c.offset[1], c.offset[2]])
                if R_pose is not None:
                    off_world = R_pose @ off_world
                p = (M[:3, 3] + off_world) * S
                # VRM colliders are point-vs-sphere tests, so they may legally
                # sit inside a hair root. MMD collides volumes, so anything the
                # spring bodies start inside of pushes them forever. Shrinking
                # the collider slightly removes that initial interpenetration
                # without losing meaningful protection.
                r = max(c.radius * S * 0.82, 0.01)
                b = PMXRigidBody()
                b.name = f"{v.node_names[node]}_{count}"
                b.bone = skel.node_to_bone.get(node, -1)
                b.group = self._collider_group_of[gi] if gi < len(self._collider_group_of) else GROUP_COLLIDER
                b.no_collide = MASK_COLLIDE_ALL
                b.shape = SHAPE_SPHERE
                b.size = np.array([r, r, r])
                b.position = np.asarray(p, dtype=np.float64)
                b.rotation = np.zeros(3)
                b.mass = 1.0
                b.linear_damping = 1.0
                b.angular_damping = 1.0
                b.repulsion = 0.0
                b.friction = 0.5 if self.preset == "vrm2pmx" else 0.0
                b.mode = MODE_FOLLOW
                self.bodies.append(b)
                self._collider_indices.append(len(self.bodies) - 1)
                count += 1

    # ----------------------------------------------------------------- springs
    def _build_springs(self):
        v, skel, S = self.vrm, self.skel, self.scale

        # collect every node that actually swings: the group roots plus all
        # their descendants
        moving: list[int] = []
        seen: set[int] = set()
        depth: dict[int, int] = {}
        group_of: dict[int, int] = {}

        for gi, g in enumerate(v.spring_groups):
            if self.preset == "single" and gi > 0:
                continue
            stack = [(b, 0) for b in g.bones]
            while stack:
                node, d = stack.pop()
                if node in seen or node >= len(v.nodes):
                    continue
                if self.preset == "shallow" and d > 2:
                    continue
                seen.add(node)
                if node not in skel.node_to_bone:
                    continue
                moving.append(node)
                depth[node] = d
                group_of[node] = gi
                for c in v.nodes[node].get("children", []):
                    stack.append((c, d + 1))

        if not moving:
            return

        moving_set = set(moving)
        max_depth = max(depth.values()) if depth else 1
        bone_body: dict[int, int] = {}

        # a body per swinging bone
        for node in moving:
            bone_i = skel.node_to_bone[node]
            bone = skel.bones[bone_i]
            p = bone.pos.copy()
            child = self._first_child(node, moving_set, skel)
            if child is not None:
                d = skel.bones[skel.node_to_bone[child]].pos - p
            else:
                d = np.array([0.0, -1.0, 0.0])

            length = float(np.linalg.norm(d))
            gi = group_of[node]
            grp = v.spring_groups[gi]

            if self.preset.startswith(("vrm2pmx", "smooth", "fixed", "zero")):
                # Port of lispcoc/VRM2PMX-Unity (PMXExporter.cs, Rigid region),
                # with the mass/damping gradients matched to the shipped
                # a converted model:
                #   mass  0.05 * 0.3^depth
                #   linear damping 0.9^depth
                #   angular damping follows the measured table
                # A light tip and heavy root is what produces the flutter; a
                # constant mass (our earlier attempt) was stable but limp.
                dep = depth[node]
                radius = max(grp.hit_radius * S, 0.1)
                damp_scale = 1.0
                damp_floor = 0.0
                if self.preset.startswith("vrm2pmx_open"):
                    # open the rotation limit so the chain can actually swing;
                    # the suffix raises a damping FLOOR to kill the ringing that
                    # opening the limit introduces
                    if self.preset.endswith("d99"):
                        damp_floor = 0.99
                    elif self.preset.endswith("d90"):
                        damp_floor = 0.90
                    elif self.preset.endswith("d80"):
                        damp_floor = 0.80

                if self.preset.startswith("smooth"):
                    damp_floor = 0.90
                elif self.preset.startswith(("fixed", "zero")):
                    # W2 uses damping 1.0 which reads like silk in zero gravity.
                    # The suffixes lower the floor so the swing gets more snap.
                    if self.preset.endswith("d90"):
                        damp_floor = 0.90
                    elif self.preset.endswith("d80"):
                        damp_floor = 0.80
                    elif self.preset.endswith("d70"):
                        damp_floor = 0.70
                    else:
                        damp_floor = 1.0
                elif self.preset.startswith("zero"):
                    # Both body and joint at identity rotation. W2 keeps the
                    # joint tilted (90°,0,90°) while the body stays at 0 and
                    # that happens to sit at the right rest pose in MMD, but
                    # tilting BOTH (our Y series) pushed the rest pose off and
                    # the chain settled skewed. Try the fully neutral frame.
                    b.rotation = np.zeros(3)

                if self.preset.startswith(("smooth", "fixed", "zero")):
                    # Mass and joint spring decay by the SAME ratio, so the
                    # natural frequency sqrt(K/I) stays constant down the chain.
                    # VRM2PMX decays mass by 0.3 but keeps K = 5, which drives
                    # the tip frequency into the hundreds of Hz — far beyond
                    # what MMD's 60 Hz solver can integrate, hence the shivering.
                    ratio = 1.0 if self.preset.startswith(("fixed", "zero")) else \
                        {"smooth_a": 0.8, "smooth_b": 0.7, "smooth_c": 0.85}.get(self.preset, 0.8)
                    base_k = 5.0 if self.preset.startswith(("fixed", "zero")) else \
                        {"smooth_a": 1.0, "smooth_b": 1.5, "smooth_c": 0.6}.get(self.preset, 1.0)
                    smooth_mass = 0.05 * (ratio ** dep)
                    self._smooth_k[node] = base_k * (ratio ** dep)
                else:
                    smooth_mass = 0.05 * (0.3 ** dep)

                b = PMXRigidBody()
                b.name = bone.name
                b.bone = bone_i
                b.shape = SHAPE_SPHERE
                b.size = np.array([radius, radius, radius])
                b.position = p
                # Joint frame rotation is handled in the joint block below.
                # "zero" keeps both body and joint at identity; the other
                # presets tilt both to (90°,0,90°) as VRM2PMX does.
                if self.preset.startswith("zero"):
                    b.rotation = np.zeros(3)
                else:
                    b.rotation = np.array([np.pi / 2.0, 0.0, np.pi / 2.0])
                b.mass = smooth_mass
                b.linear_damping = max((1.0 if dep == 0 else 0.9 ** dep) * damp_scale, damp_floor)
                b.angular_damping = max(ANG_DAMP_TABLE[min(dep, len(ANG_DAMP_TABLE) - 1)] * damp_scale, damp_floor)
                b.repulsion = 0.0
                b.friction = 0.5
                b.group = GROUP_SPRING
                b.no_collide = 0x0000 if dep == 0 else 0xFFFE
                b.mode = MODE_PHYSICS_BONE
                self.bodies.append(b)
                bone_body[node] = len(self.bodies) - 1
                self._body_spring_group[len(self.bodies) - 1] = gi
                continue

            radius = max(grp.hit_radius * S, 0.02 * S)
            radius = min(radius, max(length * 0.45, 0.01))
            # Body sits at the middle of the bone; a capsule's size.y is the
            # half length of its *cylindrical* part, so the two hemispherical
            # caps have to be subtracted or neighbouring capsules overlap and
            # the solver keeps pushing them apart (visible as permanent jitter
            # even while the model stands still).
            half = max(length * 0.5 - radius, 0.0)

            ratio = depth[node] / max(max_depth, 1)
            mass = float(np.clip(3.6 * (1.0 - 0.75 * ratio), 0.32, 4.8))
            if self.preset == "uniform":
                # Uniform mass removes the mass ratio between neighbours, which
                # Bullet's sequential impulse solver handles badly.
                mass = 2.0
            if self.preset.startswith("chain"):
                # PMX Editor's "rigid body chain" plugin approach: the fling
                # feel comes from the mass ratio between neighbours, not from a
                # joint spring. Reverse order, base 0.1, ratio 2 -> the root is
                # heavy and the tip is feather light.
                mass = float(np.clip(0.1 * (2.0 ** (max_depth - depth[node])), 0.1, 60.0))
            drag = float(np.clip(grp.drag_force, 0.0, 1.0))
            no_gravity = grp.gravity_power <= 1e-6

            b = PMXRigidBody()
            b.name = skel.bones[bone_i].name
            b.bone = bone_i
            b.group = GROUP_SPRING
            b.no_collide = MASK_COLLIDE_ALL_BUT_SELF
            if depth[node] == 0:
                # The first body of a chain sits right where the hair/tail
                # leaves the skull, which is inside the head collider in most
                # VRM rigs. It is driven by the bone anyway, so let it ignore
                # collisions instead of being pushed forever.
                b.no_collide = MASK_COLLIDE_NONE
            if self.preset in ("nocollide", "chain_nocol"):
                # diagnostic: everything in the spring group ignores all
                # colliders. If the jitter disappears, the cause is collision,
                # not the constraints.
                b.no_collide = MASK_COLLIDE_NONE

            use_sphere = self.preset in ("sphere", "atbone_sphere") or self.preset.startswith("chain")
            body_pos = p if self.preset in ("atbone", "atbone_sphere", "chain_root") else p + d * 0.5
            if use_sphere or length <= radius * 2.2:
                b.shape = SHAPE_SPHERE
                b.size = np.array([min(radius, max(length * 0.5, 0.01))] * 3)
                b.position = body_pos
                b.rotation = np.zeros(3)
            else:
                b.shape = SHAPE_CAPSULE
                b.size = np.array([radius, half, radius])
                b.position = body_pos
                b.rotation = align_y_euler(d)
            b.mass = mass
            # MMD's damping is a per-frame velocity multiplier, so it needs to
            # be pushed near the top of the range to settle a stiff chain.
            # When the VRM joint declares gravityPower = 0 the strand should not
            # sag under MMD's global gravity either, so the linear damping is
            # raised further (it bleeds off the downward velocity before it can
            # accumulate) while angular damping stays slightly lower so the
            # strand can still swing from inertia.
            lin = 0.985 if no_gravity else 0.95 + 0.045 * drag
            ang = 0.95 + 0.045 * drag
            if no_gravity:
                # Measured by the user in MMD: damping = 1 is what actually
                # removes gravity there (velocity is zeroed each frame, so the
                # downward drift never accumulates). Our earlier 0.985 was too
                # weak and the hair still sagged.
                lin = ang = 1.0
            if self.preset == "damp0":
                lin = ang = 0.0
            elif self.preset == "damp1":
                lin = ang = 1.0
            elif self.preset == "soft":
                lin = ang = 0.99
            elif self.preset == "sphere":
                # keep the gravity-free damping, only the shape changes
                pass
            elif self.preset in ("damp9999", "damp9999_move"):
                # 1.0 exactly is the edge of Bullet's damping range; some
                # engines compute pow(1 - damping, dt) and hit a degenerate
                # zero. 0.9999 keeps the gravity-free behaviour with margin.
                lin = ang = 0.9999
            b.linear_damping = float(np.clip(lin, 0.0, 1.0))
            b.angular_damping = float(np.clip(ang, 0.0, 1.0))
            b.repulsion = 0.0
            b.friction = 0.5 if self.preset.startswith("chain") else 0.0
            b.mode = MODE_PHYSICS if self.preset in ("mode1", "chain_mode1") else MODE_PHYSICS_BONE
            self.bodies.append(b)
            bone_body[node] = len(self.bodies) - 1

        # static anchors for chain roots whose parent does not swing
        anchor_body: dict[int, int] = {}

        def ensure_anchor(node: int) -> int:
            parent = self._parents.get(node, -1)
            if parent in anchor_body:
                return anchor_body[parent]
            b = PMXRigidBody()
            pname = skel.bones[skel.node_to_bone[parent]].name if parent in skel.node_to_bone else "root"
            b.name = pname + "_anchor"
            b.bone = skel.node_to_bone.get(parent, -1)
            b.group = GROUP_SPRING
            b.no_collide = MASK_COLLIDE_NONE
            b.shape = SHAPE_SPHERE
            b.size = np.array([0.05 * S] * 3)
            if self.preset.startswith(("vrm2pmx", "smooth", "fixed", "zero")):
                # VRM2PMX puts the anchor at the *parent* bone position and
                # gives it the same sphere radius as the strand bodies.
                radius = max(v.spring_groups[group_of[node]].hit_radius * S, 0.1)
                b.size = np.array([radius, radius, radius])
                if self.preset.startswith("zero"):
                    # Putting the anchor one bone away from the joint leaves a
                    # lever arm that the 6DOF constraint reads as a rotational
                    # bias, so every link settles a few degrees off and the
                    # chain curls into a spiral. Sitting the anchor exactly on
                    # the joint removes that bias.
                    b.position = (skel.bones[skel.node_to_bone[node]].pos.copy()
                                  if node in skel.node_to_bone else np.zeros(3))
                else:
                    b.position = (skel.bones[skel.node_to_bone[parent]].pos.copy()
                                  if parent in skel.node_to_bone else np.zeros(3))
                b.friction = 0.5
                b.group = 0
                b.no_collide = 0x0000
            else:
                b.position = (skel.bones[skel.node_to_bone[node]].pos.copy() if node in skel.node_to_bone
                              else np.zeros(3))
            b.rotation = np.zeros(3)
            b.mass = 1.0
            b.linear_damping = 1.0
            b.angular_damping = 1.0
            b.repulsion = 0.0
            b.friction = 0.0
            b.mode = MODE_FOLLOW
            self.bodies.append(b)
            idx = len(self.bodies) - 1
            anchor_body[parent] = idx
            return idx

        # joints
        for node in moving:
            child_body = bone_body[node]
            parent = self._parents.get(node, -1)
            parent_body = bone_body.get(parent)
            if parent_body is None:
                parent_body = ensure_anchor(node)

            bone_i = skel.node_to_bone[node]
            bone = skel.bones[bone_i]
            grp = v.spring_groups[group_of[node]]

            child = self._first_child(node, moving_set, skel)
            d = (skel.bones[skel.node_to_bone[child]].pos - bone.pos) if child is not None \
                else np.array([0.0, -1.0, 0.0])
            length = float(np.linalg.norm(d))

            # moment of inertia of a thin rod about its end ~ 1/3 m L^2
            inertia = max(1.0 / 3.0 * self.bodies[child_body].mass * length * length, 1e-6)
            # Measure the real swing frequency of the equivalent VRM spring,
            # then convert to a joint spring constant: K = I * omega^2.
            #
            # The frequency has to be divided by sqrt(scale) as well. VRM works
            # in metres with g = 9.8, while MMD feeds model units into Bullet
            # with the same g. A bone that is 14x longer therefore swings
            # sqrt(14)x slower for the same physical behaviour, so matching the
            # metre-based frequency would make hair visibly too stiff and
            # springy ("Q弹"). Dividing by sqrt(scale) puts the MMD simulation
            # back on the same visual timescale.
            w = angular_frequency(grp.stiffiness, grp.drag_force,
                                  max(length / self.scale, 1e-5), grp.gravity_power)
            # Two corrections on top of the metre-based measurement:
            #  * /sqrt(scale): MMD runs Bullet in model units, so a 14x longer
            #    bone swings sqrt(14)x slower for the same g. Without this the
            #    hair is far too stiff and springy.
            #  * /1.5: extra headroom so the damping ratio stays in a range
            #    MMD's per-frame velocity damping can actually settle. The
            #    engine only offers v *= pow(1-d, dt), which caps the usable
            #    damping ratio at roughly 0.2-0.3; anything stiffer rings
            #    forever ("一直抖").
            w /= math.sqrt(max(self.scale, 1.0)) * 1.5
            k = inertia * w * w * self.strength
            if self.preset == "soft":
                k *= 0.2

            rot_lim = ROT_LIMIT
            if self.preset.startswith("chain"):
                # PMX Editor chain plugin: "clear the joint values". No spring
                # at all; the motion comes from inertia through the mass
                # gradient, and gravity is neutralised by damping = 1.
                k = 0.0
                if self.preset in ("chain_rigid", "chain_root", "chain_nocol", "chain_mode1"):
                    rot_lim = 0.0
                elif self.preset == "chain15":
                    rot_lim = float(np.radians(15.0))
                elif self.preset == "chain30":
                    rot_lim = float(np.radians(30.0))
                elif self.preset == "chain45":
                    rot_lim = float(np.radians(45.0))
            k = float(np.clip(k, 0.0, 20000.0))

            j = PMXJoint()
            j.name = bone.name
            j.type = 0                      # 6DOF spring
            j.body_a = parent_body
            j.body_b = child_body
            j.position = bone.pos.copy()

            if self.preset.startswith(("vrm2pmx", "smooth", "fixed", "zero")):
                # Straight from PMXExporter.cs:
                #   Rot = (90deg, 0, 90deg)
                # with the original comment "何故か初期向きを傾けると縦揺れ
                # noiseが発生しない" — tilting the joint frame kills the
                # vertical jitter. Aligning it with the bone (what we did
                # before) is exactly what caused the permanent shaking.
                stiff = max(grp.stiffiness, 0.0)
                drag = float(np.clip(grp.drag_force, 0.0, 1.0))
                j.rotation = (np.zeros(3) if self.preset.startswith("zero")
                              else np.array([np.pi / 2.0, 0.0, np.pi / 2.0]))
                k5 = self._smooth_k.get(node, 5.0 * stiff)
                j.spring_rot = np.array([k5, k5, k5])
                if self.preset.startswith(("zero", "fixed15")):
                    lim = np.radians(15.0)
                elif self.preset.startswith("fixed30"):
                    lim = np.radians(30.0)
                elif self.preset.startswith("fixed90"):
                    lim = np.radians(90.0)
                elif self.preset.startswith(("vrm2pmx_open", "smooth")):
                    # VRM2PMX zeroes the limit when stiffness >= 1, which locks
                    # the chain solid: correct for stability, useless for
                    # flutter. Open it so the strand can actually swing.
                    lim = np.radians(90.0)
                else:
                    lim = np.radians(90.0) * (-90.0 if stiff < 1.0 else 0.0) * (0.0 if drag < 0.1 else drag)
                if self.preset.startswith(("zero", "fixed", "vrm2pmx_open", "smooth")):
                    # An open limit must be a range around the rest pose (0°).
                    # Writing +lim to BOTH bounds locks every joint at +lim;
                    # down a long chain those fixed offsets accumulate into the
                    # observed spiral / one-sided deflection.
                    j.rot_lo = np.array([-lim, -lim, -lim])
                    j.rot_hi = np.array([lim, lim, lim])
                else:
                    # Keep the original VRM2PMX lock behaviour unchanged. Its
                    # stable W2 path uses lim = 0, i.e. no rest-pose offset.
                    j.rot_lo = np.array([lim, lim, lim])
                    j.rot_hi = np.array([lim, lim, lim])
                j.move_lo = np.zeros(3)
                j.move_hi = np.zeros(3)
                j.spring_move = np.zeros(3)
                self.joints.append(j)
                continue

            # JOINT FRAME IS ALWAYS (90deg, 0, 90deg).
            # Both VRM2PMX (PMXExporter.cs) and the reference PMX agree on this.
            # VRM2PMX even carries the comment "何故か初期向きを傾けると縦揺れ
            # noiseが発生しない" — tilting the joint frame is what stops the
            # vertical jitter. Aligning it with the bone direction, which is
            # what all our earlier presets did, is exactly the wrong choice.
            j.rotation = np.array([np.pi / 2.0, 0.0, np.pi / 2.0])
            # move limit 0 means "no translation allowed", which Bullet turns
            # into a hard constraint. A hair-width of slack avoids the solver
            # fighting an over-constrained pair.
            slack = 0.01 if self.preset in ("move_free", "damp9999_move") else 0.0
            j.move_lo = np.array([-slack, -slack, -slack])
            j.move_hi = np.array([slack, slack, slack])
            j.rot_lo = np.array([-rot_lim, -rot_lim, -rot_lim])
            j.rot_hi = np.array([rot_lim, rot_lim, rot_lim])
            j.spring_move = np.zeros(3)
            j.spring_rot = np.array([k, k, k])
            self.joints.append(j)

        if len(self.bodies) > 300:
            self.warnings.append(
                f"物理刚体 {len(self.bodies)} 个（含 {len(self.joints)} 个关节），"
                "MMD 里可能会卡，可关闭部分毛发物理")

        zero_g = sum(1 for g in v.spring_groups if g.gravity_power <= 1e-6)
        if zero_g:
            damping_note = ("阻尼 = 1.0（无重力）"
                            if self.preset.startswith(("zero", "fixed", "smooth"))
                            else "线阻尼 0.985")
            self.warnings.append(
                f"{zero_g}/{len(v.spring_groups)} 个物理组在 VRM 里是 0 重力。"
                "PMX 没有单个刚体的重力开关（MMD 重力是全局的），"
                f"已把这些刚体的{damping_note}抑制下坠；"
                "若要完全复现，请在 MMD「物理演算 → 重力设定」里把重力调低")

    # ------------------------------------------------------------- final preset
    def _apply_final_preset(self):
        """Parameters validated in the Z20 build.

        This is deliberately a post-pass: the zero-rotation baseline already
        builds the correct bodies/joints, then these rules set the shipped
        chain masses and collision masks without duplicating the generator.
        """
        dynamic = [i for i, r in enumerate(self.bodies)
                   if r.mode == MODE_PHYSICS_BONE]
        incoming = {j.body_b: j for j in self.joints
                    if self.bodies[j.body_b].mode == MODE_PHYSICS_BONE}

        depth: dict[int, int] = {}
        chain_of: dict[int, int] = {}
        members: list[list[int]] = []
        unresolved = dict(incoming)
        while unresolved:
            progressed = False
            for body_i, joint in list(unresolved.items()):
                parent_i = joint.body_a
                if self.bodies[parent_i].mode != MODE_PHYSICS_BONE:
                    cid = len(members)
                    members.append([])
                    d = 0
                elif parent_i in depth:
                    cid = chain_of[parent_i]
                    d = depth[parent_i] + 1
                else:
                    continue
                depth[body_i] = d
                chain_of[body_i] = cid
                members[cid].append(body_i)
                del unresolved[body_i]
                progressed = True
            if not progressed:
                break

        def pe_mass(n: int, d: int, ratio: float, tip: float = 0.1) -> float:
            return tip * (ratio ** (n - 1 - d))

        ys = [float(self.bodies[i].position[1]) for i in dynamic]
        bottom, top = (min(ys), max(ys)) if ys else (0.0, 1.0)
        model_h = max(top - bottom, 1e-6)

        def name_hint(names: list[str]) -> str | None:
            """Weak prior only. Geometry decides unless the metric is ambiguous."""
            text = " ".join(names).lower()
            toks = [t for t in text.replace("-", "_").replace(".", "_").split("_") if t]
            def tok(*prefixes):
                return any(t.startswith(p) for t in toks for p in prefixes)
            if "whisk" in text or "huxu" in text or "胡须" in text or tok("hx"):
                return "whisker"
            if "ear" in text or "erduo" in text or "耳朵" in text or tok("ed"):
                return "ear"
            if "tail" in text or "weiba" in text or "尾巴" in text or tok("wb"):
                return "tail"
            if ("lingdai" in text or "hudiejie" in text or "lingdang" in text
                    or "领带" in text or "蝴蝶结" in text or "铃铛" in text
                    or tok("ld", "hdj", "ring")):
                return "accessory"
            if ("hair" in text or "toufa" in text or "cemao" in text or "houfa" in text
                    or "maofa" in text or "cefa" in text or "刘海" in text or "毛发" in text
                    or tok("tf", "cm", "hf", "mf")):
                return "hair"
            return None

        categories: dict[str, int] = {}
        for ids in members:
            ids.sort(key=lambda i: depth[i])
            n = len(ids)
            names = [self.bodies[i].name for i in ids]
            idset = set(ids)
            children: dict[int, list[int]] = {i: [] for i in ids}
            for i in ids:
                parent_i = incoming[i].body_a
                if parent_i in idset:
                    children[parent_i].append(i)

            def path_len(i: int) -> float:
                best = 0.0
                for c in children.get(i, []):
                    seg = float(np.linalg.norm(self.bodies[c].position
                                               - self.bodies[i].position))
                    best = max(best, seg + path_len(c))
                return best

            root_i = ids[0]
            root = self.bodies[root_i]
            length = path_len(root_i)
            radius = float(np.mean([self.bodies[i].size[0] for i in ids]))
            root_frac = (float(root.position[1]) - bottom) / model_h
            length_frac = length / model_h

            # Geometry first. Names are only applied afterwards as a weak hint.
            if n >= 5 and length_frac >= 0.45 and radius >= 0.7:
                category = "tail"
            elif n == 4 and radius <= 0.18 and length_frac <= 0.16:
                category = "whisker"
            elif radius >= 0.4 and n <= 4 and root_frac >= 0.6 and length_frac <= 0.35:
                category = "ear"
            elif radius <= 0.7 and root_frac >= 0.35:
                category = "hair"
            else:
                category = "accessory"

            hint = name_hint(names)
            if hint == "ear" and root_frac >= 0.5 and length_frac <= 0.45:
                category = "ear"
            elif hint == "whisker":
                category = "whisker"
            elif hint == "tail" and n >= 4:
                category = "tail"
            elif hint == "accessory" and category != "tail":
                category = "accessory"
            elif hint == "hair" and category not in ("tail", "ear"):
                category = "hair"
            categories[category] = categories.get(category, 0) + 1

            if category == "tail":
                max_d = max(depth[i] for i in ids)
                masses = [0.1 * (1.4 ** (max_d - depth[i])) for i in ids]
            elif category == "whisker":
                if n == 4:
                    # PE reference has an overflowed 6e13 batch value. Use its
                    # smallest sane strand instead (21.6 → 3.6 → 0.6 → 0.1).
                    masses = [21.6, 3.6, 0.6, 0.1]
                else:
                    raw = [pe_mass(n, depth[i], 6.0) for i in ids]
                    total = 21.6 * max(1.0, n / 4.0)
                    scale = total / sum(raw)
                    masses = [x * scale for x in raw]
            elif category == "ear":
                raw = [pe_mass(n, depth[i], 1.6) for i in ids]
                total = 0.10 * max(1.0, n / 3.0)
                scale = total / sum(raw)
                masses = [x * scale for x in raw]
            elif category == "accessory":
                raw = [pe_mass(n, depth[i], 1.6) for i in ids]
                total = 0.4 * max(1.0, n / 4.0)
                scale = total / sum(raw)
                masses = [x * scale for x in raw]
            elif n == 4 and length_frac >= 0.09 and radius >= 0.15:
                # Long, thick four-segment hair: stable, low flutter.
                masses = [2.7, 0.9, 0.3, 0.1]
            elif n >= 3:
                raw = [pe_mass(n, depth[i], 1.6) for i in ids]
                total = FINAL_LONG_BASE_TOTAL * max(1.0, n / FINAL_LONG_REF_SEGMENTS)
                scale = total / sum(raw)
                masses = [x * scale for x in raw]
            elif n == 2:
                masses = [0.16, 0.1]
            else:
                masses = [0.1] * n

            for i, mass in zip(ids, masses):
                self.bodies[i].mass = max(float(mass), FINAL_MIN_BODY_MASS)
                self.bodies[i].no_collide = MASK_COLLIDE_ALL_BUT_SELF

            # Ears should swing freely: no body collision, and a smaller
            # collider radius lowers rotational inertia instead of acting rigid.
            if category == "ear":
                for i in ids:
                    self.bodies[i].no_collide = MASK_COLLIDE_NONE
                    self.bodies[i].size = np.minimum(self.bodies[i].size, 0.6)
            elif category in ("whisker", "tail"):
                # These chains are easily kicked by volume colliders; keep them
                # collision-free and use mass to control stiffness.
                for i in ids:
                    self.bodies[i].no_collide = MASK_COLLIDE_NONE

        # Any body that starts inside a body collider would be pushed forever
        # by MMD's volume collision. Leave only those bodies collision-free.
        for body_i in dynamic:
            body = self.bodies[body_i]
            worst = min(
                (float(np.linalg.norm(self.bodies[k].position - body.position)
                       - float(self.bodies[k].size[0]) - float(body.size[0]))
                 for k in self._collider_indices),
                default=0.0,
            )
            if worst < -1e-6:
                body.no_collide = MASK_COLLIDE_NONE

        # The PE "clear joint values" step: no spring and no angular/linear
        # limits. The chain is driven purely by inertia through the joints.
        for j in self.joints:
            j.spring_rot = np.zeros(3)
            j.spring_move = np.zeros(3)
            j.rot_lo = np.zeros(3)
            j.rot_hi = np.zeros(3)
            j.move_lo = np.zeros(3)
            j.move_hi = np.zeros(3)

        zero_masks = sum(1 for i in dynamic
                         if self.bodies[i].no_collide == MASK_COLLIDE_NONE)
        cat_text = "，".join(f"{k} {v}" for k, v in sorted(categories.items()))
        self.warnings.append(
            f"正式物理预设：{len(members)} 条链（{cat_text}），动态刚体 {len(dynamic)} 个，"
            f"其中 {zero_masks} 个不参与碰撞"
        )

    # ------------------------------------------------- single root chains
    def _apply_single_chain_springs(self):
        """Give root-only chains a light spring instead of damping them still.

        The final preset intentionally clears all joint springs for the long
        chains.  A chain with exactly one bone, however, has no child and no
        chain inertia; with damping=1 it simply follows the head and never
        visibly swings.  A small spring + lower angular damping gives it the
        intended "only the terminal bone flutters" behaviour.
        """
        has_dynamic_child = {
            j.body_a for j in self.joints
            if self.bodies[j.body_b].mode == MODE_PHYSICS_BONE
        }
        roots = {
            j.body_b for j in self.joints
            if self.bodies[j.body_a].mode != MODE_PHYSICS_BONE
        }
        count = 0
        for body_i in roots:
            if body_i in has_dynamic_child:
                continue
            body = self.bodies[body_i]
            joint = next((j for j in self.joints if j.body_b == body_i), None)
            if joint is None:
                continue
            lever = float(np.linalg.norm(body.position - joint.position))
            if lever < 1e-6:
                continue
            length = lever * 2.0
            # A heavier point mass keeps Bullet stable on very short strands.
            # K is recalculated from the new inertia, so the 0.25s period and
            # the visible hardness stay unchanged.
            body.mass = max(float(body.mass), SINGLE_CHAIN_MIN_MASS)
            inertia = body.mass * (length * length / 3.0
                                   + 0.4 * float(body.size[0]) ** 2)
            omega = 2.0 * math.pi / SINGLE_CHAIN_PERIOD
            k = inertia * omega * omega
            joint.spring_rot = np.array([k, k, k])
            joint.spring_move = np.zeros(3)
            joint.move_lo = np.zeros(3)
            joint.move_hi = np.zeros(3)
            limit = np.radians(20.0)
            joint.rot_lo = np.array([-limit, -limit, -limit])
            joint.rot_hi = np.array([limit, limit, limit])
            body.linear_damping = 1.0
            body.angular_damping = SINGLE_CHAIN_ANG_DAMP
            # Root-only hairs are tiny and close to the skull; body colliders
            # cause a visible hitch on fast head motion.  Let them swing free.
            body.no_collide = MASK_COLLIDE_NONE
            count += 1
        if count:
            self.warnings.append(
                f"单段链弹簧：{count} 根，周期约 {SINGLE_CHAIN_PERIOD:.2f}s，"
                f"角阻尼 {SINGLE_CHAIN_ANG_DAMP:.3f}"
            )

    # -------------------------------------------------------- universal preset
    def _apply_universal_preset(self):
        """Geometry-only mapping, independent of bone-name categories.

        VRM spring bones are point masses on massless sticks.  In PMX we
        approximate each bone by a sphere rigid body plus a 6DOF rotational
        spring:

            mass   = constant point mass (0.05)
            I      = m * (L^2/3 + 0.4*r^2)   # rod + sphere inertia
            omega  = measured VRM angular frequency, scale-corrected
            K      = I * omega^2

        No ear/hair/whisker classification is used; only the VRM stiffness,
        drag, gravity and the bone's world-space length / collider radius.
        """
        v = self.vrm
        S = self.scale
        dynamic = [i for i, r in enumerate(self.bodies)
                   if r.mode == MODE_PHYSICS_BONE]

        incoming = {j.body_b: j for j in self.joints
                    if self.bodies[j.body_b].mode == MODE_PHYSICS_BONE}
        children_by_body: dict[int, list[PMXJoint]] = {}
        for j in self.joints:
            if self.bodies[j.body_a].mode == MODE_PHYSICS_BONE:
                children_by_body.setdefault(j.body_a, []).append(j)

        k_values: list[float] = []
        for body_i in dynamic:
            body = self.bodies[body_i]
            joint = incoming.get(body_i)
            if joint is None:
                continue

            gi = self._body_spring_group.get(body_i, -1)
            grp = v.spring_groups[gi] if 0 <= gi < len(v.spring_groups) else None
            if grp is None:
                continue

            # The rigid body must sit on the bone segment, not exactly at its
            # joint, otherwise the spring has no lever arm to rotate.  This was
            # why the universal preset appeared to "never return": the spring
            # torque had almost no effect on a body centred on its pivot.
            child_joints = children_by_body.get(body_i, [])
            if child_joints:
                child_center = np.mean(
                    [np.asarray(cj.position, dtype=np.float64)
                     for cj in child_joints], axis=0)
                body.position = (np.asarray(joint.position, dtype=np.float64)
                                 + (child_center - np.asarray(joint.position, dtype=np.float64)) * 0.5)
            elif np.linalg.norm(body.position - joint.position) < 1e-6:
                # Leaf bone: use its PMX tail vector (already handled for
                # zero-length root chains by _offset_single_body_chains()).
                if 0 <= body.bone < len(self.skel.bones):
                    tail = np.asarray(self.skel.bones[body.bone].tail_offset,
                                      dtype=np.float64)
                    if np.linalg.norm(tail) > 1e-9:
                        body.position = body.position + tail * 0.5

            # The bone's own length: prefer the outgoing child segment; a leaf
            # falls back to its incoming segment.  A root without either uses
            # its collider diameter as the smallest sensible length.
            if child_joints:
                lengths = [float(np.linalg.norm(cj.position - joint.position))
                           for cj in child_joints]
                length = float(np.mean(lengths))
            else:
                # Single-body root chains were moved half a tail-length away
                # from their joint; recover that tail length here.
                length = float(np.linalg.norm(body.position - joint.position)) * 2.0
                if length < 1e-6:
                    parent_body = self.bodies[joint.body_a]
                    length = float(np.linalg.norm(parent_body.position - body.position))
                if length < 1e-6:
                    length = max(float(body.size[0]) * 2.0, 0.1 * S)
            length = max(length, 1e-6)

            radius = max(float(body.size[0]), 1e-6)
            # VRM works on point masses; keep the mass constant and let the
            # geometry enter through inertia and the joint spring.
            mass = 0.05
            inertia = mass * (length * length / 3.0 + 0.4 * radius * radius)

            stiff = max(float(grp.stiffiness), 0.0)
            drag = float(np.clip(grp.drag_force, 0.0, 1.0))
            gravity = float(grp.gravity_power)
            length_m = max(length / S, 1e-5)
            if stiff > 1e-6:
                omega = angular_frequency(stiff, drag, length_m, gravity)
                # MMD runs Bullet in model units; scale the visual time axis
                # back to the VRM metre-based one.
                omega /= math.sqrt(max(S, 1.0))
                k = inertia * omega * omega * self.strength * UNIVERSAL_SPRING_GAIN
                k = min(k, UNIVERSAL_MAX_K)
            else:
                omega = 0.0
                k = 0.0
            k_values.append(k)

            body.mass = mass
            # Zero-gravity VRM strands must not sag in MMD's global gravity.
            if gravity <= 1e-6:
                body.linear_damping = 1.0
                body.angular_damping = 1.0
            else:
                body.linear_damping = 0.95
                body.angular_damping = 0.98

            joint.spring_rot = np.array([k, k, k])
            joint.spring_move = np.zeros(3)
            joint.move_lo = np.zeros(3)
            joint.move_hi = np.zeros(3)
            # Keep the joint frame neutral; the spring itself supplies the
            # VRM "return to the rest direction" behaviour.
            joint.rotation = np.zeros(3)
            # A generous limit keeps solver explosions from folding a chain
            # back on itself without turning the strand into a stiff rod.
            limit = np.pi / 3.0
            joint.rot_lo = np.array([-limit, -limit, -limit])
            joint.rot_hi = np.array([limit, limit, limit])

        # Same collision policy as the final preset: all dynamic bodies may
        # hit the body colliders, except those that start inside one.
        for body_i in dynamic:
            body = self.bodies[body_i]
            body.no_collide = MASK_COLLIDE_ALL_BUT_SELF
        for body_i in dynamic:
            body = self.bodies[body_i]
            worst = min(
                (float(np.linalg.norm(self.bodies[k].position - body.position)
                       - float(self.bodies[k].size[0]) - float(body.size[0]))
                 for k in self._collider_indices),
                default=0.0,
            )
            if worst < -1e-6:
                body.no_collide = MASK_COLLIDE_NONE

        if k_values:
            self.warnings.append(
                f"通用物理预设：{len(dynamic)} 个动态刚体，关节弹簧 "
                f"{min(k_values):.3f}~{max(k_values):.3f}（含 MMD 刚度补偿 ×{UNIVERSAL_SPRING_GAIN:.0f}，"
                f"上限 {UNIVERSAL_MAX_K:.0f}），按 VRM 参数与几何长度/半径换算"
            )

    # ----------------------------------------------------- universal preset v6
    def _apply_universal_v6(self, soft: bool = False):
        """Classification-free physics that is stable at 30 FPS.

        Common to both modes: every dynamic body sits half-way along its bone,
        and bodies collide with the VRM colliders unless they start inside one.  No bone names or categories are used.

        locked (preset "universal_v6"):
            all joint rotations locked, damping 1.0, mass V6_MASS at the tip and
            x V6_MASS_RATIO per segment towards the root.  Gravity-safe; the
            strands follow with the lag of the constraint solver, like the
            final preset's long chains, but without its single-chain springs.

        soft (preset "universal_soft", needs MMD gravity off):
            uniform mass V6_MASS, damping V6_SOFT_DAMPING keeps momentum, each joint gets an isotropic
            spring  K = I_body * omega^2  with omega the VRM spring's angular
            frequency (real seconds), capped at V6_SOFT_OMEGA_DT_MAX / dt(30 FPS).
        """
        v = self.vrm
        S = self.scale
        dyn = [i for i, r in enumerate(self.bodies) if r.mode == MODE_PHYSICS_BONE]
        dyn_set = set(dyn)
        incoming = {j.body_b: j for j in self.joints if j.body_b in dyn_set}
        children: dict[int, list[int]] = {}
        for j in self.joints:
            if j.body_a in dyn_set and j.body_b in dyn_set:
                children.setdefault(j.body_a, []).append(j.body_b)

        # place every body half-way along its bone (lever arm for the joint)
        for bi in dyn:
            body = self.bodies[bi]
            joint = incoming.get(bi)
            if joint is None:
                continue
            jp = np.asarray(joint.position, dtype=np.float64)
            kids = children.get(bi, [])
            if kids:
                tip = np.mean([np.asarray(incoming[c].position, dtype=np.float64)
                               for c in kids], axis=0)
                body.position = jp + (tip - jp) * 0.5
            elif np.linalg.norm(body.position - jp) < 1e-6 and 0 <= body.bone < len(self.skel.bones):
                tail = np.asarray(self.skel.bones[body.bone].tail_offset, dtype=np.float64)
                if np.linalg.norm(tail) > 1e-9:
                    body.position = jp + tail * 0.5
            body.mass = V6_MASS

        if not soft:
            # height = segments between this body and the farthest tip below it
            height: dict[int, int] = {}

            def h(bi: int) -> int:
                if bi not in height:
                    height[bi] = 1 + max((h(c) for c in children.get(bi, [])), default=-1)
                return height[bi]

            for bi in dyn:
                self.bodies[bi].mass = V6_MASS * V6_MASS_RATIO ** h(bi)

        omega_cap = V6_SOFT_OMEGA_DT_MAX * V6_REF_FPS
        limit = np.radians(V6_SOFT_LIMIT_DEG) if soft else 0.0
        k_values, capped = [], 0
        for bi in dyn:
            body = self.bodies[bi]
            joint = incoming.get(bi)
            if joint is None:
                continue
            jp = np.asarray(joint.position, dtype=np.float64)
            k = 0.0
            if soft:
                r2 = float(np.sum((np.asarray(body.position, dtype=np.float64) - jp) ** 2))
                inertia = max(body.mass * (r2 + 0.4 * float(body.size[0]) ** 2), 1e-6)
                gi = self._body_spring_group.get(bi, -1)
                grp = v.spring_groups[gi] if 0 <= gi < len(v.spring_groups) else None
                w = 0.0
                if grp is not None and grp.stiffiness > 1e-6:
                    seg = 2.0 * float(np.sqrt(r2))
                    w = angular_frequency(grp.stiffiness, float(np.clip(grp.drag_force, 0.0, 1.0)),
                                          max(seg / S, 1e-5), grp.gravity_power) * self.strength
                if w > omega_cap:
                    w = omega_cap
                    capped += 1
                k = inertia * w * w
                k_values.append(k)
            damping = V6_SOFT_DAMPING if soft else 1.0
            body.linear_damping = damping
            body.angular_damping = damping
            joint.rotation = np.zeros(3)
            joint.spring_rot = np.array([k, k, k])
            joint.spring_move = np.zeros(3)
            joint.move_lo = np.zeros(3)
            joint.move_hi = np.zeros(3)
            joint.rot_lo = np.array([-limit, -limit, -limit])
            joint.rot_hi = np.array([limit, limit, limit])

        for bi in dyn:
            body = self.bodies[bi]
            body.no_collide = MASK_COLLIDE_ALL_BUT_SELF
            worst = min(
                (float(np.linalg.norm(self.bodies[k].position - body.position)
                       - float(self.bodies[k].size[0]) - float(body.size[0]))
                 for k in self._collider_indices),
                default=0.0,
            )
            if worst < -1e-6:
                body.no_collide = MASK_COLLIDE_NONE

        if soft:
            kt = f"{min(k_values):.3f}~{max(k_values):.3f}" if k_values else "无"
            self.warnings.append(
                f"通用物理 v6（柔）：{len(dyn)} 个动态刚体，阻尼 {V6_SOFT_DAMPING}，关节弹簧 {kt}，"
                f"{capped} 个关节触到 30FPS 稳定上限。需在 MMD/PE 里把重力设为 0，否则会下垂"
            )
        else:
            self.warnings.append(
                f"通用物理 v6：{len(dyn)} 个动态刚体，关节锁定、阻尼 1.0，"
                f"质量自末端 {V6_MASS} 起每节 ×{V6_MASS_RATIO:g}，"
                "不按名称分类，重力保持默认即可"
            )

    # ----------------------------------------------------- universal preset v7
    def _apply_universal_v7(self):
        """Classification-free, jitter-free in MMD (see V7_* constants).

        * Every dynamic body sits half-way along its bone; masses grow from
          V7_MASS_TIP at the tips by V7_MASS_RATIO per segment towards the root.
        * Chains with more than one body: all joints locked, damping 1.0.  The
          visible sway is the constraint lag, as in the final preset's long
          chains, which MMD renders smoothly.
        * Single-body chains: a spring at the measured clean gain
          (K = V7_SINGLE_GAIN * I / dt^2), limit +-V7_SINGLE_LIMIT_DEG, no
          body collision.  For these the body's own inertia is all the joint
          drives, so the spring also returns them in ~dt/gain = 0.3 s.
        * Damping 1.0 everywhere, so MMD's global gravity is cancelled.
        """
        dyn = [i for i, r in enumerate(self.bodies) if r.mode == MODE_PHYSICS_BONE]
        dyn_set = set(dyn)
        incoming = {j.body_b: j for j in self.joints if j.body_b in dyn_set}
        children: dict[int, list[int]] = {}
        for j in self.joints:
            if j.body_a in dyn_set and j.body_b in dyn_set:
                children.setdefault(j.body_a, []).append(j.body_b)

        for bi in dyn:
            body = self.bodies[bi]
            joint = incoming.get(bi)
            if joint is None:
                continue
            jp = np.asarray(joint.position, dtype=np.float64)
            kids = children.get(bi, [])
            if kids:
                tip = np.mean([np.asarray(incoming[c].position, dtype=np.float64)
                               for c in kids], axis=0)
                body.position = jp + (tip - jp) * 0.5
            elif np.linalg.norm(body.position - jp) < 1e-6 and 0 <= body.bone < len(self.skel.bones):
                tail = np.asarray(self.skel.bones[body.bone].tail_offset, dtype=np.float64)
                if np.linalg.norm(tail) > 1e-9:
                    body.position = jp + tail * 0.5

        height: dict[int, int] = {}

        def h(bi: int) -> int:
            if bi not in height:
                height[bi] = 1 + max((h(c) for c in children.get(bi, [])), default=-1)
            return height[bi]

        singles = 0
        for bi in dyn:
            body = self.bodies[bi]
            body.mass = V7_MASS_TIP * V7_MASS_RATIO ** h(bi)
            body.linear_damping = 1.0
            body.angular_damping = 1.0
            joint = incoming.get(bi)
            if joint is None:
                continue
            joint.rotation = np.zeros(3)
            joint.spring_move = np.zeros(3)
            joint.move_lo = np.zeros(3)
            joint.move_hi = np.zeros(3)
            is_root = self.bodies[joint.body_a].mode != MODE_PHYSICS_BONE
            if is_root and not children.get(bi):
                lever2 = float(np.sum((np.asarray(body.position, dtype=np.float64)
                                       - np.asarray(joint.position, dtype=np.float64)) ** 2))
                inertia = body.mass * (lever2 + 0.4 * float(body.size[0]) ** 2)
                k = V7_SINGLE_GAIN * inertia / V7_MMD_DT ** 2
                lim = np.radians(V7_SINGLE_LIMIT_DEG)
                joint.spring_rot = np.array([k, k, k])
                joint.rot_lo = np.array([-lim, -lim, -lim])
                joint.rot_hi = np.array([lim, lim, lim])
                body.no_collide = MASK_COLLIDE_NONE
                singles += 1
                continue
            joint.spring_rot = np.zeros(3)
            joint.rot_lo = np.zeros(3)
            joint.rot_hi = np.zeros(3)
            body.no_collide = MASK_COLLIDE_ALL_BUT_SELF
            worst = min(
                (float(np.linalg.norm(self.bodies[k].position - body.position)
                       - float(self.bodies[k].size[0]) - float(body.size[0]))
                 for k in self._collider_indices),
                default=0.0,
            )
            if worst < -1e-6:
                body.no_collide = MASK_COLLIDE_NONE

        self.warnings.append(
            f"通用物理 v7：{len(dyn)} 个动态刚体；多节链关节锁定，质量自末端 {V7_MASS_TIP} 起每节 ×{V7_MASS_RATIO:g}；"
            f"{singles} 根单段链用 MMD 实测不抖的弹簧（每步增益 {V7_SINGLE_GAIN}）；阻尼 1.0，重力保持默认即可"
        )

    # ----------------------------------------------------- universal preset v8
    def _apply_universal_v8(self):
        """v7 + every chain of >= V8_MIN_SEGMENTS gets the sway it has in VRM.

        1. Apply v7 (bodies at mid-segment, locked multi-body chains, clean
           single-chain springs, damping 1).
        2. Run the VRM SpringBone reference on the standard test motion and
           take each chain's tip sway as the target.
        3. Invert V8_SWAY_FIT for the root->tip mass ratio that gives
           V8_TARGET_SCALE x that sway; clamp to V8_RATIO_RANGE.
        4. A chain that stays below half its target even at the softest ratio
           is unlocked to +-V8_SOFT_LIMIT_DEG with a spring at the clean gain.
        """
        from .spring_ref import tip_sway

        self._apply_universal_v7()
        dyn = [i for i, r in enumerate(self.bodies) if r.mode == MODE_PHYSICS_BONE]
        dyn_set = set(dyn)
        incoming = {j.body_b: j for j in self.joints if j.body_b in dyn_set}
        children: dict[int, list[int]] = {}
        for j in self.joints:
            if j.body_a in dyn_set and j.body_b in dyn_set:
                children.setdefault(j.body_a, []).append(j.body_b)
        roots = [bi for bi in dyn if bi in incoming
                 and self.bodies[incoming[bi].body_a].mode != MODE_PHYSICS_BONE]

        bone_to_node = {b: n for n, b in self.skel.node_to_bone.items()}
        head = self.skel.by_name.get("頭")
        pivot = (self.skel.bones[head].pos if head is not None
                 else np.array([0.0, 15.0, 0.0]))
        try:
            vrm_sway = tip_sway(self.vrm, self.pose_rot, self.bodies, bone_to_node, self.scale, pivot,
                                follow_mode=MODE_FOLLOW)
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"通用物理 v10：VRM 参考模拟失败（{e}），保持 v7 参数")
            return

        c0, c1, c2, c3, c4, c5 = V8_SWAY_FIT
        tuned, softened, ratios, head_chains, singles = 0, 0, [], 0, 0
        for root in roots:
            members, stack, depth = [], [(root, 0)], {}
            while stack:
                bi, d = stack.pop()
                members.append(bi)
                depth[bi] = d
                stack.extend((c, d + 1) for c in children.get(bi, []))
            leaf = max(members, key=lambda b: depth[b])
            n = depth[leaf] + 1
            if n == 1:
                j = incoming[root]
                body = self.bodies[root]
                if V12_LOCK_SINGLES:
                    # v12: single-bone chains locked instead of the v7 spring
                    j.spring_rot = np.zeros(3)
                    j.rot_lo = np.zeros(3)
                    j.rot_hi = np.zeros(3)
                else:
                    self._single_spring(body, j)
                singles += 1
                continue
            if n < V8_MIN_SEGMENTS or leaf not in vrm_sway:
                continue
            # chain length: root joint -> ... -> leaf body, through the body centres
            length, bi = 0.0, leaf
            while True:
                j = incoming[bi]
                a = j.body_a
                if a in dyn_set:
                    length += float(np.linalg.norm(self.bodies[bi].position - self.bodies[a].position))
                    bi = a
                else:
                    length += float(np.linalg.norm(self.bodies[bi].position - np.asarray(j.position)))
                    break
            radius = float(np.mean([self.bodies[b].size[0] for b in members]))
            target = max(V8_TARGET_SCALE * vrm_sway[leaf], 1e-4)
            base = c0 + c2 * n + c3 * np.log(max(radius, 1e-3)) + c4 * np.log(max(length, 1e-3))
            slope = c1 + c5 * n
            lr = (np.log(target) - base) / slope if slope < 0 else 0.0
            ratio = float(np.clip(np.exp(lr), *V8_RATIO_RANGE))
            if V8_PLACEMENT == "joint":
                j0, j1, j2, j3, j4 = V8_SWAY_FIT_JOINT
                jbase = j0 + j2 * n + j3 * np.log(max(radius, 1e-3)) + j4 * np.log(max(length, 1e-3))
                ratio = float(np.clip(1.0 + (np.log(target) - jbase) / (j1 * n), *V8_RATIO_RANGE))
            if self.softness != 1.0:
                # softness acts on the mass ratio directly: the sway fit is too coarse for
                # tails, which sit on the 1.3 floor.  Harness: tail sway ~x0.72 at (ratio-1)x2,
                # ~x0.47 at x3.3, so (ratio-1) scales with softness^-2.
                lo = V8_RATIO_RANGE[0] if self.softness < 1.0 else 1.0
                ratio = float(np.clip(1.0 + (ratio - 1.0) * self.softness ** -2, lo, V8_RATIO_RANGE[1]))
            if V8_FORCE_RATIO is not None:
                ratio = float(V8_FORCE_RATIO)
            pred_soft = np.exp(base + slope * np.log(V8_SOFTEST_LOCKED_RATIO))
            height = {b: 0 for b in members}
            for b in sorted(members, key=lambda b: -depth[b]):
                kids = [c for c in children.get(b, [])]
                if kids:
                    height[b] = 1 + max(height[c] for c in kids)
            if pred_soft < 0.5 * target:
                if V10_SOFT_AS_LOCKED:
                    # lock the chain, bodies on their joints, radius sized for the target sway
                    r_new = V10_SOFT_REF_RADIUS * (V10_SOFT_REF_SWAY / target) ** (1.0 / V10_SOFT_RADIUS_EXP)
                    # v12: harder by V12_SOFT_HARDNESS through a larger radius; what the
                    # original radius cannot give goes into the mass ratio
                    r_old = min(float(self.bodies[b].size[0]) for b in members)
                    r_v11 = float(np.clip(r_new, 0.15, r_old))
                    r_new *= V12_SOFT_HARDNESS ** (1.0 / V12_SOFT_RADIUS_EXP)
                    given = (float(np.clip(r_new, 0.15, r_old)) / r_v11) ** V12_SOFT_RADIUS_EXP
                    q = max(V12_SOFT_HARDNESS / given, 1.0) ** (1.0 / V12_RATIO_EXP)
                    for b in members:
                        body = self.bodies[b]
                        body.mass = V7_MASS_TIP * q ** height[b]
                        j = incoming[b]
                        j.spring_rot = np.zeros(3)
                        j.rot_lo = np.zeros(3)
                        j.rot_hi = np.zeros(3)
                        body.position = np.asarray(j.position, dtype=np.float64).copy()
                        body.size = np.full(3, float(np.clip(r_new, 0.15, float(body.size[0]))))
                    softened += 1
                    continue
                # (v9 / v10.0) too thick/short to sway while locked: open the joints a little
                lim = np.radians(V8_SOFT_LIMIT_DEG)
                for b in members:
                    body = self.bodies[b]
                    body.mass = V7_MASS_TIP
                    j = incoming[b]
                    if V8_SOFT_ROOT_ONLY and b != root:
                        j.spring_rot = np.zeros(3)
                        j.rot_lo = np.zeros(3)
                        j.rot_hi = np.zeros(3)
                        continue
                    lever2 = float(np.sum((np.asarray(body.position) - np.asarray(j.position)) ** 2))
                    k = V7_SINGLE_GAIN * body.mass * (lever2 + 0.4 * float(body.size[0]) ** 2) / V7_MMD_DT ** 2
                    j.spring_rot = np.array([k, k, k])
                    j.rot_lo = np.array([-lim, -lim, -lim])
                    j.rot_hi = np.array([lim, lim, lim])
                softened += 1
                continue
            if self._under_head(self.bodies[incoming[root].body_a].bone):
                # v12: hair / whiskers V12_HEAD_HARDNESS x harder
                ratio = float(min(ratio * V12_HEAD_HARDNESS ** (1.0 / V12_RATIO_EXP), V12_HEAD_RATIO_MAX))
                head_chains += 1
            for b in members:
                self.bodies[b].mass = V7_MASS_TIP * ratio ** height[b]
            ratios.append(ratio)
            tuned += 1
        moved = 0
        if V8_PLACEMENT == "joint":
            # every chain whose joints are all locked: bodies onto their joints
            for root in roots:
                members, stack = [], [root]
                while stack:
                    bi = stack.pop()
                    members.append(bi)
                    stack.extend(children.get(bi, []))
                if len(members) < 2:
                    continue
                if any(np.any(np.abs(incoming[b].rot_hi) > 1e-9) for b in members):
                    continue
                for b in members:
                    self.bodies[b].position = np.asarray(incoming[b].position, dtype=np.float64).copy()
                moved += 1
        # v11: bodies moved onto their joints / shrunk above can start inside a
        # body collider (model A tail segment 2 in spine_2 by 0.6, model B tail root
        # in chest_0 by 0.4).  Apply v7's rule again to the final placement:
        # a body that starts inside a collider does not collide.  Only ever
        # switches collisions off, so chains v7 already excluded stay excluded.
        inside = 0
        for bi in dyn:
            body = self.bodies[bi]
            if body.no_collide == MASK_COLLIDE_NONE:
                continue
            worst = min(
                (float(np.linalg.norm(self.bodies[k].position - body.position)
                       - float(self.bodies[k].size[0]) - float(body.size[0]))
                 for k in self._collider_indices),
                default=0.0,
            )
            if worst < -1e-6:
                body.no_collide = MASK_COLLIDE_NONE
                inside += 1
        # v11: lock at +V11_LOCK_EPS instead of exactly 0 (see V11_LOCK_EPS).
        relocked = self._lock_at_eps()
        rt = f"{min(ratios):.2f}~{max(ratios):.2f}" if ratios else "无"
        self.warnings.append(
            f"通用物理 v14（柔软度 {self.softness:g}）：按 VRM 参考摆幅逐链调整 {tuned} 条链的质量梯度（{rt}），"
            + f"其中头部 {head_chains} 条再硬 {V12_HEAD_HARDNESS:g} 倍，"
            + ((f"{singles} 根单段链锁定，" if V12_LOCK_SINGLES else
                f"{singles} 根单段链用弹簧（每步增益 {V13_SINGLE_GAIN:g}、±{V13_SINGLE_LIMIT_DEG:g}°、物理半径 ×{V13_SINGLE_RADIUS_SCALE:g}），")
               if singles else "")
            + f"粗短链再硬 {V12_SOFT_HARDNESS:g} 倍；"
            + (f"{softened} 条粗短链锁定并缩小物理半径" if V10_SOFT_AS_LOCKED else
               f"{softened} 条粗短链改为{'根关节' if V8_SOFT_ROOT_ONLY else ''} ±{V8_SOFT_LIMIT_DEG:g}° 弱弹簧")
            + (f"；{moved} 条锁定链的刚体放在关节处" if moved else "")
            + f"；{relocked} 个锁定轴改用 {V11_LOCK_EPS:g} 的上下限（防止 Bullet 在角度恰为 0 时放开该轴）"
            + (f"；{inside} 个刚体初始嵌在碰撞体里，已取消其碰撞" if inside else "")
        )

    # ------------------------------------------------------------------ helper
    def _single_spring(self, body, joint):
        """v13/v14 single-bone strand: wide limit, soft spring, large sphere, no collisions."""
        body.size = np.asarray(body.size, dtype=np.float64) * V13_SINGLE_RADIUS_SCALE
        lever2 = float(np.sum((np.asarray(body.position, dtype=np.float64)
                               - np.asarray(joint.position, dtype=np.float64)) ** 2))
        inertia = body.mass * (lever2 + 0.4 * float(body.size[0]) ** 2)
        k = V13_SINGLE_GAIN * inertia / V7_MMD_DT ** 2
        lim = np.radians(V13_SINGLE_LIMIT_DEG)
        joint.spring_rot = np.array([k, k, k])
        joint.rot_lo = np.array([-lim, -lim, -lim])
        joint.rot_hi = np.array([lim, lim, lim])
        body.no_collide = MASK_COLLIDE_NONE

    def _final_singles_v14(self) -> int:
        """Diagnostic (final_v14singles): final's single-bone strands get the v14 spring."""
        dyn = {i for i, b in enumerate(self.bodies) if b.mode == MODE_PHYSICS_BONE}
        has_child = {j.body_a for j in self.joints if j.body_a in dyn and j.body_b in dyn}
        n = 0
        for j in self.joints:
            b = j.body_b
            if b in dyn and j.body_a not in dyn and b not in has_child:
                self._single_spring(self.bodies[b], j)
                n += 1
        return n

    def _under_head(self, bone: int) -> bool:
        """True when `bone` is the head bone (頭) or one of its descendants."""
        head = self.skel.by_name.get("頭")
        if head is None:
            return False
        while bone is not None and bone >= 0:
            if bone == head:
                return True
            bone = self.skel.bones[bone].parent
        return False

    def _lock_at_eps(self) -> int:
        """Every locked axis (lower == upper == 0) of a joint into a dynamic body
        gets lower = upper = V11_LOCK_EPS.  Returns the number of axes changed."""
        changed = 0
        for joint in self.joints:
            if self.bodies[joint.body_b].mode == MODE_FOLLOW:
                continue
            for name_lo, name_hi in (("rot_lo", "rot_hi"), ("move_lo", "move_hi")):
                lo = np.array(getattr(joint, name_lo), dtype=np.float64)
                hi = np.array(getattr(joint, name_hi), dtype=np.float64)
                hit = (lo == 0.0) & (hi == 0.0)
                if hit.any():
                    lo[hit] = hi[hit] = V11_LOCK_EPS
                    setattr(joint, name_lo, lo)
                    setattr(joint, name_hi, hi)
                    changed += int(hit.sum())
        return changed

    def _first_child(self, node: int, allowed: set[int], skel) -> int | None:
        for c in self.vrm.nodes[node].get("children", []):
            if c in skel.node_to_bone and c in allowed:
                return c
        return None


def build_physics(vrm, skel, scale: float, strength: float = 1.0, preset: str = "default",
                  pose_rot: dict[int, np.ndarray] | None = None, softness: float | None = None):
    return PhysicsBuilder(vrm, skel, scale, strength, preset, pose_rot, softness).build()
