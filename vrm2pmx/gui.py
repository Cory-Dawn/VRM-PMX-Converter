# -*- coding: utf-8 -*-
"""Desktop UI (v1.4, modern minimal). The flat controls live in widgets.py;
tkinterdnd2 provides native file drag and drop.

Layout:
    title row      logo, name, version, theme switch (跟随系统 / 浅色 / 深色)
    left card      model list (title / author from the VRM meta, size, status)
    right card     settings: 输出 / 模型 / 物理 / 名称, "恢复默认"
    bottom row     status, thin progress line, log toggle, primary button
    log            collapsible, opens by itself when something fails
Settings are remembered between runs (settings.json in the user's app data).
Light and dark palettes (widgets.LIGHT / DARK); "跟随系统" follows the Windows
app theme and re-checks it every few seconds.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

try:                                    # native file drag and drop (optional)
    from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD
    _TkBase = TkinterDnD.Tk
except Exception:  # noqa: BLE001 - missing package or tkdnd binary: run without drag and drop
    COPY = DND_FILES = REFUSE_DROP = None
    TkinterDnD = None
    _TkBase = tk.Tk

from . import __version__
from .convert import ConvertOptions, Converter
from .validate import validate
from .vrm import VRM
from . import widgets as W
from .widgets import ProgressLine, RoundButton, RoundedBox, RoundEntry, Segmented, Slider, Stepper, Toggle

# colour names (ACCENT, CARD, TEXT, ...) are module globals taken from the
# current palette; _use_palette() swaps them before the UI is (re)built
globals().update(W.LIGHT)


def _use_palette(mode: str):
    globals().update(W.set_palette(mode))

APP_NAME = "VRM → PMX 转换器"
APP_TITLE = f"{APP_NAME}  v{__version__}"

DEFAULTS = {
    "out_mode": "same",          # same: next to the source file, custom: out_dir
    "out_dir": "",
    "scale": 14.0,
    "pose": 30.0,
    "physics": True,
    "preset": "universal",       # universal / final
    "softness": 0.75,
    "jp_names": False,
    "theme": "system",           # system / light / dark
}

PRESET_HELP = {
    "universal": "逐链换算 VRM 弹簧参数，不依赖骨骼名称",
    "final": "按骨骼名称分类的旧方案，仅供对比",
}
STATUS_TEXT = {"wait": "待转换", "run": "转换中", "ok": "完成", "fail": "失败", "skip": "已跳过"}


def _asset_path(*parts: str) -> str:
    """Path to bundled assets, both in source tree and PyInstaller onefile."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def _settings_path() -> str:
    root = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(root, "VRM2PMX", "settings.json")


def _sanitize_model_name(name: str, fallback: str) -> str:
    name = (name or "").strip()
    name = "".join(c for c in name if c not in '\\/:*?"<>|').strip()
    return name or fallback


def _vrm_meta(path: str) -> tuple[str, str]:
    """(title, author) from the VRM meta; empty strings when unreadable."""
    try:
        meta = VRM(path).ext.get("meta", {})
        return str(meta.get("title") or ""), str(meta.get("author") or "")
    except Exception:  # noqa: BLE001
        return "", ""


def _model_name_for_file(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    title, _ = _vrm_meta(path)
    return _sanitize_model_name(title or stem, stem)


def _find_available_model_name(base: str, root: str, used: set[str]) -> str:
    if base not in used and not os.path.exists(os.path.join(root, base)):
        return base
    i = 1
    while True:
        candidate = f"{base}({i})"
        if candidate not in used and not os.path.exists(os.path.join(root, candidate)):
            return candidate
        i += 1


def _pick_font_family(root) -> str:
    have = set(tkfont.families(root))
    for fam in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
                "WenQuanYi Zen Hei", "Segoe UI"):
        if fam in have:
            return fam
    return tkfont.nametofont("TkDefaultFont").actual("family")


