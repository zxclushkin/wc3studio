"""Tkinter front end: browse the archives, preview models, play animations, export.

Rendering happens on a worker thread (the software renderer is pure Python) and every
result carries a request id, so stale frames are dropped instead of flickering.
"""
from __future__ import annotations

import collections
import os
import queue
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import __version__
from .pipeline import Session, render_animation, write_gif
from .stormlib import StormLibError, find_game_dirs

BG = "#14161c"
PANEL = "#1c1f27"
FG = "#e2e6ee"
ACCENT = "#4d8dff"
DIM = "#8b93a7"


# --------------------------------------------------------------------------- worker

class RenderWorker(threading.Thread):
    """Renders stills and animation frames off the UI thread."""

    def __init__(self, session: Session, results: "queue.Queue"):
        super().__init__(daemon=True)
        self.session = session
        self.results = results
        self.jobs: "queue.Queue" = queue.Queue(maxsize=1)
        self.stop_flag = threading.Event()

    def submit(self, job: dict):
        """Keep only the newest request: rendering is expensive and order does not matter."""
        try:
            while True:
                self.jobs.get_nowait()
        except queue.Empty:
            pass
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            pass

    def shutdown(self):
        self.stop_flag.set()
        self.submit({"type": "stop"})

    def run(self):
        while not self.stop_flag.is_set():
            try:
                job = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle(job)
            except Exception:  # noqa: BLE001 - never kill the UI over one bad model
                self.results.put(("error", job.get("anchor", 0), traceback.format_exc()))

    def _handle(self, job: dict):
        kind = job.get("type")
        if kind == "stop":
            return
        session: Session = job["session"]
        anchor = job.get("anchor", 0)

        if kind == "still":
            image = session.render(
                job["model"], size=job["size"], yaw=job["yaw"], pitch=job["pitch"],
                zoom=job["zoom"], frame=job.get("frame"), local_time=job.get("local_time"),
                all_geosets=job.get("all_geosets", False))
            self.results.put(("still", anchor, image))
            return

        if kind == "play":
            model = session.load(job["model"])
            animator = session.animator_for(model)
            from .render import fit_camera
            from .pipeline import playlist_frames

            plan, entries = playlist_frames(model, job["playlist"], job["fps"], 0)
            camera = fit_camera(model, animator, [(f, lt) for f, lt, _ in entries])
            for frame, local, _name in entries:
                if self.stop_flag.is_set() or not self.jobs.empty():
                    return  # a newer request arrived
                image = session.render(
                    model, size=job["size"], yaw=job["yaw"], pitch=job["pitch"],
                    zoom=job["zoom"], camera=camera, frame=frame, local_time=local,
                    animator=animator, all_geosets=job.get("all_geosets", False))
                self.results.put(("frame", anchor, image))


# --------------------------------------------------------------------------- app

