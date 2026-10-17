"""GPU viewport for models: CPU skinning, OpenGL rasterisation.

The Python renderer costs ~150 ms per 512x512 frame because it rasterises in numpy.
Here only the skinning stays on the CPU (a few milliseconds of array work) and the
triangles are drawn by the graphics card, which is what makes playback smooth.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QMatrix4x4
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
                             QOpenGLTexture, QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .render import (DEFAULT_BG, FILTER_ADDITIVE, camera_for_bounds, collect,
                     fit_camera, look_at_matrix, near_plane, perspective_matrix)

VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;
layout(location = 2) in vec2 aUV;
uniform mat4 uMVP;
out vec2 vUV;
out vec3 vNormal;
void main() {
    vUV = aUV;
    vNormal = aNormal;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core
in vec2 vUV;
in vec3 vNormal;
uniform sampler2D uTexture;
uniform vec3 uLight;
uniform vec4 uColor;
out vec4 fragColor;
void main() {
    vec4 base = uColor;
    base *= texture(uTexture, vUV);
    if (base.a < 0.05) discard;
    vec3 normal = normalize(vNormal);
    float diffuse = abs(dot(normal, uLight));
    float shade = 0.45 + 0.55 * diffuse;
    fragColor = vec4(base.rgb * shade, base.a);
}
"""


def draws_item(filter_mode: int) -> bool:
    """Additive planes are soft glow gradients; the CPU preview skips them too.

    The GPU path used to skip FILTER_BLEND (2) instead of FILTER_ADDITIVE (3), so
    semi-transparent effects disappeared and glow planes were drawn as solid quads.
    """
    return filter_mode != FILTER_ADDITIVE


class _Item:
    """One drawable geoset: static indices, dynamic vertices."""

    __slots__ = ("vertex_buffer", "vao", "count", "faces", "uv", "normal",
                 "texture", "alpha", "blend", "filter_mode")

    def __init__(self):
        self.vertex_buffer = None
        self.vao = None
        self.count = 0
        self.faces = None
        self.uv = None
        self.normal = None
        self.texture = None
        self.alpha = 1.0
        self.blend = False
        self.filter_mode = 0


