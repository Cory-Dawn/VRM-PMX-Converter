"""Regression checks for the custom Tk controls."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tkinter as tk
from PIL import ImageTk

from vrm2pmx import widgets as W
from vrm2pmx.gui import App, Tooltip, _use_palette


def wait_ms(root, milliseconds):
    done = tk.BooleanVar(root, value=False)
    root.after(milliseconds, lambda: done.set(True))
    root.wait_variable(done)


def destroy_app(app):
    for callback in app.tk.call("after", "info"):
        script = app.tk.call("after", "info", callback)[0]
        if script.split()[0] in (app._tclCommands or ()):
            app.after_cancel(callback)
    app.destroy()


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_curved_edges_have_partial_alpha(self):
        canvas = tk.Canvas(self.root)
        captured = []
        photo_image = ImageTk.PhotoImage

        def capture(image, **kwargs):
            captured.append(image.copy())
            return photo_image(image, **kwargs)

        with patch.object(W.ImageTk, "PhotoImage", side_effect=capture):
            W._smooth_rect(canvas, 0, 0, 36, 20, 10, "#3b6cf6")
            W._smooth_oval(canvas, 0, 0, 16, 16, "#ffffff", "#3b6cf6", 2)
        for image in captured:
            alpha = image.getchannel("A")
            self.assertTrue(any(0 < a < 255 for a in alpha.tobytes()))

    def test_slider_endpoints_and_disabled_state(self):
        value = tk.DoubleVar(self.root, value=30)
        slider = W.Slider(self.root, value, 0, 60, 5)
        slider.configure(width=200)
        slider.pack()
        self.root.deiconify()
        self.root.update()
        slider._drag(SimpleNamespace(x=0))
        self.assertEqual(value.get(), 0)
        slider._drag(SimpleNamespace(x=slider.winfo_width()))
        self.assertEqual(value.get(), 60)
        slider.set_enabled(False)
        slider._drag(SimpleNamespace(x=0))
        self.assertEqual(value.get(), 60)
        for _ in range(20):
            slider._draw()
        self.assertLessEqual(len(slider._smooth_images), 3)

    def test_slider_animates_without_delaying_value_and_retargets(self):
        value = tk.DoubleVar(self.root, value=30)
        slider = W.Slider(self.root, value, 0, 60, 5)
        slider.configure(width=200)
        slider.pack()
        self.root.deiconify()
        self.root.update()
        slider._drag(SimpleNamespace(x=slider.winfo_width()))
        self.assertEqual(value.get(), 60)
        self.assertEqual(slider._motion.target, 60)
        wait_ms(self.root, 35)
        self.assertGreater(slider._motion.value, 30)
        self.assertLess(slider._motion.value, 60)
        slider._drag(SimpleNamespace(x=0))
        self.assertEqual(value.get(), 0)
        self.assertEqual(slider._motion.target, 0)
        wait_ms(self.root, 150)
        self.assertEqual(slider._motion.value, 0)
        slider._drag(SimpleNamespace(x=slider.winfo_width()))
        slider.destroy()
        self.assertIsNone(slider._motion.job)
        wait_ms(self.root, 30)

    def test_toggle_and_button(self):
        value = tk.BooleanVar(self.root, value=True)
        toggle = W.Toggle(self.root, value)
        toggle._toggle()
        self.assertFalse(value.get())
        toggle.set_enabled(False)
        toggle._toggle()
        self.assertFalse(value.get())
        calls = []
        button = W.RoundButton(self.root, "Add", lambda: calls.append(True))
        button.pack()
        self.root.deiconify()
        self.root.update()
        button._click(SimpleNamespace(x=2, y=2))
        button.configure_button(state="disabled")
        button._click(SimpleNamespace(x=2, y=2))
        self.assertEqual(calls, [True])

    def test_animation_reverses_and_cancels_on_destroy(self):
        canvas = tk.Canvas(self.root)
        frames = []
        motion = W._Animation(canvas, 0, lambda: frames.append(motion.value))
        motion.to(1, duration=180)
        wait_ms(self.root, 65)
        self.assertGreater(motion.value, 0)
        self.assertLess(motion.value, 1)
        motion.to(0, duration=100)
        wait_ms(self.root, 150)
        self.assertEqual(motion.value, 0)
        self.assertGreater(len(frames), 2)
        motion.to(1)
        canvas.destroy()
        self.assertIsNone(motion.job)
        wait_ms(self.root, 30)

    def test_segment_and_progress_targets(self):
        selected = tk.StringVar(self.root, value="a")
        segment = W.Segmented(self.root, selected, [("a", "A"), ("b", "B")])
        selected.set("b")
        self.assertEqual(selected.get(), "b")
        wait_ms(self.root, 230)
        self.assertEqual(segment._motion.value, 1)
        selected.set("a")
        segment.destroy()
        progress = W.ProgressLine(self.root)
        progress.set(75)
        self.assertEqual(progress.value, 75)
        wait_ms(self.root, 320)
        self.assertEqual(progress._motion.value, 75)
        progress.set(0)
        self.assertEqual(progress._motion.value, 0)

    def test_tooltip_uses_current_palette_and_closes_with_widget(self):
        for theme in ("light", "dark"):
            _use_palette(theme)
            button = tk.Button(self.root, text="tip")
            button.pack()
            self.root.deiconify()
            self.root.update()
            tip = Tooltip(button, "Example tooltip")
            tip._show()
            self.root.update()
            label = tip.tip.winfo_children()[0].winfo_children()[0]
            self.assertEqual(label.cget("background"), W.PALETTES[theme]["TIP_BG"])
            self.assertEqual(label.cget("foreground"), W.PALETTES[theme]["TIP_FG"])
            left, top, right, bottom = tip._work_area()
            self.assertGreaterEqual(tip.tip.winfo_rootx(), left)
            self.assertLessEqual(tip.tip.winfo_rootx() + tip.tip.winfo_width(), right)
            self.assertGreaterEqual(tip.tip.winfo_rooty(), top)
            self.assertLessEqual(tip.tip.winfo_rooty() + tip.tip.winfo_height(), bottom)
            button.destroy()
            self.assertIsNone(tip.tip)


class AppThemeTests(unittest.TestCase):
    def test_adjustment_bones_setting_is_remembered_and_reset(self):
        with tempfile.TemporaryDirectory() as settings_dir, patch.dict(os.environ, {"APPDATA": settings_dir}):
            app = App()
            try:
                self.assertFalse(app.adjustment_bones.get())
                self.assertEqual((app.adjustment_x.get(), app.adjustment_y.get(), app.adjustment_z.get()),
                                 (-20, 0, 0))
                self.assertTrue(all(not slider._enabled for slider in app.adjustment_sliders))
                app.adjustment_bones.set(True)
                self.assertTrue(all(slider._enabled for slider in app.adjustment_sliders))
                app.adjustment_x.set(7)
                app.adjustment_y.set(-3)
                app.adjustment_z.set(12)
                app.theme.set("dark" if app._mode == "light" else "light")
                self.assertTrue(app._collect_settings()["adjustment_bones"])
                self.assertEqual([app._collect_settings()["adjustment_" + axis] for axis in "xyz"], [7, -3, 12])
                app._save_settings()
                self.assertTrue(app._load_settings()["adjustment_bones"])
                self.assertEqual([app._load_settings()["adjustment_" + axis] for axis in "xyz"], [7, -3, 12])
                app.scale.set(20)
                app.adjustment_reset._click(SimpleNamespace(x=0, y=0))
                self.assertEqual((app.adjustment_x.get(), app.adjustment_y.get(), app.adjustment_z.get()),
                                 (-20, 0, 0))
                self.assertEqual(app.scale.get(), 20)
                app.adjustment_x.set(7)
                app.reset_settings()
                self.assertFalse(app.adjustment_bones.get())
                self.assertEqual((app.adjustment_x.get(), app.adjustment_y.get(), app.adjustment_z.get()),
                                 (-20, 0, 0))
            finally:
                destroy_app(app)

    def test_theme_rebuild_and_font_scaling(self):
        with tempfile.TemporaryDirectory() as settings_dir, patch.dict(os.environ, {"APPDATA": settings_dir}):
            app = App()
            try:
                app.withdraw()
                base_scaling = float(app.tk.call("tk", "scaling"))
                for factor in (1.0, 1.25):
                    app.tk.call("tk", "scaling", base_scaling * factor)
                    for theme in ("light", "dark"):
                        app.theme.set(theme)
                        app.apply_theme(force=True)
                        app.update_idletasks()
                        self.assertEqual(app._mode, theme)
                        self.assertEqual(app.pose_val.cget("text"), "30°")
                        self.assertEqual(app.soft_val.cget("text"), "0.75")
                        self.assertTrue(app.soft_slider._enabled)
                        app.physics.set(False)
                        self.assertFalse(app.soft_slider._enabled)
                        app.physics.set(True)
            finally:
                destroy_app(app)

    def test_theme_selector_animates_across_rebuild(self):
        with tempfile.TemporaryDirectory() as settings_dir, patch.dict(os.environ, {"APPDATA": settings_dir}):
            app = App()
            try:
                app.update()
                app.theme.set("light")
                wait_ms(app, 230)
                tip = Tooltip(app.soft_slider, "Theme test")
                tip._show()
                self.assertIsNotNone(tip.tip)
                app.theme.set("dark")
                self.assertIsNone(tip.tip)
                self.assertEqual(app._mode, "dark")
                self.assertEqual(app.theme_segment._motion.target, 2)
                self.assertLess(app.theme_segment._motion.value, 2)
                wait_ms(app, 230)
                self.assertEqual(app.theme_segment._motion.value, 2)
            finally:
                destroy_app(app)

    def test_progress_events_survive_theme_change(self):
        with tempfile.TemporaryDirectory() as settings_dir, patch.dict(os.environ, {"APPDATA": settings_dir}):
            app = App()
            try:
                app.withdraw()
                app._q.put(("progress", 65))
                app._poll()
                self.assertEqual(app.progress.value, 65)
                wait_ms(app, 70)
                self.assertGreater(app.progress._motion.value, 0)
                self.assertLess(app.progress._motion.value, 65)
                app.theme.set("light" if app._mode == "dark" else "dark")
                self.assertEqual(app.progress.value, 65)
                self.assertEqual(app.progress._motion.value, 65)
                app._q.put(("progress", 100))
                app._poll()
                wait_ms(app, 320)
                self.assertEqual(app.progress._motion.value, 100)
            finally:
                destroy_app(app)

    def test_individual_slider_resets_and_disabled_softness(self):
        with tempfile.TemporaryDirectory() as settings_dir, patch.dict(os.environ, {"APPDATA": settings_dir}):
            app = App()
            try:
                app.withdraw()
                for theme in ("light", "dark"):
                    app.theme.set(theme)
                    app.pose.set(0)
                    app.softness.set(1.0)
                    app.pose_reset._click(SimpleNamespace(x=0, y=0))
                    app.soft_reset._click(SimpleNamespace(x=0, y=0))
                    self.assertEqual(app.pose.get(), 30)
                    self.assertEqual(app.softness.get(), 0.75)
                    self.assertEqual(app.pose_slider._motion.target, 30)
                    self.assertEqual(app.soft_slider._motion.target, 0.75)
                    app.softness.set(1.0)
                    app.physics.set(False)
                    self.assertEqual(app.soft_reset._state, "disabled")
                    app.soft_reset._click(SimpleNamespace(x=0, y=0))
                    self.assertEqual(app.softness.get(), 1.0)
                    app.physics.set(True)
                    app.preset.set("final")
                    self.assertEqual(app.soft_reset._state, "disabled")
                    app.preset.set("universal")
                    app.soft_reset._click(SimpleNamespace(x=0, y=0))
                    self.assertEqual(app.softness.get(), 0.75)
            finally:
                destroy_app(app)


if __name__ == "__main__":
    unittest.main()
