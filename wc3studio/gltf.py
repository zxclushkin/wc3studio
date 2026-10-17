"""glTF 2.0 (.glb) export with skinning and per-sequence animations, plus a reader.

Why the extra maths: an MDX node's local transform is

    local = T(pivot) . T(translation) . R(rotation) . S(scaling) . T(-pivot)

which is *not* a plain glTF TRS. Writing A = R . S,

    local = [A | pivot + translation - A . pivot]

so a glTF node can express it exactly with translation' = pivot + t - A . pivot,
rotation' = R and scale' = S. Pivots then live in the node transforms and the
inverse bind matrices are all identity (MDX stores no rest pose other than pivots).
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np

from .anim import (DEFAULT_ROTATION, DEFAULT_SCALING, DEFAULT_TRANSLATION, Animator,
                   _track_value, quat_to_matrix)
from .mdx import Model
from .resources import Resources, pick_layer

__all__ = ["export_glb", "read_glb", "GlbStats", "GLB_MAGIC"]

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

FLOAT = 5126
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
UNSIGNED_BYTE = 5121

TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
DTYPE = {FLOAT: "<f4", UNSIGNED_SHORT: "<u2", UNSIGNED_INT: "<u4", UNSIGNED_BYTE: "<u1"}

WRAP_REPEAT = 10497
WRAP_CLAMP = 33071
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963


class GlbStats(dict):
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<GlbStats meshes={self.get('meshes')} triangles={self.get('triangles')} "
                f"animations={self.get('animations')} bytes={self.get('bytes')}>")


class _Builder:
    """Collects the BIN chunk plus the bufferViews and accessors pointing into it."""

    def __init__(self):
        self.bin = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def view(self, raw: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        offset = len(self.bin)
        self.bin.extend(raw)
        entry = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target is not None:
            entry["target"] = target
        self.views.append(entry)
        return len(self.views) - 1

    def accessor(self, values, type_name: str, component_type: int,
                 minmax: bool = False, target: int | None = None) -> int:
        array = np.asarray(values, dtype=DTYPE[component_type])
        count = int(array.size // TYPE_COUNT[type_name]) if array.size else 0
        view = self.view(array.tobytes(), target)
        accessor = {"bufferView": view, "componentType": component_type,
                    "count": count, "type": type_name}
        if minmax and count:
            shaped = np.asarray(values, dtype=np.float64).reshape(count, TYPE_COUNT[type_name])
            accessor["min"] = [float(v) for v in shaped.min(axis=0)]
            accessor["max"] = [float(v) for v in shaped.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _unflip_quaternions(keys) -> list:
    """Keep consecutive quaternions in one hemisphere so LINEAR means the short arc."""
    out = []
    previous = None
    for value in keys:
        q = [float(c) for c in value]
        if previous is not None and sum(a * b for a, b in zip(previous, q)) < 0.0:
            q = [-c for c in q]
        out.append(q)
        previous = q
    return out


def _sample_times(node, start: float, end: float, bake_fps: float,
                  max_angle_step: float = 6.0) -> list:
    """Times (absolute MDX frames) at which to emit animation keys for one node.

    The source keys are used directly, but a rotation about a pivot turns the node's
    translation into ``pivot - A(f).pivot``, which is curved; glTF can only interpolate
    that linearly, so such spans are subdivided by rotation angle.
    """
    times = {start, end}
    needs_bake = False
    for tag in ("KGTR", "KGRT", "KGSC"):
        track = node.tracks.get(tag)
        if not track:
            continue
        if track.get("global_seq", -1) >= 0:
            needs_bake = True
            continue
        for frame, _value in track["keys"]:
            if start <= frame <= end:
                times.add(float(frame))

    pivot_length = float(np.linalg.norm(node.pivot))
    rotation_track = node.tracks.get("KGRT")
    if rotation_track and pivot_length > 1e-6:
        keys = rotation_track["keys"]
        for (f0, q0), (f1, q1) in zip(keys, keys[1:]):
            if f1 <= start or f0 >= end or f1 <= f0:
                continue
            dot = abs(sum(a * b for a, b in zip(q0, q1)))
            angle = 2.0 * math.degrees(math.acos(min(1.0, max(0.0, dot))))
            steps = int(math.ceil(angle / max_angle_step))
            user_f0, user_f1 = max(f0, start), min(f1, end)
            for step in range(1, steps):
                times.add(user_f0 + (user_f1 - user_f0) * step / steps)

    if needs_bake:
        step = 1000.0 / max(bake_fps, 1.0)
        moment = start
        while moment < end:
            times.add(moment)
            moment += step
    return sorted(t for t in times if start <= t <= end)


def export_glb(model: Model, path: str | Path, resources: Resources | None = None,
               sequences: list[str] | None = None, all_geosets: bool = False,
               bake_fps: float = 30.0) -> GlbStats:
    """Write ``model`` as a binary glTF file with a skin and animations."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build = _Builder()
    stats = GlbStats(meshes=0, primitives=0, triangles=0, vertices=0, textures=0,
                     animations=0, nodes=0, bytes=0, missing_textures=[], skins=0)

    hidden = model.hidden_geosets if all_geosets else model.pose_hidden_geosets

    # --- skeleton ----------------------------------------------------------
    nodes: list[dict] = []
    mdx_to_gltf: dict[int, int] = {}
    for node in model.nodes:
        mdx_to_gltf[node.object_id] = len(nodes)
        # A node's rest transform is identity: the MDX pivot cancels out
        # (T(p) . T(0) . I . I . T(-p)), it only acts as the rotation centre once the
        # node is animated, which the animation channels below take care of.
        nodes.append({"name": node.name or f"{node.kind}_{node.object_id}"})

    for node in model.nodes:
        if node.parent_id in mdx_to_gltf:
            parent = nodes[mdx_to_gltf[node.parent_id]]
            parent.setdefault("children", []).append(mdx_to_gltf[node.object_id])

    root_nodes = [mdx_to_gltf[n.object_id] for n in model.nodes if n.parent_id < 0]

    # --- materials and textures -------------------------------------------
    materials: list[dict] = []
    images: list[dict] = []
    textures: list[dict] = []
    samplers: list[dict] = []
    texture_cache: dict[str, int] = {}

    def texture_index(internal_path: str, replaceable_id: int, wrap_mode: int):
        key = (internal_path or f"\x00rid{replaceable_id}").lower()
        if key in texture_cache:
            return texture_cache[key]
        if resources is None:
            return None
        png = resources.png_bytes(internal_path, replaceable_id)
        if png is None:
            if internal_path:
                stats["missing_textures"].append(internal_path)
            return None
        view = build.view(png)
        name = internal_path.replace("\\", "/").rsplit("/", 1)[-1] or f"replaceable_{replaceable_id}"
        images.append({"bufferView": view, "mimeType": "image/png", "name": name})
        samplers.append({"magFilter": 9729, "minFilter": 9987,
                         "wrapS": WRAP_CLAMP if wrap_mode & 1 else WRAP_REPEAT,
                         "wrapT": WRAP_CLAMP if wrap_mode & 2 else WRAP_REPEAT})
        textures.append({"source": len(images) - 1, "sampler": len(samplers) - 1})
        texture_cache[key] = len(textures) - 1
        stats["textures"] += 1
        return texture_cache[key]

    material_map: dict[int, int] = {}
    for index, material in enumerate(model.materials):
        layer = pick_layer(material, model)
        pbr = {"baseColorFactor": [1.0, 1.0, 1.0, 1.0], "metallicFactor": 0.0, "roughnessFactor": 1.0}
        entry = {"name": f"mat{index}", "doubleSided": bool(material.flags & 0x2),
                 "pbrMetallicRoughness": pbr}
        if layer is not None:
            alpha = max(0.0, min(1.0, float(layer.alpha)))
            pbr["baseColorFactor"][3] = alpha
            if layer.filter_mode == 1:          # WC3 "Transparent": 1-bit alpha test
                entry["alphaMode"] = "MASK"
                entry["alphaCutoff"] = 0.75
            elif layer.filter_mode in (2, 4, 5, 6) or alpha < 1.0:
                entry["alphaMode"] = "BLEND"
            if 0 <= layer.texture_id < len(model.textures):
                texture = model.textures[layer.texture_id]
                index_ = texture_index(texture.path, texture.replaceable_id, texture.wrap_mode)
                if index_ is not None:
                    pbr["baseColorTexture"] = {"index": index_}
        material_map[index] = len(materials)
        materials.append(entry)

    # --- meshes ------------------------------------------------------------
    meshes: list[dict] = []
    mesh_nodes: list[int] = []

    for geoset_index, geoset in enumerate(model.geosets):
        if geoset_index in hidden or not geoset.faces or not geoset.vertices:
            continue
        if len(geoset.faces) % 3:
            continue

        material = model.materials[geoset.material_id] if 0 <= geoset.material_id < len(model.materials) else None
        layer = pick_layer(material, model) if material else None
        coord = layer.coord_id if layer is not None else 0
        if 0 <= coord < len(geoset.uv_sets):
            uv = geoset.uv_sets[coord]
        elif geoset.uv_sets:
            uv = geoset.uv_sets[0]
        else:
            uv = None

        positions = np.asarray(geoset.vertices, dtype=np.float32).reshape(-1, 3)
        normals = (np.asarray(geoset.normals, dtype=np.float32).reshape(-1, 3)
                   if geoset.normals else np.zeros_like(positions))

        starts = []
        offset = 0
        for size in geoset.matrix_groups:
            starts.append((offset, size))
            offset += size

        joints = np.zeros((len(positions), 4), dtype=np.uint16)
        weights = np.zeros((len(positions), 4), dtype=np.float32)
        for vertex, group in enumerate(geoset.vertex_groups):
            if group >= len(starts):
                continue
            start, size = starts[group]
            bones = [b for b in geoset.matrix_indices[start:start + size] if b in mdx_to_gltf][:4]
            if not bones:
                continue
            weight = 1.0 / len(bones)
            for slot, bone in enumerate(bones):
                joints[vertex, slot] = mdx_to_gltf[bone]
                weights[vertex, slot] = weight

        attributes = {
            "POSITION": build.accessor(positions.reshape(-1), "VEC3", FLOAT,
                                       minmax=True, target=ARRAY_BUFFER),
            "NORMAL": build.accessor(normals.reshape(-1), "VEC3", FLOAT, target=ARRAY_BUFFER),
            "JOINTS_0": build.accessor(joints.reshape(-1), "VEC4", UNSIGNED_SHORT, target=ARRAY_BUFFER),
            "WEIGHTS_0": build.accessor(weights.reshape(-1), "VEC4", FLOAT, target=ARRAY_BUFFER),
        }
        if uv:
            uvs = np.asarray(uv, dtype=np.float32).reshape(-1, 2).copy()
            uvs[:, 1] = 1.0 - uvs[:, 1]        # glTF/OpenGL texture origin
            attributes["TEXCOORD_0"] = build.accessor(uvs.reshape(-1), "VEC2", FLOAT,
                                                      target=ARRAY_BUFFER)

        indices = build.accessor(np.asarray(geoset.faces, dtype=np.uint32).reshape(-1),
                                 "SCALAR", UNSIGNED_INT, target=ELEMENT_ARRAY_BUFFER)

        primitive = {"attributes": attributes, "indices": indices, "mode": 4}
        if geoset.material_id in material_map:
            primitive["material"] = material_map[geoset.material_id]
        meshes.append({"name": f"geoset_{geoset_index}", "primitives": [primitive]})

        nodes.append({"name": f"geoset_{geoset_index}", "mesh": len(meshes) - 1})
        mesh_nodes.append(len(nodes) - 1)

        stats["meshes"] += 1
        stats["primitives"] += 1
        stats["triangles"] += geoset.triangle_count
        stats["vertices"] += len(positions)

    # --- skin --------------------------------------------------------------
    skin = None
    if model.nodes and mesh_nodes:
        joint_nodes = [mdx_to_gltf[n.object_id] for n in model.nodes]
        identity = np.tile(np.eye(4, dtype=np.float32).reshape(-1), (len(joint_nodes), 1))
        skin = {
            "name": f"{model.name or 'model'}_skin",
            "joints": joint_nodes,
            "inverseBindMatrices": build.accessor(identity.reshape(-1), "MAT4", FLOAT),
        }
        if root_nodes:
            skin["skeleton"] = root_nodes[0]
        for node_index in mesh_nodes:
            nodes[node_index]["skin"] = 0
        stats["skins"] = 1

    # --- animations --------------------------------------------------------
    animations: list[dict] = []
    wanted = [s.strip().lower() for s in sequences if s.strip()] if sequences else None

    for sequence in model.sequences:
        name = sequence["name"]
        if wanted and not any(name.strip().lower() == w or name.strip().lower().startswith(w)
                              for w in wanted):
            continue
        start, end = float(sequence["start"]), float(sequence["end"])
        if end <= start:
            continue

        channels, animation_samplers = [], []
        for node in model.nodes:
            if not node.tracks:
                continue
            times = _sample_times(node, start, end, bake_fps)
            if len(times) < 2:
                continue

            pivot = np.asarray(node.pivot, dtype=np.float64)
            translations, rotations, scales = [], [], []
            for moment in times:
                local_time = moment - start
                translation = np.asarray(_track_value(node.tracks.get("KGTR"), moment,
                                                      DEFAULT_TRANSLATION, local_time,
                                                      model.global_sequences), dtype=np.float64)
                rotation = np.asarray(_track_value(node.tracks.get("KGRT"), moment,
                                                   DEFAULT_ROTATION, local_time,
                                                   model.global_sequences, quaternion=True),
                                      dtype=np.float64)
                scaling = np.asarray(_track_value(node.tracks.get("KGSC"), moment,
                                                  DEFAULT_SCALING, local_time,
                                                  model.global_sequences), dtype=np.float64)
                a = quat_to_matrix(rotation) @ np.diag(scaling)
                translations.append(pivot + translation - a @ pivot)
                rotations.append(rotation)
                scales.append(scaling)

            seconds = np.asarray([(t - start) / 1000.0 for t in times], dtype=np.float32)
            inputs = build.accessor(seconds, "SCALAR", FLOAT, minmax=True)

            step = all(node.tracks.get(tag, {}).get("interpolation", 1) == 0
                       for tag in ("KGTR", "KGRT", "KGSC") if tag in node.tracks)
            interpolation = "STEP" if step else "LINEAR"

            # A glTF node's TRS is not the MDX local matrix: the pivot has to be
            # carried in the translation as p + t - A.p, and A changes whenever the
            # node rotates or scales, so every animated node needs a translation
            # channel even if its own KGTR track is absent.
            for tag, path_name, values, type_name in (
                    (None, "translation", translations, "VEC3"),
                    ("KGRT", "rotation", rotations, "VEC4"),
                    ("KGSC", "scale", scales, "VEC3")):
                if tag is not None and not node.tracks.get(tag):
                    continue
                if path_name == "rotation":
                    values = _unflip_quaternions(values)
                output = build.accessor(np.asarray(values, dtype=np.float32).reshape(-1),
                                        type_name, FLOAT)
                animation_samplers.append({"input": inputs, "output": output,
                                           "interpolation": interpolation})
                channels.append({"sampler": len(animation_samplers) - 1,
                                 "target": {"node": mdx_to_gltf[node.object_id], "path": path_name}})

        if channels:
            animations.append({"name": name, "channels": channels, "samplers": animation_samplers})
            stats["animations"] += 1

    # --- assemble ----------------------------------------------------------
    gltf = {
        "asset": {"version": "2.0", "generator": "wc3studio"},
        "scene": 0,
        "scenes": [{"name": model.name or "model", "nodes": root_nodes + mesh_nodes}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": build.accessors,
        "bufferViews": build.views,
        "buffers": [{"byteLength": len(build.bin)}],
    }
    if textures:
        gltf["textures"] = textures
        gltf["images"] = images
        gltf["samplers"] = samplers
    if skin is not None:
        gltf["skins"] = [skin]
    if animations:
        gltf["animations"] = animations

    stats["nodes"] = len(nodes)
    stats["missing_textures"] = sorted(set(stats["missing_textures"]))
    stats["bytes"] = _write_glb(path, gltf, build.bin)
    stats["path"] = str(path)
    return stats


def _write_glb(path: Path, gltf: dict, binary: bytearray) -> int:
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "
    binary = bytearray(binary)
    while len(binary) % 4:
        binary.append(0)

    total = 12 + 8 + len(json_bytes) + (8 + len(binary) if binary else 0)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", GLB_MAGIC, 2, total))
        fh.write(struct.pack("<II", len(json_bytes), CHUNK_JSON))
        fh.write(json_bytes)
        if binary:
            fh.write(struct.pack("<II", len(binary), CHUNK_BIN))
            fh.write(bytes(binary))
    return total


# ---------------------------------------------------------------------------
# Reader: used by the tests to verify what we wrote, and by `wc3studio glb-info`
# ---------------------------------------------------------------------------

class Glb:
    """Minimal glTF reader: JSON, BIN, accessors, animation sampling, skinning."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        magic, version, length = struct.unpack_from("<III", raw, 0)
        if magic != GLB_MAGIC:
            raise ValueError("not a GLB file")
        if version != 2:
            raise ValueError(f"unsupported glTF version {version}")
        if length != len(raw):
            raise ValueError(f"declared length {length} != file size {len(raw)}")

        self.json: dict = {}
        self.bin = b""
        offset = 12
        while offset < len(raw):
            chunk_length, chunk_type = struct.unpack_from("<II", raw, offset)
            payload = raw[offset + 8: offset + 8 + chunk_length]
            if chunk_type == CHUNK_JSON:
                self.json = json.loads(payload.decode("utf-8"))
            elif chunk_type == CHUNK_BIN:
                self.bin = payload
            offset += 8 + chunk_length
        if not self.json:
            raise ValueError("GLB has no JSON chunk")
        self.gltf = self.json
        self._accessor_cache: dict[int, np.ndarray] = {}

    # -- accessors --------------------------------------------------------
    def accessor(self, index: int) -> np.ndarray:
        if index in self._accessor_cache:
            return self._accessor_cache[index]
        accessor = self.gltf["accessors"][index]
        view = self.gltf["bufferViews"][accessor["bufferView"]]
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        count = accessor["count"]
        components = TYPE_COUNT[accessor["type"]]
        dtype = np.dtype(DTYPE[accessor["componentType"]])
        array = np.frombuffer(self.bin, dtype=dtype, count=count * components, offset=offset)
        array = array.reshape(count, components) if components > 1 else array
        self._accessor_cache[index] = array
        return array

    # -- scene ------------------------------------------------------------
    def node_matrix(self, index: int) -> np.ndarray:
        node = self.gltf["nodes"][index]
        if "matrix" in node:
            return np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
        translation = node.get("translation", [0.0, 0.0, 0.0])
        rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
        scale = node.get("scale", [1.0, 1.0, 1.0])
        matrix = np.eye(4)
        matrix[:3, :3] = quat_to_matrix(rotation) @ np.diag(scale)
        matrix[:3, 3] = translation
        return matrix

    def world_matrices(self) -> dict:
        parents = {}
        for index, node in enumerate(self.gltf["nodes"]):
            for child in node.get("children", []):
                parents[child] = index
        cache: dict[int, np.ndarray] = {}

        def compute(index: int) -> np.ndarray:
            if index in cache:
                return cache[index]
            matrix = self.node_matrix(index)
            parent = parents.get(index)
            if parent is not None:
                matrix = compute(parent) @ matrix
            cache[index] = matrix
            return matrix

        for index in range(len(self.gltf["nodes"])):
            compute(index)
        return cache

    # -- animation --------------------------------------------------------
    def animation_times(self, index: int = 0) -> tuple:
        animation = self.gltf["animations"][index]
        low, high = None, None
        for sampler in animation["samplers"]:
            times = self.accessor(sampler["input"]).astype(np.float64)
            low = times.min() if low is None else min(low, times.min())
            high = times.max() if high is None else max(high, times.max())
        return float(low or 0.0), float(high or 0.0)

    def sample(self, animation_index: int, time: float) -> dict:
        """Channel targets at ``time``: {node_index: {"translation"/"rotation"/"scale": value}}."""
        animation = self.gltf["animations"][animation_index]
        out: dict[int, dict] = {}
        for channel in animation["channels"]:
            sampler = animation["samplers"][channel["sampler"]]
            times = self.accessor(sampler["input"]).astype(np.float64)
            values = self.accessor(sampler["output"]).astype(np.float64)
            interpolation = sampler.get("interpolation", "LINEAR")
            if time <= times[0]:
                value = values[0]
            elif time >= times[-1]:
                value = values[-1]
            else:
                i = int(np.searchsorted(times, time, side="right") - 1)
                span = times[i + 1] - times[i]
                t = 0.0 if span <= 0 else (time - times[i]) / span
                if interpolation == "STEP":
                    value = values[i]
                elif channel["target"]["path"] == "rotation":
                    from .anim import _slerp
                    value = np.asarray(_slerp(values[i], values[i + 1], t))
                else:
                    value = values[i] + (values[i + 1] - values[i]) * t
            out.setdefault(channel["target"]["node"], {})[channel["target"]["path"]] = value
        return out

    def posed_world_matrices(self, animation_index: int, time: float) -> dict:
        overrides = self.sample(animation_index, time)
        parents = {}
        for index, node in enumerate(self.gltf["nodes"]):
            for child in node.get("children", []):
                parents[child] = index
        cache: dict[int, np.ndarray] = {}

        def compute(index: int) -> np.ndarray:
            if index in cache:
                return cache[index]
            node = self.gltf["nodes"][index]
            translation = node.get("translation", [0.0, 0.0, 0.0])
            rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
            scale = node.get("scale", [1.0, 1.0, 1.0])
            applied = overrides.get(index, {})
            if "translation" in applied:
                translation = applied["translation"]
            if "rotation" in applied:
                rotation = applied["rotation"]
            if "scale" in applied:
                scale = applied["scale"]
            matrix = np.eye(4)
            matrix[:3, :3] = quat_to_matrix(rotation) @ np.diag(scale)
            matrix[:3, 3] = translation
            parent = parents.get(index)
            if parent is not None:
                matrix = compute(parent) @ matrix
            cache[index] = matrix
            return matrix

        for index in range(len(self.gltf["nodes"])):
            compute(index)
        return cache

    def skinned_positions(self, mesh_node_index: int, world: dict) -> np.ndarray:
        node = self.gltf["nodes"][mesh_node_index]
        primitive = self.gltf["meshes"][node["mesh"]]["primitives"][0]
        attributes = primitive["attributes"]
        positions = self.accessor(attributes["POSITION"]).astype(np.float64)
        joints = self.accessor(attributes["JOINTS_0"]).astype(np.int32)
        weights = self.accessor(attributes["WEIGHTS_0"]).astype(np.float64)
        skin = self.gltf["skins"][node["skin"]]
        joint_nodes = skin["joints"]
        ibm = self.accessor(skin["inverseBindMatrices"]).astype(np.float64).reshape(-1, 4, 4)

        out = np.zeros_like(positions)
        for slot in range(joints.shape[1]):
            weight = weights[:, slot]
            for vertex in np.nonzero(weight)[0]:
                joint = joint_nodes[joints[vertex, slot]]
                matrix = world[joint] @ ibm[joints[vertex, slot]]
                out[vertex] += weight[vertex] * (matrix[:3, :3] @ positions[vertex] + matrix[:3, 3])
        return out


def read_glb(path: str | Path) -> Glb:
    return Glb(path)
