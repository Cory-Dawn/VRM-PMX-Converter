# -*- coding: utf-8 -*-
"""Humanoid → MMD standard skeleton.

The tricky part is that MMD's left/right convention is the mirror of the
glTF convention: a bone sitting at negative X is called "右" in MMD but
"left" in VRM. That is a coordinate-system handedness difference, not a bug,
so the mapping table below swaps L/R on purpose.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .pmx import PMXBone

# MMD bone flags
F_TAIL_INDEX = 0x0001
F_ROT = 0x0002
F_MOVE = 0x0004
F_VISIBLE = 0x0008
F_ENABLED = 0x0010
F_IK = 0x0020
F_LOCAL_ADD = 0x0080
F_ROT_ADD = 0x0100
F_MOVE_ADD = 0x0200
F_AXIS = 0x0400
F_LOCAL_AXIS = 0x0800
F_AFTER_PHYS = 0x1000
F_EXT_PARENT = 0x2000

DEFAULT_FLAGS = F_ROT | F_VISIBLE | F_ENABLED


def side(name: str, is_left: bool) -> str:
    """After the X mirror a VRM `.L` bone sits on +X, which is exactly where
    MMD puts its 「左」 bones, so the side label carries over unchanged."""
    return ("左" if is_left else "右") + name


# VRM humanoid bone -> (mmd name builder, is_left)
# is_left means "this vrm bone is a .L bone", which becomes 右 in MMD.
HUMANOID_MAP: dict[str, tuple[str, bool]] = {
    # hips has no bone of its own: its weights and children go to 下半身, and a
    # separate semi-standard 腰 (グルーブ → 腰 → 上半身 / 下半身) is added.
    "hips": ("下半身", False),
    "spine": ("上半身", False),
    "chest": ("上半身2", False),
    "upperChest": ("上半身3", False),
    "neck": ("首", False),
    "head": ("頭", False),
    "leftShoulder": ("肩", True),
    "rightShoulder": ("肩", False),
    "leftUpperArm": ("腕", True),
    "rightUpperArm": ("腕", False),
    "leftLowerArm": ("ひじ", True),
    "rightLowerArm": ("ひじ", False),
    "leftHand": ("手首", True),
    "rightHand": ("手首", False),
    "leftUpperLeg": ("足", True),
    "rightUpperLeg": ("足", False),
    "leftLowerLeg": ("ひざ", True),
    "rightLowerLeg": ("ひざ", False),
    "leftFoot": ("足首", True),
    "rightFoot": ("足首", False),
    "leftToes": ("つま先", True),
    "rightToes": ("つま先", False),
    "leftEye": ("目", True),
    "rightEye": ("目", False),
}

# fingers: vrm suffix -> mmd name, index depends on how many segments exist
FINGER_MAP = {
    "Thumb": "親指",
    "Index": "人指",
    "Middle": "中指",
    "Ring": "薬指",
    "Little": "小指",
}
FINGER_SEG = {
    "ThumbProximal": 0, "ThumbIntermediate": 1, "ThumbDistal": 2,
    "IndexProximal": 1, "IndexIntermediate": 2, "IndexDistal": 3,
    "MiddleProximal": 1, "MiddleIntermediate": 2, "MiddleDistal": 3,
    "RingProximal": 1, "RingIntermediate": 2, "RingDistal": 3,
    "LittleProximal": 1, "LittleIntermediate": 2, "LittleDistal": 3,
}
FULLWIDTH = "０１２３４５６７８９"

# English names of the standard / semi-standard bones (as in common MMD models);
# side bones get "_L" / "_R".  Other bones keep their VRM node name.
STD_EN = {
    "操作中心": "view cnt", "全ての親": "master", "センター": "center", "グルーブ": "groove",
    "腰": "waist", "上半身": "upper body", "上半身2": "upper body2", "上半身3": "upper body3",
    "首": "neck", "頭": "head", "両目": "eyes", "下半身": "lower body",
}
SIDE_EN = {
    "目": "eye", "肩P": "shoulderP", "肩": "shoulder", "肩C": "shoulderC", "腕": "arm",
    "腕捩": "arm twist", "ひじ": "elbow", "手捩": "wrist twist", "手首": "wrist", "ダミー": "dummy",
    "足": "leg", "ひざ": "knee", "足首": "ankle", "つま先": "toe", "足IK親": "leg IKP",
    "足ＩＫ": "leg IK", "つま先ＩＫ": "toe IK", "足D": "leg D", "ひざD": "knee D",
    "足首D": "ankle D", "足先EX": "toe EX",
    "親指": "thumb", "人指": "fore", "中指": "middle", "薬指": "third", "小指": "little",
}


def english_name(name: str) -> str:
    if name in STD_EN:
        return STD_EN[name]
    if name.startswith("腰キャンセル") and name[-1:] in "左右":
        return "waist cancel_" + ("L" if name[-1] == "左" else "R")
    if name[:1] in "左右":
        core, sfx = name[1:], "_L" if name[0] == "左" else "_R"
        digit = ""
        if core[-1:] in FULLWIDTH:
            digit, core = str(FULLWIDTH.index(core[-1])), core[:-1]
        if core in SIDE_EN:
            return SIDE_EN[core] + digit + sfx
    return name if name.isascii() else ""


@dataclass
class BoneBuild:
    name: str
    pos: np.ndarray
    parent: int = -1
    layer: int = 0
    flags: int = DEFAULT_FLAGS
    tail_bone: int = 0
    tail_offset: np.ndarray = field(default_factory=lambda: np.zeros(3))
    inherit_parent: int = -1
    inherit_weight: float = 0.0
    fixed_axis: np.ndarray = field(default_factory=lambda: np.zeros(3))
    ik_target: int = -1
    ik_loop: int = 0
    ik_limit: float = 0.0
    ik_links: list = field(default_factory=list)
    source_node: int = -1
    kind: str = "vrm"           # vrm / std / helper

    def to_pmx(self) -> PMXBone:
        b = PMXBone()
        b.name = self.name
        b.name_en = english_name(self.name)
        b.position = self.pos
        b.parent = self.parent
        b.layer = self.layer
        b.flags = self.flags
        b.tail_bone = self.tail_bone
        b.tail_offset = self.tail_offset
        b.inherit_parent = self.inherit_parent
        b.inherit_weight = self.inherit_weight
        b.fixed_axis = self.fixed_axis
        b.ik_target = self.ik_target
        b.ik_loop = self.ik_loop
        b.ik_limit = self.ik_limit
        b.ik_links = self.ik_links
        return b


class SkeletonBuilder:
    def __init__(self, vrm, scale: float):
        self.vrm = vrm
        self.scale = scale
        self.bones: list[BoneBuild] = []
        self.by_name: dict[str, int] = {}
        self.node_to_bone: dict[int, int] = {}
        self.warnings: list[str] = []
        # Names survive the final index reorder; used to distribute skin weights.
        self.twist_chains: list[tuple[str, str, str, tuple[str, ...]]] = []
        # Names are resolved after reordering and optional adjustment controls.
        self.leg_weight_chains: list[tuple[str, ...]] = []
        self.adjustment_pairs: list[tuple[str, str]] = []
        self.adjustment_offset = np.zeros(3)

    # ------------------------------------------------------------------ build
    def build(self) -> "SkeletonBuilder":
        v = self.vrm
        w = v.world
        S = self.scale

        def wp(node: int) -> np.ndarray:
            return (w[node][:3, 3] * S).astype(np.float64)

        # ---- 1. MMD root bones
        self._add("操作中心", np.zeros(3), -1, kind="std", flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED)
        self._add("全ての親", np.zeros(3), -1, kind="std", flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED)

        # ---- 2. rename humanoid bones
        rename: dict[int, tuple[str, str]] = {}
        for vrm_name, (mmd_base, is_left) in HUMANOID_MAP.items():
            if vrm_name not in v.humanoid:
                continue
            node = v.humanoid[vrm_name]
            mmd_name = side(mmd_base, is_left) if is_left or vrm_name.startswith(("left", "right")) else mmd_base
            if vrm_name in ("hips", "spine", "chest", "upperChest", "neck", "head"):
                mmd_name = mmd_base
            rename[node] = (mmd_name, vrm_name)

        # fingers
        for vrm_name, node in v.humanoid.items():
            if not vrm_name.startswith(("left", "right")):
                continue
            is_left = vrm_name.startswith("left")
            core = vrm_name[4:] if is_left else vrm_name[5:]
            for key, mmd in FINGER_MAP.items():
                if core.startswith(key) and core in FINGER_SEG:
                    idx = FINGER_SEG[core]
                    rename[node] = (side(mmd + FULLWIDTH[idx], is_left), vrm_name)
                    break

        # ---- 3. every node becomes a bone; mesh holders are kept as the
        # grouping bones MMD users expect (身体 / 毛发 / 耳朵 ...).
        candidate = list(range(len(v.nodes)))

        # topology order: parents before children
        order = self._topo_order(candidate)

        # ---- 4. standard torso bones are created explicitly; VRM bones that
        # map onto them ARE them (rename), everything else is copied.
        std_positions = {}
        for vrm_name in ("hips", "spine", "chest", "upperChest", "neck", "head"):
            if vrm_name in v.humanoid:
                std_positions[vrm_name] = wp(v.humanoid[vrm_name])

        spine_pos = std_positions.get("spine", std_positions.get("hips", np.zeros(3)))
        hips_pos = std_positions.get("hips", spine_pos)
        head_pos = std_positions.get("head", spine_pos + np.array([0, 4 * S, 0]))
        knee = None
        for vrm_name in ("leftLowerLeg", "rightLowerLeg"):
            if vrm_name in v.humanoid:
                knee = wp(v.humanoid[vrm_name])
                break
        center_y = spine_pos[1] - (spine_pos[1] - knee[1]) * 0.4423 if knee is not None else spine_pos[1] * 0.75
        center = np.array([0.0, center_y, spine_pos[2] * 0.5])
        groove = np.array([0.0, center_y + (spine_pos[1] - center_y) * 0.081, center[2]])

        center_i = self._add("センター", center, self.by_name["全ての親"], kind="std",
                             flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED)
        groove_i = self._add("グルーブ", groove, center_i, kind="std",
                             flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED)
        # Semi-standard 腰: parent of 上半身 and 下半身, half-way between the
        # waist (上半身 / 下半身 origin) and the leg roots.  Motions that key 腰
        # turn the whole body around it, as on standard MMD models.
        legs_y = [wp(v.humanoid[k])[1] for k in ("leftUpperLeg", "rightUpperLeg") if k in v.humanoid]
        waist_y = (spine_pos[1] + float(np.mean(legs_y))) * 0.5 if legs_y else (spine_pos[1] + hips_pos[1]) * 0.5
        waist_i = self._add("腰", np.array([0.0, waist_y, spine_pos[2]]), groove_i, kind="std")
        # 下半身 carries the VRM hips weights (pivot at the waist, MMD standard)
        lower_i = self._add("下半身", spine_pos.copy(), waist_i, kind="std")

        # ---- 5. VRM bones
        hips_node = v.humanoid.get("hips")
        for node in order:
            if node == hips_node:
                # the VRM hips node IS 下半身 (weights, children, physics anchors)
                self.node_to_bone[node] = lower_i
                self.bones[lower_i].source_node = node
                continue
            name, _ = rename.get(node, (v.node_names[node], ""))
            name = self._unique_name(name, node)
            flags = F_ROT | F_VISIBLE | F_ENABLED   # 上半身 too: rotation only, as in MMD
            parent_bone = self._parent_bone(node, rename)
            self._add(name, wp(node), parent_bone, kind="vrm", source_node=node, flags=flags)

        # ---- 6. rewire the torso chain to the MMD standard
        self._rewire_torso(rename, waist_i, lower_i)

        # ---- 7. append MMD helpers
        self._add_arm_helpers(rename)
        self._add_leg_helpers(rename)
        self._add_eyes(rename, head_pos)

        # ---- 8. tail offsets (cosmetic, keeps PMX viewer happy)
        self._fill_tails()

        # Both-eyes control is displayed above the head and points forward
        # (MMD front is negative Z); it is not a child of either eye.
        if "両目" in self.by_name:
            self.bones[self.by_name["両目"]].tail_offset = np.array([0.0, 0.0, -0.04 * S])

        # Match the conventional PMX display: the arm points to its joint,
        # while twist controls have a zero display offset and a fixed axis.
        for root, end, twist, helpers in self.twist_chains:
            root_bone = self.bones[self.by_name[root]]
            root_bone.flags |= F_TAIL_INDEX
            root_bone.tail_bone = self.by_name[end]
            for name in (twist, *helpers):
                self.bones[self.by_name[name]].tail_offset = np.zeros(3)

        # ---- 9. MMD evaluates bone deformation in *index* order, so a child
        # sitting before its parent gets computed with a stale parent transform
        # and the mesh collapses. Sorting by depth (and keeping standard bones
        # ahead of helpers at the same depth) guarantees parents come first.
        self.reorder()

        return self

    # ---------------------------------------------------------------- reorder
    def reorder(self):
        """Sort bones so every parent precedes its children, then remap every
        index that refers to a bone (parent / inherit / IK target / IK links /
        node-to-bone map)."""
        n = len(self.bones)
        if n == 0:
            return
        depth = []
        for b in self.bones:
            d, cur, guard = 0, b.parent, 0
            while cur >= 0 and guard < 500:
                d += 1
                cur = self.bones[cur].parent
                guard += 1
            depth.append(d)
        kind_rank = {"std": 0, "vrm": 1, "helper": 2}
        order = sorted(range(n), key=lambda i: (depth[i], kind_rank.get(self.bones[i].kind, 3), i))
        self._apply_order(order)

    def _apply_order(self, order):
        """Apply an index permutation and remap all skeleton references."""
        old2new = {old: new for new, old in enumerate(order)}
        self.bones = [self.bones[i] for i in order]
        for b in self.bones:
            if b.parent >= 0:
                b.parent = old2new[b.parent]
            if b.inherit_parent >= 0:
                b.inherit_parent = old2new[b.inherit_parent]
            if b.ik_target >= 0:
                b.ik_target = old2new[b.ik_target]
            if b.ik_links:
                b.ik_links = [(old2new.get(l[0], -1), l[1], l[2], l[3]) for l in b.ik_links]
            if b.tail_bone >= 0:
                b.tail_bone = old2new.get(b.tail_bone, b.tail_bone)
        self.node_to_bone = {node: old2new[idx] for node, idx in self.node_to_bone.items()}
        self.by_name = {b.name: i for i, b in enumerate(self.bones)}

    def add_adjustment_bones(self, offset=(-20.0, 0.0, 0.0)):
        """Add an offset control rig and let selected MMD bones inherit from it."""
        offset = np.asarray(offset, dtype=np.float64)
        if offset.shape != (3,) or not np.all(np.isfinite(offset)):
            raise ValueError("调整骨位置必须是三个有限数值")
        names = ("センター", "グルーブ", "左足ＩＫ", "右足ＩＫ", "左つま先ＩＫ", "右つま先ＩＫ",
                 "下半身", "上半身", "上半身2", "首", "左肩", "右肩", "頭", "両目",
                 "左腕", "右腕", "左腕捩", "右腕捩", "左ひじ", "右ひじ", "左手捩", "右手捩",
                 "左手首", "右手首", "左足", "右足", "左ひざ", "右ひざ", "左足首", "右足首")
        master = self.by_name.get("全ての親")
        if master is None or "調整ボーン親" in self.by_name:
            self.warnings.append("无法创建调整骨：缺少全ての親或存在同名调整骨")
            return []
        eligible = []
        for name in names:
            i = self.by_name.get(name)
            if i is None:
                continue
            bone = self.bones[i]
            if name + "調整" in self.by_name or bone.flags & (F_ROT_ADD | F_MOVE_ADD):
                self.warnings.append(f"{name} 已有同名调整骨或附加变换，已跳过")
                continue
            eligible.append(i)
        d_indices = set()
        for chain in self.leg_weight_chains:
            for name in chain[4:7]:
                i = self.by_name[name]
                if name + "調整" in self.by_name or name + "調整付与" in self.by_name:
                    self.warnings.append(f"{name} 已有同名调整骨或辅助骨，已跳过")
                    continue
                eligible.append(i)
                d_indices.add(i)
        if not eligible:
            self.warnings.append("没有可添加调整骨的目标骨骼")
            return []

        old_count = len(self.bones)
        self.adjustment_offset = offset.copy()
        root = self._add("調整ボーン親", self.bones[master].pos + offset, master, kind="helper",
                         flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED)
        self.adjustment_pairs.append((self.bones[master].name, "調整ボーン親"))
        adjustment_for = {}
        drivers = {}
        created_names = ["調整ボーン親"]
        # Original bones are already in parent-before-child order.
        for i in sorted(eligible):
            original = self.bones[i]
            parent = original.parent
            visited = set()
            while parent >= 0 and parent not in adjustment_for and parent not in visited:
                visited.add(parent)
                parent = self.bones[parent].parent
            adjust_parent = adjustment_for.get(parent, root)
            flags = original.flags & (F_ROT | F_MOVE | F_AXIS)
            flags |= F_VISIBLE | F_ENABLED
            name = original.name + "調整"
            created_names.append(name)
            self.adjustment_pairs.append((original.name, name))
            adjustment_for[i] = self._add(name, original.pos + offset, adjust_parent,
                                          kind="helper", flags=flags, layer=original.layer,
                                          fixed_axis=original.fixed_axis.copy(),
                                          tail_offset=original.tail_offset.copy())
            if i in d_indices:
                # D already inherits the real joint. A same-pivot intermediary
                # adds the adjustment without replacing that existing grant.
                driver = self._add(original.name + "調整付与", original.pos, original.parent,
                                   kind="helper", layer=original.layer,
                                   flags=F_ROT | F_ENABLED | F_ROT_ADD,
                                   inherit_parent=adjustment_for[i], inherit_weight=1.0)
                drivers[i] = driver
                original.parent = driver
                continue
            if original.flags & F_ROT:
                original.flags |= F_ROT_ADD
            if original.flags & F_MOVE:
                original.flags |= F_MOVE_ADD
            original.inherit_parent = adjustment_for[i]
            original.inherit_weight = 1.0

        for i, adjust_i in adjustment_for.items():
            original, adjust = self.bones[i], self.bones[adjust_i]
            if original.flags & F_TAIL_INDEX:
                if original.tail_bone in adjustment_for:
                    adjust.flags |= F_TAIL_INDEX
                    adjust.tail_bone = adjustment_for[original.tail_bone]
                elif 0 <= original.tail_bone < old_count:
                    adjust.tail_offset = self.bones[original.tail_bone].pos - original.pos

        # All controls must be evaluated before the bones inheriting from them.
        # The original master is already before every selected target.
        driver_set = set(drivers.values())
        controls = [i for i in range(old_count, len(self.bones)) if i not in driver_set]
        original_order = []
        for i in range(master + 1, old_count):
            if i in drivers:
                original_order.append(drivers[i])
            original_order.append(i)
        order = list(range(master + 1)) + controls + original_order
        self._apply_order(order)
        return created_names

    # ------------------------------------------------------------------ utils
    def _add(self, name: str, pos, parent: int, kind: str = "std",
             flags: int = DEFAULT_FLAGS, **kw) -> int:
        b = BoneBuild(name=name, pos=np.asarray(pos, dtype=np.float64).copy(),
                      parent=parent, kind=kind, flags=flags, **kw)
        idx = len(self.bones)
        self.bones.append(b)
        self.by_name[name] = idx
        if b.source_node >= 0:
            self.node_to_bone[b.source_node] = idx
        return idx

    def _unique_name(self, name: str, node: int) -> str:
        if name not in self.by_name:
            return name
        # duplicate names inside VRM (e.g. two nodes called "spine")
        n = 2
        while f"{name}.{n:03d}" in self.by_name:
            n += 1
        return f"{name}.{n:03d}"

    def _topo_order(self, nodes: list[int]) -> list[int]:
        parent = {}
        for i, nd in enumerate(self.vrm.nodes):
            for c in nd.get("children", []):
                parent[c] = i
        nodeset = set(nodes)
        depth = {}
        for n in nodes:
            d, cur = 0, n
            while cur in parent and d < 512:
                cur = parent[cur]
                d += 1
            depth[n] = d
        return sorted(nodes, key=lambda x: (depth[x], x))

    def _parent_bone(self, node: int, rename) -> int:
        """Walk up the glTF tree until a node that already became a bone."""
        v = self.vrm
        parents = {}
        for i, nd in enumerate(v.nodes):
            for c in nd.get("children", []):
                parents[c] = i
        cur = parents.get(node)
        guard = 0
        while cur is not None and guard < 512:
            if cur in self.node_to_bone:
                return self.node_to_bone[cur]
            cur = parents.get(cur)
            guard += 1
        return self.by_name["全ての親"]

    def _rewire_torso(self, rename, waist_i: int, lower_i: int):
        """グルーブ → 腰 → 上半身 → 上半身2 → …,  腰 → 下半身 → legs / hips children."""
        v = self.vrm
        spine = v.humanoid.get("spine")
        upper = self.node_to_bone.get(spine, -1) if spine is not None else -1
        if upper < 0 or upper == lower_i:
            return
        self.bones[upper].parent = waist_i
        # Many motions key 上半身2.  A VRM without chest gets an empty 上半身2
        # between 上半身 and everything that hangs from it (arms, neck, ...).
        if "chest" not in v.humanoid and "上半身2" not in self.by_name:
            kids = [i for i, b in enumerate(self.bones) if b.parent == upper]
            nxt = None
            for key in ("upperChest", "neck"):
                n = v.humanoid.get(key)
                if n is not None and self.node_to_bone.get(n, -1) in kids:
                    nxt = self.node_to_bone[n]
                    break
            up = self.bones[upper].pos
            pos = (up + self.bones[nxt].pos) * 0.5 if nxt is not None else up + np.array([0.0, 0.5 * self.scale / 14.0, 0.0])
            u2 = self._add("上半身2", pos, upper, kind="std")
            for k in kids:
                self.bones[k].parent = u2

    # ------------------------------------------------------------------ arms
    def _add_arm_helpers(self, rename):
        """Standard MMD arm chain:

            上半身3 → 肩P → 肩 → 肩C → 腕 → 腕捩 → ひじ → 手捩 → 手首 → ダミー

        肩C carries a negative inheritance from 肩P so that rotating the
        pass-through bone does not move the arm twice.
        """
        v = self.vrm
        S = self.scale
        upper3 = -1
        for key in ("upperChest", "chest", "spine"):
            upper3 = self.node_to_bone.get(v.humanoid.get(key, -1), -1)
            if upper3 >= 0:
                if key == "spine" and "上半身2" in self.by_name:
                    upper3 = self.by_name["上半身2"]   # the empty 上半身2 added for chest-less VRMs
                break

        for is_left in (True, False):
            prefix = "left" if is_left else "right"
            ua = v.humanoid.get(prefix + "UpperArm")
            la = v.humanoid.get(prefix + "LowerArm")
            hd = v.humanoid.get(prefix + "Hand")
            sh = v.humanoid.get(prefix + "Shoulder")
            if ua is None or la is None:
                continue
            ua_i = self.node_to_bone.get(ua)
            la_i = self.node_to_bone.get(la)
            hd_i = self.node_to_bone.get(hd) if hd is not None else -1
            if ua_i is None or la_i is None:
                continue

            p_ua = self.bones[ua_i].pos
            p_la = self.bones[la_i].pos

            # 肩P / 肩C
            sh_i = self.node_to_bone.get(sh) if sh is not None else -1
            parent_for_arm = sh_i
            if sh_i >= 0:
                p_sh = self.bones[sh_i].pos
                shp_i = self._add(side("肩P", is_left), p_sh.copy(),
                                  upper3 if upper3 >= 0 else self.by_name["上半身"], kind="helper")
                self.bones[sh_i].parent = shp_i
                shc_i = self._add(side("肩C", is_left), p_ua.copy(), sh_i, kind="helper",
                                  flags=F_ROT | F_ENABLED | F_ROT_ADD,
                                  inherit_parent=shp_i, inherit_weight=-1.0)
                parent_for_arm = shc_i
            if parent_for_arm >= 0:
                self.bones[ua_i].parent = parent_for_arm

            self._add_twist_chain(is_left, "腕捩", ua_i, la_i)

            if hd_i >= 0:
                p_hd = self.bones[hd_i].pos
                self._add_twist_chain(is_left, "手捩", la_i, hd_i)
                # ダミー: short helper past the wrist, used by hand IK rigs
                d = p_hd - p_la
                dn = np.linalg.norm(d)
                dummy_pos = p_hd + (d / dn * 0.6 * S / 14.0 if dn > 1e-9 else np.array([0, -1, 0]) * 0.6 * S / 14.0)
                self._add(side("ダミー", is_left), dummy_pos, hd_i, kind="helper")

    def _add_twist_chain(self, is_left: bool, core: str, root_i: int, end_i: int):
        """Create a fixed-axis control and three weighted rotation grants."""
        twist = side(core, is_left)
        helpers = tuple(twist + str(n) for n in (1, 2, 3))
        if any(name in self.by_name for name in (twist, *helpers)):
            self.warnings.append(f"{twist} 存在同名骨骼，跳过自动生成扭转骨，避免覆盖原骨骼")
            return
        root, end = self.bones[root_i], self.bones[end_i]
        direction = end.pos - root.pos
        length = float(np.linalg.norm(direction))
        if length <= 1e-9:
            self.warnings.append(f"{twist} 的关节位置重合，跳过自动生成扭转骨")
            return
        control = self._add(twist, root.pos + direction * 0.6, root_i, kind="helper",
                            flags=F_ROT | F_VISIBLE | F_ENABLED | F_AXIS,
                            fixed_axis=direction / length)
        for name, ratio in zip(helpers, (0.25, 0.5, 0.75)):
            self._add(name, root.pos + direction * ratio, root_i, kind="helper",
                      flags=F_ROT | F_ENABLED | F_ROT_ADD,
                      inherit_parent=control, inherit_weight=ratio)
        self.bones[end_i].parent = control
        self.twist_chains.append((root.name, end.name, twist, helpers))

    # ------------------------------------------------------------------- legs
    def _add_leg_helpers(self, rename):
        v = self.vrm
        S = self.scale
        for is_left in (True, False):
            prefix = "left" if is_left else "right"
            ul = v.humanoid.get(prefix + "UpperLeg")
            ll = v.humanoid.get(prefix + "LowerLeg")
            ft = v.humanoid.get(prefix + "Foot")
            to = v.humanoid.get(prefix + "Toes")
            if ul is None or ll is None or ft is None:
                continue
            ul_i = self.node_to_bone.get(ul)
            ll_i = self.node_to_bone.get(ll)
            ft_i = self.node_to_bone.get(ft)
            to_i = self.node_to_bone.get(to, -1) if to is not None else -1
            if None in (ul_i, ll_i, ft_i):
                continue
            p_ft = self.bones[ft_i].pos
            p_to = self.bones[to_i].pos if to_i >= 0 else p_ft + np.array([0, 0, -S])

            # 足IK親 sits on the ground plane under the ankle
            ikp_name = side("足IK親", is_left)
            ikp_i = self._add(ikp_name, np.array([p_ft[0], 0.0, p_ft[2]]),
                              self.by_name["全ての親"], kind="helper",
                              flags=F_TAIL_INDEX | F_ROT | F_MOVE | F_VISIBLE | F_ENABLED)
            ik_name = side("足ＩＫ", is_left)
            ik_i = self._add(ik_name, p_ft.copy(), ikp_i, kind="helper",
                             flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED | F_IK, layer=1,
                             ik_target=ft_i, ik_loop=40, ik_limit=2.0,
                             ik_links=[(ll_i, 1, np.array([-np.pi, 0, 0]), np.array([-0.0087, 0, 0])),
                                       (ul_i, 0, None, None)])
            # F_TAIL_INDEX must point at this side's 足ＩＫ.  The default
            # tail_bone=0 is 操作中心, which made both IK-parent bones visually
            # point at the model origin instead of their child IK.
            self.bones[ikp_i].tail_bone = ik_i
            toik_name = side("つま先ＩＫ", is_left)
            self._add(toik_name, p_to.copy(), ik_i, kind="helper",
                      flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED | F_IK, layer=1,
                      ik_target=to_i if to_i >= 0 else ft_i, ik_loop=3, ik_limit=4.0,
                      ik_links=[(ft_i, 0, None, None)])   # MMD standard: 3 loops, no limit

            # 腰キャンセル (semi-standard): between 下半身 and 足, cancels a 腰 rotation
            # for the legs (inherits 腰 with -1), so turning 腰 does not swing the legs
            leg_parent = self.bones[ul_i].parent
            wc_i = self._add("腰キャンセル" + ("左" if is_left else "右"), self.bones[ul_i].pos.copy(), leg_parent,
                             kind="helper", flags=F_ROT | F_ROT_ADD,
                             inherit_parent=self.by_name["腰"], inherit_weight=-1.0)
            self.bones[ul_i].parent = wc_i
            leg_names = [side(n, is_left) for n in ("足D", "ひざD", "足首D", "足先EX", "足先EX補助")]
            if any(name in self.by_name for name in leg_names):
                self.warnings.append(f"{side('足D', is_left)} 系列存在同名骨骼，跳过生成与权重转移")
                continue
            # A parallel D chain reproduces the source only if the joints are
            # directly connected. Do not silently break nonstandard rigs.
            if (self.bones[ll_i].parent != ul_i or self.bones[ft_i].parent != ll_i
                    or (to_i >= 0 and self.bones[to_i].parent != ft_i)):
                self.warnings.append(f"{side('足D', is_left)} 的源骨骼不是直接相连，跳过生成与权重转移")
                continue
            # D bones: same parent as 足 (腰キャンセル), inherit rotation from the real leg
            d_ul = self._add(side("足D", is_left), self.bones[ul_i].pos.copy(), wc_i, kind="helper",
                             flags=F_ROT | F_VISIBLE | F_ENABLED | F_ROT_ADD, layer=1,
                             inherit_parent=ul_i, inherit_weight=1.0)
            d_ll = self._add(side("ひざD", is_left), self.bones[ll_i].pos.copy(), d_ul, kind="helper",
                             flags=F_ROT | F_VISIBLE | F_ENABLED | F_ROT_ADD, layer=1,
                             inherit_parent=ll_i, inherit_weight=1.0)
            d_ft = self._add(side("足首D", is_left), self.bones[ft_i].pos.copy(), d_ll, kind="helper",
                             flags=F_ROT | F_VISIBLE | F_ENABLED | F_ROT_ADD, layer=1,
                             inherit_parent=ft_i, inherit_weight=1.0)
            ex_pos = p_ft + (p_to - p_ft) * (2.0 / 3.0)
            ex_i = self._add(side("足先EX", is_left), ex_pos, d_ft, kind="helper", layer=1)
            toe_name = ""
            toe_helper = ""
            if to_i >= 0:
                # Preserve source toe rotation at its original pivot while EX
                # remains an independently animatable parent of the toe mesh.
                toe_name = self.bones[to_i].name
                toe_helper = side("足先EX補助", is_left)
                self._add(toe_helper, p_to.copy(), ex_i, kind="helper", layer=1,
                          flags=F_ROT | F_ENABLED | F_ROT_ADD,
                          inherit_parent=to_i, inherit_weight=1.0)
            self.leg_weight_chains.append((self.bones[ul_i].name, self.bones[ll_i].name,
                                           self.bones[ft_i].name, toe_name,
                                           *leg_names[:4], toe_helper))

    # ------------------------------------------------------------------- eyes
    def _add_eyes(self, rename, head_pos):
        v = self.vrm
        le = v.humanoid.get("leftEye")
        re_ = v.humanoid.get("rightEye")
        if le is None or re_ is None:
            return
        if le not in self.node_to_bone or re_ not in self.node_to_bone:
            return
        mid = (self.bones[self.node_to_bone[le]].pos + self.bones[self.node_to_bone[re_]].pos) / 2
        head_i = self.node_to_bone.get(v.humanoid.get("head", -1), -1)
        parent = head_i if head_i >= 0 else self.by_name["全ての親"]
        head_top = self._head_top(v.humanoid.get("head"))
        margin = max(abs(float(mid[1] - head_pos[1])) * 0.4, self.scale * 0.04)
        mid[1] = max(float(mid[1]), head_top) + margin
        # kind "std" sorts 両目 ahead of the eyes, which inherit its rotation
        # (MMD evaluates 付与 in index order)
        both = self._add("両目", mid, parent, kind="std")
        for node in (le, re_):
            eye = self.bones[self.node_to_bone[node]]
            eye.flags |= F_ROT_ADD
            eye.inherit_parent = both
            eye.inherit_weight = 1.0

    def _head_top(self, head_node):
        """Estimate the top from vertices attached to the head and descendants.

        A high percentile avoids a stray vertex putting the control far away.
        If geometry is absent, fall back to the head bone height.
        """
        v = self.vrm
        if head_node is None or head_node not in self.node_to_bone:
            return 0.0
        seen, stack = set(), [head_node]
        while stack:
            node = stack.pop()
            if node in seen or not 0 <= node < len(v.nodes):
                continue
            seen.add(node)
            stack.extend(v.nodes[node].get("children", []))
        heights = []
        for mesh in v.meshes:
            head_weight = (np.isin(mesh.joints, list(seen)) * mesh.weights).sum(axis=1)
            y = mesh.positions[head_weight > 0.5, 1] * self.scale
            if len(y):
                heights.append(y[np.isfinite(y)])
        heights = [y for y in heights if len(y)]
        return (float(np.quantile(np.concatenate(heights), 0.999)) if heights
                else float(self.bones[self.node_to_bone[head_node]].pos[1]))

    # --------------------------------------------------------------- shoulders
    def _add_shoulder_helpers(self, rename):
        """肩P = the pass-through parent of 肩, standard in modern MMD rigs."""
        v = self.vrm
        upper2 = self.node_to_bone.get(v.humanoid.get("chest", -1), -1)
        if upper2 < 0:
            upper2 = self.node_to_bone.get(v.humanoid.get("spine", -1), -1)
        for is_left in (True, False):
            prefix = "left" if is_left else "right"
            sh = v.humanoid.get(prefix + "Shoulder")
            if sh is None or sh not in self.node_to_bone:
                continue
            sh_i = self.node_to_bone[sh]
            p = self._add(side("肩P", is_left), self.bones[sh_i].pos.copy(),
                          upper2 if upper2 >= 0 else self.by_name["上半身"], kind="helper")
            self.bones[sh_i].parent = p
            self.bones[sh_i].flags |= F_ROT_ADD
            self.bones[sh_i].inherit_parent = p
            self.bones[sh_i].inherit_weight = 1.0

    # ------------------------------------------------------------------- tails
    def _fill_tails(self):
        """Point each bone's tail along the chain.

        PMX has no bone rotation field: MMD and PMX Editor infer a bone's local
        orientation from its tail direction. Two mistakes here are visible
        immediately in PMX Editor:

        * a tail of (0,-1,0) for leaf bones makes every hair strand render as a
          spike pointing at the floor;
        * helper bones that share their position with a child (肩P/肩, 肩C/腕)
          would get a zero-length tail, which is a degenerate frame and makes
          the whole arm collapse in TransformView.

        So: walk down until a child with a *different* position is found, and
        for true leaves continue the parent's direction instead of pointing
        straight down.
        """
        children: dict[int, list[int]] = {}
        for i, b in enumerate(self.bones):
            if b.parent >= 0:
                children.setdefault(b.parent, []).append(i)

        def first_moved(node: int, guard: int = 0) -> int | None:
            """Nearest descendant that is not at the same position."""
            if guard > 8:
                return None
            kids = children.get(node, [])
            here = self.bones[node].pos
            for k in kids:
                if float(np.linalg.norm(self.bones[k].pos - here)) > 1e-4:
                    return k
            for k in kids:
                r = first_moved(k, guard + 1)
                if r is not None:
                    return r
            return None

        legs = [self.by_name[n] for n in ("左足", "右足") if n in self.by_name]
        for i, b in enumerate(self.bones):
            if b.name == "下半身" and legs:
                # MMD standard: 下半身 points down, between the legs (its first
                # child can be a tail or skirt root above the leg roots)
                mid = np.mean([self.bones[k].pos for k in legs], axis=0)
                if float(np.linalg.norm(mid - b.pos)) > 1e-4:
                    b.tail_offset = mid - b.pos
                    continue
            if b.flags & F_IK:
                # IK bones keep a tail pointing at their goal so PMX Editor
                # draws the chain sensibly
                if b.ik_target >= 0:
                    d = self.bones[b.ik_target].pos - b.pos
                    if float(np.linalg.norm(d)) > 1e-4:
                        b.tail_offset = d
                        continue
            target = first_moved(i)
            if target is not None:
                b.tail_offset = self.bones[target].pos - b.pos
                continue
            # leaf: continue the parent direction (never straight down)
            if b.parent >= 0:
                d = b.pos - self.bones[b.parent].pos
                n = float(np.linalg.norm(d))
                if n > 1e-4:
                    b.tail_offset = d * 0.5
                else:
                    b.tail_offset = np.array([0.0, 0.0, -0.5])
            else:
                b.tail_offset = np.array([0.0, 0.0, -0.5])

    # ------------------------------------------------------------------ output
    def to_pmx_bones(self) -> list[PMXBone]:
        result = [b.to_pmx() for b in self.bones]

        # The ankle and its IK control share a pivot. Give the IK display tail
        # a distinct backward direction, following the reference model, so the
        # corresponding adjustment controls do not lie exactly on top of each
        # other. This changes display geometry only, not IK links or pivots.
        for left in (True, False):
            ik_i = self.by_name.get(side("足ＩＫ", left))
            toe_i = self.by_name.get(side("つま先ＩＫ", left))
            if ik_i is None or not result[ik_i].flags & F_IK:
                continue
            ik = result[ik_i]
            back = ik.position - result[toe_i].position if toe_i is not None else np.zeros(3)
            back = np.array([back[0], 0., back[2]])
            length = float(np.linalg.norm(back))
            if length < 1e-8:
                back = np.array([0., 0., abs(self.scale) * 0.04])
            else:
                # A missing VRM toe may use a distant synthetic IK target;
                # keep its display handle proportional to the model.
                back *= min(0.7, abs(self.scale) * 0.15 / length)
            ik.flags &= ~F_TAIL_INDEX
            ik.tail_offset = back

        # Match the reference D controls: the thigh and knee have an absent
        # tail-bone index, and the ankle has a zero offset. MMD keeps these as
        # compact controls; Blender's MMD importer displays zero-length bones
        # pointing up. Only alter exported display metadata, not the build-time
        # tails used by physics or the ordinary leg/ankle bones.
        for chain in self.leg_weight_chains:
            for name in chain[4:6]:
                bone = result[self.by_name[name]]
                bone.flags |= F_TAIL_INDEX
                bone.tail_bone = -1
            ankle = result[self.by_name[chain[6]]]
            ankle.flags &= ~F_TAIL_INDEX
            ankle.tail_offset = np.zeros(3)

        # VRM joints don't carry Blender's display length. For a non-humanoid
        # head child that fans out into several chains, a particular child is
        # not a meaningful tail. Use the source node's local Y direction and a
        # short scale-relative display vector instead. Keep BoneBuild untouched:
        # physics derives some body offsets from its original tail data.
        if self.vrm is None:
            self._sync_adjustment_display(result)
            return result
        head_node = self.vrm.humanoid.get("head")
        head_index = self.node_to_bone.get(head_node, -1)
        humanoid_nodes = set(self.vrm.humanoid.values())
        child_counts = [0] * len(self.bones)
        for b in self.bones:
            if 0 <= b.parent < len(self.bones) and np.linalg.norm(b.pos - self.bones[b.parent].pos) > 1e-4:
                child_counts[b.parent] += 1
        for i, b in enumerate(self.bones):
            node = b.source_node
            if (head_index < 0 or b.parent != head_index or node < 0 or node in humanoid_nodes
                    or child_counts[i] < 3 or b.flags & (F_TAIL_INDEX | F_IK | F_AXIS)):
                continue
            axis = np.asarray(self.vrm.world[node][:3, 1], dtype=np.float64)
            length = float(np.linalg.norm(axis))
            if np.isfinite(length) and length > 1e-9:
                result[i].tail_offset = axis / length * (0.05 * self.scale)
        self._sync_adjustment_display(result)
        return result

    def _sync_adjustment_display(self, bones: list[PMXBone]):
        """Copy final source geometry and axes after all export-only changes.

        Keep an indexed tail when its translated adjustment target exists;
        otherwise use the equivalent offset, so it never points back at the
        unshifted original rig. Inheritance and control flags remain independent.
        """
        copies = {self.by_name[src]: self.by_name[dst] for src, dst in self.adjustment_pairs}
        auto_arm_names = {side(n, left) for left in (True, False)
                          for n in ("肩", "腕", "腕捩", "ひじ", "手捩", "手首")}
        for source_i, control_i in copies.items():
            source, control = bones[source_i], bones[control_i]
            control.position = source.position + self.adjustment_offset
            control.flags = (control.flags & ~(F_AXIS | F_LOCAL_AXIS)) | (source.flags & (F_AXIS | F_LOCAL_AXIS))
            control.fixed_axis = source.fixed_axis.copy()
            control.local_axis_x = source.local_axis_x.copy()
            control.local_axis_z = source.local_axis_z.copy()
            if source.flags & F_TAIL_INDEX:
                target_i = source.tail_bone
                if target_i < 0:
                    control.flags |= F_TAIL_INDEX
                    control.tail_bone = -1
                elif target_i in copies:
                    control.flags |= F_TAIL_INDEX
                    control.tail_bone = copies[target_i]
                else:
                    control.flags &= ~F_TAIL_INDEX
                    control.tail_offset = bones[target_i].position - source.position
            else:
                control.flags &= ~F_TAIL_INDEX
                control.tail_offset = source.tail_offset.copy()

            # Blender's MMD importer assigns an automatic roll to standard arm
            # names but not to their 調整 counterparts. Describe the equivalent
            # axes from this bone's actual direction so both import identically.
            # An explicit source axis always takes precedence.
            if source.name in auto_arm_names and not source.flags & F_LOCAL_AXIS:
                if source.flags & F_TAIL_INDEX:
                    direction = (bones[source.tail_bone].position - source.position
                                 if source.tail_bone >= 0 else np.zeros(3))
                else:
                    direction = source.tail_offset.copy()
                if np.linalg.norm(direction) < 1e-8:
                    direction = source.fixed_axis.copy() if source.flags & F_AXIS else np.array([0., 1., 0.])
                length = float(np.linalg.norm(direction))
                horizontal = float(np.linalg.norm(direction[:2]))
                if length > 1e-8 and horizontal > 1e-8:
                    along = direction / length
                    perpendicular = np.array([-direction[1], direction[0], 0.]) / horizontal
                    normal = -np.cross(along, perpendicular)
                    normal /= np.linalg.norm(normal)
                    control.flags |= F_LOCAL_AXIS
                    control.local_axis_x = along
                    control.local_axis_z = normal
