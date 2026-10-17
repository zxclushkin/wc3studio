"""Wavefront OBJ + MTL export (static geometry, textures as PNG)."""
from __future__ import annotations

import os
from pathlib import Path

from .mdx import Model
from .resources import Resources, pick_layer

__all__ = ["export_obj", "ObjStats"]


class ObjStats(dict):
    """Conversion statistics; a dict so it prints and serialises easily."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<ObjStats geosets={self.get('geosets')} triangles={self.get('triangles')} "
                f"vertices={self.get('vertices')} textures={self.get('textures')}>")


def export_obj(model: Model, obj_path: str | os.PathLike, resources: Resources | None = None,
               textures_root: str | os.PathLike | None = None,
               all_geosets: bool = False, pose: str = "stand") -> ObjStats:
    """Write ``model`` as OBJ + MTL + PNG textures.

    ``textures_root`` lets many models share one texture folder (MTL then references it
    through a relative path). ``pose`` is "stand" (the model's normal appearance) or
    "all" (every geoset that is ever visible).
    """
    obj_path = Path(obj_path)
    out_dir = obj_path.resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    if textures_root is None:
        textures_root = out_dir / "textures"
    textures_root = Path(textures_root)
    base = obj_path.stem
    mtl_path = out_dir / (base + ".mtl")

    if pose == "all":
        hidden = model.hidden_geosets
    else:
        hidden = model.pose_hidden_geosets

    stats = ObjStats(vertices=0, triangles=0, geosets=0, materials=0, textures=0,
                     missing_textures=[], skipped_geosets=0, hidden_geosets=sorted(hidden))

    # --- materials ---------------------------------------------------------
    mtl_lines = []
    for index, material in enumerate(model.materials):
        layer = pick_layer(material, model)
        texture_ref = None
        alpha = 1.0
        if layer is not None:
            alpha = float(layer.alpha)
            if 0 <= layer.texture_id < len(model.textures):
                texture = model.textures[layer.texture_id]
                if resources is not None:
                    png = resources.write_png(texture.path, textures_root, texture.replaceable_id)
                    if png is not None:
                        texture_ref = os.path.relpath(png, out_dir).replace("\\", "/")
                        stats["textures"] += 1
                    elif texture.path:
                        stats["missing_textures"].append(texture.path)

        mtl_lines += [
            f"newmtl mat{index}",
            "Kd 1.000 1.000 1.000",
            "Ka 0.200 0.200 0.200",
            "Ks 0.000 0.000 0.000",
            f"d {max(0.0, min(1.0, alpha)):.4f}",
        ]
        if texture_ref:
            mtl_lines.append(f"map_Kd {texture_ref}")
        if material.flags & 0x2:
            mtl_lines.append("illum 0")  # two-sided
        mtl_lines.append("")
        stats["materials"] += 1

    mtl_path.write_text(
        "# Converted from Warcraft III MDX by wc3studio\n"
        f"# source model: {model.name}\n\n" + "\n".join(mtl_lines), encoding="utf-8")

    # --- geometry ----------------------------------------------------------
    lines = [
        "# Converted from Warcraft III MDX by wc3studio",
        f"# source model: {model.name}",
        f"# mdx version: {model.version}",
        f"# hidden geosets (animation-controlled): {sorted(hidden)}",
        f"mtllib {base}.mtl",
        "",
    ]
    vertex_base = 0
    for geoset_index, geoset in enumerate(model.geosets):
        if geoset_index in hidden or not geoset.faces or not geoset.vertices:
            stats["skipped_geosets"] += 1
            continue
        if len(geoset.faces) % 3:
            stats["skipped_geosets"] += 1
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

        count = geoset.vertex_count
        lines.append(f"o geoset_{geoset_index}")
        lines.append(f"# material_id={geoset.material_id} vertices={count} "
                     f"triangles={geoset.triangle_count}")
        for i in range(count):
            x, y, z = geoset.vertices[i * 3:i * 3 + 3]
            lines.append(f"v {x:.5f} {y:.5f} {z:.5f}")
        if uv is not None:
            for i in range(count):
                u, v = uv[i * 2:i * 2 + 2]
                lines.append(f"vt {u:.5f} {1.0 - v:.5f}")
        if geoset.normals:
            for i in range(count):
                nx, ny, nz = geoset.normals[i * 3:i * 3 + 3]
                lines.append(f"vn {nx:.5f} {ny:.5f} {nz:.5f}")

        if 0 <= geoset.material_id < len(model.materials):
            lines.append(f"usemtl mat{geoset.material_id}")

        has_uv = uv is not None
        has_normal = bool(geoset.normals)
        for t in range(0, len(geoset.faces), 3):
            parts = []
            for k in range(3):
                index = geoset.faces[t + k] + 1 + vertex_base
                if has_uv and has_normal:
                    parts.append(f"{index}/{index}/{index}")
                elif has_uv:
                    parts.append(f"{index}/{index}")
                elif has_normal:
                    parts.append(f"{index}//{index}")
                else:
                    parts.append(str(index))
            lines.append("f " + " ".join(parts))
        lines.append("")

        vertex_base += count
        stats["vertices"] += count
        stats["triangles"] += geoset.triangle_count
        stats["geosets"] += 1

    obj_path.write_text("\n".join(lines), encoding="utf-8")
    stats["missing_textures"] = sorted(set(stats["missing_textures"]))
    stats["obj"] = str(obj_path)
    stats["mtl"] = str(mtl_path)
    return stats
