"""Audit every model: parse, collect, render on the CPU, optionally on the GPU.

    python tools/sweep_models.py                     # CPU audit over all models
    python tools/sweep_models.py --limit 200         # quick pass
    python tools/sweep_models.py --gl                # also drive the OpenGL viewport

The GL pass needs PySide6 and a graphics context; it opens a small window, grabs one
frame per model and compares it with the CPU result. Nothing is written outside ``--out``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BACKGROUND = (22, 24, 30)


def _blank_fraction_pil(image, tolerance: int = 14) -> float:
    import numpy as np
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    distance = np.abs(pixels - np.array(BACKGROUND, dtype=np.float32)).sum(axis=2)
    return float((distance < tolerance).mean())


def _blank_fraction_qt(image, tolerance: int = 14) -> float:
    import numpy as np
    from PySide6.QtGui import QImage
    image = image.convertToFormat(QImage.Format_RGBA8888)
    pixels = np.frombuffer(image.constBits().tobytes(), dtype=np.uint8).reshape(
        image.height(), image.width(), 4)
    distance = np.abs(pixels[:, :, :3].astype(np.float32) - np.array(BACKGROUND)).sum(axis=2)
    return float((distance < tolerance).mean())


def cpu_audit(session, models, out: Path, report: list) -> dict:
    """Parse + collect + render every model on the CPU."""
    import numpy as np
    from wc3studio.render import collect

    result = {"checked": 0, "exceptions": [], "no_geometry": [], "no_texture": [],
              "no_sequences": [], "nan_positions": [], "blank_frames": []}
    started = time.perf_counter()
    for index, internal in enumerate(models, 1):
        try:
            model = session.load(internal)
            frame = model.sequences[0]["start"] if model.sequences else None
            animator = session.animator(internal) if model.nodes and frame is not None else None
            items = collect(model, session.resources, animator=animator, frame=frame)
            result["checked"] += 1
            if not model.sequences:
                result["no_sequences"].append(internal)
            if not items:
                result["no_geometry"].append(internal)
            elif not any(item["tex"] is not None for item in items):
                result["no_texture"].append(internal)
            for item in items:
                if not np.isfinite(item["pos"]).all():
                    result["nan_positions"].append(internal)
                    break
            image = session.render(internal, size=96, frame=frame, yaw=35, pitch=15)
            if _blank_fraction_pil(image) > 0.999:
                result["blank_frames"].append(internal)
        except Exception as exc:  # noqa: BLE001 - one model must not stop the sweep
            result["exceptions"].append({"model": internal,
                                         "error": f"{type(exc).__name__}: {exc}",
                                         "trace": traceback.format_exc(limit=2)})
        if index % 100 == 0 or index == len(models):
            elapsed = time.perf_counter() - started
            line = (f"cpu  {index}/{len(models)}  {index / max(elapsed, 1e-6):5.1f} мод/с  "
                    f"exc={len(result['exceptions'])}")
            report.append(line)
            print(line, flush=True)
            session._models.clear()
            if hasattr(session, "_animators"):
                session._animators.clear()
    result["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    return result


def gl_audit(session, models, report: list) -> dict:
    """Drive the OpenGL viewport and check it draws something for every model."""
    from PySide6.QtWidgets import QApplication
    from wc3studio.gui_qt import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = MainWindow(session)
    window.resize(160, 160)
    window.show()
    app.processEvents()

    result = {"checked": 0, "exceptions": [], "blank": []}
    started = time.perf_counter()
    for index, internal in enumerate(models, 1):
        try:
            view = window.view
            view._clear_textures()
            view.set_model(internal)
            view.stop()
            image = view.grabFramebuffer()
            view.model  # noqa: B018 - make sure the model is loaded
            result["checked"] += 1
            if _blank_fraction_qt(image) > 0.999:
                result["blank"].append(internal)
        except Exception as exc:  # noqa: BLE001
            result["exceptions"].append({"model": internal,
                                         "error": f"{type(exc).__name__}: {exc}"})
        if index % 100 == 0 or index == len(models):
            elapsed = time.perf_counter() - started
            line = (f"gl   {index}/{len(models)}  {index / max(elapsed, 1e-6):5.1f} мод/с  "
                    f"blank={len(result['blank'])}")
            report.append(line)
            print(line, flush=True)
            session._models.clear()
            if hasattr(session, "_animators"):
                session._animators.clear()
    window.close()
    result["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    return result


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="build/sweep", help="where to write the report")
    parser.add_argument("--limit", type=int, default=0, help="only the first N models")
    parser.add_argument("--gl", action="store_true", help="also sweep the OpenGL viewport")
    parser.add_argument("--game", help="Warcraft III folder (default: autodetect)")
    args = parser.parse_args(argv)

    from wc3studio.pipeline import Session

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    session = Session(args.game) if args.game else Session(None)
    models = session.models()
    if args.limit:
        models = models[: args.limit]
    print(f"моделей: {len(models)}", flush=True)

    report: list[str] = [f"моделей: {len(models)}"]
    payload = {"cpu": cpu_audit(session, models, out, report)}
    if args.gl:
        payload["gl"] = gl_audit(session, models, report)

    (out / "sweep.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    summary = []
    for name, data in payload.items():
        summary.append(f"{name}: проверено {data['checked']}, "
                       f"исключений {len(data['exceptions'])}")
        for key, value in data.items():
            if key in ("checked", "elapsed_seconds", "exceptions"):
                continue
            if isinstance(value, list) and value:
                summary.append(f"   {key}: {len(value)}")
    (out / "summary.txt").write_text("\n".join(report + summary), encoding="utf-8")
    print("\n".join(summary))
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
