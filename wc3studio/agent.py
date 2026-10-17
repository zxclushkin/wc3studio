"""Agent-friendly API: one JSON in, one JSON out, self-describing.

An AI agent should not have to read --help or parse Russian errors: it asks for
``capabilities``, gets a manifest with every operation and its parameters, then calls
operations with a JSON request. Every reply is a JSON object::

    {"ok": true,  "op": "model.info", "data": {...}, "elapsed_ms": 12}
    {"ok": false, "op": "model.info", "error": {"code": "MODEL_NOT_FOUND", ...}}

Use it from Python::

    from wc3studio.agent import Api
    api = Api()
    api.call({"op": "assets.search", "query": "footman", "limit": 5})
    api.close()

Or from the shell (``wc3studio agent``, see :func:`dispatch_cli`).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import __version__

__all__ = ["Api", "Op", "OPS", "ApiError", "capabilities", "dispatch_cli", "serve_mcp"]


class ApiError(Exception):
    """Error with a stable machine code, so an agent can react without parsing prose."""

    def __init__(self, code: str, message: str, hints: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hints = hints or []


@dataclass
class Op:
    name: str
    description: str
    params: dict
    returns: str
    handler: Callable


OPS: dict[str, Op] = {}


def operation(name: str, description: str, params: dict | None = None, returns: str = ""):
    def register(handler: Callable) -> Callable:
        OPS[name] = Op(name, description, params or {}, returns, handler)
        return handler
    return register


# --------------------------------------------------------------------------- helpers

def _session(api: "Api"):
    if api.session is None:
        from .pipeline import Session
        from .stormlib import StormLibError
        try:
            api.session = Session(api.game_dir) if api.game_dir else Session(None)
        except StormLibError as exc:
            raise ApiError("GAME_NOT_FOUND", str(exc),
                           ["pass game=/path/to/Warcraft III",
                            "call doctor to see what was found"]) from exc
    return api.session


def _model(session, wanted: str):
    try:
        internal = session.resolve(wanted)
    except LookupError as exc:
        raise ApiError("MODEL_NOT_FOUND", str(exc),
                       ["call assets.search to find the exact path"]) from exc
    return internal


def _number(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- ops

@operation("capabilities", "List every operation with its parameters and result shape",
           {}, "server, version, operations[]")
def _capabilities(api, **_):
    return capabilities()


@operation("doctor", "Check the environment: game found, archives, library", {}, "checks[]")
def _doctor(api, **_):
    from .stormlib import find_game_dirs
    game = None
    found = [str(path) for path in find_game_dirs()]
    checks = []
    try:
        session = _session(api)
        game = str(session.root)
        checks.append({"name": "archives", "ok": True, "detail": game})
        checks.append({"name": "files", "ok": True, "detail": f"{len(session.names())} names"})
    except ApiError as exc:
        checks.append({"name": "archives", "ok": False, "detail": exc.message})
    checks.append({"name": "candidates", "ok": bool(found), "detail": found})
    return {"version": __version__, "game": game, "checks": checks}


@operation("assets.search", "Find assets by substring across models, textures, audio, ui, maps, tables",
           {"query": {"type": "string", "required": True},
            "kinds": {"type": "array", "default": ["model"]},
            "limit": {"type": "integer", "default": 20}},
           "matches[{kind, path, size}]")
def _assets_search(api, query="", kinds=None, limit=20, **_):
    session = _session(api)
    kinds = kinds or ["model"]
    suffixes = {"model": (".mdx", ".mdl"), "texture": (".blp", ".tga"),
                "audio": (".wav", ".mp3"), "map": (".w3x", ".w3m", ".w3n"),
                "ui": (".fdf",), "table": (".slk", ".txt")}
    wanted = []
    for kind in kinds:
        wanted.extend(suffixes.get(kind, ()))
    needle = (query or "").lower()
    matches = []
    for name in session.names():
        lower = name.lower()
        if wanted and not lower.endswith(tuple(wanted)):
            continue
        if needle and needle not in lower:
            continue
        matches.append({"kind": _kind_of(lower), "path": name, "size": session.size(name)})
        if len(matches) >= int(limit):
            break
    return {"query": query, "kinds": kinds, "matches": matches, "count": len(matches)}


def _kind_of(lower: str) -> str:
    if lower.endswith((".mdx", ".mdl")):
        return "model"
    if lower.endswith((".blp", ".tga")):
        return "texture"
    if lower.endswith((".wav", ".mp3")):
        return "audio"
    if lower.endswith((".w3x", ".w3m", ".w3n")):
        return "map"
    if lower.endswith(".fdf"):
        return "ui"
    if lower.endswith((".slk", ".txt")):
        return "table"
    return "other"


@operation("assets.list", "List assets of one kind",
           {"kind": {"type": "string", "default": "model",
                     "enum": ["model", "texture", "audio", "map", "ui", "table"]},
            "filter": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 50}},
           "items[{path, size}], total")
def _assets_list(api, kind="model", filter="", limit=50, **_):
    session = _session(api)
    suffixes = {"model": (".mdx", ".mdl"), "texture": (".blp", ".tga"),
                "audio": (".wav", ".mp3"), "map": (".w3x", ".w3m", ".w3n"),
                "ui": (".fdf",), "table": (".slk", ".txt")}
    wanted = suffixes.get(kind, ())
    needle = (filter or "").lower()
    items = [{"path": name, "size": session.size(name)}
             for name in session.names()
             if name.lower().endswith(wanted) and (not needle or needle in name.lower())]
    return {"kind": kind, "total": len(items), "items": items[: int(limit)]}


@operation("model.info", "Everything about one model: geosets, materials, bones, sequences, bounds",
           {"model": {"type": "string", "required": True}},
           "path, geosets, materials, textures, bones, triangles, sequences[], bounds")
def _model_info(api, model="", **_):
    session = _session(api)
    internal = _model(session, model)
    parsed = session.load(internal)
    low = [min(g.min_extent[axis] for g in parsed.geosets) if parsed.geosets else 0.0
           for axis in range(3)]
    high = [max(g.max_extent[axis] for g in parsed.geosets) if parsed.geosets else 0.0
            for axis in range(3)]
    low = [float(value) for value in low]
    high = [float(value) for value in high]
    return {
        "path": internal,
        "geosets": len(parsed.geosets),
        "materials": len(parsed.materials),
        "textures": [texture.path for texture in parsed.textures if texture.path],
        "bones": len(parsed.nodes),
        "triangles": parsed.triangle_count,
        "sequences": [{"name": sequence["name"],
                       "start": sequence["start"], "end": sequence["end"]}
                      for sequence in parsed.sequences],
        "bounds": {"min": low, "max": high},
        "bytes": session.size(internal),
    }


@operation("model.animations", "Animation sequences of a model",
           {"model": {"type": "string", "required": True}},
           "sequences[{name, start, end, duration_ms}]")
def _model_animations(api, model="", **_):
    session = _session(api)
    internal = _model(session, model)
    parsed = session.load(internal)
    sequences = []
    for sequence in parsed.sequences:
        span = sequence["end"] - sequence["start"]
        sequences.append({"name": sequence["name"], "start": sequence["start"],
                          "end": sequence["end"], "duration_ms": span})
    return {"path": internal, "count": len(sequences), "sequences": sequences}


@operation("model.render", "Render a PNG preview of a model",
           {"model": {"type": "string", "required": True},
            "out": {"type": "string", "required": True},
            "size": {"type": "integer", "default": 512},
            "yaw": {"type": "number", "default": 35},
            "pitch": {"type": "number", "default": 15},
            "sequence": {"type": "string", "default": ""},
            "time": {"type": "number", "default": 0.0},
            "zoom": {"type": "number", "default": 1.0}},
           "out, width, height, bytes")
def _model_render(api, model="", out="", size=512, yaw=35, pitch=15,
                  sequence="", time=0.0, zoom=1.0, **_):
    session = _session(api)
    internal = _model(session, model)
    parsed = session.load(internal)
    frame = None
    if sequence:
        chosen = parsed.sequence(sequence)
        if chosen is None:
            raise ApiError("BAD_PARAM", f"no sequence {sequence!r}",
                           [f"sequences: {', '.join(s['name'] for s in parsed.sequences)}"])
        frame = chosen["start"]
    image = session.render(internal, size=int(size), yaw=_number(yaw, 35),
                           pitch=_number(pitch, 15), zoom=_number(zoom, 1.0),
                           frame=frame, local_time=_number(time, 0.0))
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return {"path": internal, "out": str(target), "width": image.width,
            "height": image.height, "bytes": target.stat().st_size}


@operation("model.export", "Export a model to OBJ (+textures), glTF or raw .mdx",
           {"model": {"type": "string", "required": True},
            "out": {"type": "string", "required": True},
            "format": {"type": "string", "default": "glb", "enum": ["obj", "glb", "mdx"]},
            "sequences": {"type": "array", "default": []}},
           "out, files[], bytes")
def _model_export(api, model="", out="", format="glb", sequences=None, **_):
    session = _session(api)
    internal = _model(session, model)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    files = []
    if format == "obj":
        session.export_obj(internal, target)
        files = [str(target)]
        parent = target.parent
        stem = target.stem
        files.extend(str(path) for path in parent.glob(stem + ".mtl"))
        files.extend(str(path) for path in (parent / "textures").rglob("*.png")
                     if (parent / "textures").exists())
    elif format == "glb":
        session.export_glb(internal, target, sequences=sequences or None)
        files = [str(target)]
    elif format == "mdx":
        root = target.parent
        size = session.export_raw(internal, root)
        files = [str(root / internal.replace("\\", "/"))]
        if not size:
            raise ApiError("EXPORT_FAILED", f"could not read {internal}")
    else:
        raise ApiError("BAD_PARAM", f"unknown format {format!r}",
                       ["use obj, glb or mdx"])
    total = sum(Path(path).stat().st_size for path in files if Path(path).exists())
    return {"path": internal, "format": format, "files": files[:50], "bytes": total}


@operation("tables.list", "List gameplay data tables",
           {"filter": {"type": "string", "default": ""}, "limit": {"type": "integer",
                                                                   "default": 50}},
           "tables[{name, kind, rows, columns}], total")
def _tables_list(api, filter="", limit=50, **_):
    from .tables import TableDatabase
    session = _session(api)
    database = TableDatabase(session.game)
    rows = database.tables_summary()
    needle = (filter or "").lower()
    if needle:
        rows = [row for row in rows if needle in row[0].lower()]
    rows.sort(key=lambda row: row[0].lower())
    return {"total": len(rows),
            "tables": [{"name": name, "kind": kind, "rows": count, "columns": columns}
                       for name, kind, count, columns in rows[: int(limit)]]}


@operation("tables.query", "Read a data table with optional column selection and filters",
           {"table": {"type": "string", "required": True},
            "columns": {"type": "array", "default": []},
            "where": {"type": "object", "default": {}},
            "limit": {"type": "integer", "default": 50}},
           "columns[], rows[][], total, matched")
def _tables_query(api, table="", columns=None, where=None, limit=50, **_):
    from .tables import TableDatabase
    session = _session(api)
    database = TableDatabase(session.game)
    try:
        resolved = database._match_table_name(table)
        data = database.load(resolved)
    except (FileNotFoundError, LookupError) as exc:
        raise ApiError("TABLE_NOT_FOUND", str(exc),
                       ["call tables.list to see the names"]) from exc
    for column, value in (where or {}).items():
        try:
            data = data.where(**{column: str(value)})
        except KeyError as exc:
            raise ApiError("BAD_PARAM", f"no column {column!r}",
                           [f"columns: {', '.join(data.columns[:20])}"]) from exc
    if columns:
        try:
            data = data.select(list(columns))
        except KeyError as exc:
            raise ApiError("BAD_PARAM", str(exc),
                           [f"columns: {', '.join(data.columns[:20])}"]) from exc
    total = data.row_count
    rows = [[str(cell) for cell in row] for row in data.rows[: int(limit)]]
    return {"table": resolved, "columns": list(data.columns), "total": total,
            "matched": total, "rows": rows}


@operation("units.query", "The joined unit database (names, stats, costs)",
           {"filter": {"type": "string", "default": ""},
            "columns": {"type": "array", "default": []},
            "limit": {"type": "integer", "default": 20}},
           "columns[], rows[][], total")
def _units_query(api, filter="", columns=None, limit=20, **_):
    from .tables import TableDatabase
    session = _session(api)
    units = TableDatabase(session.game).units()
    if filter:
        needle = filter.lower()
        units = units.filter(lambda row, _record: any(needle in str(cell).lower() for cell in row))
    if columns:
        try:
            units = units.select(list(columns))
        except KeyError as exc:
            raise ApiError("BAD_PARAM", str(exc),
                           [f"columns: {', '.join(units.columns)}"]) from exc
    rows = [[str(cell) for cell in row] for row in units.rows[: int(limit)]]
    return {"columns": list(units.columns), "total": units.row_count, "rows": rows}


@operation("ui.documents", "List FDF interface documents",
           {"filter": {"type": "string", "default": ""}, "limit": {"type": "integer",
                                                                   "default": 50}},
           "documents[{path, frames}], total")
def _ui_documents(api, filter="", limit=50, **_):
    from .ui import UiLibrary
    session = _session(api)
    library = UiLibrary(session.game)
    documents = []
    for path in library.documents(filter):
        try:
            documents.append({"path": path, "frames": len(library.load(path).all_frames())})
        except FileNotFoundError:
            continue
    return {"total": len(documents), "documents": documents[: int(limit)]}


@operation("ui.frame", "Resolved properties of a frame (with its INHERITS chain)",
           {"name": {"type": "string", "required": True}},
           "name, kind, source, inherits[], properties{}, textures[]")
def _ui_frame(api, name="", **_):
    from .ui import UiLibrary
    session = _session(api)
    try:
        details = UiLibrary(session.game).resolve(name)
    except KeyError as exc:
        raise ApiError("FRAME_NOT_FOUND", str(exc),
                       ["call assets.list kind=ui to browse FDF files"]) from exc
    return {"name": details["name"], "kind": details["kind"], "source": details["source"],
            "inherits": details["chain"], "properties": details["properties"],
            "textures": [reference for reference, _resolved in details["textures"]]}


@operation("audio.list", "Inventory of the game's audio",
           {"category": {"type": "string", "default": "",
                         "enum": ["", "music", "ambient", "dialogue", "abilities",
                                  "units", "ui", "other"]},
            "filter": {"type": "string", "default": ""},
            "measure": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50}},
           "categories{}, items[], total")
def _audio_list(api, category="", filter="", measure=False, limit=50, **_):
    from .audio import inventory, summary
    session = _session(api)
    files = inventory(session.game, category=category or None, needle=filter,
                      measure=bool(measure))
    rows = summary(files)
    items = [{"path": item.internal, "bytes": item.size, "category": item.category,
              "seconds": round(item.duration, 3) if item.duration else None}
             for item in files[: int(limit)]]
    return {"categories": rows, "total": len(files), "items": items}


@operation("bundle.build", "Export a full asset bundle (optionally zipped)",
           {"out": {"type": "string", "required": True},
            "formats": {"type": "array", "default": ["models", "glb", "sounds"]},
            "filter": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 0},
            "zip": {"type": "boolean", "default": False},
            "previews": {"type": "boolean", "default": False}},
           "counts{}, bytes, archive, manifest")
def _bundle_build(api, out="", formats=None, filter="", limit=0, zip=False,
                  previews=False, **_):
    from .bundle import BundleOptions, build_bundle
    session = _session(api)
    options = BundleOptions(out=Path(out), formats=set(formats or ["models", "glb", "sounds"]),
                            model_filter=filter, limit=int(limit),
                            archive=bool(zip), previews=bool(previews))
    stats = build_bundle(session, options)
    return {"out": stats["out"], "counts": stats["counts"], "bytes": stats["bytes"],
            "archive": stats.get("archive"), "archive_bytes": stats.get("archive_bytes"),
            "manifest": stats.get("manifest"), "errors": stats["errors"][:10],
            "error_count": len(stats["errors"])}


# --------------------------------------------------------------------------- api

def capabilities() -> dict:
    """Machine-readable manifest of every operation."""
    operations = []
    for op in OPS.values():
        operations.append({
            "name": op.name,
            "description": op.description,
            "params": op.params,
            "returns": op.returns,
        })
    operations.sort(key=lambda item: item["name"])
    return {"server": "wc3studio", "version": __version__, "protocol": "wc3studio-agent/1",
            "operations": operations}


class Api:
    """Keeps one game session open and turns JSON requests into results."""

    def __init__(self, game_dir: str | None = None):
        self.game_dir = game_dir
        self.session = None

    def call(self, request: dict) -> dict:
        op_name = (request or {}).get("op", "")
        started = time.perf_counter()
        if not op_name:
            return self._error("", ApiError("BAD_REQUEST", "missing 'op'",
                                            ["call capabilities to list operations"]))
        op = OPS.get(op_name)
        if op is None:
            return self._error(op_name, ApiError(
                "UNKNOWN_OP", f"no operation {op_name!r}",
                [f"known: {', '.join(sorted(OPS))}"]))
        params = request["params"] if "params" in request else \
            {key: value for key, value in request.items() if key != "op"}
        params = params or {}
        unknown = [key for key in params if key not in op.params]
        if unknown:
            return self._error(op_name, ApiError(
                "BAD_PARAM", f"unknown parameter(s): {', '.join(unknown)}",
                [f"{op_name} accepts: {', '.join(op.params) or 'none'}"]))
        missing = [key for key, spec in op.params.items()
                   if spec.get("required") and key not in params]
        if missing:
            return self._error(op_name, ApiError(
                "BAD_PARAM", f"missing required parameter(s): {', '.join(missing)}",
                [f"{key}: {op.params[key].get('type')} - {op.params[key].get('description', '')}"
                 for key in missing]))
        merged = {}
        for key, spec in op.params.items():
            merged[key] = params.get(key, spec.get("default"))
        try:
            result = op.handler(self, **merged)
            return {"ok": True, "op": op_name, "data": result,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
        except ApiError as exc:
            return self._error(op_name, exc)
        except FileNotFoundError as exc:
            return self._error(op_name, ApiError("NOT_FOUND", str(exc)))
        except Exception as exc:  # noqa: BLE001 - the agent gets a code, not a traceback
            return self._error(op_name, ApiError(
                "INTERNAL", f"{type(exc).__name__}: {exc}",
                ["this is a bug; try a simpler request"]))

    def _error(self, op_name: str, exc: ApiError) -> dict:
        error = {"code": exc.code, "message": exc.message}
        if exc.hints:
            error["hints"] = exc.hints
        return {"ok": False, "op": op_name, "error": error}

    def close(self):
        if self.session is not None:
            self.session.close()
            self.session = None


# --------------------------------------------------------------------------- cli

EXIT_CODES = {"BAD_REQUEST": 2, "BAD_PARAM": 2, "UNKNOWN_OP": 2,
              "GAME_NOT_FOUND": 4, "MODEL_NOT_FOUND": 3, "TABLE_NOT_FOUND": 3,
              "FRAME_NOT_FOUND": 3, "NOT_FOUND": 3}


def _emit(payload: dict, pretty: bool) -> None:
    import sys
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def dispatch_cli(argv: list) -> int:
    """Entry point for ``wc3studio agent``: request on argv, stdin or a batch file."""
    import sys
    pretty = "--pretty" in argv
    argv = [item for item in argv if item != "--pretty"]
    game_dir = None
    if "--game" in argv:
        position = argv.index("--game")
        if position + 1 < len(argv):
            game_dir = argv[position + 1]
            argv = argv[:position] + argv[position + 2:]
    api = Api(game_dir)
    exit_code = 0
    try:
        if "--capabilities" in argv:
            _emit(capabilities(), pretty)
            return 0
        batch = None
        if "--batch" in argv:
            batch = argv[argv.index("--batch") + 1]
        request = None
        if "--call" in argv:
            request = {"op": argv[argv.index("--call") + 1]}
            if "--params" in argv:
                request.update(json.loads(argv[argv.index("--params") + 1]))
        elif batch is None and "--capabilities" not in argv:
            raw = sys.stdin.read()
            if raw.strip():
                request = json.loads(raw)
        if batch:
            with open(batch, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    reply = api.call(json.loads(line))
                    _emit(reply, pretty)
                    exit_code = max(exit_code, _code(reply))
            return exit_code
        if request is None:
            _emit({"ok": False, "error": {"code": "BAD_REQUEST",
                                          "message": "no request: use --call/-params, "
                                                     "--batch FILE or stdin",
                                          "hints": ["wc3studio agent --capabilities"]}},
                  pretty)
            return 2
        reply = api.call(request)
        _emit(reply, pretty)
        return _code(reply)
    except json.JSONDecodeError as exc:
        _emit({"ok": False, "error": {"code": "BAD_JSON", "message": str(exc)}}, pretty)
        return 2
    finally:
        api.close()


def _code(reply: dict) -> int:
    if reply.get("ok"):
        return 0
    return EXIT_CODES.get(reply.get("error", {}).get("code", ""), 1)


# --------------------------------------------------------------------------- mcp

def serve_mcp() -> int:
    """A dependency-free MCP server on stdio, exposing every operation as a tool."""
    import sys

    api = Api()

    def send(message: dict) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": "parse error"}})
            continue
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "wc3studio", "version": __version__}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            tools = [{"name": op.name, "description": op.description,
                      "inputSchema": {"type": "object", "properties": {
                          key: {"type": {"integer": "integer", "number": "number",
                                         "boolean": "boolean", "array": "array",
                                         "object": "object"}.get(spec.get("type"), "string"),
                                **({"enum": spec["enum"]} if "enum" in spec else {}),
                                **({"default": spec["default"]} if "default" in spec else {})}
                          for key, spec in op.params.items()},
                          "required": [key for key, spec in op.params.items()
                                       if spec.get("required")]}}
                     for op in OPS.values()]
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}})
        elif method == "tools/call":
            params = request.get("params") or {}
            reply = api.call({"op": params.get("name", ""),
                              "params": params.get("arguments") or {}})
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": json.dumps(reply, ensure_ascii=False)}],
                "isError": not reply.get("ok")}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif request_id is not None:
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": f"unknown method {method}"}})
    api.close()
    return 0
