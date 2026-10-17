"""wc3studio - inspect, extract, convert and animate Warcraft III assets.

Quick start::

    from wc3studio import Game, Resources, parse_mdx, render, export_obj, export_glb

    game = Game.autodetect()
    resources = Resources(game)
    model = parse_mdx(game.read(r"Units\\Critters\\Sheep\\Sheep.mdx"))
    export_obj(model, "sheep.obj", resources)
    export_glb(model, "sheep.glb", resources)

Or from the command line::

    python -m wc3studio --help
    python -m wc3studio gui
"""
from __future__ import annotations

__version__ = "1.0.0"

from .anim import Animator
from .blp import decode as decode_blp
from .gltf import Glb, export_glb, read_glb
from .mdx import Model, MdxError, parse as parse_mdx
from .objexport import export_obj
from .render import fit_camera, render
from .resources import Resources, pick_layer
from .stormlib import Archive, Game, StormLib, StormLibError, find_game_dirs

__all__ = [
    "__version__",
    "Animator", "Archive", "Game", "Glb", "MdxError", "Model", "Resources", "StormLib",
    "StormLibError", "decode_blp", "export_glb", "export_obj", "find_game_dirs",
    "fit_camera", "parse_mdx", "pick_layer", "read_glb", "render",
]
