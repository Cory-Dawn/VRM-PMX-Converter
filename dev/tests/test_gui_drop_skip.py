"""Checks for file drops and batch conflict handling."""
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tkinterdnd2 import COPY, REFUSE_DROP

from dev.tests.test_widgets import destroy_app
from vrm2pmx.gui import App
from vrm2pmx.widgets import RoundButton


class GuiDropAndSkipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"APPDATA": self.tmp.name})
        self.env.start()
        self.app = App()
        self.app.withdraw()

    def tearDown(self):
        destroy_app(self.app)
        self.env.stop()
        self.tmp.cleanup()

    def test_settings_scroll_in_small_window_and_theme_changes(self):
        app = self.app
        app.geometry("960x720-10000-10000")
        app.deiconify()
        app.update()
        for theme in ("light", "dark"):
            app.theme.set(theme)
            app.update()
            canvas = app.settings_scroll
            self.assertLess(canvas.yview()[1], 1.0)
            app.soft_slider.event_generate("<MouseWheel>", delta=-120)
            app.update()
            self.assertGreater(canvas.yview()[0], 0.0)
            canvas.yview_moveto(1.0)
            app.update()
            self.assertAlmostEqual(canvas.yview()[1], 1.0)
            last_row = app.soft_slider.master.winfo_children()[-1]
            self.assertTrue(last_row.winfo_ismapped())
            self.assertLessEqual(last_row.winfo_rooty() + last_row.winfo_height(),
                                 canvas.winfo_rooty() + canvas.winfo_height())
            app.soft_slider.event_generate("<MouseWheel>", delta=120)
            app.update()
            self.assertLess(canvas.yview()[1], 1.0)
            app.toggle_log(True)
            app.update()
            canvas.yview_moveto(0)
            app.tree.event_generate("<MouseWheel>", delta=-120)
            app.update()
            self.assertEqual(canvas.yview()[0], 0.0)
            app.out_entry.entry.event_generate("<MouseWheel>", delta=-120)
            app.update()
            self.assertGreater(canvas.yview()[0], 0.0)
            app.toggle_log(False)

        app.geometry("1080x1000-10000-10000")
        app.update()
        canvas = app.settings_scroll
        if canvas.winfo_height() >= app.soft_slider.master.winfo_reqheight():
            self.assertEqual(canvas.yview(), (0.0, 1.0))
        else:
            canvas.yview_moveto(1.0)
            app.update()
            self.assertAlmostEqual(canvas.yview()[1], 1.0)

    def test_scale_reset_and_drop_with_unicode_and_duplicate_paths(self):
        app = self.app
        for theme in ("light", "dark"):
            app.theme.set(theme)
            app.scale.set(20)
            app.scale_reset._click(SimpleNamespace(x=0, y=0))
            self.assertEqual(app.scale.get(), 14)
            self.assertTrue(app.tree.dnd_bind("<<Drop>>"))
            self.assertTrue(app.empty.dnd_bind("<<Drop>>"))
            self.assertTrue(app.empty.winfo_children()[0].dnd_bind("<<Drop>>"))

        first = Path(self.tmp.name) / "模型 one.vrm"
        second = Path(self.tmp.name) / "two.VRM"
        other = Path(self.tmp.name) / "other.txt"
        for path in (first, second, other):
            path.touch()
        data = " ".join("{" + str(path) + "}" for path in (first, second, first, other))
        with patch("vrm2pmx.gui.threading.Thread"):
            self.assertEqual(app._drop_files(SimpleNamespace(data=data)), COPY)
        self.assertEqual([f["path"] for f in app.files], [str(first), str(second)])
        app._busy = True
        self.assertEqual(app._drop_files(SimpleNamespace(data=data)), REFUSE_DROP)
        self.assertEqual(len(app.files), 2)

    def test_skip_all_existing_and_convert_remaining(self):
        app = self.app
        output = Path(self.tmp.name) / "output"
        output.mkdir()
        for name in ("first", "second"):
            (output / name).mkdir()
            (output / name / "keep.txt").write_text("keep", encoding="utf-8")
        paths = [str(Path(self.tmp.name) / (name + ".vrm")) for name in ("first", "second", "third")]
        app.files = [
            {"path": path, "title": "", "author": "", "size": 0,
             "status": "ok" if i == 0 else "wait",
             "out": "old.pmx" if i == 0 else ""}
            for i, path in enumerate(paths)
        ]
        app.out_mode.set("custom")
        app.out_dir.set(str(output))
        with patch("vrm2pmx.gui._model_name_for_file", side_effect=lambda p: Path(p).stem), \
             patch.object(app, "_ask_conflict", return_value="skip") as ask, \
             patch("vrm2pmx.gui.threading.Thread") as worker:
            app.start()
        ask.assert_called_once()
        self.assertTrue(ask.call_args.kwargs["allow_skip"])
        jobs, _, skipped = worker.call_args.kwargs["args"]
        self.assertEqual([job[0] for job in jobs], [2])
        self.assertEqual(skipped, 2)
        self.assertEqual((app.files[0]["status"], app.files[0]["out"]), ("ok", "old.pmx"))
        self.assertEqual(app.files[1]["status"], "skip")
        self.assertEqual((output / "first" / "keep.txt").read_text(encoding="utf-8"), "keep")
        self.assertEqual((output / "second" / "keep.txt").read_text(encoding="utf-8"), "keep")

        with patch.object(app, "_show_done_dialog") as done:
            app._q.put(("done", (1, 1, str(output), 2)))
            app._poll()
        self.assertIn("已跳过 2", app.status.get())
        done.assert_called_once_with(1, 1, str(output), 2)

    def test_all_skipped_and_cancel_leave_existing_output_alone(self):
        app = self.app
        output = Path(self.tmp.name) / "output"
        output.mkdir()
        for name in ("one", "two"):
            (output / name).mkdir()
        app.files = [{"path": str(Path(self.tmp.name) / (name + ".vrm")),
                      "title": "", "author": "", "size": 0, "status": "wait", "out": ""}
                     for name in ("one", "two")]
        app.out_mode.set("custom")
        app.out_dir.set(str(output))
        with patch("vrm2pmx.gui._model_name_for_file", side_effect=lambda p: Path(p).stem), \
             patch.object(app, "_ask_conflict", return_value="skip"), \
             patch("vrm2pmx.gui.threading.Thread") as worker:
            app.start()
        worker.assert_not_called()
        self.assertFalse(app._busy)
        self.assertEqual([f["status"] for f in app.files], ["skip", "skip"])
        self.assertIn("已跳过 2", app.status.get())

        before = [f.copy() for f in app.files]
        with patch("vrm2pmx.gui._model_name_for_file", side_effect=lambda p: Path(p).stem), \
             patch.object(app, "_ask_conflict", side_effect=["overwrite", None]):
            app.start()
        self.assertEqual(app.files, before)
        self.assertEqual(app.status.get(), "已取消")

    def test_conflict_dialog_shows_skip_only_for_multiple_models(self):
        app = self.app
        for allow_skip in (False, True):
            observed = {}

            def inspect_dialog():
                dialog = next(w for w in app.winfo_children() if w.winfo_class() == "Toplevel")
                dialog.update_idletasks()
                frame = dialog.winfo_children()[0]
                button_row = frame.winfo_children()[-1]
                buttons = [w for w in button_row.winfo_children() if isinstance(w, RoundButton)]
                observed["texts"] = [button._text for button in buttons]
                observed["visible"] = all(
                    button.winfo_ismapped()
                    and button.winfo_width() >= button.winfo_reqwidth()
                    and button.winfo_x() >= 0
                    and button.winfo_x() + button.winfo_width() <= button_row.winfo_width()
                    for button in buttons
                )
                button = next(b for b in buttons if b._text == ("跳过已转换模型" if allow_skip else "取消"))
                button.command()

            app.after(100, inspect_dialog)
            result = app._ask_conflict("模型", str(Path(self.tmp.name) / "模型"), "模型(1)", allow_skip)
            self.assertEqual("跳过已转换模型" in observed["texts"], allow_skip)
            self.assertTrue(observed["visible"])
            self.assertEqual(result, "skip" if allow_skip else None)


if __name__ == "__main__":
    unittest.main()
