# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.1] - 2026-10-17

Hardening the agent API after a full pass over every operation.

### Fixed

* **An unknown `kind` in `assets.list`/`assets.search` silently returned an empty list** —
  a typo looked like "no assets of that kind". It is now `BAD_PARAM` with the enum in the
  hint. Parameter validation happens before the archives are opened, so a bad request in a
  machine without the game reports `BAD_PARAM`, not `GAME_NOT_FOUND`.
* **Several JSON lines on stdin were parsed as one broken request** — they now behave like
  `--batch`, which is what an agent that pipes a JSONL file expects.

### Added

* `texture.export` — BLP/TGA → PNG with optional resize.
* `ui.strings` — localised interface strings.
* `audio.extract` — copy audio files out by category.

### Performance

* The table database, the joined unit table and the FDF library are cached per session:
  the second `tables.list` costs 0.3 ms instead of 1.6 s, `units.query` 0 ms instead of
  27 ms.

## [1.4.0] - 2026-10-17

An API that an AI agent can use without reading `--help`.

### Added

* **`wc3studio agent`** — self-describing JSON API: one request in, one JSON object out,
  no prompts, no prose on stdout. 15 operations covering search, model info/animations/
  render/export, data tables, units, interface frames, audio and bundles.
  * `--capabilities` returns a manifest of every operation with its parameters and
    result shape, so an agent can discover the surface at runtime;
  * errors carry a stable `code` (`BAD_PARAM`, `MODEL_NOT_FOUND`, `GAME_NOT_FOUND`, …)
    plus `hints` listing the allowed operations, parameters or columns;
  * exit codes are part of the contract: 0 ok, 2 bad request, 3 not found, 4 no game;
  * `--call OP --params JSON`, a request on stdin, or `--batch FILE` for JSONL streams
    (the session stays open, so batch turns cost ~2 ms per request instead of ~0.5 s).
* **`wc3studio mcp`** — dependency-free MCP server on stdio: `initialize`,
  `tools/list`, `tools/call`, `ping`. Every API operation becomes an MCP tool with a
  generated `inputSchema`, so Claude Desktop, Cursor and other MCP clients can call the
  tool straight away.
* **`wc3studio audio`** — the game's audio: 6444 files, 670 MiB, grouped into music,
  ambient, dialogue, unit voice lines, ability sounds and UI. `--durations` measures
  play time by parsing WAV headers (exact) and MP3 frame headers (approximate);
  `--extract` copies them out byte for byte.
* **Python API** — `from wc3studio import Api; Api().call({"op": "model.info", ...})`.

### Fixed

* **Geoset bounds were wrong for many models.** The extents stored in the MDX header do
  not match the model's own vertices in every file (the Sheep reported 76.8 against a
  real maximum of 26.1). They are now derived from the vertices at parse time, which
  also makes `bounds_radius` trustworthy. Found while building `model.info`.
* `Game.names()` rebuilt its 17 000-name list on every call; it is cached now, as is a
  new case-folded name → size map used by the API.

## [1.3.0] - 2026-10-17

Sound, and everything else that belongs to a model.

### Added

* **`sounds` format in the bundle** — the MDX format never references audio, but the game
  keeps a unit's voice lines next to its model (`Units\Human\Footman\FootmanYes1.wav`), so
  a bundle now copies every `.wav`/`.mp3` from the model's folder, right next to the
  exported mesh: Footman gets 18 files, HeroPaladin 22, the Sheep 7, a Town Hall 3.
  Across the archives that is 444 model folders and ~2900 files.
* **`models.json`** — a per-model index of *everything that belongs to that model*: the
  exported files, the portrait model, referenced textures, sounds, triangle/bone/geoset
  counts and the animation names. `--no-index` turns it off.
* **`--formats all`** shorthand for `models,glb,raw,sounds,tables,ui`; the default is now
  `models,glb,sounds`.
* **GUI** — a "Звуки модели" checkbox in the Bundle dialog, and the progress bar knows
  about the sounds and index passes.

### Changed

* The bundle's default formats include sounds (it used to be models + glTF only).

### Notes

* A Human-only trial bundle (71 models, 478 sounds, 213 tables) comes to **93 MiB in
  35 seconds**; the sounds alone are 45 MiB of that.
* A full run with sounds is roughly **2.2 GiB**.

## [1.2.0] - 2026-10-17

Everything at once.

### Added

* **`bundle` command** (`wc3studio/bundle.py`) — one call exports a whole asset pack into
  a folder and, optionally, a single `.zip`:
  * `models` — OBJ + MTL + PNG textures,
  * `glb` — glTF per model **with the skeleton and every animation**,
  * `raw` — the original `.mdx` files,
  * `tables` — all gameplay tables as CSV,
  * `ui` — the interface artwork as PNG,
  * `--previews` — a short GIF per model of its `Stand` animation,
  * plus `manifest.json` with counts, sizes, timings and any per-model errors.
* **`--zip`** writes `<out>.zip` next to the folder; the archive contains the manifest too.
* **GUI** — an "Bundle everything…" dialog in the Export tab: format checkboxes, model
  limit and path filter, live progress bar, and a summary when it finishes.
