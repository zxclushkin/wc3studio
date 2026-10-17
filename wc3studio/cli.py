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


def cmd_gui(args) -> int:
    try:
        from .gui import run
    except ImportError as exc:  # pragma: no cover
        print(f"error: the GUI needs tkinter ({exc})", file=sys.stderr)
        return 2
    run(game_dir=args.game, model=args.model, sequence=args.sequence)
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

    p = subparsers.add_parser("gui", help="open the graphical interface")
    add_game(p)
    p.add_argument("--model", help="model to select at startup")
    p.add_argument("--sequence", help="animation to select at startup")
    p.set_defaults(func=cmd_gui)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
