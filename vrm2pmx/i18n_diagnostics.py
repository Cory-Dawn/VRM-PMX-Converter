"""GUI presentation of known converter diagnostics.

Conversion reports and exported files remain unchanged. Match complete messages
only; captured names, paths and exception text are never translated.
"""
from __future__ import annotations

import re

# Each rule is anchored by fullmatch. Numeric fields are deliberately constrained
# so unrelated user text cannot accidentally be treated as a diagnostic.
_N = r"[-+0-9.eE]+"
_RULES: list[tuple[re.Pattern, str, str]] = []


def _rule(pattern: str, en: str, ja: str) -> None:
    _RULES.append((re.compile(pattern, re.DOTALL), en, ja))


_rule(r"读取 VRM 失败：(.*)", "Failed to read VRM: {0}", "VRM の読み込みに失敗しました：{0}")
_rule(r"已把手臂下摆 (" + _N + r")°（A-pose）", "Arms lowered by {0}° (A-pose)", "腕を {0}° 下げました（A ポーズ）")
_rule(r"已将 (\d+) 个材质/形态键名称转为日文字形", "Converted {0} material/morph names to Japanese glyphs", "{0} 件の材質・モーフ名を日本語の字形に変換しました")
_rule(r"未发现需要转换的中文材质/形态键名称", "No Chinese material/morph names needed glyph conversion", "字形の変換が必要な中国語の材質・モーフ名はありませんでした")
_rule(r"物理生成失败（已跳过）：(.*)", "Physics generation failed (skipped): {0}", "物理の生成に失敗しました（スキップ）：{0}")
_rule(r"输出文件不存在：(.*)", "Output file does not exist: {0}", "出力ファイルが見つかりません：{0}")
_rule(r"PMX 回读失败：(.*)", "Failed to read back PMX: {0}", "出力した PMX の再読み込みに失敗しました：{0}")
_rule(r"顶点数为 0", "Vertex count is zero", "頂点数が 0 です")
_rule(r"骨骼数为 0", "Bone count is zero", "ボーン数が 0 です")
_rule(r"索引数 (\d+) 不是 3 的倍数", "Index count {0} is not a multiple of 3", "インデックス数 {0} が 3 の倍数ではありません")
_rule(r"面索引越界：(-?\d+)\.\.(-?\d+) / 顶点 (\d+)", "Face indices out of range: {0}..{1} / vertices {2}", "面のインデックスが範囲外です：{0}..{1} / 頂点数 {2}")
_rule(r"材质面数合计 (\d+) != 索引数 (\d+)", "Material surface count total {0} != index count {1}", "材質の面インデックス数の合計 {0} != インデックス数 {1}")
_rule(r"(\d+) 个顶点权重异常（和不为 1 或骨骼索引越界）", "{0} vertices have invalid weights (sum is not 1 or bone index is out of range)", "{0} 個の頂点のウェイトが不正です（合計が 1 ではないか、ボーンのインデックスが範囲外）")
_rule(r"骨骼 (.*) 的父索引越界", "Bone {0}: parent index out of range", "ボーン {0}：親のインデックスが範囲外です")
_rule(r"骨骼 (.*) 的 IK 目标越界", "Bone {0}: IK target out of range", "ボーン {0}：IK ターゲットが範囲外です")
_rule(r"表情 (.*) 的顶点索引越界", "Morph {0}: vertex index out of range", "モーフ {0}：頂点のインデックスが範囲外です")
_rule(r"表情 (.*) 的材质索引越界", "Morph {0}: material index out of range", "モーフ {0}：材質のインデックスが範囲外です")
_rule(r"刚体 (.*) 关联骨骼越界", "Rigid body {0}: linked bone out of range", "剛体 {0}：関連ボーンが範囲外です")
_rule(r"刚体 (.*) 质量非法：(.*)", "Rigid body {0}: invalid mass {1}", "剛体 {0}：質量が不正です（{1}）")
_rule(r"关节 (.*) 刚体索引越界", "Joint {0}: rigid body index out of range", "ジョイント {0}：剛体のインデックスが範囲外です")
_rule(r"关节 (.*) 弹簧参数非法", "Joint {0}: invalid spring parameters", "ジョイント {0}：ばねのパラメーターが不正です")
_rule(r"贴图缺失：(.*)", "Missing texture: {0}", "テクスチャが見つかりません：{0}")
_rule(r"单段链力臂修正：(\d+) 根末端骨骼已移到骨骼中点", "Single-segment lever-arm correction: moved {0} end bones to bone midpoints", "1 節チェーンのてこ腕を補正：{0} 本の末端ボーンをボーンの中点へ移動しました")
_rule(r"物理刚体 (\d+) 个（含 (\d+) 个关节），MMD 里可能会卡，可关闭部分毛发物理", "{0} rigid bodies and {1} joints: MMD may run slowly; consider disabling some hair physics", "剛体 {0} 個・ジョイント {1} 個：MMD が重くなる場合は、一部の髪の物理を無効にしてください")
_rule(r"正式物理预设：(\d+) 条链（(.*)），动态刚体 (\d+) 个，其中 (\d+) 个不参与碰撞", "Legacy physics preset: {0} chains ({1}), {2} dynamic rigid bodies, {3} excluded from collisions", "旧版物理プリセット：{0} チェーン（{1}）、動的剛体 {2} 個、うち {3} 個は衝突なし")
_rule(r"单段链弹簧：(\d+) 根，周期约 (" + _N + r")s，角阻尼 (" + _N + r")", "Single-segment springs: {0}, period about {1}s, angular damping {2}", "1 節チェーンのばね：{0} 本、周期約 {1} 秒、角度減衰 {2}")
_rule(r"诊断预设 universal_nocol：universal 参数，所有动态刚体不参与碰撞", "Diagnostic preset universal_nocol: universal parameters, all dynamic rigid bodies excluded from collisions", "診断プリセット universal_nocol：universal のパラメーターを使用し、すべての動的剛体の衝突を無効化")
_rule(r"通用物理 v10：VRM 参考模拟失败（(.*)），保持 v7 参数", "Universal physics v10: VRM reference simulation failed ({0}); keeping v7 parameters", "汎用物理 v10：VRM の参照シミュレーションに失敗（{0}）。v7 のパラメーターを維持します")
_rule(r"诊断预设 final_v14singles：final 参数，(\d+) 根单段链改用通用版 v14 的弹簧（每步增益 (" + _N + r")、±(" + _N + r")°、物理半径 ×(" + _N + r")）", "Diagnostic preset final_v14singles: final parameters; {0} single-segment chains use universal v14 springs (per-step gain {1}, ±{2}°, physics radius ×{3})", "診断プリセット final_v14singles：final のパラメーターを使用。{0} 本の 1 節チェーンに汎用 v14 のばねを適用（ステップごとのゲイン {1}、±{2}°、物理半径 ×{3}）")
_rule(r"通用物理预设：(\d+) 个动态刚体，关节弹簧 (" + _N + r")~(" + _N + r")（含 MMD 刚度补偿 ×(" + _N + r")，上限 (" + _N + r")），按 VRM 参数与几何长度/半径换算", "Universal physics preset: {0} dynamic rigid bodies, joint springs {1}~{2} (MMD stiffness compensation ×{3}, cap {4}), derived from VRM parameters and geometric length/radius", "汎用物理プリセット：動的剛体 {0} 個、ジョイントのばね {1}~{2}（MMD の剛性補正 ×{3}、上限 {4}）。VRM のパラメーターと長さ・半径から換算")
_rule(r"通用物理 v6：(\d+) 个动态刚体，关节锁定、阻尼 1.0，质量自末端 (" + _N + r") 起每节 ×(" + _N + r")，不按名称分类，重力保持默认即可", "Universal physics v6: {0} dynamic rigid bodies, locked joints, damping 1.0; mass starts at {1} at the tip and multiplies by {2} per segment; no name-based classification, keep default gravity", "汎用物理 v6：動的剛体 {0} 個、ジョイント固定、減衰 1.0。末端の質量 {1} から 1 節ごとに ×{2}。名前で分類せず、重力は初期値のまま使用できます")
_rule(r"通用物理 v6（柔）：(\d+) 个动态刚体，阻尼 (" + _N + r")，关节弹簧 (.*)，(\d+) 个关节触到 30FPS 稳定上限。需在 MMD/PE 里把重力设为 0，否则会下垂", "Universal physics v6 (soft): {0} dynamic rigid bodies, damping {1}, joint springs {2}; {3} joints reached the 30 FPS stability cap. Set gravity to 0 in MMD/PE to prevent sagging", "汎用物理 v6（柔）：動的剛体 {0} 個、減衰 {1}、ジョイントのばね {2}。{3} 個のジョイントが 30 FPS の安定上限に達しました。垂れ下がりを防ぐため、MMD/PE の重力を 0 にしてください")
_rule(r"通用物理 v7：(\d+) 个动态刚体；多节链关节锁定，质量自末端 (" + _N + r") 起每节 ×(" + _N + r")；(\d+) 根单段链用 MMD 实测不抖的弹簧（每步增益 (" + _N + r")）；阻尼 1.0，重力保持默认即可", "Universal physics v7: {0} dynamic rigid bodies; multi-segment chains locked, mass starts at {1} at the tip and multiplies by {2} per segment; {3} single-segment chains use springs tested for stability in MMD (per-step gain {4}); damping 1.0, keep default gravity", "汎用物理 v7：動的剛体 {0} 個。複数節のチェーンを固定し、末端の質量 {1} から 1 節ごとに ×{2}。{3} 本の 1 節チェーンには MMD で安定性を確認したばねを適用（ステップごとのゲイン {4}）。減衰 1.0、重力は初期値のまま使用できます")

