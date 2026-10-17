# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
