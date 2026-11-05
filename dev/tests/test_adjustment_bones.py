import unittest

import numpy as np

from vrm2pmx.convert import Converter
from vrm2pmx.pmx import PMXModel
from vrm2pmx.skeleton import (F_AXIS, F_ENABLED, F_LOCAL_AXIS, F_MOVE, F_MOVE_ADD, F_ROT,
                              F_ROT_ADD, F_TAIL_INDEX, F_VISIBLE, SkeletonBuilder)


class AdjustmentBoneTests(unittest.TestCase):
    def make_skeleton(self):
        s = SkeletonBuilder(None, 1.0)
        s._add("操作中心", [0, 0, 0], -1, kind="std")
        master = s._add("全ての親", [0, 0, 0], -1, kind="std")
        center = s._add("センター", [0, 1, 0], master, flags=F_ROT | F_MOVE | F_VISIBLE | F_ENABLED)
        spine = s._add("上半身", [0, 2, 0], center)
        arm = s._add("左腕", [1, 3, 0], spine)
        twist = s._add("左腕捩", [2, 3, 0], arm, flags=F_ROT | F_VISIBLE | F_ENABLED | F_AXIS,
                       fixed_axis=np.array([1., 0., 0.]))
        elbow = s._add("左ひじ", [3, 3, 0], twist)
        s.bones[arm].flags |= F_TAIL_INDEX
        s.bones[arm].tail_bone = elbow
        s.node_to_bone[123] = elbow
        s.reorder()
        return s

    def test_adjustment_hierarchy_inheritance_and_remapping(self):
        s = self.make_skeleton()
        names = s.add_adjustment_bones()
        self.assertIn("左腕捩調整", names)
        for name in names[1:]:
            control_index = s.by_name[name]
            control = s.bones[control_index]
            original = s.bones[s.by_name[name[:-2]]]
            np.testing.assert_allclose(control.pos, original.pos + [-20, 0, 0])
            self.assertLess(control_index, s.by_name[original.name])
            self.assertEqual(original.inherit_parent, control_index)
            self.assertEqual(original.inherit_weight, 1)
            self.assertTrue(original.flags & F_ROT_ADD)
        self.assertEqual(s.bones[s.by_name["センター"]].flags & F_MOVE_ADD, F_MOVE_ADD)
        self.assertEqual(s.bones[s.by_name["左腕捩調整"]].flags & F_AXIS, F_AXIS)
        self.assertEqual(s.bones[s.by_name["左ひじ調整"]].parent, s.by_name["左腕捩調整"])
        self.assertEqual(s.bones[s.by_name["左腕調整"]].tail_bone, s.by_name["左ひじ調整"])
        self.assertEqual(s.bones[s.node_to_bone[123]].name, "左ひじ")
        model = PMXModel(bones=s.to_pmx_bones())
        frame = next(f for f in Converter()._display_frames(model, names) if f["name"] == "_調整ボーン")
        self.assertEqual([model.bones[i].name for _, i in frame["items"]], names)

    def test_existing_inheritance_and_name_collisions_are_not_overwritten(self):
        s = self.make_skeleton()
        arm = s.bones[s.by_name["左腕"]]
        arm.flags |= F_ROT_ADD
        arm.inherit_parent = s.by_name["上半身"]
        arm.inherit_weight = 0.4
        s._add("左ひじ調整", [0, 0, 0], -1)
        names = s.add_adjustment_bones()
        self.assertNotIn("左腕調整", names)
        self.assertNotIn("左ひじ調整", names)
        arm = s.bones[s.by_name["左腕"]]
        self.assertEqual(s.bones[arm.inherit_parent].name, "上半身")
        self.assertEqual(arm.inherit_weight, 0.4)
        self.assertEqual(len(s.warnings), 2)

    def test_leg_fk_controls_follow_chain_and_drive_original_bones(self):
        s = self.make_skeleton()
        lower = s._add("下半身", [0, 1, 0], s.by_name["センター"])
        for side, x in (("左", 1), ("右", -1)):
            waist_cancel = s._add("腰キャンセル" + side, [x, 1, 0], lower)
            thigh = s._add(side + "足", [x, 1, 0], waist_cancel)
            knee = s._add(side + "ひざ", [x, 0, 0], thigh)
            s._add(side + "足首", [x, -1, 0], knee)
        s.reorder()
        names = s.add_adjustment_bones()
        for side in ("左", "右"):
            for original_name, parent_name in ((side + "足", "下半身調整"),
                                               (side + "ひざ", side + "足調整"),
                                               (side + "足首", side + "ひざ調整")):
                original = s.bones[s.by_name[original_name]]
                control_name = original_name + "調整"
                self.assertIn(control_name, names)
                control_i = s.by_name[control_name]
                self.assertEqual(s.bones[s.bones[control_i].parent].name, parent_name)
                self.assertEqual(original.inherit_parent, control_i)
                self.assertTrue(original.flags & F_ROT_ADD)
                self.assertEqual(original.inherit_weight, 1.0)
                self.assertLess(control_i, s.by_name[original_name])

    def test_custom_offset_moves_only_adjustment_controls(self):
        s = self.make_skeleton()
        positions = {b.name: b.pos.copy() for b in s.bones}
        names = s.add_adjustment_bones((7.0, -3.0, 12.0))
        np.testing.assert_allclose(s.bones[s.by_name["調整ボーン親"]].pos, [7, -3, 12])
        for name in names[1:]:
            np.testing.assert_allclose(s.bones[s.by_name[name]].pos,
                                       positions[name[:-2]] + [7, -3, 12])
        for name, position in positions.items():
            np.testing.assert_allclose(s.bones[s.by_name[name]].pos, position)

    def test_export_resynchronizes_late_source_changes_and_unmatched_tail(self):
        s = self.make_skeleton()
        s.bones[s.by_name["全ての親"]].pos[:] = [1, 2, 3]
        s.bones[s.by_name["全ての親"]].tail_offset[:] = [0, 3, 0]
        s.add_adjustment_bones((7, -3, 12))
        np.testing.assert_array_equal(s.bones[s.by_name["調整ボーン親"]].pos, [8, -1, 15])
        center = s.bones[s.by_name["センター"]]
        center.pos[:] = [2, 4, 6]
        center.tail_offset = np.array([0., 2., -1.])
        arm = s.bones[s.by_name["左腕"]]
        other = s._add("unadjusted tip", [3, 7, 9], s.by_name["左腕"])
        arm.tail_bone = other
        arm.flags |= F_TAIL_INDEX
        result = s.to_pmx_bones()
        root = result[s.by_name["調整ボーン親"]]
        np.testing.assert_allclose(root.position, [8, -1, 15])
        np.testing.assert_allclose(root.tail_offset, [0, 3, 0])
        actual_center = result[s.by_name["センター調整"]]
        np.testing.assert_allclose(actual_center.position, [9, 1, 18])
        np.testing.assert_allclose(actual_center.tail_offset, center.tail_offset)
        actual_arm = result[s.by_name["左腕調整"]]
        self.assertFalse(actual_arm.flags & F_TAIL_INDEX)
        np.testing.assert_allclose(actual_arm.tail_offset, s.bones[other].pos - arm.pos)

    def test_adjustment_arm_axes_follow_the_actual_source_direction(self):
        s = self.make_skeleton()
        s.add_adjustment_bones((3, 5, -2))
        source = s.bones[s.by_name["左腕"]]
        target = s.bones[source.tail_bone]
        target.pos[:] = source.pos + np.array([2., -1., 0.5])
        result = s.to_pmx_bones()
        control = result[s.by_name["左腕調整"]]
        self.assertTrue(control.flags & F_LOCAL_AXIS)
        direction = target.pos - source.pos
        np.testing.assert_allclose(control.local_axis_x, direction / np.linalg.norm(direction))
        self.assertAlmostEqual(float(np.dot(control.local_axis_x, control.local_axis_z)), 0)
        self.assertAlmostEqual(float(np.linalg.norm(control.local_axis_z)), 1)
        twist = result[s.by_name["左腕捩調整"]]
        self.assertTrue(twist.flags & F_LOCAL_AXIS)
        np.testing.assert_allclose(twist.local_axis_x, [1, 0, 0])

    def test_invalid_offset_is_rejected_before_changes(self):
        s = self.make_skeleton()
        original_count = len(s.bones)
        for invalid in ((1, 2), (1, float("nan"), 3)):
            with self.assertRaises(ValueError):
                s.add_adjustment_bones(invalid)
            self.assertEqual(len(s.bones), original_count)


if __name__ == "__main__":
    unittest.main()
