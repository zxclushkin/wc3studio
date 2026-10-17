"""Software renderer: z-buffered, textured, alpha-blended, with skinning support.

It is deliberately self-contained (numpy + Pillow only) so the tool can show a model
without pulling in a GPU stack or a game engine.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .mdx import Model
from .resources import Resources, pick_layer

__all__ = ["render", "fit_camera", "collect", "DEFAULT_BG"]

UNTEXTURED = np.array([0.62, 0.66, 0.72], dtype=np.float32)
GLOW_COLOR = np.array([0.35, 0.55, 1.0], dtype=np.float32)

FILTER_NONE = 0
FILTER_TRANSPARENT = 1
FILTER_BLEND = 2
FILTER_ADDITIVE = 3

DEFAULT_BG = (24, 26, 32)


def _load_texture(resources: Resources | None, model: Model, material):
    """Return (image array, is_replaceable_glow) for a material."""
    if material is None or resources is None:
        return None, False
    layer = pick_layer(material, model)
    if layer is None or not (0 <= layer.texture_id < len(model.textures)):
        return None, False
    texture = model.textures[layer.texture_id]
    image = resources.image(texture.path, texture.replaceable_id)
    glow = bool(texture.replaceable_id) and not texture.path
    if image is None:
        return None, glow
    return np.asarray(image, dtype=np.float32) / 255.0, glow


def collect(model: Model, resources: Resources | None, all_geosets: bool = False,
            skip_effects: bool = True, animator=None, frame: float | None = None,
            local_time: float | None = None, matrices: dict | None = None):
    """Turn a model into a list of draw items (positions, faces, uv, texture...)."""
    hidden = model.hidden_geosets if all_geosets else model.pose_hidden_geosets
    items = []
    texture_cache: dict[int, tuple] = {}

    for geoset_index, geoset in enumerate(model.geosets):
        if geoset_index in hidden:
            continue
        if not geoset.faces or not geoset.vertices or len(geoset.faces) % 3:
            continue

        material = model.materials[geoset.material_id] if 0 <= geoset.material_id < len(model.materials) else None
        layer = pick_layer(material, model)
        coord = layer.coord_id if layer is not None else 0
        if 0 <= coord < len(geoset.uv_sets):
            uv = geoset.uv_sets[coord]
        elif geoset.uv_sets:
            uv = geoset.uv_sets[0]
        else:
            uv = None

        filter_mode = layer.filter_mode if layer is not None else FILTER_NONE

        if material is not None:
            key = id(material)
            if key not in texture_cache:
                texture_cache[key] = _load_texture(resources, model, material)
            texture, replaceable_glow = texture_cache[key]
        else:
            texture, replaceable_glow = None, False

        if skip_effects and filter_mode == FILTER_ADDITIVE:
            # additive geosets are glow/splat planes: soft gradients in game, hard quads
            # without a real glow renderer, so leave them out of previews
            continue

        positions = np.asarray(geoset.vertices, dtype=np.float32).reshape(-1, 3)
        normals = np.asarray(geoset.normals, dtype=np.float32).reshape(-1, 3) if geoset.normals else None
        if animator is not None and frame is not None:
            if matrices is None:
                matrices = animator.node_matrices(frame, local_time)
            skinned = animator.skin(geoset, matrices)
            if skinned is not None:
                positions = skinned.astype(np.float32)
            if normals is not None:
                skinned_normals = animator.skin(geoset, matrices, normals=True)
                if skinned_normals is not None:
                    normals = skinned_normals.astype(np.float32)

        items.append({
            "pos": positions,
            "faces": np.asarray(geoset.faces, dtype=np.int32).reshape(-1, 3),
            "uv": np.asarray(uv, dtype=np.float32).reshape(-1, 2) if uv else None,
            "nrm": normals,
            "tex": texture,
            "tex_path": (model.textures[layer.texture_id].path
                         if layer is not None and 0 <= layer.texture_id < len(model.textures)
                         else ""),
            "replaceable_glow": replaceable_glow,
            "filter_mode": filter_mode,
            "solid": (texture is not None) or not replaceable_glow,
            "alpha": float(layer.alpha) if layer is not None else 1.0,
            "material_id": geoset.material_id,
            "geoset": geoset_index,
        })
    return items


def _look_at(eye, target, up=(0.0, 0.0, 1.0)):
    eye = np.asarray(eye, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    forward = target - eye
    forward /= np.linalg.norm(forward)
    up = np.asarray(up, dtype=np.float64)
    right = np.cross(forward, up)
    norm = np.linalg.norm(right)
    if norm < 1e-8:
        right = np.array([1.0, 0.0, 0.0])
    else:
        right /= norm
    up = np.cross(right, forward)
    return eye, np.stack([right, up, forward])


def look_at_matrix(eye, target, up=(0.0, 0.0, 1.0)) -> np.ndarray:
    """Right-handed view matrix: the camera looks down -Z, as OpenGL expects."""
    eye = np.asarray(eye, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    forward = target - eye
    forward /= np.linalg.norm(forward) or 1.0
    up = np.asarray(up, dtype=np.float64)
    right = np.cross(forward, up)
    if np.linalg.norm(right) < 1e-6:
        right = np.cross(forward, np.array([0.0, 1.0, 0.0]))
    right /= np.linalg.norm(right) or 1.0
    true_up = np.cross(right, forward)
    matrix = np.eye(4)
    matrix[0, :3] = right
    matrix[0, 3] = -right @ eye
    matrix[1, :3] = true_up
    matrix[1, 3] = -true_up @ eye
    matrix[2, :3] = -forward
    matrix[2, 3] = forward @ eye
    return matrix


def perspective_matrix(focal: float, aspect: float, near: float, far: float) -> np.ndarray:
    """OpenGL projection: near maps to -1 and far to +1 in clip space."""
    matrix = np.zeros((4, 4))
    matrix[0, 0] = focal / max(aspect, 1e-6)
    matrix[1, 1] = focal
    matrix[2, 2] = (far + near) / (near - far)
    matrix[2, 3] = 2.0 * far * near / (near - far)
    matrix[3, 2] = -1.0
    return matrix


def camera_for_bounds(low, high, margin: float = 1.14) -> dict:
    center = (np.asarray(low, dtype=np.float64) + np.asarray(high, dtype=np.float64)) / 2.0
    radius = float(np.linalg.norm(np.asarray(high) - np.asarray(low))) / 2.0
    return {"center": center, "radius": max(radius, 1e-3) * margin}


def fit_camera(model: Model, animator, frames, hidden=frozenset(), all_geosets: bool = False,
               margin: float = 1.14) -> dict:
    """Static camera that keeps the whole model in frame across ``frames``.

    ``frames`` is a sequence of (absolute frame, local time) pairs.
    """
    if not hidden and not all_geosets:
        hidden = model.pose_hidden_geosets
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    for frame, local_time in frames:
        matrices = animator.node_matrices(frame, local_time)
        for index, geoset in enumerate(model.geosets):
            if index in hidden:
                continue
            points = animator.skin(geoset, matrices)
            if points is None or not len(points):
                continue
            low = np.minimum(low, points.min(axis=0))
            high = np.maximum(high, points.max(axis=0))
    if not np.isfinite(low).all():
        items = collect(model, None, all_geosets=all_geosets)
        if items:
            points = np.concatenate([item["pos"] for item in items], axis=0)
            low, high = points.min(axis=0), points.max(axis=0)
        else:
            return {"center": np.zeros(3), "radius": 1.0}
    return camera_for_bounds(low, high, margin)


def _apply_contact_shadow(color, center, radius, ground_z, eye, basis, focal, width, height,
                          pitch_deg: float, strength: float = 0.55):
    point = np.array([center[0], center[1], ground_z], dtype=np.float64)
    cam = basis @ (point - eye)
    if cam[2] <= 1e-3:
        return
    gx = cam[0] * focal / cam[2] * (width * 0.5) + width * 0.5
    gy = height * 0.5 - cam[1] * focal / cam[2] * (height * 0.5)
    sx = radius * focal / cam[2] * (width * 0.5)
    sy = sx * max(0.16, abs(np.sin(np.radians(pitch_deg))))
    if not np.isfinite(gx) or sx < 1:
        return
    x0 = max(int(gx - sx * 1.6), 0)
    x1 = min(int(gx + sx * 1.6) + 1, width)
    y0 = max(int(gy - sy * 1.6), 0)
    y1 = min(int(gy + sy * 1.6) + 1, height)
    if x0 >= x1 or y0 >= y1:
        return
    ys, xs = np.mgrid[y0:y1, x0:x1]
    distance = ((xs - gx) / sx) ** 2 + ((ys - gy) / sy) ** 2
    mask = np.clip(1.0 - distance, 0.0, 1.0) ** 1.4
    color[y0:y1, x0:x1] *= (1.0 - strength * mask)[..., None]


def render(model: Model, resources: Resources | None = None, size: int = 800,
           yaw: float = 35.0, pitch: float = 15.0, fov: float = 32.0,
           bg=DEFAULT_BG, light=(-0.35, -0.55, 0.75), ambient: float = 0.42,
           zoom: float = 1.0, all_geosets: bool = False, skip_effects: bool = True,
           frame: float | None = None, local_time: float | None = None,
           camera: dict | None = None, animator=None, shadow: bool = True) -> Image.Image:
    """Render one frame of a model to a PIL image."""
    if animator is None and frame is not None and model.nodes:
        from .anim import Animator
        animator = Animator(model)
    matrices = (animator.node_matrices(frame, local_time)
                if animator is not None and frame is not None else None)
    items = collect(model, resources, all_geosets, skip_effects, animator, frame, local_time, matrices)
    if not items:
        return Image.new("RGB", (size, size), bg)

    framing = [item for item in items if item["solid"]] or items
    if camera is not None:
        center = np.asarray(camera["center"], dtype=np.float64)
        radius = float(camera["radius"])
    else:
        points = np.concatenate([item["pos"] for item in framing], axis=0)
        low, high = points.min(axis=0), points.max(axis=0)
        center = (low + high) / 2.0
        radius = float(np.linalg.norm(high - low)) / 2.0 or 1.0

    yaw_rad, pitch_rad = np.radians(yaw), np.radians(pitch)
    direction = np.array([np.sin(yaw_rad) * np.cos(pitch_rad),
                          -np.cos(yaw_rad) * np.cos(pitch_rad),
                          np.sin(pitch_rad)])
    distance = radius / np.tan(np.radians(fov) / 2.0) * 1.15 / zoom
    eye, basis = _look_at(center + direction * distance, center)

    width = height = size
    color = np.zeros((height, width, 3), dtype=np.float32)
    color[:] = np.asarray(bg, dtype=np.float32) / 255.0
    depth = np.full((height, width), -np.inf, dtype=np.float32)
    focal = 1.0 / np.tan(np.radians(fov) / 2.0)

    light_view = np.asarray(light, dtype=np.float64)
    light_view = light_view / np.linalg.norm(light_view)

    if shadow:
        ground_z = min(float(item["pos"][:, 2].min()) for item in framing)
        _apply_contact_shadow(color, center, radius, ground_z, eye, basis, focal,
                              width, height, pitch)

    ys, xs = np.mgrid[0:height, 0:width]

    for item in items:
        points, faces = item["pos"], item["faces"]
        cam = (points - eye) @ basis.T
        z = cam[:, 2]
        screen_x = (cam[:, 0] * focal / np.maximum(z, 1e-6)) * (width * 0.5) + width * 0.5
        screen_y = height * 0.5 - (cam[:, 1] * focal / np.maximum(z, 1e-6)) * (height * 0.5)
        inv_z = np.where(z > 1e-4, 1.0 / np.maximum(z, 1e-6), 0.0)

        uv, normals, texture = item["uv"], item["nrm"], item["tex"]
        alpha = float(np.clip(item["alpha"], 0.0, 1.0))
        if texture is not None:
            tex_h, tex_w = texture.shape[:2]

        for triangle in faces:
            if np.all(z[triangle] <= 1e-3):
                continue
            xt, yt = screen_x[triangle], screen_y[triangle]
            x0 = max(int(np.floor(xt.min())), 0)
            x1 = min(int(np.ceil(xt.max())) + 1, width)
            y0 = max(int(np.floor(yt.min())), 0)
            y1 = min(int(np.ceil(yt.max())) + 1, height)
            if x0 >= x1 or y0 >= y1:
                continue

            ax, ay = xt[0], yt[0]
            bx, by = xt[1], yt[1]
            cx, cy = xt[2], yt[2]
            area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if abs(area) < 1e-9:
                continue

            px = xs[y0:y1, x0:x1] + 0.5
            py = ys[y0:y1, x0:x1] + 0.5
            w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area
            w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue

            inv = inv_z[triangle]
            iz = w0 * inv[0] + w1 * inv[1] + w2 * inv[2]
            sub_depth = depth[y0:y1, x0:x1]
            closer = inside & (iz > sub_depth)
            if not closer.any():
                continue

            face_normal = np.cross(points[triangle[1]] - points[triangle[0]],
                                   points[triangle[2]] - points[triangle[0]])
            length = np.linalg.norm(face_normal)
            if length < 1e-12:
                shade = 1.0
            else:
                shade = ambient + (1.0 - ambient) * max(
                    0.0, float(np.dot((basis @ (face_normal / length)), light_view)))

            if texture is not None and uv is not None:
                u = (w0 * uv[triangle[0], 0] * inv[0] + w1 * uv[triangle[1], 0] * inv[1]
                     + w2 * uv[triangle[2], 0] * inv[2]) / np.maximum(iz, 1e-9)
                v = (w0 * uv[triangle[0], 1] * inv[0] + w1 * uv[triangle[1], 1] * inv[1]
                     + w2 * uv[triangle[2], 1] * inv[2]) / np.maximum(iz, 1e-9)
                tx = np.clip((u % 1.0) * tex_w, 0, tex_w - 1).astype(np.int32)
                ty = np.clip((v % 1.0) * tex_h, 0, tex_h - 1).astype(np.int32)
                rgba = texture[ty, tx]
                rgb = rgba[..., :3]
                triangle_alpha = rgba[..., 3] * alpha
            elif item["replaceable_glow"]:
                rgb = np.broadcast_to(GLOW_COLOR, (y1 - y0, x1 - x0, 3))
                triangle_alpha = np.full((y1 - y0, x1 - x0), alpha * 0.55, dtype=np.float32)
            else:
                rgb = np.broadcast_to(UNTEXTURED, (y1 - y0, x1 - x0, 3))
                triangle_alpha = np.full((y1 - y0, x1 - x0), alpha, dtype=np.float32)

            shaded = rgb * shade
            destination = color[y0:y1, x0:x1]
            blend = np.where(closer, triangle_alpha, 0.0)[..., None]
            if item["filter_mode"] == FILTER_ADDITIVE:
                destination[:] = np.clip(destination + shaded * blend, 0.0, 1.0)
            else:
                destination[:] = destination * (1.0 - blend) + shaded * blend
            sub_depth[closer] = iz[closer]

    return Image.fromarray((np.clip(color, 0, 1) * 255).astype(np.uint8), "RGB")
