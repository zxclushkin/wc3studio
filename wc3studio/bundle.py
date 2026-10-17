"""Build a complete asset bundle: models, glTF with animations, raw files, sounds,
textures, tables, UI — optionally zipped, plus a manifest and a per-model index.

Sounds are not referenced by the MDX format: the game keeps a unit's voice lines
next to its model (``Units\\Human\\Footman\\FootmanYes1.wav``), so "the sounds of a
model" are the audio files that share its folder.
"""
from __future__ import annotations

import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["BundleOptions", "build_bundle", "zip_folder", "FORMATS"]

FORMATS = ("models", "glb", "raw", "sounds", "icons", "tables", "ui")
AUDIO_EXTENSIONS = (".wav", ".mp3")
DEFAULT_FORMATS = {"models", "glb", "sounds"}
ICON_FORMATS = {"icons"}


@dataclass
class BundleOptions:
    out: Path
    formats: set = field(default_factory=lambda: set(DEFAULT_FORMATS))
    textures: bool = True
    previews: bool = False
    preview_size: int = 192
    preview_fps: int = 10
    preview_frames: int = 10
    preview_sequence: str = "Stand"
    index: bool = True
    limit: int = 0
    model_filter: str = ""
    all_geosets: bool = False
    sequences: list | None = None
    archive: bool = False
    keep_folder: bool = True

    def normalised(self) -> "BundleOptions":
        self.formats = {fmt for fmt in self.formats if fmt in FORMATS}
        if not self.formats:
            self.formats = set(DEFAULT_FORMATS)
        self.out = Path(self.out)
        return self


def _audio_by_folder(game) -> dict:
    """Map lower-case folder -> audio file names, from one pass over the archives."""
    grouped: dict[str, list] = {}
    for name in game.names():
        if name.lower().endswith(AUDIO_EXTENSIONS):
            grouped.setdefault(name.lower().rsplit("\\", 1)[0], []).append(name)
    return grouped


