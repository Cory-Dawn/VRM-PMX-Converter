# -*- coding: utf-8 -*-
"""Flat, modern-looking widgets drawn on tk.Canvas (pure tkinter, no extra
runtime): rounded button, toggle switch, segmented control, slider, number
stepper, thin progress line.

Colours come from a light and a dark palette; set_palette() switches the
module-level colour names, and widgets read them every time they redraw.
"""
from __future__ import annotations

import math
import sys
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

LIGHT = {
    "ACCENT": "#3b6cf6", "ACCENT_HOVER": "#2f5be0", "ACCENT_SOFT": "#e9effe",
    "BG": "#f5f6f8", "CARD": "#ffffff", "LINE": "#e6e8ec", "TRACK": "#dfe3e9",
    "TEXT": "#1d2129", "MUTED": "#86909c", "DISABLED": "#c9cdd4",
    "GOOD": "#00a870", "BAD": "#e34d59",
    "GHOST": "#f2f3f5", "GHOST_HOVER": "#e8eaee", "SEG_BG": "#f0f2f5", "SEG_SEL": "#ffffff",
    "KNOB": "#ffffff", "PRIMARY_OFF": "#a9bdf9", "PRIMARY_OFF_FG": "#ffffff",
    "TOGGLE_OFF": "#dfe3e9", "TOGGLE_ON_OFF": "#c5d3fb", "TOGGLE_OFF_OFF": "#eceef1",
    "FIELD_OFF": "#f7f8fa", "SCROLL": "#e1e4e9", "SCROLL_HOVER": "#c9cdd4",
    "TIP_BG": "#ffffff", "TIP_FG": "#303846", "TIP_BORDER": "#dce2eb", "TIP_KEY": "#f7f8fa",
}
DARK = {
    "ACCENT": "#5b8cff", "ACCENT_HOVER": "#7aa2ff", "ACCENT_SOFT": "#253250",
    "BG": "#16171b", "CARD": "#1f2126", "LINE": "#2d3037", "TRACK": "#3a3e47",
    "TEXT": "#e7e9ec", "MUTED": "#8b919c", "DISABLED": "#4d525b",
    "GOOD": "#2fc48c", "BAD": "#f26671",
    "GHOST": "#2a2d34", "GHOST_HOVER": "#343841", "SEG_BG": "#2a2d34", "SEG_SEL": "#3b3f49",
    "KNOB": "#f5f6f8", "PRIMARY_OFF": "#2f3a55", "PRIMARY_OFF_FG": "#7d8698",
    "TOGGLE_OFF": "#40444d", "TOGGLE_ON_OFF": "#2f3a55", "TOGGLE_OFF_OFF": "#2a2d34",
    "FIELD_OFF": "#25272c", "SCROLL": "#373a42", "SCROLL_HOVER": "#4b4f58",
    "TIP_BG": "#30343c", "TIP_FG": "#eef0f4", "TIP_BORDER": "#4a505c", "TIP_KEY": "#282c34",
}
PALETTES = {"light": LIGHT, "dark": DARK}

# current colours (module globals, updated by set_palette)
globals().update(LIGHT)


def set_palette(mode: str) -> dict:
    """Switch the module colours to 'light' or 'dark'; returns the palette."""
    pal = PALETTES.get(mode, LIGHT)
    globals().update(pal)
    return pal


def system_prefers_dark() -> bool:
    """Windows 10/11 app theme (AppsUseLightTheme = 0 means dark); light elsewhere."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def _colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def set_dark_titlebar(window, dark: bool, caption: str | None = None, text: str | None = None):
    """Theme the native title bar on Windows; no-op elsewhere.

    dark: DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10 1809+ / 11).
    caption / text: exact title bar background and title colours
    (DWMWA_CAPTION_COLOR / DWMWA_TEXT_COLOR, Windows 11 only - ignored on 10),
    so the bar blends into the window background.
    Windows 11 still fades the bar between the old and new colour for a moment
    when the theme changes; that animation belongs to the system."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        dwm = ctypes.windll.dwmapi

        def put(attr, val):
            v = ctypes.c_int(val)
            return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0

        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new / pre-20H1)
            if put(attr, 1 if dark else 0):
                break
        if caption:
            put(35, _colorref(caption))   # DWMWA_CAPTION_COLOR
        if text:
            put(36, _colorref(text))      # DWMWA_TEXT_COLOR
        # repaint the frame now instead of on the next activation
        # (SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:  # noqa: BLE001
        pass


