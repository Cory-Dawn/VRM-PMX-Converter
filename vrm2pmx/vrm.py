# -*- coding: utf-8 -*-
"""VRM 0.x semantic layer on top of raw glTF.

Everything the converter needs is normalised here: world-space bones,
deduplicated mesh vertices in one global order, materials, blendshapes and
spring-bone data. Keeping this layer dumb and explicit makes the later
conversion stages easy to reason about.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .glb import GlbFile, global_transforms, read_glb


@dataclass
class Primitive:
    material: int
    indices: np.ndarray          # triangle vertex indices, into the mesh-local vertex array
    vao: int                     # glTF accessor id of POSITION (for dedup)


@dataclass
class MeshData:
    index: int
    name: str
    node: int
    positions: np.ndarray        # (n,3) world space, glTF metres
    normals: np.ndarray
    uvs: np.ndarray
    joints: np.ndarray           # (n,4) global bone ids (already mapped through skin)
    weights: np.ndarray
    primitives: list[Primitive] = field(default_factory=list)
    vertex_offset: int = 0       # offset into the concatenated vertex array

    @property
    def count(self) -> int:
        return len(self.positions)


@dataclass
class TextureRef:
    index: int
    name: str
    mime: str
    data: bytes


@dataclass
class SpringGroup:
    comment: str
    bones: list[int]
    stiffiness: float
    drag_force: float
    gravity_power: float
    gravity_dir: np.ndarray
    center: int
    hit_radius: float


@dataclass
class Collider:
    node: int
    offset: np.ndarray
    radius: float


@dataclass
class ColliderGroup:
    node: int
    colliders: list[Collider]


class VRM:
    def __init__(self, path: str):
        self.path = path
        self.glb: GlbFile = read_glb(path)
        self.gltf = self.glb.gltf
        self.ext = self.gltf.get("extensions", {}).get("VRM", {})
        if not self.ext:
            raise ValueError("文件里没有 VRM 扩展数据，可能不是 VRM 0.x 模型")
        self.nodes = self.gltf.get("nodes", [])
        self.world = global_transforms(self.gltf)

        self.humanoid: dict[str, int] = {
            b["bone"]: b["node"] for b in self.ext.get("humanoid", {}).get("humanBones", [])
        }
        self.node_names = [n.get("name", f"node{i}") for i, n in enumerate(self.nodes)]

        self.meshes: list[MeshData] = []
        self.materials = self.gltf.get("materials", [])
        self.mat_props = {m.get("name", ""): m for m in self.ext.get("materialProperties", [])}
        self.textures: list[TextureRef] = []
        self.blendshapes = self.ext.get("blendShapeMaster", {}).get("blendShapeGroups", [])
        self.spring_groups: list[SpringGroup] = []
        self.collider_groups: list[ColliderGroup] = []

        self._load_textures()
        self._load_meshes()
        self._load_physics()
        self.mirrored = False
        self.mirror_x()

    # ----------------------------------------------------------------- mirror
    def mirror_x(self):
        """glTF is right-handed, MMD is left-handed. Negating X converts one to
        the other. Verified against all three reference models: X is negated,
        Y and Z are untouched (bounding boxes and per-vertex Z match exactly).

        After the flip a VRM `.L` bone lands on +X, which is where MMD puts its
        「左」 bones, so left/right naming needs no swapping.
        """
        for m in self.meshes:
            m.positions[:, 0] *= -1.0
            m.normals[:, 0] *= -1.0
        # Mirror the transform too, so that local offsets (spring colliders)
        # transform correctly: p_mmd = M @ R @ offset + t_mmd.
        self.world[:, 0, 0:3] *= -1.0
        self.world[:, 0, 3] *= -1.0
        self.mirrored = True

    # ------------------------------------------------------------------ utils
    def node_world_pos(self, node: int) -> np.ndarray:
        return self.world[node][:3, 3].astype(np.float64)

    def bone_name(self, node: int) -> str:
        return self.node_names[node]

    # --------------------------------------------------------------- textures
    def _load_textures(self):
        images = self.gltf.get("images", [])
        for ti, tex in enumerate(self.gltf.get("textures", [])):
            src = tex.get("source")
            if src is None:
                continue
            img = images[src]
            if "bufferView" not in img:
                continue
            bv = self.gltf["bufferViews"][img["bufferView"]]
            raw = self.glb.buffer_bytes(bv["buffer"]).tobytes()
            start = bv.get("byteOffset", 0)
            data = raw[start: start + bv["byteLength"]]
            self.textures.append(TextureRef(
                index=ti,
                name=img.get("name") or f"texture{ti}",
                mime=img.get("mimeType", "image/png"),
                data=data,
            ))

    # ----------------------------------------------------------------- meshes
    def _load_meshes(self):
        """Flatten every mesh into one vertex array, one entry per unique
        POSITION accessor (UniVRM splits a mesh into per-material primitives
        that all share the same vertex buffer)."""
        offset = 0
        for mi, mesh in enumerate(self.gltf.get("meshes", [])):
            node = self._mesh_node(mi)
            M = self.world[node] if node is not None else np.eye(4)
            nrm_mat = M[:3, :3]
            prims = mesh.get("primitives", [])
            seen: dict[int, int] = {}       # accessor -> local index
            order: list[int] = []
            for pi, prim in enumerate(prims):
                vao = prim["attributes"]["POSITION"]
                if vao not in seen:
                    seen[vao] = len(order)
                    order.append(vao)

            if not order:
                continue

            first = prims[0]
            pos = self.glb.accessor(first["attributes"]["POSITION"]).astype(np.float64)
            nrm = self.glb.accessor(first["attributes"]["NORMAL"]).astype(np.float64)
            uv = self.glb.accessor(first["attributes"]["TEXCOORD_0"]).astype(np.float64)

            # world transform (VRM exports are usually identity, but be safe)
            pos = pos @ M[:3, :3].T + M[:3, 3]
            if abs(np.linalg.det(nrm_mat)) > 1e-9:
                inv = np.linalg.inv(nrm_mat).T
                nrm = nrm @ inv.T
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            nrm = nrm / np.maximum(ln, 1e-12)

            joints = np.zeros((len(pos), 4), dtype=np.int64)
            weights = np.zeros((len(pos), 4), dtype=np.float64)
            skin_id = self.nodes[node].get("skin") if node is not None else None
            if skin_id is not None and "JOINTS_0" in first["attributes"]:
                skin = self.gltf["skins"][skin_id]
                jraw = self.glb.accessor(first["attributes"]["JOINTS_0"]).astype(np.int64)
                wraw = self.glb.accessor(first["attributes"]["WEIGHTS_0"]).astype(np.float64)
                joints = np.vectorize(lambda v: skin["joints"][int(v)])(jraw)
                weights = wraw
                # normalise
                ssum = weights.sum(1, keepdims=True)
                weights = np.where(ssum > 1e-9, weights / np.maximum(ssum, 1e-9), 0.0)

            md = MeshData(
                index=mi, name=mesh.get("name", f"mesh{mi}"), node=node if node is not None else -1,
                positions=pos, normals=nrm, uvs=uv, joints=joints, weights=weights,
                vertex_offset=offset,
            )
            for pi, prim in enumerate(prims):
                idx = self.glb.indices(prim["indices"]).reshape(-1)
                md.primitives.append(Primitive(
                    material=prim.get("material", -1),
                    indices=idx,
                    vao=prim["attributes"]["POSITION"],
                ))
            offset += len(pos)
            self.meshes.append(md)

    def _mesh_node(self, mesh_index: int):
        for ni, nd in enumerate(self.nodes):
            if nd.get("mesh") == mesh_index:
                return ni
        return None

    @property
    def vertex_count(self) -> int:
        return sum(m.count for m in self.meshes)

    # ---------------------------------------------------------------- physics
    def _load_physics(self):
        anim = self.ext.get("secondaryAnimation", {})
        for g in anim.get("boneGroups", []):
            gd = g.get("gravityDir", {})
            if isinstance(gd, dict):
                gdir = np.array([gd.get("x", 0), gd.get("y", -1), gd.get("z", 0)], dtype=np.float64)
            else:
                gdir = np.array(gd, dtype=np.float64)
            self.spring_groups.append(SpringGroup(
                comment=g.get("comment", ""),
                bones=list(g.get("bones", [])),
                stiffiness=float(g.get("stiffiness", 1.0)),
                drag_force=float(g.get("dragForce", 0.4)),
                gravity_power=float(g.get("gravityPower", 0.0)),
                gravity_dir=gdir,
                center=int(g.get("center", -1)),
                hit_radius=float(g.get("hitRadius", 0.0)),
            ))
        for cg in anim.get("colliderGroups", []):
            cols = []
            for c in cg.get("colliders", []):
                off = c.get("offset", {})
                if isinstance(off, dict):
                    off = np.array([off.get("x", 0), off.get("y", 0), off.get("z", 0)], dtype=np.float64)
                else:
                    off = np.array(off, dtype=np.float64)
                cols.append(Collider(node=int(cg.get("node", -1)), offset=off,
                                     radius=float(c.get("radius", 0.0))))
            self.collider_groups.append(ColliderGroup(node=int(cg.get("node", -1)), colliders=cols))

    # --------------------------------------------------------------- materials
    def material_props(self, name: str) -> dict:
        return self.mat_props.get(name, {})

    def material_texture(self, mat_index: int):
        """Return the glTF texture index used as base colour, or None."""
        if mat_index < 0 or mat_index >= len(self.materials):
            return None
        pbr = self.materials[mat_index].get("pbrMetallicRoughness", {})
        bct = pbr.get("baseColorTexture")
        if bct:
            return bct.get("index")
        return None

    def summary(self) -> dict:
        return {
            "文件": self.path,
            "模型名": self.ext.get("meta", {}).get("title", ""),
            "节点数": len(self.nodes),
            "网格数": len(self.meshes),
            "顶点数": self.vertex_count,
            "材质数": len(self.materials),
            "贴图数": len(self.textures),
            "表情数": len(self.blendshapes),
            "物理组": len(self.spring_groups),
            "碰撞体组": len(self.collider_groups),
            "人形骨骼": len(self.humanoid),
        }
