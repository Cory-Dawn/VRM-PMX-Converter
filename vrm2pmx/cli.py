# -*- coding: utf-8 -*-
"""Command line entry point: python -m vrm2pmx.cli a.vrm b.vrm --scale 14"""
from __future__ import annotations

import argparse
import os
import sys

from .convert import ConvertOptions, Converter
from .validate import validate


def main(argv=None):
    ap = argparse.ArgumentParser(description="VRM → PMX 批量转换")
    ap.add_argument("inputs", nargs="+", help="一个或多个 .vrm 文件")
    ap.add_argument("-o", "--out", default="", help="输出目录（默认与源文件同目录）")
    ap.add_argument("-s", "--scale", type=float, default=14.0, help="缩放倍数，默认 14")
    ap.add_argument("-p", "--pose", type=float, default=30.0, help="手臂下摆角度，0 表示保持 T-pose")
    ap.add_argument("--no-physics", action="store_true", help="不生成物理")
    ap.add_argument("--strength", type=float, default=1.0, help="物理强度倍率（仅旧预设使用）")
    ap.add_argument("--preset", default="universal", help="物理预设，默认 universal（通用版 v14，不按名称分类）；旧正式版 final；诊断 universal_nocol（通用版无碰撞）、final_v14singles（final + v14 单段链）；旧版 universal_v7 / universal_v6 / universal_soft / universal_v5 仅供对比；调试可用 zero/chain 等")
    ap.add_argument("--softness", type=float, default=0.75,
                    help="通用版柔软度，默认 0.75；调小更硬、回落更快，建议 0.55–1.0")
    ap.add_argument("--jp-names", action="store_true", help="把材质/形态键名称里的中文换成日文字形")
    args = ap.parse_args(argv)

    ok = 0
    for path in args.inputs:
        print(f"\n===== {os.path.basename(path)} =====")
        opt = ConvertOptions(
            scale=args.scale,
            pose_angle_deg=args.pose,
            export_physics=not args.no_physics,
            physics_strength=args.strength,
            physics_softness=args.softness,
            physics_preset=args.preset,
            localize_names=args.jp_names,
            out_dir=args.out,
        )
        rep = Converter(opt).convert(path)
        if not rep.ok:
            print("失败：", "；".join(rep.errors))
            continue
        for w in rep.warnings:
            print("提示：", w)
        print(f"输出：{rep.out_pmx}")
        print(f"顶点 {rep.vertices}  三角面 {rep.triangles}  骨骼 {rep.bones}  "
              f"表情 {rep.morphs}  材质 {rep.materials}  贴图 {rep.textures}")
        if rep.rigid_bodies:
            print(f"物理：刚体 {rep.rigid_bodies}  关节 {rep.joints}")
        problems = validate(rep.out_pmx)
        if problems:
            for p in problems:
                print("自检问题：", p)
        else:
            print("自检通过")
        ok += 1
    print(f"\n完成 {ok}/{len(args.inputs)}")
    return 0 if ok == len(args.inputs) else 1


if __name__ == "__main__":
    sys.exit(main())
