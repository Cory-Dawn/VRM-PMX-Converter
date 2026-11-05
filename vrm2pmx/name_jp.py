# -*- coding: utf-8 -*-
"""Optional Chinese -> Japanese glyph localization for PMX names.

PMX stores Unicode, but Japanese MMD/PMX Editor fonts often cannot render
Chinese glyphs and show mojibake.  This module keeps the original Chinese word
order and only replaces characters with Japanese kanji / Shinjitai glyphs.
Characters with no CP932 (Japanese) equivalent fall back to capitalized
pinyin, e.g. 垫 -> Dian, 绑 -> Bang.

Rules:
  * names containing kana or pure ASCII are left untouched;
  * only material and morph names are renamed, never geometry or morph data;
  * the feature is opt-in and has a VMD-name compatibility risk.
"""
from __future__ import annotations

import re

CJK_RE = re.compile(r"[\u3400-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")

# Ambiguous characters must be resolved by word context:
# 发 = hair in 头发/发圈, but emit/glow in 发光.
PHRASE_MAP: dict[str, str] = {
    "头发": "頭髪",
    "毛发": "毛髪",
    "发圈": "髪圏",
    "发光": "発光",
    "护目镜": "護目鏡",
    "耳朵": "耳朶",
    "手环": "手環",
    "胡须": "胡須",
    "铃铛": "鈴鐺",
    "肉垫": "肉Dian",
    "护目镜绑带": "護目鏡Bang帯",
}

# Character-level Japanese Shinjitai / kanji forms.  This deliberately keeps
# Chinese wording; it does not translate terms into Japanese words.
CHAR_MAP: dict[str, str] = {
    "侧": "側", "关": "関", "发": "発", "壳": "殻", "头": "頭",
    "带": "帯", "开": "開", "护": "護", "挂": "掛", "时": "時",
    "泪": "涙", "环": "環", "红": "紅", "绳": "縄", "藏": "蔵",
    "铃": "鈴", "镜": "鏡", "隐": "隠", "须": "須", "鳍": "鰭",
    "脸": "顔", "脖": "頸", "铛": "鐺", "朵": "朶", "广": "広",
}

_PHRASES = sorted(PHRASE_MAP, key=len, reverse=True)


def _cp932_ok(ch: str) -> bool:
    try:
        ch.encode("cp932")
        return True
    except UnicodeEncodeError:
        return False


def _pinyin(ch: str) -> str:
    """One-character pinyin fallback, capitalized (垫 -> Dian)."""
    try:
        from pypinyin import Style, lazy_pinyin
        parts = lazy_pinyin(ch, style=Style.NORMAL, errors=lambda x: [x])
        if parts and parts[0].isalpha():
            return parts[0].capitalize()
    except Exception:
        pass
    return ch


def localize_name(name: str) -> str:
    """Convert Chinese-looking material/morph names to Japanese glyphs.

    Japanese names (containing kana) and English/ASCII names are returned
    unchanged so standard MMD morph names remain intact.
    """
    if not name or not CJK_RE.search(name) or KANA_RE.search(name):
        return name

    out = name
    for src in _PHRASES:
        if src in out:
            out = out.replace(src, PHRASE_MAP[src])

    result = []
    for ch in out:
        if ch in CHAR_MAP:
            result.append(CHAR_MAP[ch])
        elif _cp932_ok(ch):
            result.append(ch)
        elif CJK_RE.match(ch):
            result.append(_pinyin(ch))
        else:
            result.append(ch)
    return "".join(result)


def localize_pmx_names(materials, morphs) -> list[tuple[str, str]]:
    """Rename material and morph names in place; return changed pairs."""
    changed: list[tuple[str, str]] = []
    for obj in list(materials) + list(morphs):
        old = obj.name
        new = localize_name(old)
        if new != old:
            obj.name = new
            changed.append((old, new))
    return changed
