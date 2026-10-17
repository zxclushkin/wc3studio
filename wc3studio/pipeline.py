"""High-level session API shared by the CLI and the GUI.

``Session`` owns an opened game, a texture/model cache and the conversion entry points,
so both front ends stay thin.
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import gltf as gltf_module
from .anim import Animator
from .mdx import Model, parse as parse_mdx
from .objexport import export_obj as _export_obj
from .render import fit_camera, render
from .resources import Resources
from .stormlib import Game, StormLibError

__all__ = ["Session", "render_animation", "write_gif", "write_mp4", "playlist_frames"]


class Session:
    """An opened Warcraft III installation plus caches."""

    def __init__(self, game_dir: str | os.PathLike | None = None, texture_cache: str | os.PathLike | None = None):
        self.game = Game.open(game_dir) if game_dir else Game.autodetect()
        self.resources = Resources(self.game, texture_cache)
        self._models: dict[str, Model] = {}
        self._animators: dict[str, Animator] = {}
        self._animators_by_model: dict[int, Animator] = {}
        self._names: list[str] | None = None

    # -- discovery --------------------------------------------------------
    @property
    def root(self) -> Path | None:
        return self.game.root

    def names(self) -> list[str]:
        if self._names is None:
            self._names = self.game.names()
        return self._names

    def models(self, pattern: str = "") -> list[str]:
        needle = pattern.lower()
        return [n for n in self.names()
                if n.lower().endswith((".mdx", ".mdl")) and (not needle or needle in n.lower())]

    def search(self, pattern: str, limit: int = 0) -> list[str]:
        needle = pattern.lower()
        out = [n for n in self.names() if needle in n.lower()]
        return out[:limit] if limit else out

    def resolve(self, wanted: str) -> str:
        """Find one internal name from a full path, a partial path or a bare file name.

        Raises LookupError (with the candidates in the message) when ambiguous.
        """
        needle = wanted.replace("/", "\\").strip().lower()
        if self.game.read(wanted) is not None:
            return wanted
        names = self.names()
        exact = [n for n in names if n.lower() == needle]
        if exact:
            return exact[0]
        if "\\" not in needle:
            bare = needle[:-4] if needle.endswith((".mdx", ".mdl")) else needle
            stems = [n for n in names
                     if n.lower().endswith((".mdx", ".mdl"))
                     and os.path.splitext(os.path.basename(n))[0].lower() == bare]
            if stems:
                return sorted(stems)[0]
        partial = [n for n in names if needle in n.lower()]
        if len(partial) == 1:
            return partial[0]
        if partial:
            models = [n for n in partial if n.lower().endswith((".mdx", ".mdl"))]
            pool = models or partial
            if len(pool) == 1:
                return pool[0]
            raise LookupError(f"'{wanted}' matches {len(pool)} files: " + ", ".join(sorted(pool)[:12]))
        raise LookupError(f"nothing matching '{wanted}' in the archives")

    # -- loading ----------------------------------------------------------
    def raw(self, internal_path: str) -> bytes | None:
        return self.game.read(internal_path)

    def size(self, internal_path: str) -> int:
        """Size of one internal file in bytes (0 when missing)."""
        return self.game.size(internal_path)

    def load(self, internal_path: str) -> Model:
        key = internal_path.lower()
        if key not in self._models:
            data = self.game.read(internal_path)
            if data is None:
                raise FileNotFoundError(f"not found in archives: {internal_path}")
            self._models[key] = parse_mdx(data)
        return self._models[key]

    def animator(self, internal_path: str) -> Animator:
        key = internal_path.lower()
        if key not in self._animators:
            self._animators[key] = Animator(self.load(internal_path))
        return self._animators[key]

    def animator_for(self, model: Model) -> Animator:
        """Animator for an already-parsed model (shared, so its matrix cache is reused)."""
        key = id(model)
        if key not in self._animators_by_model:
            self._animators_by_model[key] = Animator(model)
        return self._animators_by_model[key]

    def summary(self, internal_path: str) -> dict:
        model = self.load(internal_path)
        visible = sorted(set(range(len(model.geosets))) - model.pose_hidden_geosets)
        textures = []
        for texture in model.textures:
            known = bool(texture.path) and self.game.read(texture.path) is not None
            textures.append({
                "path": texture.path,
                "replaceable_id": texture.replaceable_id,
                "available": known or bool(texture.replaceable_id),
            })
        return {
            "path": internal_path,
            "name": model.name,
            "version": model.version,
            "geosets": len(model.geosets),
            "visible_geosets": len(visible),
            "triangles": sum(model.geosets[i].triangle_count for i in visible),
            "triangles_total": model.triangle_count,
            "vertices": sum(model.geosets[i].vertex_count for i in visible),
            "materials": len(model.materials),
            "textures": textures,
            "nodes": len(model.nodes),
            "bones": len(model.bones),
            "helpers": len(model.helpers),
            "sequences": [{"name": s["name"], "start": s["start"], "end": s["end"]}
                          for s in model.sequences],
            "bytes": len(self.game.read(internal_path) or b""),
        }

    # -- rendering --------------------------------------------------------
    def _as_model(self, model_or_path) -> Model:
        if isinstance(model_or_path, Model):
            return model_or_path
        return self.load(str(model_or_path))

    def render(self, model_or_path, **kwargs) -> Image.Image:
        """Render a still. Without an explicit frame the model's representative pose is
        used (the start of its first animation), which is what a preview should show."""
        model = self._as_model(model_or_path)
        if kwargs.get("frame") is None and getattr(model, "sequences", None) and model.nodes:
            kwargs["frame"] = model.first_frame
            kwargs.setdefault("local_time", 0.0)
        return render(model, self.resources, **kwargs)

    def animation(self, model_or_path, sequence: str = "Walk", **kwargs):
        """Render an animation playlist to a list of images (see :func:`render_animation`)."""
        return render_animation(self, self._as_model(model_or_path), sequence, **kwargs)

    # -- exporting --------------------------------------------------------
    def export_obj(self, model_or_path, out_path: str | os.PathLike,
                   textures_root: str | os.PathLike | None = None, **kwargs):
        return _export_obj(self._as_model(model_or_path), out_path, self.resources,
                           textures_root=textures_root, **kwargs)

    def export_glb(self, model_or_path, out_path: str | os.PathLike, **kwargs):
        return gltf_module.export_glb(self._as_model(model_or_path), out_path,
                                      self.resources, **kwargs)

    def export_raw(self, internal_path: str, out_root: str | os.PathLike) -> int | None:
        data = self.game.read(internal_path)
        if data is None:
            return None
        dest = Path(out_root) / internal_path.replace("\\", os.sep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)

    def close(self):
        self.game.close()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc):
        self.close()


def playlist_frames(model: Model, playlist: list[str], fps: float, frames_per_sequence: int = 0):
    """Expand a sequence playlist into (frame, local time, sequence name) entries.

    ``playlist`` entries are sequence names or prefixes; repeating a name plays it again.
    """
    plan = []
    for name in playlist:
        sequence = model.sequence(name)
        if sequence is None:
            available = ", ".join(s["name"] for s in model.sequences)
            raise ValueError(f"no animation matching {name!r}; available: {available}")
        plan.append(sequence)

    entries = []
    for sequence in plan:
        duration = max(sequence["end"] - sequence["start"], 1)
        count = frames_per_sequence or max(2, int(round(duration / 1000.0 * fps)))
        step = duration / count
        for i in range(count):
            frame = sequence["start"] + i * step
            entries.append((frame, frame - sequence["start"], sequence["name"]))
    return plan, entries


def render_animation(session: Session, model: Model, sequence="Walk", fps: float = 20.0,
                     size: int = 512, yaw: float = 35.0, pitch: float = 15.0,
                     orbit: float = 0.0, zoom: float = 1.0, frames: int = 0,
                     all_geosets: bool = False, progress=None) -> list:
    """Render every frame of an animation playlist. Returns (images, metadata)."""
    playlist = [s.strip() for s in sequence.split(",")] if isinstance(sequence, str) else list(sequence)
    playlist = [s for s in playlist if s]
    plan, entries = playlist_frames(model, playlist, fps, frames)
    animator = session.animator_for(model)

    camera = fit_camera(model, animator, [(f, lt) for f, lt, _ in entries],
                        all_geosets=all_geosets)
    images = []
    for index, (frame, local, _name) in enumerate(entries):
        image = render(model, session.resources, size=size,
                       yaw=yaw + orbit * (index / fps), pitch=pitch, camera=camera,
                       frame=frame, local_time=local, animator=animator, zoom=zoom,
                       all_geosets=all_geosets)
        images.append(image)
        if progress is not None:
            progress(index + 1, len(entries))
    meta = {
        "sequences": [s["name"] for s in plan],
        "frames": len(images),
        "fps": fps,
        "camera": camera,
    }
    return images, meta


def write_gif(images: list, path: str | os.PathLike, fps: float, loop: int = 0) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    palette = [image.convert("P", palette=Image.ADAPTIVE, colors=256) for image in images]
    duration = max(int(round(1000.0 / max(fps, 0.1))), 20)
    palette[0].save(path, save_all=True, append_images=palette[1:],
                    duration=duration, loop=loop, optimize=True, disposal=2)
    return path


def write_mp4(frames_dir: str | os.PathLike, path: str | os.PathLike, fps: float,
              pattern: str = "frame_%04d.png") -> bool:
    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps),
           "-i", str(Path(frames_dir) / pattern), "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-crf", "18", str(path)]
    try:
        subprocess.run(cmd, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
