"""Regression checks for material visibility and PMX outline alpha."""
import tempfile
import unittest
from pathlib import Path

from vrm2pmx.material import MaterialConverter
from vrm2pmx.morph import MorphConverter
from vrm2pmx.pmx import PMXModel, read_pmx, write_pmx


class FakeVrm:
    def __init__(self, base_alpha, edge_alpha, target_alpha, *, outline=True, prop="_Color"):
        self.materials = [{"name": "part"}]
        self.blendshapes = [{
            "name": "switch",
            "materialValues": [{
                "materialName": "part",
                "propertyName": prop,
                "targetValue": [1, 1, 1, target_alpha],
            }],
        }]
        self.props = {
            "vectorProperties": {
                "_Color": [1, 1, 1, base_alpha],
                "_OutlineColor": [0.2, 0.3, 0.4, edge_alpha],
            },
            "floatProperties": {
                "_OutlineWidthMode": 1 if outline else 0,
                "_OutlineWidth": 0.1,
            },
        }

    def material_props(self, name):
        return self.props if name == "part" else {}

    def material_texture(self, _index):
        return None


class OutlineMorphTests(unittest.TestCase):
    def test_visibility_tracks_base_and_interpolated_alpha(self):
        for base, target, edge in ((0.0, 1.0, 1.0), (1.0, 0.0, 1.0),
                                   (0.2, 0.8, 0.4), (1.0, 0.0, 0.0)):
            with self.subTest(base=base, target=target, edge=edge):
                vrm = FakeVrm(base, edge, target)
                materials, _ = MaterialConverter(vrm, "").convert()
                morphs = MorphConverter(vrm, 14, {"part": 0}).convert().morphs
                model = PMXModel(materials=materials, morphs=morphs)
                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / "test.pmx")
                    write_pmx(model, path)
                    restored = read_pmx(path)
                material = restored.materials[0]
                offset = restored.morphs[0].offsets[0]
                for weight in (0.0, 0.5, 1.0):
                    body_alpha = material.diffuse[3] + weight * offset[2][3]
                    outline_alpha = material.edge_color[3] + weight * offset[6][3]
                    self.assertAlmostEqual(body_alpha, base + weight * (target - base), places=6)
                    self.assertAlmostEqual(outline_alpha, edge * body_alpha, places=6)

    def test_unrelated_and_unoutlined_materials_keep_original_edge(self):
        for vrm in (FakeVrm(0.2, 0.4, 0.2),
                    FakeVrm(0.2, 0.4, 1.0, prop="_EmissionColor"),
                    FakeVrm(0.2, 0.4, 1.0, outline=False)):
            with self.subTest(vrm=vrm):
                materials, _ = MaterialConverter(vrm, "").convert()
                self.assertAlmostEqual(materials[0].edge_color[3], 0.4)


if __name__ == "__main__":
    unittest.main()
