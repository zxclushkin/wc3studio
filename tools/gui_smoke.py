"""Headless-ish GUI test: build the window, drive the worker, verify a preview arrives."""
import sys
import time

sys.path.insert(0, r"C:\Users\zxclu\Downloads\ai_tech\wc3studio")

from wc3studio.gui import App

MODEL = r"Units\Critters\Sheep\Sheep.mdx"


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


app = App()
pump(app, 3.0)
print("status      :", app.status_var.get())
print("title       :", app.title())
print("tree roots  :", len(app.tree.get_children()))
assert app.session is not None, "session did not open"

# load a model the way the tree selection would
app.model = app.session.load(MODEL)
app.model_path = MODEL
app._update_info()
app._update_sequences()
app._request_still()
pump(app, 6.0)

print("model       :", app.model.name)
print("sequences   :", app.sequence_box.cget("values")[:4], "...")
print("playlist    :", app.playlist)
print("textures    :", app.texture_list.size())
print("preview set :", app._photo is not None)
print("status      :", app.status_var.get())
assert app._photo is not None, "no preview image arrived"

# exercise the animation playback path
app.sequence_var.set("Walk - 1")
app._on_sequence_change()
app._toggle_play()
pump(app, 6.0)
print("playing     :", app.playing, "| frames buffered:", len(app._frame_buffer))
app._toggle_play()
pump(app, 1.0)
assert app._photo is not None

# texture tab
app.texture_list.selection_set(0)
app._show_texture()
pump(app, 0.5)
print("texture tab :", app.texture_preview.cget("text") or "image shown")

# info summary sanity
info = app.session.summary(MODEL)
print("summary     :", info["triangles"], "tris,", len(info["sequences"]), "animations")

app._on_close()
print("\nGUI smoke test OK")
