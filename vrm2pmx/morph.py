# -*- coding: utf-8 -*-
"""BlendShape → PMX morph.

Vertex morph deltas are copied 1:1 (scaled) from the glTF morph target; the
reference models confirm the vertex sets match exactly (14385 / 11081 / 4002).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .pmx import PMXMorph

PANEL_RESERVED = 0
PANEL_BROW = 1
PANEL_EYE = 2
PANEL_MOUTH = 3
PANEL_OTHER = 4

PANEL_NAMES = {PANEL_BROW: "眉", PANEL_EYE: "眼", PANEL_MOUTH: "口", PANEL_OTHER: "其他"}

# exact names observed in the reference models (highest confidence)
EXACT_PANEL: dict[str, int] = {
    # 眉
    "にこり": PANEL_BROW, "上": PANEL_BROW, "下": PANEL_BROW,
    "真面目": PANEL_BROW, "困る": PANEL_BROW, "怒り": PANEL_BROW,
    # 眼
    "まばたき": PANEL_EYE, "ｳｨﾝｸ２右": PANEL_EYE, "ウィンク２": PANEL_EYE,
    "なごみ": PANEL_EYE, "笑い": PANEL_EYE, "ウィンク右": PANEL_EYE,
    "ウィンク": PANEL_EYE, "じと目": PANEL_EYE, "はぅ": PANEL_EYE,
    "ｷﾘｯ": PANEL_EYE, "びっくり": PANEL_EYE, "にやり目": PANEL_EYE,
    "怒目": PANEL_EYE, "困目": PANEL_EYE, "高光隠し": PANEL_EYE,
    "白目": PANEL_EYE, "瞳小": PANEL_EYE, "ハイライト大": PANEL_EYE,
    "はぁと": PANEL_EYE, "星目": PANEL_EYE, "涙": PANEL_EYE,
    "涙目": PANEL_EYE, "ぐるぐる目": PANEL_EYE, "照れ": PANEL_EYE,
    # 口
    "にやり": PANEL_MOUTH, "にやり２": PANEL_MOUTH, "口角上げ": PANEL_MOUTH,
    "口角下げ": PANEL_MOUTH, "口横広げ": PANEL_MOUTH, "口縦狭め": PANEL_MOUTH,
    "∧": PANEL_MOUTH, "ぺろっ": PANEL_MOUTH,
    "あ": PANEL_MOUTH, "い": PANEL_MOUTH, "う": PANEL_MOUTH,
    "え": PANEL_MOUTH, "お": PANEL_MOUTH,
}

EYE_KEYS = ("まばたき", "ウィンク", "ｳｨﾝｸ", "目", "瞳", "涙", "照れ", "ハイライト",
            "高光", "なごみ", "はぅ", "ｷﾘｯ", "びっくり", "じと", "笑い", "はぁと", "星")
MOUTH_KEYS = ("口", "あ", "い", "う", "え", "お", "にやり", "∧", "舌", "ぺろ")
BROW_KEYS = ("眉", "真面目", "困る", "怒り", "にこり")
# Accessories must be checked before face-part keywords. "护目镜" contains
# "目" and would otherwise be classified as an eye morph.
ACCESSORY_KEYS = ("护目镜", "眼镜", "墨镜", "耳麦", "手环", "帽子", "发饰", "头饰",
                  "项圈", "项链", "耳环", "戒指", "领带", "蝴蝶结", "铃铛",
                  "配件", "饰品")

# glTF / ARKit style names always land in "其他" in the reference models
LATIN_ONLY = re.compile(r"^[A-Za-z][A-Za-z0-9_\.\+]*$")


def classify_panel(name: str) -> int:
    if name in EXACT_PANEL:
        return EXACT_PANEL[name]
    if LATIN_ONLY.match(name):
        return PANEL_OTHER
    # Accessories win over face keywords ("护目镜" contains "目").
    if any(k in name for k in ACCESSORY_KEYS):
        return PANEL_OTHER
    if any(k in name for k in BROW_KEYS):
        return PANEL_BROW
    if any(k in name for k in MOUTH_KEYS):
        return PANEL_MOUTH
    if any(k in name for k in EYE_KEYS):
        return PANEL_EYE
    return PANEL_OTHER


@dataclass
class MorphResult:
    morphs: list[PMXMorph] = field(default_factory=list)
    panels: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class MorphConverter:
    def __init__(self, vrm, scale: float, material_name_map: dict[str, int],
                 vertex_rot: dict[int, np.ndarray] | None = None):
        self.vrm = vrm
        self.scale = scale
        self.material_name_map = material_name_map  # VRM material name -> PMX material index
        self.vertex_rot = vertex_rot or {}

    def convert(self) -> MorphResult:
        res = MorphResult()
        v = self.vrm

        for group in v.blendshapes:
            name = group.get("name", "").strip()
            if not name:
                continue
            binds = group.get("binds", []) or []
            mat_values = group.get("materialValues", []) or []
            is_binary = bool(group.get("isBinary"))

            if binds:
                offsets = self._vertex_offsets(binds)
                # keep even empty morphs: vrc.v_sil legitimately has no movement
                # and the reference models keep the slot so VMD mouth tracks
                # still find their target.
                mo = PMXMorph()
                mo.name = name
                mo.name_en = ""
                mo.panel = classify_panel(name)
                mo.type = 1
                mo.offsets = offsets
                res.morphs.append(mo)
                res.panels[name] = mo.panel
                continue

            if mat_values:
                offsets = self._material_offsets(mat_values, is_binary)
                if offsets:
                    mo = PMXMorph()
                    mo.name = name
                    mo.name_en = ""
                    mo.panel = classify_panel(name)
                    mo.type = 8
                    mo.offsets = offsets
                    res.morphs.append(mo)
                    res.panels[name] = mo.panel
                continue

            # empty group (Neutral et al.) is dropped on purpose
        return res

    # ------------------------------------------------------------------ vertex
    def _vertex_offsets(self, binds) -> list:
        v = self.vrm
        merged: dict[int, np.ndarray] = {}
        for bind in binds:
            mesh_idx = int(bind.get("mesh", -1))
            target_idx = int(bind.get("index", -1))
            weight = float(bind.get("weight", 100.0)) / 100.0
            if mesh_idx < 0 or mesh_idx >= len(v.meshes):
                continue
            mesh = v.meshes[mesh_idx]
            gltf_mesh = v.gltf["meshes"][mesh_idx]
            if not gltf_mesh.get("primitives"):
                continue
            targets = gltf_mesh["primitives"][0].get("targets") or []
            if target_idx < 0 or target_idx >= len(targets):
                continue
            tgt = targets[target_idx]
            if "POSITION" not in tgt:
                continue
            delta = v.glb.accessor(tgt["POSITION"]).astype(np.float64)
            if len(delta) != mesh.count:
                # target length must match the mesh vertex count
                n = min(len(delta), mesh.count)
                delta = delta[:n]
            if getattr(v, "mirrored", False):
                delta[:, 0] *= -1.0          # right-handed -> left-handed
            delta = delta * (self.scale * weight)
            # if the arms were baked into an A-pose the deltas must follow
            rot = self.vertex_rot.get(mesh_idx)
            if rot is not None and len(rot) >= len(delta):
                delta = np.einsum("nij,nj->ni", rot[:len(delta)], delta)
            for i in range(len(delta)):
                if np.abs(delta[i]).sum() < 1e-9:
                    continue
                gi = mesh.vertex_offset + i
                if gi in merged:
                    merged[gi] = merged[gi] + delta[i]
                else:
                    merged[gi] = delta[i].copy()
        return [(gi, d) for gi, d in sorted(merged.items())]

    # ---------------------------------------------------------------- material
    def _material_offsets(self, mat_values, is_binary: bool) -> list:
        v = self.vrm
        # One PMX material morph entry per material. Multiple VRM properties
        # for the same material must be merged, otherwise MMD only applies the
        # last offset.
        acc: dict[int, dict] = {}
        cache: dict[int, dict] = {}
        for mv in mat_values:
            mat_name = mv.get("materialName", "")
            prop = mv.get("propertyName", "_Color")
            target = mv.get("targetValue", [1, 1, 1, 1])
            pmx_idx = self.material_name_map.get(mat_name)
            if pmx_idx is None:
                continue
            if pmx_idx not in cache:
                cache[pmx_idx] = self._base_values(v, mat_name)
            base = cache[pmx_idx]
            tgt = np.array([float(x) for x in target], dtype=np.float64)
            if tgt.size < 4:
                tgt = np.pad(tgt, (0, 4 - tgt.size), constant_values=0.0)

            a = acc.setdefault(pmx_idx, {
                "diffuse": np.zeros(4),
                "ambient": np.zeros(3),
                "edge": np.zeros(4),
            })

            if prop == "_Color":
                diff = tgt - base["diffuse"]
                if np.abs(diff).sum() < 1e-9:
                    continue
                a["diffuse"] += diff
                # Initial edge alpha is scaled by material alpha during export.
                a["edge"][3] += base["outline_alpha"] * diff[3]
            elif prop == "_EmissionColor":
                # PMX materials have no emission channel. MMD's ambient
                # (環境色) is the closest additive colour term, so map the
                # emission delta there. RGB only: MToon's emission alpha is
                # not a visibility flag (many targets store alpha 0).
                delta = tgt[:3] - base["emission"][:3]
                if np.abs(delta).sum() < 1e-9:
                    continue
                a["ambient"] += delta
            # Other MToon properties (rim, shade, outline) are not exposed by
            # PMX material morphs and are intentionally ignored.

        out = []
        for pmx_idx in sorted(acc):
            a = acc[pmx_idx]
            if (np.abs(a["diffuse"]).sum() < 1e-9
                    and np.abs(a["ambient"]).sum() < 1e-9
                    and np.abs(a["edge"]).sum() < 1e-9):
                continue
            out.append((
                pmx_idx, 1,                      # calc mode: add
                a["diffuse"],
                np.zeros(3), 0.0, a["ambient"],
                a["edge"],
                0.0,
                np.zeros(4), np.zeros(4), np.zeros(4),
            ))
        return out

    def _base_values(self, v, mat_name: str) -> dict:
        props = v.material_props(mat_name)
        vp = props.get("vectorProperties", {}) or {}
        color = vp.get("_Color") or [1, 1, 1, 1]
        emission = vp.get("_EmissionColor")
        if emission is None:
            # GLB fallback for materials without VRM materialProperties.
            emission = [0.0, 0.0, 0.0, 1.0]
            for mat in v.materials:
                if mat.get("name") == mat_name and mat.get("emissiveFactor"):
                    ef = mat["emissiveFactor"]
                    emission = [float(ef[0]), float(ef[1]), float(ef[2]), 1.0]
                    break
        return {
            "diffuse": np.array([float(x) for x in color[:4]], dtype=np.float64),
            "emission": np.array([float(x) for x in emission[:4]], dtype=np.float64),
            "outline_alpha": float((vp.get("_OutlineColor") or [0, 0, 0, 1])[3]),
        }
