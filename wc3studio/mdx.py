"""Minimal, dependency-free Warcraft III MDX (binary model) parser.

Format reference: the chunk layout follows the MDX specification as implemented
by the mdx-m3-viewer project (parsers/mdlx/*). Only the static geometry parts
needed for conversion are kept: version, textures, materials, geosets.
Unknown/skipped chunks are jumped over using their declared size.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


class MdxError(Exception):
    pass


class Reader:
    __slots__ = ("b", "i")

    def __init__(self, b: bytes):
        self.b = b
        self.i = 0

    @property
    def remaining(self) -> int:
        return len(self.b) - self.i

    def need(self, n: int):
        if self.i + n > len(self.b):
            raise MdxError(f"unexpected end of data at {self.i} (need {n})")

    def skip(self, n: int):
        self.need(n)
        self.i += n

    def u8n(self, n):
        self.need(n)
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.b, self.i)[0]
        self.i += 4
        return v

    def i32(self):
        self.need(4)
        v = struct.unpack_from("<i", self.b, self.i)[0]
        self.i += 4
        return v

    def f32(self):
        self.need(4)
        v = struct.unpack_from("<f", self.b, self.i)[0]
        self.i += 4
        return v

    def floats(self, n):
        self.need(4 * n)
        v = struct.unpack_from(f"<{n}f", self.b, self.i)
        self.i += 4 * n
        return v

    def u32s(self, n):
        self.need(4 * n)
        v = struct.unpack_from(f"<{n}I", self.b, self.i)
        self.i += 4 * n
        return v

    def u16s(self, n):
        self.need(2 * n)
        v = struct.unpack_from(f"<{n}H", self.b, self.i)
        self.i += 2 * n
        return v

    def cstr(self, n):
        raw = self.u8n(n)
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")

    def tag(self):
        self.need(4)
        t = self.b[self.i:self.i + 4]
        self.i += 4
        return t.decode("latin-1")

    def peek_tag(self):
        self.need(4)
        return self.b[self.i:self.i + 4].decode("latin-1")


def _expect(r: Reader, tag: str):
    got = r.tag()
    if got != tag:
        raise MdxError(f"expected tag {tag!r}, got {got!r} at offset {r.i - 4}")


@dataclass
class Texture:
    replaceable_id: int = 0
    path: str = ""
    wrap_mode: int = 0


@dataclass
class Layer:
    filter_mode: int = 0
    flags: int = 0
    texture_id: int = -1
    texture_anim_id: int = -1
    coord_id: int = 0
    alpha: float = 1.0


@dataclass
class Material:
    priority_plane: int = 0
    flags: int = 0
    shader: str = ""
    layers: list = field(default_factory=list)


@dataclass
class Geoset:
    vertices: tuple = ()
    normals: tuple = ()
    face_type_groups: tuple = ()
    face_groups: tuple = ()
    faces: tuple = ()
    vertex_groups: bytes = b""
    matrix_groups: tuple = ()
    matrix_indices: tuple = ()
    material_id: int = 0
    selection_group: int = 0
    selection_flags: int = 0
    uv_sets: list = field(default_factory=list)
    min_extent: tuple = (0.0, 0.0, 0.0)
    max_extent: tuple = (0.0, 0.0, 0.0)
    bounds_radius: float = 0.0
    lod: int = -1
    lod_name: str = ""

    @property
    def vertex_count(self) -> int:
        return len(self.vertices) // 3

    @property
    def triangle_count(self) -> int:
        return len(self.faces) // 3


@dataclass
class GeosetAnimation:
    alpha: float = 1.0
    flags: int = 0
    color: tuple = (1.0, 1.0, 1.0)
    geoset_id: int = -1
    # animated alpha track (KGAO) as a list of (frame, value); empty when static
    alpha_keys: list = field(default_factory=list)

    def alpha_at(self, frame: float) -> float:
        """Alpha at a given frame.

        Before the first keyframe the static alpha applies; between keyframes the
        value is stepped/linearly interpolated (the sampled tracks in these models
        use interpolation type 0, i.e. step).
        """
        if not self.alpha_keys:
            return self.alpha
        if frame < self.alpha_keys[0][0]:
            return self.alpha
        value = self.alpha_keys[0][1][0]
        for f, v in self.alpha_keys:
            if f <= frame:
                value = v[0]
            else:
                break
        return min(self.alpha, value)

    @property
    def max_alpha(self) -> float:
        """Highest alpha this track ever reaches (0 means permanently invisible)."""
        if not self.alpha_keys:
            return self.alpha
        return max(self.alpha, max(v[0] for _, v in self.alpha_keys))


@dataclass
class Model:
    version: int = 800
    name: str = ""
    textures: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    geosets: list = field(default_factory=list)
    geoset_animations: list = field(default_factory=list)
    sequences: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    pivots: list = field(default_factory=list)
    bones: list = field(default_factory=list)
    helpers: list = field(default_factory=list)
    global_sequences: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def node_by_id(self, object_id: int):
        """Look up a node in the model-wide node array (bones first, then helpers)."""
        if 0 <= object_id < len(self.nodes):
            return self.nodes[object_id]
        return None

    def sequence(self, name: str):
        wanted = name.strip().lower()
        for seq in self.sequences:
            if seq["name"].strip().lower() == wanted:
                return seq
        for seq in self.sequences:
            if seq["name"].strip().lower().startswith(wanted):
                return seq
        return None

    @property
    def triangle_count(self) -> int:
        return sum(g.triangle_count for g in self.geosets)

    @property
    def visible_geosets(self) -> set:
        return set(range(len(self.geosets))) - self.hidden_geosets

    @property
    def hidden_geosets(self) -> set:
        """Geosets that are invisible at every point on the animation timeline.

        WC3 uses geoset alpha tracks to hide gore/decay parts, spell effects and
        half-built states. Anything that shows up at some point is kept, so a static
        export contains the model's full geometry; only tracks that never leave zero
        are dropped (e.g. a unit's gore geoset).
        """
        by_geoset: dict[int, list] = {}
        for ga in self.geoset_animations:
            if ga.geoset_id >= 0:
                by_geoset.setdefault(ga.geoset_id, []).append(ga)
        hidden = set()
        for index, anims in by_geoset.items():
            if index >= len(self.geosets):
                continue
            if max(a.max_alpha for a in anims) <= 0.001:
                hidden.add(index)
        return hidden

    @property
    def first_frame(self) -> float:
        if self.sequences:
            return min(s["start"] for s in self.sequences)
        return 0.0

    def visible_at(self, frame: float) -> set:
        by_geoset: dict[int, list] = {}
        for ga in self.geoset_animations:
            if ga.geoset_id >= 0:
                by_geoset.setdefault(ga.geoset_id, []).append(ga)
        out = set()
        for i in range(len(self.geosets)):
            anims = by_geoset.get(i)
            if not anims or min(a.alpha_at(frame) for a in anims) > 0.001:
                out.add(i)
        return out

    @property
    def pose_hidden_geosets(self) -> set:
        """Geosets to drop for the model's representative (standing) appearance.

        Geoset alpha tracks are keyed to sequence starts, so the value at the first
        sequence's start frame is the model's normal look: it drops gore splats and
        spell-effect parts for units, and for multi-level buildings it selects the
        base level instead of stacking every upgrade on top of itself.

        A few models have a degenerate first pose (their timeline starts mid-build);
        there the full set of ever-visible geosets is kept so nothing real is lost.
        """
        hidden = set(self.hidden_geosets)
        count = len(self.geosets)
        everything = set(range(count)) - hidden
        if not self.geoset_animations or not self.sequences:
            return hidden

        pose = self.visible_at(self.first_frame) & everything
        total_tris = sum(self.geosets[i].triangle_count for i in everything)
        pose_tris = sum(self.geosets[i].triangle_count for i in pose)
        if pose and total_tris and pose_tris / total_tris >= POSE_MIN_RATIO:
            return set(range(count)) - pose
        return hidden

    @property
    def sequence_ranges(self) -> list:
        return [(s["start"], s["end"]) for s in self.sequences]


def _read_textures(r: Reader, size: int) -> list:
    if size % 268:
        raise MdxError(f"TEXS size {size} is not a multiple of 268")
    out = []
    for _ in range(size // 268):
        t = Texture()
        t.replaceable_id = r.u32()
        t.path = r.cstr(260)
        t.wrap_mode = r.u32()
        out.append(t)
    return out


def _read_layer(r: Reader, version: int) -> Layer:
    start = r.i
    size = r.u32()
    if size < 28:
        raise MdxError(f"layer size {size} too small")
    end = start + size
    lay = Layer()
    lay.filter_mode = r.u32()
    lay.flags = r.u32()
    lay.texture_id = r.i32()
    lay.texture_anim_id = r.i32()
    lay.coord_id = r.u32()
    lay.alpha = r.f32()
    if version > 800:
        r.skip(24)
    if r.i > end:
        raise MdxError("layer overran its declared size")
    r.i = end
    return lay


def _read_materials(r: Reader, size: int, version: int) -> list:
    end = r.i + size
    out = []
    while r.i < end:
        start = r.i
        msize = r.u32()
        if msize < 12:
            raise MdxError(f"material size {msize} too small")
        mend = start + msize
        m = Material()
        m.priority_plane = r.i32()
        m.flags = r.u32()
        if version > 800:
            m.shader = r.cstr(80)
        _expect(r, "LAYS")
        for _ in range(r.u32()):
            m.layers.append(_read_layer(r, version))
        if r.i > mend:
            raise MdxError("material overran its declared size")
        r.i = mend
        out.append(m)
    if r.i != end:
        raise MdxError("material chunk misaligned")
    return out


def _read_geoset(r: Reader, version: int) -> Geoset:
    start = r.i
    size = r.u32()
    end = start + size
    g = Geoset()

    _expect(r, "VRTX")
    g.vertices = r.floats(r.u32() * 3)
    _expect(r, "NRMS")
    g.normals = r.floats(r.u32() * 3)
    _expect(r, "PTYP")
    g.face_type_groups = r.u32s(r.u32())
    _expect(r, "PCNT")
    g.face_groups = r.u32s(r.u32())
    _expect(r, "PVTX")
    g.faces = r.u16s(r.u32())
    _expect(r, "GNDX")
    g.vertex_groups = bytes(r.u8n(r.u32()))
    _expect(r, "MTGC")
    g.matrix_groups = r.u32s(r.u32())
    _expect(r, "MATS")
    g.matrix_indices = r.u32s(r.u32())

    g.material_id = r.u32()
    g.selection_group = r.u32()
    g.selection_flags = r.u32()

    if version > 800:
        g.lod = r.i32()
        g.lod_name = r.cstr(80)

    g.min_extent = r.floats(3)
    g.max_extent = r.floats(3)
    g.bounds_radius = r.f32()

    for _ in range(r.u32()):
        r.skip(28)

    if version > 800:
        if r.peek_tag() == "TANG":
            r.skip(4)
            r.skip(r.u32() * 16)
        if r.peek_tag() == "SKIN":
            r.skip(4)
            r.skip(r.u32())

    _expect(r, "UVAS")
    for _ in range(r.u32()):
        _expect(r, "UVBS")
        g.uv_sets.append(r.floats(r.u32() * 2))

    if r.i != end:
        raise MdxError(f"geoset ended at {r.i}, declared {end} (delta {end - r.i})")
    return g


def _read_geosets(r: Reader, size: int, version: int) -> list:
    end = r.i + size
    out = []
    while r.i < end:
        out.append(_read_geoset(r, version))
    if r.i != end:
        raise MdxError("geoset chunk misaligned")
    return out


# MDX animation tracks: tag -> number of floats per keyframe.
# Node tracks (KGTR/KGRT/KGSC) drive the skeleton, geoset tracks the alpha/colour.
_TRACK_SIZE = {
    "KGTR": 3, "KGRT": 4, "KGSC": 3,   # node translation / rotation (quaternion) / scaling
    "KGAO": 1, "KGAC": 3,              # geoset animation alpha / colour
    "KMTF": 1, "KMTA": 1, "KMTE": 1,   # material layer texture-id / alpha / emissive
    "KTAT": 3, "KTAR": 4, "KTAS": 3,   # texture animation
}

# A model's opening pose must show at least this share of its geometry to be treated
# as representative; below it the model starts mid-build and the full set is kept.
POSE_MIN_RATIO = 0.05


def _read_animations(r: Reader, size: int) -> dict:
    """Read the animation blocks occupying the next ``size`` bytes.

    MDX animation block layout:
        char[4] tag
        u32     key count
        u32     interpolation type (0 none, 1 linear, 2 hermite, 3 bezier)
        i32     global sequence id
        keys:   u32 frame, float[track_size] value [, in-tangent, out-tangent]

    Returns {tag: {"interpolation", "global_seq", "keys": [(frame, values)]}}, keeping
    the first block seen for each tag. The reader always ends up at the block end.
    """
    end = r.i + size
    out: dict[str, dict] = {}
    while r.i + 8 <= end:
        tag = r.peek_tag()
        if tag not in _TRACK_SIZE:
            break
        r.skip(4)
        track_size = _TRACK_SIZE[tag]
        keys = []
        interpolation = 0
        global_seq = -1
        try:
            key_count = r.u32()
            interpolation = r.u32()
            global_seq = r.i32()
            for _ in range(key_count):
                frame = r.u32()
                value = r.floats(track_size)
                keys.append((frame, value))
                if interpolation > 1:
                    r.skip(8 * track_size)  # in/out tangents
        except MdxError:
            break
        out.setdefault(tag, {"interpolation": interpolation,
                             "global_seq": global_seq, "keys": keys})
    r.i = min(end, len(r.b))
    return out


def _read_sequences(r: Reader, size: int) -> list:
    if size % 132:
        raise MdxError(f"SEQS size {size} is not a multiple of 132")
    out = []
    for _ in range(size // 132):
        seq = {"name": r.cstr(80)}
        seq["start"] = r.u32()
        seq["end"] = r.u32()
        seq["move_speed"] = r.f32()
        seq["flags"] = r.u32()
        seq["rarity"] = r.f32()
        seq["sync_point"] = r.u32()
        seq["extent"] = r.floats(7)
        out.append(seq)
    return out


@dataclass
class Node:
    """A skeleton node: bone or helper.

    ``object_id`` indexes the model-wide node array, ``parent_id`` points at another
    node in it (-1 for a root).
    """
    name: str = ""
    kind: str = "bone"
    object_id: int = -1
    parent_id: int = -1
    flags: int = 0
    geoset_id: int = -1
    geoset_anim_id: int = -1
    tracks: dict = field(default_factory=dict)
    pivot: tuple = (0.0, 0.0, 0.0)


def _read_nodes(r: Reader, size: int, kind: str) -> list:
    """Read a BONE or HELP chunk.

    The object's declared size covers the name/ids/flags and the node's own KGTR,
    KGRT and KGSC tracks; a bone then carries geosetId and geosetAnimId after them.
    """
    end = r.i + size
    out = []
    while r.i < end:
        start = r.i
        nsize = r.u32()
        node = Node(kind=kind)
        node.name = r.cstr(80)
        node.object_id = r.i32()
        node.parent_id = r.i32()
        node.flags = r.u32()
        node.tracks = _read_animations(r, max(nsize - 96, 0))
        if kind == "bone":
            node.geoset_id = r.i32()
            node.geoset_anim_id = r.i32()
            r.i = start + nsize + 8
        else:
            r.i = start + nsize
        out.append(node)
    if r.i != end:
        raise MdxError(f"{kind.upper()} chunk misaligned: at {r.i}, expected {end}")
    return out


def _read_pivots(r: Reader, size: int) -> list:
    if size % 12:
        raise MdxError(f"PIVT size {size} is not a multiple of 12")
    return [r.floats(3) for _ in range(size // 12)]


def _read_geoset_animations(r: Reader, size: int) -> list:
    end = r.i + size
    out = []
    while r.i < end:
        start = r.i
        gsize = r.u32()
        if gsize < 28:
            raise MdxError(f"geoset animation size {gsize} too small")
        ga = GeosetAnimation()
        ga.alpha = r.f32()
        ga.flags = r.u32()
        ga.color = r.floats(3)
        ga.geoset_id = r.i32()
        anims = _read_animations(r, start + gsize - r.i)
        ga.alpha_keys = anims.get("KGAO", {}).get("keys", [])
        r.i = start + gsize
        out.append(ga)
    if r.i != end:
        raise MdxError("geoset animation chunk misaligned")
    return out


def parse(data: bytes) -> Model:
    if data[:4] != b"MDLX":
        raise MdxError("not an MDX file (missing MDLX magic)")
    r = Reader(data)
    r.i = 4
    m = Model()
    version = 800
    while r.remaining >= 8:
        tag = r.tag()
        size = r.u32()
        end = r.i + size
        if end > len(data):
            raise MdxError(f"chunk {tag} size {size} runs past end of file")
        if tag == "VERS":
            version = r.u32()
            m.version = version
        elif tag == "MODL":
            m.name = r.cstr(80)
            r.skip(260)
            r.skip(28)
            r.skip(4)
        elif tag == "SEQS":
            m.sequences = _read_sequences(r, size)
        elif tag == "TEXS":
            m.textures = _read_textures(r, size)
        elif tag == "MTLS":
            m.materials = _read_materials(r, size, version)
        elif tag == "GEOS":
            m.geosets = _read_geosets(r, size, version)
        elif tag == "GEOA":
            m.geoset_animations = _read_geoset_animations(r, size)
        elif tag == "BONE":
            m.bones = _read_nodes(r, size, "bone")
        elif tag == "HELP":
            m.helpers = _read_nodes(r, size, "helper")
        elif tag == "PIVT":
            m.pivots = _read_pivots(r, size)
        elif tag == "GLBS":
            m.global_sequences = list(r.u32s(size // 4))
        r.i = end

    # Bones come first in the model-wide node array, helpers after them; geoset
    # matrix groups and node parent ids both index into this array.
    m.nodes = m.bones + m.helpers
    for node in m.nodes:
        if 0 <= node.object_id < len(m.pivots):
            node.pivot = m.pivots[node.object_id]
    return m
