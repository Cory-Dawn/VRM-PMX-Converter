# -*- coding: utf-8 -*-
"""PMX 2.0 / 2.1 reader + writer primitives.

The reader keeps everything needed for a lossless round trip, because the
converter writes a new PMX and must be able to re-open its own output for
verification.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAGIC = b"PMX "

# ------------------------------------------------------------------- helpers
class Buf:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        b = self.data[self.pos: self.pos + n]
        if len(b) != n:
            raise EOFError(f"unexpected EOF at {self.pos} (want {n})")
        self.pos += n
        return b

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i8(self) -> int:
        v = struct.unpack_from("<b", self.data, self.pos)[0]
        self.pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i16(self) -> int:
        v = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def vec(self, n: int) -> tuple:
        v = struct.unpack_from("<" + "f" * n, self.data, self.pos)
        self.pos += 4 * n
        return v

    def text(self, encoding: str) -> str:
        n = self.i32()
        raw = self.take(n)
        return raw.decode(encoding, errors="replace")


class Writer:
    def __init__(self, encoding: str = "utf-16-le"):
        self.parts: list[bytes] = []
        self.encoding = encoding

    def raw(self, b: bytes):
        self.parts.append(b)

    def u8(self, v):
        self.parts.append(struct.pack("<B", v & 0xFF))

    def i8(self, v):
        self.parts.append(struct.pack("<b", max(-128, min(127, int(v)))))

    def u16(self, v):
        self.parts.append(struct.pack("<H", v & 0xFFFF))

    def i16(self, v):
        self.parts.append(struct.pack("<h", int(v)))

    def i32(self, v):
        self.parts.append(struct.pack("<i", int(v)))

    def u32(self, v):
        self.parts.append(struct.pack("<I", v & 0xFFFFFFFF))

    def f32(self, v):
        self.parts.append(struct.pack("<f", float(v)))

    def f32s(self, vals):
        self.parts.append(struct.pack("<" + "f" * len(vals), *[float(x) for x in vals]))

    def text(self, s: str):
        raw = s.encode(self.encoding, errors="replace")
        self.i32(len(raw))
        self.parts.append(raw)

    def idx(self, v: int, size: int):
        """Write a signed index in 1/2/4 bytes."""
        if size == 1:
            self.i8(v)
        elif size == 2:
            self.i16(v)
        else:
            self.i32(v)

    def bytes(self) -> bytes:
        return b"".join(self.parts)


# ------------------------------------------------------------------ dataclass
@dataclass
class PMXVertex:
    position: np.ndarray
    normal: np.ndarray
    uv: np.ndarray
    additional_uvs: list = field(default_factory=list)
    weight_type: int = 0
    bones: list = field(default_factory=list)
    weights: list = field(default_factory=list)
    sdef_c: np.ndarray | None = None
    sdef_r0: np.ndarray | None = None
    sdef_r1: np.ndarray | None = None
    edge_scale: float = 1.0


@dataclass
class PMXMaterial:
    name: str = ""
    name_en: str = ""
    diffuse: tuple = (1, 1, 1, 1)
    specular: tuple = (0, 0, 0)
    shininess: float = 0.0
    ambient: tuple = (0, 0, 0)
    flags: int = 0
    edge_color: tuple = (0, 0, 0, 1)
    edge_size: float = 0.0
    texture: int = -1
    sphere_texture: int = -1
    sphere_mode: int = 0
    toon_ref: int = 1
    toon_value: int = 0
    memo: str = ""
    surface_count: int = 0


@dataclass
class PMXBone:
    name: str = ""
    name_en: str = ""
    position: np.ndarray = field(default_factory=lambda: np.zeros(3))
    parent: int = -1
    layer: int = 0
    flags: int = 0
    tail_bone: int = 0
    tail_offset: np.ndarray = field(default_factory=lambda: np.zeros(3))
    inherit_parent: int = -1
    inherit_weight: float = 0.0
    fixed_axis: np.ndarray = field(default_factory=lambda: np.zeros(3))
    local_axis_x: np.ndarray = field(default_factory=lambda: np.zeros(3))
    local_axis_z: np.ndarray = field(default_factory=lambda: np.zeros(3))
    external_key: int = 0
    ik_target: int = -1
    ik_loop: int = 0
    ik_limit: float = 0.0
    ik_links: list = field(default_factory=list)  # [(bone, has_limit, lo, hi)]


@dataclass
class PMXMorph:
    name: str = ""
    name_en: str = ""
    panel: int = 4
    type: int = 1
    offsets: list = field(default_factory=list)


@dataclass
class PMXRigidBody:
    name: str = ""
    name_en: str = ""
    bone: int = -1
    group: int = 0
    no_collide: int = 0
    shape: int = 0
    size: np.ndarray = field(default_factory=lambda: np.zeros(3))
    position: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rotation: np.ndarray = field(default_factory=lambda: np.zeros(3))
    mass: float = 1.0
    linear_damping: float = 0.5
    angular_damping: float = 0.5
    repulsion: float = 0.0
    friction: float = 0.0
    mode: int = 1


@dataclass
class PMXJoint:
    name: str = ""
    name_en: str = ""
    type: int = 0
    body_a: int = -1
    body_b: int = -1
    position: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rotation: np.ndarray = field(default_factory=lambda: np.zeros(3))
    move_lo: np.ndarray = field(default_factory=lambda: np.zeros(3))
    move_hi: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rot_lo: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rot_hi: np.ndarray = field(default_factory=lambda: np.zeros(3))
    spring_move: np.ndarray = field(default_factory=lambda: np.zeros(3))
    spring_rot: np.ndarray = field(default_factory=lambda: np.zeros(3))


@dataclass
class PMXModel:
    version: float = 2.0
    encoding: int = 0
    extra_uv: int = 0
    idx_vertex: int = 2
    idx_texture: int = 1
    idx_material: int = 1
    idx_bone: int = 1
    idx_morph: int = 1
    idx_rigid: int = 1
    name: str = ""
    name_en: str = ""
    comment: str = ""
    comment_en: str = ""
    vertices: list = field(default_factory=list)
    faces: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    textures: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    bones: list = field(default_factory=list)
    morphs: list = field(default_factory=list)
    display_frames: list = field(default_factory=list)
    rigid_bodies: list = field(default_factory=list)
    joints: list = field(default_factory=list)
    soft_bodies: list = field(default_factory=list)


# -------------------------------------------------------------------- reader
def read_pmx(path: str) -> PMXModel:
    with open(path, "rb") as f:
        data = f.read()
    return parse_pmx(data)


def parse_pmx(data: bytes) -> PMXModel:
    b = Buf(data)
    if b.take(4) != MAGIC:
        raise ValueError("not a PMX file")
    m = PMXModel()
    m.version = b.f32()
    globals_count = b.u8()
    g = [b.u8() for _ in range(globals_count)]
    m.encoding = g[0]
    m.extra_uv = g[1]
    m.idx_vertex, m.idx_texture, m.idx_material, m.idx_bone, m.idx_morph, m.idx_rigid = g[2:8]
    enc = "utf-16-le" if m.encoding == 0 else "utf-8"

    m.name = b.text(enc)
    m.name_en = b.text(enc)
    m.comment = b.text(enc)
    m.comment_en = b.text(enc)

    # ---- vertices
    vcount = b.i32()
    verts = []
    for _ in range(vcount):
        v = PMXVertex(
            position=np.array(b.vec(3), dtype=np.float64),
            normal=np.array(b.vec(3), dtype=np.float64),
            uv=np.array(b.vec(2), dtype=np.float64),
        )
        for _ in range(m.extra_uv):
            v.additional_uvs.append(np.array(b.vec(4), dtype=np.float64))
        wtype = b.u8()
        v.weight_type = wtype
        if wtype == 0:
            v.bones = [b.idx_read(m.idx_bone)]
            v.weights = [1.0]
        elif wtype == 1:
            v.bones = [b.idx_read(m.idx_bone), b.idx_read(m.idx_bone)]
            v.weights = [b.f32()]
        elif wtype == 2:
            v.bones = [b.idx_read(m.idx_bone) for _ in range(4)]
            v.weights = [b.f32() for _ in range(4)]
        elif wtype == 3:
            v.bones = [b.idx_read(m.idx_bone), b.idx_read(m.idx_bone)]
            v.weights = [b.f32()]
            v.sdef_c = np.array(b.vec(3))
            v.sdef_r0 = np.array(b.vec(3))
            v.sdef_r1 = np.array(b.vec(3))
        elif wtype == 4:
            v.bones = [b.idx_read(m.idx_bone) for _ in range(4)]
            v.weights = [b.f32() for _ in range(4)]
        else:
            raise ValueError(f"unknown weight type {wtype}")
        v.edge_scale = b.f32()
        verts.append(v)
    m.vertices = verts

    # ---- faces
    fcount = b.i32()
    faces = np.empty(fcount, dtype=np.int32)
    for i in range(fcount):
        faces[i] = b.idx_read(m.idx_vertex)
    m.faces = faces

    # ---- textures
    m.textures = [b.text(enc) for _ in range(b.i32())]

    # ---- materials
    mat_count = b.i32()
    for _ in range(mat_count):
        mt = PMXMaterial()
        mt.name = b.text(enc)
        mt.name_en = b.text(enc)
        mt.diffuse = b.vec(4)
        mt.specular = b.vec(3)
        mt.shininess = b.f32()
        mt.ambient = b.vec(3)
        mt.flags = b.u8()
        mt.edge_color = b.vec(4)
        mt.edge_size = b.f32()
        mt.texture = b.idx_read(m.idx_texture)
        mt.sphere_texture = b.idx_read(m.idx_texture)
        mt.sphere_mode = b.u8()
        mt.toon_ref = b.u8()
        mt.toon_value = b.idx_read(m.idx_texture) if mt.toon_ref == 0 else b.u8()
        mt.memo = b.text(enc)
        mt.surface_count = b.i32()
        m.materials.append(mt)

    # ---- bones
    bone_count = b.i32()
    for _ in range(bone_count):
        bo = PMXBone()
        bo.name = b.text(enc)
        bo.name_en = b.text(enc)
        bo.position = np.array(b.vec(3))
        bo.parent = b.idx_read(m.idx_bone)
        bo.layer = b.i32()
        bo.flags = b.u16()
        if bo.flags & 0x0001:
            bo.tail_bone = b.idx_read(m.idx_bone)
        else:
            bo.tail_offset = np.array(b.vec(3))
        if bo.flags & (0x0100 | 0x0200):
            bo.inherit_parent = b.idx_read(m.idx_bone)
            bo.inherit_weight = b.f32()
        if bo.flags & 0x0400:
            bo.fixed_axis = np.array(b.vec(3))
        if bo.flags & 0x0800:
            bo.local_axis_x = np.array(b.vec(3))
            bo.local_axis_z = np.array(b.vec(3))
        if bo.flags & 0x2000:
            bo.external_key = b.i32()
        if bo.flags & 0x0020:
            bo.ik_target = b.idx_read(m.idx_bone)
            bo.ik_loop = b.i32()
            bo.ik_limit = b.f32()
            link_count = b.i32()
            links = []
            for _ in range(link_count):
                bi = b.idx_read(m.idx_bone)
                has_limit = b.u8()
                if has_limit:
                    lo = np.array(b.vec(3))
                    hi = np.array(b.vec(3))
                else:
                    lo = hi = None
                links.append((bi, has_limit, lo, hi))
            bo.ik_links = links
        m.bones.append(bo)

    # ---- morphs
    morph_count = b.i32()
    for _ in range(morph_count):
        mo = PMXMorph()
        mo.name = b.text(enc)
        mo.name_en = b.text(enc)
        mo.panel = b.u8()
        mo.type = b.u8()
        n = b.i32()
        offs = []
        for _ in range(n):
            if mo.type == 0:
                offs.append((b.idx_read(m.idx_morph), b.f32()))
            elif mo.type == 1:
                offs.append((b.idx_read(m.idx_vertex), np.array(b.vec(3))))
            elif mo.type == 2:
                offs.append((b.idx_read(m.idx_bone), np.array(b.vec(3)), np.array(b.vec(4))))
            elif 3 <= mo.type <= 7:
                offs.append((b.idx_read(m.idx_vertex), np.array(b.vec(4))))
            elif mo.type == 8:
                offs.append((
                    b.idx_read(m.idx_material),
                    b.u8(),
                    np.array(b.vec(4)),   # diffuse
                    np.array(b.vec(3)),   # specular
                    b.f32(),              # shininess
                    np.array(b.vec(3)),   # ambient
                    np.array(b.vec(4)),   # edge color
                    b.f32(),              # edge size
                    np.array(b.vec(4)),   # texture tint
                    np.array(b.vec(4)),   # sphere tint
                    np.array(b.vec(4)),   # toon tint
                ))
            elif mo.type == 9:
                offs.append((b.idx_read(m.idx_morph), b.f32()))
            elif mo.type == 10:
                offs.append((
                    b.idx_read(m.idx_rigid), b.u8(),
                    np.array(b.vec(3)), np.array(b.vec(3)),
                ))
            else:
                raise ValueError(f"unknown morph type {mo.type}")
        mo.offsets = offs
        m.morphs.append(mo)

    # ---- display frames
    for _ in range(b.i32()):
        fr = {}
        fr["name"] = b.text(enc)
        fr["name_en"] = b.text(enc)
        fr["special"] = b.u8()
        items = []
        for _ in range(b.i32()):
            t = b.u8()
            items.append((t, b.idx_read(m.idx_morph if t == 1 else m.idx_bone)))
        fr["items"] = items
        m.display_frames.append(fr)

    # ---- rigid bodies
    for _ in range(b.i32()):
        r = PMXRigidBody()
        r.name = b.text(enc)
        r.name_en = b.text(enc)
        r.bone = b.idx_read(m.idx_bone)
        r.group = b.u8()
        r.no_collide = b.u16()
        r.shape = b.u8()
        r.size = np.array(b.vec(3))
        r.position = np.array(b.vec(3))
        r.rotation = np.array(b.vec(3))
        r.mass = b.f32()
        r.linear_damping = b.f32()
        r.angular_damping = b.f32()
        r.repulsion = b.f32()
        r.friction = b.f32()
        r.mode = b.u8()
        m.rigid_bodies.append(r)

    # ---- joints
    for _ in range(b.i32()):
        j = PMXJoint()
        j.name = b.text(enc)
        j.name_en = b.text(enc)
        j.type = b.u8()
        j.body_a = b.idx_read(m.idx_rigid)
        j.body_b = b.idx_read(m.idx_rigid)
        j.position = np.array(b.vec(3))
        j.rotation = np.array(b.vec(3))
        j.move_lo = np.array(b.vec(3))
        j.move_hi = np.array(b.vec(3))
        j.rot_lo = np.array(b.vec(3))
        j.rot_hi = np.array(b.vec(3))
        j.spring_move = np.array(b.vec(3))
        j.spring_rot = np.array(b.vec(3))
        m.joints.append(j)

    # ---- soft bodies (2.1)
    if m.version >= 2.1 and b.pos < len(data):
        n = b.i32()
        for _ in range(n):
            sb = {}
            sb["name"] = b.text(enc)
            sb["name_en"] = b.text(enc)
            sb["shape"] = b.u8()
            sb["material"] = b.idx_read(m.idx_material)
            sb["group"] = b.u8()
            sb["no_collide"] = b.u16()
            sb["flags"] = b.u8()
            sb["b_link_distance"] = b.i32()
            sb["cluster_count"] = b.i32()
            sb["total_mass"] = b.f32()
            sb["collision_margin"] = b.f32()
            sb["aero_model"] = b.i32()
            sb["config"] = dict(
                VCF=b.f32(), DP=b.f32(), DG=b.f32(), LF=b.f32(), PR=b.f32(), VC=b.f32(),
                DF=b.f32(), MT=b.f32(), CHR=b.f32(), KHR=b.f32(), SHR=b.f32(), AHR=b.f32(),
                SRHR_CL=b.f32(), SKHR_CL=b.f32(), SSHR_CL=b.f32(),
                SR_SPLT_CL=b.f32(), SK_SPLT_CL=b.f32(), SS_SPLT_CL=b.f32(),
                V_IT=b.i32(), P_IT=b.i32(), D_IT=b.i32(), C_IT=b.i32(),
                LST=b.f32(), AST=b.f32(), VST=b.f32(),
            )
            sb["anchors"] = [b.idx_read(m.idx_rigid) for _ in range(b.i32())]
            sb["pins"] = [b.idx_read(m.idx_vertex) for _ in range(b.i32())]
            m.soft_bodies.append(sb)

    return m


def _idx_read(self: Buf, size: int) -> int:
    if size == 1:
        return self.i8()
    if size == 2:
        return self.i16()
    return self.i32()


Buf.idx_read = _idx_read


# -------------------------------------------------------------------- writer
def write_pmx(m: PMXModel, path: str):
    w = Writer("utf-16-le" if m.encoding == 0 else "utf-8")
    w.raw(MAGIC)
    w.f32(m.version)
    w.u8(8)
    w.u8(m.encoding)
    w.u8(m.extra_uv)
    w.u8(m.idx_vertex)
    w.u8(m.idx_texture)
    w.u8(m.idx_material)
    w.u8(m.idx_bone)
    w.u8(m.idx_morph)
    w.u8(m.idx_rigid)
    w.text(m.name)
    w.text(m.name_en)
    w.text(m.comment)
    w.text(m.comment_en)

    # vertices
    w.i32(len(m.vertices))
    for v in m.vertices:
        w.f32s(v.position)
        w.f32s(v.normal)
        w.f32s(v.uv)
        for auv in v.additional_uvs:
            w.f32s(auv)
        w.u8(v.weight_type)
        if v.weight_type == 0:
            w.idx(v.bones[0], m.idx_bone)
        elif v.weight_type == 1:
            w.idx(v.bones[0], m.idx_bone)
            w.idx(v.bones[1], m.idx_bone)
            w.f32(v.weights[0])
        elif v.weight_type in (2, 4):
            for i in range(4):
                w.idx(v.bones[i], m.idx_bone)
            for i in range(4):
                w.f32(v.weights[i])
        elif v.weight_type == 3:
            w.idx(v.bones[0], m.idx_bone)
            w.idx(v.bones[1], m.idx_bone)
            w.f32(v.weights[0])
            w.f32s(v.sdef_c)
            w.f32s(v.sdef_r0)
            w.f32s(v.sdef_r1)
        w.f32(v.edge_scale)

    # faces
    w.i32(len(m.faces))
    for i in m.faces:
        w.idx(int(i), m.idx_vertex)

    # textures
    w.i32(len(m.textures))
    for t in m.textures:
        w.text(t)

    # materials
    w.i32(len(m.materials))
    for mt in m.materials:
        w.text(mt.name)
        w.text(mt.name_en)
        w.f32s(mt.diffuse)
        w.f32s(mt.specular)
        w.f32(mt.shininess)
        w.f32s(mt.ambient)
        w.u8(mt.flags)
        w.f32s(mt.edge_color)
        w.f32(mt.edge_size)
        w.idx(mt.texture, m.idx_texture)
        w.idx(mt.sphere_texture, m.idx_texture)
        w.u8(mt.sphere_mode)
        w.u8(mt.toon_ref)
        if mt.toon_ref == 0:
            w.idx(mt.toon_value, m.idx_texture)
        else:
            w.u8(mt.toon_value)
        w.text(mt.memo)
        w.i32(mt.surface_count)

    # bones
    w.i32(len(m.bones))
    for bo in m.bones:
        w.text(bo.name)
        w.text(bo.name_en)
        w.f32s(bo.position)
        w.idx(bo.parent, m.idx_bone)
        w.i32(bo.layer)
        w.u16(bo.flags)
        if bo.flags & 0x0001:
            w.idx(bo.tail_bone, m.idx_bone)
        else:
            w.f32s(bo.tail_offset)
        if bo.flags & (0x0100 | 0x0200):
            w.idx(bo.inherit_parent, m.idx_bone)
            w.f32(bo.inherit_weight)
        if bo.flags & 0x0400:
            w.f32s(bo.fixed_axis)
        if bo.flags & 0x0800:
            w.f32s(bo.local_axis_x)
            w.f32s(bo.local_axis_z)
        if bo.flags & 0x2000:
            w.i32(bo.external_key)
        if bo.flags & 0x0020:
            w.idx(bo.ik_target, m.idx_bone)
            w.i32(bo.ik_loop)
            w.f32(bo.ik_limit)
            w.i32(len(bo.ik_links))
            for bi, has_limit, lo, hi in bo.ik_links:
                w.idx(bi, m.idx_bone)
                w.u8(has_limit)
                if has_limit:
                    w.f32s(lo)
                    w.f32s(hi)

    # morphs
    w.i32(len(m.morphs))
    for mo in m.morphs:
        w.text(mo.name)
        w.text(mo.name_en)
        w.u8(mo.panel)
        w.u8(mo.type)
        w.i32(len(mo.offsets))
        for off in mo.offsets:
            if mo.type in (0, 9):
                w.idx(off[0], m.idx_morph)
                w.f32(off[1])
            elif mo.type == 1:
                w.idx(off[0], m.idx_vertex)
                w.f32s(off[1])
            elif mo.type == 2:
                w.idx(off[0], m.idx_bone)
                w.f32s(off[1])
                w.f32s(off[2])
            elif 3 <= mo.type <= 7:
                w.idx(off[0], m.idx_vertex)
                w.f32s(off[1])
            elif mo.type == 8:
                w.idx(off[0], m.idx_material)
                w.u8(off[1])
                w.f32s(off[2])
                w.f32s(off[3])
                w.f32(off[4])
                w.f32s(off[5])
                w.f32s(off[6])
                w.f32(off[7])
                w.f32s(off[8])
                w.f32s(off[9])
                w.f32s(off[10])
            elif mo.type == 10:
                w.idx(off[0], m.idx_rigid)
                w.u8(off[1])
                w.f32s(off[2])
                w.f32s(off[3])

    # display frames
    w.i32(len(m.display_frames))
    for fr in m.display_frames:
        w.text(fr["name"])
        w.text(fr["name_en"])
        w.u8(fr["special"])
        w.i32(len(fr["items"]))
        for t, i in fr["items"]:
            w.u8(t)
            w.idx(i, m.idx_morph if t == 1 else m.idx_bone)

    # rigid bodies
    w.i32(len(m.rigid_bodies))
    for r in m.rigid_bodies:
        w.text(r.name)
        w.text(r.name_en)
        w.idx(r.bone, m.idx_bone)
        w.u8(r.group)
        w.u16(r.no_collide)
        w.u8(r.shape)
        w.f32s(r.size)
        w.f32s(r.position)
        w.f32s(r.rotation)
        w.f32(r.mass)
        w.f32(r.linear_damping)
        w.f32(r.angular_damping)
        w.f32(r.repulsion)
        w.f32(r.friction)
        w.u8(r.mode)

    # joints
    w.i32(len(m.joints))
    for j in m.joints:
        w.text(j.name)
        w.text(j.name_en)
        w.u8(j.type)
        w.idx(j.body_a, m.idx_rigid)
        w.idx(j.body_b, m.idx_rigid)
        w.f32s(j.position)
        w.f32s(j.rotation)
        w.f32s(j.move_lo)
        w.f32s(j.move_hi)
        w.f32s(j.rot_lo)
        w.f32s(j.rot_hi)
        w.f32s(j.spring_move)
        w.f32s(j.spring_rot)

    with open(path, "wb") as f:
        f.write(w.bytes())
