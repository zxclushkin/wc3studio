"""Skeletal animation for MDX models: node hierarchy, track sampling, linear blend skinning.

WC3 node transform convention (per node):

    local = T(pivot) . T(translation) . R(rotation) . S(scaling) . T(-pivot)
    world = parent_world . local

and a vertex is skinned over the bones listed in its geoset matrix group with equal
weights (non-Reforged models carry no per-vertex weights).
"""
from __future__ import annotations

import math

import numpy as np

from .mdx import Model, Node

DEFAULT_TRANSLATION = (0.0, 0.0, 0.0)
DEFAULT_ROTATION = (0.0, 0.0, 0.0, 1.0)
DEFAULT_SCALING = (1.0, 1.0, 1.0)

# Node flags
DONT_INHERIT_TRANSLATION = 0x1
DONT_INHERIT_SCALING = 0x2
DONT_INHERIT_ROTATION = 0x4
FLAG_NAMES = {
    DONT_INHERIT_TRANSLATION: "DontInheritTranslation",
    DONT_INHERIT_SCALING: "DontInheritScaling",
    DONT_INHERIT_ROTATION: "DontInheritRotation",
}


def _slerp(v0, v1, t):
    """Shortest-path quaternion interpolation.

    MDX stores rotations as quaternions whose sign is arbitrary between keys, so a
    plain component-wise blend between q and -q collapses through zero and tears the
    mesh apart. Flip to the same hemisphere first, then slerp.
    """
    dot = sum(a * b for a, b in zip(v0, v1))
    if dot < 0.0:
        v1 = tuple(-b for b in v1)
        dot = -dot
    if dot > 0.9995:
        blended = [a + (b - a) * t for a, b in zip(v0, v1)]
        n = math.sqrt(sum(c * c for c in blended)) or 1.0
        return tuple(c / n for c in blended)
    theta0 = math.acos(max(-1.0, min(1.0, dot)))
    theta = theta0 * t
    sin_theta0 = math.sin(theta0)
    s0 = math.sin(theta0 - theta) / sin_theta0
    s1 = math.sin(theta) / sin_theta0
    return tuple(a * s0 + b * s1 for a, b in zip(v0, v1))


def _track_value(track: dict | None, frame: float, default, local_time: float | None = None,
                 global_sequences: list | None = None, quaternion: bool = False):
    """Sample a track at ``frame``.

    Interpolation types 2 (hermite) and 3 (bezier) are sampled linearly: WC3 uses them
    mostly for smooth camera and particle motion, and the tangents only bend the curve
    between keys, which the frame sampling here does not need.
    """
    if not track:
        return default
    keys = track["keys"]
    if not keys:
        return default

    if local_time is not None and global_sequences:
        gseq = track.get("global_seq", -1)
        if 0 <= gseq < len(global_sequences):
            duration = global_sequences[gseq]
            if duration > 0:
                frame = math.fmod(local_time, float(duration))

    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]

    for i in range(1, len(keys)):
        f1, v1 = keys[i]
        if frame <= f1:
            f0, v0 = keys[i - 1]
            if track.get("interpolation", 1) == 0 or f1 == f0:
                return v0
            t = (frame - f0) / (f1 - f0)
            if quaternion:
                return _slerp(v0, v1, t)
            return tuple(a + (b - a) * t for a, b in zip(v0, v1))
    return keys[-1][1]


def quat_to_matrix(q) -> np.ndarray:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-9:
        return np.eye(3)
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


def _translation_matrix(v) -> np.ndarray:
    m = np.eye(4)
    m[0, 3], m[1, 3], m[2, 3] = v
    return m