class Tooltip:
    """Small hover tooltip for any widget."""

    def __init__(self, widget, text: str):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _work_area(self):
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class MonitorInfo(ctypes.Structure):
                    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                                ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

                user32 = ctypes.windll.user32
                user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
                user32.MonitorFromPoint.restype = wintypes.HANDLE
                user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
                user32.GetMonitorInfoW.restype = wintypes.BOOL
                point = wintypes.POINT(self.widget.winfo_rootx(), self.widget.winfo_rooty())
                monitor = user32.MonitorFromPoint(point, 2)
                info = MonitorInfo()
                info.size = ctypes.sizeof(info)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    return info.work.left, info.work.top, info.work.right, info.work.bottom
            except (AttributeError, OSError, tk.TclError):
                pass
        return 0, 0, self.widget.winfo_screenwidth(), self.widget.winfo_screenheight()

    def _show(self, _e=None):
        if self.tip or not self.text:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.withdraw()
        self.tip.wm_overrideredirect(True)
        background = TIP_BG
        if sys.platform == "win32":
            try:
                self.tip.wm_attributes("-transparentcolor", TIP_KEY)
                background = TIP_KEY
            except tk.TclError:
                pass
        self.tip.configure(background=background)
        canvas = tk.Canvas(self.tip, background=background, highlightthickness=0, borderwidth=0)
        canvas.pack()
        label = tk.Label(canvas, text=self.text, justify="left", anchor="w",
                         background=TIP_BG, foreground=TIP_FG, wraplength=300)
        label.update_idletasks()
        width, height = label.winfo_reqwidth() + 24, label.winfo_reqheight() + 16
        canvas.configure(width=width, height=height)
        W._smooth_rect(canvas, 0, 0, width, height, 8, TIP_BORDER)
        W._smooth_rect(canvas, 1, 1, width - 1, height - 1, 7, TIP_BG)
        canvas.create_window(12, 8, window=label, anchor="nw")
        left, top, right, bottom = self._work_area()
        x = max(left, min(self.widget.winfo_rootx() + 8, right - width))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + height > bottom:
            y = self.widget.winfo_rooty() - height - 6
        y = max(top, min(y, bottom - height))
        self.tip.wm_geometry(f"{width}x{height}{x:+d}{y:+d}")
        self.tip.deiconify()

    def _hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class App(_TkBase):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        try:
            self.iconbitmap(_asset_path("assets", "VRM2PMX.ico"))
        except Exception:  # noqa: BLE001
            pass
        self.geometry("1080x760")
        self.minsize(960, 720)
        self.configure(background=BG)

        s = dict(DEFAULTS)
        s.update(self._load_settings())
        self.files: list[dict] = []           # {path, title, author, size, status, out}
        self.out_mode = tk.StringVar(value=s["out_mode"])
        self.out_dir = tk.StringVar(value=s["out_dir"])
        self.scale = tk.DoubleVar(value=s["scale"])
        self.pose = tk.DoubleVar(value=s["pose"])
        self.physics = tk.BooleanVar(value=s["physics"])
        self.preset = tk.StringVar(value=s["preset"] if s["preset"] in PRESET_HELP else "universal")
        self.softness = tk.DoubleVar(value=s["softness"])
        self.jp_names = tk.BooleanVar(value=s["jp_names"])
        self.theme = tk.StringVar(value=s["theme"] if s["theme"] in ("system", "light", "dark") else "system")
        self._mode = ""                       # palette in use: light / dark
        self._progress_val = 0.0
        self._log_lines: list[tuple[str, str]] = []
        self.status = tk.StringVar(value="")
        self._busy = False
        self._log_open = False
        self._q: queue.Queue = queue.Queue()

        self._fonts()
        for var in (self.out_mode, self.physics, self.preset):
            var.trace_add("write", lambda *_: self._sync())
        self.softness.trace_add("write", lambda *_: self._value_labels())
        self.pose.trace_add("write", lambda *_: self._value_labels())
        self.theme.trace_add("write", lambda *_: self.apply_theme())
        self.apply_theme()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(3000, self._watch_system_theme)

    # ---------------------------------------------------------------- theme
    def _wanted_mode(self) -> str:
        t = self.theme.get()
        if t == "system":
            return "dark" if W.system_prefers_dark() else "light"
        return t

    def apply_theme(self, force: bool = False):
        """(Re)build the whole window in the light or dark palette; keeps every value."""
        mode = self._wanted_mode()
        if mode == self._mode and not force:
            return
        previous_theme_position = (self.theme_segment._motion.value
                                   if hasattr(self, "theme_segment") else None)
        self._mode = mode
        _use_palette(mode)
        W.set_dark_titlebar(self, mode == "dark", caption=BG, text=TEXT)   # before the rebuild
        for child in self.winfo_children():
            if not isinstance(child, tk.Toplevel):     # keep open dialogs
                child.destroy()
        self.configure(background=BG)
        self._styles()
        self._build()
        if previous_theme_position is not None:
            self.theme_segment._motion.jump(previous_theme_position)
            self.theme_segment._motion.to(self.theme_segment._selected_index())
        self.log.configure(state="normal")
        for text, tag in self._log_lines:
            self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")
        self.toggle_log(self._log_open)
        self.progress.set(self._progress_val, animate=False)
        self._sync()
        self._value_labels()
        self._refresh()
        if self._busy:
            self.go.configure_button(text="正在转换…", state="disabled")

    def _watch_system_theme(self):
        if self.theme.get() == "system":
            self.apply_theme()
        self.after(3000, self._watch_system_theme)

    # ----------------------------------------------------------------- look
    def _fonts(self):
        fam = _pick_font_family(self)
        self.f_base = (fam, 10)
        self.f_small = (fam, 9)
        self.f_bold = (fam, 10, "bold")
        self.f_value = ("Segoe UI" if sys.platform == "win32" else fam, 10, "bold")
        self.f_section = (fam, 9, "bold")
        self.f_title = (fam, 16, "bold")
        self.f_card = (fam, 12, "bold")
        self.f_go = (fam, 11, "bold")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=fam, size=10)
            except tk.TclError:
                pass

    def _styles(self):
        st = ttk.Style(self)
        st.theme_use("clam")      # the only stock theme that honours flat colours everywhere
        st.configure("Flat.Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                     rowheight=36, borderwidth=0, relief="flat", font=self.f_base)
        st.layout("Flat.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Flat.Treeview.Heading", background=CARD, foreground=MUTED, font=self.f_small,
                     relief="flat", borderwidth=0, padding=(6, 6))
        st.map("Flat.Treeview.Heading", background=[("active", CARD)])
        st.map("Flat.Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", TEXT)])
        st.configure("Flat.Vertical.TScrollbar", gripcount=0, background=SCROLL, troughcolor=CARD,
                     bordercolor=CARD, lightcolor=SCROLL, darkcolor=SCROLL, arrowsize=0, relief="flat")
        st.map("Flat.Vertical.TScrollbar", background=[("active", SCROLL_HOVER)])
        st.configure("Flat.TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, bordercolor=LINE,
                     lightcolor=LINE, darkcolor=LINE, padding=(8, 5))
        st.map("Flat.TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
               fieldbackground=[("disabled", FIELD_OFF)], foreground=[("disabled", DISABLED)])

    # ---------------------------------------------------------------- helpers
    def _label(self, parent, text="", kind="body", **kw):
        font, fg = {"body": (self.f_base, TEXT), "muted": (self.f_small, MUTED),
                    "section": (self.f_section, MUTED), "card": (self.f_card, TEXT),
                    "value": (self.f_value, ACCENT), "warn": (self.f_small, BAD)}[kind]
        return tk.Label(parent, text=text, font=font, foreground=fg, background=kw.pop("bg", CARD),
                        anchor="w", justify="left", **kw)

    def _card(self, parent, **pack):
        box = RoundedBox(parent, radius=14, pad=(22, 18), fill_height=True, bg=BG)
        box.pack(**pack)
        return box.inner

    def _section(self, parent, title, first=False):
        if not first:
            tk.Frame(parent, background=LINE, height=1).pack(fill="x", pady=(12, 10))
        self._label(parent, title, "section").pack(fill="x", pady=(0, 6))

    # --------------------------------------------------------------- layout
    def _build(self):
        # title row
        top = tk.Frame(self, background=BG, padx=28, pady=18)
        top.pack(fill="x")
        try:
            self._logo = tk.PhotoImage(file=_asset_path("assets", "VRM2PMX_32.png"))
            tk.Label(top, image=self._logo, background=BG).pack(side="left", padx=(0, 12))
        except Exception:  # noqa: BLE001
            pass
        tk.Label(top, text=APP_NAME, font=self.f_title, foreground=TEXT, background=BG).pack(side="left")
        tk.Label(top, text=f" v{__version__} ", font=self.f_small, foreground=ACCENT,
                 background=ACCENT_SOFT).pack(side="left", padx=(10, 0), pady=(4, 0))
        self.theme_segment = Segmented(top, self.theme,
                                       [("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")],
                                       font=self.f_small, bg=BG, height=30, width=228)
        self.theme_segment.pack(side="right")

        # bottom row (packed before the body so it never gets squeezed out)
        bottom = tk.Frame(self, background=BG, padx=28, pady=16)
        bottom.pack(side="bottom", fill="x")
        bar = tk.Frame(bottom, background=BG)
        bar.pack(fill="x")
        self.log_btn = RoundButton(bar, "日志", self.toggle_log, kind="link", font=self.f_small, padx=0, pady=4,
                                   width=64)
        self.log_btn.pack(side="left", anchor="s")
        self.go = RoundButton(bar, "开始转换", self.start, kind="primary", font=self.f_go, padx=34, pady=11,
                              radius=10)
        self.go.pack(side="right")
        mid = tk.Frame(bar, background=BG)
        mid.pack(side="left", fill="x", expand=True, padx=(18, 22))
        status_row = tk.Frame(mid, background=BG)
        status_row.pack(fill="x")
        tk.Label(status_row, text="CoryDawn 的 Vibe Coding 作品", font=self.f_small,
                 foreground=MUTED, background=BG).pack(side="right", padx=(12, 0))
        tk.Label(status_row, textvariable=self.status, font=self.f_small, foreground=MUTED, background=BG,
                 anchor="w").pack(side="left", fill="x", expand=True)
        self.progress = ProgressLine(mid, bg=BG)
        self.progress.pack(fill="x", pady=(6, 0))

        # body
        body = tk.Frame(self, background=BG, padx=28)
        body.pack(fill="both", expand=True)
        right = tk.Frame(body, background=BG, width=370)
        right.pack(side="right", fill="y", padx=(18, 0))
        right.pack_propagate(False)
        self._build_files(body)
        self._build_settings(right)

    def _build_files(self, parent):
        card = self._card(parent, side="left", fill="both", expand=True)
        head = tk.Frame(card, background=CARD)
        head.pack(fill="x", pady=(0, 14))
        self._label(head, "模型", "card").pack(side="left")
        self.count_lbl = self._label(head, "", "muted")
        self.count_lbl.pack(side="left", padx=(10, 0), pady=(3, 0))
        self.btn_clear = RoundButton(head, "清空", self.clear_files, font=self.f_small, padx=12, pady=5, bg=CARD)
        self.btn_clear.pack(side="right")
        self.btn_remove = RoundButton(head, "移除", self.remove_selected, font=self.f_small, padx=12, pady=5, bg=CARD)
        self.btn_remove.pack(side="right", padx=(0, 6))
        RoundButton(head, "添加文件夹", self.add_folder, font=self.f_small, padx=12, pady=5,
                    bg=CARD).pack(side="right", padx=(0, 6))
        RoundButton(head, "＋ 添加模型", self.add_files, font=self.f_small, padx=12, pady=5,
                    bg=CARD).pack(side="right", padx=(0, 6))

        holder = tk.Frame(card, background=CARD)
        holder.pack(side="top", fill="both", expand=True)
        self.tree = ttk.Treeview(holder, columns=("title", "author", "size", "status"), show="headings",
                                 selectmode="extended", style="Flat.Treeview", height=3)
        for col, text, width, anchor in (("title", "模型名", 240, "w"), ("author", "作者", 150, "w"),
                                         ("size", "大小", 80, "e"), ("status", "状态", 80, "center")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "title"))
        self.tree.tag_configure("ok", foreground=GOOD)
        self.tree.tag_configure("fail", foreground=BAD)
        self.tree.tag_configure("skip", foreground=MUTED)
        self.tree.tag_configure("run", foreground=ACCENT)
        self.tree.tag_configure("wait", foreground=TEXT)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview, style="Flat.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_path())
        self.tree.bind("<ButtonRelease-1>", lambda e: self._refresh()
                       if self.tree.identify_region(e.x, e.y) == "separator" else None, add="+")

        self.empty = tk.Frame(holder, background=CARD)
        tk.Label(self.empty, text="＋", font=(self.f_title[0], 28), foreground=DISABLED,
                 background=CARD).pack()
        self._label(self.empty, "还没有模型", "body").pack()
        self._label(self.empty, "拖放 .vrm 文件到这里，或点击按钮添加" if TkinterDnD is not None
                    else "点击按钮添加 .vrm 文件，或选择一个文件夹批量添加", "muted").pack(pady=(4, 14))
        RoundButton(self.empty, "＋ 添加模型", self.add_files, kind="primary", font=self.f_base, padx=18,
                    pady=7, bg=CARD).pack()
        self._register_drop_area(holder)

        self.path_lbl = self._label(card, "", "muted")
        self.path_lbl.pack(side="bottom", fill="x")
        tk.Frame(card, background=LINE, height=1).pack(side="bottom", fill="x", pady=(10, 8))
        self._build_log(card)

    def _build_settings(self, parent):
        container = self._card(parent, fill="both", expand=True)
        self.settings_scroll = tk.Canvas(container, background=CARD, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.settings_scroll.yview,
                                  style="Flat.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y", padx=(10, 0))
        self.settings_scroll.pack(side="left", fill="both", expand=True)
        self.settings_scroll.configure(yscrollcommand=scrollbar.set)
        card = tk.Frame(self.settings_scroll, background=CARD)
        window = self.settings_scroll.create_window(0, 0, window=card, anchor="nw")

        def layout_settings(_event=None):
            width = self.settings_scroll.winfo_width()
            height = max(card.winfo_reqheight(), self.settings_scroll.winfo_height())
            self.settings_scroll.itemconfigure(window, width=width, height=height)
            self.settings_scroll.configure(scrollregion=(0, 0, width, height))

        self.settings_scroll.bind("<Configure>", layout_settings)
        card.bind("<Configure>", layout_settings)

        # 输出
        self._section(card, "输出", first=True)
        Segmented(card, self.out_mode, [("same", "源文件目录"), ("custom", "指定目录")],
                  font=self.f_small, bg=CARD).pack(fill="x")
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x", pady=(8, 0))
        self.out_entry = RoundEntry(row, self.out_dir, font=self.f_small, bg=CARD)
        self.out_entry.pack(side="left", fill="x", expand=True)
        self.out_btn = RoundButton(row, "浏览", self.pick_out, font=self.f_small, padx=12, pady=5, bg=CARD)
        self.out_btn.pack(side="left", padx=(8, 0))

        # 模型
        self._section(card, "模型")
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x")
        self._label(row, "缩放倍数").pack(side="left")
        self.scale_reset = RoundButton(row, "↺", lambda: self.scale.set(DEFAULTS["scale"]), kind="link",
                                       font=("Segoe UI Symbol", 13), padx=4, pady=0, width=28, bg=CARD)
        self.scale_reset.pack(side="left", padx=(4, 0))
        Tooltip(self.scale_reset, "恢复缩放倍数默认值（14）")
        st = Stepper(row, self.scale, 4, 30, 0.5, font=self.f_base, bg=CARD)
        st.pack(side="right")
        Tooltip(st, "1 米 = 14 个 MMD 单位（默认 14），与 MMD 标准模型的身高一致，通常保持默认。")
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x", pady=(12, 0))
        self._label(row, "手臂下摆").pack(side="left")
        self.pose_reset = RoundButton(row, "↺", lambda: self.pose.set(DEFAULTS["pose"]), kind="link",
                                      font=("Segoe UI Symbol", 13), padx=4, pady=0, width=28, bg=CARD)
        self.pose_reset.pack(side="left", padx=(4, 0))
        Tooltip(self.pose_reset, "恢复手臂下摆默认值（30°）")
        self.pose_val = self._label(row, "", "value")
        self.pose_val.pack(side="right")
        self.pose_slider = Slider(card, self.pose, 0, 60, 5, bg=CARD)
        self.pose_slider.pack(fill="x", pady=(4, 0))
        Tooltip(self.pose_slider, "VRM 是 T-pose（手臂平举），MMD 动作假设手臂下垂。默认 30°；0° 保持原姿势。")

        # 物理
        self._section(card, "物理")
        Toggle(card, self.physics, "生成物理（刚体 + 关节）", font=self.f_base, bg=CARD).pack(anchor="w")
        self.seg_preset = Segmented(card, self.preset, [("universal", "通用（推荐）"), ("final", "正式（旧版）")],
                                    font=self.f_small, bg=CARD)
        self.seg_preset.pack(fill="x", pady=(10, 0))
        self.preset_help = self._label(card, "", "muted")
        self.preset_help.pack(fill="x", pady=(6, 0))
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x", pady=(10, 0))
        self.soft_title = self._label(row, "柔软度")
        self.soft_title.pack(side="left")
        self.soft_reset = RoundButton(row, "↺", lambda: self.softness.set(DEFAULTS["softness"]), kind="link",
                                      font=("Segoe UI Symbol", 13), padx=4, pady=0, width=28, bg=CARD)
        self.soft_reset.pack(side="left", padx=(4, 0))
        Tooltip(self.soft_reset, "恢复柔软度默认值（0.75）")
        self.soft_val = self._label(row, "", "value")
        self.soft_val.pack(side="right")
        self.soft_slider = Slider(card, self.softness, 0.5, 1.0, 0.05, bg=CARD)
        self.soft_slider.pack(fill="x", pady=(4, 0))
        ends = tk.Frame(card, background=CARD)
        ends.pack(fill="x")
        self._label(ends, "更硬", "muted").pack(side="left")
        self._label(ends, "更软", "muted").pack(side="right")
        Tooltip(self.soft_slider, "默认 0.75。调小更硬、回落更快；调大更柔软、摆得更开。只对通用物理有效。")

        # 名称
        self._section(card, "名称")
        tg = Toggle(card, self.jp_names, "材质 / 表情名转日文字形", font=self.f_base, bg=CARD)
        tg.pack(anchor="w")
        Tooltip(tg, "防止 PMX Editor / MMD 显示乱码。会改写名称：旧 VMD 里指向中文表情名的轨道可能失效。")

        foot = tk.Frame(card, background=CARD)
        foot.pack(side="bottom", fill="x")
        RoundButton(foot, "恢复默认", self.reset_settings, kind="link", font=self.f_small, padx=0, pady=2,
                    bg=CARD).pack(side="right")

        def scroll_settings(event):
            if event.num in (4, 5):
                direction = -1 if event.num == 4 else 1
            else:
                direction = -1 if event.delta > 0 else 1
            self.settings_scroll.yview_scroll(direction * 3, "units")
            return "break"

        def bind_wheel(widget):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, scroll_settings, add="+")
            for child in widget.winfo_children():
                bind_wheel(child)

        bind_wheel(container)

    def _build_log(self, parent):
        self.log_frame = RoundedBox(parent, radius=10, pad=(6, 6), bg=CARD)
        box = self.log_frame.inner
        self.log = tk.Text(box, height=8, wrap="word", font=("Consolas", 9), relief="flat",
                           background=CARD, foreground=TEXT, insertbackground=TEXT, selectbackground=ACCENT_SOFT,
                           selectforeground=TEXT, padx=12, pady=8, highlightthickness=0, borderwidth=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(box, orient="vertical", command=self.log.yview,
                            style="Flat.Vertical.TScrollbar")
        lsb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=lsb.set, state="disabled")
        self.log.tag_configure("head", font=("Consolas", 9, "bold"), foreground=ACCENT)
        self.log.tag_configure("bad", foreground=BAD)
        self.log.tag_configure("good", foreground=GOOD)

    # ------------------------------------------------------------- reactions
    def _sync(self):
        """Enable / disable the controls that depend on other choices."""
        custom = self.out_mode.get() == "custom" and not self._busy
        self.out_entry.set_enabled(custom)
        self.out_btn.configure_button(state="normal" if custom else "disabled")
        phys = self.physics.get()
        self.seg_preset.set_enabled(phys)
        soft_on = phys and self.preset.get() == "universal"
        self.soft_slider.set_enabled(soft_on)
        self.soft_reset.configure_button(state="normal" if soft_on else "disabled")
        self.soft_title.configure(foreground=TEXT if soft_on else DISABLED)
        self.soft_val.configure(foreground=ACCENT if soft_on else DISABLED)
        self.preset_help.configure(text=PRESET_HELP[self.preset.get()] if phys else "不生成刚体和关节，头发等不会摆动。")

    def _value_labels(self):
        try:
            self.soft_val.configure(text=f"{float(self.softness.get()):.2f}")
            v = float(self.pose.get())
            self.pose_val.configure(text=f"{v:.0f}°" + ("  T-pose" if v == 0 else ""))
        except (tk.TclError, ValueError):
            pass

    def toggle_log(self, show: bool | None = None):
        self._log_open = (not self._log_open) if show is None else show
        if self._log_open:
            self.log_frame.pack(side="bottom", fill="x", pady=(10, 0))
            self.log_btn.configure_button(text="收起日志")
        else:
            self.log_frame.pack_forget()
            self.log_btn.configure_button(text="日志")

    # -------------------------------------------------------------- settings
    def _collect_settings(self) -> dict:
        def num(var, default):
            try:
                return float(var.get())
            except (tk.TclError, ValueError):
                return default
        return {
            "out_mode": self.out_mode.get(), "out_dir": self.out_dir.get().strip(),
            "scale": num(self.scale, DEFAULTS["scale"]), "pose": num(self.pose, DEFAULTS["pose"]),
            "physics": bool(self.physics.get()), "preset": self.preset.get(),
            "softness": num(self.softness, DEFAULTS["softness"]), "jp_names": bool(self.jp_names.get()),
            "theme": self.theme.get(),
        }

    @staticmethod
    def _load_settings() -> dict:
        try:
            with open(_settings_path(), encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if k in DEFAULTS and isinstance(v, type(DEFAULTS[k]))}
        except Exception:  # noqa: BLE001
            return {}

    def _save_settings(self):
        try:
            p = _settings_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self._collect_settings(), f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    def reset_settings(self):
        for key, var in (("out_mode", self.out_mode), ("out_dir", self.out_dir), ("scale", self.scale),
                         ("pose", self.pose), ("physics", self.physics), ("preset", self.preset),
                         ("softness", self.softness), ("jp_names", self.jp_names)):
            var.set(DEFAULTS[key])

    def _on_close(self):
        if self._busy and not messagebox.askyesno(APP_TITLE, "正在转换，确定要退出吗？"):
            return
        self._save_settings()
        self.destroy()

    # -------------------------------------------------------------- file ops
    def _register_drop_area(self, widget):
        if TkinterDnD is None or not hasattr(widget, "drop_target_register"):
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<DropEnter>>", lambda _event: REFUSE_DROP if self._busy else COPY)
        widget.dnd_bind("<<DropPosition>>", lambda _event: REFUSE_DROP if self._busy else COPY)
        widget.dnd_bind("<<Drop>>", self._drop_files)
        for child in widget.winfo_children():
            self._register_drop_area(child)

    def _drop_files(self, event):
        if self._busy:
            return REFUSE_DROP
        try:
            paths = self.tk.splitlist(event.data)
        except tk.TclError:
            return REFUSE_DROP
        self._add_paths([p for p in paths if os.path.isfile(p)])
        return COPY

    def _add_paths(self, paths):
        known = {os.path.normcase(f["path"]) for f in self.files}
        new = []
        for p in paths:
            p = os.path.abspath(p)
            key = os.path.normcase(p)
            if key in known or not p.lower().endswith(".vrm"):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            item = {"path": p, "title": "", "author": "", "size": size, "status": "wait", "out": ""}
            self.files.append(item)
            known.add(key)
            new.append(item)
        self._refresh()
        if new:
            threading.Thread(target=self._read_meta, args=(new,), daemon=True).start()

    def _read_meta(self, items):
        for it in items:
            title, author = _vrm_meta(it["path"])
            self._q.put(("meta", (it["path"], title, author)))

    def add_files(self):
        if not self._busy:
            self._add_paths(filedialog.askopenfilenames(
                title="选择 VRM 模型", filetypes=[("VRM 模型", "*.vrm"), ("所有文件", "*.*")]))

    def add_folder(self):
        if self._busy:
            return
        d = filedialog.askdirectory(title="选择包含 VRM 模型的文件夹")
        if not d:
            return
        found = []
        for root, _dirs, names in os.walk(d):
            found += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(".vrm")]
        if not found:
            messagebox.showinfo(APP_TITLE, "这个文件夹里没有找到 .vrm 文件。")
        self._add_paths(found)

    def remove_selected(self):
        if self._busy:
            return
        drop = {int(i) for i in self.tree.selection()}
        self.files = [f for i, f in enumerate(self.files) if i not in drop]
        self._refresh()

    def clear_files(self):
        if self._busy:
            return
        self.files.clear()
        self._refresh()

    def pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_dir.set(d)
            self.out_mode.set("custom")

    def _refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(self.files):
            stem = os.path.splitext(os.path.basename(f["path"]))[0]
            size = f"{f['size'] / 1024 / 1024:.1f} MB" if f["size"] else "—"
            dot = {"wait": "○", "run": "◐", "ok": "●", "fail": "●", "skip": "○"}[f["status"]]
            self.tree.insert("", "end", iid=str(i), tags=(f["status"],),
                             values=(self._fit("  " + (f["title"] or stem), "title"),
                                     self._fit(f["author"] or "—", "author"), size,
                                     f"{dot} {STATUS_TEXT[f['status']]}"))
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_add(i)
        n = len(self.files)
        if n:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
        done = sum(f["status"] == "ok" for f in self.files)
        self.count_lbl.configure(text=(f"{n} 个" + (f" · 已完成 {done}" if done else "")) if n else "")
        state = "disabled" if self._busy or not n else "normal"
        self.btn_remove.configure_button(state=state)
        self.btn_clear.configure_button(state=state)
        if not self._busy:
            self.go.configure_button(text=f"开始转换  {n}" if n > 1 else "开始转换",
                                     state="normal" if n else "disabled")
            self.status.set(f"{n} 个模型，准备就绪" if n else "添加模型后即可开始")
        self._show_path()

    def _fit(self, text: str, column: str) -> str:
        """Cut text to the column width with an ellipsis (Treeview clips silently)."""
        if not hasattr(self, "_measure"):
            self._measure = tkfont.Font(font=self.f_base)
        room = int(self.tree.column(column, "width")) - 16
        if self._measure.measure(text) <= room:
            return text
        while text and self._measure.measure(text + "…") > room:
            text = text[:-1]
        return text + "…"

    def _show_path(self):
        sel = self.tree.selection()
        if len(sel) == 1:
            f = self.files[int(sel[0])]
            who = f"{f['title']}" + (f" · {f['author']}" if f["author"] else "") + "    " if f["title"] else ""
            self.path_lbl.configure(text=who + (("输出  " + f["out"]) if f["out"] else f["path"]))
        elif any(f["status"] == "ok" for f in self.files):
            self.path_lbl.configure(text="双击已完成的模型，打开输出文件夹")
        else:
            self.path_lbl.configure(text="选中模型可查看路径 · Delete 键移除")

    def _on_double(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            f = self.files[int(iid)]
            self._open_and_select(f["out"] or f["path"])

    # ------------------------------------------------------------- conversion
    def _target_root(self, path: str) -> str:
        if self.out_mode.get() == "custom" and self.out_dir.get().strip():
            return self.out_dir.get().strip()
        return os.path.dirname(os.path.abspath(path))

    def start(self):
        if self._busy or not self.files:
            return
        if self.out_mode.get() == "custom" and not self.out_dir.get().strip():
            messagebox.showinfo(APP_TITLE, "请选择输出目录，或改为「源文件目录」。")
            return
        try:
            float(self.scale.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning(APP_TITLE, "缩放倍数需要是数字。")
            return
        opts = self._collect_settings()

        jobs: list[tuple[int, str, str, str]] = []
        used: set[str] = set()
        skipped: list[int] = []
        skip_existing = False
        for idx, f in enumerate(self.files):
            path = f["path"]
            base = _model_name_for_file(path)
            root = self._target_root(path)
            next_name = _find_available_model_name(base, root, used)
            if os.path.exists(os.path.join(root, base)):
                choice = ("skip" if skip_existing else
                          self._ask_conflict(base, os.path.join(root, base), next_name,
                                             allow_skip=len(self.files) > 1))
                if choice is None:
                    self.status.set("已取消")
                    return
                if choice == "skip":
                    skip_existing = True
                    skipped.append(idx)
                    continue
                model_name = base if choice == "overwrite" else next_name
            else:
                model_name = next_name
            if model_name in used:
                model_name = _find_available_model_name(model_name, root, used)
            used.add(model_name)
            out_dir = self.out_dir.get().strip() if self.out_mode.get() == "custom" else ""
            jobs.append((idx, path, model_name, out_dir))

        self._save_settings()
        for idx in skipped:
            if self.files[idx]["status"] != "ok":
                self.files[idx]["status"] = "skip"
        for idx, _, _, _ in jobs:
            self.files[idx]["status"], self.files[idx]["out"] = "wait", ""
        if not jobs:
            self._refresh()
            self.status.set(f"没有需要转换的模型，已跳过 {len(skipped)} 个")
            return
        self._busy = True
        self._sync()
        self.go.configure_button(text="正在转换…", state="disabled")
        self._progress_val = 0.0
        self.progress.set(0)
        self._log_clear()
        self._refresh()
        threading.Thread(target=self._run, args=(jobs, opts, len(skipped)), daemon=True).start()

    def _center(self, dlg):
        """Place a (withdrawn) dialog over the window, give it the theme's title bar, show it."""
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        W.set_dark_titlebar(dlg, self._mode == "dark", caption=CARD, text=TEXT)
        dlg.deiconify()

    def _dialog(self, title: str, heading: str, body: str, detail: str = ""):
        dlg = tk.Toplevel(self)
        dlg.withdraw()                      # shown by _center() once built and styled
        dlg.title(title)
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.configure(background=CARD)
        try:
            dlg.iconbitmap(_asset_path("assets", "VRM2PMX.ico"))
        except Exception:  # noqa: BLE001
            pass
        frame = tk.Frame(dlg, background=CARD, padx=28, pady=24)
        frame.pack(fill="both", expand=True)
        self._label(frame, heading, "card").pack(fill="x")
        if body:
            self._label(frame, body, "body", wraplength=420).pack(fill="x", pady=(10, 0))
        if detail:
            self._label(frame, detail, "muted", wraplength=420).pack(fill="x", pady=(6, 0))
        buttons = tk.Frame(frame, background=CARD)
        buttons.pack(fill="x", pady=(22, 0))
        return dlg, buttons

    def _ask_conflict(self, base: str, target_dir: str, next_name: str, allow_skip: bool = False):
        result = {"choice": None}
        body = "覆盖会替换其中的同名文件；另存为会新建一个文件夹，原文件保留。"
        if allow_skip:
            body += "\n跳过会保留已有输出，并跳过本次其余已存在的输出目录。"
        dlg, buttons = self._dialog("输出目录已存在", f"「{base}」已经转换过",
                                    body, target_dir)

        def choose(value):
            result["choice"] = value
            dlg.destroy()

        RoundButton(buttons, f"另存为「{next_name}」", lambda: choose("saveas"), kind="primary",
                    font=self.f_base, bg=CARD).pack(side="right")
        RoundButton(buttons, "覆盖", lambda: choose("overwrite"), font=self.f_base,
                    bg=CARD).pack(side="right", padx=(0, 8))
        RoundButton(buttons, "取消", lambda: choose(None), kind="link", font=self.f_base, padx=4,
                    bg=CARD).pack(side="left")
        if allow_skip:
            RoundButton(buttons, "跳过已转换模型", lambda: choose("skip"), font=self.f_base,
                        bg=CARD).pack(side="left", padx=(8, 8))
        dlg.bind("<Escape>", lambda _e: choose(None))
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        self._center(dlg)
        dlg.grab_set()
        dlg.focus_set()
        dlg.wait_window()
        return result["choice"]

    def _run(self, jobs, opts, skipped_count=0):
        total = len(jobs)
        ok_count = 0
        last_dir = ""
        for n, (idx, path, model_name, out_dir) in enumerate(jobs):
            name = os.path.basename(path)
            self._q.put(("row", (idx, "run", "")))
            self._q.put(("status", f"正在转换 {n + 1} / {total}  ·  {name}"))
            self._q.put(("log", (f"===== {name} =====", "head")))
            out_pmx = ""
            try:
                opt = ConvertOptions(
                    scale=float(opts["scale"]),
                    pose_angle_deg=float(opts["pose"]),
                    export_physics=bool(opts["physics"]),
                    physics_strength=1.0,
                    physics_softness=float(opts["softness"]),
                    physics_preset=opts["preset"],
                    localize_names=bool(opts["jp_names"]),
                    model_name=model_name,
                    out_dir=out_dir,
                )
                rep = Converter(opt).convert(path)
                if not rep.ok:
                    self._q.put(("log", ("失败：" + "；".join(rep.errors), "bad")))
                    self._q.put(("row", (idx, "fail", "")))
                    self._q.put(("progress", (n + 1) / total * 100))
                    continue
                for w in rep.warnings:
                    self._q.put(("log", ("提示：" + w, "")))
                out_pmx = rep.out_pmx
                self._q.put(("log", (f"写出：{rep.out_pmx}", "")))
                self._q.put(("log", (f"顶点 {rep.vertices}  三角面 {rep.triangles}  骨骼 {rep.bones}  "
                                     f"表情 {rep.morphs}  材质 {rep.materials}  贴图 {rep.textures}", "")))
                if rep.rigid_bodies:
                    self._q.put(("log", (f"物理：刚体 {rep.rigid_bodies}  关节 {rep.joints}", "")))
                problems = validate(rep.out_pmx)
                if problems:
                    for p in problems:
                        self._q.put(("log", ("自检问题：" + p, "bad")))
                    self._q.put(("row", (idx, "fail", out_pmx)))
                else:
                    self._q.put(("log", ("自检通过：文件结构完整", "good")))
                    self._q.put(("row", (idx, "ok", out_pmx)))
                    ok_count += 1
                    last_dir = os.path.dirname(os.path.abspath(rep.out_pmx))
            except Exception:  # noqa: BLE001
                self._q.put(("log", ("异常：\n" + traceback.format_exc(), "bad")))
                self._q.put(("row", (idx, "fail", out_pmx)))
            self._q.put(("progress", (n + 1) / total * 100))
        self._q.put(("done", (ok_count, total, last_dir, skipped_count)))

    def _poll(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    text, tag = payload
                    self._log(text, tag)
                elif kind == "status":
                    self.status.set(payload)
                elif kind == "progress":
                    self._progress_val = payload
                    self.progress.set(payload)
                elif kind == "meta":
                    path, title, author = payload
                    for f in self.files:
                        if f["path"] == path:
                            f["title"], f["author"] = title, author
                    self._refresh()
                elif kind == "row":
                    idx, st, out = payload
                    if 0 <= idx < len(self.files):
                        self.files[idx]["status"] = st
                        if out:
                            self.files[idx]["out"] = out
                    if st == "fail":
                        self.toggle_log(True)
                    self._refresh()
                elif kind == "done":
                    ok, total, last_dir, skipped_count = payload
                    self._busy = False
                    self._sync()
                    self._refresh()
                    self.status.set(f"完成  {ok} / {total}"
                                    + (f"  ·  已跳过 {skipped_count}" if skipped_count else "")
                                    + ("" if ok == total else "  ·  失败的模型请看日志"))
                    if ok:
                        self._show_done_dialog(ok, total, last_dir, skipped_count)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_done_dialog(self, ok: int, total: int, last_dir: str, skipped_count: int = 0):
        body = f"{ok} / {total} 个模型转换成功。"
        if skipped_count:
            body += f"\n已跳过 {skipped_count} 个已有输出。"
        dlg, buttons = self._dialog(APP_NAME, "转换完成", body,
                                    f"输出目录：{last_dir}" if last_dir else "")
        RoundButton(buttons, "打开输出文件夹", lambda: (self._open_and_select(last_dir), dlg.destroy()),
                    kind="primary", font=self.f_base, bg=CARD).pack(side="right")
        RoundButton(buttons, "关闭", dlg.destroy, font=self.f_base, bg=CARD).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", lambda _e: dlg.destroy())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        self._center(dlg)
        dlg.grab_set()
        dlg.focus_set()

    def _open_and_select(self, path: str):
        if not path or not os.path.exists(path):
            messagebox.showinfo(APP_TITLE, "目标文件或文件夹不存在。")
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        except OSError as e:
            messagebox.showwarning(APP_TITLE, f"无法打开：\n{path}\n\n{e}")

    # ------------------------------------------------------------------- log
    def _log(self, text: str, tag: str = ""):
        self._log_lines.append((text, tag))
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_clear(self):
        self._log_lines.clear()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main():
    App().mainloop()