class App(tk.Tk):
    def __init__(self, game_dir: str | None = None, model: str | None = None,
                 sequence: str | None = None):
        super().__init__()
        self.title("wc3studio - Warcraft III asset browser")
        self.geometry("1280x820")
        self.minsize(1000, 640)
        self.configure(bg=BG)

        self.initial_model = model
        self.initial_sequence = sequence
        self.session: Session | None = None
        self.model_path: str | None = None
        self.model = None
        self.yaw, self.pitch, self.zoom = 35.0, 15.0, 1.0
        self.playing = False
        self.playlist: list[str] = []
        self.anchor = 0
        self._photo = None
        self._thumb_refs: list = []
        self.textures: list = []
        self._frame_buffer: collections.deque = collections.deque(maxlen=2)
        self._last_frame_time = 0.0
        self._drag = None
        self._resize_job = None
        self._tree_paths: dict[str, str] = {}
        self._folder_tree: dict = {}
        self._busy_count = 0

        self.results: "queue.Queue" = queue.Queue()
        self.worker: RenderWorker | None = None

        self._build_menu()
        self._build_layout()
        self.after(25, self._poll)

        self.bind("<space>", lambda _e: self._toggle_play())
        self.bind("<Left>", lambda _e: self._orbit(-10))
        self.bind("<Right>", lambda _e: self._orbit(10))
        self.bind("<Up>", lambda _e: self._tilt(5))
        self.bind("<Down>", lambda _e: self._tilt(-5))

        self.after(50, lambda: self._open_game(game_dir))

    # -- construction -----------------------------------------------------
    def _apply_theme(self):
        """Dark ttk theme; the clam theme is the only stock one that honours colours."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Dim.TLabel", background=BG, foreground=DIM)
        style.configure("TCheckbutton", background=BG, foreground=FG,
                        indicatorcolor=PANEL, focuscolor=BG)
        style.map("TCheckbutton", background=[("active", BG)],
                  indicatorcolor=[("selected", ACCENT)])

        style.configure("TButton", background=PANEL, foreground=FG, padding=(6, 3),
                        bordercolor="#33384a", lightcolor=PANEL, darkcolor=PANEL,
                        focuscolor=BG)
        style.map("TButton",
                  background=[("pressed", ACCENT), ("active", "#2a3040")],
                  foreground=[("disabled", DIM)])

        for name in ("TEntry", "TSpinbox"):
            style.configure(name, fieldbackground=PANEL, background=PANEL, foreground=FG,
                            insertcolor=FG, bordercolor="#33384a", arrowcolor=FG,
                            lightcolor=PANEL, darkcolor=PANEL)
            style.map(name, fieldbackground=[("disabled", BG)], foreground=[("disabled", DIM)])

        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG,
                        arrowcolor=FG, bordercolor="#33384a", selectbackground=PANEL,
                        selectforeground=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL), ("disabled", BG)],
                  foreground=[("disabled", DIM)],
                  background=[("readonly", PANEL), ("active", "#2a3040")])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")

        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                        rowheight=20, borderwidth=0, bordercolor=BG)
        style.configure("Treeview.Item", background=PANEL, foreground=FG)
        style.configure("Treeview.Heading", background=PANEL, foreground=DIM,
                        relief="flat", borderwidth=0)
        style.map("Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])
        style.map("Treeview.Item", background=[("selected", ACCENT)])

        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(2, 4, 2, 0))
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM, padding=(10, 5),
                        borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])

        style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG,
                        bordercolor=BG, arrowcolor=DIM)
        style.map("Vertical.TScrollbar", background=[("active", "#2a3040")])

    def _build_menu(self):
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Open game folder...", command=self._choose_game, accelerator="Ctrl+O")
        file_menu.add_command(label="Reload archives", command=lambda: self._open_game(self.session.root if self.session else None))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menu.add_cascade(label="File", menu=file_menu)

        export_menu = tk.Menu(menu, tearoff=0)
        export_menu.add_command(label="Export OBJ + MTL + textures...", command=lambda: self._export("obj"))
        export_menu.add_command(label="Export glTF (.glb)...", command=lambda: self._export("glb"))
        export_menu.add_command(label="Export animation GIF...", command=lambda: self._export("gif"))
        export_menu.add_command(label="Save preview PNG...", command=lambda: self._export("png"))
        export_menu.add_separator()
        export_menu.add_command(label="Export raw .mdx...", command=lambda: self._export("raw"))
        menu.add_cascade(label="Export", menu=export_menu)

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Show active model path", command=self._copy_path)
        help_menu.add_command(label="About", command=self._about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menu)

        self.bind_all("<Control-o>", lambda _e: self._choose_game())

    def _build_layout(self):
        self._apply_theme()
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        # --- left: search + tree
        left = ttk.Frame(outer, width=330)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        ttk.Label(left, text="Archives").pack(anchor="w")
        search_row = ttk.Frame(left)
        search_row.pack(fill="x", pady=(2, 4))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search_row, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<KeyRelease>", lambda _e: self._on_search())
        ttk.Button(search_row, text="X", width=2, command=lambda: (self.search_var.set(""), self._on_search())).pack(side="left", padx=(4, 0))

        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self._on_expand)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # --- centre: preview
        centre = ttk.Frame(outer)
        centre.pack(side="left", fill="both", expand=True, padx=8)

        self.canvas = tk.Label(centre, bg="#0f1014", bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", lambda _e: self._request_still(debounce=True))

        controls = ttk.Frame(centre)
        controls.pack(fill="x", pady=(6, 0))
        self.play_button = ttk.Button(controls, text="Play", width=8, command=self._toggle_play)
        self.play_button.pack(side="left")
        ttk.Label(controls, text="Anim").pack(side="left", padx=(10, 4))
        self.sequence_var = tk.StringVar(value="")
        self.sequence_box = ttk.Combobox(controls, textvariable=self.sequence_var, width=22, state="readonly")
        self.sequence_box.pack(side="left")
        self.sequence_box.bind("<<ComboboxSelected>>", lambda _e: self._on_sequence_change())
        ttk.Label(controls, text="fps").pack(side="left", padx=(10, 2))
        self.fps_var = tk.IntVar(value=18)
        ttk.Spinbox(controls, from_=4, to=60, width=4, textvariable=self.fps_var).pack(side="left")
        ttk.Label(controls, text="size").pack(side="left", padx=(10, 2))
        self.size_var = tk.IntVar(value=520)
        ttk.Spinbox(controls, from_=160, to=900, increment=40, width=5, textvariable=self.size_var).pack(side="left")
        self.all_geosets_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="all geosets", variable=self.all_geosets_var,
                        command=lambda: self._request_still()).pack(side="left", padx=(12, 0))
        self.orbit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="orbit", variable=self.orbit_var).pack(side="left", padx=(8, 0))

        # --- right: tabs
        right = ttk.Frame(outer, width=320)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)

        self.info_text = tk.Text(notebook, bg=PANEL, fg=FG, bd=0, wrap="word",
                                 font=("Consolas", 9), height=20)
        self.info_text.configure(state="disabled")
        notebook.add(self.info_text, text="Model")

        self.sequence_list = tk.Listbox(notebook, bg=PANEL, fg=FG, bd=0,
                                        selectbackground=ACCENT, highlightthickness=0)
        self.sequence_list.bind("<Double-Button-1>", lambda _e: self._play_selected_sequence())
        notebook.add(self.sequence_list, text="Animations")

        texture_frame = ttk.Frame(notebook)
        self.texture_list = tk.Listbox(texture_frame, bg=PANEL, fg=FG, bd=0, height=8,
                                       selectbackground=ACCENT, highlightthickness=0)
        self.texture_list.pack(fill="x")
        self.texture_list.bind("<<ListboxSelect>>", lambda _e: self._show_texture())
        self.texture_preview = tk.Label(texture_frame, bg=PANEL, text="", fg=DIM)
        self.texture_preview.pack(fill="both", expand=True, pady=6)
        notebook.add(texture_frame, text="Textures")

        export_frame = ttk.Frame(notebook)
        notebook.add(export_frame, text="Export")
        for label, fmt in (("OBJ + MTL + textures", "obj"), ("glTF binary (.glb)", "glb"),
                           ("Animation GIF", "gif"), ("Preview PNG", "png"), ("Raw .mdx", "raw")):
            ttk.Button(export_frame, text=label, command=lambda f=fmt: self._export(f)).pack(fill="x", pady=3)
        ttk.Label(export_frame, text="GIF uses the selected animation;\npick \"<all animations>\" for a playlist.",
                  style="Dim.TLabel", justify="left").pack(anchor="w", pady=(10, 0))

        # --- status bar
        self.status_var = tk.StringVar(value="starting...")
        status = ttk.Label(self, textvariable=self.status_var, style="Dim.TLabel")
        status.pack(fill="x", padx=10, pady=(0, 6))

    # -- session ----------------------------------------------------------
    def _open_game(self, game_dir=None):
        try:
            session = Session(game_dir) if game_dir else Session(None)
        except StormLibError as exc:
            if not game_dir:
                found = [str(p) for p in find_game_dirs()]
                if found:
                    return self._open_game(found[0])
            messagebox.showerror("wc3studio", f"Could not open the game archives.\n\n{exc}")
            self.status_var.set("no game archives loaded")
            return
        if self.session:
            self.session.close()
        self.session = session
        self.worker = RenderWorker(session, self.results)
        self.worker.start()
        names = session.names()
        self.status_var.set(f"{session.root}  -  {len(names)} files, {len(session.models())} models")
        self.title(f"wc3studio - {session.root}")
        self._folder_tree = self._build_folder_tree(session.models())
        self._populate_tree()
        if self.initial_model:
            self.after(120, self._open_initial_model)

    def _open_initial_model(self):
        try:
            internal = self.session.resolve(self.initial_model)
        except LookupError as exc:
            self.status_var.set(f"could not find {self.initial_model}: {exc}")
            return
        self._select_model(internal)
        if self.initial_sequence and self.model:
            sequence = self.model.sequence(self.initial_sequence)
            if sequence:
                self.sequence_var.set(sequence["name"])
                self._on_sequence_change()

    def _select_model(self, internal: str):
        """Load a model into the UI, exactly as a tree selection would."""
        try:
            self.model = self.session.load(internal)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("wc3studio", f"Could not load {internal}\n\n{exc}")
            return
        self.model_path = internal
        self.yaw, self.pitch, self.zoom = 35.0, 15.0, 1.0
        self._update_info()
        self._update_sequences()
        self._request_still()

    def _choose_game(self):
        folder = filedialog.askdirectory(title="Select the Warcraft III folder (contains War3.mpq)")
        if folder:
            self._open_game(folder)

    def _build_folder_tree(self, paths) -> dict:
        root: dict = {}
        for path in paths:
            parts = path.replace("/", "\\").split("\\")
            node = root
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = None
        return root

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._tree_paths.clear()
        if not self.session:
            return
        for name in sorted(self._folder_tree):
            node = self.tree.insert("", "end", text=f"  {name}", open=False)
            self._tree_paths[node] = name
            self.tree.insert(node, "end", text="...")

    def _on_expand(self, _event):
        item = self.tree.focus()
        prefix = self._tree_paths.get(item)
        if not prefix:
            return
        children = self.tree.get_children(item)
        if len(children) == 1 and self.tree.item(children[0], "text") == "...":
            self.tree.delete(children[0])
            self._insert_children(item, prefix)

    def _insert_children(self, parent_item: str, prefix: str):
        node = self._folder_tree
        for part in prefix.split("\\"):
            node = node.get(part) or {}
        for name in sorted(node):
            child = node[name]
            item = self.tree.insert(parent_item, "end", text=("  " + name) if child is not None else name)
            full = f"{prefix}\\{name}"
            self._tree_paths[item] = full
            if child is not None:
                self.tree.insert(item, "end", text="...")

    def _on_search(self):
        needle = self.search_var.get().strip().lower()
        if not self.session:
            return
        self.tree.delete(*self.tree.get_children())
        self._tree_paths.clear()
        if not needle:
            self._populate_tree()
            return
        matches = [n for n in self.session.models() if needle in n.lower()][:400]
        for name in matches:
            item = self.tree.insert("", "end", text=name)
            self._tree_paths[item] = name
        self.status_var.set(f"{len(matches)} matching models")

    def _on_select(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        path = self._tree_paths.get(selection[0], "")
        if not path or not path.lower().endswith((".mdx", ".mdl")):
            return
        self._select_model(path)

    # -- preview ----------------------------------------------------------
    def _request_still(self, debounce=False):
        if not (self.session and self.model_path):
            return
        if debounce:
            if self._resize_job:
                self.after_cancel(self._resize_job)
            self._resize_job = self.after(160, self._request_still)
            return
        self._resize_job = None
        self.playing = False
        self.play_button.configure(text="Play")
        self.anchor += 1
        self.worker.submit({
            "type": "still", "session": self.session, "anchor": self.anchor,
            "model": self.model_path, "size": self._preview_size(),
            "yaw": self.yaw, "pitch": self.pitch, "zoom": self.zoom,
            "all_geosets": self.all_geosets_var.get(),
        })

    def _request_play(self):
        if not (self.session and self.model_path) or not self.playlist:
            return
        self.anchor += 1
        self._frame_buffer.clear()
        self.worker.submit({
            "type": "play", "session": self.session, "anchor": self.anchor,
            "model": self.model_path, "size": self._preview_size(),
            "yaw": self.yaw, "pitch": self.pitch, "zoom": self.zoom,
            "playlist": self.playlist, "fps": max(self.fps_var.get(), 1),
            "all_geosets": self.all_geosets_var.get(),
        })

    def _preview_size(self) -> int:
        width = max(self.canvas.winfo_width(), 200)
        height = max(self.canvas.winfo_height(), 200)
        return int(max(160, min(760, min(width, height) - 8)))

    def _toggle_play(self):
        if self.playing:
            self.playing = False
            self.play_button.configure(text="Play")
            self._request_still()
            return
        if not self.playlist:
            return
        self.playing = True
        self.play_button.configure(text="Stop")
        self._request_play()

    def _play_selected_sequence(self):
        selection = self.sequence_list.curselection()
        if not selection:
            return
        name = self.sequence_list.get(selection[0])
        self.sequence_var.set(name)
        self._on_sequence_change()
        if not self.playing:
            self._toggle_play()

    def _on_sequence_change(self):
        name = self.sequence_var.get()
        if name.startswith("<all"):
            self.playlist = [s["name"] for s in (self.model.sequences if self.model else [])]
        else:
            self.playlist = [name] if name else []
        if self.playing:
            self._request_play()
        else:
            self._request_still()

    def _orbit(self, degrees: float):
        self.yaw += degrees
        self._request_still()

    def _tilt(self, degrees: float):
        self.pitch = max(-80.0, min(80.0, self.pitch + degrees))
        self._request_still()

    def _on_press(self, event):
        self._drag = (event.x, event.y)

    def _on_drag(self, event):
        if not self._drag:
            return
        dx = event.x - self._drag[0]
        dy = event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.yaw += dx * 0.5
        self.pitch = max(-80.0, min(80.0, self.pitch + dy * 0.4))
        self._request_still()

    def _on_wheel(self, event):
        self.zoom = max(0.4, min(3.0, self.zoom * (1.1 if event.delta > 0 else 0.9)))
        self._request_still()

    # -- results ----------------------------------------------------------
    def _poll(self):
        latest = None
        try:
            while True:
                kind, anchor, payload = self.results.get_nowait()
                if anchor != self.anchor:
                    continue
                if kind == "error":
                    self.status_var.set("render error")
                    print(payload)
                    continue
                latest = (kind, payload)
                if kind == "frame":
                    self._frame_buffer.append(payload)
        except queue.Empty:
            pass

        if latest and latest[0] == "still":
            self._set_image(latest[1])
            # the first render can happen before Tk has laid the canvas out; if the
            # image does not match the current canvas, ask for the right size once
            if (not self._resize_job and not self.playing
                    and abs(latest[1].size[0] - self._preview_size()) > 8):
                self._request_still(debounce=True)
        if self.playing:
            self._tick_playback()
        self.after(25, self._poll)

    def _tick_playback(self):
        if not self._frame_buffer:
            return
        interval = 1.0 / max(self.fps_var.get(), 1)
        now = time.time()
        if now - self._last_frame_time < interval:
            return
        self._last_frame_time = now
        self._set_image(self._frame_buffer.popleft())

    def _set_image(self, image: Image.Image):
        self._photo = ImageTk.PhotoImage(image)
        self.canvas.configure(image=self._photo)

    # -- panels -----------------------------------------------------------
    def _update_info(self):
        model = self.model
        if model is None:
            return
        try:
            info = self.session.summary(self.model_path)
        except Exception:  # noqa: BLE001
            return
        visible = info["visible_geosets"]
        lines = [
            f"{info['name']}",
            f"path      {self.model_path}",
            f"version   {info['version']}   size {info['bytes'] / 1024:.1f} KiB",
            "",
            f"geosets   {info['geosets']}  ({visible} visible)",
            f"triangles {info['triangles']} of {info['triangles_total']}",
            f"vertices  {info['vertices']}",
            f"materials {info['materials']}",
            f"skeleton  {info['bones']} bones, {info['helpers']} helpers",
            f"textures  {len(info['textures'])}",
        ]
        for texture in info["textures"]:
            mark = "ok " if texture["available"] else "!! "
            label = texture["path"] or f"<replaceable {texture['replaceable_id']}>"
            lines.append(f"   {mark}{label.rsplit(chr(92), 1)[-1]}")
        lines += ["", f"animations: {len(info['sequences'])}"]
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", "\n".join(lines))
        self.info_text.configure(state="disabled")
        self._update_textures()

    def _update_sequences(self):
        self.sequence_list.delete(0, "end")
        if self.model is None:
            return
        names = [s["name"] for s in self.model.sequences]
        self.sequence_list.insert("end", "<all animations>")
        for name in names:
            self.sequence_list.insert("end", name)
        preferred = None
        for candidate in ("Stand", "Stand - 1", "Walk", "Walk - 1"):
            match = self.model.sequence(candidate)
            if match:
                preferred = match["name"]
                break
        self.sequence_box.configure(values=["<all animations>"] + names)
        self.sequence_var.set(preferred or (names[0] if names else ""))
        self._on_sequence_change()

    def _update_textures(self):
        self.texture_list.delete(0, "end")
        self._thumb_refs.clear()
        self.texture_preview.configure(image="", text="")
        if not (self.session and self.model):
            return
        self.textures = self.session.resources.model_textures(self.model)
        for entry in self.textures:
            label = entry["path"] or f"<replaceable {entry['replaceable_id']}>"
            self.texture_list.insert("end", ("ok  " if entry["exists"] else "!!  ") + label.rsplit("\\", 1)[-1])

    def _show_texture(self):
        selection = self.texture_list.curselection()
        if not selection:
            return
        entry = self.textures[selection[0]]
        image = entry["image"]
        if image is None:
            self.texture_preview.configure(image="", text="texture not available")
            return
        preview = image.copy()
        preview.thumbnail((260, 260))
        self._thumb_refs = [ImageTk.PhotoImage(preview)]
        self.texture_preview.configure(image=self._thumb_refs[0], text="")

    # -- export -----------------------------------------------------------
    def _export(self, fmt: str):
        if not (self.session and self.model_path):
            messagebox.showinfo("wc3studio", "Load a model first.")
            return
        stem = Path(self.model_path.replace("\\", "/")).stem
        if fmt == "obj":
            folder = filedialog.askdirectory(title="Folder for the OBJ export")
            if not folder:
                return
            target = Path(folder) / f"{stem}.obj"

            def work():
                return self.session.export_obj(self.model_path, target,
                                               textures_root=Path(folder) / "textures",
                                               all_geosets=self.all_geosets_var.get())
        elif fmt == "glb":
            path = filedialog.asksaveasfilename(title="Save glTF", defaultextension=".glb",
                                                initialfile=f"{stem}.glb",
                                                filetypes=[("glTF binary", "*.glb")])
            if not path:
                return
            target = Path(path)

            def work():
                return self.session.export_glb(self.model_path, target,
                                               all_geosets=self.all_geosets_var.get())
        elif fmt == "png":
            path = filedialog.asksaveasfilename(title="Save preview", defaultextension=".png",
                                                initialfile=f"{stem}.png",
                                                filetypes=[("PNG", "*.png")])
            if not path:
                return
            target = Path(path)

            def work():
                image = self.session.render(self.model_path, size=900, yaw=self.yaw,
                                            pitch=self.pitch, zoom=self.zoom,
                                            all_geosets=self.all_geosets_var.get())
                image.save(target)
                return {"path": str(target)}
        elif fmt == "gif":
            path = filedialog.asksaveasfilename(title="Save animation GIF",
                                                defaultextension=".gif",
                                                initialfile=f"{stem}.gif",
                                                filetypes=[("GIF", "*.gif")])
            if not path:
                return
            target = Path(path)
            playlist = self.playlist or [self.sequence_var.get()]

            def work():
                images, meta = render_animation(self.session, self.model, playlist,
                                                fps=self.fps_var.get(), size=420,
                                                yaw=self.yaw, pitch=self.pitch,
                                                all_geosets=self.all_geosets_var.get(),
                                                progress=lambda done, total: self.status_var.set(
                                                    f"rendering {done}/{total} frames..."))
                write_gif(images, target, self.fps_var.get())
                return {"frames": meta["frames"], "path": str(target)}
        else:  # raw
            folder = filedialog.askdirectory(title="Folder for the raw .mdx")
            if not folder:
                return
            target = Path(folder)

            def work():
                size = self.session.export_raw(self.model_path, target)
                return {"bytes": size, "path": str(target)}

        threading.Thread(target=self._run_export, args=(fmt, work), daemon=True).start()

    def _run_export(self, fmt: str, work):
        self._busy_count += 1
        self.status_var.set(f"exporting {fmt}...")
        try:
            stats = work()
            message = f"{fmt} export finished: {stats.get('path', '')}"
            self.status_var.set(message)
            self.results.put(("done", self.anchor, message))
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"{fmt} export failed: {exc}")
            self.results.put(("error", self.anchor, traceback.format_exc()))
        finally:
            self._busy_count -= 1

    # -- misc -------------------------------------------------------------
    def _copy_path(self):
        if self.model_path:
            self.clipboard_clear()
            self.clipboard_append(self.model_path)
            self.status_var.set(f"copied: {self.model_path}")

    def _about(self):
        messagebox.showinfo(
            "wc3studio",
            f"wc3studio {__version__}\n\n"
            "Browse, preview, animate and convert Warcraft III models from the game's\n"
            "MPQ archives (MDX/BLP) to OBJ, glTF/GLB and GIF/MP4.\n\n"
            "MPQ reading: StormLib (MIT) by Ladislav Zezula.\n"
            "Game assets remain the property of Blizzard Entertainment.")

    def _on_close(self):
        try:
            if self.worker:
                self.worker.shutdown()
            if self.session:
                self.session.close()
        finally:
            self.destroy()


def run(game_dir: str | None = None, model: str | None = None,
        sequence: str | None = None) -> int:
    app = App(game_dir, model, sequence)
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
