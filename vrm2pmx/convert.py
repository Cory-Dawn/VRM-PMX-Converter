# -*- coding: utf-8 -*-
"""Conversion pipeline: VRM in, PMX out."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import numpy as np

from .material import MaterialConverter, sort_materials
from .morph import MorphConverter, PANEL_NAMES
from .pmx import PMXModel, PMXVertex, write_pmx
from .pose import DEFAULT_ANGLE_DEG, PoseBaker
from .skeleton import F_VISIBLE, SkeletonBuilder
from .vrm import VRM

DEFAULT_SCALE = 14.0


@dataclass
class ConvertOptions:
    scale: float = DEFAULT_SCALE
    pose_angle_deg: float = DEFAULT_ANGLE_DEG   # 0 disables the A-pose bake
    export_physics: bool = True
    physics_strength: float = 1.0
    physics_softness: float = 0.75   # universal preset: smaller = stiffer, less jitter (1.0 = v9 hardness)
    physics_preset: str = "universal"   # universal (default since 2026-11-02) / final / zero / ...
    localize_names: bool = False     # Chinese material/morph names -> Japanese glyphs
    adjustment_bones: bool = False   # optional motion correction controls
    adjustment_offset: tuple[float, float, float] = (-20.0, 0.0, 0.0)
    model_name: str = ""
    out_dir: str = ""
    overwrite: bool = True


@dataclass
class ConvertReport:
    ok: bool = False
    out_pmx: str = ""
    vertices: int = 0
    triangles: int = 0
    bones: int = 0
    morphs: int = 0
    materials: int = 0
    textures: int = 0
    rigid_bodies: int = 0
    joints: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _index_size(count: int, signed: bool = True) -> int:
    if signed:
        if count <= 127:
            return 1
        if count <= 32767:
            return 2
        return 4
    if count <= 255:
        return 1
    if count <= 65535:
        return 2
    return 4


class Converter:
    def __init__(self, options: ConvertOptions | None = None):
        self.opt = options or ConvertOptions()
        self.report = ConvertReport()

    # ------------------------------------------------------------------- main
    def convert(self, vrm_path: str, out_pmx: str | None = None) -> ConvertReport:
        t0 = time.time()
        rep = self.report
        rep.warnings = []
        rep.errors = []
        try:
            vrm = VRM(vrm_path)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"读取 VRM 失败：{e}")
            return rep

        scale = float(self.opt.scale)
        baker = PoseBaker(vrm, self.opt.pose_angle_deg)
        if baker.apply():
            rep.warnings.append(f"已把手臂下摆 {self.opt.pose_angle_deg:.0f}°（A-pose）")

        skel = SkeletonBuilder(vrm, scale).build()
        adjustment_names = skel.add_adjustment_bones(self.opt.adjustment_offset) if self.opt.adjustment_bones else []
        rep.warnings.extend(skel.warnings)

        model_name = (self.opt.model_name
                      or vrm.ext.get("meta", {}).get("title", "")
                      or os.path.splitext(os.path.basename(vrm_path))[0])

        out_pmx = out_pmx or self._default_out(vrm_path, model_name)
        out_dir = os.path.dirname(os.path.abspath(out_pmx))
        tex_dir = os.path.join(out_dir, "tex")
        os.makedirs(out_dir, exist_ok=True)

        mat_conv = MaterialConverter(vrm, tex_dir)
        tex_paths = mat_conv.export_textures()
        _, converted = mat_conv.convert()
        converted = sort_materials(converted)

        # ---- PMX material index lookup (sorted order)
        mat_order = {c.source_index: i for i, c in enumerate(converted)}
        name_to_pmx_mat = {}
        for i, c in enumerate(converted):
            vrm_name = vrm.materials[c.source_index].get("name", "")
            name_to_pmx_mat[vrm_name] = i

        model = PMXModel()
        model.version = 2.0
        model.encoding = 0
        model.name = model_name
        model.name_en = ""
        model.comment = self._comment(vrm)
        model.comment_en = ""
        model.textures = tex_paths
        model.materials = [c.pmx for c in converted]

        # ---- vertices
        verts, vmap_ok = self._build_vertices(vrm, skel, scale)
        model.vertices = verts

        # ---- faces grouped per material
        faces, surf_counts = self._build_faces(vrm, vmap_ok, mat_order, len(converted))
        model.faces = faces
        for i, c in enumerate(converted):
            c.pmx.surface_count = surf_counts[i]

        # ---- bones
        model.bones = skel.to_pmx_bones()

        # ---- morphs
        mc = MorphConverter(vrm, scale, name_to_pmx_mat, baker.vertex_rot)
        mr = mc.convert()
        model.morphs = mr.morphs
        rep.warnings.extend(mr.warnings)

        if self.opt.localize_names:
            from .name_jp import localize_pmx_names
            changed = localize_pmx_names(model.materials, model.morphs)
            if changed:
                rep.warnings.append(
                    f"已将 {len(changed)} 个材质/形态键名称转为日文字形"
                )
            else:
                rep.warnings.append("未发现需要转换的中文材质/形态键名称")

        # ---- physics (stage 2)
        if self.opt.export_physics:
            try:
                from .physics import build_physics
                bodies, joints, pw = build_physics(vrm, skel, scale, self.opt.physics_strength,
                                                   preset=self.opt.physics_preset,
                                                   pose_rot=baker.node_rot,
                                                   softness=self.opt.physics_softness)
                model.rigid_bodies = bodies
                model.joints = joints
                rep.warnings.extend(pw)
            except Exception as e:  # noqa: BLE001
                rep.warnings.append(f"物理生成失败（已跳过）：{e}")

        # ---- index sizes
        model.idx_vertex = _index_size(len(model.vertices), signed=False)
        model.idx_texture = _index_size(len(model.textures) + 1)
        model.idx_material = _index_size(len(model.materials) + 1)
        model.idx_bone = _index_size(len(model.bones) + 1)
        model.idx_morph = _index_size(len(model.morphs) + 1)
        model.idx_rigid = _index_size(len(model.rigid_bodies) + 1)

        # ---- display frames
        model.display_frames = self._display_frames(model, adjustment_names)

        write_pmx(model, out_pmx)

        rep.ok = True
        rep.out_pmx = out_pmx
        rep.vertices = len(model.vertices)
        rep.triangles = len(model.faces) // 3
        rep.bones = len(model.bones)
        rep.morphs = len(model.morphs)
        rep.materials = len(model.materials)
        rep.textures = len(model.textures)
        rep.rigid_bodies = len(model.rigid_bodies)
        rep.joints = len(model.joints)
        rep.seconds = time.time() - t0
        return rep

    # ------------------------------------------------------------------ pieces
    def _default_out(self, vrm_path: str, model_name: str = "") -> str:
        """每套模型输出到自己的文件夹：<输出根目录>/<模型名>/<模型名>.pmx
        贴图放在同级的 tex/ 里。这样多个模型转换到同一目录时不会互相覆盖贴图。
        """
        stem = os.path.splitext(os.path.basename(vrm_path))[0]
        base = (model_name or stem).strip() or stem
        # strip characters that are illegal in Windows paths
        base = "".join(c for c in base if c not in '\\/:*?"<>|').strip() or stem
        root = self.opt.out_dir or os.path.dirname(os.path.abspath(vrm_path))
        return os.path.join(root, base, base + ".pmx")

    def _comment(self, vrm) -> str:
        meta = vrm.ext.get("meta", {})
        lines = [
            "-模型名：" + str(meta.get("title", "")),
            "",
            "-版本：" + str(meta.get("version", "")),
            "",
            "-制作：",
            "",
            "  作者：" + str(meta.get("author", "")),
            "",
            "-----------------------------------",
            "本文件由 VRM to PMX Converter 自动生成",
            "模型版权归原作者所有，请遵守原 VRM 的使用条款：",
            "  " + str(meta.get("licenseName", "")),
        ]
        if meta.get("otherPermissionUrl"):
            lines.append("  " + str(meta.get("otherPermissionUrl")))
        return "\r\n".join(lines)

    def _build_vertices(self, vrm, skel, scale: float):
        verts = []
        ok = []          # per vertex: 4 bone ids actually used
        twist_chains = {}
        for root, end, twist, helpers in skel.twist_chains:
            root_i, end_i = skel.by_name[root], skel.by_name[end]
            start = skel.bones[root_i].pos
            direction = skel.bones[end_i].pos - start
            twist_chains[root_i] = (start, direction, float(np.dot(direction, direction)),
                                    [root_i, *(skel.by_name[n] for n in helpers), skel.by_name[twist]])
        leg_maps = {}
        foot_splits = {}
        for upper, lower, foot, toe, upper_d, lower_d, foot_d, ex, toe_helper in skel.leg_weight_chains:
            for source, target in ((upper, upper_d), (lower, lower_d), (foot, foot_d), (toe, toe_helper)):
                if source and target:
                    leg_maps[skel.by_name[source]] = skel.by_name[target]
            start = skel.bones[skel.by_name[foot]].pos
            # EX lies 2/3 of the way from ankle to the toe pivot.
            direction = (skel.bones[skel.by_name[ex]].pos - start) * 1.5
            length2 = float(np.dot(direction, direction))
            if length2 > 1e-12:
                foot_splits[skel.by_name[foot_d]] = (start, direction, length2, skel.by_name[ex])
        for mesh in vrm.meshes:
            n = mesh.count
            pos = mesh.positions * scale
            nrm = mesh.normals
            uv = mesh.uvs
            for i in range(n):
                w = mesh.weights[i]
                js = mesh.joints[i]
                pairs = []
                for k in range(4):
                    if w[k] <= 1e-6:
                        continue
                    b = skel.node_to_bone.get(int(js[k]), -1)
                    if b < 0:
                        continue
                    pairs.append((b, float(w[k])))
                if not pairs:
                    # fall back to the root bone so the vertex is not lost
                    pairs = [(skel.by_name["全ての親"], 1.0)]
                pairs.sort(key=lambda x: -x[1])
                pairs = pairs[:4]
                total = sum(p[1] for p in pairs)
                pairs = [(b, wt / total) for b, wt in pairs]
                if twist_chains and any(b in twist_chains for b, _ in pairs):
                    pairs = self._distribute_twist_weights(pos[i], pairs, twist_chains)
                if leg_maps and any(b in leg_maps for b, _ in pairs):
                    pairs = self._distribute_leg_weights(pos[i], pairs, leg_maps, foot_splits)

                v = PMXVertex(
                    position=pos[i].copy(),
                    normal=nrm[i].copy(),
                    # MMD uses DirectX texture coordinates, whose origin is the
                    # top-left corner exactly like glTF, so V is copied as-is.
                    # (The reference PMX files store V-1, which is the same
                    # value modulo 1 and confirms there is no flip.)
                    uv=np.array([uv[i][0], uv[i][1]]),
                )
                if len(pairs) == 1:
                    v.weight_type = 0
                    v.bones = [pairs[0][0]]
                    v.weights = [1.0]
                elif len(pairs) == 2:
                    v.weight_type = 1
                    v.bones = [pairs[0][0], pairs[1][0]]
                    v.weights = [pairs[0][1]]
                else:
                    v.weight_type = 2
                    v.bones = [p[0] for p in pairs]
                    v.weights = [p[1] for p in pairs]
                    while len(v.bones) < 4:
                        v.bones.append(pairs[0][0])
                        v.weights.append(0.0)
                v.edge_scale = 1.0
                verts.append(v)
                ok.append(True)
        return verts, ok

    @staticmethod
    def _distribute_leg_weights(position, pairs, mapping, foot_splits):
        """Move existing leg influence to its equivalent D chain.

        Smoothly share the forefoot's ankle influence with EX, never discarding
        another source influence when PMX's four-bone limit is already full.
        """
        merged = {}
        for bone, weight in pairs:
            target = mapping.get(bone, bone)
            merged[target] = merged.get(target, 0.0) + weight
        groups = []
        for bone, weight in merged.items():
            if bone not in foot_splits:
                groups.append([(bone, weight)])
                continue
            start, direction, length2, ex = foot_splits[bone]
            t = float(np.clip((np.dot(position - start, direction) / length2 - 1 / 3) * 1.5, 0, 1))
            t = t * t * (3 - 2 * t)
            if t <= 1e-8:
                groups.append([(bone, weight)])
            elif t >= 1 - 1e-8:
                groups.append([(ex, weight)])
            else:
                groups.append([(bone, weight * (1 - t)), (ex, weight * t)])
        spare = max(0, 4 - len(groups))
        splits = sorted((i for i, group in enumerate(groups) if len(group) == 2),
                        key=lambda i: min(groups[i][0][1], groups[i][1][1]), reverse=True)
        keep = set(splits[:spare])
        final = {}
        for i, group in enumerate(groups):
            if len(group) == 2 and i not in keep:
                group = [(max(group, key=lambda item: item[1])[0], sum(w for _, w in group))]
            for bone, weight in group:
                final[bone] = final.get(bone, 0.0) + weight
        return sorted(final.items(), key=lambda item: -item[1])

    @staticmethod
    def _distribute_twist_weights(position, pairs, chains):
        """Split only existing arm influence; preserve unrelated weights and PMX's 4-bone limit.

        A twist control contributes its full rotation; its three siblings receive
        1/4, 1/2 and 3/4. Linear interpolation between adjacent influences gives
        a smooth ramp. If no extra slot exists, use the nearer of those two
        bones instead of dropping another original influence.
        """
        merged = {}
        for bone, weight in pairs:
            merged[bone] = merged.get(bone, 0.0) + weight
        groups = []
        for bone, weight in merged.items():
            if bone not in chains:
                groups.append([(bone, weight)])
                continue
            start, direction, length2, indices = chains[bone]
            t = float(np.clip(np.dot(position - start, direction) / length2, 0.0, 1.0)) * 4.0
            lower = min(int(t), 3)
            fraction = t - lower
            if fraction <= 1e-8:
                groups.append([(indices[lower], weight)])
            elif fraction >= 1.0 - 1e-8:
                groups.append([(indices[lower + 1], weight)])
            else:
                groups.append([(indices[lower], weight * (1.0 - fraction)),
                               (indices[lower + 1], weight * fraction)])
        spare = max(0, 4 - len(groups))
        split_order = sorted((i for i, group in enumerate(groups) if len(group) == 2),
                             key=lambda i: min(groups[i][0][1], groups[i][1][1]), reverse=True)
        keep_split = set(split_order[:spare])
        result = []
        for i, group in enumerate(groups):
            if len(group) == 2 and i not in keep_split:
                dominant = max(group, key=lambda item: item[1])[0]
                result.append((dominant, sum(weight for _, weight in group)))
            else:
                result.extend(group)
        # Adjacent groups can target the same bone at a joint.
        final = {}
        for bone, weight in result:
            final[bone] = final.get(bone, 0.0) + weight
        return sorted(final.items(), key=lambda item: -item[1])

    def _build_faces(self, vrm, vmap_ok, mat_order, n_materials):
        """Collect triangles grouped by material.

        The X mirror flips handedness, so every triangle's winding has to be
        reversed (v0,v1,v2 -> v0,v2,v1) or MMD will treat the front faces as
        back faces and cull them. Verified against the reference models: their
        geometric normal agrees with the vertex normals for 99.99% of faces,
        which only holds when the winding is flipped after mirroring.
        """
        per_material: list[list[int]] = [[] for _ in range(n_materials)]
        for mesh in vrm.meshes:
            offset = mesh.vertex_offset
            for prim in mesh.primitives:
                mi = mat_order.get(prim.material)
                if mi is None:
                    continue
                tri = (prim.indices + offset).reshape(-1, 3)
                flipped = tri[:, [0, 2, 1]]
                per_material[mi].extend(flipped.reshape(-1).tolist())
        faces = []
        counts = []
        for lst in per_material:
            counts.append(len(lst))
            faces.extend(lst)
        return np.array(faces, dtype=np.int32), counts

    def _display_frames(self, model, adjustment_names=()):
        """Group visible controls once; retain hidden deformation helpers outside the panel."""
        by = {b.name: i for i, b in enumerate(model.bones)}
        used: set[int] = set()

        def pick(names):
            out = []
            for nm in names:
                i = by.get(nm)
                if i is not None and i not in used:
                    used.add(i)
                    out.append((0, i))
            return out

        sides = ("左", "右")
        fingers = [s + f + d for s in sides for f in ("親指", "人指", "中指", "薬指", "小指") for d in "０１２３"]
        frames = [
            {"name": "Root", "name_en": "Root", "special": 1, "items": pick(["操作中心", "全ての親"]) or [(0, 0)]},
            {"name": "表情", "name_en": "Exp", "special": 1, "items": [(1, i) for i in range(len(model.morphs))]},
            {"name": "センター", "name_en": "Center", "special": 0, "items": pick(["センター", "グルーブ", "腰"])},
            {"name": "IK", "name_en": "IK", "special": 0,
             "items": pick([s + n for s in sides for n in ("足IK親", "足ＩＫ", "つま先ＩＫ")])},
            {"name": "体(上)", "name_en": "Body[u]", "special": 0,
             "items": pick(["上半身", "上半身2", "上半身3", "首", "頭", "両目", "左目", "右目"])},
            {"name": "腕", "name_en": "Arms", "special": 0,
             "items": pick([s + n for s in sides for n in ("肩P", "肩", "腕", "腕捩", "ひじ", "手捩", "手首", "ダミー")])},
            {"name": "指", "name_en": "Fingers", "special": 0, "items": pick(fingers)},
            {"name": "体(下)", "name_en": "Body[l]", "special": 0, "items": pick(["下半身"])},
            {"name": "足", "name_en": "Legs", "special": 0,
             "items": pick([s + n for s in sides for n in ("足", "ひざ", "足首", "つま先", "足D", "ひざD", "足首D", "足先EX")])},
        ]
        if adjustment_names:
            frames.append({"name": "_調整ボーン", "name_en": "Adjustment", "special": 0,
                           "items": pick(adjustment_names)})
        frames.append({"name": "その他", "name_en": "Others", "special": 0,
                       "items": [(0, i) for i, b in enumerate(model.bones) if i not in used and b.flags & F_VISIBLE]})
        return frames
