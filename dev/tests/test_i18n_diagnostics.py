"""Diagnostic translations never rewrite user-provided paths, names or errors."""
import unittest

from vrm2pmx.i18n_diagnostics import translate_diagnostic


class DiagnosticTranslationTests(unittest.TestCase):
    def test_preserves_paths_names_and_exception_details(self):
        path = 'C:\\模型\\骨骼 输出文件不存在：测试{0}\\贴图.png'
        name = '骨骼 的父索引越界 / 表情{0}'
        error = '读取失败：\nC:\\模型\\文件.vrm {0}'
        samples = [
            (f'贴图缺失：{path}', path),
            (f'输出文件不存在：{path}', path),
            (f'骨骼 {name} 的父索引越界', name),
            (f'表情 {name} 的材质索引越界', name),
            (f'读取 VRM 失败：{error}', error),
            (f'物理生成失败（已跳过）：{error}', error),
            (f'PMX 回读失败：{error}', error),
        ]
        for source, value in samples:
            for lang in ('en', 'ja'):
                with self.subTest(source=source, language=lang):
                    result = translate_diagnostic(lang, source)
                    self.assertNotEqual(result, source)
                    self.assertIn(value, result)

    def test_chinese_unknown_language_and_unknown_messages_are_unchanged(self):
        for lang in ('zh', 'fr', '', None):
            self.assertEqual(translate_diagnostic(lang, '顶点数为 0'), '顶点数为 0')
        for source in ('custom diagnostic', 'C:\\贴图缺失：图片.png', '前缀顶点数为 0',
                       '顶点数为 0 后缀', '未知错误：骨骼数为 0'):
            for lang in ('en', 'ja'):
                self.assertEqual(translate_diagnostic(lang, source), source)

    def test_basic_messages(self):
        sources = [
            '顶点数为 0', '骨骼数为 0',
            '已把手臂下摆 30°（A-pose）',
            '已将 12 个材质/形态键名称转为日文字形',
            '未发现需要转换的中文材质/形态键名称',
            '索引数 5 不是 3 的倍数',
            '面索引越界：-1..9 / 顶点 3',
            '材质面数合计 3 != 索引数 6',
            '5 个顶点权重异常（和不为 1 或骨骼索引越界）',
            '骨骼 test 的 IK 目标越界',
            '表情 test 的顶点索引越界',
            '刚体 test 关联骨骼越界',
            '刚体 test 质量非法：nan',
            '关节 test 刚体索引越界',
            '关节 test 弹簧参数非法',
            '单段链力臂修正：3 根末端骨骼已移到骨骼中点',
            '物理刚体 350 个（含 320 个关节），MMD 里可能会卡，可关闭部分毛发物理',
            '正式物理预设：12 条链（hair 12），动态刚体 50 个，其中 2 个不参与碰撞',
            '单段链弹簧：3 根，周期约 0.25s，角阻尼 0.900',
            '诊断预设 universal_nocol：universal 参数，所有动态刚体不参与碰撞',
            '通用物理 v10：VRM 参考模拟失败（oops），保持 v7 参数',
            '诊断预设 final_v14singles：final 参数，2 根单段链改用通用版 v14 的弹簧（每步增益 0.1、±2°、物理半径 ×0.5）',
            '通用物理预设：15 个动态刚体，关节弹簧 0.100~1.200（含 MMD 刚度补偿 ×50，上限 10），按 VRM 参数与几何长度/半径换算',
            '通用物理 v6：12 个动态刚体，关节锁定、阻尼 1.0，质量自末端 0.01 起每节 ×3，不按名称分类，重力保持默认即可',
            '通用物理 v6（柔）：12 个动态刚体，阻尼 0.9，关节弹簧 无，3 个关节触到 30FPS 稳定上限。需在 MMD/PE 里把重力设为 0，否则会下垂',
            '通用物理 v7：12 个动态刚体；多节链关节锁定，质量自末端 0.01 起每节 ×3；2 根单段链用 MMD 实测不抖的弹簧（每步增益 0.1）；阻尼 1.0，重力保持默认即可',
        ]
        for source in sources:
            for lang in ('en', 'ja'):
                with self.subTest(source=source, language=lang):
                    result = translate_diagnostic(lang, source)
                    self.assertNotEqual(result, source)
                    if lang == 'en':
                        self.assertFalse(any('\u4e00' <= c <= '\u9fff' for c in result), result)

    def test_v150_adjustment_and_semistandard_bone_diagnostics(self):
        name = '左足D / 調整{0} 中文'
        messages = [
            ('无法创建调整骨：缺少全ての親或存在同名调整骨', None),
            ('没有可添加调整骨的目标骨骼', None),
            (f'{name} 已有同名调整骨或附加变换，已跳过', name),
            (f'{name} 已有同名调整骨或辅助骨，已跳过', name),
            (f'{name} 存在同名骨骼，跳过自动生成扭转骨，避免覆盖原骨骼', name),
            (f'{name} 的关节位置重合，跳过自动生成扭转骨', name),
            (f'{name} 系列存在同名骨骼，跳过生成与权重转移', name),
            (f'{name} 的源骨骼不是直接相连，跳过生成与权重转移', name),
        ]
        for source, value in messages:
            for lang in ('en', 'ja'):
                with self.subTest(source=source, language=lang):
                    result = translate_diagnostic(lang, source)
                    self.assertNotEqual(result, source)
                    if value:
                        self.assertIn(value, result)
            self.assertEqual(translate_diagnostic('zh', source), source)

    def test_zero_gravity_variants(self):
        for damping in ('阻尼 = 1.0（无重力）', '线阻尼 0.985'):
            source = ('2/5 个物理组在 VRM 里是 0 重力。PMX 没有单个刚体的重力开关（MMD 重力是全局的），'
                      f'已把这些刚体的{damping}抑制下坠；若要完全复现，请在 MMD「物理演算 → 重力设定」里把重力调低')
            for lang in ('en', 'ja'):
                self.assertNotEqual(translate_diagnostic(lang, source), source)

    def test_v14_optional_clauses_and_unknown_fallback(self):
        prefix = '通用物理 v14（柔软度 0.75）：按 VRM 参考摆幅逐链调整 10 条链的质量梯度（0.50~2.00），其中头部 3 条再硬 2 倍，'
        tail = '；12 个锁定轴改用 1e-05 的上下限（防止 Bullet 在角度恰为 0 时放开该轴）'
        for single in ('', '2 根单段链锁定，', '2 根单段链用弹簧（每步增益 0.1、±2°、物理半径 ×0.5），'):
            for thick in ('2 条粗短链锁定并缩小物理半径', '2 条粗短链改为根关节 ±3° 弱弹簧', '2 条粗短链改为 ±3° 弱弹簧'):
                for extra in ('', '；3 个刚体初始嵌在碰撞体里，已取消其碰撞'):
                    source = prefix + single + '粗短链再硬 2 倍；' + thick + '；2 条锁定链的刚体放在关节处' + tail + extra
                    for lang in ('en', 'ja'):
                        result = translate_diagnostic(lang, source)
                        self.assertNotEqual(result, source)
                        if lang == 'en':
                            self.assertFalse(any('\u4e00' <= c <= '\u9fff' for c in result), result)
        source = prefix + 'unknown or new clause'
        self.assertEqual(translate_diagnostic('en', source), source)


if __name__ == '__main__':
    unittest.main()
