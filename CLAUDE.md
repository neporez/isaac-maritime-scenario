# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An NVIDIA Isaac Sim (Kit 107+) extension for building maritime scenarios: an animated Gerstner wave,
a controllable own ship, and other-vessel traffic (mouse placement, waypoint paths, save/load),
controllable from a UI panel and from Python. The extension lives in `exts/maritime.scenario/` (Kit manifest
in `config/extension.toml`, Python package `maritime_scenario`). Modeled directly on `isaac-weather-fx`'s own
architecture (see that repo's CLAUDE.md) -- same core/runtime/backends/ui split, same state->controller
->manager->backend data flow.

Ported from `boat_mesh_pipeline/06_scenario_editor` (a Script-Editor-paste-driven prototype in a
different, private repo), with two deliberate scope cuts made during the port:
- **No LiDAR/data-capture.** The original folder could mount an RTX LiDAR sensor for visual preview;
  this extension is scenario-BUILDING only, no sensor, no .bin/.jsonl capture pipeline at all. A sensor
  backend can be added later the same way weather-fx's own `backends/lidar.py`/`radar.py` are
  placeholders for planned (not yet implemented) sensor integration.
- **Web meshes only.** The original folder could spawn either generic web-sourced boat models OR this
  project's own privately-reconstructed meshes (`usd_out/`, built from real LiDAR scans). This extension
  is meant to be a standalone, shareable tool, so it ships only `core/meshes.py`'s generic web model
  registry -- no private mesh pool, no train/val json dependency.

**No 3D model files are bundled in this repo, and the registry path is NOT hardcoded to one machine**
(a real bug, caught and fixed 2026-10-30: an earlier version hardcoded `WEB_MODEL_DIR` to one person's
home directory, which silently broke the extension for anyone else who cloned it). `core/meshes.py`
resolves the model directory from the `MARITIME_SCENARIO_MESH_DIR` env var, falling back to
`<repo root>/assets/models` (gitignored, `.gitkeep`-only) -- see that folder's own README.md for what to
put there and why. `backends/meshes.py`'s `list_all()` only offers meshes whose file actually exists
(so an unpopulated `assets/models/` doesn't crash the UI, just offers nothing, with a logged warning),
and `measure()` raises a clear, actionable error naming the missing path if something tries to use a
mesh that isn't there.

## Commands

```bash
pip install -e ".[dev]"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest   # all tests; no Isaac Sim needed
# (PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 sidesteps a broken ROS Jazzy launch_testing pytest11 plugin that
# can be globally registered on dev machines with a ROS workspace sourced -- harmless to always pass)
pytest tests/test_ship_motion.py::test_derive_matches_hand_computed_values_for_a_known_hull   # one test
```

`pyproject.toml` puts `exts/maritime.scenario` on the pytest path, so tests import `maritime_scenario.core`
directly. Only `core/` is testable outside Kit. Everything else (`runtime/`, `backends/`, `ui/`,
`api.py`, `extension.py`) imports `omni`/`pxr`/`carb` and can only run inside Isaac Sim, via the Script
Editor or a real Kit process.

## Architecture

**The core/Kit boundary is strict**, same rule as weather-fx. `maritime_scenario/core/` is plain Python with
no Omniverse imports: ship-motion physics (`ship_motion.py`), Gerstner wave math (`gerstner.py`), the
vessel/mesh data model (`vessel.py`, `meshes.py`), scenario state (`state.py`) and save/load
(`scenario_io.py`). `maritime_scenario/__init__.py` imports the Kit extension class only when `omni.ext` is
importable, so `import maritime_scenario.core` works in plain Python. Keep new pure logic in `core/` so it
stays unit-testable; put `omni`/`pxr` imports inside the runtime/backend/ui layers.

**`core/state.py`'s `ScenarioState` holds the CONTINUOUS, globally-shared settings** -- `wave`
(`gerstner.WaveParams`), `ship_physics` (`ship_motion.ShipPhysicsCoeffs`), `general`. Frozen dataclasses;
`with_updates(section, **kwargs)` returns a new state rather than mutating. Deliberately does NOT hold
the vessel list or the own ship's current mesh -- see the next point.

**Vessels are discrete entities, not continuous state.** A `core.vessel.Vessel` is one placed boat's
definition (mesh_id, pose, behavior, waypoints, speed) -- spawned/despawned by explicit actions, never
diffed the way a fog density is. `runtime/manager.py` (the `ScenarioManager`, a process-wide singleton
from `api.get_controller()`) holds the live vessel list plus each vessel's own frame-to-frame simulation
state (prev_z/prev_roll/wp_index/etc, which never needs saving), and the own ship's current mesh choice.

**`core/scenario_io.py`'s file format is ONE file for the whole scenario** -- wave + ship physics + own
ship mesh + every vessel, `schema_version`-tagged, lenient on load (unknown/missing fields degrade
gracefully, same spirit as weather-fx's `from_dict`). This is a deliberate improvement over
06_scenario_editor's old `save_scenario()`/`load_scenario()`, which only ever saved the vessel list (wave
and physics sliders weren't part of the file). A scenario file saves a vessel's `mesh_id` (a key into
`core/meshes.py`), not its raw usd path/dx/dy/dz -- the registry + the actual USD file are the one
source of truth for a mesh's geometry, re-measured at load time, not cached into every saved file.

**Backends** (ported from `06_scenario_editor`):
- `wave.py`: spawns/animates the Gerstner wave surface (from `spawn_gerstner_wave.py`) and applies the
  `salt_water` nonvisual material property via the save-as-stage+reopen restart dance (from
  `apply_salt_water.py`/`restart_stage.py`) -- that restart is required for a nonvisual material change
  to actually take effect, same as any other Isaac Sim nonvisual-material edit. `setup_async()` is the
  one-time build+restart sequence; `ScenarioManager.setup_async()` awaits it BEFORE building ship/
  vessels, since the restart reopens the stage out from under anything built earlier (see `Backend.
  build()`'s own docstring in `backends/base.py` for why it's split from `attach()`).

**Multi-tile layout foundation (2026-10-30).** This is a separate assignment from this repo's own
graduation-thesis scope (see memory `project_isaac_maritime_scenario_scope` in `boat_mesh_pipeline`'s
own notes) -- a feasibility survey on using Isaac Sim for autonomous-ship-navigation testing, where the
user explicitly wants the water architecture to stay extensible toward much larger (~10km) or non-square
(e.g. river-shaped) scenarios later, without building that scale today. `WaveBackend` no longer builds
one hardcoded mesh at `WAVE_PATH`; `WAVE_PATH` is now a parent `Xform` holding the 5 SHARED physical
`wave:*` attrs (amplitude/wavelength/speed/steepness/num_waves -- the real sea state, same across every
tile) + one shared material, with each tile its own `Mesh` prim under `WAVE_PATH + "/Tiles/<tile_id>"`
(its own `tile:center_x/center_y/size/resolution` attrs), all bound to that one material.
`core.gerstner.single_tile_layout(params)` (today's exact single-mesh behavior, the default if
`set_layout()` is never called) and `grid_tile_layout(nx, ny, tile_size_m, tile_resolution)` (a simple
rectangular tiling preset) are the two layouts available so far -- a river-shaped/polyline layout would
be a third, built the same way (just a different `list[WaveTileSpec]`), not yet written since nothing
needs it yet. `core.gerstner.grid_points()` grew an `origin_xy` param (default `(0,0)`, backward
compatible) specifically so multiple tiles share ONE continuous wave phase/field (matching crests/
troughs at a shared boundary) instead of each tile restarting its own phase at its own local origin,
which would make every tile seam a visible discontinuity -- mirrors how `wave_height(..., origin_xyz=...)`
already worked for physics sampling. `height_at()` (what ship/vessel buoyancy physics reads every frame)
is **completely unaffected** by any of this -- it was already a pure function of the shared `wave:*`
attrs + world (x,y), never mesh geometry, regardless of how many tiles exist. `WaveBackend.
set_tile_active(tile_id, bool)` exists as a hook for later distance-based culling (e.g. only tiles near
the ship get re-animated each frame) -- **nothing calls it automatically yet**, this round only built the
registry it'll sit on top of. Also explicitly deferred: any UI for building/editing a tile layout
(scriptable via `WaveBackend.set_layout()` only, this round) and persisting a chosen layout through
`save()`/`load()` scenario files. Verified via `tests/test_gerstner.py` (phase-consistency across
`origin_xy`, both layout presets) and a new `tests/test_wave_backend_tiles.py` (tile registry mechanics
against a real in-memory USD stage: shared-material binding, `apply_state()`'s "only resize the 'main'
tile while the default layout is active" rule, `set_tile_active(False)` actually freezing a tile) --
unlike the reverted ripple-texture attempt above, this is geometry-only (no new shaders/textures) and the
default layout is required to exactly reproduce pre-existing output, so the live-render risk is much
lower, but switching to a non-default layout in a live Isaac Sim session to visually confirm tiles line
up with no seam is still the real test, not yet done.

**Incremental tile add/remove + mouse-click tile placement (2026-10-30).** `set_layout()` is a full
clear-and-rebuild of every tile (expensive and resets every tile's `tile_active` state) -- the wrong
primitive for placing tiles one at a time. `WaveBackend` grew `generate_tile_id()` (a fresh, collision-
free id), `add_tile(spec)` (builds exactly ONE new tile, O(1), doesn't touch any other tile) and
`remove_tile(tile_id)`, all reusing the same `_ensure_root`/`_ensure_material`/`_build_tile_mesh` helpers
`set_layout()` already used. `api.py` got thin wrappers (`add_water_tile`/`remove_water_tile`/
`list_water_tiles`/`clear_water_tiles`), same shape as the existing `spawn_vessel`.

The UI feature: `ui/tile_placement.py`'s `TilePlacementController` -- a **snap-to-grid "paint" mode**,
not free-position placement. The water is treated as a world-anchored grid; hovering highlights the
cell under the cursor (a single flat `Cube`, GREEN if empty, RED if blocked); a click installs a tile in
the hovered cell. There's no bound on cell index -- any cell the mouse can reach gets painted, so this
extends arbitrarily far, not to some pre-declared area. Stays in placement mode across clicks
(continuous, user's own explicit choice, unlike `ui/placement.py`'s vessel placement which spawns one
vessel then exits), until Stop/Cancel. (Grid convention and tile sizing changed 2026-10-30 -- see the
power-of-2 note below; this paragraph describes the current, not the original, version.)

**Bug found + fixed during the first (free-position) version, same session.** Live-follow (confirmed by
the user: the ghost visibly tracked the mouse correctly) and the click handler each independently
re-derived ground position -- live-follow via `carb.input`'s global mouse pixel minus a calibrated
offset, the click handler via `set_mouse_pressed_fn`'s own `(x, y)` args treated as already-local widget
pixels (the same assumption `placement.py`'s vessel click handler makes, copied over, but apparently
never actually exercised/verified before now). Every click landed the tile at world `(0, 0)` regardless
of where the ghost was, meaning that second, click-args-based path was wrong. Fix: **removed the click
handler's own ray-cast entirely** -- a click now just installs a tile in whatever cell `self._hovered_cell`
(set by live-follow, the confirmed-working path) currently holds, so there is only one mouse-position
path left, not two that can silently disagree. This also happens to be the more natural implementation of
"paint the cell you can see highlighted" anyway. If vessel placement (`ui/placement.py`) turns out to
have the same click-vs-hover mismatch, the fix there would need to be different (a vessel's click
position is real spawn data, not just "which of a finite set of cells" -- can't simply reuse a cached
hover cell the way tiles do); not yet checked, flagged here for when that's tested live.

**Correction, same session, after actually testing the above fix live: that wasn't the (whole) bug.**
Tiles still appeared stacked at world `(0, 0)` regardless of the hovered cell. The real cause was one
level deeper, in `WaveBackend._build_tile_mesh()` itself (so it affected EVERY tile, not just
mouse-placed ones -- `grid_tile_layout()` tiles would have shown the exact same symptom, just never
tried live): `core.gerstner.grid_points()` deliberately returns LOCAL-frame points (its own docstring
says so -- `origin_xy` shifts only the Gerstner PHASE, never the output x/y), relying on the tile
prim's own world transform to actually place it. `_build_tile_mesh()` never set that transform. Fixed
by adding one `AddTranslateOp(...).Set(Gf.Vec3d(spec.center_x, spec.center_y, 0.0))` at tile-creation
time. The single default "main" tile never exposed this (its own center IS the origin, so a missing
translate was indistinguishable from a correct one). New regression test,
`test_tile_mesh_is_actually_positioned_at_its_world_center` in `tests/test_wave_backend_tiles.py` --
none of the earlier tests caught this because they all compared `grid_points()`'s own (local-frame, by
design) output against itself, never checked a tile prim's actual `ComputeLocalToWorldTransform()`.
Lesson for next time: when verifying "is a prim in the right place", check the prim's resolved world
transform directly, not just that the math function which fed its point values returned the values it
was supposed to.

**A second, unrelated bug reported in the same pass**: placing a tile at grid cell `(-1, -1)` crashed
Isaac Sim (`UsdStage::_IsValidPathForCreatingPrim`, "Path must be an absolute path: `<>`"). Cause: USD
prim names may not contain `-` (hyphen); `tile_id = f"tile_{i}_{j}"` produced `"tile_-1_-1"` for negative
cell indices, an invalid identifier. Fixed with `TilePlacementController._fmt_index()` (`-1` -> `"n1"`),
so cell ids stay valid (`[A-Za-z_][A-Za-z0-9_]*`) in every direction -- `grid_tile_layout()` never hits
this since its `i, j` only ever come from `range()` (always >= 0), so it was left as-is.

**The default water layout is now an explicit choice, not automatic (2026-10-30, user's own request).**
`setup_async()` used to default to `gerstner.single_tile_layout(params)` when nothing was pre-configured
-- now it defaults to an EMPTY layout (`[]`). It still builds the root prim (shared `wave:*` attrs) and
material and does the salt_water + restart dance, so `height_at()`/ship physics work immediately with
zero visible tiles (that method never reads tile geometry). Two explicit, separate entry points now exist
after "Build scene": `ScenarioController.set_wave_single_plane()` (the "Build default wave plane" button
in the Wave section -- the classic single NxN plane, sized from the Size/Resolution sliders) and
`TilePlacementController`/"Place tiles with mouse" (the grid-paint mode above). Both call `set_layout()`,
which fully replaces whatever's there -- picking one wipes the other, by design (these are two distinct
layout MODES to switch between, not something meant to be layered together).

**Tile-boundary lighting seam -- analytic per-vertex normals (2026-10-30).** After the position bug was
fixed, the user reported adjacent tiles still looked unnatural at their boundary: positions lined up
exactly, but a visible brightness/shading line remained. Cause: neither `grid_points()` nor
`_build_tile_mesh()` ever authored explicit normals, so the renderer computed them from each tile's own
LOCAL mesh topology -- two independently-computed normals at the same world-space boundary vertex don't
generally agree, even when the vertex positions do (a standard problem in any chunked/tiled mesh system).
Fixed by computing normals ANALYTICALLY instead of leaving them to the renderer or to a finite-difference
scheme: `core/gerstner.py` grew `gerstner_dz()` (closed-form d(z)/dx, d(z)/dy for one wave train -- plain
chain rule on `gerstner_z`'s `amp*sin(phase)`, reusing the same `cos(phase)` term `gerstner_offset()`
already needs, so no extra trig beyond what the height calculation already does), `wave_height_gradient()`
(summed over wave trains, same shape as `wave_height()`), `wave_normal()` (`normalize((-dz/dx, -dz/dy,
1))`, the standard heightfield-to-normal conversion, deliberately ignoring the Gerstner horizontal
wobble's own contribution to the normal -- a common, visually-fine simplification at this project's
steepness range, not the full Jacobian), and `grid_normals()` (matches `grid_points()`'s exact
iteration/indexing, sampled at the same undisplaced world position). Because this is a pure function of
world `(x, y, t)` (same as `wave_height`/`grid_points`'s own `origin_xy` design), two tiles sampling the
same world point get the IDENTICAL normal -- no seam. `_build_tile_mesh()` now calls
`mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)` once at creation, and `_set_tile_points()` (the one
shared per-frame/per-apply_state update path) now also sets `GetNormalsAttr()` every time it sets
`GetPointsAttr()`. Deliberately NOT `wave_slope()` (the older, still-unused, finite-difference
slope function) -- `wave_height_gradient()` is a separate, cheaper (no extra `wave_height()` evaluations,
closed-form instead of 4x central-difference), purpose-built function for this, not a refactor of that
one. Verified: `core/gerstner.py` gradient/normal math (flat-water edge cases, unit-length, matches a
numeric finite-difference of `wave_height()` as a cross-check, and the actual payoff --
`test_grid_normals_continuous_across_tile_boundary`) in `tests/test_gerstner.py`; the real USD mesh
attributes (`SetNormalsInterpolation`, normals count, and two adjacent tiles' boundary normals matching)
in `tests/test_wave_backend_tiles.py`. NOT yet confirmed live whether this actually reads as seamless in
the RTX viewport -- the math and the authored USD data are now provably consistent, but the final render
step is, as always in this project, the one thing that can only be checked by actually running it.

**Update: the seam was still visible live, even after the normal fix above (2026-10-30, same session).**
User-confirmed: positions line up, but a brightness/shading line remains at tile boundaries. This led to
a third, likely-real culprit, found by reasoning through "if positions AND normals match, what else could
differ between two independently-authored meshes?": **`UsdGeomMesh` defaults to
`subdivisionScheme="catmullClark"` when never explicitly set** -- found 2026-10-30 by grepping this
file for `subdivision` and getting zero hits. A Catmull-Clark-refined mesh is NOT rendered as its raw
authored polygons; the renderer refines it first, and that refinement uses ONLY the mesh's own internal
topology -- each tile's boundary edge gets pulled/smoothed independently of whatever its neighbor's
refinement does to ITS matching edge, so the two tiles' actually-rendered (post-subdivision) surfaces can
disagree right at the boundary even though the raw authored points+normals matched exactly. Fixed:
`_build_tile_mesh()` now calls `mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)`, telling the
renderer to rasterize the authored polygons as-is, with no neighbor-unaware refinement left to disagree
between tiles. Verified via `test_tile_mesh_has_subdivision_disabled` in `tests/test_wave_backend_tiles.py`
(84 tests total now). Not yet confirmed live whether this was the full story or just one of several
contributing causes -- the sub-pixel-gap and renderer-ignores-authored-normals theories from the earlier
paragraph are still untested and could also be contributing; try it live and report back whether the seam
is gone, fainter, or unchanged.

**Power-of-2 "quadtree-style" tile sizing + a separate Delete mode (2026-10-30).** User's framing: tile
size should be restricted to powers of 2 (32/64/128/256/512m, `TILE_SIZE_LEVELS_M` in
`ui/tile_placement.py`) and managed so placing a bigger tile over an area automatically removes
("coarsens away") the smaller tiles it now fully covers -- fine detail in some spots, coarse coverage
elsewhere, without an actual tree data structure (tile counts stay in the tens-to-hundreds for this
interactive tool, so a flat dict -- `WaveBackend.tiles`, already existed -- plus an O(n) geometric
overlap/containment scan per hover tick is simpler and plenty fast; "quadtree" here means the SIZE
semantics, not a literal node tree). This needed the grid convention to change from CENTER-aligned
(`cell (i,j)` centered at `i*size`) to **corner-aligned** (`cell (i,j)` covers `[i*size, (i+1)*size]`,
centered at `(i+0.5)*size`) -- only corner alignment makes a bigger level's cells nest EXACTLY over the
smaller level's (a 64m cell = exactly four 32m cells), which is what turns "does this bigger tile fully
cover these smaller ones" into a plain rectangle-containment check (`_rect`/`_overlaps`/`_contained_in`/
`_placement_check` in `ui/tile_placement.py`).

Clarifying question asked and answered: what about the REVERSE case, placing a small tile where a bigger
one already exists? User's call: don't auto-carve a hole in the big tile (unneeded complexity, "그런거
생각 안해도되잖아") -- instead placement is simply BLOCKED (red) if any equal-or-larger existing tile
overlaps without being fully contained, and a new, separate **Delete mode**
(`TilePlacementController.mode = "delete"`, same controller/class as placement -- NOT a third duplicated
mouse-event-wiring class, since place and delete share all the live-hover-follow/debounce/viewport-
handler-lifecycle machinery and only differ in what hovering highlights and what a click does) lets the
user hover over and click any existing tile (whatever size) to remove it outright, then place smaller
ones in the cleared area. `ui/window.py`'s "Tile size" field became an `IntSlider` over
`TILE_SIZE_LEVELS_M`'s indices (not free numeric entry); a new "Delete tile with mouse" button sits next
to "Place tiles with mouse", both funneling through the SAME mutual-exclusion-with-vessel-placement
pattern already in place. Known, called-out-in-the-docstring limitation: `undo_last()` only re-removes
the tile it just added -- it does NOT restore smaller tiles a placement coarsened away (their specs
aren't remembered, only that they existed); revisit if that turns out to matter in practice. Verified via
a scratch script (pure dict/geometry logic, no pxr/omni needed for the core math -- place 4 contained 32m
tiles, confirm the 64m placement over them correctly identifies and coarsens exactly those 4, confirm a
32m placement inside the resulting 64m tile is blocked, confirm Delete mode finds and removes the 64m
tile regardless of the level slider's current setting, confirm the 32m placement then succeeds, confirm
an exact duplicate is reported as BLOCKED not as a coarsenable/containable tile) plus a real-in-memory-
stage check of the ghost draw/hide/color logic.

**Update: tried live -- real geometric gaps (T-junction cracks), not just a shading seam, fixed via
resolution scaling (2026-10-30, same session).** User's screenshot of a mixed-size layout (big tile
adjacent to small tiles) showed actual black triangular holes at the boundaries, not the subtle
brightness line from the normals/subdivision work above. Cause, confirmed by the user's own answers
(subdivision fix WAS already applied; the gaps appeared specifically where DIFFERENT-sized tiles met):
a classic LOD-terrain T-junction crack. Every level used the SAME `resolution` (vertex COUNT) regardless
of tile size, so a bigger tile's edge is one long straight segment between its own widely-spaced
vertices, while a smaller neighbor samples that same physical stretch more densely and follows the
actual (wavy) surface more closely -- the gap is the difference between the straight-line approximation
and the true curve, concentrated right at the point where the smaller tiles' own shared boundary meets
the bigger tile (a T, not a plain edge-to-edge X). Fixed by `ui/tile_placement.py`'s
`resolution_for_level()` -- user's own proposal, arrived at independently -- which scales resolution so
vertex SPACING (not count) stays constant across every level: `R_L = (base_resolution - 1) * 2**level + 1`,
not a naive `base_resolution * 2**level`. With this, a level-`L+1` tile's edge contains EXACTLY the same
world points that two level-`L` tiles covering that same span would produce when placed edge-to-edge --
not approximately, the identical set, proven in
`test_matching_vertex_spacing_eliminates_boundary_gaps_between_different_tile_sizes`
(`tests/test_gerstner.py`, 85 tests total now) and re-checked for the real adjacency geometry in a scratch
script. The UI's "Tile size" row now also shows the resolved resolution per level (e.g. "64m (33x33)");
the former free `resolution` field became `base_resolution` (level-0/smallest-tile resolution only --
every other level is derived, not independently settable, since independent settings would reintroduce
the exact crack this fixes). Trade-off, accepted knowingly: bigger tiles now have proportionally MORE
vertices (not a fixed count), so the "fewer vertices for big coarse areas" benefit of tiling is smaller
than it would be with constant resolution -- the user chose crack-free correctness over that savings.
Considered and explicitly rejected in favor of this: "skirt" geometry (hanging a thin wall from each
tile's edge to visually hide gaps without removing them) -- doesn't fix the geometry, just covers it, and
the user picked the exact fix instead once they'd independently arrived at the same idea.

Still open, separately: the fainter shading line between SAME-size tiles noted in the earlier
normals/subdivision section -- user confirmed (2026-10-30) it's still faintly visible even with matching
resolution, so it's unrelated to T-junctions. Likely either a genuinely hard-to-fully-eliminate residual
(antialiasing/rasterization at the border between two independently-drawn meshes, which pixel-perfect
position/normal/topology matching may not fully solve without actually merging the meshes) or something
not yet diagnosed. Not yet resolved.

**Distance-based tile activation (the real perf fix) + auto-merge-upward + uncapped levels (2026-10-30).**
Three requests from one message, kept conceptually separate (see each subsection) even though they shipped
together:

- **Auto-merge reduces tile-prim COUNT, not per-vertex COST -- this distinction matters and was stated to
  the user up front before building anything.** `resolution_for_level()` ties resolution to size via a
  constant vertex spacing, so merging 4 tiles into 1 bigger one costs roughly the same total vertex count
  (a few fewer at the now-internal seams) -- auto-merge is a tidiness/prim-count win, not what fixes the
  "slows down past 200-300m" report.
- **The actual perf fix**: `WaveBackend.set_tile_active()` was built earlier this session specifically as
  a dormant hook for this ("nothing calls it automatically yet") -- now driven by
  `update_tile_activity()`, called every frame from `_on_update()` before `_set_points_all()`. Tiles
  farther than `active_radius_m` (+ their own half-size, so a tile doesn't go inactive while the ship is
  still near ITS edge) from the own ship's position stop being re-animated -- `set_tile_active()`'s
  existing semantics (frozen, not hidden/removed) are unchanged, just finally driven by something.
  `ShipBackend` grew `get_position()` (same cross-backend lookup pattern `ship.py` already uses to reach
  `WaveBackend`, just the reverse direction: `wave.py` now reaches `ship.py`), returning `None` before the
  ship exists -- `update_tile_activity()` is a no-op in that case (today's prior "everything active"
  default). `api.py`'s `set_wave_active_radius()` + a new UI slider (50-2000m, default 150m) let the user
  tune this live rather than trusting one guessed number -- **told explicitly, not assumed**: the radius
  only meaningfully culls anything once it's smaller than roughly half the tiled extent, so if a report
  like "slow past 200-300m" persists, the fix is picking a noticeably SMALLER radius via the slider, not
  that the mechanism doesn't work. Known remaining limit, not addressed this round: this only stops
  RE-ANIMATION (frozen tiles still cost a draw call); if that's not enough, the next escalations would be
  hiding far tiles outright or vectorizing the per-vertex math with numpy -- neither attempted here.
- **Uncapped-in-practice levels**: the old fixed `TILE_SIZE_LEVELS_M = [32, 64, 128, 256, 512]` list
  became `size_for_level(level) = BASE_TILE_SIZE_M * 2**level` (formula, not a list) + `MAX_TILE_LEVEL =
  10` (~32.8km) as a practical UI-slider ceiling only -- nothing else in the math assumes that exact
  number, raise it later if ever needed. "무한대로", user's own framing -- not literally infinite, but no
  structural cap standing in the way below a many-kilometer practical ceiling.
- **Auto-merge-upward**: `ui/tile_placement.py`'s `_try_merge_upward(tile_id, cx, cy, size_m, level)`,
  called from `_handle_place_click()` right after the existing coarsen-on-placement cleanup. Checks
  whether the just-placed tile now has 3 same-size neighbors forming a clean 2x-bigger square
  (`_find_merge_group()`). If so: removes the 4, adds 1 tile at the parent level (via
  `generate_tile_id()`, not a derived index-based id -- see the correction just below for why), then
  repeats the SAME check for the new bigger tile -- bounded only by `MAX_TILE_LEVEL`, so a
  quartet-of-quartets etc. keeps bubbling as far as it can ("무한대로" again). Deliberately scoped like
  coarsening-on-placement already was: only matches an EXACT quartet of same-size tiles, no attempt to
  detect/merge a "logically complete but differently-subdivided" area. `_placed_order` (what
  `undo_last()` can remove) gets updated to point at the final merged tile when a merge happens, so "undo
  right after a click that triggered a merge" removes the visible outcome -- but (same documented
  limitation as coarsening) undo does NOT restore the smaller tiles that got absorbed into a merge, their
  specs aren't remembered.

  **Correction, same session, after live testing: the first (index-based) version never actually
  merged.** User: "안합쳐지는데?" -- confirmed with the actual tile ids
  (`tile_L1_0_0`/`tile_L1_0_n1`/`tile_L1_n1_0`/`tile_L1_n1_n1`, a textbook-clean 2x2 quartet symmetric
  around the world origin, exactly where the own ship spawns -- `ship.py`'s `SPAWN_X, SPAWN_Y = 0.0,
  0.0`). Root cause: the original check computed a "parent" via `parent_i = i // 2`, which pairs index 0
  with 1 and index -1 with -2 -- NOT with 0. That's a perfectly valid, internally-consistent quadtree
  pairing, but it has no special relationship to the world origin, even though the origin is exactly
  where a user is most likely to start building (the ship's spawn point). A tile quartet placed
  symmetrically around (0,0) straddles that pairing's phase boundary on both axes and can NEVER satisfy
  `i // 2`-based grouping, no matter how the user places things -- not a placement mistake, a mismatch
  between the grid's assumed phase and the one actual usage patterns produce. Mathematically: making an
  index-based pairing BOTH recursively nest across levels AND treat the origin as symmetric down to
  arbitrary depth would require the parent-level cell boundaries to be offset by half a child-cell-width
  at every level relative to the naive `[p*2S, (p+1)*2S]` -- i.e. two incompatible indexing schemes
  for the SAME level depending on which "phase" you started from, not implementable with the simple
  single-origin corner grid `_cell_index()`/`_cell_center()` also need for placement/hover.

  **Fixed by dropping the index-based approach entirely for the merge check.** `_find_merge_group(cx, cy,
  size_m, existing)` instead asks a purely GEOMETRIC question: do 3 OTHER same-size tiles exist at the 3
  world positions that would complete a square with this one, tried in all 4 possible roles
  (bottom-left/-right, top-left/-right) the tile could play in that square -- reading actual positions
  off `list_water_tiles()`, no assumption about global grid phase at all. This merges the user's
  origin-symmetric quartet (and any other valid quartet, anywhere, on any phase) the same way, verified
  directly against the user's own reported tile ids in a scratch script. Placement/hover
  (`_cell_index`/`_cell_center`, still index-based, still using the original single-phase corner grid) is
  completely unaffected -- only the MERGE check changed; new tiles placed via a click still land on the
  regular snap grid as before, merged tiles just no longer need to land on that same grid's "parent"
  cells, which is fine since every other tile/placement operation (`_placement_check`,
  `_find_tile_containing`) was already purely geometric too, never tile-id-based.

Verified: `update_tile_activity()` (near tile stays active / far tile deactivates / half-size margin at
the boundary / no-op with no ship backend / no-op before the ship has a position) in
`tests/test_wave_backend_tiles.py` (89 tests total now, real in-memory USD stage + a fake ship backend);
`_try_merge_upward()`/`_find_merge_group()` (no merge at 3/4 siblings, a phase-aligned 2x2 quartet still
merges correctly including size/resolution/center, a RECURSIVE merge of already-merged level-1 tiles
bubbles to level-2, AND -- the part that actually mattered -- the user's own reported origin-symmetric
quartet, reproduced with their exact tile ids, now merges to one tile centered exactly at (0,0)) in a
scratch script (same pattern as every other `ui/tile_placement.py` check this session, pure dict/geometry
logic).

**Update: tried live -- distance-based activation alone was NOT enough (2026-10-30, same session).**
User tested with the active radius already turned down to 150m and it was still noticeably laggy, with an
explicit requirement that this scale needs to be smooth since LiDAR + other components are planned on top
of it later. This led to actually MEASURING the per-frame cost rather than continuing to guess at
activation-radius tuning -- see the NumPy vectorization note right below, which is what the real fix
turned out to be (the activation work above is still correct and still helps, it just wasn't the dominant
cost at this scale).

**NumPy-vectorized per-frame wave math -- the actual fix for the "still laggy at 150m" report
(2026-10-30).** Measured directly (not guessed) before changing anything: for one 256m tile at
`resolution=129` (16,641 vertices -- an ORDINARY size `resolution_for_level()` produces for a mid-size
tile, not an extreme case), the pure-Python path cost ~40ms for `grid_points()`+`grid_normals()`'s
per-vertex trig math, PLUS ~17ms EACH for converting the resulting Python list into a `Vt.Vec3fArray` via
`[Gf.Vec3f(*p) for p in points]` (building 16,641 individual Python objects) -- **~74ms total, for ONE
active tile, every single frame**, already far over a 60fps frame budget (16.6ms) by itself. This fully
explains the reported lag and confirms it was a fixable bottleneck, not a fundamental limit.

Fixed with two changes, both measured before and after:
- `core/gerstner.py` grew `grid_points_and_normals_np(t, params, origin_xy)`: the SAME formulas as the
  scalar `grid_points()`/`grid_normals()`, computed together in one pass with `numpy` array ops
  (`np.meshgrid` + vectorized `np.sin`/`np.cos` across the whole grid at once) instead of a double Python
  `for` loop calling per-vertex trig functions. Measured: ~1.7ms for the same 16,641-vertex grid (~23x
  faster) -- confirmed numerically EQUIVALENT to the scalar functions (exact match to float32 precision
  across several parameter sets) in `test_grid_points_and_normals_np_matches_scalar_functions_exactly`,
  and confirmed the crack-elimination and boundary-normal-matching properties (the ones that actually
  matter for live rendering) still hold for the new function specifically, not just inherited by
  assumption, in `test_grid_points_and_normals_np_preserves_crack_elimination`/
  `..._preserves_boundary_normal_matching` (`tests/test_gerstner.py`, 92 tests total now). This is a
  scoped exception to the module's own "pure Python + math only, no omni/pxr" design boundary (now "+
  numpy") -- deliberate, not a general policy change: the existing SCALAR functions (`wave_height`,
  `wave_offset`, `wave_normal`, etc.) are untouched and still used as-is for single-point sampling
  (`height_at()`, ship/vessel physics), where numpy's per-call array-construction overhead would make a
  ONE-point query slower, not faster -- only the full-grid per-tile-mesh path benefits from vectorizing.
- `backends/wave.py` grew `_to_vt_vec3f_array()`: `Vt.Vec3fArray.FromNumpy(array)` (confirmed available
  in this environment's pxr build) instead of the `[Gf.Vec3f(*p) for p in points]` list comprehension --
  measured ~0.4ms vs ~17ms for the same vertex count (~40x faster), with a `try/except AttributeError`
  fallback to the old list-comprehension approach for pxr builds that don't have `FromNumpy` (not
  guaranteed across every USD version). `_set_tile_points()` (the one shared per-build/per-frame path
  both `_build_tile_mesh()` and `_set_points_all()` already routed through) now calls
  `grid_points_and_normals_np()` once and converts both outputs through this helper.

Combined: **~74ms -> ~1-2.5ms per active tile per frame** (re-measured through the REAL end-to-end
`WaveBackend._set_tile_points()` path, not just the isolated prototype numbers, in a scratch script: a
built 256m/resolution-129 tile's per-frame update cost came out to ~1.06ms) -- turning "one active tile
alone blows the frame budget" into "dozens of active tiles still fit comfortably". Explicitly NOT done
this round (noted as the next escalation if this still isn't enough once tried live): batching ALL active
tiles into one combined numpy call (would share per-wave-train setup across tiles for a further, smaller
win, but is real added complexity over per-tile vectorization alone), and GPU/shader-based displacement
(the "real" AAA-engine approach to this problem, a much bigger undertaking). NOT yet confirmed live
whether this fully resolves the original "laggy at 150m" report, only that the isolated and
real-code-path measurements both show the fix working as designed.

**GPU (NVIDIA Warp) batched wave computation, additive fast path over the NumPy path above
(2026-10-30).** User's own framing: NumPy already fixed the reported lag, but before adding more
components (LiDAR etc.) on top, wanted to confirm there's real headroom extending water out toward this
project's actual goal of ~10km. New `core/gerstner_gpu.py`, same resilience pattern as
`ui/placement.py`'s `VIEWPORT_PICKING_AVAILABLE`: `import warp as wp; wp.init(); GPU_AVAILABLE = ...`
inside a broad `try/except Exception` (not just `ImportError` -- `wp.init()`/device queries can fail
other ways, e.g. no CUDA driver), so a machine without a working GPU/Warp never crashes, just falls back
to the NumPy path. `warp-lang` (NVIDIA Warp, a Python-embedded GPU kernel library Isaac Sim already uses
internally) installed via pip; this sandbox has a real CUDA device (RTX 4090), so `GPU_AVAILABLE` is
`True` and the GPU tests actually run here (102 tests total now), not skip -- still **unconfirmed
whether `warp` is importable in Isaac Sim's own Python environment**, that's the live check still owed.

Key design point, worth stating explicitly: **10km total layout extent does NOT matter for per-frame
cost -- only the ACTIVE tile count does.** `update_tile_activity()` already only re-animates nearby
tiles; a tile far away costs nothing per frame regardless of how far "away" is. Measured directly: the
O(n) distance scan over 1,600 total tiles (a flat grid spanning ~10km) costs ~0.15ms/call, negligible.

The actual batching: `WaveBackend._set_points_all()` groups ACTIVE tiles into buckets keyed by
`(resolution, size_m)` (tiles at the same quadtree level always share both, by construction of
`resolution_for_level()`). A bucket with more than one tile, with `self.use_gpu` (a plain attribute,
defaults to `GPU_AVAILABLE`, not a UI checkbox this round) True, is computed in ONE
`gerstner_gpu.compute_batch()` kernel launch instead of one NumPy call per tile -- one thread per
`(tile, grid-point)` pair via `tile = tid // (resolution*resolution)`. A singleton bucket, or any bucket
when `use_gpu` is False, still uses the existing per-tile `_set_tile_points()` NumPy path unchanged
(measured: a single-tile GPU round-trip only nets ~2x over NumPy -- GPU<->CPU transfer overhead eats
most of the advantage for just one tile, so batching is where the real win is). Correctness: the kernel
matches the scalar/NumPy functions to within `~2e-05` (points) / `~6e-06` (normals) across every
`num_waves` value the UI's "Number of wave trains" slider allows (1-4) -- an earlier draft of the kernel
hardcoded only 2 wave trains (scalar params instead of `wp.vec4`), which would have silently dropped
trains 3/4 for anyone past `num_waves=2`; caught and fixed before any test ran, not user-reported.

**Measured, two different scenarios, both worth keeping in mind:**
- At the *default* `active_radius_m=150m`, with 1,600 total tiles spread uniformly across ~10km at the
  finest (256m/resolution-129) level: only **4 tiles active** at once -- in realistic play the quadtree's
  own auto-merge keeps the near field sparse (a handful of fine tiles right around the ship) and merges
  everything farther out into much bigger, lower-vertex-density tiles, so this is already comfortably
  inside budget.
- An artificial worst case built to actually stress the GPU path -- `active_radius_m` widened to 700m
  while keeping every one of the 1,600 tiles at the finest level (i.e. deliberately defeating
  auto-merge, something the real placement tools wouldn't produce) -- lands **32 active 256m/resolution-
  129 tiles** (~532K vertices) active simultaneously: GPU path ~18.5ms/frame vs NumPy's ~34.6ms (still
  ~1.87x faster), but **both are over the 16.6ms/60fps budget** at this density. Broken down further:
  of that ~18.5ms, `gerstner_gpu.compute_batch()` itself is only ~7.6ms -- the other ~9ms is the existing
  per-tile `_to_vt_vec3f_array()` conversion + `Mesh.Get{Points,Normals}Attr().Set()` calls, a cost
  **neither GPU nor NumPy compute-side work touches**, since it's paid once per tile regardless of which
  side computed the underlying array. If a future session needs to push past this, the USD-write side
  (not more compute batching) is where the next win would have to come from -- e.g. fewer, larger tiles
  instead of many small ones at the near field, or writing fewer separate mesh prims.

Explicitly out of scope this round (deferred, not forgotten): caching the per-resolution `base`
numpy/warp array across frames (rebuilt each `compute_batch()` call currently), any UI checkbox for
`use_gpu`, running Warp on a CPU device as a non-GPU fallback (`GPU_AVAILABLE` specifically requires a
real CUDA device, falling back straight to the already-fast NumPy path otherwise).

Reuses `PlacementController.mouse_to_ground_xy` directly (pure `@staticmethod`, no vessel state) for the
live-follow ray-cast; duplicates (doesn't share via a base class) the stateful live-hover-follow/
viewport-handler-lifecycle code from `placement.py` -- these are two independent, **mutually exclusive**
placement modes (`ui/window.py` calls the other controller's `cancel()` before starting either one, so a
single click can't trigger both). No yaw/keyboard handling at all for tiles (axis-aligned squares,
nothing to nudge) -- a deliberate simplification vs. vessel placement, not an oversight. The highlight
itself is a single flat `Cube` with `displayColor` set directly (green/red, no material) -- the same
"`Gprim` + `displayColor`, no shader" mechanism the waypoint markers already use successfully, chosen
specifically to avoid any new material/shader risk after the ripple-texture failure below.
Verified: `add_tile`/`remove_tile`/`generate_tile_id` + the world-transform regression test in
`tests/test_wave_backend_tiles.py` (real in-memory USD stage); the grid-snap math (cell indexing,
hover-then-click using the cached cell not a fresh ray-cast, red/green recoloring, debounce, undo, an
arbitrarily-far cell index, negative-index ids with no hyphen, placing at `(-1,-1)` not crashing) in a
scratch script (same pattern as every other prim-building check this session) -- this round's two bugs
were both live-session findings the earlier (structurally-correct-looking) tests had missed, so treat
"passes these tests" as necessary, not sufficient. NOT verified: the live mouse/keyboard event wiring
itself (same standing caveat as vessel placement) -- try it live (Build scene, then either "Build default
wave plane" or "Place tiles with mouse", hover around, click a green cell, confirm it turns red and
actually renders at that position -- not the origin, Stop, Undo, Clear) once in a real session.
Explicitly deferred: distance-based auto-activation of `set_tile_active()` (the user's own call -- build
placement first, measure the before/after once many tiles are placed, do the perf work next),
saving/loading a placed layout through scenario files, tile rotation.

**Ripple normal-map texture -- tried and reverted (2026-10-30).** Attempted layering fine ripple detail
as a scrolling tangent-space normal map (`UsdPrimvarReader_float2("st") -> UsdTransform2d(scale, animated
translation) -> UsdUVTexture -> UsdPreviewSurface.normal`), specifically to avoid needing the wave mesh's
own `resolution` to be fine enough to show cm-scale ripples as geometry (texture-space detail is resolved
per-pixel, not per-vertex, so it was meant to be free of that cost). The shader graph itself was verified
correct (node ids/connections/UV-primvar counts against a real in-memory USD stage), but live in Isaac
Sim's RTX real-time renderer it produced a dense, uniform basket-weave/moire noise pattern across the
whole water surface -- not visible ripples, and it made the water look broken rather than better (user
screenshot, 2026-10-30). Read as texture aliasing: the ripple normal map's finest components (~15cm
wavelength) were well below what RTX real-time was filtering/mip-mapping correctly at that tile scale.
Reverted in full (the shader graph, `core.gerstner.grid_uvs()`/`ripple_uv_offset()`, the generated
`assets/textures/` texture+generator) rather than iterating blind without being able to see the live
render. If this is revisited, don't repeat the same "small physically-sized tile + naive repeat, no
mipmap-awareness" approach -- either generate proper mipmaps for the normal map, use a coarser/lower-
frequency ripple pattern, or try Isaac Sim's own built-in ocean/water material instead of a hand-rolled
UsdPreviewSurface graph.
- `ship.py`: the own ship -- spawn, I/J/K/L keyboard-driven thrust/turn control (not WASD/arrows, which
  are Isaac Sim's own default viewport camera shortcuts), the heave/roll/pitch spring-damper from
  `core.ship_motion` applied every frame (from `float_mesh_test.py`).
- `vessels.py`: the other-vessel pool -- `set_count(n)` (random swarm) and `spawn(vessel)` (exact
  placement, any `Vessel.behavior`) both build on the same prim-spawning path, per-frame physics +
  behavior motion (`forward`/`backward`/`turn_left`/`turn_right`/`stationary`/`follow_path`, the last via
  `core.ship_motion.path_steer`), OBB collision-aware placement (`core.placement`, from
  `other_boats.py`), and wraparound-at-the-area-edge (on by default, `set_wraparound_enabled(False)` to
  disable, same as the original).

**Roll/pitch target: 4 hull-fixed probes, not a single point under the center (2026-10-30).** The
original design (`float_mesh_test.py`/`other_boats.py`, ported verbatim at first) sampled the water's
instantaneous slope at ONE point under the hull's center (`wave.py`'s old `slope_at()`, a central
difference at a fixed `WAVE_SLOPE_EPS_M=1.0` step in WORLD x/y) and applied that angle as a rigid
rotation of the whole hull. User-reported symptom: boats looked unrealistically submerged, and lowering
`draft_to_beam_ratio` to its floor didn't fix it. Root cause, confirmed by inspection (not yet visually
re-verified in a live session): two compounding bugs, not a draft/mass tuning problem --
  1. The slope was sampled in world x/y, not the hull's own forward/beam, so "pitch" and "roll" silently
     swapped meaning whenever the hull yawed away from world-axis-aligned.
  2. Applying a point-sampled angle as a rigid rotation about the center swings the ends of a long hull by
     `~half_length * sin(angle)` -- a 20m hull pitching 10deg (an ordinary wave-slope magnitude at the
     default wave params) puts the bow ~1.7m deeper than heave/draft alone would ever predict, and this
     is completely independent of `draft_to_beam_ratio`.
Fixed by `core.ship_motion.heave_pitch_roll_targets()`: sample wave height at 4 probes fixed to the
hull's own bow/stern/port/starboard (`pos ± forward*(dx/2)`, `pos ± beam*(dy/2)`), then fit pitch/roll
via `atan2(rise, run)` using the hull's actual length/beam as the run (same atan()-based formula as
before, just with the finite-difference step widened from a fixed epsilon to the hull's own size and
reoriented into hull-local axes) and average the 4 heights for heave's target instead of a single point.
A hull much longer than the wave's wavelength now naturally averages across multiple crests (rides swell
like a real ship) instead of pivoting to match the water's tilt under one point. `wave.py`'s old
`slope_at()` (and the point-slope call sites in `ship.py`/`vessels.py`) were removed as dead code;
`core.gerstner.wave_slope()` itself is left in place as a tested, general-purpose utility even though
nothing currently calls it.
- `meshes.py` (backend, not `core/meshes.py`): the pxr-dependent half of mesh handling -- opens each
  `core.meshes.MeshEntry`'s USD file once via `UsdGeom.BBoxCache` to measure its real-world (dx,dy,dz)
  and local_center (most of these models' local origin sits at their base, not their bbox center; their
  raw vertex units need exactly their hand-tuned `scale`, since USD reference composition ignores
  `metersPerUnit` -- same two gotchas `06_scenario_editor`'s own files already documented).

**UI** (`ui/window.py`, ported from `scenario_ui.py`): one panel -- a "Build scene" button that runs
`ScenarioManager.setup_async()`, wave sliders, ship physics sliders (shared by the own ship and every
vessel, same as the original), own-ship mesh picker, vessel spawn (mesh/behavior/speed pickers, a
"Place with mouse" / "Finish Path" / "Cancel" button group, yaw +/- nudge buttons + a Step field, a
"Show paths" checkbox, numeric X/Y/Yaw fields, a semicolon-separated waypoints text field for
`follow_path` as a typing-based fallback), a random-swarm count field, save/load scenario file.
Mouse-click placement itself (ghost preview, click-to-place, click-to-add-a-waypoint, live hover-follow,
`[`/`]` yaw nudge while placing) lives in `ui/placement.py`'s `PlacementController` -- kept out of
`window.py` since it owns its own USD prims (ghost, waypoint markers, persistent per-vessel paths) and
viewport/keyboard subscriptions; `window.py` only wires buttons/fields to it and reflects its
status/waypoint-count/yaw callbacks back into labels and fields. This was the original `scenario_ui.py`'s
single most complex and least-verified piece (its own docstring: "no running Isaac Sim available to test
it against while writing") -- same caveat still applies here: the ray-unprojection math and prim-building
are tested against real `Gf.Matrix4d`/in-memory-USD-stage cases, but the live carb.input/viewport EVENT
WIRING itself has not been exercised against a running Kit process.

**"Build scene" no longer auto-spawns the own ship; collapsible sections for scannability
(2026-10-31, user's own call, prepping the GitHub readme/guide writeup).** Two related UI changes:

- `ShipBackend.build()` used to call `self.set_mesh(self._default_mesh_id)` immediately -- so the very
  first thing "Build scene" did was drop a default-mesh ship into the air (before any water layout even
  existed, before Play). Confusing as a first impression, and backwards for the two-step workflow the
  upcoming asset guide documents (1: set up the Gerstner wave/water layout, 2: spawn the own ship +
  vessels + build out the scenario). `build()` now only starts the update/keyboard subscriptions;
  `set_mesh()` (still the same method, still what the UI's "Own Ship" panel and
  `ScenarioController.set_own_ship_mesh()` call) is what actually spawns it, the first time it's called.
  `ShipBackend.__init__`'s now-unused `default_mesh_id` param was removed rather than left dead. Every
  per-frame/position method already guarded on `self._measured`/`self._params` being `None`
  (`get_position()`, `_on_update()`, `reset()`), so a not-yet-spawned ship is simply inert, not broken --
  confirmed by re-running the full suite (still 102 passing; no backend-level test exercises
  `ShipBackend.build()` itself, since it's pxr-dependent, same standing caveat as the rest of `ship.py`).
- `ui/window.py`'s flat list of full-height sections (Setup, Gerstner Wave, Water Tiles, Own Ship, Ship
  Physics, Vessels, Save/Load) meant scrolling past everything just to reach one slider -- compared
  directly against another installed extension's Weather FX panel, which keeps each category compact
  (one-line collapsed header + a ▼ to expand, label and slider on the SAME row) so the whole panel reads
  as a table of contents at a glance. Every section except the top Setup/status/Build bar (which stays a
  plain always-visible bar, same role Weather FX's own top Preset/Apply row plays) is now wrapped in an
  `omni.ui.CollapsableFrame`; each section's old centered `ui.Label(title, font_size=16)` header was
  removed (the CollapsableFrame supplies its own title). NOT yet confirmed live (same standing caveat as
  the rest of this UI) whether `CollapsableFrame` renders/collapses the way this assumes in this
  project's actual Kit/Isaac Sim version.

**Second pass the same day, after seeing it rendered as a screenshot (2026-10-31): all explanatory
prose removed from the panel, sliders made single-row, sections reordered/trimmed to match the asset
guide's two-step workflow, and boat assets get a UI folder picker + a placeholder-box fallback.**
User's framing: the long gray-text explanations belong in the GitHub README/guide (already being
written), not live in the panel -- a real app's settings UI doesn't carry paragraphs. Specific changes:
- `_wave_slider()`/`_physics_slider()`: label + slider + value now share one `ui.HStack` row (label at a
  fixed `width=150`) instead of the label sitting on its own line above the slider.
- Every multi-line gray explanatory `ui.Label` (what "Build scene" does, what active radius means, the
  tile power-of-2/coarsening rules, I/J/K/L controls, waypoint syntax, etc.) was deleted outright -- the
  controls themselves (and their one-word field labels, e.g. "Mesh", "Behavior", "X (m)") are all that's
  left; CLAUDE.md and the upcoming README are now the only place that prose lives.
- The classic single-plane water path was dropped from the UI entirely: the "Build default wave plane"
  button and the Size(m)/Resolution(points/side) sliders that fed it are gone from `_build_wave_section()`
  (user: "기본 플레인 생성하는것도 그냥 지우자" -- tile-based water, below, is now the only UI-driven way to
  build water). `ScenarioController.set_wave_single_plane()` itself is UNCHANGED and still callable from
  a script -- only its dedicated UI button was removed, not the underlying capability.
- Section order now matches the asset guide's own two-step structure: **Water Tiles** (renamed from
  "Water Tiles (advanced)", now collapsed=False like everything else below it -- it's the primary way to
  build water now, not an advanced/optional path) -> **Own Ship** -> **Vessels** -> **Ship Physics**
  (collapsed=True, fine-tuning) -> **Gerstner Wave** (sea-state sliders + active radius, moved from first
  to last) -> **Save/Load** (collapsed=True, occasional use).

**Boat assets folder is now a UI control, with a placeholder-box fallback (2026-10-31).** Previously the
only way to point the extension at boat models was the `MARITIME_SCENARIO_MESH_DIR` env var (set before
Isaac Sim even starts) or dropping files into `assets/models/` directly. User wanted a live "Choose
folder..." button instead, plus a sane default when no assets exist yet rather than a dead/empty picker:
- `core/meshes.py`'s `WEB_MODEL_DIR`/`WEB_MESHES`/`_BY_ID` became mutable module state with a new
  `set_model_dir(path)` that rebuilds them against a new folder at runtime (previously computed once at
  import time) -- the existing env-var-or-default resolution still seeds the INITIAL value, this just
  adds a way to change it afterward. Existing tests (`test_web_model_dir_not_hardcoded_to_one_machine`
  etc.) already monkeypatched `WEB_MODEL_DIR`/`WEB_MESHES` directly, so they needed no changes.
- `backends/meshes.py` grew `clear_cache()` (the per-process measured-mesh cache assumed files never
  change mid-session -- no longer true once the folder itself can change), `FALLBACK_MESH_ID`/
  `fallback_measured()` (a synthetic `MeasuredMesh` with `usd_path=""` and generic elongated-box
  proportions, `dx=8.0, dy=2.5, dz=1.8`, no pxr touched), and `build_visual(stage, path, measured)` -- a
  new shared helper (`ship.py`'s `set_mesh()` and `vessels.py`'s `_spawn_prim()` both previously
  duplicated the same reference+strip-normals+xform sequence inline; consolidating it here meant the
  fallback branch only had to be written once) that defines either a real USD reference (unchanged
  behavior) or, when `measured.usd_path` is empty, a plain `UsdGeom.Cube` scaled to the fallback's real
  dx/dy/dz with a hull-brown display color. `list_all()` now NEVER returns empty: falls back to
  `[fallback_measured()]` alone instead of `[]`, so the UI's mesh dropdown and `VesselsBackend.set_count()`
  always have something to work with (the old `set_count()` early-return on an empty mesh pool became
  dead code and was removed).
- `ShipBackend.set_mesh()` now returns `True` when it resolved to the fallback (propagated through
  `ScenarioManager.set_own_ship_mesh()` and `ScenarioController.set_own_ship_mesh()`) so the UI's "Apply
  to own ship" can show a warning in the status line without `backends/meshes.py` needing to know
  anything about the UI. Vessel spawning (`spawn_vessel`/`set_vessel_count`) doesn't thread a flag back
  the same way -- the UI instead tracks `self._no_real_assets` (set whenever `_refresh_mesh_choices()`
  runs, true exactly when the mesh picker's only option is the fallback) and checks it before any vessel-
  spawn action, which is equivalent since the fallback is the ONLY choice on offer in that case anyway.
  `ScenarioController` grew `set_mesh_dir(path)`/`get_mesh_dir()`, delegating to
  `ScenarioManager.set_mesh_dir()` (which calls `core.meshes.set_model_dir()` then
  `backends.meshes.clear_cache()` together, the one place both need to happen) and
  `core.meshes.WEB_MODEL_DIR` respectively.
- `ui/window.py`'s "Choose folder..." button uses `omni.kit.window.filepicker.FilePickerDialog`
  (`click_apply_handler`, folder-only selection by navigating in then clicking Select without picking a
  file, so the callback's `dirname` is the chosen folder). **NOT yet confirmed live** -- same standing
  caveat as every other Kit-API-dependent piece of this UI (mouse/keyboard event wiring, now
  `CollapsableFrame` too) -- whether this exact filepicker API/usage is correct for this project's
  installed Kit version; try it live and fix the call if it isn't.
- New tests: `tests/test_meshes.py` (`set_model_dir` rebuilds paths and round-trips back),
  `tests/test_backend_meshes.py` (new file -- `fallback_measured()`'s shape, `measure()` routing to it
  for `FALLBACK_MESH_ID`, `build_visual()` producing a correctly-scaled `Cube` for the fallback case AND
  switching cleanly from fallback to a real reference on the same prim path, `clear_cache()`,
  `list_all()`'s fallback-when-empty behavior) -- 110 tests total now, all against real pxr (no stubs),
  same pattern as `test_wave_backend_tiles.py`.

**Third pass the same day: tighter spacing, a stepped base-resolution slider, own-ship movement
buttons, a lower default draft, "Build scene" moved to the top (2026-10-31).**
- Every section-body `ui.VStack`'s `spacing` dropped from 8 to 0 (plus the outer `VStack` holding
  Setup+all the `CollapsableFrame`s), so option rows sit directly against each other -- user, after
  seeing a live screenshot: "옵션들끼리 한칸씩 띄지말고 그냥 바로 다음줄로 오도록 딱 붙혀라" (don't gap
  options apart, pack them tight). The one deliberate `ui.Spacer(height=10)` between Setup and the first
  `CollapsableFrame` stays -- that's a section-level break, not an option-level one.
- `_build_setup_section()`: "Build scene" is now the FIRST widget (was last, after the status label and
  the boat-assets-folder row) -- user: "맨 위가 build Scene이 되도록 해라".
- Water Tiles' "Base resolution" control changed from a free-typed `ui.IntField` to a 4-step
  `ui.IntSlider` (user: raw numbers like "17"/"33" aren't as clean as a slider, and wanted ~4 steps shown
  as tidy powers of 2 -- "8 16 32 64"). The DISPLAYED values (`BASE_RES_DISPLAY_STEPS = [8, 16, 32, 64]`,
  `ui/window.py`) are one less than what's actually passed to `TilePlacementController.set_base_resolution()`
  (9/17/33/65) -- `resolution_for_level()`'s crack-elimination formula needs an ODD base resolution (see
  its own docstring), so the slider shows the clean number while still driving the real one underneath.
  Default index 1 (displayed 16 / real 17) matches `tile_placement.DEFAULT_BASE_RESOLUTION` exactly, so
  the slider's default position reproduces today's default behavior unchanged.
- `ShipPhysicsCoeffs.draft_to_beam_ratio`'s default lowered from 0.1 to 0.05 (user: a better match for
  this project's own boats) -- `tests/test_ship_motion.py`'s hand-computed regression test was updated to
  read the ratio off `coeffs.draft_to_beam_ratio` rather than hardcoding the old `0.1` literal, so it
  stays correct if this default ever moves again.
- Own Ship gained a small directional pad -- "Forward (I)" / "Left (J)" / "Back (K)" / "Right (L)"
  buttons, each wired via `set_mouse_pressed_fn`/`set_mouse_released_fn` to call
  `ScenarioController.ship_control(name, True/False)` for as long as it's held (the UI-button equivalent
  of holding the matching keyboard key, not a one-shot nudge), plus a one-line reminder that I/J/K/L
  itself still works. This was prompted by the user asking to "make movement keys for the own ship,
  something other than WASD" and to expose the same control as UI buttons -- I/J/K/L (`backends/ship.py`,
  unchanged) already satisfies "not WASD" and was already fully wired, just no longer visible anywhere in
  the UI after the previous round stripped its explanatory label; this round re-surfaces it (as a button
  label, not a paragraph) instead of re-deriving a new key scheme. **NOT yet confirmed live** (same
  standing caveat as every other Kit-API-dependent piece of this UI) whether
  `set_mouse_pressed_fn`/`set_mouse_released_fn`'s `(x, y, button, modifier)` callback signature is
  exactly right for this project's installed Kit version.
- Correction, same conversation: "선박 회전하는 키정도는 angular 옆에 써두는게 좋을 것 같고" was initially
  (mis)read as "label the own-ship turn buttons with their keys" (addressed above) -- the user clarified
  it actually meant the VESSEL-placement "[ / ] also nudge yaw by Step" hint, which the second-pass
  prose-stripping round had deleted outright along with everything else, should come back as a short
  comment next to the Vessels section's "Angular" field specifically (not the own ship at all). Restored
  as a single inline label right after the Angular field on the same row: `"([ / ] rotate while
  placing)"` -- compact, matching this round's own "no paragraphs" rule, placed exactly where asked
  rather than where it's most mechanically related (the actual yaw-nudge-while-placing behavior is tied
  to the Yaw/Step fields below, not Angular, which is the vessel's autonomous turning SPEED -- but the
  user asked for it beside Angular specifically, so that's where it went).

**Own ship Linear/Angular speed control, tile-level ceiling lowered 10 -> 6 after a live crash, I/J/K/L
reported not working (2026-11-01).**
- `MAX_TILE_LEVEL` (`ui/tile_placement.py`) lowered from 10 to 6, confirmed live: "30000미터하면 터진다"
  (breaks at ~30,000m). The math behind why: `resolution_for_level()` keeps constant vertex SPACING
  across levels, which means vertex COUNT grows with tile AREA, not linearly with level -- at the default
  `base_resolution=17`, level 10's ~32.8km tile works out to a `(16*2**10+1)^2 ~= 268M`-vertex single
  mesh, while level 6's ~2048m tile is "only" `(16*64+1)^2 ~= 1.05M` vertices, apparently the practical
  ceiling that actually holds up. Not a structural fix (capping resolution growth independent of tile
  size would be the real one) -- just pulling the UI ceiling back from a value a previous round had
  pushed toward "uncapped" without this having been tried live yet.
- Own ship had no Linear/Angular speed control at all, unlike vessels (which get `linear_speed`/
  `angular_speed` per spawn_vessel() call) -- `backends/ship.py`'s key-held forward/back thrust and
  left/right turn torque were hardcoded to module constants (`DEFAULT_THRUST_FRACTION=0.3`,
  `KEY_TURN_FRACTION=0.01`), the exact same fraction-of-(mass*gravity)/(max_torque) convention as
  `core.vessel.DEFAULT_LINEAR_SPEED`/`DEFAULT_ANGULAR_SPEED` (same default values too -- confirmed these
  were always meant to be the ship-equivalent of the same parameter, just never exposed). Made instance
  state (`self._linear_fraction`/`self._angular_fraction`, seeded from those same module constants) with
  a new `set_speed(linear=None, angular=None)`, threaded through
  `ScenarioManager.set_ship_speed()`/`ScenarioController.set_ship_speed()`, and a Linear/Angular field row
  in the Own Ship UI section (applies live on change, same pattern as the wave/physics sliders) --
  `tests/test_ship_backend.py` (new file) covers `set_speed()` directly since it's pure Python, unlike
  `build()`/`_on_update()` which stay untested here (pxr-dependent, same standing caveat as the rest of
  `ship.py`). 112 tests total now.
- User reported I/J/K/L not actually moving the ship live. Static review of `_start_keyboard()`/
  `_on_update()` found no code bug -- the keyboard subscription setup (`carb.input`, unchanged this
  round) looks structurally correct, and the far more likely explanations are procedural: (a) "Build
  scene" no longer auto-spawns the ship (this session's own earlier change) -- without clicking "Apply to
  own ship" first, there's no body for the keys to move; (b) the own ship only responds while the
  timeline is PLAYING (`_on_update()`'s own early-return), not just built; (c) Omniverse apps commonly
  only route raw keyboard events to `carb.input` subscribers while the 3D VIEWPORT has focus, not a
  floating extension panel -- if focus was on this panel (e.g. a field was just edited) when I/J/K/L was
  pressed, that would also explain it. (c) is the one most worth checking live first. The Own Ship
  section's keyboard hint label was reworded to say so directly ("click into the 3D viewport first (not
  this panel)") rather than leave it as an open question -- **still not confirmed live, same standing
  caveat as every other keyboard/mouse-event-dependent piece of this UI**; if none of (a)/(b)/(c) explain
  it, the actual `carb.input`/`omni.appwindow` wiring needs to be debugged live next, not re-guessed here.

## Current status (as of 2026-10-30)

Everything above is built: `core/` + `backends/wave.py`'s tile registry (112 passing tests total),
`runtime/manager.py`, all three backends, `api.py`, `extension.py`, `ui/window.py`, `ui/placement.py`,
`ui/tile_placement.py`. Verified so far: `core/`'s pure-Python logic (real pytest, including a regression
test for the model-path portability bug and the wave-tiling phase-consistency math), every file
parses/imports cleanly, `placement.py`'s ray-unprojection math (analytic `Gf.Matrix4d` cases) and its
ghost/waypoint-marker prim building (real in-memory USD stage + a real measured `boat1.usd`), `wave.py`'s
tile-registry mechanics INCLUDING incremental `add_tile`/`remove_tile`/`generate_tile_id` (also against a
real in-memory USD stage), and `tile_placement.py`'s ghost-square corner/midpoint/length math (scratch
script, same pattern). NOT yet verified: actual behavior inside a running Isaac Sim (no live instance
available while building this), including whether `carb.input`'s mouse/keyboard subscriptions actually
fire the way the original `scenario_ui.py`'s hard-won workarounds assumed, whether a non-default wave
tile layout actually looks seamless live, and whether mouse-click tile placement's event wiring works the
same way vessel placement's does -- expect to find and fix real bugs once `Build scene` + "Place with
mouse" (vessels) + "Place tiles with mouse" are actually pressed, and once `set_layout()`/`add_tile()`
are tried with something other than the default, in a live session. Not yet pushed to GitHub.