def _round_rect(cv, x0, y0, x1, y1, r, fill, **_kw):
    """Filled rounded rectangle from two rectangles and four circles (crisp corners)."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    kw = dict(fill=fill, outline=fill, width=0)
    if r < 1:
        cv.create_rectangle(x0, y0, x1, y1, **kw)
        return
    cv.create_rectangle(x0 + r, y0, x1 - r, y1, **kw)
    cv.create_rectangle(x0, y0 + r, x1, y1 - r, **kw)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)


def _smooth_shape(cv, x0, y0, x1, y1, draw_shape):
    """Draw small controls at higher resolution, then downsample their edges."""
    if x1 <= x0 or y1 <= y0:
        return
    scale = 4
    left, top = math.floor(x0), math.floor(y0)
    right, bottom = math.ceil(x1), math.ceil(y1)
    width, height = right - left, bottom - top
    image = Image.new("RGBA", (width * scale, height * scale))
    bounds = tuple(round(v * scale) for v in
                   (x0 - left, y0 - top, x1 - left, y1 - top))
    draw_shape(ImageDraw.Draw(image), bounds, scale)
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(image, master=cv)
    item = cv.create_image(left, top, image=photo, anchor="nw")
    # Tk does not keep Python image objects alive. Drop images for deleted items.
    images = getattr(cv, "_smooth_images", {})
    existing = set(cv.find_all())
    cv._smooth_images = {key: value for key, value in images.items() if key in existing}
    cv._smooth_images[item] = photo


def _smooth_rect(cv, x0, y0, x1, y1, radius, fill):
    radius = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    _smooth_shape(cv, x0, y0, x1, y1,
                  lambda draw, bounds, scale: draw.rounded_rectangle(
                      bounds, radius=round(radius * scale), fill=fill))


def _smooth_oval(cv, x0, y0, x1, y1, fill, outline=None, width=1):
    _smooth_shape(cv, x0, y0, x1, y1,
                  lambda draw, bounds, scale: draw.ellipse(
                      bounds, fill=fill, outline=outline, width=width * scale))


def _mix_color(start, end, fraction):
    rgb = [round(int(start[i:i + 2], 16) * (1 - fraction) + int(end[i:i + 2], 16) * fraction)
           for i in (1, 3, 5)]
    return "#{:02x}{:02x}{:02x}".format(*rgb)


class _Animation:
    """One interruptible, ease-out transition owned by a Tk widget."""

    def __init__(self, widget, value, redraw):
        self.widget, self.value, self.target, self.redraw = widget, float(value), float(value), redraw
        self.job = None
        widget.bind("<Destroy>", self._destroyed, add="+")

    def _destroyed(self, event):
        if event.widget is self.widget:
            self._cancel()

    def _cancel(self):
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def jump(self, value):
        self._cancel()
        self.value = self.target = float(value)
        self.redraw()

    def to(self, target, duration=180):
        target = float(target)
        if target == self.target and self.job is not None:
            return
        self._cancel()
        self.target = target
        if abs(self.value - target) < 1e-6 or duration <= 0:
            self.jump(target)
            return
        self.start, self.started, self.duration = self.value, time.monotonic(), duration / 1000
        self._tick()

    def _tick(self):
        self.job = None
        elapsed = min((time.monotonic() - self.started) / self.duration, 1.0)
        eased = 1 - (1 - elapsed) ** 3
        self.value = self.start + (self.target - self.start) * eased
        self.redraw()
        if elapsed < 1 and self.widget.winfo_exists():
            self.job = self.widget.after(16, self._tick)


def _round_outline(cv, x0, y0, x1, y1, r, color, width=1, tags=()):
    """1-px rounded border drawn as one stroke (4 lines + 4 arcs), so the
    corners join the edges without gaps."""
    r = max(1.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    kw = dict(fill=color, width=width, tags=tags)
    cv.create_line(x0 + r, y0, x1 - r, y0, **kw)
    cv.create_line(x0 + r, y1, x1 - r, y1, **kw)
    cv.create_line(x0, y0 + r, x0, y1 - r, **kw)
    cv.create_line(x1, y0 + r, x1, y1 - r, **kw)
    akw = dict(style="arc", outline=color, width=width, extent=90, tags=tags)
    cv.create_arc(x0, y0, x0 + 2 * r, y0 + 2 * r, start=90, **akw)
    cv.create_arc(x1 - 2 * r, y0, x1, y0 + 2 * r, start=0, **akw)
    cv.create_arc(x0, y1 - 2 * r, x0 + 2 * r, y1, start=180, **akw)
    cv.create_arc(x1 - 2 * r, y1 - 2 * r, x1, y1, start=270, **akw)


class RoundButton(tk.Canvas):
    """Rounded button. kind: primary (filled accent), ghost (light), link (text only)."""

    def __init__(self, master, text, command=None, kind="ghost", font=None, padx=16, pady=7,
                 radius=8, bg=None, width=None):
        self._bg = bg or master.cget("background")
        super().__init__(master, highlightthickness=0, borderwidth=0, background=self._bg, cursor="hand2")
        self.command, self.kind, self.font = command, kind, font
        self.padx, self.pady, self.radius = padx, pady, radius
        self._text, self._state, self._hover, self._fixed_w = text, "normal", False, width
        self._hover_motion = _Animation(self, 0, self._draw)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    def _colors(self):
        if self.kind == "primary":
            if self._state == "disabled":
                return PRIMARY_OFF, PRIMARY_OFF_FG
            return _mix_color(ACCENT, ACCENT_HOVER, self._hover_motion.value), "#ffffff"
        if self.kind == "link":
            return self._bg, (DISABLED if self._state == "disabled" else
                              _mix_color(ACCENT, ACCENT_HOVER, self._hover_motion.value))
        if self._state == "disabled":
            return GHOST, DISABLED
        return _mix_color(GHOST, GHOST_HOVER, self._hover_motion.value), TEXT

    def _draw(self):
        self.delete("all")
        fill, fg = self._colors()
        tid = self.create_text(0, 0, text=self._text, font=self.font, fill=fg, anchor="nw")
        x0, y0, x1, y1 = self.bbox(tid)
        tw, th = x1 - x0, y1 - y0
        w = self._fixed_w or tw + 2 * self.padx
        h = th + 2 * self.pady
        self.configure(width=w, height=h)
        if self.kind != "link":
            _smooth_rect(self, 0, 0, w, h, self.radius, fill=fill)
        self.coords(tid, (w - tw) / 2, (h - th) / 2)
        self.tag_raise(tid)

    def _set_hover(self, on):
        self._hover = on and self._state == "normal"
        self._hover_motion.to(1 if self._hover else 0, duration=130)

    def _click(self, e):
        if self._state == "normal" and self.command and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()

    def configure_button(self, text=None, state=None):
        if text is not None:
            self._text = text
        if state is not None:
            self._state = state
            self.configure(cursor="hand2" if state == "normal" else "arrow")
            if state != "normal":
                self._hover = False
                self._hover_motion.jump(0)
        self._draw()


class Toggle(tk.Canvas):
    """Switch bound to a BooleanVar, with a text label on the right."""

    def __init__(self, master, variable: tk.BooleanVar, text="", font=None, bg=None):
        self._bg = bg or master.cget("background")
        super().__init__(master, highlightthickness=0, borderwidth=0, background=self._bg, cursor="hand2")
        self.var, self.text, self.font, self._enabled = variable, text, font, True
        self._motion = _Animation(self, int(bool(variable.get())), self._draw)
        self.bind("<Button-1>", self._toggle)
        self._trace = variable.trace_add("write", lambda *_: self._motion.to(int(bool(self.var.get()))))
        self.bind("<Destroy>", lambda _e: self._untrace(), add="+")
        self._draw()

    def _untrace(self):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = 36, 20
        off, on = (TOGGLE_OFF, ACCENT) if self._enabled else (TOGGLE_OFF_OFF, TOGGLE_ON_OFF)
        col = _mix_color(off, on, self._motion.value)
        _smooth_rect(self, 0, 2, w, 2 + h, h / 2, fill=col)
        x = h / 2 + (w - h) * self._motion.value
        _smooth_oval(self, x - 8, 2 + h / 2 - 8, x + 8, 2 + h / 2 + 8, fill=KNOB)
        tid = self.create_text(w + 10, 2 + h / 2, text=self.text, font=self.font, anchor="w",
                               fill=TEXT if self._enabled else DISABLED)
        x1 = self.bbox(tid)[2]
        self.configure(width=x1 + 2, height=h + 4)

    def _toggle(self, _e=None):
        if self._enabled:
            self.var.set(not bool(self.var.get()))

    def set_enabled(self, on: bool):
        self._enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()


class Segmented(tk.Canvas):
    """Segmented control bound to a StringVar: options = [(value, label), ...]."""

    def __init__(self, master, variable: tk.StringVar, options, font=None, bg=None, height=32, width=None):
        self._bg = bg or master.cget("background")
        super().__init__(master, highlightthickness=0, borderwidth=0, background=self._bg, height=height,
                         cursor="hand2")
        if width:
            self.configure(width=width)
        self.var, self.options, self.font, self.h, self._enabled = variable, options, font, height, True
        self._motion = _Animation(self, self._selected_index(), self._draw)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._click)
        self._trace = variable.trace_add("write", lambda *_: self._motion.to(self._selected_index()))
        self.bind("<Destroy>", lambda _e: self._untrace(), add="+")

    def _selected_index(self):
        return next((i for i, (value, _) in enumerate(self.options) if value == self.var.get()), 0)

    def _untrace(self):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 1 else self.winfo_pixels(self.cget("width") or 60)
        h = self.h
        _smooth_rect(self, 0, 0, w, h, 8, fill=SEG_BG)
        seg = w / len(self.options)
        pos = self._motion.value
        _smooth_rect(self, pos * seg + 3, 3, (pos + 1) * seg - 3, h - 3, 6,
                     fill=SEG_SEL if self._enabled else SEG_BG)
        for i, (value, label) in enumerate(self.options):
            sel = self.var.get() == value
            fg = (ACCENT if sel else MUTED) if self._enabled else DISABLED
            self.create_text(i * seg + seg / 2, h / 2, text=label, font=self.font, fill=fg)

    def _click(self, e):
        if not self._enabled:
            return
        i = int(e.x / (max(self.winfo_width(), 1) / len(self.options)))
        self.var.set(self.options[max(0, min(i, len(self.options) - 1))][0])

    def set_enabled(self, on: bool):
        self._enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()


class Slider(tk.Canvas):
    """Thin slider bound to a DoubleVar, snapping to `step`."""

    def __init__(self, master, variable: tk.DoubleVar, from_, to, step, bg=None, height=22):
        self._bg = bg or master.cget("background")
        super().__init__(master, highlightthickness=0, borderwidth=0, background=self._bg, height=height,
                         cursor="hand2")
        self.var, self.lo, self.hi, self.step, self.h, self._enabled = variable, from_, to, step, height, True
        self._motion = _Animation(self, self._value(), self._draw)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self._trace = variable.trace_add("write", lambda *_: self._motion.to(self._value(), duration=100))
        self.bind("<Destroy>", lambda _e: self._untrace(), add="+")

    def _untrace(self):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _value(self):
        try:
            return min(max(float(self.var.get()), self.lo), self.hi)
        except (tk.TclError, ValueError):
            return self.lo

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h, r = max(self.winfo_width(), 40), self.h, 8
        y = h / 2
        x = r + (w - 2 * r) * (self._motion.value - self.lo) / (self.hi - self.lo)
        on = ACCENT if self._enabled else DISABLED
        _smooth_rect(self, r, y - 2, w - r, y + 2, 2, fill=TRACK)
        _smooth_rect(self, r, y - 2, x, y + 2, 2, fill=on)
        _smooth_oval(self, x - r, y - r, x + r, y + r, fill=KNOB, outline=on, width=2)

    def _drag(self, e):
        if not self._enabled:
            return
        w, r = max(self.winfo_width(), 40), 8
        t = min(max((e.x - r) / (w - 2 * r), 0.0), 1.0)
        v = self.lo + t * (self.hi - self.lo)
        self.var.set(round(round(v / self.step) * self.step, 4))

    def set_enabled(self, on: bool):
        self._enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()


class RoundedBox(tk.Canvas):
    """Rounded panel: a Canvas draws the rounded fill and 1 px border, the
    content goes into `.inner` (a Frame placed inside the rounded area).

    fill_height=True: the box takes its height from its own packing (cards);
    otherwise it is as tall as the content (fields)."""

    def __init__(self, master, radius=12, fill=None, border=None, pad=(16, 14), fill_height=False, bg=None):
        # small initial size: packing / the content decide the real one
        super().__init__(master, highlightthickness=0, borderwidth=0, width=40, height=20,
                         background=bg or master.cget("background"))
        self.radius, self.pad, self.fill_height = radius, pad, fill_height
        self._fill, self._border = fill, border
        self.inner = tk.Frame(self, background=self.fill)
        self._win = self.create_window(pad[0], pad[1], window=self.inner, anchor="nw")
        self.bind("<Configure>", lambda _e: self._layout())
        if not fill_height:
            self.inner.bind("<Configure>", lambda _e: self._fit_height())

    @property
    def fill(self):
        return self._fill or CARD

    @property
    def border(self):
        return self._border or LINE

    def set_colors(self, fill=None, border=None):
        if fill is not None:
            self._fill = fill
            self.inner.configure(background=fill)
        if border is not None:
            self._border = border
        self._draw()

    def _fit_height(self):
        h = self.inner.winfo_reqheight() + 2 * self.pad[1]
        if int(self.cget("height")) != h:
            self.configure(height=h)

    def _layout(self):
        w, h = self.winfo_width(), self.winfo_height()
        kw = {"width": max(w - 2 * self.pad[0], 1)}
        if self.fill_height:
            kw["height"] = max(h - 2 * self.pad[1], 1)
        self.itemconfigure(self._win, **kw)
        self._draw()

    def _draw(self):
        self.delete("bg")
        w, h, r = self.winfo_width(), self.winfo_height(), self.radius
        if w < 4 or h < 4:
            return
        _round_rect(self, 0, 0, w - 1, h - 1, r, fill=self.fill)
        for item in self.find_all():
            if item != self._win:
                self.addtag_withtag("bg", item)
        _round_outline(self, 0, 0, w - 1, h - 1, r, self.border, tags=("bg",))
        self.tag_lower("bg")


class RoundEntry(RoundedBox):
    """Single-line text field with rounded corners (accent border when focused)."""

    def __init__(self, master, variable, font=None, bg=None):
        super().__init__(master, radius=8, pad=(10, 6), bg=bg)
        self._enabled = True
        self.entry = tk.Entry(self.inner, textvariable=variable, font=font, relief="flat", borderwidth=0,
                              highlightthickness=0, background=CARD, foreground=TEXT, insertbackground=TEXT,
                              disabledbackground=FIELD_OFF, disabledforeground=DISABLED,
                              selectbackground=ACCENT_SOFT, selectforeground=TEXT)
        self.entry.pack(fill="x")
        self.entry.bind("<FocusIn>", lambda _e: self.set_colors(border=ACCENT))
        self.entry.bind("<FocusOut>", lambda _e: self.set_colors(border=LINE))

    def set_enabled(self, on: bool):
        self._enabled = on
        self.entry.configure(state="normal" if on else "disabled")
        self.set_colors(fill=CARD if on else FIELD_OFF, border=LINE)


class Stepper(RoundedBox):
    """Compact rounded number field with − / + buttons."""

    def __init__(self, master, variable: tk.DoubleVar, from_, to, step, font=None, width=5, bg=None):
        super().__init__(master, radius=8, pad=(4, 3), bg=bg)
        self.var, self.lo, self.hi, self.step = variable, from_, to, step
        for txt, d in (("−", -1), (None, 0), ("+", 1)):
            if txt is None:
                e = tk.Entry(self.inner, textvariable=variable, width=width, justify="center", font=font,
                             relief="flat", borderwidth=0, background=CARD, foreground=TEXT,
                             highlightthickness=0, insertbackground=TEXT)
                e.pack(side="left", ipady=2, padx=4)
                continue
            b = RoundButton(self.inner, txt, lambda d=d: self._bump(d), font=font, padx=8, pady=1, radius=6,
                            bg=CARD)
            b.pack(side="left")
        self.bind("<Configure>", lambda _e: (self._layout(), self._shrink()), add="+")
        self._shrink_job = self.after_idle(self._shrink_idle)
        self.bind("<Destroy>", self._cancel_shrink, add="+")

    def _shrink_idle(self):
        self._shrink_job = None
        self._shrink()

    def _cancel_shrink(self, event):
        if event.widget is self and self._shrink_job is not None:
            self.after_cancel(self._shrink_job)
            self._shrink_job = None

    def _shrink(self):
        w = self.inner.winfo_reqwidth() + 2 * self.pad[0]
        if int(self.cget("width")) != w:
            self.configure(width=w)

    def _bump(self, d):
        try:
            v = float(self.var.get())
        except (tk.TclError, ValueError):
            v = self.lo
        self.var.set(round(min(max(v + d * self.step, self.lo), self.hi), 4))


class ProgressLine(tk.Canvas):
    """Thin rounded progress bar."""

    def __init__(self, master, bg=None, height=6):
        super().__init__(master, highlightthickness=0, borderwidth=0, height=height,
                         background=bg or master.cget("background"))
        self.h, self.value = height, 0.0
        self._motion = _Animation(self, 0, self._draw)
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, value: float, animate=True):
        self.value = min(max(value, 0.0), 100.0)
        if animate and self.value > self._motion.value:
            self._motion.to(self.value, duration=260)
        else:
            self._motion.jump(self.value)

    def _draw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), 10), self.h
        _smooth_rect(self, 0, 0, w, h, h / 2, fill=TRACK)
        if self._motion.value > 0:
            x = max(h, w * self._motion.value / 100)
            col = GOOD if self._motion.value >= 100 else ACCENT
            _smooth_rect(self, 0, 0, x, h, h / 2, fill=col)
