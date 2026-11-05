# -*- coding: utf-8 -*-
"""GLB (binary glTF) reader used by the VRM analyzer/converter.

Only the pieces the converter needs are implemented, but parse is strict
enough that errors surface early instead of silently producing garbage.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

GLB_MAGIC = 0x46546C67  # 'glTF'
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT_DTYPE = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
TYPE_SIZE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


@dataclass
class GlbFile:
    path: str
    gltf: dict[str, Any]
    bin_chunk: bytes = b""
    json_chunk: bytes = b""
    _buffer_cache: dict[int, np.ndarray] = field(default_factory=dict)

    # ---------------------------------------------------------------- buffers
    def buffer_bytes(self, index: int) -> np.ndarray:
        if index in self._buffer_cache:
            return self._buffer_cache[index]
        buf = self.gltf.get("buffers", [])[index]
        uri = buf.get("uri")
        if uri is None:
            arr = np.frombuffer(self.bin_chunk, dtype=np.uint8)
        elif uri.startswith("data:"):
            import base64
            payload = uri.split(",", 1)[1]
            arr = np.frombuffer(base64.b64decode(payload), dtype=np.uint8)
        else:
            raise ValueError(f"external buffer not supported: {uri}")
        self._buffer_cache[index] = arr
        return arr

    # -------------------------------------------------------------- accessors
    def accessor(self, index: int) -> np.ndarray:
        """Return accessor data as numpy array shaped (count, ncomp)."""
        acc = self.gltf["accessors"][index]
        dtype = np.dtype(COMPONENT_DTYPE[acc["componentType"]])
        ncomp = TYPE_SIZE[acc["type"]]
        count = acc["count"]

        if "bufferView" in acc:
            bv = self.gltf["bufferViews"][acc["bufferView"]]
            data = self.buffer_bytes(bv["buffer"])
            base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
            stride = bv.get("byteStride") or (dtype.itemsize * ncomp)
            if stride == dtype.itemsize * ncomp:
                raw = data[base: base + stride * count]
                out = raw.view(dtype).reshape(count, ncomp).copy()
            else:
                out = np.empty((count, ncomp), dtype=dtype)
                for i in range(count):
                    off = base + i * stride
                    out[i] = np.frombuffer(
                        data[off: off + dtype.itemsize * ncomp].tobytes(), dtype=dtype, count=ncomp
                    )
        else:
            out = np.zeros((count, ncomp), dtype=dtype)

        if "sparse" in acc:
            sp = acc["sparse"]
            scount = sp["count"]
            idx_info = sp["indices"]
            idx_dt = np.dtype(COMPONENT_DTYPE[idx_info["componentType"]])
            ibv = self.gltf["bufferViews"][idx_info["bufferView"]]
            ib = self.buffer_bytes(ibv["buffer"])[ibv.get("byteOffset", 0) + idx_info.get("byteOffset", 0):]
            indices = np.frombuffer(ib[: scount * idx_dt.itemsize].tobytes(), dtype=idx_dt, count=scount)
            v_info = sp["values"]
            vbv = self.gltf["bufferViews"][v_info["bufferView"]]
            vb = self.buffer_bytes(vbv["buffer"])[vbv.get("byteOffset", 0) + v_info.get("byteOffset", 0):]
            vals = np.frombuffer(
                vb[: scount * dtype.itemsize * ncomp].tobytes(), dtype=dtype, count=scount * ncomp
            ).reshape(scount, ncomp)
            out = out.astype(dtype, copy=True)
            out[indices] = vals

        if acc.get("normalized") and acc["componentType"] != 5126:
            out = normalize_components(out, acc["componentType"])
        return out

    def accessor_scalar(self, index: int) -> np.ndarray:
        return self.accessor(index)[:, 0]

    def indices(self, index: int) -> np.ndarray:
        return self.accessor_scalar(index).astype(np.int64)


def normalize_components(out: np.ndarray, component_type: int) -> np.ndarray:
    maxv = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0, 5125: 4294967295.0}[component_type]
    return out.astype(np.float32) / maxv


def read_glb(path: str) -> GlbFile:
    with open(path, "rb") as f:
        header = f.read(12)
        magic, version, length = struct.unpack("<III", header)
        if magic != GLB_MAGIC:
            raise ValueError(f"not a GLB file: {path}")
        json_chunk = b""
        bin_chunk = b""
        while f.tell() < length:
            chdr = f.read(8)
            if len(chdr) < 8:
                break
            clen, ctype = struct.unpack("<II", chdr)
            payload = f.read(clen)
            if ctype == CHUNK_JSON:
                json_chunk = payload
            elif ctype == CHUNK_BIN:
                bin_chunk = payload
    gltf = json.loads(json_chunk.decode("utf-8"))
    return GlbFile(path=path, gltf=gltf, bin_chunk=bin_chunk, json_chunk=json_chunk)


# --------------------------------------------------------------------- nodes
def node_local_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T  # glTF is column-major
    t = np.array(node.get("translation", [0, 0, 0]), dtype=np.float64)
    r = np.array(node.get("rotation", [0, 0, 0, 1]), dtype=np.float64)  # x,y,z,w
    s = np.array(node.get("scale", [1, 1, 1]), dtype=np.float64)
    return compose_trs(t, r, s)


def compose_trs(t: np.ndarray, q: np.ndarray, s: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        x, y, z, w = 0.0, 0.0, 0.0, 1.0
        n = 1.0
    s2 = 2.0 / n
    xx, yy, zz = x * x * s2, y * y * s2, z * z * s2
    xy, xz, yz = x * y * s2, x * z * s2, y * z * s2
    wx, wy, wz = w * x * s2, w * y * s2, w * z * s2
    m = np.eye(4, dtype=np.float64)
    m[0, 0] = (1 - (yy + zz)) * s[0]
    m[0, 1] = (xy - wz) * s[0]
    m[0, 2] = (xz + wy) * s[0]
    m[1, 0] = (xy + wz) * s[1]
    m[1, 1] = (1 - (xx + zz)) * s[1]
    m[1, 2] = (yz - wx) * s[1]
    m[2, 0] = (xz - wy) * s[2]
    m[2, 1] = (yz + wx) * s[2]
    m[2, 2] = (1 - (xx + yy)) * s[2]
    m[0, 3], m[1, 3], m[2, 3] = t
    return m


def global_transforms(gltf: dict) -> np.ndarray:
    """World transform per node (glTF space)."""
    nodes = gltf.get("nodes", [])
    out = np.zeros((len(nodes), 4, 4), dtype=np.float64)
    visited = np.zeros(len(nodes), dtype=bool)

    def walk(idx: int, parent: np.ndarray):
        if visited[idx]:
            return
        visited[idx] = True
        m = parent @ node_local_matrix(nodes[idx])
        out[idx] = m
        for c in nodes[idx].get("children", []):
            walk(c, m)

    for scene in gltf.get("scenes", []):
        for root in scene.get("nodes", []):
            walk(root, np.eye(4))
    for i in range(len(nodes)):  # orphan nodes
        if not visited[i]:
            walk(i, np.eye(4))
    return out
