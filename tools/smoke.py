"""End-to-end smoke test of the wc3studio library on the real game archives."""
import sys
import traceback

sys.path.insert(0, r"C:\Users\zxclu\Downloads\ai_tech\wc3studio")

import numpy as np

from wc3studio import Game, Resources, parse_mdx, render, export_obj, export_glb, read_glb
from wc3studio.anim import Animator

MODEL = r"Units\Critters\Sheep\Sheep.mdx"

try:
    game = Game.autodetect()
    print("game:", game)
    print("models:", len(game.models()))

    data = game.read(MODEL)
    print("model bytes:", len(data))
    model = parse_mdx(data)
    print("sequences:", [(s["name"], s["start"], s["end"]) for s in model.sequences])
    print("nodes:", len(model.nodes), "geosets:", len(model.geosets),
          "materials:", len(model.materials), "textures:", len(model.textures))

    resources = Resources(game)
    img = render(model, resources, size=256, shadow=True)
    print("render:", img.size, img.mode)

    stats = export_obj(model, r"C:\Users\zxclu\Downloads\ai_tech\wc3studio\build\smoke\sheep.obj", resources)
    print("obj:", dict(stats))

    glb_stats = export_glb(model, r"C:\Users\zxclu\Downloads\ai_tech\wc3studio\build\smoke\sheep.glb", resources)
    print("glb:", dict(glb_stats))

    glb = read_glb(r"C:\Users\zxclu\Downloads\ai_tech\wc3studio\build\smoke\sheep.glb")
    print("glb read back: nodes", len(glb.gltf["nodes"]), "meshes", len(glb.gltf["meshes"]),
          "anims", len(glb.gltf.get("animations", [])))

    # compare the GLB skinning against the MDX path at three times of the walk
    animator = Animator(model)
    walk = model.sequence("Walk")
    anim_index = next(i for i, a in enumerate(glb.gltf["animations"]) if a["name"].startswith("Walk"))
    duration = walk["end"] - walk["start"]
    print(f"\nwalk: {walk['start']}-{walk['end']}  (glb anim {anim_index})")
    for fraction in (0.0, 0.33, 0.66):
        frame = walk["start"] + duration * fraction
        time = duration * fraction / 1000.0

        mdx_matrices = animator.node_matrices(frame, frame - walk["start"])
        mdx_points = animator.skin(model.geosets[0], mdx_matrices)

        glb_world = glb.posed_world_matrices(anim_index, time)
        mesh_node = next(i for i, n in enumerate(glb.gltf["nodes"]) if "mesh" in n)
        glb_points = glb.skinned_positions(mesh_node, glb_world)

        delta = np.linalg.norm(mdx_points - glb_points, axis=1)
        print(f"  t={time:.3f}s  max|mdx-glb| = {delta.max():.4f}  mean = {delta.mean():.4f}")
except Exception:
    traceback.print_exc()
    sys.exit(1)
