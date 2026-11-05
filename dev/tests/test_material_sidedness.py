"""Verify VRM material culling is reflected in PMX double-sided flags."""
import tempfile
import unittest
from pathlib import Path

from vrm2pmx.material import FLAG_DOUBLE_SIDED, FLAG_OUTLINE, MaterialConverter
from vrm2pmx.pmx import PMXModel, read_pmx, write_pmx


class FakeVrm:
    def __init__(self, cull_mode, double_sided):
        self.materials = [{"name": "part"}]
        if double_sided is not None:
            self.materials[0]["doubleSided"] = double_sided
        self.blendshapes = []
        self.floats = {"_OutlineWidthMode": 1, "_OutlineWidth": 0.1}
        if cull_mode != "missing":
            self.floats["_CullMode"] = cull_mode

    def material_props(self, _name):
        return {"floatProperties": self.floats}

    def material_texture(self, _index):
        return None


class MaterialSidednessTests(unittest.TestCase):
    def test_source_culling_and_gltf_fallback_survive_pmx_roundtrip(self):
        cases = (
            (0, True, True),
            (0, False, True),
            (1, True, False),
            (2, True, False),
            (2, False, False),
            ("missing", True, True),
            ("missing", False, False),
            ("missing", None, False),
            (None, True, True),
        )
        for cull_mode, gltf_double, expected in cases:
            with self.subTest(cull_mode=cull_mode, gltf_double=gltf_double):
                materials, _ = MaterialConverter(FakeVrm(cull_mode, gltf_double), "").convert()
                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / "test.pmx")
                    write_pmx(PMXModel(materials=materials), path)
                    result = read_pmx(path).materials[0]
                self.assertEqual(bool(result.flags & FLAG_DOUBLE_SIDED), expected)
                self.assertTrue(result.flags & FLAG_OUTLINE)


if __name__ == "__main__":
    unittest.main()