# Semi-standard and motion-adjustment bone diagnostics (v1.5.0).
_rule(r"无法创建调整骨：缺少全ての親或存在同名调整骨", "Cannot create adjustment bones: missing 全ての親 or an adjustment bone with the same name already exists", "調整ボーンを作成できません：全ての親がないか、同名の調整ボーンが存在します")
_rule(r"(.*) 已有同名调整骨或附加变换，已跳过", "{0}: an adjustment bone with the same name or an additional transform already exists; skipped", "{0}：同名の調整ボーンまたは付与変換が存在するため、スキップしました")
_rule(r"(.*) 已有同名调整骨或辅助骨，已跳过", "{0}: an adjustment bone or helper bone with the same name already exists; skipped", "{0}：同名の調整ボーンまたは補助ボーンが存在するため、スキップしました")
_rule(r"没有可添加调整骨的目标骨骼", "No eligible target bones for adding adjustment bones", "調整ボーンを追加できる対象ボーンがありません")
_rule(r"(.*) 存在同名骨骼，跳过自动生成扭转骨，避免覆盖原骨骼", "{0}: a bone with the same name already exists; skipped automatic twist-bone creation to preserve the existing bone", "{0}：同名のボーンが存在するため、既存のボーンを保護する目的で捩りボーンの自動生成をスキップしました")
_rule(r"(.*) 的关节位置重合，跳过自动生成扭转骨", "{0}: joint positions coincide; skipped automatic twist-bone creation", "{0}：関節の位置が重なっているため、捩りボーンの自動生成をスキップしました")
_rule(r"(.*) 系列存在同名骨骼，跳过生成与权重转移", "{0} chain: a bone with the same name already exists; skipped creation and weight transfer", "{0} 系列：同名のボーンが存在するため、生成とウェイト転送をスキップしました")
_rule(r"(.*) 的源骨骼不是直接相连，跳过生成与权重转移", "{0}: source bones are not directly connected; skipped creation and weight transfer", "{0}：元のボーンが直接つながっていないため、生成とウェイト転送をスキップしました")

