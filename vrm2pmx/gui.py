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
import math
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
from .i18n import LANGUAGES, normalize_language, translate
from .i18n_diagnostics import translate_diagnostic
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
    "adjustment_bones": False,
    "adjustment_x": -20.0,
    "adjustment_y": 0.0,
    "adjustment_z": 0.0,
    "language": "zh",          # interface only; does not change exported names
    "theme": "system",           # system / light / dark
}

PRESET_HELP = {
    "universal": "逐链换算 VRM 弹簧参数，不依赖骨骼名称",
    "final": "按骨骼名称分类的旧方案，仅供对比",
}
STATUS_TEXT = {"wait": "待转换", "run": "转换中", "ok": "完成", "fail": "失败", "skip": "已跳过"}


def _auto_scrollbar(scrollbar, viewport, **packing):
    """Hide an unnecessary scrollbar and restore it before its viewport."""
    def update(first, last):
        scrollbar.set(first, last)
        visible = float(first) > 0.0 or float(last) < 1.0
        if visible and not scrollbar.winfo_manager():
            scrollbar.pack(before=viewport, **packing)
        elif not visible and scrollbar.winfo_manager():
            scrollbar.pack_forget()
    return update


class _SmoothScroll:
    """Ease wheel scrolling while leaving scrollbar dragging direct."""

    def __init__(self, viewport, fraction_per_notch):
        self.viewport = viewport
        self.fraction_per_notch = fraction_per_notch
        self.motion = W._Animation(viewport, viewport.yview()[0], self._apply)

    def _apply(self):
        self.viewport.yview_moveto(self.motion.value)

    def wheel(self, event):
        first, last = self.viewport.yview()
        if last - first >= 1.0:
            return "break"
        if getattr(event, "num", None) in (4, 5):
            amount = -1.0 if event.num == 4 else 1.0
        else:
            amount = -float(getattr(event, "delta", 0)) / 120.0
        if not amount:
            return "break"
        if self.motion.job is None:
            self.motion.value = self.motion.target = first
        step = self.fraction_per_notch(first, last)
        target = min(max(self.motion.target + amount * step, 0.0), max(0.0, 1.0 - (last - first)))
        self.motion.to(target, duration=180)
        return "break"

    def scrollbar(self, *args):
        self.motion._cancel()
        self.viewport.yview(*args)
        self.motion.value = self.motion.target = self.viewport.yview()[0]


