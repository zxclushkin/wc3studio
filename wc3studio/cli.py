"""wc3studio command line interface.

    python -m wc3studio archives
    python -m wc3studio list --filter footman
    python -m wc3studio info "Units\\Critters\\Sheep\\Sheep.mdx"
    python -m wc3studio extract --pattern "*.mdx" --out raw
    python -m wc3studio convert --model ... --formats obj,glb --out converted
    python -m wc3studio render --model ... --out sheep.png --sequence Walk --time 0.4
    python -m wc3studio animate --model ... --sequence Stand,Walk,Walk,Death --out anim --mp4
    python -m wc3studio gallery --out gallery
    python -m wc3studio validate converted
    python -m wc3studio doctor
    python -m wc3studio gui
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import time
from pathlib import Path

from . import __version__
from .mdx import MdxError
from .pipeline import Session, render_animation, write_gif, write_mp4
from .stormlib import StormLib, StormLibError, find_game_dirs

DEFAULT_GALLERY = [
    ("Human Footman", r"Units\Human\Footman\Footman.mdx", 35, 15),
    ("Human Knight", r"Units\Human\Knight\Knight.mdx", 35, 15),
    ("Human Paladin (hero)", r"Units\Human\HeroPaladin\HeroPaladin.mdx", 35, 15),
    ("Orc Grunt", r"Units\Orc\Grunt\grunt.mdx", 35, 15),
    ("Blademaster (hero)", r"Units\Orc\HeroBlademaster\HeroBlademaster.mdx", 35, 15),
    ("Tauren", r"Units\Orc\Tauren\Tauren.mdx", 35, 15),
    ("Undead Ghoul", r"Units\Undead\Ghoul\Ghoul.mdx", 35, 15),
    ("Abomination", r"Units\Undead\Abomination\Abomination.mdx", 35, 15),
    ("Death Knight (hero)", r"Units\Undead\HeroDeathKnight\HeroDeathKnight.mdx", 35, 15),
    ("Night Elf Archer", r"Units\NightElf\Archer\Archer.mdx", 35, 15),
    ("Demon Hunter (hero)", r"Units\NightElf\HeroDemonHunter\HeroDemonHunter.mdx", 35, 15),
    ("Mountain Giant", r"Units\NightElf\MountainGiant\MountainGiant.mdx", 35, 15),
    ("Sheep (critter)", r"Units\Critters\Sheep\Sheep.mdx", 35, 15),
    ("Town Hall", r"Buildings\Human\TownHall\TownHall.mdx", 40, 18),
    ("Farm", r"Buildings\Human\Farm\Farm.mdx", 40, 18),
    ("Gold Mine", r"Buildings\Other\Goldmine\Goldmine.mdx", 40, 18),
]


# --------------------------------------------------------------------------- utils

def _fmt_size(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(num) < 1024 or unit == "GiB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} GiB"


def _open(args) -> Session:
    try:
        return Session(getattr(args, "game", None))
    except StormLibError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _resolve(session: Session, wanted: str) -> str:
    """Accept a full internal path, a partial path or a bare file name."""
    try:
        return session.resolve(wanted)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _progress(done, total, label="frames"):
    width = 28
    filled = int(width * done / max(total, 1))
    sys.stdout.write(f"\r  [{('#' * filled).ljust(width)}] {done}/{total} {label}")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")


# --------------------------------------------------------------------------- commands

def cmd_archives(args) -> int:
    session = _open(args)
    print(f"game folder : {session.root}")
    print(f"stormlib    : {StormLib.instance().path}")
    print(f"stormlib ver: {StormLib.instance().version()}")
    print()
    total_models = 0
    for archive in session.game.archives:
        names = archive.listfile()
        models = sum(1 for n in names if n.lower().endswith((".mdx", ".mdl")))
        total_models += models
        print(f"  {archive.name:<18} {_fmt_size(archive.size):>10}  "
              f"{len(names):>6} files  {models:>5} models")
    print()
    print(f"unique internal names : {len(session.names())}")
    print(f"unique model files    : {len(session.models())}")
    return 0


def cmd_list(args) -> int:
    session = _open(args)
    names = session.names()
    if args.models_only:
        names = [n for n in names if n.lower().endswith((".mdx", ".mdl"))]
    if args.ext:
        wanted = tuple("." + e.lower().lstrip(".") for e in args.ext.split(","))
        names = [n for n in names if n.lower().endswith(wanted)]
    if args.filter:
        pattern = args.filter.lower()
        names = [n for n in names if fnmatch.fnmatch(n.lower(), pattern)
                 or pattern in n.lower()]
    names.sort()
    shown = names[: args.limit] if args.limit else names
    for name in shown:
        size = None
        data = session.game.read(name)
        if data is not None:
            size = len(data)
        print(f"{_fmt_size(size) if size is not None else '?':>10}  {name}")
    print(f"\n{len(shown)} shown, {len(names)} matched")
    return 0


def cmd_info(args) -> int:
    session = _open(args)
    for wanted in args.model:
        internal = _resolve(session, wanted)
        info = session.summary(internal)
        print(f"\n=== {internal}")
        print(f"  model name    : {info['name']}")
        print(f"  mdx version   : {info['version']}")
        print(f"  size          : {_fmt_size(info['bytes'])}")
        print(f"  geosets       : {info['geosets']} ({info['visible_geosets']} in the standing pose)")
        print(f"  triangles     : {info['triangles']} of {info['triangles_total']}")
        print(f"  vertices      : {info['vertices']}")
        print(f"  materials     : {info['materials']}")
        print(f"  skeleton      : {info['bones']} bones, {info['helpers']} helpers")
        print("  textures:")
        for texture in info["textures"]:
            mark = "ok " if texture["available"] else "MISSING"
            label = texture["path"] or f"<replaceable id {texture['replaceable_id']}>"
            print(f"      [{mark}] {label}")
        print(f"  animations ({len(info['sequences'])}):")
        for sequence in info["sequences"]:
            ms = sequence["end"] - sequence["start"]
            print(f"      {sequence['name']:<24} {ms:>7} ms")
    return 0


def cmd_extract(args) -> int:
    session = _open(args)
    names = session.names()
    if args.pattern:
        patterns = [p.lower() for p in args.pattern.split(",")]
        names = [n for n in names if any(fnmatch.fnmatch(n.lower(), p) or p.strip("*") in n.lower()
                                         for p in patterns)]
    if args.limit:
        names = names[: args.limit]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    total = 0
    failed = []
    for index, name in enumerate(names, 1):
        size = session.export_raw(name, out)
        if size is None:
            failed.append(name)
        else:
            total += size
        if index % 200 == 0 or index == len(names):
            print(f"  {index}/{len(names)}  {_fmt_size(total)}")
    print(f"\nextracted {len(names) - len(failed)} files, {_fmt_size(total)} "
          f"in {time.time() - t0:.1f}s -> {out.resolve()}")
    if failed:
        print(f"failed: {len(failed)}")
    return 0


def cmd_convert(args) -> int:
    session = _open(args)
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    for fmt in formats:
        if fmt not in ("obj", "glb"):
            print(f"error: unknown format {fmt!r} (use obj, glb)", file=sys.stderr)
            return 2

    if args.all:
        names = session.models()
        if args.filter:
            names = [n for n in names if args.filter.lower() in n.lower()]
    else:
        names = [_resolve(session, m) for m in args.model]
    if args.limit:
        names = names[: args.limit]
    if not names:
        print("error: nothing to convert (pass --model or --all)", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    textures_root = out / "textures"
    t0 = time.time()
    ok = failed = 0
    triangles = 0
    missing: set[str] = set()

    for index, internal in enumerate(names, 1):
        stem = os.path.splitext(os.path.basename(internal))[0]
        rel = Path(internal.replace("\\", "/")).parent if args.tree else Path("")
        target = out / rel
        target.mkdir(parents=True, exist_ok=True)
        try:
            model = session.load(internal)
            for fmt in formats:
                if fmt == "obj":
                    stats = session.export_obj(internal, target / f"{stem}.obj",
                                               textures_root=textures_root,
                                               all_geosets=args.all_geosets)
                    triangles = stats["triangles"]
                    missing.update(stats["missing_textures"])
                else:
                    stats = session.export_glb(internal, target / f"{stem}.glb",
                                               sequences=args.sequences.split(",") if args.sequences else None,
                                               all_geosets=args.all_geosets)
                    missing.update(stats["missing_textures"])
            ok += 1
        except (MdxError, OSError, ValueError) as exc:
            print(f"  !! {internal}: {exc}")
            failed += 1
        if index % 50 == 0 or index == len(names):
            print(f"  {index}/{len(names)}  ok={ok} failed={failed}  {time.time() - t0:.1f}s")

    print(f"\nconverted {ok} models ({failed} failed) in {time.time() - t0:.1f}s")
    print(f"output: {out.resolve()}")
    if missing:
        print(f"textures referenced but missing: {len(missing)}")
        for name in sorted(missing)[:10]:
            print("   ", name)
    return 0


def cmd_render(args) -> int:
    session = _open(args)
    internal = _resolve(session, args.model)
    model = session.load(internal)
    frame = local_time = None
    if args.sequence:
        sequence = model.sequence(args.sequence)
        if sequence is None:
            names = ", ".join(s["name"] for s in model.sequences)
            print(f"error: no animation matching {args.sequence!r}; available: {names}", file=sys.stderr)
            return 2
        span = sequence["end"] - sequence["start"]
        local_time = span * max(0.0, min(1.0, args.time))
        frame = sequence["start"] + local_time
    image = session.render(internal, size=args.size, yaw=args.yaw, pitch=args.pitch,
                           zoom=args.zoom, all_geosets=args.all_geosets,
                           frame=frame, local_time=local_time)
    image.save(args.out)
    suffix = f" at {args.sequence} t={args.time:.2f}" if args.sequence else ""
    print(f"rendered {args.size}x{args.size}{suffix} -> {Path(args.out).resolve()}")
    return 0


def cmd_animate(args) -> int:
    session = _open(args)
    internal = _resolve(session, args.model)
    model = session.load(internal)
    print(f"{internal}")
    print(f"  playlist : {args.sequence}")
    images, meta = render_animation(session, model, args.sequence, fps=args.fps, size=args.size,
                                    yaw=args.yaw, pitch=args.pitch, orbit=args.orbit,
                                    zoom=args.zoom, frames=args.frames,
                                    all_geosets=args.all_geosets,
                                    progress=_progress)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.png:
        for index, image in enumerate(images):
            image.save(out / f"frame_{index:04d}.png")
    stem = os.path.splitext(os.path.basename(internal))[0]
    tag = meta["sequences"][0].split()[0].lower() if len(meta["sequences"]) == 1 else "anim"
    gif = write_gif(images, out / f"{stem}_{tag}.gif", args.fps)
    print(f"  gif      : {gif.resolve()}  ({meta['frames']} frames)")
    if args.mp4:
        if args.png or args.preview:
            frames_dir = out
        else:
            frames_dir = out / "_frames"
            frames_dir.mkdir(exist_ok=True)
            for index, image in enumerate(images):
                image.save(frames_dir / f"frame_{index:04d}.png")
        target = out / f"{stem}_{tag}.mp4"
        if write_mp4(frames_dir, target, args.fps):
            print(f"  mp4      : {target.resolve()}")
        else:
            print("  mp4      : ffmpeg not found, skipped")
        if not args.png and not args.preview:
            for path in frames_dir.glob("*.png"):
                path.unlink()
            frames_dir.rmdir()
    if not args.png:
        print("  (use --png to keep the individual frames)")
    return 0


def cmd_gallery(args) -> int:
    from PIL import Image, ImageDraw, ImageFont
    session = _open(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tile = args.tile
    font = None
    for candidate in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, max(12, tile // 18))
                break
            except OSError:
                pass
    if font is None:
        font = ImageFont.load_default()

    entries = list(DEFAULT_GALLERY)
    if args.model:
        entries = [(m, _resolve(session, m), args.yaw, args.pitch) for m in args.model]

    rendered = []
    for label, internal, yaw, pitch in entries:
        try:
            image = session.render(internal, size=tile, yaw=yaw, pitch=pitch, zoom=1.1)
        except (FileNotFoundError, MdxError) as exc:
            print(f"  skip {internal}: {exc}")
            continue
        rendered.append((label, image))
        print(f"  {label}")

    if not rendered:
        print("nothing rendered", file=sys.stderr)
        return 1

    columns = args.columns
    rows = (len(rendered) + columns - 1) // columns
    padding = 10
    label_height = max(20, tile // 12)
    sheet = Image.new("RGB", (columns * (tile + padding) + padding,
                              rows * (tile + label_height + padding) + padding), (14, 15, 19))
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(rendered):
        x = padding + (index % columns) * (tile + padding)
        y = padding + (index // columns) * (tile + label_height + padding)
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + tile + 4), label, fill=(225, 228, 235), font=font)
    target = out / "gallery.png"
    sheet.save(target)
    print(f"\ngallery -> {target.resolve()}  {sheet.size[0]}x{sheet.size[1]}")
    return 0


def cmd_validate(args) -> int:
    """Check an export folder: OBJ indices, MTL links, GLB structure."""
    root = Path(args.folder)
    if not root.is_dir():
        print(f"error: not a folder: {root}", file=sys.stderr)
        return 2

    obj_files = sorted(root.rglob("*.obj"))
    glb_files = sorted(root.rglob("*.glb"))
    print(f"checking {len(obj_files)} OBJ and {len(glb_files)} GLB files under {root}")

    bad = empty = missing_mtl = 0
    triangles = 0
    for path in obj_files:
        vertices = uv = normals = faces = 0
        problems = 0
        mtl = None
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("v "):
                    vertices += 1
                elif line.startswith("vt "):
                    uv += 1
                elif line.startswith("vn "):
                    normals += 1
                elif line.startswith("f "):
                    faces += 1
                    for part in line.split()[1:]:
                        fields = part.split("/")
                        try:
                            if not (1 <= int(fields[0]) <= vertices):
                                problems += 1
                            if len(fields) > 1 and fields[1] and not (1 <= int(fields[1]) <= uv):
                                problems += 1
                            if len(fields) > 2 and fields[2] and not (1 <= int(fields[2]) <= normals):
                                problems += 1
                        except ValueError:
                            problems += 1
                elif line.startswith("mtllib "):
                    mtl = line.split(None, 1)[1].strip()
        triangles += faces
        if problems:
            bad += 1
            print(f"  BAD  {path.relative_to(root)}  ({problems} index errors)")
        if faces == 0:
            empty += 1
        if mtl and not (path.parent / mtl).is_file():
            missing_mtl += 1
            print(f"  MISSING MTL {path.relative_to(root)} -> {mtl}")

    glb_problems = 0
    for path in glb_files:
        try:
            from .gltf import read_glb
            document = read_glb(path)
            for mesh in document.gltf.get("meshes", []):
                for primitive in mesh["primitives"]:
                    document.accessor(primitive["attributes"]["POSITION"])
                    document.accessor(primitive["indices"])
            for index in range(len(document.gltf.get("animations", []))):
                document.animation_times(index)
        except Exception as exc:  # noqa: BLE001
            glb_problems += 1
            print(f"  BAD GLB {path.relative_to(root)}: {exc}")

    print()
    print(f"  OBJ checked      : {len(obj_files)}")
    print(f"  triangles        : {triangles}")
    print(f"  no geometry      : {empty}")
    print(f"  index errors     : {bad}")
    print(f"  missing .mtl     : {missing_mtl}")
    print(f"  GLB checked      : {len(glb_files)}")
    print(f"  GLB problems     : {glb_problems}")
    return 0 if not (bad or missing_mtl or glb_problems) else 1


def cmd_doctor(args) -> int:
    print(f"wc3studio {__version__}")
    print(f"python     : {sys.version.split()[0]} ({'64' if sys.maxsize > 2**32 else '32'}-bit)")
    ok = True

    try:
        storm = StormLib.instance()
        print(f"stormlib   : {storm.path}  (version {storm.version()})")
    except StormLibError as exc:
        ok = False
        print(f"stormlib   : MISSING - {exc}")

    for module in ("PIL", "numpy"):
        try:
            imported = __import__(module)
            print(f"{module:<11}: {getattr(imported, '__version__', 'present')}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"{module:<11}: MISSING ({exc})")

    try:
        import tkinter
        print(f"tkinter    : {tkinter.TkVersion}")
    except Exception as exc:  # noqa: BLE001
        print(f"tkinter    : unavailable ({exc}) - the GUI needs it")

    import shutil
    print(f"ffmpeg     : {shutil.which('ffmpeg') or 'not found (MP4 export disabled)'}")

    print("\nlooking for Warcraft III:")
    found = False
    for candidate in find_game_dirs():
        has = (candidate / "War3.mpq").is_file()
        print(f"  {'OK ' if has else '   '} {candidate}")
        found = found or has
    if not found:
        ok = False
        print("  no installation found - pass --game PATH")

    if found:
        try:
            session = Session(None)
            print(f"\narchives   : {', '.join(a.name for a in session.game.archives)}")
            print(f"models     : {len(session.models())}")
            session.close()
        except StormLibError as exc:
            ok = False
            print(f"\narchives   : FAILED - {exc}")

    print("\nresult:", "ready" if ok else "problems found")
    return 0 if ok else 1


def cmd_bundle(args) -> int:
    from .bundle import BundleOptions, build_bundle
    session = _open(args)
    requested = {part.strip().lower() for part in args.formats.split(",") if part.strip()}
    if "all" in requested:
        requested = {"models", "glb", "raw", "sounds", "icons", "tables", "ui"}
    options = BundleOptions(
        out=Path(args.out),
        formats=requested,
        previews=args.previews,
        preview_size=args.preview_size,
        preview_fps=args.preview_fps,
        preview_frames=args.preview_frames,
        preview_sequence=args.preview_sequence,
        index=not args.no_index,
        limit=args.limit,
        model_filter=args.filter or "",
        all_geosets=args.all_geosets,
        sequences=args.sequences.split(",") if args.sequences else None,
        archive=args.zip,
    )

    state = {"kind": ""}

    def progress(kind, done, total, message=""):
        if kind != state["kind"]:
            state["kind"] = kind
        filled = int(24 * done / max(total, 1))
        percent = int(100 * done / max(total, 1))
        sys.stdout.write(f"\r  {kind:<8} [{'#' * filled:-<24}] {done}/{total} {percent:>3}% ")
        sys.stdout.flush()
        if done >= total:
            sys.stdout.write("\n")

    print(f"bundle -> {Path(args.out).resolve()}")
    print(f"  formats : {', '.join(sorted(options.normalised().formats))}"
          + (" + previews" if options.previews else "")
          + (" + zip" if options.archive else ""))
    stats = build_bundle(session, options, progress)

    counts = stats["counts"]
    print("\nwritten:")
    for name in ("models", "glb", "raw", "sounds", "icons", "tables", "ui",
                 "previews", "indexed"):
        if counts.get(name):
            print(f"  {name:<9} {counts[name]:>6}")
    print(f"  size      {stats['bytes'] / 1048576:>10.1f} MiB")
    if stats.get("archive"):
        print(f"  archive   {Path(stats['archive']).resolve()} "
              f"({stats['archive_bytes'] / 1048576:.1f} MiB)")
    print(f"  manifest  {stats['manifest']}")
    if stats.get("index"):
        print(f"  index     {stats['index']}")
    if stats["errors"]:
        print(f"\n  errors: {len(stats['errors'])}")
        for error in stats["errors"][:10]:
            print("   ", error)
    return 0 if not stats["errors"] else 1


def cmd_audio(args) -> int:
    from .audio import extract, inventory, summary
    session = _open(args)
    if args.extract:
        out = Path(args.out or "audio")
        result = extract(session.game, out, category=args.category or None,
                         needle=args.filter or "", limit=args.limit)
        print(f"written {result['written']} of {result['count']} files "
              f"({result['bytes'] / 1048576:.1f} MiB) -> {out.resolve()}")
        if result["failed"]:
            print(f"failed: {result['failed']}")
        return 0
    files = inventory(session.game, category=args.category or None,
                      needle=args.filter or "", measure=args.durations)
    rows = summary(files)
    print(f"{'category':<11}{'files':>7}{'MiB':>9}{'hours':>8}")
    for category, data in sorted(rows.items(), key=lambda item: -item[1]["bytes"]):
        print(f"{category:<11}{data['files']:>7}{data['bytes'] / 1048576:>9.1f}"
              f"{data['seconds'] / 3600:>8.2f}")
    total_bytes = sum(data["bytes"] for data in rows.values())
    total_seconds = sum(data["seconds"] for data in rows.values())
    print(f"{'total':<11}{len(files):>7}{total_bytes / 1048576:>9.1f}"
          f"{total_seconds / 3600:>8.2f}")
    if not args.durations:
        print("\n(hours are measured with --durations: it reads every file)")
    if args.list:
        print()
        for item in files[: args.limit or 40]:
            print(f"  {item.size / 1024:>8.1f} KiB  {item.category:<10} {item.internal}")
    return 0


def cmd_icons(args) -> int:
    from .icons import KINDS, build_index, export
    from .tables import TableDatabase
    from .ui import build_contact_sheet
    session = _open(args)
    index = build_index(session.game, TableDatabase(session.game))
    kinds = [args.kind] if args.kind else list(KINDS)

    records = [record for record in index.records if record.kind in kinds]
    if args.filter:
        needle = args.filter.lower()
        records = [record for record in records
                   if needle in record.id.lower() or needle in record.name.lower()
                   or needle in record.icon.lower()]
    if args.limit:
        records = records[: args.limit]

    if args.out:
        out = Path(args.out)
        stats = export(index, session.resources, out, kinds=kinds, limit=args.limit)
        print(f"written {stats['written']} PNG ({stats['bytes'] / 1048576:.1f} MiB), "
              f"{stats['icons']} unique icons for {stats['records']} objects -> {out.resolve()}")
        if stats["failed"]:
            print(f"failed: {stats['failed']}")
        print(f"index: {stats['index']}")
    else:
        print(f"{'kind':<9}{'objects':>8}{'icons':>7}{'models':>8}{'МБ':>7}")
        for kind in kinds:
            items = [record for record in index.records if record.kind == kind]
            unique = {record.icon for record in items}
            models = sum(1 for record in items if record.model)
            size = sum(session.game.size(path) for path in unique) / 1048576
            print(f"{kind:<9}{len(items):>8}{len(unique):>7}{models:>8}{size:>7.1f}")
        total_icons = {record.icon for record in index.records if record.kind in kinds}
        print(f"{'total':<9}{len(records):>8}{len(total_icons):>7}"
              f"{sum(1 for r in records if r.model):>8}")
        print("\n--out DIR — выгрузить PNG, --sheet FILE — контактный лист")

    if args.sheet:
        images, labels = [], []
        for record in records[: args.sheet_tiles]:
            image = session.resources.image(record.icon)
            if image is not None:
                images.append(image)
                labels.append(f"{record.id}")
        if images:
            sheet = build_contact_sheet(images, labels, tile=args.tile,
                                        columns=args.columns)
            target = Path(args.sheet)
            target.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(target)
            print(f"contact sheet -> {target.resolve()} "
                  f"({sheet.width}x{sheet.height}, {len(images)} иконок)")

    if args.details:
        for record in records[: args.limit or 20]:
            print(f"  {record.kind:<8} {record.id:<8} {record.icon_name:<28} {record.name}")
    return 0


def cmd_icon(args) -> int:
    from .icons import build_index
    from .tables import TableDatabase
    session = _open(args)
    index = build_index(session.game, TableDatabase(session.game))
    record = None
    for wanted in args.what:
        record = index.by_id(wanted) or index.for_model(wanted)
        if record is None:
            print(f"error: no icon for {wanted!r}", file=sys.stderr)
            return 3
        image = session.resources.image(record.icon)
        print(f"{record.kind:<8} {record.id:<8} {record.name}")
        print(f"   icon    : {record.icon}" + (f"  ({image.width}x{image.height})"
                                              if image else ""))
        if record.disabled:
            print(f"   disabled: {record.disabled}")
        if record.model:
            print(f"   model   : {record.model}")
        if record.buttonpos:
            print(f"   button  : {record.buttonpos}")
        if args.out:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            if image is not None:
                image.save(target)
                print(f"   written : {target.resolve()}")
    return 0


def cmd_agent(args) -> int:
    from .agent import dispatch_cli
    argv = list(args.request or [])
    return dispatch_cli(argv)


def cmd_mcp(args) -> int:
    from .agent import serve_mcp
    return serve_mcp()


def cmd_gui(args) -> int:
    framework = (getattr(args, "framework", "") or "auto").lower()
    if framework in ("auto", "qt"):
        try:
            from .gui_qt import run as run_qt
        except ImportError as exc:
            if framework == "qt":
                print(f"error: the Qt window needs PySide6 ({exc})", file=sys.stderr)
                return 2
        else:
            return run_qt(game_dir=args.game, model=args.model,
                          sequence=args.sequence) or 0
    try:
        from .gui import run
    except ImportError as exc:  # pragma: no cover
        print(f"error: the GUI needs tkinter ({exc})", file=sys.stderr)
        return 2
    run(game_dir=args.game, model=args.model, sequence=args.sequence, tab=args.tab)
    return 0


# --------------------------------------------------------------------------- data tables

def _render_table(columns, rows, max_width: int = 26, limit: int = 0) -> str:
    """Plain-text table that survives Russian text and long cells."""
    rows = rows[:limit] if limit else rows

    def clip(text) -> str:
        text = str(text).replace("\n", " ")
        return text if len(text) <= max_width else text[: max_width - 1] + "…"

    header = [clip(column) for column in columns]
    body = [[clip(cell) for cell in row] for row in rows]
    widths = [max([len(header[i])] + [len(row[i]) for row in body]) for i in range(len(header))]
    lines = ["  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(header))]
    lines.append("  " + "  ".join("-" * width for width in widths))
    for row in body:
        lines.append("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def cmd_tables(args) -> int:
    from .tables import TableDatabase
    session = _open(args)
    db = TableDatabase(session.game)
    summary = db.tables_summary()
    if args.filter:
        needle = args.filter.lower()
        summary = [item for item in summary if needle in item[0].lower()]
    summary.sort(key=lambda item: item[0].lower())
    if args.limit:
        summary = summary[: args.limit]
    print(f"{'kind':<5}{'rows':>7}{'cols':>6}  table")
    for name, kind, rows, columns in summary:
        print(f"{kind:<5}{rows:>7}{columns:>6}  {name}")
    print(f"\n{len(summary)} tables")
    return 0


def cmd_table(args) -> int:
    from .tables import TableDatabase
    session = _open(args)
    db = TableDatabase(session.game)
    try:
        resolved = db._match_table_name(args.name)
    except (FileNotFoundError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    table = db.load(resolved)
    print(f"{resolved}: {table.row_count} rows x {table.column_count} columns ({table.kind})",
          file=sys.stderr)

    for condition in args.where or []:
        if "=" not in condition:
            print(f"error: --where needs column=value, got {condition!r}", file=sys.stderr)
            return 2
        column, value = condition.split("=", 1)
        try:
            table = table.where(**{column.strip(): value.strip()})
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.columns:
        try:
            table = table.select([name.strip() for name in args.columns.split(",")])
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.format == "csv":
        payload = table.to_csv()
    elif args.format == "json":
        payload = table.to_json()
    else:
        print(_render_table(table.columns, table.rows, limit=args.limit))
        return 0

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        print(f"written {path.resolve()} ({len(payload):,} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(payload)
    return 0


def cmd_units(args) -> int:
    from .tables import TableDatabase
    session = _open(args)
    db = TableDatabase(session.game)
    table = db.units()
    if args.filter:
        needle = args.filter.lower()

        def keep(row, _record):
            return any(needle in str(cell).lower() for cell in row)

        table = table.filter(keep)
    if args.columns:
        table = table.select([name.strip() for name in args.columns.split(",")])
    else:
        table = table.select([name for name in ("id", "name", "race", "level", "hp", "armor",
                                                "damage", "dps", "gold", "lumber", "food",
                                                "buildtime", "move_type")
                              if name in table.columns])
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(table.to_csv(), encoding="utf-8")
        print(f"written {path.resolve()} ({table.row_count} rows)")
        return 0
    print(f"{table.row_count} units")
    print(_render_table(table.columns, table.rows, max_width=18, limit=args.limit))
    return 0


def cmd_strings(args) -> int:
    import csv as _csv
    from .tables import TableDatabase
    from .ui import UiLibrary
    session = _open(args)
    rows = []
    if args.kind in ("ui", "all"):
        for key, value in sorted(UiLibrary(session.game).strings().items()):
            rows.append(["ui", key, value])
    if args.kind in ("unit", "all"):
        for unit_id, record in sorted(TableDatabase(session.game).strings.items()):
            for key in ("Name", "Ubertip", "Hotkey", "Propernames"):
                value = record.get(key)
                if value:
                    rows.append(["unit", f"{unit_id}.{key}", value])
    if args.search:
        needle = args.search.lower()
        rows = [row for row in rows if needle in row[1].lower() or needle in row[2].lower()]
    if args.limit:
        rows = rows[: args.limit]
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = _csv.writer(handle)
            writer.writerow(["kind", "key", "value"])
            writer.writerows(rows)
        print(f"written {path.resolve()} ({len(rows)} strings)")
        return 0
    print(_render_table(["kind", "key", "value"], rows, max_width=60, limit=args.limit or 40))
    print(f"\n{len(rows)} strings")
    return 0


# --------------------------------------------------------------------------- interface

def cmd_ui(args) -> int:
    from .ui import UiLibrary
    session = _open(args)
    lib = UiLibrary(session.game)
    if args.frames:
        frames = lib.frames(args.filter, limit=args.limit)
        print(_render_table(["kind", "name", "source"],
                            [[frame.kind, frame.name, frame.source] for frame in frames],
                            max_width=44))
        print(f"\n{len(frames)} frames (of {len(lib.index())} unique names)")
        return 0
    rows = []
    for path in lib.documents(args.filter):
        try:
            document = lib.load(path)
        except FileNotFoundError:
            continue
        rows.append([path, len(document.all_frames()), len(document.includes),
                     len(document.strings)])
    rows.sort(key=lambda row: row[0].lower())
    if args.limit:
        rows = rows[: args.limit]
    print(_render_table(["fdf document", "frames", "includes", "strings"], rows, max_width=52))
    print(f"\n{len(lib.documents())} documents, {len(lib.index())} unique frame names, "
          f"{len(lib.strings())} localised strings")
    return 0


def cmd_ui_frame(args) -> int:
    from .ui import UiLibrary
    session = _open(args)
    lib = UiLibrary(session.game)
    for name in args.name:
        try:
            details = lib.resolve(name)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"\n=== {details['name']}  ({details['kind']})")
        print(f"  source   : {details['source']}")
        print(f"  inherits : {' <- '.join(details['chain'])}")
        print(f"  textures : {len(details['textures'])} paths, "
              f"{len(details['decorated'])} decorated names")
        for reference, resolved in details["textures"]:
            if resolved:
                print(f"      ok  {reference}")
            else:
                print(f"      ??  {reference}   (not in this install)")
        if details["decorated"]:
            shown = ", ".join(details["decorated"][:6])
            print(f"      decorated: {shown}")
        print(f"  properties ({len(details['properties'])}):")
        for key, value in details["properties"].items():
            origin = details["origin"].get(key, "")
            mark = f"  (from {origin})" if origin and origin.lower() != details["name"].lower() else ""
            print(f"      {key} = {value}{mark}")
    return 0


def cmd_ui_textures(args) -> int:
    from .ui import UiLibrary
    session = _open(args)
    items = UiLibrary(session.game).assets(args.filter)
    rows = [[item["path"], f"{item['size'] / 1024:.1f}"] for item in items[: args.limit]]
    print(_render_table(["ui image", "KiB"], rows, max_width=64))
    print(f"\n{len(items)} images, {sum(item['size'] for item in items) / 1048576:.1f} MiB")
    return 0


def cmd_ui_assets(args) -> int:
    from .ui import UiLibrary, build_contact_sheet
    session = _open(args)
    lib = UiLibrary(session.game)
    out = Path(args.out)
    result = lib.dump_assets(session.resources, out, needle=args.filter, limit=args.limit)
    print(f"converted {result['written']} of {result['count']} UI images "
          f"({result['bytes'] / 1048576:.1f} MiB) -> {out.resolve()}")
    if result["failed"]:
        print(f"failed: {result['failed']}")

    if args.sheet:
        items = lib.assets(args.filter)
        if args.limit:
            items = items[: args.limit]
        images, labels = [], []
        for item in items[: args.sheet_tiles]:
            image = session.resources.image(item["path"])
            if image is not None:
                images.append(image)
                labels.append(item["name"])
        sheet = build_contact_sheet(images, labels, tile=args.tile, columns=args.columns)
        sheet_path = Path(args.sheet)
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(sheet_path)
        print(f"contact sheet -> {sheet_path.resolve()} "
              f"({sheet.width}x{sheet.height}, {len(images)} tiles)")
    return 0


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wc3studio",
        description="Inspect, extract, convert and animate Warcraft III assets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples")[-1] if "Examples" in __doc__ else None)
    parser.add_argument("--version", action="version", version=f"wc3studio {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_game(sub):
        sub.add_argument("--game", help="Warcraft III folder (default: autodetect)")

    p = subparsers.add_parser("archives", help="list the game archives and their statistics")
    add_game(p)
    p.set_defaults(func=cmd_archives)

    p = subparsers.add_parser("list", help="search internal file names")
    add_game(p)
    p.add_argument("--filter", help="substring or glob, e.g. 'footman' or 'units\\\\human\\\\*'")
    p.add_argument("--ext", help="only these extensions, e.g. mdx,blp")
    p.add_argument("--models-only", action="store_true", help="only .mdx/.mdl")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_list)

    p = subparsers.add_parser("info", help="summarise models: geosets, textures, animations")
    add_game(p)
    p.add_argument("model", nargs="+", help="internal path, partial path or file name")
    p.set_defaults(func=cmd_info)

    p = subparsers.add_parser("extract", help="extract raw internal files")
    add_game(p)
    p.add_argument("--out", required=True)
    p.add_argument("--pattern", default="*.mdx", help="comma separated globs (default *.mdx)")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_extract)

    p = subparsers.add_parser("convert", help="convert models to OBJ and/or GLB")
    add_game(p)
    p.add_argument("--model", action="append", default=[], help="model path (repeatable)")
    p.add_argument("--all", action="store_true", help="convert every model in the archives")
    p.add_argument("--filter", help="with --all, keep names containing this")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", required=True)
    p.add_argument("--formats", default="obj", help="obj, glb or obj,glb")
    p.add_argument("--tree", action="store_true", help="mirror the internal folder structure")
    p.add_argument("--all-geosets", action="store_true", help="include every ever-visible geoset")
    p.add_argument("--sequences", help="with glb: comma separated animation names (default all)")
    p.set_defaults(func=cmd_convert)

    p = subparsers.add_parser("render", help="render one still image")
    add_game(p)
    p.add_argument("--model", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--size", type=int, default=768)
    p.add_argument("--yaw", type=float, default=35.0)
    p.add_argument("--pitch", type=float, default=15.0)
    p.add_argument("--zoom", type=float, default=1.0)
    p.add_argument("--sequence", help="pose the model inside this animation")
    p.add_argument("--time", type=float, default=0.0, help="0..1 position inside the sequence")
    p.add_argument("--all-geosets", action="store_true")
    p.set_defaults(func=cmd_render)

    p = subparsers.add_parser("animate", help="render an animation to GIF/MP4")
    add_game(p)
    p.add_argument("--model", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--sequence", default="Walk",
                   help="name or playlist, e.g. 'Stand,Walk,Walk,Death'")
    p.add_argument("--fps", type=float, default=20.0)
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--frames", type=int, default=0, help="frames per sequence (default: from fps)")
    p.add_argument("--yaw", type=float, default=35.0)
    p.add_argument("--pitch", type=float, default=15.0)
    p.add_argument("--orbit", type=float, default=0.0, help="degrees of camera yaw per second")
    p.add_argument("--zoom", type=float, default=1.0)
    p.add_argument("--png", action="store_true", help="also keep the frames as PNG")
    p.add_argument("--preview", action="store_true", help="keep the frames used for the MP4")
    p.add_argument("--mp4", action="store_true", help="also write MP4 (needs ffmpeg)")
    p.add_argument("--all-geosets", action="store_true")
    p.set_defaults(func=cmd_animate)

    p = subparsers.add_parser("gallery", help="render a contact sheet of well-known models")
    add_game(p)
    p.add_argument("--out", default="gallery")
    p.add_argument("--tile", type=int, default=320)
    p.add_argument("--columns", type=int, default=4)
    p.add_argument("--yaw", type=float, default=35.0)
    p.add_argument("--pitch", type=float, default=15.0)
    p.add_argument("--model", action="append", default=[], help="use these models instead of the preset")
    p.set_defaults(func=cmd_gallery)

    p = subparsers.add_parser("validate", help="check an export folder")
    p.add_argument("folder")
    p.set_defaults(func=cmd_validate)

    p = subparsers.add_parser("doctor", help="check the environment and find the game")
    p.set_defaults(func=cmd_doctor)

    # -- data tables ------------------------------------------------------
    p = subparsers.add_parser("tables", help="list the game's data tables (SLK/TXT)")
    add_game(p)
    p.add_argument("--filter", help="only names containing this")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_tables)

    p = subparsers.add_parser("table", help="print or export one data table")
    add_game(p)
    p.add_argument("name", help="table name or path, e.g. UnitBalance or Units\\UnitData.slk")
    p.add_argument("--columns", help="comma separated subset of columns")
    p.add_argument("--where", action="append", help="column=value filter (repeatable)")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--format", default="table", choices=["table", "csv", "json"])
    p.add_argument("--out", help="write the CSV/JSON here instead of stdout")
    p.set_defaults(func=cmd_table)

    p = subparsers.add_parser("units", help="joined unit database with localised names")
    add_game(p)
    p.add_argument("--filter", help="substring to match in any cell")
    p.add_argument("--columns", help="comma separated subset of columns")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--out", help="write the whole table as CSV")
    p.set_defaults(func=cmd_units)

    p = subparsers.add_parser("strings", help="localised text: UI labels and unit names")
    add_game(p)
    p.add_argument("--kind", default="all", choices=["ui", "unit", "all"])
    p.add_argument("--search", help="only strings containing this")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", help="write everything as CSV")
    p.set_defaults(func=cmd_strings)

    # -- interface --------------------------------------------------------
    p = subparsers.add_parser("ui", help="list FDF interface documents or frames")
    add_game(p)
    p.add_argument("--filter", help="only paths/names containing this")
    p.add_argument("--frames", action="store_true", help="list frame names instead of documents")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_ui)

    p = subparsers.add_parser("ui-frame", help="properties of a frame, including inherited ones")
    add_game(p)
    p.add_argument("name", nargs="+", help="frame name, e.g. StandardHeavyBackdropTemplate")
    p.set_defaults(func=cmd_ui_frame)

    p = subparsers.add_parser("ui-textures", help="list the interface artwork")
    add_game(p)
    p.add_argument("--filter", help="only paths containing this")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_ui_textures)

    p = subparsers.add_parser("ui-assets", help="convert interface artwork to PNG (+ contact sheet)")
    add_game(p)
    p.add_argument("--out", required=True)
    p.add_argument("--filter", help="only paths containing this, e.g. Widgets or Buttons")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--sheet", help="also write a contact sheet PNG here")
    p.add_argument("--sheet-tiles", type=int, default=48)
    p.add_argument("--tile", type=int, default=128)
    p.add_argument("--columns", type=int, default=8)
    p.set_defaults(func=cmd_ui_assets)

    p = subparsers.add_parser("bundle", help="export everything at once into a folder and/or zip")
    add_game(p)
    p.add_argument("--out", required=True, help="output folder")
    p.add_argument("--formats", default="models,glb,sounds",
                   help="comma separated: models (OBJ+MTL+PNG), glb, raw (.mdx), sounds, "
                        "tables (CSV), ui (PNG), or all")
    p.add_argument("--zip", action="store_true", help="also write <out>.zip")
    p.add_argument("--no-index", action="store_true",
                   help="skip models.json (per-model list of files, textures, sounds, animations)")
    p.add_argument("--previews", action="store_true",
                   help="render a short GIF per model (slow: several frames each)")
    p.add_argument("--preview-size", type=int, default=192)
    p.add_argument("--preview-fps", type=int, default=10)
    p.add_argument("--preview-frames", type=int, default=10)
    p.add_argument("--preview-sequence", default="Stand",
                   help="which animation the preview shows (falls back to the first)")
    p.add_argument("--sequences", help="with glb: restrict the exported animations")
    p.add_argument("--filter", help="only model paths containing this")
    p.add_argument("--limit", type=int, default=0, help="only the first N models (for a quick try)")
    p.add_argument("--all-geosets", action="store_true")
    p.set_defaults(func=cmd_bundle)

    p = subparsers.add_parser("audio", help="inventory or extract the game's audio")
    add_game(p)
    p.add_argument("--category", default="",
                   choices=["", "music", "ambient", "dialogue", "abilities", "units",
                            "ui", "other"])
    p.add_argument("--filter", help="only paths containing this")
    p.add_argument("--durations", action="store_true",
                   help="read every file to measure play time (slower)")
    p.add_argument("--list", action="store_true", help="also print the files")
    p.add_argument("--extract", action="store_true", help="copy the files out")
    p.add_argument("--out", help="folder for --extract")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_audio)

    p = subparsers.add_parser("icons", help="command-button icons for units, abilities, upgrades, items")
    add_game(p)
    p.add_argument("--kind", default="", choices=["", "unit", "ability", "upgrade", "item"])
    p.add_argument("--filter", help="only ids/names/icons containing this")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", help="write the icons as PNG here")
    p.add_argument("--sheet", help="write a contact sheet here")
    p.add_argument("--sheet-tiles", type=int, default=48)
    p.add_argument("--tile", type=int, default=96)
    p.add_argument("--columns", type=int, default=8)
    p.add_argument("--details", action="store_true", help="print the records")
    p.set_defaults(func=cmd_icons)

    p = subparsers.add_parser("icon", help="icon of one unit, ability, upgrade or item")
    add_game(p)
    p.add_argument("what", nargs="+", help="id (hfoo, AHhb) or a model path")
    p.add_argument("--out", help="save the icon as PNG")
    p.set_defaults(func=cmd_icon)

    p = subparsers.add_parser("agent", help="JSON API for AI agents (request on stdin or --call)")
    p.add_argument("request", nargs=argparse.REMAINDER,
                   help="--capabilities, --call OP [--params JSON], --batch FILE, --pretty, "
                        "[--game PATH]")
    p.set_defaults(func=cmd_agent)

    p = subparsers.add_parser("mcp", help="MCP server on stdio (tools for AI agents)")
    p.set_defaults(func=cmd_mcp)

    p = subparsers.add_parser("gui", help="open the graphical interface")
    add_game(p)
    p.add_argument("--model", help="model to select at startup")
    p.add_argument("--sequence", help="animation to select at startup")
    p.add_argument("--tab", help="tab to open at startup: Model, Animations, Textures, Data, UI")
    p.add_argument("--framework", default="auto", choices=["auto", "qt", "tk"],
                   help="qt = PySide6 + OpenGL (smooth playback), tk = the classic window")
    p.set_defaults(func=cmd_gui)

    return parser


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # ``agent`` and ``mcp`` take free-form arguments, so they bypass argparse: an agent
    # should never see "unrecognized arguments", only JSON.
    if raw and raw[0] == "agent":
        from .agent import dispatch_cli
        return dispatch_cli(raw[1:])
    if raw and raw[0] == "mcp":
        from .agent import serve_mcp
        return serve_mcp()
    parser = build_parser()
    args = parser.parse_args(raw)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except StormLibError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
