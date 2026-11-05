# -*- coding: utf-8 -*-
"""Render the supplied V2P SVG into PNG/ICO assets."""
from __future__ import annotations

import os
import re

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SVG = os.path.join(ASSETS, "VRM2PMX.svg")

FINAL = 1024
SS = 2
W = FINAL * SS


def _hex_color(s: str) -> tuple[int, int, int]:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _parse_svg(svg_text: str):
    vb = re.search(r'viewBox="([^"]+)"', svg_text)
    if not vb:
        raise RuntimeError("viewBox not found")
    vx, vy, vw, vh = [float(x) for x in vb.group(1).split()]
    scale = W / vw

    rect = re.search(r'<rect([^>]+)/>', svg_text)
    if not rect:
        raise RuntimeError("background rect not found")
    rfill = re.search(r'fill="([^"]+)"', rect.group(1))
    rrx = re.search(r'rx="([^"]+)"', rect.group(1))
    bg_color = _hex_color(rfill.group(1)) if rfill else (30, 34, 64)
    bg_radius = float(rrx.group(1)) if rrx else 14.0

    grad = re.search(r'<linearGradient[^>]*x1="([^"]+)"[^>]*y1="([^"]+)"[^>]*x2="([^"]+)"[^>]*y2="([^"]+)"', svg_text)
    stops = []
    for sm in re.finditer(r'<stop[^>]*offset="([^"]+)"[^>]*stop-color="([^"]+)"', svg_text):
        stops.append((float(sm.group(1)), _hex_color(sm.group(2))))
    stops.sort(key=lambda x: x[0])
    g1 = (float(grad.group(1)), float(grad.group(2)))
    g2 = (float(grad.group(3)), float(grad.group(4)))

    path = re.search(r'<path([^>]+)/>', svg_text)
    if not path:
        raise RuntimeError("path not found")
    attrs = path.group(1)
    d = re.search(r'd="([^"]+)"', attrs).group(1)
    stroke_w = float(re.search(r'stroke-width="([^"]+)"', attrs).group(1))
    tx = ty = 0.0
    tr = re.search(r'transform="translate\(([^ )]+)[ ,]+([^ )]+)\)"', attrs)
    if tr:
        tx, ty = float(tr.group(1)), float(tr.group(2))

    tokens = re.findall(r"([MCL])([^MCL]*)", d)
    pts = []
    cur = (0.0, 0.0)
    for cmd, body in tokens:
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", body)]
        if cmd == "M":
            cur = (nums[0], nums[1])
            pts.append(cur)
        elif cmd == "L":
            for i in range(0, len(nums), 2):
                cur = (nums[i], nums[i + 1])
                pts.append(cur)
        elif cmd == "C":
            for i in range(0, len(nums), 6):
                p0 = cur
                p1 = (nums[i], nums[i + 1])
                p2 = (nums[i + 2], nums[i + 3])
                p3 = (nums[i + 4], nums[i + 5])
                for k in range(1, 81):
                    t = k / 80.0
                    u = 1.0 - t
                    x = (u**3 * p0[0] + 3 * u * u * t * p1[0]
                         + 3 * u * t * t * p2[0] + t**3 * p3[0])
                    y = (u**3 * p0[1] + 3 * u * u * t * p1[1]
                         + 3 * u * t * t * p2[1] + t**3 * p3[1])
                    pts.append((x, y))
                cur = p3

    pts = [((x + tx) * scale, (y + ty) * scale) for x, y in pts]
    return pts, bg_color, bg_radius * scale, stroke_w * scale, g1, g2, stops, scale


def _gradient_image(size: int, g1, g2, stops):
    x1, y1 = g1[0] * (size / 64.0), g1[1] * (size / 64.0)
    x2, y2 = g2[0] * (size / 64.0), g2[1] * (size / 64.0)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    dx, dy = x2 - x1, y2 - y1
    denom = dx * dx + dy * dy
    t = np.clip(((xx - x1) * dx + (yy - y1) * dy) / max(denom, 1e-9), 0, 1)
    arr = np.zeros((size, size, 3), dtype=np.float32)
    for i in range(len(stops) - 1):
        o0, c0 = stops[i]
        o1, c1 = stops[i + 1]
        mask = (t >= o0) & (t <= o1) if i == 0 else (t > o0) & (t <= o1)
        local = np.clip((t - o0) / max(o1 - o0, 1e-9), 0, 1)[..., None]
        arr[mask] = (np.array(c0, dtype=np.float32) * (1 - local)
                     + np.array(c1, dtype=np.float32) * local)[mask]
    arr[t <= stops[0][0]] = np.array(stops[0][1], dtype=np.float32)
    arr[t >= stops[-1][0]] = np.array(stops[-1][1], dtype=np.float32)
    return arr.astype(np.uint8)


def _rounded_mask(size: int, radius: int):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def main():
    with open(SVG, "r", encoding="utf-8") as f:
        pts, bg_color, bg_radius, stroke_w, g1, g2, stops, _scale = _parse_svg(f.read())

    canvas = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    bg = Image.new("RGBA", (W, W), bg_color + (255,))
    canvas.paste(bg, (0, 0), _rounded_mask(W, int(bg_radius)))

    width = max(1, int(round(stroke_w)))
    stroke = Image.new("L", (W, W), 0)
    sd = ImageDraw.Draw(stroke)
    r = width // 2
    for a, b in zip(pts, pts[1:]):
        sd.line([a, b], fill=255, width=width)
    for p in pts:
        sd.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=255)

    grad = Image.fromarray(_gradient_image(W, g1, g2, stops), "RGB").convert("RGBA")
    canvas = Image.alpha_composite(canvas, Image.composite(
        grad, Image.new("RGBA", (W, W), (0, 0, 0, 0)), stroke))

    icon = canvas.resize((FINAL, FINAL), Image.Resampling.LANCZOS)
    icon.save(os.path.join(ASSETS, "VRM2PMX_1024.png"))
    for size in (256, 128, 64, 48, 32, 24, 16):
        icon.resize((size, size), Image.Resampling.LANCZOS).save(
            os.path.join(ASSETS, f"VRM2PMX_{size}.png"))
    icon.save(os.path.join(ASSETS, "VRM2PMX.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    sheet = Image.new("RGB", (980, 420), "#f4f2ee")
    d = ImageDraw.Draw(sheet)
    d.rectangle((490, 0, 980, 420), fill="#1b1b1f")
    big = icon.resize((300, 300), Image.Resampling.LANCZOS)
    sheet.paste(big, (40, 60), big)
    x = 380
    for s in (128, 64, 32, 16):
        im = icon.resize((s, s), Image.Resampling.LANCZOS)
        sheet.paste(im, (x, 80), im)
        x += s + 18
    for i, s in enumerate((128, 64, 32, 16)):
        im = icon.resize((s, s), Image.Resampling.LANCZOS)
        sheet.paste(im, (520 + i * 70, 80), im)
    sheet.save(os.path.join(ASSETS, "VRM2PMX_preview.png"))


if __name__ == "__main__":
    main()
