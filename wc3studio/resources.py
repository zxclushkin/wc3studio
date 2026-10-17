"""Texture resolution: internal WC3 paths -> BLP -> Pillow images / PNG bytes.

Model files reference textures by internal path with inconsistent casing, and the
archives themselves are patched in layers, so every lookup goes through the game's
archive chain with a case-folded index.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image

from . import blp
from .stormlib import Game

__all__ = ["Resources", "pick_layer", "placeholder_texture", "REPLACEABLE_NAMES"]

# Textures that exist only as a "replaceable" id: team colour, glow, and friends.
REPLACEABLE_NAMES = {
    1: "team_color",
    2: "team_glow",
    11: "team_color_2",
    31: "team_color_3",
}
REPLACEABLE_RGB = {
    1: (60, 110, 220),
    2: (240, 200, 80),
    11: (60, 110, 220),
    31: (60, 110, 220),
}


def pick_layer(material, model=None):
    """Choose the layer that best represents a material as a single-texture map.

    Multi-layer WC3 materials usually stack a team-colour / glow layer over the real
    texture, so an image-backed layer wins over an unbacked one.
    """
    if material is None or not material.layers:
        return None
    if model is not None:
        for layer in material.layers:
            if 0 <= layer.texture_id < len(model.textures) and model.textures[layer.texture_id].path:
                return layer
        for layer in material.layers:
            if 0 <= layer.texture_id < len(model.textures) and model.textures[layer.texture_id].replaceable_id:
                return layer
    return material.layers[0]


def placeholder_texture(replaceable_id: int, size: int = 32) -> Image.Image:
    rgb = REPLACEABLE_RGB.get(replaceable_id, (170, 170, 170))
    return Image.new("RGBA", (size, size), rgb + (255,))


class Resources:
    """Reads anything a model needs out of the game archives."""

    def __init__(self, game: Game, cache_dir: str | os.PathLike | None = None):
        self.game = game
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._images: dict[str, Image.Image | None] = {}
        self._png: dict[str, bytes | None] = {}
        self.missing: set[str] = set()
        self.fallbacks: dict[str, str] = {}

    # -- raw --------------------------------------------------------------
    def read(self, internal_path: str) -> bytes | None:
        return self.game.read(internal_path)

    # -- textures ---------------------------------------------------------
    def _fallback_texture(self, internal_path: str):
        """Find a texture whose path was baked on the artist's machine.

        A few models reference absolute paths like
        ``\\\\guldan\\drive1\\...\\star5.tga``; the file itself lives in the archives
        under a normal name (``...\\star5tga.tga``), so match by base name instead.
        """
        base = internal_path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        stem = base.rsplit(".", 1)[0]
        if not stem:
            return None
        candidates = []
        for name in self.game.names():
            lower = name.lower()
            if not lower.endswith((".blp", ".tga")):
                continue
            file_stem = lower.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            if file_stem == stem:
                candidates.append((0, len(name), name))
            elif file_stem.startswith(stem):
                candidates.append((1, len(name), name))
            elif stem.startswith(file_stem) and len(file_stem) >= 6:
                candidates.append((2, len(name), name))
        for _priority, _length, name in sorted(candidates):
            data = self.read(name)
            if data and data[:4] in (b"BLP1", b"BLP2"):
                try:
                    from . import blp as _blp
                    image = _blp.decode(data).convert("RGBA")
                except Exception:  # noqa: BLE001
                    continue
                self.fallbacks[internal_path] = name
                return image
        return None

    def image(self, internal_path: str, replaceable_id: int = 0) -> Image.Image | None:
        """Decode a texture to RGBA, falling back to a flat placeholder."""
        key = internal_path.lower() if internal_path else f"\x00replaceable{replaceable_id}"
        if key in self._images:
            return self._images[key]

        image = None
        if internal_path:
            data = self.read(internal_path)
            if data and data[:4] in (b"BLP1", b"BLP2"):
                try:
                    image = blp.decode(data).convert("RGBA")
                except Exception:  # noqa: BLE001 - a broken texture must not kill a conversion
                    image = None
            if image is None:
                image = self._fallback_texture(internal_path)
            if image is None:
                self.missing.add(internal_path)
        elif replaceable_id:
            image = placeholder_texture(replaceable_id)

        self._images[key] = image
        return image

    def png_bytes(self, internal_path: str, replaceable_id: int = 0) -> bytes | None:
        key = internal_path.lower() if internal_path else f"\x00replaceable{replaceable_id}"
        if key in self._png:
            return self._png[key]
        image = self.image(internal_path, replaceable_id)
        data = None
        if image is not None:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
        self._png[key] = data
        return data

    def write_png(self, internal_path: str, root: str | os.PathLike,
                  replaceable_id: int = 0) -> Path | None:
        """Convert a texture once into <root>/<internal path>.png and return the file."""
        if internal_path:
            rel = internal_path.replace("\\", "/") + ".png"
        else:
            rel = f"_replaceable/{REPLACEABLE_NAMES.get(replaceable_id, f'replaceable_{replaceable_id}')}.png"
        dest = Path(root) / rel
        if dest.is_file():
            return dest
        data = self.png_bytes(internal_path, replaceable_id)
        if data is None:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    # -- convenience ------------------------------------------------------
    def model_textures(self, model) -> list[dict]:
        """Summary of every texture a model references."""
        out = []
        for index, texture in enumerate(model.textures):
            entry = {
                "index": index,
                "path": texture.path,
                "replaceable_id": texture.replaceable_id,
                "wrap_mode": texture.wrap_mode,
                "exists": None,
                "image": None,
            }
            image = self.image(texture.path, texture.replaceable_id)
            entry["exists"] = image is not None
            entry["image"] = image
            out.append(entry)
        return out
