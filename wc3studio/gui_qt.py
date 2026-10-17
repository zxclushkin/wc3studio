"""PySide6 front-end with an OpenGL viewport, for smooth animation playback.

    wc3studio gui --framework qt

The Tkinter window is still there (``--framework tk``); this one exists because Qt
can hold a 60 fps OpenGL surface while the old window was limited by the CPU
rasteriser (~6 fps at 512x512).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,
                               QDockWidget, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton, QSpinBox,
                               QSplitter, QTabWidget, QTextBrowser, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import __version__
from .glview import ModelView
from .pipeline import Session
from .stormlib import StormLibError, find_game_dirs


class MainWindow(QMainWindow):
    def __init__(self, session: Session, model: str | None = None,
                 sequence: str | None = None):
        super().__init__()
        self.session = session
        self.model_path = ""
        self.setWindowTitle(f"wc3studio {__version__} - {session.root}")
        self.resize(1360, 880)

        self.view = ModelView(session, self)
        self.setCentralWidget(self._build_center())

        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._build_assets())
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._build_inspector())
        self._build_menu()
        self.statusBar().showMessage("ready")

        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.start(500)

        self._populate_assets()
        if model:
            self.after_load = QTimer.singleShot(120, lambda: self.open_model(model, sequence))

    # -- layout -----------------------------------------------------------
    def _build_center(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view, 1)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.addWidget(QLabel("Анимация"))
        self.sequence_box = QComboBox()
        self.sequence_box.setMinimumWidth(200)
        self.sequence_box.currentTextChanged.connect(self._on_sequence)
        row.addWidget(self.sequence_box)

        self.play_button = QPushButton("▶ Play")
        self.play_button.clicked.connect(self._toggle_play)
        row.addWidget(self.play_button)

        row.addWidget(QLabel("fps"))
        self.fps_box = QSpinBox()
        self.fps_box.setRange(1, 60)
        self.fps_box.setValue(30)
        self.fps_box.valueChanged.connect(self._on_fps)
        row.addWidget(self.fps_box)

        self.all_geosets_box = QCheckBox("все геосеты")
        self.all_geosets_box.toggled.connect(self._on_all_geosets)
        row.addWidget(self.all_geosets_box)

        self.fps_label = QLabel("-- fps")
        row.addWidget(self.fps_label)
        row.addStretch(1)
        layout.addWidget(bar)
        return container

    def _build_assets(self) -> QDockWidget:
        dock = QDockWidget("Модели", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea
                             | Qt.DockWidgetArea.RightDockWidgetArea)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("поиск: footman, hero, knight…")
        self.search_box.textChanged.connect(self._filter_assets)
        layout.addWidget(self.search_box)

        self.asset_tree = QTreeWidget()
        self.asset_tree.setHeaderHidden(True)
        self.asset_tree.itemDoubleClicked.connect(self._on_asset_activated)
        layout.addWidget(self.asset_tree, 1)
        dock.setWidget(panel)
        dock.setMinimumWidth(260)
        return dock

    def _build_inspector(self) -> QDockWidget:
        dock = QDockWidget("Модель", self)
        self.tabs = QTabWidget()

        self.info_view = QTextBrowser()
        self.tabs.addTab(self.info_view, "Инфо")

        self.animation_list = QListWidget()
        self.animation_list.itemDoubleClicked.connect(
            lambda item: self.sequence_box.setCurrentText(item.text()))
        self.tabs.addTab(self.animation_list, "Анимации")

        textures = QWidget()
        texture_layout = QVBoxLayout(textures)
        self.texture_list = QListWidget()
        self.texture_list.currentTextChanged.connect(self._show_texture)
        texture_layout.addWidget(self.texture_list, 1)
        self.texture_preview = QLabel("выбери текстуру")
        self.texture_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.texture_preview.setMinimumHeight(160)
        texture_layout.addWidget(self.texture_preview, 1)
        self.tabs.addTab(textures, "Текстуры")

        export = QWidget()
        export_layout = QVBoxLayout(export)
        for label, handler in (("OBJ + MTL + текстуры", self._export_obj),
                               ("glTF (.glb)", self._export_glb),
                               ("Анимация GIF", self._export_gif),
                               ("PNG-кадр", self._export_png),
                               ("Сырой .mdx", self._export_raw)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            export_layout.addWidget(button)
        export_layout.addStretch(1)
        self.tabs.addTab(export, "Экспорт")

        dock.setWidget(self.tabs)
        dock.setMinimumWidth(300)
        return dock

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        open_action = QAction("Открыть папку игры…", self)
        open_action.triggered.connect(self._choose_game)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        quit_action = QAction("Выход", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("Вид")
        reset_action = QAction("Сбросить камеру", self)
        reset_action.triggered.connect(self._reset_camera)
        view_menu.addAction(reset_action)

    # -- assets -----------------------------------------------------------
    def _populate_assets(self) -> None:
        self.asset_tree.clear()
        self._items: dict[str, str] = {}
        folders: dict[str, QTreeWidgetItem] = {}
        for internal in self.session.models():
            parts = internal.split("\\")
            parent = None
            path = ""
            for part in parts[:-1]:
                path = f"{path}\\{part}" if path else part
                node = folders.get(path)
                if node is None:
                    node = QTreeWidgetItem([part])
                    if parent is None:
                        self.asset_tree.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    folders[path] = node
                parent = node
            leaf = QTreeWidgetItem([parts[-1]])
            leaf.setData(0, Qt.ItemDataRole.UserRole, internal)
            if parent is None:
                self.asset_tree.addTopLevelItem(leaf)
            else:
                parent.addChild(leaf)
            self._items[internal.lower()] = internal
        self.asset_tree.expandItem(self.asset_tree.topLevelItem(0)
                                   if self.asset_tree.topLevelItemCount() else None)

    def _filter_assets(self, text: str) -> None:
        needle = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            own = item.text(0).lower()
            visible_child = False
            for index in range(item.childCount()):
                visible_child = visit(item.child(index)) or visible_child
            visible = visible_child or not needle or needle in own
            item.setHidden(not visible)
            if needle and visible:
                item.setExpanded(True)
            return visible

        for index in range(self.asset_tree.topLevelItemCount()):
            visit(self.asset_tree.topLevelItem(index))

    def _on_asset_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        internal = item.data(0, Qt.ItemDataRole.UserRole)
        if not internal:
            item.setExpanded(not item.isExpanded())
            return
        self.open_model(internal)

    def open_model(self, wanted: str, sequence: str | None = None) -> None:
        try:
            internal = wanted if "\\" in wanted else self.session.resolve(wanted)
        except LookupError:
            internal = wanted
        self.statusBar().showMessage(f"загружаю {internal}…")
        try:
            self.view.set_model(internal)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "wc3studio", f"Не удалось открыть {internal}\n\n{exc}")
            return
        self.model_path = internal
        self._fill_panels()
        if sequence:
            self.sequence_box.setCurrentText(sequence)
        if self.view.has_geometry():
            self.view.play()
            self.play_button.setText("⏸ Pause")
            self.statusBar().showMessage(f"{internal}  -  {len(self.view.items)} геосетов")
        else:
            self.stop_playback()
            self.statusBar().showMessage(
                f"{internal}  -  у модели нет геометрии (только точки привязки и эффекты)")

    def _fill_panels(self) -> None:
        model = self.view.model
        summary = self.session.summary(self.model_path)
        lines = [f"<b>{Path(self.model_path).stem}</b>",
                 self.model_path, "",
                 f"геосеты: {summary['geosets']} (видимых {summary['visible_geosets']})",
                 f"треугольники: {summary['triangles']}",
                 f"материалы: {summary['materials']}", f"кости: {summary['bones']}",
                 f"анимации: {len(model.sequences)}", f"размер: {summary['bytes']} байт"]
        self.info_view.setHtml("<br>".join(lines))

        self.animation_list.clear()
        self.sequence_box.blockSignals(True)
        self.sequence_box.clear()
        for sequence in model.sequences:
            self.sequence_box.addItem(sequence["name"])
            item = QListWidgetItem(f"{sequence['name']}  ({sequence['end'] - sequence['start']} мс)")
            self.animation_list.addItem(item)
        if model.sequences:
            self.sequence_box.setCurrentIndex(0)
        self.sequence_box.blockSignals(False)

        self.texture_list.clear()
        for texture in model.textures:
            if texture.path:
                self.texture_list.addItem(texture.path)

    def _show_texture(self, path: str) -> None:
        image = self.session.resources.image(path)
        if image is None:
            self.texture_preview.setText("не найдена в этой установке")
            return
        rgba = image.convert("RGBA")
        qimage = QImage(rgba.tobytes("raw", "RGBA"), rgba.width, rgba.height,
                        QImage.Format_RGBA8888).copy()
        self.texture_preview.setPixmap(QPixmap.fromImage(qimage).scaled(
            260, 260, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    # -- controls ---------------------------------------------------------
    def _on_sequence(self, name: str) -> None:
        if name:
            self.view.set_sequence(name)

    def _on_fps(self, value: int) -> None:
        self.view.fps = float(value)
        if self.view.playing:
            self.view.play()

    def _on_all_geosets(self, enabled: bool) -> None:
        self.view.set_all_geosets(enabled)

    def stop_playback(self) -> None:
        self.view.stop()
        self.play_button.setText("▶ Play")

    def _toggle_play(self) -> None:
        self.view.toggle()
        self.play_button.setText("⏸ Pause" if self.view.playing else "▶ Play")

    def _reset_camera(self) -> None:
        self.view.yaw, self.view.pitch, self.view.zoom = 35.0, 15.0, 1.0
        self.view.update()

    def _update_fps(self) -> None:
        self.fps_label.setText(f"{self.view.measured_fps:4.1f} fps")

    # -- export -----------------------------------------------------------
    def _ask_save(self, title: str, suffix: str) -> str:
        stem = Path(self.model_path).stem if self.model_path else "model"
        path, _ = QFileDialog.getSaveFileName(self, title, f"{stem}.{suffix}",
                                              f"{suffix.upper()} (*.{suffix})")
        return path

    def _export_obj(self) -> None:
        path = self._ask_save("Сохранить OBJ", "obj")
        if path:
            self.session.export_obj(self.model_path, path,
                                    textures_root=Path(path).parent / "textures")
            self.statusBar().showMessage(f"OBJ: {path}")

    def _export_glb(self) -> None:
        path = self._ask_save("Сохранить glTF", "glb")
        if path:
            self.session.export_glb(self.model_path, path)
            self.statusBar().showMessage(f"GLB: {path}")

    def _export_png(self) -> None:
        path = self._ask_save("Сохранить PNG", "png")
        if path:
            self.view.grabFramebuffer().save(path)
            self.statusBar().showMessage(f"PNG: {path}")

    def _export_raw(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Куда сохранить .mdx")
        if folder:
            self.session.export_raw(self.model_path, folder)
            self.statusBar().showMessage(f"MDX -> {folder}")

    def _export_gif(self) -> None:
        from .pipeline import render_animation, write_gif
        path = self._ask_save("Сохранить GIF", "gif")
        if not path:
            return
        sequence = self.sequence_box.currentText() or None
        self.statusBar().showMessage("рендерю GIF…")
        images, _meta = render_animation(self.session, self.model_path, sequence,
                                         fps=self.fps_box.value(), size=256, frames=20)
        write_gif(images, path, self.fps_box.value())
        self.statusBar().showMessage(f"GIF: {path}")

    def _choose_game(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка Warcraft III")
        if not folder:
            return
        try:
            self.session.close()
            self.session = Session(folder)
        except StormLibError as exc:
            QMessageBox.critical(self, "wc3studio", str(exc))
            return
        self.view.session = self.session
        self._populate_assets()
        self.setWindowTitle(f"wc3studio {__version__} - {self.session.root}")


def run(game_dir: str | None = None, model: str | None = None,
        sequence: str | None = None) -> int:
    """Entry point: ``wc3studio gui --framework qt``."""
    if QApplication.instance() is None:
        app = QApplication(sys.argv[:1])
    else:
        app = QApplication.instance()
    app.setApplicationName("wc3studio")
    try:
        session = Session(game_dir) if game_dir else Session(None)
    except StormLibError as exc:
        found = [str(path) for path in find_game_dirs()]
        message = f"{exc}\n\nНайденные кандидаты:\n" + "\n".join(found or ["(нет)"])
        QMessageBox.critical(None, "wc3studio", message)
        return 2
    window = MainWindow(session, model, sequence)
    window.show()
    code = app.exec()
    session.close()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
