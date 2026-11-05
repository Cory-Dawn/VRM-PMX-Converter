import unittest
from types import SimpleNamespace

import numpy as np

from vrm2pmx.convert import Converter
from vrm2pmx.skeleton import F_ENABLED, F_ROT_ADD, F_TAIL_INDEX, F_VISIBLE, SkeletonBuilder


class LegDTests(unittest.TestCase):
    def builder(self, intermediate=False):
        vrm = SimpleNamespace(humanoid={"leftUpperLeg": 0, "leftLowerLeg": 1,
                                        "leftFoot": 2, "leftToes": 3})
        s = SkeletonBuilder(vrm, 1)
        root = s._add("全ての親", [0, 0, 0], -1)
        waist = s._add("腰", [0, 3, 0], root)
        upper = s._add("左足", [0, 3, 0], waist, source_node=0)
        parent = s._add("extra", [0, 2.5, 0], upper) if intermediate else upper
        lower = s._add("左ひざ", [0, 2, 0], parent, source_node=1)
        foot = s._add("左足首", [0, 1, 0], lower, source_node=2)
        s._add("左つま先", [0, 1, -1], foot, source_node=3)
        s._add_leg_helpers({})
        return s

    def test_parallel_chain_preserves_toe_pivot_and_is_reordered(self):
        s = self.builder()
        s.reorder()
        self.assertEqual(len(s.leg_weight_chains), 1)
        helper = s.bones[s.by_name["左足先EX補助"]]
        self.assertEqual(helper.parent, s.by_name["左足先EX"])
        self.assertEqual(helper.inherit_parent, s.by_name["左つま先"])
        self.assertTrue(helper.flags & F_ROT_ADD)
        self.assertFalse(helper.flags & F_VISIBLE)
        self.assertTrue(helper.flags & F_ENABLED)
        np.testing.assert_allclose(helper.pos, s.bones[s.by_name["左つま先"]].pos)
        for name in ("左足D", "左ひざD", "左足首D", "左足先EX"):
            self.assertTrue(s.bones[s.by_name[name]].flags & F_VISIBLE)

    def test_nonstandard_chain_is_not_reweighted(self):
        s = self.builder(intermediate=True)
        self.assertFalse(s.leg_weight_chains)
        self.assertNotIn("左足D", s.by_name)
        self.assertTrue(any("直接相连" in warning for warning in s.warnings))

    def test_d_bone_export_matches_reference_without_changing_build_tails(self):
        s = self.builder()
        s._fill_tails()
        s.reorder()
        before = [bone.tail_offset.copy() for bone in s.bones]
        exported = s.to_pmx_bones()
        for name in ("左足D", "左ひざD"):
            bone = exported[s.by_name[name]]
            self.assertTrue(bone.flags & F_TAIL_INDEX)
            self.assertEqual(bone.tail_bone, -1)
        ankle = exported[s.by_name["左足首D"]]
        self.assertFalse(ankle.flags & F_TAIL_INDEX)
        np.testing.assert_array_equal(ankle.tail_offset, [0, 0, 0])
        for old, bone in zip(before, s.bones):
            np.testing.assert_array_equal(old, bone.tail_offset)
        np.testing.assert_array_equal(exported[s.by_name["左足首"]].tail_offset,
                                      s.bones[s.by_name["左足首"]].tail_offset)

    def test_d_adjustments_preserve_original_grants_and_match_exported_geometry(self):
        s = self.builder()
        s._fill_tails()
        s.reorder()
        source = {n: s.bones[s.bones[s.by_name[n]].inherit_parent].name
                  for n in ("左足D", "左ひざD", "左足首D")}
        names = s.add_adjustment_bones((7, -3, 12))
        bones = s.to_pmx_bones()
        for name, source_name in source.items():
            original_i = s.by_name[name]
            original = s.bones[original_i]
            control_i = s.by_name[name + "調整"]
            bridge_i = s.by_name[name + "調整付与"]
            bridge = s.bones[bridge_i]
            self.assertIn(name + "調整", names)
            self.assertEqual(s.bones[original.inherit_parent].name, source_name)
            self.assertEqual(original.parent, bridge_i)
            self.assertEqual(bridge.inherit_parent, control_i)
            self.assertFalse(bridge.flags & F_VISIBLE)
            self.assertLess(control_i, bridge_i)
            self.assertLess(bridge_i, original_i)
            self.assertLess(bridge.parent, bridge_i)
            np.testing.assert_allclose(bones[control_i].position - bones[original_i].position, [7, -3, 12])
            self.assertEqual(bool(bones[control_i].flags & F_TAIL_INDEX), bool(bones[original_i].flags & F_TAIL_INDEX))
            if bones[original_i].flags & F_TAIL_INDEX:
                self.assertEqual(bones[control_i].tail_bone, -1)
            else:
                np.testing.assert_array_equal(bones[control_i].tail_offset, bones[original_i].tail_offset)

    def test_ik_adjustment_has_source_pivot_and_distinct_tail_from_ankle(self):
        s = self.builder()
        s._fill_tails()
        s.reorder()
        s.add_adjustment_bones((7, -3, 12))
        bones = s.to_pmx_bones()
        ik, ankle, control, ankle_control = [bones[s.by_name[n]] for n in
            ("左足ＩＫ", "左足首", "左足ＩＫ調整", "左足首調整")]
        np.testing.assert_array_equal(ik.position, ankle.position)
        np.testing.assert_allclose(control.position - ik.position, [7, -3, 12])
        np.testing.assert_allclose(control.position, ankle_control.position)
        np.testing.assert_allclose(control.tail_offset, ik.tail_offset)
        self.assertGreater(ik.tail_offset[2], 0)
        self.assertFalse(np.allclose(ik.tail_offset, ankle.tail_offset))
        self.assertFalse(np.allclose(control.tail_offset, ankle_control.tail_offset))

    def test_ik_display_handle_is_bounded_for_distant_synthetic_toe(self):
        s = self.builder()
        ik_i, toe_i = s.by_name["左足ＩＫ"], s.by_name["左つま先ＩＫ"]
        s.bones[toe_i].pos = s.bones[ik_i].pos + np.array([0., 0., -100.])
        s.scale = 14
        result = s.to_pmx_bones()
        self.assertAlmostEqual(np.linalg.norm(result[ik_i].tail_offset), 2.1)

    def test_split_preserves_unrelated_influences_and_limit(self):
        mapping = {1: 11, 2: 12}
        splits = {11: (np.zeros(3), np.array([0, 0, -1.]), 1., 13)}
        split = dict(Converter._distribute_leg_weights(np.array([0, 0, -2 / 3]),
                                                       [(1, .8), (2, .2)], mapping, splits))
        self.assertAlmostEqual(split[11], .4)
        self.assertAlmostEqual(split[13], .4)
        self.assertAlmostEqual(split[12], .2)
        crowded = dict(Converter._distribute_leg_weights(np.array([0, 0, -2 / 3]),
                                                         [(1, .4), (2, .3), (3, .2), (4, .1)], mapping, splits))
        self.assertEqual(len(crowded), 4)
        self.assertAlmostEqual(sum(crowded.values()), 1)
        self.assertEqual([crowded[12], crowded[3], crowded[4]], [.3, .2, .1])

    def test_display_groups_hide_helpers_without_hiding_custom_bones(self):
        s = self.builder()
        s._add("TouFa.Main", [0, 0, 0], 0)
        s.reorder()
        model = SimpleNamespace(bones=s.to_pmx_bones(), morphs=[])
        frames = {f["name"]: [model.bones[i].name for kind, i in f["items"] if kind == 0]
                  for f in Converter()._display_frames(model)}
        for name in ("左足D", "左ひざD", "左足首D", "左足先EX"):
            self.assertIn(name, frames["足"])
        self.assertIn("左足ＩＫ", frames["IK"])
        self.assertIn("TouFa.Main", frames["その他"])
        self.assertNotIn("左足先EX補助", sum(frames.values(), []))


if __name__ == "__main__":
    unittest.main()