class Animator:
    """Samples a parsed model's skeleton and skins its geosets."""

    def __init__(self, model: Model):
        self.model = model
        self.nodes: list[Node] = list(model.nodes)
        self.by_id = {n.object_id: n for n in self.nodes}
        self.global_sequences = [s for s in getattr(model, "global_sequences", [])]
        self._cache: dict[float, np.ndarray] = {}

    # -- skeleton ---------------------------------------------------------
    def node_matrices(self, frame: float, local_time: float | None = None) -> dict:
        """World matrix for every node at ``frame`` (memoised per frame)."""
        key = round(frame, 4)
        if key in self._cache:
            return self._cache[key]
        if local_time is None:
            local_time = frame

        matrices: dict[int, np.ndarray] = {}

        def compute(node: Node, guard: int = 0) -> np.ndarray:
            if node.object_id in matrices:
                return matrices[node.object_id]
            if guard > 64:  # malformed parent chain
                return np.eye(4)

            local = self._local_matrix(node, frame, local_time)
            parent = self.by_id.get(node.parent_id)
            world = compute(parent, guard + 1) @ local if parent is not None else local
            matrices[node.object_id] = world
            return world

        for node in self.nodes:
            compute(node)
        self._cache[key] = matrices
        if len(self._cache) > 512:
            self._cache.clear()
        return matrices

    def _local_matrix(self, node: Node, frame: float, local_time: float) -> np.ndarray:
        tracks = node.tracks
        translation = _track_value(tracks.get("KGTR"), frame, DEFAULT_TRANSLATION,
                                   local_time, self.global_sequences)
        rotation = _track_value(tracks.get("KGRT"), frame, DEFAULT_ROTATION,
                                local_time, self.global_sequences, quaternion=True)
        scaling = _track_value(tracks.get("KGSC"), frame, DEFAULT_SCALING,
                               local_time, self.global_sequences)

        m = np.eye(4)
        m[:3, :3] = quat_to_matrix(rotation) @ np.diag(scaling)
        m[:3, 3] = translation

        pivot = np.asarray(node.pivot, dtype=np.float64)
        return _translation_matrix(pivot) @ m @ _translation_matrix(-pivot)

    # -- skinning ---------------------------------------------------------
    def skin(self, geoset, matrices: dict, normals: bool = False) -> np.ndarray | None:
        """Skin a geoset's vertices (or normals) at the given pose."""
        source = geoset.normals if normals else geoset.vertices
        if not source:
            return None
        pts = np.asarray(source, dtype=np.float64).reshape(-1, 3)
        if not geoset.matrix_groups or not geoset.matrix_indices:
            return pts

        groups = geoset.matrix_groups
        indices = geoset.matrix_indices
        vertex_groups = geoset.vertex_groups
        out = np.array(pts, dtype=np.float64)
        offset = 0
        starts = []
        for size in groups:
            starts.append((offset, size))
            offset += size

        # bucket vertices by matrix group so each group is transformed in one go
        buckets: dict[int, list[int]] = {}
        for vertex, group in enumerate(vertex_groups):
            if group < len(starts):
                buckets.setdefault(group, []).append(vertex)

        identity = np.eye(4)
        for group, vertices in buckets.items():
            start, size = starts[group]
            bones = indices[start:start + size]
            if not bones:
                continue
            if not normals:
                acc = np.zeros((len(vertices), 3), dtype=np.float64)
                used = 0
                for bone in bones:
                    mat = matrices.get(bone, identity)
                    acc += pts[vertices] @ mat[:3, :3].T + mat[:3, 3]
                    used += 1
                if used:
                    out[vertices] = acc / used
            else:
                acc = np.zeros((len(vertices), 3), dtype=np.float64)
                used = 0
                for bone in bones:
                    mat = matrices.get(bone, identity)
                    acc += pts[vertices] @ mat[:3, :3].T
                    used += 1
                if used:
                    acc /= used
                norms = np.linalg.norm(acc, axis=1, keepdims=True)
                out[vertices] = acc / np.maximum(norms, 1e-9)
        return out

    def skinned_geoset(self, geoset, frame: float, local_time: float | None = None):
        return self.skin(geoset, self.node_matrices(frame, local_time))

    # -- helpers ----------------------------------------------------------
    def bounds(self, frame: float, local_time: float | None = None):
        matrices = self.node_matrices(frame, local_time)
        mins = np.full(3, np.inf)
        maxs = np.full(3, -np.inf)
        for geoset in self.model.geosets:
            pts = self.skin(geoset, matrices)
            if pts is None or not len(pts):
                continue
            mins = np.minimum(mins, pts.min(axis=0))
            maxs = np.maximum(maxs, pts.max(axis=0))
        return mins, maxs

    def track_report(self) -> list:
        """Human-readable summary of what animates on each node."""
        rows = []
        for node in self.nodes:
            moving = []
            for tag, label in (("KGTR", "T"), ("KGRT", "R"), ("KGSC", "S")):
                track = node.tracks.get(tag)
                if track and track["keys"]:
                    moving.append(f"{label}:{len(track['keys'])}")
            if moving:
                rows.append((node.object_id, node.name, node.kind, node.parent_id, " ".join(moving)))
        return rows
