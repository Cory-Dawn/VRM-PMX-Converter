import unittest

import numpy as np

from vrm2pmx.convert import Converter
from vrm2pmx.skeleton import F_AXIS, F_ROT_ADD, SkeletonBuilder


class ArmTwistTest(unittest.TestCase):
    def setUp(self):
        self.builder = SkeletonBuilder(None, 1.0)
        self.root = self.builder._add("左腕", [0, 0, 0], -1)
        self.end = self.builder._add("左ひじ", [4, 0, 0], self.root)
        self.builder._add_twist_chain(True, "腕捩", self.root, self.end)

    def test_chain_has_fixed_axis_and_ordered_rotation_grants(self):
        skel = self.builder
        skel.reorder()
        control = skel.bones[skel.by_name["左腕捩"]]
        self.assertTrue(control.flags & F_AXIS)
        np.testing.assert_allclose(control.fixed_axis, [1, 0, 0])
        np.testing.assert_allclose(control.pos, [2.4, 0, 0])
        self.assertEqual(skel.bones[skel.by_name["左ひじ"]].parent, skel.by_name["左腕捩"])
        for n, ratio in enumerate((0.25, 0.5, 0.75), 1):
            helper = skel.bones[skel.by_name[f"左腕捩{n}"]]
            self.assertTrue(helper.flags & F_ROT_ADD)
            self.assertEqual(helper.parent, skel.by_name["左腕"])
            self.assertEqual(helper.inherit_parent, skel.by_name["左腕捩"])
            self.assertLess(helper.inherit_parent, skel.by_name[helper.name])
            self.assertEqual(helper.inherit_weight, ratio)

    def test_interpolated_influence_and_four_bone_limit(self):
        # Root, three intermediate grants, full control.
        chains = {0: (np.zeros(3), np.array([4., 0, 0]), 16., [0, 10, 11, 12, 13])}
        split = dict(Converter._distribute_twist_weights(np.array([1.5, 0, 0]), [(0, 0.8), (2, 0.2)], chains))
        self.assertAlmostEqual(split[10], 0.4)
        self.assertAlmostEqual(split[11], 0.4)
        self.assertAlmostEqual(split[2], 0.2)
        crowded = dict(Converter._distribute_twist_weights(np.array([1.5, 0, 0]),
                                                           [(0, 0.4), (2, 0.3), (3, 0.2), (4, 0.1)], chains))
        self.assertEqual(len(crowded), 4)
        self.assertAlmostEqual(sum(crowded.values()), 1.0)
        for bone, expected in [(2, 0.3), (3, 0.2), (4, 0.1)]:
            self.assertAlmostEqual(crowded[bone], expected)
        self.assertAlmostEqual(crowded.get(10, 0) + crowded.get(11, 0), 0.4)

    def test_duplicate_names_and_degenerate_segments_do_not_add_controls(self):
        skel = SkeletonBuilder(None, 1)
        root = skel._add("左腕", [0, 0, 0], -1)
        end = skel._add("左ひじ", [0, 0, 0], root)
        skel._add_twist_chain(True, "腕捩", root, end)
        self.assertEqual(len(skel.bones), 2)
        skel.bones[end].pos[0] = 1
        skel._add("左腕捩1", [0, 0, 0], root)
        skel._add_twist_chain(True, "腕捩", root, end)
        self.assertEqual(len(skel.bones), 3)
        self.assertEqual(len(skel.warnings), 2)


if __name__ == "__main__":
    unittest.main()