def build_bundle(session, options: BundleOptions, progress=None) -> dict:
    """Export everything requested into ``options.out`` and return statistics.

    ``progress`` is called as ``progress(kind, done, total, message)``.
    """
    options = options.normalised()
    out = options.out
    out.mkdir(parents=True, exist_ok=True)
    textures_root = out / "textures"

    stats = {
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": str(session.root) if session.root else "",
        "formats": sorted(options.formats),
        "counts": {"models": 0, "glb": 0, "raw": 0, "sounds": 0, "icons": 0,
                   "tables": 0, "ui": 0,
                   "previews": 0, "indexed": 0},
        "bytes": 0,
        "errors": [],
        "out": str(out),
    }

    def report(kind, done, total, message=""):
        if progress is not None:
            progress(kind, done, total, message)

    models = session.models(options.model_filter)
    if options.limit:
        models = models[: options.limit]
    total_models = len(models)

    needs_folders = bool(models) and ("sounds" in options.formats or options.index)
    audio = _audio_by_folder(session.game) if needs_folders else {}
    written: dict[str, list] = {internal.lower(): [] for internal in models}

    # --- models -----------------------------------------------------------
    if options.formats & {"models", "glb"} or options.previews:
        for index, internal in enumerate(models, 1):
            stem = Path(internal.replace("\\", "/")).stem
            folder = out / Path(internal.replace("\\", "/")).parent
            folder.mkdir(parents=True, exist_ok=True)
            try:
                if "models" in options.formats:
                    session.export_obj(internal, folder / f"{stem}.obj",
                                       textures_root=textures_root,
                                       all_geosets=options.all_geosets)
                    written[internal.lower()] += [f"{stem}.obj", f"{stem}.mtl"]
                    stats["bytes"] += _size_of(folder / f"{stem}.obj")
                    stats["counts"]["models"] += 1
                if "glb" in options.formats:
                    session.export_glb(internal, folder / f"{stem}.glb",
                                       sequences=options.sequences,
                                       all_geosets=options.all_geosets)
                    written[internal.lower()].append(f"{stem}.glb")
                    stats["bytes"] += _size_of(folder / f"{stem}.glb")
                    stats["counts"]["glb"] += 1
                if options.previews:
                    path = _write_preview(session, internal, folder, options)
                    if path:
                        written[internal.lower()].append(path.name)
                        stats["bytes"] += _size_of(path)
                        stats["counts"]["previews"] += 1
            except Exception as exc:  # noqa: BLE001 - one broken model must not stop the bundle
                stats["errors"].append(f"{internal}: {exc}")
            if index % 25 == 0 or index == total_models:
                report("models", index, total_models, internal)

    # --- raw source files -------------------------------------------------
    if "raw" in options.formats:
        for index, internal in enumerate(models, 1):
            try:
                size = session.export_raw(internal, out / "raw")
                if size:
                    written[internal.lower()].append(f"raw/{internal.replace(chr(92), '/')}")
                    stats["bytes"] += size
                    stats["counts"]["raw"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"raw {internal}: {exc}")
            if index % 100 == 0 or index == total_models:
                report("raw", index, total_models, internal)

    # --- sounds (the audio that lives in each model's folder) -------------
    if "sounds" in options.formats and models:
        folders = {}
        for internal in models:
            folder = internal.rsplit("\\", 1)[0]
            folders.setdefault(folder.lower(), folder)
        ordered = sorted(folders.items())
        for index, (key, folder) in enumerate(ordered, 1):
            names = audio.get(key, [])
            for name in names:
                data = session.game.read(name)
                if not data:
                    stats["errors"].append(f"sound {name}: unreadable")
                    continue
                target = out / Path(name.replace("\\", "/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                stats["bytes"] += len(data)
                stats["counts"]["sounds"] += 1
            if index % 50 == 0 or index == len(ordered):
                report("sounds", index, len(ordered), folder)

    # --- icons ------------------------------------------------------------
    icon_index = None
    if ICON_FORMATS & options.formats or options.index:
        from .icons import build_index
        from .tables import TableDatabase
        icon_index = build_index(session.game, TableDatabase(session.game))
    if "icons" in options.formats and icon_index is not None:
        from .icons import export as export_icons
        result = export_icons(icon_index, session.resources, out / "icons",
                              progress=lambda done, total, message: report(
                                  "icons", done, total, message))
        stats["bytes"] += result["bytes"]
        stats["counts"]["icons"] = result["written"]

    # --- data tables ------------------------------------------------------
    if "tables" in options.formats:
        from .tables import TableDatabase
        database = TableDatabase(session.game)
        summary = database.tables_summary()
        for index, (internal, _kind, _rows, _columns) in enumerate(summary, 1):
            try:
                table = database.load(internal)
                target = out / "tables" / (internal.replace("\\", "/") + ".csv")
                table.write(target, "csv")
                stats["bytes"] += _size_of(target)
                stats["counts"]["tables"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"table {internal}: {exc}")
            if index % 25 == 0 or index == len(summary):
                report("tables", index, len(summary), internal)

    # --- interface artwork ------------------------------------------------
    if "ui" in options.formats:
        from .ui import UiLibrary
        library = UiLibrary(session.game)
        items = library.assets()
        for index, item in enumerate(items, 1):
            try:
                image = session.resources.image(item["path"])
                if image is None:
                    continue
                target = out / "ui" / (item["path"].replace("\\", "/") + ".png")
                target.parent.mkdir(parents=True, exist_ok=True)
                image.save(target)
                stats["bytes"] += _size_of(target)
                stats["counts"]["ui"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"ui {item['path']}: {exc}")
            if index % 100 == 0 or index == len(items):
                report("ui", index, len(items), item["path"])

    # --- per-model index --------------------------------------------------
    index_path = out / "models.json"
    if options.index and models:
        records = _build_index(session, models, audio, written, out, options, report,
                               icon_index=icon_index)
        index_path.write_text(json.dumps(records, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        stats["counts"]["indexed"] = len(records)
        stats["index"] = str(index_path)

    stats["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    # The manifest is written before archiving so the zip contains it too, and both
    # copies stay identical (the archive size only goes into the returned stats).
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["manifest"] = str(manifest)

    if options.archive:
        report("zip", 0, 1, "archiving")
        archive = out.with_suffix(".zip")
        size = zip_folder(out, archive)
        stats["archive_bytes"] = size
        stats["archive"] = str(archive)
        report("zip", 1, 1, str(archive))
    return stats


def _build_index(session, models, audio, written, out, options, report=None,
                 icon_index=None) -> list:
    """One record per model: what was exported, its textures, sounds and animations."""
    records = []
    total = len(models)
    # one pass over all models, so the portrait lookup does not rescan the archives
    by_folder: dict[str, list] = {}
    for name in session.models():
        by_folder.setdefault(name.rsplit("\\", 1)[0].lower(), []).append(name)
    for position, internal in enumerate(models, 1):
        folder = internal.rsplit("\\", 1)[0]
        key = folder.lower()
        try:
            model = session.load(internal)
        except Exception as exc:  # noqa: BLE001
            records.append({"model": internal, "error": str(exc)})
            continue
        portrait = None
        for name in by_folder.get(key, []):
            if "portrait" in name.lower().rsplit("\\", 1)[-1]:
                portrait = name
                break
        sounds = [name.rsplit("\\", 1)[-1] for name in audio.get(key, [])]
        icon = icon_index.for_model(internal) if icon_index is not None else None
        records.append({
            "model": internal,
            "folder": folder.replace("\\", "/"),
            "portrait": portrait,
            "files": written.get(internal.lower(), []),
            "textures": [texture.path for texture in model.textures if texture.path],
            "sounds": sounds,
            "sound_count": len(sounds),
            "icon": icon.icon if icon else "",
            "icon_png": ("icons/" + icon.icon.replace(chr(92), "/") + ".png"
                          if icon and "icons" in options.formats else ""),
            "triangles": model.triangle_count,
            "bones": len(model.nodes),
            "geosets": len(model.geosets),
            "animations": [sequence["name"] for sequence in model.sequences],
        })
        if position % 100 == 0 or position == total:
            if report is not None:
                report("index", position, total, internal)
    return records


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _write_preview(session, internal: str, folder: Path, options: BundleOptions):
    """Render a short GIF of one sequence, so a bundle has something to look at."""
    from .pipeline import render_animation, write_gif
    stem = Path(internal.replace("\\", "/")).stem
    try:
        model = session.load(internal)
    except Exception:  # noqa: BLE001
        return None
    if not model.sequences:
        return None
    sequence = model.sequence(options.preview_sequence) or model.sequences[0]
    target = folder / f"{stem}.gif"
    try:
        images, _meta = render_animation(
            session, model, [sequence["name"]], fps=options.preview_fps,
            size=options.preview_size, frames=options.preview_frames,
            all_geosets=options.all_geosets)
        write_gif(images, target, options.preview_fps)
    except Exception:  # noqa: BLE001
        return None
    return target


def zip_folder(folder: Path, archive: Path, compress_level: int = 6) -> int:
    """Zip ``folder`` (storing paths relative to its parent) and return the size."""
    folder = Path(folder)
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=compress_level) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder.parent))
    return archive.stat().st_size