class _BottomFade:
    """A click-through Windows alpha overlay showing more content below."""

    def __init__(self, parent, viewport, color):
        self.viewport = viewport
        self.strips = []
        self.visible = False
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetLayeredWindowAttributes.argtypes = (wintypes.HWND, wintypes.DWORD,
                                                           wintypes.BYTE, wintypes.DWORD)
            user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
            for i in range(12):
                strip = tk.Frame(parent, background=color, borderwidth=0, highlightthickness=0)
                hwnd = strip.winfo_id()
                style = user32.GetWindowLongW(hwnd, -20)
                user32.SetWindowLongW(hwnd, -20, style | 0x00080000 | 0x00000020)
                alpha = int(215 * ((i + 1) / 12) ** 1.7)
                if not user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 0x2):
                    strip.destroy()
                    raise OSError("layered window setup failed")
                self.strips.append(strip)
        except (AttributeError, OSError, tk.TclError):
            for strip in self.strips:
                strip.destroy()
            self.strips.clear()

    def show(self, visible):
        visible = bool(visible and self.strips)
        if visible == self.visible:
            return
        self.visible = visible
        for i, strip in enumerate(self.strips):
            if visible:
                strip.place(in_=self.viewport, relx=0, rely=1, relwidth=1, y=-24 + i * 2, height=2)
                strip.lift()
            else:
                strip.place_forget()


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
                "Noto Sans CJK JP", "Yu Gothic UI", "Meiryo", "WenQuanYi Zen Hei", "Segoe UI"):
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
        s = dict(DEFAULTS)
        s.update(self._load_settings())
        self._language = normalize_language(s["language"])
        self.language = tk.StringVar(value=self._language)
        self.title(self.app_title)
        try:
            self.iconbitmap(_asset_path("assets", "VRM2PMX.ico"))
        except Exception:  # noqa: BLE001
            pass
        self.geometry("1080x760")
        self.minsize(960, 720)
        self.configure(background=BG)

        self.files: list[dict] = []           # {path, title, author, size, status, out}
        self.out_mode = tk.StringVar(value=s["out_mode"])
        self.out_dir = tk.StringVar(value=s["out_dir"])
        self.scale = tk.DoubleVar(value=s["scale"])
        self.pose = tk.DoubleVar(value=s["pose"])
        self.physics = tk.BooleanVar(value=s["physics"])
        self.preset = tk.StringVar(value=s["preset"] if s["preset"] in PRESET_HELP else "universal")
        self.softness = tk.DoubleVar(value=s["softness"])
        self.jp_names = tk.BooleanVar(value=s["jp_names"])
        self.adjustment_bones = tk.BooleanVar(value=s["adjustment_bones"])
        self.adjustment_x = tk.DoubleVar(value=s["adjustment_x"])
        self.adjustment_y = tk.DoubleVar(value=s["adjustment_y"])
        self.adjustment_z = tk.DoubleVar(value=s["adjustment_z"])
        self.theme = tk.StringVar(value=s["theme"] if s["theme"] in ("system", "light", "dark") else "system")
        self._mode = ""                       # palette in use: light / dark
        self._progress_val = 0.0
        self._log_lines: list[tuple[str, str]] = []
        self.status = tk.StringVar(value="")
        self._busy = False
        self._log_open = False
        self._q: queue.Queue = queue.Queue()

        self._fonts()
        for var in (self.out_mode, self.physics, self.preset, self.adjustment_bones):
            var.trace_add("write", lambda *_: self._sync())
        self.softness.trace_add("write", lambda *_: self._value_labels())
        self.pose.trace_add("write", lambda *_: self._value_labels())
        for var in (self.adjustment_x, self.adjustment_y, self.adjustment_z):
            var.trace_add("write", lambda *_: self._value_labels())
        self.theme.trace_add("write", lambda *_: self.apply_theme())
        self.language.trace_add("write", lambda *_: self._change_language())
        self.apply_theme()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(3000, self._watch_system_theme)

    @property
    def app_title(self):
        return f"{self.tr(APP_NAME)}  v{__version__}"

    def tr(self, source: str, **values) -> str:
        # A plain Python attribute is safe to read from the conversion worker;
        # never read a Tk variable outside the main thread.
        return translate(self._language, source, **values)

    def _change_language(self):
        language = normalize_language(self.language.get())
        if self._busy:
            if self.language.get() != self._language:
                self.language.set(self._language)
            return
        self._language = language
        self.title(self.app_title)
        self.apply_theme(force=True)
        self._save_settings()

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
        selection = self.tree.selection() if hasattr(self, "tree") else ()
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
        for iid in selection:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)
        self._show_path()
        if self._busy:
            self.go.configure_button(text=self.tr('正在转换…'), state="disabled")

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
        tk.Label(top, text=self.tr(APP_NAME), font=self.f_title, foreground=TEXT, background=BG).pack(side="left")
        tk.Label(top, text=f" v{__version__} ", font=self.f_small, foreground=ACCENT,
                 background=ACCENT_SOFT).pack(side="left", padx=(10, 0), pady=(4, 0))
        self.theme_segment = Segmented(top, self.theme,
                                       [("system", self.tr('跟随系统')), ("light", self.tr('浅色')), ("dark", self.tr('深色'))],
                                       font=self.f_small, bg=BG, height=30, width=228)
        self.theme_segment.pack(side="right")
        language_row = tk.Frame(self, background=BG, padx=28)
        language_row.pack(fill="x", pady=(0, 10))
        self._label(language_row, self.tr("语言"), "muted", bg=BG).pack(side="left", padx=(0, 12))
        self.language_segment = Segmented(language_row, self.language, list(LANGUAGES.items()),
                                          font=self.f_small, bg=BG, height=30, width=330)
        self.language_segment.pack(side="left")

        # bottom row (packed before the body so it never gets squeezed out)
        bottom = tk.Frame(self, background=BG, padx=28, pady=16)
        bottom.pack(side="bottom", fill="x")
        bar = tk.Frame(bottom, background=BG)
        bar.pack(fill="x")
        self.log_btn = RoundButton(bar, self.tr('日志'), self.toggle_log, kind="link", font=self.f_small, padx=0, pady=4,
                                   width=64)
        self.log_btn.pack(side="left", anchor="s")
        self.go = RoundButton(bar, self.tr('开始转换'), self.start, kind="primary", font=self.f_go, padx=34, pady=11,
                              radius=10)
        self.go.pack(side="right")
        mid = tk.Frame(bar, background=BG)
        mid.pack(side="left", fill="x", expand=True, padx=(18, 22))
        status_row = tk.Frame(mid, background=BG)
        status_row.pack(fill="x")
        tk.Label(status_row, text=self.tr('CoryDawn 的 Vibe Coding 作品'), font=self.f_small,
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
        heading = tk.Frame(head, background=CARD)
        self._label(heading, self.tr('模型'), "card").pack(side="left")
        self.count_lbl = self._label(heading, "", "muted")
        self.count_lbl.pack(side="left", padx=(10, 0), pady=(3, 0))
        actions = tk.Frame(head, background=CARD)
        self.btn_clear = RoundButton(actions, self.tr('清空'), self.clear_files, font=self.f_small, padx=12, pady=5, bg=CARD)
        self.btn_clear.pack(side="right")
        self.btn_remove = RoundButton(actions, self.tr('移除'), self.remove_selected, font=self.f_small, padx=12, pady=5, bg=CARD)
        self.btn_remove.pack(side="right", padx=(0, 6))
        RoundButton(actions, self.tr('添加文件夹'), self.add_folder, font=self.f_small, padx=12, pady=5,
                    bg=CARD).pack(side="right", padx=(0, 6))
        RoundButton(actions, self.tr('＋ 添加模型'), self.add_files, font=self.f_small, padx=12, pady=5,
                    bg=CARD).pack(side="right", padx=(0, 6))

        # Long translations need more space; move the actions to a second row
        # only when the title, count, and buttons cannot all fit.
        stacked = None

        def layout_header(_event=None):
            nonlocal stacked
            if not head.winfo_exists() or head.winfo_width() <= 1:
                return
            needs_two_rows = head.winfo_width() < heading.winfo_reqwidth() + actions.winfo_reqwidth() + 8
            if needs_two_rows == stacked:
                return
            stacked = needs_two_rows
            heading.pack_forget()
            actions.pack_forget()
            if stacked:
                heading.pack(side="top", fill="x")
                actions.pack(side="top", anchor="e", pady=(8, 0))
            else:
                heading.pack(side="left")
                actions.pack(side="right")

        head.bind("<Configure>", layout_header)
        heading.bind("<Configure>", layout_header)
        actions.bind("<Configure>", layout_header)
        self.count_lbl.bind("<Configure>", layout_header)

        holder = tk.Frame(card, background=CARD)
        holder.pack(side="top", fill="both", expand=True)
        self.tree = ttk.Treeview(holder, columns=("title", "author", "size", "status"), show="headings",
                                 selectmode="extended", style="Flat.Treeview", height=3)
        for col, text, width, anchor in (("title", self.tr('模型名'), 240, "w"), ("author", self.tr('作者'), 150, "w"),
                                         ("size", self.tr('大小'), 80, "e"), ("status", self.tr('状态'), 80, "center")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "title"))
        self.tree.tag_configure("ok", foreground=GOOD)
        self.tree.tag_configure("fail", foreground=BAD)
        self.tree.tag_configure("skip", foreground=MUTED)
        self.tree.tag_configure("run", foreground=ACCENT)
        self.tree.tag_configure("wait", foreground=TEXT)
        self.tree.pack(side="left", fill="both", expand=True)

        def fit_title_column(_event=None):
            # Keep the fixed columns visible even when the window is reduced
            # before Tk has had a chance to distribute the initial widths.
            width = self.tree.winfo_width()
            if width <= 1:
                return
            fixed = sum(int(self.tree.column(col, "width")) for col in ("author", "size", "status"))
            title_width = max(20, width - fixed - 2)
            if self.tree.column("title", "width") != title_width:
                self.tree.column("title", width=title_width)
            # Tk may have already stretched the column before this event;
            # recompute the visible text even when no width change is needed.
            for i, f in enumerate(self.files):
                if self.tree.exists(str(i)):
                    stem = os.path.splitext(os.path.basename(f["path"]))[0]
                    self.tree.set(str(i), "title", self._fit("  " + (f["title"] or stem), "title"))

        self.tree.bind("<Configure>", fit_title_column)
        self.model_scroll_motion = _SmoothScroll(self.tree, lambda _first, _last:
                                                3.0 / max(1, len(self.tree.get_children())))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.tree.bind(sequence, self.model_scroll_motion.wheel)
        self.model_scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.model_scroll_motion.scrollbar,
                                             style="Flat.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=_auto_scrollbar(self.model_scrollbar, self.tree,
                                                          side="right", fill="y"))
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_path())
        self.tree.bind("<ButtonRelease-1>", lambda e: self._refresh()
                       if self.tree.identify_region(e.x, e.y) == "separator" else None, add="+")

        self.empty = tk.Frame(holder, background=CARD)
        tk.Label(self.empty, text="＋", font=(self.f_title[0], 28), foreground=DISABLED,
                 background=CARD).pack()
        self._label(self.empty, self.tr('还没有模型'), "body").pack()
        self._label(self.empty, self.tr('拖放 .vrm 文件到这里，或点击按钮添加') if TkinterDnD is not None
                    else self.tr('点击按钮添加 .vrm 文件，或选择一个文件夹批量添加'), "muted").pack(pady=(4, 14))
        RoundButton(self.empty, self.tr('＋ 添加模型'), self.add_files, kind="primary", font=self.f_base, padx=18,
                    pady=7, bg=CARD).pack()
        self._register_drop_area(holder)

        self.path_lbl = self._label(card, "", "muted")
        self.path_lbl.pack(side="bottom", fill="x")
        tk.Frame(card, background=LINE, height=1).pack(side="bottom", fill="x", pady=(10, 8))
        self._build_log(card)

    def _build_settings(self, parent):
        container = self._card(parent, fill="both", expand=True)
        foot = tk.Frame(container, background=CARD)
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        tk.Frame(foot, background=LINE, height=1).pack(fill="x")
        self.reset_all_button = RoundButton(foot, self.tr('↺  恢复默认'), self.reset_settings, kind="link",
                                            font=self.f_small, padx=0, pady=5, bg=CARD)
        self.reset_all_button.pack(side="right")
        Tooltip(self.reset_all_button, self.tr('恢复所有设置的默认值（主题和语言除外）'))

        viewport_row = tk.Frame(container, background=CARD)
        viewport_row.pack(side="top", fill="both", expand=True)
        self.settings_scroll = tk.Canvas(viewport_row, background=CARD, highlightthickness=0, borderwidth=0)
        self.settings_scroll_motion = _SmoothScroll(self.settings_scroll, lambda first, last: 0.3 * (last - first))
        self.settings_scrollbar = ttk.Scrollbar(viewport_row, orient="vertical", command=self.settings_scroll_motion.scrollbar,
                                               style="Flat.Vertical.TScrollbar")
        self.settings_scroll.pack(side="left", fill="both", expand=True)
        auto_scroll = _auto_scrollbar(self.settings_scrollbar, self.settings_scroll,
                                      side="right", fill="y", padx=(10, 0))
        self.settings_fade = _BottomFade(viewport_row, self.settings_scroll, CARD)

        def update_scrollbar(first, last):
            auto_scroll(first, last)
            self.settings_fade.show(float(last) < 1.0)

        self.settings_scroll.configure(yscrollcommand=update_scrollbar)
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
        self._section(card, self.tr('输出'), first=True)
        Segmented(card, self.out_mode, [("same", self.tr('源文件目录')), ("custom", self.tr('指定目录'))],
                  font=self.f_small, bg=CARD).pack(fill="x")
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x", pady=(8, 0))
        self.out_entry = RoundEntry(row, self.out_dir, font=self.f_small, bg=CARD)
        self.out_entry.pack(side="left", fill="x", expand=True)
        self.out_btn = RoundButton(row, self.tr('浏览'), self.pick_out, font=self.f_small, padx=12, pady=5, bg=CARD)
        self.out_btn.pack(side="left", padx=(8, 0))

        # 模型
        self._section(card, self.tr('模型'))
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x")
        self._label(row, self.tr('缩放倍数')).pack(side="left")
        self.scale_reset = RoundButton(row, "↺", lambda: self.scale.set(DEFAULTS["scale"]), kind="link",
                                       font=("Segoe UI Symbol", 13), padx=4, pady=0, width=28, bg=CARD)
        self.scale_reset.pack(side="left", padx=(4, 0))
        Tooltip(self.scale_reset, self.tr('恢复缩放倍数默认值（14）'))
        st = Stepper(row, self.scale, 4, 30, 0.5, font=self.f_base, bg=CARD)
        st.pack(side="right")
        Tooltip(st, self.tr('1 米 = 14 个 MMD 单位（默认 14），与 MMD 标准模型的身高一致，通常保持默认。'))
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x", pady=(12, 0))
        self._label(row, self.tr('手臂下摆')).pack(side="left")
        self.pose_reset = RoundButton(row, "↺", lambda: self.pose.set(DEFAULTS["pose"]), kind="link",
                                      font=("Segoe UI Symbol", 13), padx=4, pady=0, width=28, bg=CARD)
        self.pose_reset.pack(side="left", padx=(4, 0))
        Tooltip(self.pose_reset, self.tr('恢复手臂下摆默认值（30°）'))
        self.pose_val = self._label(row, "", "value")
        self.pose_val.pack(side="right")
        self.pose_slider = Slider(card, self.pose, 0, 60, 5, bg=CARD)
        self.pose_slider.pack(fill="x", pady=(4, 0))
        Tooltip(self.pose_slider, self.tr('VRM 是 T-pose（手臂平举），MMD 动作假设手臂下垂。默认 30°；0° 保持原姿势。'))

        tg_adjust = Toggle(card, self.adjustment_bones, self.tr('生成动作调整骨'), font=self.f_base, bg=CARD)
        tg_adjust.pack(anchor="w", pady=(12, 0))
        Tooltip(tg_adjust, self.tr('生成调整骨，方便在 MMD 中手动修正动作和穿模；关闭足 IK 时也可调整腿部旋转。'))

        adjustment_heading = tk.Frame(card, background=CARD)
        adjustment_heading.pack(fill="x", pady=(10, 2))
        self._label(adjustment_heading, self.tr('调整骨生成位置'), "muted").pack(side="left")
        self.adjustment_reset = RoundButton(adjustment_heading, "↺", self.reset_adjustment_position,
                                            kind="link", font=("Segoe UI Symbol", 13), padx=4, pady=0,
                                            width=28, bg=CARD)
        self.adjustment_reset.pack(side="left", padx=(4, 0))
        Tooltip(self.adjustment_reset, self.tr('恢复调整骨位置默认值（X −20，Y 0，Z 0）'))
        self.adjustment_sliders, self.adjustment_values, self.adjustment_titles = [], [], []
        for axis, var in (("X", self.adjustment_x), ("Y", self.adjustment_y), ("Z", self.adjustment_z)):
            row = tk.Frame(card, background=CARD)
            row.pack(fill="x", pady=(2, 0))
            label = self._label(row, axis)
            label.pack(side="left", padx=(0, 8))
            value = self._label(row, "", "value", width=5)
            value.configure(anchor="e")
            value.pack(side="right", padx=(6, 0))
            slider = Slider(row, var, -100, 100, 1, bg=CARD)
            slider.pack(side="left", fill="x", expand=True)
            self.adjustment_titles.append(label)
            self.adjustment_values.append(value)
            self.adjustment_sliders.append(slider)
            Tooltip(slider, self.tr('调整骨整体沿 {p0} 轴偏移（PMX 单位），范围 -100 至 100。', p0=axis))

        # 物理
        self._section(card, self.tr('物理'))
        Toggle(card, self.physics, self.tr('生成物理（刚体 + 关节）'), font=self.f_base, bg=CARD).pack(anchor="w")
        self.seg_preset = Segmented(card, self.preset, [("universal", self.tr('通用（推荐）')), ("final", self.tr('正式（旧版）'))],
                                    font=self.f_small, bg=CARD)
        self.seg_preset.pack(fill="x", pady=(10, 0))
        self.preset_help = self._label(card, "", "muted", wraplength=280)
        self.preset_help.pack(fill="x", pady=(6, 0))
        row = tk.Frame(card, background=CARD)
        row.pack(fill="x", pady=(10, 0))
        self.soft_title = self._label(row, self.tr('柔软度'))
        self.soft_title.pack(side="left")
        self.soft_reset = RoundButton(row, "↺", lambda: self.softness.set(DEFAULTS["softness"]), kind="link",
                                      font=("Segoe UI Symbol", 13), padx=4, pady=0, width=28, bg=CARD)
        self.soft_reset.pack(side="left", padx=(4, 0))
        Tooltip(self.soft_reset, self.tr('恢复柔软度默认值（0.75）'))
        self.soft_val = self._label(row, "", "value")
        self.soft_val.pack(side="right")
        self.soft_slider = Slider(card, self.softness, 0.5, 1.0, 0.05, bg=CARD)
        self.soft_slider.pack(fill="x", pady=(4, 0))
        ends = tk.Frame(card, background=CARD)
        ends.pack(fill="x")
        self._label(ends, self.tr('更硬'), "muted").pack(side="left")
        self._label(ends, self.tr('更软'), "muted").pack(side="right")
        Tooltip(self.soft_slider, self.tr('默认 0.75。调小更硬、回落更快；调大更柔软、摆得更开。只对通用物理有效。'))

        # 名称
        self._section(card, self.tr('名称'))
        tg = Toggle(card, self.jp_names, self.tr('材质 / 表情名转日文字形'), font=self.f_base, bg=CARD)
        tg.pack(anchor="w")
        Tooltip(tg, self.tr('防止 PMX Editor / MMD 显示乱码。会改写名称：旧 VMD 里指向中文表情名的轨道可能失效。'))

        def bind_wheel(widget):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self.settings_scroll_motion.wheel, add="+")
            for child in widget.winfo_children():
                bind_wheel(child)

        bind_wheel(viewport_row)

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
        self.language_segment.set_enabled(not self._busy)
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
        self.preset_help.configure(text=self.tr(PRESET_HELP[self.preset.get()]) if phys else self.tr('不生成刚体和关节，头发等不会摆动。'))
        adjust_on = self.adjustment_bones.get() and not self._busy
        self.adjustment_reset.configure_button(state="normal" if adjust_on else "disabled")
        for slider, title, value in zip(self.adjustment_sliders, self.adjustment_titles, self.adjustment_values):
            slider.set_enabled(adjust_on)
            title.configure(foreground=TEXT if adjust_on else DISABLED)
            value.configure(foreground=ACCENT if adjust_on else DISABLED)

    def _value_labels(self):
        try:
            self.soft_val.configure(text=f"{float(self.softness.get()):.2f}")
            v = float(self.pose.get())
            self.pose_val.configure(text=f"{v:.0f}°" + ("  T-pose" if v == 0 else ""))
            for label, var in zip(self.adjustment_values, (self.adjustment_x, self.adjustment_y, self.adjustment_z)):
                label.configure(text=f"{float(var.get()):.0f}")
        except (tk.TclError, ValueError):
            pass

    def toggle_log(self, show: bool | None = None):
        self._log_open = (not self._log_open) if show is None else show
        if self._log_open:
            self.log_frame.pack(side="bottom", fill="x", pady=(10, 0))
            self.log_btn.configure_button(text=self.tr('收起日志'))
        else:
            self.log_frame.pack_forget()
            self.log_btn.configure_button(text=self.tr('日志'))

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
            "adjustment_bones": bool(self.adjustment_bones.get()),
            "adjustment_x": num(self.adjustment_x, DEFAULTS["adjustment_x"]),
            "adjustment_y": num(self.adjustment_y, DEFAULTS["adjustment_y"]),
            "adjustment_z": num(self.adjustment_z, DEFAULTS["adjustment_z"]),
            "theme": self.theme.get(), "language": self._language,
        }

    @staticmethod
    def _load_settings() -> dict:
        try:
            with open(_settings_path(), encoding="utf-8") as f:
                data = json.load(f)
            settings = {k: v for k, v in data.items() if k in DEFAULTS and isinstance(v, type(DEFAULTS[k]))}
            for axis in "xyz":
                key = "adjustment_" + axis
                value = data.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    settings[key] = max(-100.0, min(100.0, float(value)))
                else:
                    settings.pop(key, None)
            return settings
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
                         ("softness", self.softness), ("jp_names", self.jp_names),
                         ("adjustment_bones", self.adjustment_bones),
                         ("adjustment_x", self.adjustment_x), ("adjustment_y", self.adjustment_y),
                         ("adjustment_z", self.adjustment_z)):
            var.set(DEFAULTS[key])

    def reset_adjustment_position(self):
        for axis in "xyz":
            getattr(self, "adjustment_" + axis).set(DEFAULTS["adjustment_" + axis])

    def _on_close(self):
        if self._busy and not messagebox.askyesno(self.app_title, self.tr('正在转换，确定要退出吗？')):
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
                title=self.tr('选择 VRM 模型'), filetypes=[(self.tr('VRM 模型'), "*.vrm"), (self.tr('所有文件'), "*.*")]))

    def add_folder(self):
        if self._busy:
            return
        d = filedialog.askdirectory(title=self.tr('选择包含 VRM 模型的文件夹'))
        if not d:
            return
        found = []
        for root, _dirs, names in os.walk(d):
            found += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(".vrm")]
        if not found:
            messagebox.showinfo(self.app_title, self.tr('这个文件夹里没有找到 .vrm 文件。'))
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
        d = filedialog.askdirectory(title=self.tr('选择输出目录'))
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
                                     f"{dot} {self.tr(STATUS_TEXT[f['status']])}"))
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_add(i)
        n = len(self.files)
        if n:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
        done = sum(f["status"] == "ok" for f in self.files)
        self.count_lbl.configure(text=(self.tr('{p0} 个', p0=n) + (self.tr(' · 已完成 {p0}', p0=done) if done else "")) if n else "")
        state = "disabled" if self._busy or not n else "normal"
        self.btn_remove.configure_button(state=state)
        self.btn_clear.configure_button(state=state)
        if not self._busy:
            self.go.configure_button(text=self.tr('开始转换  {p0}', p0=n) if n > 1 else self.tr('开始转换'),
                                     state="normal" if n else "disabled")
            self.status.set(self.tr('{p0} 个模型，准备就绪', p0=n) if n else self.tr('添加模型后即可开始'))
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
            self.path_lbl.configure(text=who + ((self.tr('输出  ') + f["out"]) if f["out"] else f["path"]))
        elif any(f["status"] == "ok" for f in self.files):
            self.path_lbl.configure(text=self.tr('双击已完成的模型，打开输出文件夹'))
        else:
            self.path_lbl.configure(text=self.tr('选中模型可查看路径 · Delete 键移除'))

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
            messagebox.showinfo(self.app_title, self.tr('请选择输出目录，或改为「源文件目录」。'))
            return
        try:
            float(self.scale.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning(self.app_title, self.tr('缩放倍数需要是数字。'))
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
                    self.status.set(self.tr('已取消'))
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
            self.status.set(self.tr('没有需要转换的模型，已跳过 {p0} 个', p0=len(skipped)))
            return
        self._busy = True
        self._sync()
        self.go.configure_button(text=self.tr('正在转换…'), state="disabled")
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
        body = self.tr('覆盖会替换其中的同名文件；另存为会新建一个文件夹，原文件保留。')
        if allow_skip:
            body += self.tr('\n跳过会保留已有输出，并跳过本次其余已存在的输出目录。')
        dlg, buttons = self._dialog(self.tr('输出目录已存在'), self.tr('「{p0}」已经转换过', p0=base),
                                    body, target_dir)

        def choose(value):
            result["choice"] = value
            dlg.destroy()

        RoundButton(buttons, self.tr('另存为「{p0}」', p0=next_name), lambda: choose("saveas"), kind="primary",
                    font=self.f_base, bg=CARD).pack(side="right")
        RoundButton(buttons, self.tr('覆盖'), lambda: choose("overwrite"), font=self.f_base,
                    bg=CARD).pack(side="right", padx=(0, 8))
        RoundButton(buttons, self.tr('取消'), lambda: choose(None), kind="link", font=self.f_base, padx=4,
                    bg=CARD).pack(side="left")
        if allow_skip:
            RoundButton(buttons, self.tr('跳过已转换模型'), lambda: choose("skip"), font=self.f_base,
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
            self._q.put(("status", self.tr('正在转换 {p0} / {p1}  ·  {p2}', p0=n + 1, p1=total, p2=name)))
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
                    adjustment_bones=bool(opts.get("adjustment_bones", False)),
                    adjustment_offset=tuple(float(opts.get("adjustment_" + axis, DEFAULTS["adjustment_" + axis]))
                                            for axis in ("x", "y", "z")),
                    model_name=model_name,
                    out_dir=out_dir,
                )
                rep = Converter(opt).convert(path)
                if not rep.ok:
                    self._q.put(("log", (self.tr('失败：') + ("；" if self._language == "zh" else "; ").join(translate_diagnostic(self._language, e) for e in rep.errors), "bad")))
                    self._q.put(("row", (idx, "fail", "")))
                    self._q.put(("progress", (n + 1) / total * 100))
                    continue
                for w in rep.warnings:
                    self._q.put(("log", (self.tr('提示：') + translate_diagnostic(self._language, w), "")))
                out_pmx = rep.out_pmx
                self._q.put(("log", (self.tr('写出：{p0}', p0=rep.out_pmx), "")))
                self._q.put(("log", (self.tr('顶点 {p0}  三角面 {p1}  骨骼 {p2}  表情 {p3}  材质 {p4}  贴图 {p5}', p0=rep.vertices, p1=rep.triangles, p2=rep.bones, p3=rep.morphs, p4=rep.materials, p5=rep.textures), "")))
                if rep.rigid_bodies:
                    self._q.put(("log", (self.tr('物理：刚体 {p0}  关节 {p1}', p0=rep.rigid_bodies, p1=rep.joints), "")))
                problems = validate(rep.out_pmx)
                if problems:
                    for p in problems:
                        self._q.put(("log", (self.tr('自检问题：') + translate_diagnostic(self._language, p), "bad")))
                    self._q.put(("row", (idx, "fail", out_pmx)))
                else:
                    self._q.put(("log", (self.tr('自检通过：文件结构完整'), "good")))
                    self._q.put(("row", (idx, "ok", out_pmx)))
                    ok_count += 1
                    last_dir = os.path.dirname(os.path.abspath(rep.out_pmx))
            except Exception:  # noqa: BLE001
                self._q.put(("log", (self.tr('异常：\n') + traceback.format_exc(), "bad")))
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
                    self.status.set(self.tr('完成  {p0} / {p1}', p0=ok, p1=total)
                                    + (self.tr('  ·  已跳过 {p0}', p0=skipped_count) if skipped_count else "")
                                    + ("" if ok == total else self.tr('  ·  失败的模型请看日志')))
                    if ok:
                        self._show_done_dialog(ok, total, last_dir, skipped_count)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_done_dialog(self, ok: int, total: int, last_dir: str, skipped_count: int = 0):
        body = self.tr('{p0} / {p1} 个模型转换成功。', p0=ok, p1=total)
        if skipped_count:
            body += self.tr('\n已跳过 {p0} 个已有输出。', p0=skipped_count)
        dlg, buttons = self._dialog(self.tr(APP_NAME), self.tr('转换完成'), body,
                                    self.tr('输出目录：{p0}', p0=last_dir) if last_dir else "")
        RoundButton(buttons, self.tr('打开输出文件夹'), lambda: (self._open_and_select(last_dir), dlg.destroy()),
                    kind="primary", font=self.f_base, bg=CARD).pack(side="right")
        RoundButton(buttons, self.tr('关闭'), dlg.destroy, font=self.f_base, bg=CARD).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", lambda _e: dlg.destroy())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        self._center(dlg)
        dlg.grab_set()
        dlg.focus_set()

    def _open_and_select(self, path: str):
        if not path or not os.path.exists(path):
            messagebox.showinfo(self.app_title, self.tr('目标文件或文件夹不存在。'))
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        except OSError as e:
            messagebox.showwarning(self.app_title, self.tr('无法打开：\n{p0}\n\n{p1}', p0=path, p1=e))

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
