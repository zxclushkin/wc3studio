"""wc3studio - inspect, extract, convert and animate Warcraft III assets.

Quick start::

    from wc3studio import Game, Resources, parse_mdx, render, export_obj, export_glb

    game = Game.autodetect()
    resources = Resources(game)
    model = parse_mdx(game.read(r"Units\\Critters\\Sheep\\Sheep.mdx"))
    export_obj(model, "sheep.obj", resources)
    export_glb(model, "sheep.glb", resources)     # skeleton + every animation

Data tables, the interface, and a one-call bundle::

    from wc3studio import TableDatabase, UiLibrary, BundleOptions, build_bundle
    from wc3studio.pipeline import Session

    units = TableDatabase(game).units()           # gameplay numbers, joined
    ui = UiLibrary(game)                          # FDF frame definitions
    ui.resolve("StandardHeavyBackdropTemplate")

    build_bundle(Session(game.root),
                 BundleOptions(out="wc3_assets", formats={"models", "glb", "raw", "tables"},
                               archive=True))     # folder + zip + manifest

Or from the command line::

    python -m wc3studio --help
    python -m wc3studio bundle --out wc3_assets --formats models,glb,raw,tables --zip
    python -m wc3studio gui
"""
from __future__ import annotations

__version__ = "1.6.1"

from .agent import Api, capabilities
from .anim import Animator
from .blp import decode as decode_blp
from .bundle import BundleOptions, build_bundle
from .gltf import Glb, export_glb, read_glb
from .icons import IconIndex, IconRecord
from .mdx import Model, MdxError, parse as parse_mdx
from .objexport import export_obj
from .render import fit_camera, render
from .resources import Resources, pick_layer
from .stormlib import Archive, Game, StormLib, StormLibError, find_game_dirs
from .tables import Table, TableDatabase, parse_ini, parse_sylk
from .ui import FdfDocument, Frame, UiLibrary, parse_fdf

__all__ = [
    "__version__",
    "Animator", "Api", "Archive", "BundleOptions", "FdfDocument", "Frame", "Game", "Glb",
    "IconIndex", "IconRecord",
    "MdxError", "Model", "Resources", "StormLib", "StormLibError", "Table",
    "TableDatabase", "UiLibrary", "build_bundle", "capabilities", "decode_blp", "export_glb",
    "export_obj", "find_game_dirs", "fit_camera", "parse_fdf", "parse_ini",
    "parse_mdx", "parse_sylk", "pick_layer", "read_glb", "render",
]