_ZERO_G = re.compile(r"(\d+)/(\d+) 个物理组在 VRM 里是 0 重力。PMX 没有单个刚体的重力开关（MMD 重力是全局的），已把这些刚体的(阻尼 = 1\.0（无重力）|线阻尼 0\.985)抑制下坠；若要完全复现，请在 MMD「物理演算 → 重力设定」里把重力调低")

# v14's message is assembled from optional numeric-only clauses. Parsing each
# complete clause keeps user data out of any replacement operation.
_V14_PREFIX = re.compile(r"通用物理 v14（柔软度 (" + _N + r")）：按 VRM 参考摆幅逐链调整 (\d+) 条链的质量梯度（(" + _N + r"~" + _N + r"|无)），其中头部 (\d+) 条再硬 (" + _N + r") 倍，(.*)", re.DOTALL)
_V14_CLAUSES = [
    (re.compile(r"(\d+) 根单段链锁定，"), "{0} single-segment chains locked; ", "1 節チェーン {0} 本を固定。"),
    (re.compile(r"(\d+) 根单段链用弹簧（每步增益 (" + _N + r")、±(" + _N + r")°、物理半径 ×(" + _N + r")），"), "{0} single-segment chains use springs (per-step gain {1}, ±{2}°, physics radius ×{3}); ", "1 節チェーン {0} 本にばねを適用（ステップごとのゲイン {1}、±{2}°、物理半径 ×{3}）。"),
    (re.compile(r"粗短链再硬 (" + _N + r") 倍；"), "short, thick chains stiffened by ×{0}; ", "太く短いチェーンの硬さを ×{0} に調整。"),
    (re.compile(r"(\d+) 条粗短链锁定并缩小物理半径"), "{0} short, thick chains locked with reduced physics radius", "太く短いチェーン {0} 本を固定し、物理半径を縮小"),
    (re.compile(r"(\d+) 条粗短链改为(根关节)? ±(" + _N + r")° 弱弹簧"), "{0} short, thick chains use weak ±{2}° springs ({1})", "太く短いチェーン {0} 本を ±{2}° の弱いばねに変更（{1}）"),
    (re.compile(r"；(\d+) 条锁定链的刚体放在关节处"), "; rigid bodies of {0} locked chains placed at joints", "。固定チェーン {0} 本の剛体をジョイント位置に配置"),
    (re.compile(r"；(\d+) 个锁定轴改用 (" + _N + r") 的上下限（防止 Bullet 在角度恰为 0 时放开该轴）"), "; {0} locked axes use {1} for both limits (prevents Bullet from releasing an axis at exactly 0°)", "。固定軸 {0} 本の上下限を {1} に設定（角度が厳密に 0 のときに Bullet が軸を解放するのを防止）"),
    (re.compile(r"；(\d+) 个刚体初始嵌在碰撞体里，已取消其碰撞"), "; disabled collisions for {0} rigid bodies initially inside colliders", "。初期状態でコライダーに埋まっていた剛体 {0} 個の衝突を無効化"),
]


