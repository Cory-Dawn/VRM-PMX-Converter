import unittest
from types import SimpleNamespace

import numpy as np

from vrm2pmx.skeleton import SkeletonBuilder


class BranchBoneDisplayTests(unittest.TestCase):
    def test_head_branch_uses_source_axis_without_changing_physics_tail(self):
        world = np.tile(np.eye(4), (2, 1, 1))
        world[1, :3, 1] = [0, 0, -2]
        s = SkeletonBuilder(SimpleNamespace(humanoid={"head": 0}, world=world), 14)
        head = s._add("頭", [0, 0, 0], -1, source_node=0)
        branch = s._add("hair branch", [0, 1, 0], head, source_node=1)
        for xyz in ([2, 1, 0], [-2, 1, 0], [0, 3, 0]):
            s._add("strand", xyz, branch)
        s._fill_tails()
        before = s.bones[branch].tail_offset.copy()
        original_parents = [b.parent for b in s.bones]
        output = s.to_pmx_bones()
        np.testing.assert_allclose(output[branch].tail_offset, [0, 0, -0.7])
        np.testing.assert_array_equal(s.bones[branch].tail_offset, before)
        self.assertEqual([b.parent for b in output], original_parents)

    def test_two_children_and_non_head_branches_are_unchanged(self):
        world = np.tile(np.eye(4), (3, 1, 1))
        s = SkeletonBuilder(SimpleNamespace(humanoid={"head": 0}, world=world), 14)
        head = s._add("頭", [0, 0, 0], -1, source_node=0)
        fork = s._add("fork", [0, 1, 0], head, source_node=1)
        other = s._add("other", [0, 2, 0], fork, source_node=2)
        s._add("fork child", [2, 1, 0], fork)
        for i in range(3):
            s._add("other child", [i + 1, 3, 0], other)
        s._fill_tails()
        output = s.to_pmx_bones()
        np.testing.assert_array_equal(output[fork].tail_offset, s.bones[fork].tail_offset)
        np.testing.assert_array_equal(output[other].tail_offset, s.bones[other].tail_offset)


if __name__ == "__main__":
    unittest.main()
