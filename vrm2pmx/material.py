# -*- coding: utf-8 -*-
"""Material + texture conversion.

Verified against the three reference PMX files:
  diffuse      = MToon _Color
  ambient      = MToon _ShadeColor      (exact match, no color-space conversion)
  edge colour  = MToon _OutlineColor    (exact match)
  outline size = 0.5 for every outlined material
  flags        = 0x0e base | 0x01 double sided | 0x10 outline
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np

from .pmx import PMXMaterial

BASE_FLAGS = 0x02 | 0x04 | 0x08      # ground shadow, cast shadow, receive shadow
FLAG_DOUBLE_SIDED = 0x01
FLAG_OUTLINE = 0x10

TOON_REF_INTERNAL = 1
TOON_INDEX_SPHERE = 4               # toon04.bmp, the usual MMD default
OUTLINE_SIZE = 0.5


@dataclass
class ConvertedMaterial:
    pmx: PMXMaterial
    source_index: int
    transparent: bool
    render_queue: int
    triangles_by_mesh: dict = field(default_factory=dict)   # mesh index -> indices


def _vec4(v, default=(1, 1, 1, 1)):
    if not v:
        return np.array(default, dtype=np.float64)
    return np.array([float(x) for x in v[:4]], dtype=np.float64)


def _vec3(v, default=(0, 0, 0)):
    if not v:
        return np.array(default, dtype=np.float64)
    return np.array([float(x) for x in v[:3]], dtype=np.float64)


def sanitize_filename(name: str, used: set[str]) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "texture"
    base, ext = os.path.splitext(name)
    ext = ext or ".png"
    candidate = f"{base}{ext}"
    n = 2
    while candidate.lower() in used:
        candidate = f"{base}_{n}{ext}"
        n += 1
    used.add(candidate.lower())
    return candidate


class MaterialConverter:
    def __init__(self, vrm, tex_dir: str):
        self.vrm = vrm
        self.tex_dir = tex_dir
        self.texture_paths: list[str] = []      # index in PMX texture list -> relative path
        self._tex_index: dict[int, int] = {}    # glTF texture index -> PMX texture index
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ textures
    def export_textures(self) -> list[str]:
        os.makedirs(self.tex_dir, exist_ok=True)
        used: set[str] = set()
        by_gltf = {t.index: t for t in self.vrm.textures}
        referenced = []
        for mat_index in range(len(self.vrm.materials)):
            mat = self.vrm.materials[mat_index]
            props = self.vrm.material_props(mat.get("name", ""))
            tp = props.get("textureProperties") or {}

            # Main texture, plus emission textures used by MToon / emissive
            # blend shapes. Previously only _MainTex was exported, which lost
            # every _EmissionMap even though the morphs referenced it.
            candidates = [self.vrm.material_texture(mat_index), tp.get("_MainTex"),
                          tp.get("_EmissionMap")]
            # glTF fallback for materials without VRM materialProperties.
            candidates.append((mat.get("emissiveTexture") or {}).get("index"))
            for ti in candidates:
                if ti is not None and ti not in referenced:
                    referenced.append(ti)

        for ti in referenced:
            tex = by_gltf.get(ti)
            if tex is None:
                continue
            fname = sanitize_filename(tex.name + ".png", used)
            path = os.path.join(self.tex_dir, fname)
            with open(path, "wb") as f:
                f.write(tex.data)
            self._tex_index[ti] = len(self.texture_paths)
            self.texture_paths.append(f"tex/{fname}")
        return self.texture_paths

    # ----------------------------------------------------------------- materials
    def convert(self) -> tuple[list[PMXMaterial], list[ConvertedMaterial]]:
        v = self.vrm
        alpha_animated = set()
        for group in v.blendshapes:
            if group.get("binds"):
                continue
            for value in group.get("materialValues", []) or []:
                if value.get("propertyName", "_Color") != "_Color":
                    continue
                name = value.get("materialName", "")
                target = value.get("targetValue", [1, 1, 1, 1])
                target_alpha = float(target[3]) if len(target) >= 4 else 0.0
                props = v.material_props(name).get("vectorProperties", {}) or {}
                base_alpha = _vec4(props.get("_Color"))[3]
                if abs(target_alpha - base_alpha) >= 1e-9:
                    alpha_animated.add(name)
        out: list[ConvertedMaterial] = []
        for mi, mat in enumerate(v.materials):
            name = mat.get("name", f"material{mi}")
            props = v.material_props(name)
            fp = props.get("floatProperties", {}) or {}
            vp = props.get("vectorProperties", {}) or {}
            tp = props.get("textureProperties", {}) or {}
            queue = int(props.get("renderQueue", 2000) or 2000)
            transparent = queue > 2000

            color = _vec4(vp.get("_Color"), (1, 1, 1, 1))
            shade = _vec3(vp.get("_ShadeColor"), (0, 0, 0))
            outline_color = _vec4(vp.get("_OutlineColor"), (0, 0, 0, 1))
            outline_mode = float(fp.get("_OutlineWidthMode", 0) or 0)
            outline_width = float(fp.get("_OutlineWidth", 0) or 0)
            has_outline = outline_mode > 0.5 and outline_width > 0.0
            cull_mode = fp.get("_CullMode")
            double_sided = bool(mat.get("doubleSided", False)) if cull_mode is None else float(cull_mode) == 0
            if has_outline and name in alpha_animated:
                outline_color[3] *= color[3]

            flags = BASE_FLAGS
            if double_sided:
                flags |= FLAG_DOUBLE_SIDED
            if has_outline:
                flags |= FLAG_OUTLINE

            tex_index = -1
            ti = v.material_texture(mi)
            if ti is None:
                ti = tp.get("_MainTex")
            if ti is not None and ti in self._tex_index:
                tex_index = self._tex_index[ti]

            pm = PMXMaterial()
            pm.name = f"{queue}_{name}"
            pm.name_en = ""
            pm.diffuse = tuple(color)
            pm.specular = (0.0, 0.0, 0.0)
            pm.shininess = 0.0
            pm.ambient = tuple(shade)
            pm.flags = flags
            pm.edge_color = tuple(outline_color)
            pm.edge_size = OUTLINE_SIZE if has_outline else 0.0
            pm.texture = tex_index
            pm.sphere_texture = -1
            pm.sphere_mode = 0
            pm.toon_ref = TOON_REF_INTERNAL
            pm.toon_value = TOON_INDEX_SPHERE
            pm.memo = ""
            pm.surface_count = 0

            out.append(ConvertedMaterial(
                pmx=pm, source_index=mi,
                transparent=transparent, render_queue=queue,
            ))

        return [c.pmx for c in out], out


def sort_materials(converted: list[ConvertedMaterial]) -> list[ConvertedMaterial]:
    """Opaque (queue <= 2000) first, then transparent, both stable."""
    return sorted(converted, key=lambda c: (c.transparent, c.render_queue))