* **Tests** — 6 new (62 total): zip round-trip, option normalisation, and game-backed
  bundles that check counts, the exported tree, the manifest and that the `.glb` inside a
  bundle really carries animations. The zip-vs-manifest ordering bug was caught this way.

### Notes

* A full bundle of all 3225 models with OBJ + glTF + raw + tables is roughly **2 GiB** and
  takes ~15–20 minutes on this machine; most of the weight is the glTF animation data
  (~0.55 MiB per model). Use `--limit` or `--filter` for a quick subset.

## [1.1.0] - 2026-10-17

The game's numbers and its interface.

### Added

* **Data tables** (`wc3studio/tables.py`) — parser for the SYLK spreadsheets the game
  keeps its gameplay in (a cell is `C;X<col>[;Y<row>];K<value>`, and the row number only
  appears when it changes) plus the INI-like `.txt` files with `[section]` headers.
  `Table` supports column selection, filtering, CSV/JSON export and writing to disk.
* **`TableDatabase`** — discovers all 213 tables (59 SLK + 154 TXT), loads them lazily,
  resolves friendly names (`UnitBalance` → `Units\UnitBalance.slk`, not the Custom/Melee
  copies) and joins the four unit spreadsheets into a single **812-row unit database**
  with localised names, HP, mana, armour, damage, DPS, cost, food, build time and
  ability lists.
* **Interface layer** (`wc3studio/ui.py`) — FDF parser producing a frame tree with
  `INHERITS`/`INHERITS WITHCHILDREN` resolution, nested `Texture`/`String` blocks,
  `StringList` localisation, `IncludeFile` references and both comment styles. Parses all
  86 shipped FDF files (2239 frames, 1849 unique names) with **zero warnings**.
* **`UiLibrary`** — frame index, merged properties through the template chain, texture
  reference resolution (including `.mdl` → `.mdx` and case-insensitive basenames),
  1214 localised UI strings, UI artwork inventory (1561 images, 84 MiB) and PNG export
  with contact sheets.
* **Localisation** — Russian unit names and UI labels are read from the game files
  (`Units\*Strings.txt`, `StringList` blocks).
* **CLI** — new subcommands `tables`, `table`, `units`, `strings`, `ui`, `ui-frame`,
  `ui-textures`, `ui-assets`.
* **GUI** — two new tabs: **Data** (all tables in a tree, double-click opens a filterable
  grid with CSV export) and **UI** (FDF documents with their frames, frame browser with
  resolved templates, UI artwork dump and contact sheet).
* **Tests** — 56 total (was 25): synthetic SLK/INI/FDF fixtures that run everywhere plus
  game-backed checks (table shapes, unit database contents, zero-warning FDF parsing,
  localised strings, artwork inventory).

## [1.0.0] - 2026-10-17

First public release.

### Added

* **MPQ** — ctypes binding to StormLib (x64 DLL bundled), `Archive` and `Game` types,
  the four Warcraft III archives read as a patch chain, case-insensitive lookup of
  ~17 000 internal names, raw extraction.
* **MDX** — parser for the binary model format: version, sequences, global sequences,
  textures, materials and layers, geosets, geoset animations, bones and helpers with
  their animation tracks, pivots.
* **BLP** — decoder for both BLP1 variants: paletted (BGRA palette + optional alpha) and
  JPEG (shared header block that ends on the `FF C0` marker).
* **Animation** — skeleton evaluation (`T(pivot)·T(t)·R·S·T(-pivot)` per node),
  shortest-path quaternion slerp, linear blend skinning with the model's matrix groups.
* **Renderer** — dependency-light software rasteriser: z-buffer, perspective-correct
  texture mapping, alpha blending and additive passes, contact shadow, fixed cameras for
  animation playback.
* **OBJ export** — Wavefront OBJ + MTL with textures converted to PNG, shared texture
  folder, animation-aware geoset selection.
* **glTF export** — binary `.glb` with a skin, per-sequence animations, embedded PNG
  textures, and a reader used to verify what was written.
* **CLI** — `archives`, `list`, `info`, `extract`, `convert`, `render`, `animate`,
  `gallery`, `validate`, `doctor`, `gui`.
* **GUI** — Tkinter browser with a lazy file tree, search, mouse-orbit preview,
  animation player, texture tab and export actions; rendering runs on a worker thread.
* **Tests** — 25 tests: a synthetic MDX built in memory (runs everywhere, including CI)
  plus game-backed tests that skip when no installation is present.
* **Docs** — README with screenshots, `docs/gallery.png`, a rendered animation in
  `docs/anim/`, CI workflow, MIT licence and third-party notices.

### Notes

* An MDX node's local matrix is not a plain glTF TRS; the pivot is folded into the node
  translation as `pivot + t - A·pivot` (with `A = R·S`), which is why every animated
  joint gets a translation channel even without its own translation track.
* Windows only, because of StormLib and the registry lookup; 64-bit Python required.
