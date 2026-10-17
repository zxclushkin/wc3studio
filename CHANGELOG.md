# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
