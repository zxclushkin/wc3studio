# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.4] - 2026-10-17

Release packaging: the wheel installed cleanly, but the metadata still said "alpha".

### Fixed

* **The `[qt]` extra did not exist**, although the README told people to run
  `pip install -e ".[qt]"`: `pyproject.toml` only had `dev`. Added `qt` and `all`
  extras with `PySide6>=6.5`.
* **`Homepage = "https://github.com/"`** - a placeholder. There are now real
  `Homepage`, `Repository`, `Issues` and `Changelog` URLs.
* **`authors` said "wc3studio"** while LICENSE says Alexey (zxclushkin); the metadata
  now matches the licence.
* **README screenshots used relative paths** (`docs/screenshot.png`), which do not
  render on PyPI. They now point at raw.githubusercontent URLs.
* Classifiers were only "Python 3 / Windows": added Development Status 5,
  OSI MIT, Python 3.10-3.13, `Environment :: Win32 (MS Windows)`, intended audience
  and languages.

### Verified for release

* `python -m pip wheel .` produces `wc3studio-1.6.4-py3-none-any.whl` (423 KiB) with
  `vendor/StormLib.dll`, `LICENSE`, the `wc3studio` entry point and the README as the
  long description.
* Clean `python -m venv` + `pip install <wheel>` + running from outside the repository:
  `wc3studio --version` -> 1.6.4, `doctor` -> "ready" (4 archives, 3225 models),
  `render --model sheep` -> 42 KiB PNG, `agent` -> 20 operations as JSON.

## [1.6.3] - 2026-10-17

Sweep over the whole archive, then two audits in parallel (GPU camera, CPU path) on
3225 models. Two more real defects, found by the auditors and fixed here.

### Fixed

* **189 models rendered as nothing** because every one of their geosets is additive.
  The preview drops additive glow planes on purpose, but for the Wisp, the Bone Yard,
  the Graveyard, the Undead Shipyard and the selection circle there is nothing else, so
  the frame stayed empty. When a model has no other geometry, its additive layers are
  drawn instead of being dropped.
* **Textures baked to an artist's machine never resolved.** Two models reference
  `\\guldan\drive1\...\star5tga.tga`; the files sit in the archives as
  `Textures\star5tga.blp`. `Resources` now falls back to a base-name match and records
  what it substituted in `Resources.fallbacks`.
* **The GPU camera framed the union of four sequences** instead of the active one, and
  only counted the first frames: a Death pose stretched the radius up to 114x, so 43
  effects were a fraction of a pixel (and 6 mini icons were clipped by a 0.05 near
  plane). The camera now measures the current sequence, frames only solid items like the
  CPU renderer, refits when the sequence changes, and the near plane scales down to
  1e-4.
* **Contact-shadow false positives** in the sweep: "content" on the CPU was the shadow
  of five missile models. The sweep now compares against `render(..., shadow=False)`.

### The sweep, before and after

`python tools/sweep_models.py --gl`

| | before 1.6.3 | after |
|---|---|---|
| models | 3225 | 3225 |
| exceptions | 0 | 0 |
| rendered ok | 3162 | **3203** |
| blank frames on GPU | 43 | **1** |
| no geometry (particle-only) | 398 | **209** |
| no texture | 2 | **0** |
| elapsed | 50 s | 54 s |

