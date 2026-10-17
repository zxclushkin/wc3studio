"""Independent verification of the exported GLB using trimesh (a third-party loader)."""
import sys

import numpy as np
import trimesh

sys.path.insert(0, r"C:\Users\zxclu\Downloads\ai_tech\wc3studio")

from wc3studio import Game, Resources, parse_mdx, export_glb
from wc3studio.anim import Animator

path = r"C:\Users\zxclu\Downloads\ai_tech\wc3studio\docs\anim\sheep_check.glb"
game = Game.autodetect()
model = parse_mdx(game.read(r"Units\Critters\Sheep\Sheep.mdx"))
stats = export_glb(model, path, Resources(game))
print("exported:", dict(stats))

scene = trimesh.load(path)
print("\ntrimesh loaded:", type(scene).__name__)
geometries = list(scene.geometry.values()) if hasattr(scene, "geometry") else [scene]
mesh = geometries[0]
print("  geometry   :", mesh.vertices.shape, mesh.faces.shape)
print("  bounds     :", np.round(mesh.bounds, 2).tolist())

# the mesh should sit where the MDX model sits
mdx_vertices = np.asarray(model.geosets[0].vertices, dtype=np.float64).reshape(-1, 3)
print("  mdx bounds :", np.round([mdx_vertices.min(axis=0), mdx_vertices.max(axis=0)], 2).tolist())
deviation = np.abs(np.sort(mesh.bounds.flatten()) - np.sort(np.asarray(
    [mdx_vertices.min(axis=0), mdx_vertices.max(axis=0)]).flatten())).max()
print("  bounds delta vs MDX:", round(float(deviation), 4))

# trimesh reads the skin/animations from the glTF header
with open(path, "rb") as handle:
    header = handle.read(12)
print("\n  glb header :", header[:4], "version", int.from_bytes(header[4:8], "little"),
      "bytes", int.from_bytes(header[8:12], "little"))

print("\nOK: an independent glTF loader reads the file" if deviation < 0.01 else "\nMISMATCH")
