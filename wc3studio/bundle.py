"""Build a complete asset bundle: models, glTF with animations, raw files, tables, UI.

One call produces a folder (optionally zipped) with everything the tool can export,
plus a manifest describing what was written.
"""
from __future__ import annotations

import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["BundleOptions", "build_bundle", "zip_folder"]

FORMATS = ("models", "glb", "raw", "tables", "ui")


@dataclass
class BundleOptions:
    out: Path
    formats: set = field(default_factory=lambda: {"models", "glb"})
    textures: bool = True
    previews: bool = False
    preview_size: int = 192
    preview_fps: int = 10
    preview_frames: int = 10
    preview_sequence: str = "Stand"
    limit: int = 0
    model_filter: str = ""
    all_geosets: bool = False
    sequences: list | None = None
    archive: bool = False
    keep_folder: bool = True

    def normalised(self) -> "BundleOptions":
        self.formats = {fmt for fmt in self.formats if fmt in FORMATS}
        if not self.formats:
            self.formats = {"models", "glb"}
        self.out = Path(self.out)
        return self


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
        "counts": {"models": 0, "glb": 0, "raw": 0, "tables": 0, "ui": 0, "previews": 0},
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

    # --- models -----------------------------------------------------------
    if "models" in options.formats or "glb" in options.formats or options.previews:
        for index, internal in enumerate(models, 1):
            stem = Path(internal.replace("\\", "/")).stem
            folder = out / Path(internal.replace("\\", "/")).parent
            folder.mkdir(parents=True, exist_ok=True)
            try:
                if "models" in options.formats:
                    session.export_obj(internal, folder / f"{stem}.obj",
                                       textures_root=textures_root,
                                       all_geosets=options.all_geosets)
                    stats["bytes"] += _size_of(folder / f"{stem}.obj")
                    stats["counts"]["models"] += 1
                if "glb" in options.formats:
                    session.export_glb(internal, folder / f"{stem}.glb",
                                       sequences=options.sequences,
                                       all_geosets=options.all_geosets)
                    stats["bytes"] += _size_of(folder / f"{stem}.glb")
                    stats["counts"]["glb"] += 1
                if options.previews:
                    path = _write_preview(session, internal, folder, options)
                    if path:
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
                    stats["bytes"] += size
                    stats["counts"]["raw"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"raw {internal}: {exc}")
            if index % 100 == 0 or index == total_models:
                report("raw", index, total_models, internal)

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