The remaining blank is `RaiseSkeleton` (0.18% of the frame on both paths - a thin dark
skeleton, below the sweep's 0.1% threshold). The 209 without geometry have no `GEOS`
chunk at all: their visuals come from particles, ribbons and light, which neither
renderer implements.

### Added

* `tools/sweep_models.py` - the audit used here, runnable: `--gl` drives the OpenGL
  viewport, `--limit` for a quick pass, writes `sweep.json` + `summary.txt`.

### Testing

* 7 new regression tests (119 total): additive-only models keep their items, normal
  models still skip glow planes, the dev-path texture resolves, unknown textures still
  report missing, the near plane no longer clips small units, and the camera of an effect
  is at least 2x tighter than the all-sequence union.

## [1.6.2] - 2026-10-17

Full sweep over every model: 3225 checked in 50 seconds, 0 exceptions, and three
more defects that only a whole-archive pass could find.

### Fixed

* **The GPU skipped the wrong filter mode.** It dropped `FILTER_BLEND` (2) where the
  CPU preview drops `FILTER_ADDITIVE` (3), so semi-transparent effect layers vanished
  and additive glow planes were drawn as solid quads. The decision is now a named
  `draws_item()` shared with the CPU convention.
* **Effects were framed by geometry that is never drawn.** The camera used
  `fit_camera()`, which skims every geoset including the huge additive glow planes, so
  a spell effect ended up a few pixels tall: **63 models** rendered essentially empty
  while the CPU render showed them at 8-25% of the frame. The camera now takes its
  bounds from `collect()`, the same items that are drawn.
* **The fragment shader lost its lighting and alpha** to a leftover debug line
  (`fragColor = vec4(texture(...).rgb, 1.0)`), so models were flat and fully opaque.

### The sweep itself

`build/sweep_all.py` walks every model through the GL viewport and compares the frame
with the CPU data path:

| | |
|---|---|
| models checked | 3225 in 50 s |
| exceptions | 0 |
| rendered ok | 3162 |
| blank on GPU | 63 (all fixed by the camera change, 1 effect still frames loosely) |
| no geometry | 398 (target-only models, expected) |
| no textures | 2 (`HeroBloodSphere.MDX`, replaceable-only team colour) |
| UV outside 0..1 | 171 (legitimate tiling) |
| NaN positions | 0 |

### Testing

* 3 new regression tests (112 total): `draws_item` for blend vs additive, and the effect
  camera being tighter than the all-geometry fit.

## [1.6.1] - 2026-10-17

Bug hunt on the new window: four real defects, all found by comparing renders and by
stating checks, all fixed with regression tests.

### Fixed

* **The viewport went blank whenever playback was paused.** `paintGL` read
  `_uploaded_frame` before `__init__` created it, so the first paused frame raised
  `AttributeError`; Qt swallows exceptions inside paintGL, so the user just saw an empty
  canvas. The attribute (and `_white`) is now initialised up front.
* **GL resources were rebuilt without a current context.** Opening a second model or
  toggling "all geosets" recreated textures from a Qt slot, where the context is not
  current: `QOpenGLTexture::setData() requires a valid current context`, the texture was
  never created and the model rendered untextured. `_ensure_context`/`_release_context`
  now wrap every rebuild.
* **"Все геосеты" did not refit the camera** and kept the previous item list: with the same
  number of visible geosets the old textures stayed bound to the new vertices. Toggling now
  refits the camera and rebuilds the items (Town Hall: 3 -> 35 geosets).
* **GPU textures leaked when switching models** - the cache was never cleared. It is now
  per model: after five different models the cache holds one texture instead of eight.
* **Models with no geometry** (`AntiMagicShell`, `feralspirittarget`, `ZombifyTarget`,
  `AItbTarget`, `DemolisherMissile` - target effects with attachments only) opened as an
  empty animated black canvas. The window now stops playback and says the model has no
  geometry.

### Testing

* 4 new regression tests (109 total) that pin the state the bugs corrupted: initialised
  upload state, camera refit on the geoset toggle, `has_geometry` for a target-only model,
  and a real unit still loading with a camera.
* A headless sweep over 40 models (every 80th) reports: 0 exceptions, 0 models without
  textures, 5 without geometry (expected), and UVs outside 0..1 (legitimate tiling).

## [1.6.0] - 2026-10-17

A GPU window, because the old one could not keep up.

### Why

The Tkinter preview was not slow because of Tk: converting a 512x512 frame to a Tk
image costs 1.6 ms. The CPU rasteriser did: **155 ms per frame** (6.4 fps) for a Knight
at 512x512, up to 244 ms at 800x800. Playback therefore never had a chance.

### Added

* **`gui --framework qt`** — a PySide6 window with an **OpenGL viewport**
  (`wc3studio/glview.py`): the CPU only skins the mesh (a few milliseconds of numpy) and
  the graphics card rasterises and textures it. Measured on this machine: **62.6 fps at
  1380x900** for the same Knight, versus 6.4 fps before.
  * orbit with the mouse, zoom with the wheel, `Space` to play/pause;
  * animation list, texture preview, model info and export buttons;
  * asset tree with search; `--model` / `--sequence` to open something straight away.
* `--framework tk` still starts the old window; `auto` prefers Qt and falls back to Tk
  when PySide6 is missing.
* `[qt]` optional dependency: `pip install -e ".[qt]"`.

### Notes

* The GL viewport skips additive glow planes and the contact shadow, exactly like the
  preview path of the CPU renderer; colours are close but not identical.
* Textures now travel to the GPU as RGBA bytes built once per material and reused for
  every frame.

## [1.5.0] - 2026-10-17

Icons for units and abilities.

### Added

* **`wc3studio icons`** / **`wc3studio icon`** (`wc3studio/icons.py`) — the command-button
  library joined with the objects that use it:
  * **1810 records**: 816 units, 665 abilities, 55 upgrades, 274 items;
  * icons come from the `Art` key of the `*Func.txt` tables, the unit's model from
    `Units\unitUI.slk` (`file` without an extension), item models from `ItemData.slk`;
  * names are localised (Russian in this install), and the greyed-out variant is derived
    and verified (`CommandButtons\BTNFoo.blp` → `CommandButtonsDisabled\DISBTNFoo.blp`,
    `PASBTN` included);
  * `--out DIR` writes PNG keeping the archive layout plus `icons.json`, `--sheet` renders
    a contact sheet.
* **`icons.list` / `icon.export`** in the agent API: `icons.list` can also answer
  "what is the icon of this model".
* **`--formats icons`** in the bundle, and `models.json` now carries `icon` + `icon_png`
  for every model that has one (540 of the 3225 models — units and items; doodads,
  critters and effects have no command button, so they correctly report nothing).
* Unit icons resolve for **812 models**; abilities for 665.

### Notes

* 890 unique icon files, ~8 MiB of BLP; exported PNGs are 64x64.
* The icon index builds in 0.7 s and is cached per agent session.

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
