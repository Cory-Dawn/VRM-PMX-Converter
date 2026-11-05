import unittest
from types import SimpleNamespace

import numpy as np

from vrm2pmx.skeleton import F_ROT_ADD, SkeletonBuilder


class EyeControlTests(unittest.TestCase):
    def test_control_above_head_faces_forward_and_drives_both_eyes(self):
        world = np.tile(np.eye(4), (3, 1, 1))
        world[0, :3, 3] = [0, 2, 0]
        world[1, :3, 3] = [0.2, 3, -0.2]
        world[2, :3, 3] = [-0.2, 3, -0.2]
        mesh = SimpleNamespace(positions=np.array([[0., 2., 0.], [0., 4., 0.], [0., 5., 0.]]),
                               joints=np.zeros((3, 4), dtype=int),
                               weights=np.tile([1., 0., 0., 0.], (3, 1)))
        vrm = SimpleNamespace(world=world, nodes=[{"children": [1, 2]}, {}, {}],
                              node_names=["head", "eye.L", "eye.R"], meshes=[mesh],
                              humanoid={"head": 0, "leftEye": 1, "rightEye": 2})
        s = SkeletonBuilder(vrm, 1.0).build()
        control_i = s.by_name["両目"]
        control = s.bones[control_i]
        self.assertGreater(control.pos[1], 5.0)
        np.testing.assert_allclose(control.tail_offset, [0, 0, -0.04])
        self.assertEqual(s.bones[control.parent].name, "頭")
        for name in ("左目", "右目"):
            eye = s.bones[s.by_name[name]]
            self.assertTrue(eye.flags & F_ROT_ADD)
            self.assertEqual(eye.inherit_parent, control_i)
            self.assertEqual(eye.inherit_weight, 1.0)
            self.assertLess(control_i, s.by_name[name])


if __name__ == "__main__":
    unittest.main()