def _v14(language: str, message: str) -> str | None:
    match = _V14_PREFIX.fullmatch(message)
    if not match:
        return None
    softness, count, ratios, heads, hardness, remaining = match.groups()
    ratios = ("none" if language == "en" else "なし") if ratios == "无" else ratios
    if language == "en":
        output = f"Universal physics v14 (softness {softness}): adjusted mass gradients of {count} chains ({ratios}) to VRM reference sway; {heads} head chains stiffened by ×{hardness}; "
    else:
        output = f"汎用物理 v14（柔らかさ {softness}）：VRM の参照揺れ幅に合わせて {count} チェーンの質量勾配を調整（{ratios}）。頭部の {heads} チェーンの硬さを ×{hardness} に調整。"
    while remaining:
        for pattern, en, ja in _V14_CLAUSES:
            part = pattern.match(remaining)
            if part:
                values = list(part.groups())
                if len(values) == 3 and values[1] in (None, "根关节"):
                    values[1] = ("root joints only" if values[1] else "all joints") if language == "en" else ("根元のジョイントのみ" if values[1] else "全ジョイント")
                output += (en if language == "en" else ja).format(*values)
                remaining = remaining[part.end():]
                break
        else:
            return None
    return output


def translate_diagnostic(language: str, message: str) -> str:
    """Translate only known messages; preserve unknown diagnostics verbatim."""
    if language not in ("en", "ja"):
        return message
    for pattern, en, ja in _RULES:
        match = pattern.fullmatch(message)
        if match:
            values = list(match.groups())
            # The only nonnumeric internal value in this specific template.
            if pattern.pattern.startswith("通用物理 v6（柔）") and values[2] == "无":
                values[2] = "none" if language == "en" else "なし"
            return (en if language == "en" else ja).format(*values)
    match = _ZERO_G.fullmatch(message)
    if match:
        zero, total, damping = match.groups()
        if language == "en":
            damping = "damping = 1.0 (no gravity)" if damping.startswith("阻尼") else "linear damping 0.985"
            return f"{zero}/{total} VRM physics groups use zero gravity. PMX has no per-body gravity switch (MMD gravity is global); applied {damping} to reduce sagging. For a closer match, lower gravity in MMD's Physics → Gravity Settings."
        damping = "減衰 = 1.0（無重力）" if damping.startswith("阻尼") else "移動減衰 0.985"
        return f"VRM の物理グループ {total} 個中 {zero} 個は重力 0 です。PMX には剛体ごとの重力設定がないため（MMD の重力は全体共通）、{damping} を適用して垂れ下がりを抑えています。より近づけるには、MMD の「物理演算 → 重力設定」で重力を下げてください。"
    return _v14(language, message) or message
