# -*- coding: utf-8 -*-
"""Post-write validation. Re-opens the produced PMX and checks the invariants
that make MMD either load the file or reject it."""
from __future__ import annotations

import os

import numpy as np

from .pmx import read_pmx


def validate(path: str) -> list[str]:
    problems: list[str] = []
    if not os.path.exists(path):
        return [f"输出文件不存在：{path}"]
    try:
        m = read_pmx(path)
    except Exception as e:  # noqa: BLE001
        return [f"PMX 回读失败：{e}"]

    nv = len(m.vertices)
    nb = len(m.bones)
    nm = len(m.morphs)
    nmat = len(m.materials)

    if nv == 0:
        problems.append("顶点数为 0")
    if nb == 0:
        problems.append("骨骼数为 0")

    # faces
    if len(m.faces) % 3 != 0:
        problems.append(f"索引数 {len(m.faces)} 不是 3 的倍数")
    if len(m.faces):
        if m.faces.min() < 0 or m.faces.max() >= nv:
            problems.append(f"面索引越界：{m.faces.min()}..{m.faces.max()} / 顶点 {nv}")

    # material surface counts must add up
    total = sum(mt.surface_count for mt in m.materials)
    if total != len(m.faces):
        problems.append(f"材质面数合计 {total} != 索引数 {len(m.faces)}")

    # vertices
    bad_w = 0
    for v in m.vertices:
        if any(b < 0 or b >= nb for b in v.bones):
            bad_w += 1
            continue
        if v.weight_type == 0:
            continue                      # BDEF1: weight is implicit 1.0
        if v.weight_type == 1:
            # BDEF2 stores only w1, w2 is implicitly 1-w1
            if not (0.0 <= v.weights[0] <= 1.0 + 1e-4):
                bad_w += 1
            continue
        s = sum(v.weights)                # BDEF4 / QDEF / SDEF
        if abs(s - 1.0) > 0.02:
            bad_w += 1
    if bad_w:
        problems.append(f"{bad_w} 个顶点权重异常（和不为 1 或骨骼索引越界）")

    # bones
    for i, b in enumerate(m.bones):
        if b.parent >= nb:
            problems.append(f"骨骼 {b.name} 的父索引越界")
            break
        if b.ik_target >= nb:
            problems.append(f"骨骼 {b.name} 的 IK 目标越界")
            break

    # morphs
    for mo in m.morphs:
        if mo.type == 1:
            for idx, _ in mo.offsets:
                if idx < 0 or idx >= nv:
                    problems.append(f"表情 {mo.name} 的顶点索引越界")
                    break
        if mo.type == 8:
            for off in mo.offsets:
                if off[0] < 0 or off[0] >= nmat:
                    problems.append(f"表情 {mo.name} 的材质索引越界")
                    break

    # physics
    nr = len(m.rigid_bodies)
    for r in m.rigid_bodies:
        if r.bone >= nb:
            problems.append(f"刚体 {r.name} 关联骨骼越界")
            break
        if r.mass <= 0 or not np.isfinite(r.mass):
            problems.append(f"刚体 {r.name} 质量非法：{r.mass}")
            break
    for j in m.joints:
        if j.body_a >= nr or j.body_b >= nr:
            problems.append(f"关节 {j.name} 刚体索引越界")
            break
        if not np.all(np.isfinite(j.spring_rot)):
            problems.append(f"关节 {j.name} 弹簧参数非法")
            break

    # textures exist
    base = os.path.dirname(os.path.abspath(path))
    for t in m.textures:
        p = os.path.join(base, t.replace("\\", os.sep))
        if not os.path.exists(p):
            problems.append(f"贴图缺失：{t}")
            break

    return problems
