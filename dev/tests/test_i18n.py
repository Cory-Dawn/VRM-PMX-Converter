"""Translation coverage, formatting, persistence and GUI language regressions."""
import ast
import json
import os
from pathlib import Path
import string
import tempfile
import unittest
from unittest.mock import patch

from vrm2pmx.i18n import CATALOGS, normalize_language, translate


class CatalogTests(unittest.TestCase):
    def test_catalogs_have_same_keys_and_placeholders(self):
        self.assertEqual(set(CATALOGS['en']), set(CATALOGS['ja']))
        fmt = string.Formatter()
        fields = lambda s: {(name, spec, conv) for _, name, spec, conv in fmt.parse(s) if name is not None}
        for lang, catalog in CATALOGS.items():
            for source, text in catalog.items():
                with self.subTest(lang=lang, source=source):
                    self.assertTrue(text)
                    self.assertEqual(fields(source), fields(text))
                    self.assertEqual(source.count('\n'), text.count('\n'))

    def test_all_literal_gui_keys_are_translated(self):
        path = Path(__file__).parents[2] / 'vrm2pmx' / 'gui.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'tr':
                if n.args and isinstance(n.args[0], ast.Constant):
                    for lang in CATALOGS:
                        self.assertIn(n.args[0].value, CATALOGS[lang])

    def test_fallback_and_user_values_remain_verbatim(self):
        self.assertEqual(normalize_language('invalid'), 'zh')
        self.assertEqual(translate('invalid', '完成'), '完成')
        self.assertEqual(translate('en', 'unknown {literal}'), 'unknown {literal}')
        name = '模型_日本語_{literal}'
        self.assertIn(name, translate('en', '另存为「{p0}」', p0=name))


class LanguageGuiTests(unittest.TestCase):
    def setUp(self):
        from vrm2pmx.gui import App
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'APPDATA': self.tmp.name})
        self.env.start()
        self.app = App()
        self.app.withdraw()

    def tearDown(self):
        from dev.tests.test_widgets import destroy_app
        destroy_app(self.app)
        self.env.stop()
        self.tmp.cleanup()

    def test_switch_preserves_settings_selection_and_log(self):
        a = self.app
        a.files = [{'path': '/tmp/模型.vrm', 'title': '模型名', 'author': '作者名',
                    'size': 100, 'status': 'ok', 'out': '/tmp/模型.pmx'}]
        a.scale.set(20)
        a.jp_names.set(True)
        a._refresh()
        a.tree.selection_set('0')
        a._log('Existing diagnostic / 原始记录')
        a.toggle_log(True)
        for lang in ('en', 'ja', 'zh', 'en'):
            a.language.set(lang)
            a.update()
            self.assertEqual(a.scale.get(), 20)
            self.assertTrue(a.jp_names.get())
            self.assertEqual(a.files[0]['title'], '模型名')
            self.assertEqual(a.tree.selection(), ('0',))
            self.assertEqual(a.tree.heading('title')['text'], translate(lang, '模型名'))
            self.assertIn(translate(lang, '完成'), a.tree.item('0')['values'][-1])
            self.assertIn('Existing diagnostic', a.log.get('1.0', 'end'))
            self.assertTrue(a._log_open)
        from vrm2pmx.gui import App
        self.assertEqual(App._load_settings()['language'], 'en')
        self.assertEqual(a._collect_settings()['language'], 'en')
        a.theme.set('dark')
        a.reset_settings()
        self.assertEqual(a.language.get(), 'en')
        self.assertEqual(a.theme.get(), 'dark')
        tip = '恢复所有设置的默认值（主题和语言除外）'
        self.assertIn('theme and language', translate('en', tip))
        self.assertIn('テーマと言語', translate('ja', tip))

    def test_model_controls_and_status_fit_when_resized_and_localized(self):
        from vrm2pmx.widgets import RoundButton

        a = self.app
        a.deiconify()
        a.files = [{'path': f'/tmp/model-{i}.vrm', 'title': f'Example model {i} with a deliberately long name', 'author': 'Author',
                    'size': 100, 'status': 'wait', 'out': ''} for i in range(30)]
        for lang in ('en', 'ja', 'zh'):
            a.language.set(lang)
            for width in (960, 1080, 1560, 960):
                a.geometry(f'{width}x720')
                a._refresh()
                a.update()

                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)

                add_buttons = [w for w in descendants(a)
                               if isinstance(w, RoundButton) and w._text == translate(lang, '＋ 添加模型')
                               and w.winfo_ismapped()]
                self.assertEqual(len(add_buttons), 1)
                self.assertGreaterEqual(add_buttons[0].winfo_width(), add_buttons[0].winfo_reqwidth())
                status = a.tree.bbox('0', 'status')
                self.assertTrue(status)
                self.assertLessEqual(status[0] + status[2], a.tree.winfo_width())
                title = a.tree.set('0', 'title')
                available = int(a.tree.column('title', 'width')) - 16
                if a._measure.measure('  ' + a.files[0]['title']) <= available:
                    self.assertEqual(title.strip(), a.files[0]['title'])
                else:
                    self.assertTrue(title.endswith('…'))
                    self.assertLessEqual(a._measure.measure(title), available)

    def test_language_locked_during_conversion_and_theme_rebuild(self):
        a = self.app
        a.language.set('en')
        a._busy = True
        a._sync()
        a.language.set('ja')
        self.assertEqual(a.language.get(), 'en')
        self.assertEqual(a._language, 'en')
        a.theme.set('dark')
        a.update()
        self.assertEqual(a.language.get(), 'en')
        self.assertIn('Converter', a.title())
        a._busy = False
        a._sync()
        a.language.set('ja')
        self.assertEqual(a._language, 'ja')

    def test_invalid_saved_language_falls_back_to_chinese(self):
        from vrm2pmx.gui import App, _settings_path
        p = Path(_settings_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'language': 'invalid'}), encoding='utf-8')
        self.assertEqual(normalize_language(App._load_settings()['language']), 'zh')