class ModelView(QOpenGLWidget):
    """An orbitable, playable model viewport."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.model = None
        self.model_path = ""
        self.animator = None
        self.sequence = None
        self.frame = 0.0
        self.fps = 30.0
        self.yaw = 35.0
        self.pitch = 15.0
        self.zoom = 1.0
        self.fov = 32.0
        self.all_geosets = False
        self.playing = False
        self.camera = None
        self.items: list[_Item] = []
        self.program = None
        self.textures: dict[int, QOpenGLTexture] = {}
        self.bg = DEFAULT_BG
        self._last_time = time.perf_counter()
        self._frames = 0
        self.measured_fps = 0.0
        self._drag = None
        self._uploaded_frame = None
        self._white = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- model ------------------------------------------------------------
    def _ensure_context(self) -> bool:
        """Make the widget's context current; True when we must release it again."""
        context = self.context()
        if context is None:
            return False
        if QOpenGLContext.currentContext() is context:
            return False
        self.makeCurrent()
        return True

    def _release_context(self, acquired: bool) -> None:
        if acquired:
            self.doneCurrent()

    def _clear_textures(self) -> None:
        """Drop the GPU textures of the previous model; the cache is per model."""
        if not self.textures:
            return
        acquired = self._ensure_context()
        try:
            for texture in self.textures.values():
                texture.destroy()
        finally:
            self._release_context(acquired)
        self.textures.clear()
        self._white = None

    def set_model(self, internal_path: str) -> None:
        self._clear_textures()
        self._uploaded_frame = None
        self.model_path = internal_path
        self.model = self.session.load(internal_path)
        self.animator = self.session.animator_for(self.model)
        self.sequence = self.model.sequences[0] if self.model.sequences else None
        self.frame = float(self.sequence["start"]) if self.sequence else 0.0
        self.yaw, self.pitch, self.zoom = 35.0, 15.0, 1.0
        self._rebuild_camera()
        if self.context() is not None and self.program is not None:
            self._rebuild_geometry()
        self.update()

    def set_all_geosets(self, enabled: bool) -> None:
        """Hidden geosets are a different item list *and* a different bounding box."""
        self.all_geosets = bool(enabled)
        self._rebuild_camera()
        self._uploaded_frame = None
        if self.context() is not None and self.program is not None:
            self._rebuild_geometry()
        self.update()

    def set_sequence(self, name: str) -> None:
        sequence = self.model.sequence(name) if self.model else None
        if sequence is None:
            return
        self.sequence = sequence
        self.frame = float(sequence["start"])
        self._rebuild_camera()
        self.update()

    def has_geometry(self) -> bool:
        return bool(self.items)

    def sequences(self) -> list:
        return list(self.model.sequences) if self.model else []

    def _rebuild_camera(self) -> None:
        """Frame the current sequence, not the union of the first four.

        Unioning the starts of several sequences stretched the camera by up to 114x
        (a Death pose throws the bounds far away, and some units move between
        sequences), so effects ended up a fraction of a pixel. Only solid items are
        measured, exactly like the CPU renderer does.
        """
        if self.model is None:
            self.camera = None
            return
        sequence = self.sequence or (self.model.sequences[0] if self.model.sequences else None)
        frame = float(sequence["start"]) if sequence else 0.0
        low = np.full(3, np.inf)
        high = np.full(3, -np.inf)
        for item in collect(self.model, self.session.resources,
                            all_geosets=self.all_geosets, animator=self.animator,
                            frame=frame, local_time=0.0):
            if not item["solid"]:
                continue
            points = item["pos"]
            if len(points):
                low = np.minimum(low, points.min(axis=0))
                high = np.maximum(high, points.max(axis=0))
        if not np.isfinite(low).all():
            self.camera = None
            return
        self.camera = camera_for_bounds(low, high)

    # -- playback ---------------------------------------------------------
    def play(self) -> None:
        self.playing = True
        self._last_time = time.perf_counter()
        self.timer.start(max(5, int(1000 / self.fps)))

    def stop(self) -> None:
        self.playing = False
        self.timer.stop()

    def toggle(self) -> None:
        self.stop() if self.playing else self.play()

    def _tick(self) -> None:
        if not self.playing or self.sequence is None:
            return
        # MDX frames are milliseconds, so one second of playback is 1000 ms of frames
        self.frame += 1000.0 / max(self.fps, 1.0)
        if self.frame > self.sequence["end"]:
            self.frame = float(self.sequence["start"])
        self.update()

    # -- gl ---------------------------------------------------------------
    def initializeGL(self) -> None:
        functions = self.context().functions()
        self.functions = functions
        functions.glClearColor(self.bg[0] / 255, self.bg[1] / 255, self.bg[2] / 255, 1.0)
        functions.glEnable(0x0B71)  # GL_DEPTH_TEST
        functions.glEnable(0x0B44)  # GL_CULL_FACE off: WC3 quads are double sided
        functions.glDisable(0x0B44)

        self.program = QOpenGLShaderProgram(self)
        self.program.addShaderFromSourceCode(QOpenGLShader.Vertex, VERTEX_SHADER)
        self.program.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAGMENT_SHADER)
        self.program.link()
        if self.model is not None:
            self._rebuild_geometry()

    def _texture_for(self, array, key) -> QOpenGLTexture | None:
        """collect() hands out float arrays in 0..1; OpenGL wants RGBA bytes."""
        if array is None:
            return None
        cached = self.textures.get(key)
        if cached is not None:
            return cached
        pixels = np.asarray(array)
        if pixels.dtype != np.uint8:
            pixels = np.clip(pixels * 255.0, 0, 255).astype(np.uint8)
        if pixels.ndim == 2:
            pixels = np.dstack([pixels] * 3)
        if pixels.shape[2] == 3:
            alpha = np.full(pixels.shape[:2] + (1,), 255, dtype=np.uint8)
            pixels = np.concatenate([pixels, alpha], axis=2)
        pixels = np.ascontiguousarray(pixels)
        qimage = QImage(pixels.data, pixels.shape[1], pixels.shape[0],
                        QImage.Format_RGBA8888).copy()
        texture = QOpenGLTexture(qimage, QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
        texture.setMinificationFilter(QOpenGLTexture.Filter.Nearest)
        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
        self.textures[key] = texture
        return texture

    def _white_texture(self) -> QOpenGLTexture:
        if getattr(self, "_white", None) is None:
            pixels = np.full((1, 1, 4), 255, dtype=np.uint8)
            qimage = QImage(pixels.data, 1, 1, QImage.Format_RGBA8888).copy()
            self._white = QOpenGLTexture(qimage,
                                         QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
        return self._white

    def _rebuild_geometry(self) -> None:
        if self.program is None or self.model is None or self.context() is None:
            return
        acquired = self._ensure_context()
        try:
            self._rebuild_geometry_inner()
        finally:
            self._release_context(acquired)

    def _rebuild_geometry_inner(self) -> None:
        if self.program is None or self.model is None:
            return
        items = collect(self.model, self.session.resources, all_geosets=self.all_geosets,
                        animator=self.animator, frame=self.frame)
        for item in self.items:
            if item.vertex_buffer is not None:
                item.vertex_buffer.destroy()
            if item.vao is not None:
                item.vao.destroy()
        self.items = []
        self._uploaded_frame = None
        for source in items:
            positions = np.asarray(source["pos"], dtype=np.float32)
            normals = source["nrm"]
            if normals is None:
                normals = np.zeros_like(positions)
            uv = source["uv"]
            if uv is None:
                uv = np.zeros((len(positions), 2), dtype=np.float32)
            entry = _Item()
            entry.faces = np.asarray(source["faces"], dtype=np.int64).reshape(-1, 3)
            entry.count = int(entry.faces.size)
            entry.normal = normals
            entry.uv = uv
            positions = positions[entry.faces.ravel()]
            normals = normals[entry.faces.ravel()]
            uv = uv[entry.faces.ravel()]
            entry.alpha = float(source["alpha"])
            entry.blend = entry.alpha < 0.999
            entry.filter_mode = int(source["filter_mode"])
            entry.texture = self._texture_for(source["tex"],
                                              source.get("tex_path") or source["geoset"])

            entry.vao = QOpenGLVertexArrayObject()
            entry.vao.create()
            entry.vao.bind()

            entry.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            entry.vertex_buffer.create()
            entry.vertex_buffer.bind()
            entry.vertex_buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
            interleaved = np.hstack([positions, normals, uv]).astype(np.float32)
            entry.vertex_buffer.allocate(interleaved.tobytes(), interleaved.nbytes)

            program = self.program
            program.bind()
            stride = 8 * 4
            program.enableAttributeArray(0)
            program.setAttributeBuffer(0, 0x1406, 0, 3, stride)      # GL_FLOAT
            program.enableAttributeArray(1)
            program.setAttributeBuffer(1, 0x1406, 3 * 4, 3, stride)
            program.enableAttributeArray(2)
            program.setAttributeBuffer(2, 0x1406, 6 * 4, 2, stride)
            entry.vao.release()
            program.release()
            self.items.append(entry)

    def _update_vertices(self) -> None:
        """Re-skin on the CPU and push new positions/normals into the buffers."""
        if self.model is None:
            return
        sources = collect(self.model, self.session.resources, all_geosets=self.all_geosets,
                          animator=self.animator, frame=self.frame)
        if len(sources) != len(self.items):
            self._rebuild_geometry()
            return
        for entry, source in zip(self.items, sources):
            positions = np.asarray(source["pos"], dtype=np.float32)
            normals = source["nrm"]
            if normals is None:
                normals = np.zeros_like(positions)
            uv = source["uv"]
            if uv is None or len(uv) != len(positions):
                uv = np.zeros((len(positions), 2), dtype=np.float32)
            order = entry.faces.ravel()
            interleaved = np.hstack([positions[order], normals[order],
                                     uv[order]]).astype(np.float32)
            binds = [entry.vertex_buffer.bind(), entry.vao.bind()]
            del binds
            entry.vertex_buffer.write(0, interleaved.tobytes(), interleaved.nbytes)
            entry.vertex_buffer.release()
            entry.vao.release()
        self._uploaded_frame = self.frame

    def paintGL(self) -> None:
        functions = self.functions
        functions.glClear(0x4100)  # GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT
        if self.model is None or self.program is None or not self.items:
            return
        if self.playing or self._uploaded_frame != self.frame:
            self._update_vertices()

        camera = self.camera or {"center": np.zeros(3), "radius": 1.0}
        center = np.asarray(camera["center"], dtype=np.float64)
        radius = float(camera["radius"])
        yaw, pitch = np.radians(self.yaw), np.radians(self.pitch)
        direction = np.array([np.sin(yaw) * np.cos(pitch),
                              -np.cos(yaw) * np.cos(pitch),
                              np.sin(pitch)])
        distance = radius / np.tan(np.radians(self.fov) / 2.0) * 1.15 / max(self.zoom, 1e-3)
        eye = center + direction * distance
        view = look_at_matrix(eye, center)
        aspect = self.width() / max(self.height(), 1)
        near = near_plane(distance)
        far = distance + radius * 6.0
        projection = perspective_matrix(1.0 / np.tan(np.radians(self.fov) / 2.0),
                                        aspect, near, far)
        mvp = projection @ view

        functions.glViewport(0, 0, self.width(), self.height())
        self.program.bind()
        self.program.setUniformValue("uMVP", QMatrix4x4(*mvp.flatten()))
        light = np.array([-0.35, -0.55, 0.75])
        light /= np.linalg.norm(light)
        self.program.setUniformValue("uLight", float(light[0]), float(light[1]), float(light[2]))

        drawables = [entry for entry in self.items if draws_item(entry.filter_mode)] \
            or self.items   # all-additive model: draw it rather than nothing
        for entry in drawables:
            if entry.blend:
                functions.glEnable(0x0BE2)  # GL_BLEND
                functions.glBlendFunc(0x0302, 0x0303)  # SRC_ALPHA, ONE_MINUS_SRC_ALPHA
            else:
                functions.glDisable(0x0BE2)
            entry.vao.bind()
            texture = entry.texture or self._white_texture()
            texture.bind(0)
            self.program.setUniformValue("uTexture", 0)
            self.program.setUniformValue("uColor", 1.0, 1.0, 1.0, entry.alpha)
            functions.glDrawArrays(0x0004, 0, entry.count)  # GL_TRIANGLES
            entry.vao.release()
            texture.release(0)
        self.program.release()

        self._frames += 1
        now = time.perf_counter()
        if now - self._last_time >= 0.5:
            self.measured_fps = self._frames / (now - self._last_time)
            self._frames = 0
            self._last_time = now

    # -- input ------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        self._drag = (event.position().x(), event.position().y())

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        dx = event.position().x() - self._drag[0]
        dy = event.position().y() - self._drag[1]
        self._drag = (event.position().x(), event.position().y())
        self.yaw = (self.yaw - dx * 0.4) % 360
        self.pitch = float(np.clip(self.pitch + dy * 0.3, -85, 85))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120.0
        self.zoom = float(np.clip(self.zoom * (1.15 ** steps), 0.2, 8.0))
        self.update()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Space:
            self.toggle()
        elif key == Qt.Key_Left:
            self.yaw = (self.yaw + 10) % 360
            self.update()
        elif key == Qt.Key_Right:
            self.yaw = (self.yaw - 10) % 360
            self.update()
        else:
            super().keyPressEvent(event)
