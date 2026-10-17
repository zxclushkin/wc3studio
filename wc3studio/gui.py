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
from .bundle import build_bundle
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
                 sequence: str | None = None, tab: str | None = None):
        super().__init__()
        self.title("wc3studio - Warcraft III asset browser")
        self.geometry("1280x820")
        self.minsize(1000, 640)
        self.configure(bg=BG)

        self.initial_model = model
        self.initial_sequence = sequence
        self.initial_tab = tab
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
        self.panel_queue: "queue.Queue" = queue.Queue()
        self.tables_db = None
        self.ui_lib = None
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
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM, padding=(6, 4),
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
        right = ttk.Frame(outer, width=404)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        self.info_text = tk.Text(notebook, bg=PANEL, fg=FG, bd=0, wrap="word",
                                 font=("Consolas", 9), height=20)
        self.info_text.configure(state="disabled")
        notebook.add(self.info_text, text="Model")

        self.sequence_list = tk.Listbox(notebook, bg=PANEL, fg=FG, bd=0,
                                        selectbackground=ACCENT, highlightthickness=0)
        self.sequence_list.bind("<Double-Button-1>", lambda _e: self._play_selected_sequence())
        notebook.add(self.sequence_list, text="Anims")

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
        ttk.Separator(export_frame).pack(fill="x", pady=(12, 8))
        ttk.Button(export_frame, text="Bundle everything…",
                   command=self._open_bundle_dialog).pack(fill="x")
        ttk.Label(export_frame, text="Все модели, glTF с анимациями, звуки,\nтаблицы и интерфейс — в папку и/или zip.",
                  style="Dim.TLabel", justify="left").pack(anchor="w", pady=(6, 0))

        # --- data tables tab
        data_frame = ttk.Frame(notebook)
        notebook.add(data_frame, text="Data")
        self.data_filter = tk.StringVar()
        data_entry = ttk.Entry(data_frame, textvariable=self.data_filter)
        data_entry.pack(fill="x", pady=(0, 4))
        data_entry.bind("<KeyRelease>", lambda _e: self._filter_tables())
        self.table_tree = ttk.Treeview(data_frame, columns=("kind", "rows"), show="tree headings",
                                       selectmode="browse", height=12)
        self.table_tree.heading("#0", text="table")
        self.table_tree.heading("kind", text="kind")
        self.table_tree.heading("rows", text="rows")
        self.table_tree.column("#0", width=200, anchor="w")
        self.table_tree.column("kind", width=44, anchor="w")
        self.table_tree.column("rows", width=48, anchor="e")
        self.table_tree.pack(fill="both", expand=True)
        self.table_tree.bind("<Double-Button-1>", lambda _e: self._open_table())
        ttk.Button(data_frame, text="Open table", command=self._open_table).pack(fill="x", pady=(4, 2))
        ttk.Button(data_frame, text="Export all tables…", command=self._export_tables).pack(fill="x")
        ttk.Label(data_frame, text="212 gameplay tables: units, abilities,\nitems, doodads, sounds. Double-click to open.",
                  style="Dim.TLabel", justify="left").pack(anchor="w", pady=(8, 0))

        # --- interface tab
        ui_frame = ttk.Frame(notebook)
        notebook.add(ui_frame, text="UI")
        self.ui_tree = ttk.Treeview(ui_frame, columns=("frames",), show="tree headings",
                                    selectmode="browse", height=10)
        self.ui_tree.heading("#0", text="fdf document")
        self.ui_tree.heading("frames", text="fr")
        self.ui_tree.column("#0", width=210)
        self.ui_tree.column("frames", width=40, anchor="e")
        self.ui_tree.pack(fill="both", expand=True)
        self.ui_tree.bind("<<TreeviewSelect>>", self._ui_select)
        ui_buttons = ttk.Frame(ui_frame)
        ui_buttons.pack(fill="x", pady=(4, 2))
        ttk.Button(ui_buttons, text="Frames…", command=self._open_frame_browser).pack(side="left", expand=True, fill="x")
        ttk.Button(ui_buttons, text="Dump art…", command=self._dump_ui_art).pack(side="left", expand=True, fill="x")
        ttk.Button(ui_buttons, text="Sheet…", command=self._ui_sheet).pack(side="left", expand=True, fill="x")
        self.ui_details = tk.Text(ui_frame, bg=PANEL, fg=FG, bd=0, wrap="word",
                                  font=("Consolas", 9), height=8)
        self.ui_details.configure(state="disabled")
        self.ui_details.pack(fill="both", expand=True, pady=(4, 0))

        # --- status bar
        self.status_var = tk.StringVar(value="starting...")
        status = ttk.Label(self, textvariable=self.status_var, style="Dim.TLabel")
        status.pack(fill="x", padx=10, pady=(0, 6))

        if self.initial_tab:
            wanted = self.initial_tab.strip().lower()
            for index, tab_id in enumerate(self.notebook.tabs()):
                if self.notebook.tab(tab_id, "text").strip().lower() == wanted:
                    self.notebook.select(index)
                    break

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
        self._start_panel_loaders()
        if self.initial_model:
            self.after(120, self._open_initial_model)

    # -- data / UI panels -------------------------------------------------
    def _start_panel_loaders(self):
        """Load the data tables and FDF documents off the UI thread."""
        threading.Thread(target=self._load_tables, daemon=True).start()
        threading.Thread(target=self._load_ui, daemon=True).start()

    def _load_tables(self):
        try:
            from .tables import TableDatabase
            database = TableDatabase(self.session.game)
            summary = database.tables_summary()
            self.tables_db = database
            self.panel_queue.put(("tables", summary))
        except Exception as exc:  # noqa: BLE001
            self.panel_queue.put(("status", f"data tables failed: {exc}"))

    def _load_ui(self):
        try:
            from .ui import UiLibrary
            library = UiLibrary(self.session.game)
            documents = []
            for path in library.documents():
                try:
                    documents.append((path, len(library.load(path).all_frames())))
                except FileNotFoundError:
                    continue
            self.ui_lib = library
            self.panel_queue.put(("ui_docs", documents))
        except Exception as exc:  # noqa: BLE001
            self.panel_queue.put(("status", f"interface files failed: {exc}"))

    def _filter_tables(self):
        needle = self.data_filter.get().strip().lower()

        def base_first(item):
            """Base game tables before the Custom_V0/Custom_V1/Melee_V0 copies."""
            name = item[0].lower()
            return (name.startswith(("custom_v", "melee_v")), name)

        self.table_tree.delete(*self.table_tree.get_children())
        for name, kind, rows, _columns in sorted(getattr(self, "_table_rows", []), key=base_first):
            if needle and needle not in name.lower():
                continue
            self.table_tree.insert("", "end", iid=name, values=(kind, rows), text=name)

    def _open_table(self):
        selection = self.table_tree.selection()
        if not selection or self.tables_db is None:
            return
        name = selection[0]
        self.status_var.set(f"loading table {name}…")

        def work():
            try:
                self.panel_queue.put(("table_data", name, self.tables_db.load(name)))
            except Exception as exc:  # noqa: BLE001
                self.panel_queue.put(("status", f"table failed: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _show_table_window(self, name: str, table):
        window = tk.Toplevel(self)
        window.title(f"{name}  -  {table.row_count} rows x {table.column_count} columns")
        window.geometry("1000x620")
        window.configure(bg=BG)

        top = ttk.Frame(window)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="filter").pack(side="left")
        query = tk.StringVar()
        entry = ttk.Entry(top, textvariable=query)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        counter = tk.StringVar()
        ttk.Label(top, textvariable=counter, style="Dim.TLabel").pack(side="left")

        columns = table.columns
        frame = ttk.Frame(window)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=110, stretch=False, anchor="w")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        state = {"rows": table.rows}

        def populate(rows):
            tree.delete(*tree.get_children())
            for row in rows[:4000]:
                tree.insert("", "end", values=list(row))
            counter.set(f"{len(rows)} of {table.row_count} rows")

        def apply_filter(_event=None):
            needle = query.get().strip().lower()
            if not needle:
                populate(state["rows"])
                return
            populate([row for row in state["rows"]
                      if any(needle in str(cell).lower() for cell in row)])

        entry.bind("<KeyRelease>", apply_filter)
        populate(table.rows)

        buttons = ttk.Frame(window)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Export CSV…",
                   command=lambda: self._export_table_csv(name, table)).pack(side="left")
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")
        self.status_var.set(f"{name}: {table.row_count} rows, {table.column_count} columns")

    def _export_table_csv(self, name: str, table):
        path = filedialog.asksaveasfilename(title="Save table as CSV", defaultextension=".csv",
                                            initialfile=name.replace("\\", "_").rsplit(".", 1)[0] + ".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            table.write(path, "csv")
            self.status_var.set(f"saved {path}")
        except OSError as exc:
            messagebox.showerror("wc3studio", f"Could not write {path}\n\n{exc}")

    def _export_tables(self):
        if self.tables_db is None:
            messagebox.showinfo("wc3studio", "Tables are still loading.")
            return
        folder = filedialog.askdirectory(title="Folder for the CSV export")
        if not folder:
            return
        self.status_var.set("exporting tables…")

        def work():
            from pathlib import Path
            written = failed = 0
            for internal, _kind, _rows, _columns in self.tables_db.tables_summary():
                try:
                    table = self.tables_db.load(internal)
                    target = Path(folder) / (internal.replace("\\", "/") + ".csv")
                    table.write(target, "csv")
                    written += 1
                except (OSError, ValueError):
                    failed += 1
            self.panel_queue.put(("status", f"tables exported: {written} files, {failed} failed"))

        threading.Thread(target=work, daemon=True).start()

    def _ui_select(self, _event=None):
        selection = self.ui_tree.selection()
        if not selection or self.ui_lib is None:
            return
        path = selection[0]
        try:
            document = self.ui_lib.load(path)
        except FileNotFoundError:
            return
        lines = [path, f"frames: {len(document.all_frames())}",
                 f"includes: {len(document.includes)}", f"strings: {len(document.strings)}", ""]
        for include in document.includes[:6]:
            lines.append(f"  include {include}")
        if document.includes:
            lines.append("")
        for frame in document.all_frames()[:25]:
            lines.append(f"  {frame.kind:<14} {frame.name}")
        if len(document.all_frames()) > 25:
            lines.append(f"  … {len(document.all_frames()) - 25} more")
        self.ui_details.configure(state="normal")
        self.ui_details.delete("1.0", "end")
        self.ui_details.insert("1.0", "\n".join(lines))
        self.ui_details.configure(state="disabled")

    def _open_frame_browser(self):
        if self.ui_lib is None:
            messagebox.showinfo("wc3studio", "Interface files are still loading.")
            return
        window = tk.Toplevel(self)
        window.title("UI frames")
        window.geometry("900x560")
        window.configure(bg=BG)
        top = ttk.Frame(window)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="search").pack(side="left")
        query = tk.StringVar()
        ttk.Entry(top, textvariable=query).pack(side="left", fill="x", expand=True, padx=6)

        panes = ttk.Frame(window)
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        tree = ttk.Treeview(panes, columns=("kind", "source"), show="tree headings")
        tree.heading("#0", text="frame")
        tree.heading("kind", text="kind")
        tree.heading("source", text="source")
        tree.column("#0", width=250)
        tree.column("kind", width=110)
        tree.column("source", width=330)
        tree.pack(side="left", fill="both", expand=True)
        details = tk.Text(panes, bg=PANEL, fg=FG, bd=0, width=46, wrap="word",
                          font=("Consolas", 9))
        details.pack(side="right", fill="y")

        frames = self.ui_lib.frames()

        def populate():
            needle = query.get().strip().lower()
            tree.delete(*tree.get_children())
            for frame in frames:
                if needle and needle not in frame.name.lower() and needle not in frame.kind.lower():
                    continue
                tree.insert("", "end", iid=frame.name, text=frame.name,
                            values=(frame.kind, frame.source))

        def show(_event=None):
            selection = tree.selection()
            if not selection:
                return
            try:
                data = self.ui_lib.resolve(selection[0])
            except KeyError:
                return
            lines = [f"{data['name']}  ({data['kind']})", f"source: {data['source']}",
                     f"inherits: {' <- '.join(data['chain'])}", "",
                     f"textures ({len(data['textures'])}):"]
            for reference, resolved in data["textures"]:
                lines.append(f"  {'ok' if resolved else '??'} {reference}")
            if data["decorated"]:
                lines.append(f"decorated: {', '.join(data['decorated'][:6])}")
            lines += ["", "properties:"]
            for key, value in data["properties"].items():
                lines.append(f"  {key} = {value}")
            details.configure(state="normal")
            details.delete("1.0", "end")
            details.insert("1.0", "\n".join(lines))
            details.configure(state="disabled")

        query.trace_add("write", lambda *_: populate())
        tree.bind("<<TreeviewSelect>>", show)
        populate()

    def _dump_ui_art(self):
        if self.ui_lib is None:
            messagebox.showinfo("wc3studio", "Interface files are still loading.")
            return
        folder = filedialog.askdirectory(title="Folder for the UI artwork (PNG)")
        if not folder:
            return
        self.status_var.set("converting UI artwork…")

        def work():
            result = self.ui_lib.dump_assets(self.session.resources, folder)
            self.panel_queue.put(("status", f"UI artwork: {result['written']} PNG, "
                                            f"{result['bytes'] / 1048576:.1f} MiB -> {folder}"))

        threading.Thread(target=work, daemon=True).start()

    def _ui_sheet(self):
        if self.ui_lib is None:
            messagebox.showinfo("wc3studio", "Interface files are still loading.")
            return
        path = filedialog.asksaveasfilename(title="Save UI contact sheet", defaultextension=".png",
                                            initialfile="ui_sheet.png",
                                            filetypes=[("PNG", "*.png")])
        if not path:
            return
        self.status_var.set("building UI contact sheet…")

        def work():
            from .ui import build_contact_sheet
            items = self.ui_lib.assets()
            images, labels = [], []
            for item in items[:96]:
                image = self.session.resources.image(item["path"])
                if image is not None:
                    images.append(image)
                    labels.append(item["name"])
            if not images:
                self.panel_queue.put(("status", "no UI images found"))
                return
            sheet = build_contact_sheet(images, labels, tile=120, columns=12)
            sheet.save(path)
            self.panel_queue.put(("status", f"contact sheet -> {path} ({len(images)} tiles)"))

        threading.Thread(target=work, daemon=True).start()

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
        self._drain_panels()
        self.after(25, self._poll)

    def _drain_panels(self):
        """Results from the data-table and interface loaders."""
        try:
            while True:
                message = self.panel_queue.get_nowait()
                kind = message[0]
                if kind == "tables":
                    self._table_rows = message[1]
                    self._filter_tables()
                    self.status_var.set(f"data tables loaded: {len(self._table_rows)}")
                elif kind == "ui_docs":
                    for path, frames in message[1]:
                        self.ui_tree.insert("", "end", iid=path, text=path, values=(frames,))
                    self.status_var.set(f"interface documents loaded: {len(message[1])}")
                elif kind == "table_data":
                    self._show_table_window(message[1], message[2])
                elif kind == "bundle_done":
                    self._on_bundle_done(message[1])
                elif kind == "bundle_failed":
                    state = getattr(self, "_bundle_state", None)
                    if state:
                        state["running"] = False
                        state["progress"].set(f"ошибка: {message[1]}")
                    self.status_var.set(f"bundle failed: {message[1]}")
                    messagebox.showerror("wc3studio", f"Bundle failed:\n\n{message[1]}")
                elif kind == "status":
                    self.status_var.set(message[1])
        except queue.Empty:
            return

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

    # -- bundle -----------------------------------------------------------
    def _open_bundle_dialog(self):
        if self.session is None:
            messagebox.showinfo("wc3studio", "Load the game first.")
            return
        window = tk.Toplevel(self)
        window.title("Bundle everything")
        window.geometry("520x470")
        window.configure(bg=BG)
        window.transient(self)

        folder_var = tk.StringVar(value=str(Path.cwd() / "wc3_bundle"))
        flags = {
            "models": tk.BooleanVar(value=True),
            "glb": tk.BooleanVar(value=True),
            "sounds": tk.BooleanVar(value=True),
            "raw": tk.BooleanVar(value=False),
            "tables": tk.BooleanVar(value=True),
            "ui": tk.BooleanVar(value=False),
            "previews": tk.BooleanVar(value=False),
            "archive": tk.BooleanVar(value=True),
        }
        limit_var = tk.IntVar(value=0)
        filter_var = tk.StringVar(value="")

        pad = {"padx": 10}
        ttk.Label(window, text="Куда сохранять").pack(anchor="w", **pad, pady=(10, 2))
        row = ttk.Frame(window)
        row.pack(fill="x", **pad)
        ttk.Entry(row, textvariable=folder_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3, command=lambda: folder_var.set(
            filedialog.askdirectory(title="Bundle folder") or folder_var.get())).pack(side="left", padx=(4, 0))

        ttk.Label(window, text="Что включить").pack(anchor="w", **pad, pady=(12, 2))
        labels = {
            "models": "Модели: OBJ + MTL + PNG-текстуры",
            "glb": "glTF (.glb) — со скелетом и всеми анимациями",
            "sounds": "Звуки модели (WAV, лежат рядом с моделью)",
            "raw": "Исходные .mdx",
            "tables": "Таблицы данных (CSV)",
            "ui": "Картинки интерфейса (PNG)",
            "previews": "Превью-анимации GIF на каждую модель (медленно)",
            "archive": "Ещё и одним zip-архивом",
        }
        for key, text in labels.items():
            ttk.Checkbutton(window, text=text, variable=flags[key]).pack(anchor="w", **pad)

        ttk.Label(window, text="Ограничения").pack(anchor="w", **pad, pady=(12, 2))
        row = ttk.Frame(window)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="только первых моделей").pack(side="left")
        ttk.Spinbox(row, from_=0, to=4000, increment=25, width=6,
                    textvariable=limit_var).pack(side="left", padx=(6, 14))
        ttk.Label(row, text="фильтр пути").pack(side="left")
        ttk.Entry(row, textvariable=filter_var, width=18).pack(side="left", padx=(6, 0))

        progress_var = tk.StringVar(value="готово к запуску")
        ttk.Label(window, textvariable=progress_var, style="Dim.TLabel",
                  wraplength=480, justify="left").pack(anchor="w", **pad, pady=(14, 4))
        bar = ttk.Progressbar(window, maximum=100)
        bar.pack(fill="x", **pad)

        state = {"running": False, "window": window, "progress": progress_var, "bar": bar,
                 "flags": flags, "folder": folder_var, "limit": limit_var, "filter": filter_var}

        def start():
            if state["running"]:
                return
            from .bundle import BundleOptions
            chosen = {key for key in ("models", "glb", "sounds", "raw", "tables", "ui")
                      if flags[key].get()}
            if not chosen and not flags["previews"].get():
                messagebox.showinfo("wc3studio", "Выберите хотя бы один формат.", parent=window)
                return
            options = BundleOptions(
                out=Path(state["folder"].get()),
                formats=chosen or {"glb"},
                previews=flags["previews"].get(),
                limit=state["limit"].get(),
                model_filter=state["filter"].get().strip(),
                archive=flags["archive"].get(),
            )
            state["running"] = True
            progress_var.set("стартую…")
            bar.configure(value=0)
            self.status_var.set("bundling…")

            def work():
                try:
                    stats = build_bundle(self.session, options, self._bundle_progress)
                    self.panel_queue.put(("bundle_done", stats))
                except Exception as exc:  # noqa: BLE001
                    self.panel_queue.put(("bundle_failed", str(exc)))

            threading.Thread(target=work, daemon=True).start()

        buttons = ttk.Frame(window)
        buttons.pack(fill="x", **pad, pady=(16, 10))
        ttk.Button(buttons, text="Старт", command=start).pack(side="left")
        ttk.Button(buttons, text="Закрыть", command=window.destroy).pack(side="right")

        self._bundle_state = state
        window.lift()

    def _bundle_progress(self, kind, done, total, message=""):
        state = getattr(self, "_bundle_state", None)
        if not state or not state.get("window"):
            return
        percent = 100 * done / max(total, 1)
        state["bar"].configure(value=percent)
        label = {"models": "модели", "raw": "сырые .mdx", "sounds": "звуки",
                 "tables": "таблицы", "ui": "интерфейс", "index": "индекс",
                 "zip": "архив"}.get(kind, kind)
        state["progress"].set(f"{label}: {done}/{total} ({percent:.0f}%)  {message[-70:]}")

    def _on_bundle_done(self, stats):
        state = getattr(self, "_bundle_state", None)
        if state:
            state["running"] = False
            counts = stats["counts"]
            state["progress"].set(f"готово: {stats['bytes'] / 1048576:.1f} MiB")
            state["bar"].configure(value=100)
        counts = stats["counts"]
        parts = [f"{name}: {counts[name]}" for name in ("models", "glb", "raw", "sounds",
                                                        "tables", "ui", "previews", "indexed")
                 if counts[name]]
        summary = (f"Готово.\n\n" + "\n".join(parts)
                   + f"\n\nРазмер: {stats['bytes'] / 1048576:.1f} MiB")
        if stats.get("archive"):
            summary += (f"\nАрхив: {stats['archive']} "
                        f"({stats['archive_bytes'] / 1048576:.1f} MiB)")
        if stats["errors"]:
            summary += f"\n\nОшибок: {len(stats['errors'])}"
        self.status_var.set(f"bundle -> {stats['out']}")
        messagebox.showinfo("wc3studio", summary)

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
        sequence: str | None = None, tab: str | None = None) -> int:
    app = App(game_dir, model, sequence, tab)
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
