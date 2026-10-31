"""Gerstner wave mesh(es) + salt_water material -- ported from boat_mesh_pipeline/06_scenario_editor's
spawn_gerstner_wave.py (mesh/material/per-frame animation) + apply_salt_water.py (the nonvisual
material tag) + restart_stage.py (the save-as-stage+reopen that makes the tag take effect). All the
actual wave MATH lives in core.gerstner -- this module is just the pxr plumbing around it.

Multi-tile layout (2026-10-30): the water is built as one or more independent tile meshes under
WAVE_PATH + "/Tiles/<tile_id>", all sharing ONE material, rather than a single hardcoded mesh --
set_layout()'s own docstring has the details, CLAUDE.md has the "why" (extensibility toward much larger
or non-square scenarios without over-building that today). The *default* layout (set_layout() never
called) is exactly one tile, identical in every way to this module's original single-mesh behavior, so
nothing that only drives the 7 existing wave UI sliders needs to change or even be aware tiling exists.

setup_async() is the one-time sequence (build mesh+material, tag it salt_water, then await the
save_as_stage_async/open_stage_async restart) -- NOT done in attach(), because it's async and because
the restart reopens the stage out from under anything built before it. ScenarioManager.setup_async()
calls this FIRST, then build()s the ship/vessels backends on the freshly-reopened stage -- see that
method's own docstring for the full ordering and why (same order main.py's _run() coroutine used).

IMPORTANT (restart_stage.py's own hard-won finding): context.save_as_stage(url)/open_stage(url) (no
"_async") are NOT blocking -- they fire a callback-based request and return immediately, no guarantee
of completion or ordering. Only the _async versions, awaited, actually wait.
"""
import logging
import math

from ..core import gerstner, gerstner_gpu
from .base import Backend

log = logging.getLogger("maritime_scenario")

WAVE_PATH = "/World/GerstnerWave"
TILES_PATH = WAVE_PATH + "/Tiles"
MATERIAL_PATH = WAVE_PATH + "/Looks/WaterPreview"
RESTART_SAVE_PATH = "/tmp/maritime_scenario_restart_scratch.usd"
DEFAULT_ACTIVE_RADIUS_M = 150.0   # see update_tile_activity()'s own docstring for what this controls

# The 5 "shared physical sea state" fields, authored on WAVE_PATH itself and read by every tile plus
# height_at() -- deliberately excludes size_m/resolution, which are per-tile (see WaveTileSpec).
_SHARED_FIELD_TO_ATTR = {
  "amplitude_m": "wave:amplitude",
  "wavelength_m": "wave:wavelength",
  "speed_mps": "wave:speed",
  "steepness": "wave:steepness",
  "num_waves": "wave:num_waves",
}


def _to_vt_vec3f_array(np_array):
  """Vt.Vec3fArray.FromNumpy is ~40x faster than a Python list comprehension of Gf.Vec3f (measured
  2026-10-30: ~0.4ms vs ~17ms for ~16,600 points) -- falls back to the list comprehension if FromNumpy
  isn't available in whatever pxr build is actually running (not guaranteed across every USD version),
  so this stays correct even without the fast path, just slower."""
  from pxr import Gf, Vt
  try:
    return Vt.Vec3fArray.FromNumpy(np_array.astype("float32", copy=False))
  except AttributeError:
    return Vt.Vec3fArray([Gf.Vec3f(*p) for p in np_array])


class WaveBackend(Backend):
  name = "wave"

  def __init__(self, restart_save_path=RESTART_SAVE_PATH):
    self.restart_save_path = restart_save_path
    self._sub = None
    self._built = False
    self.tiles = {}         # tile_id -> gerstner.WaveTileSpec, the CURRENT layout
    self.tile_active = {}   # tile_id -> bool, driven every frame by update_tile_activity() below
    self._next_tile_num = 0   # generate_tile_id()'s counter
    self.active_radius_m = DEFAULT_ACTIVE_RADIUS_M
    # Plain, toggleable attribute (not hidden/UI-only) -- lets GPU batching be turned off even on a
    # GPU-capable machine for comparison/debugging. See gerstner_gpu.py + CLAUDE.md for the measured
    # numbers behind this (2026-10-30): batching every active same-resolution/size tile into ONE Warp
    # kernel launch is ~3.6x faster than today's per-tile NumPy path once more than one tile shares a
    # bucket -- see _set_points_all() below for where that batching actually happens.
    self.use_gpu = gerstner_gpu.GPU_AVAILABLE

  # ------------------------------------------------------------ one-time async setup
  async def setup_async(self):
    """Builds the wave INFRASTRUCTURE (root prim with the shared wave:* physical attrs + material),
    tags the material salt_water, then save+reopens the stage so that tag actually takes effect -- but
    builds NO visible tile by default (2026-10-30: this used to default to the single-tile classic
    plane, but the user wants that to be an explicit choice, not automatic -- see
    ScenarioController.set_wave_single_plane() / the UI's "Build default wave plane" button, called
    separately after this). height_at() still works correctly with zero tiles (it only ever reads the
    shared wave:* attrs off the root prim, never tile geometry), so ship/vessel buoyancy physics is
    unaffected either way.

    Safe to call again (e.g. a user-triggered "Rebuild" in the UI) -- ALWAYS rebuilds fresh and restarts
    again, using whatever layout is CURRENTLY configured (self.tiles, if something already called
    set_layout()/set_wave_single_plane()/placed tiles with the mouse) or staying empty otherwise -- so a
    "Rebuild" after any of those correctly rebuilds that same layout, not reset back to empty."""
    stage = self.context.stage()
    tile_specs = list(self.tiles.values())
    self.set_layout(tile_specs)
    self._apply_salt_water(stage)
    log.info("maritime_scenario(wave): restarting stage (save + reopen) so salt_water takes effect...")
    await self._restart_stage_async()
    log.info("maritime_scenario(wave): stage restarted (%s)", self.restart_save_path)
    self._start_update()
    self._built = True

  # ------------------------------------------------------------ layout (tiles)
  def set_layout(self, tile_specs):
    """Rebuilds the water as exactly these tiles -- clears any existing tiles first (simple, robust
    clear-and-rebuild, same approach this module always used via RemovePrim; this is a deliberate,
    infrequent action, not a per-frame/per-slider-drag one, so the rebuild cost doesn't matter).

    gerstner.single_tile_layout(params) reproduces the classic single-mesh wave plane (one tile named
    "main", size/resolution from the wave UI sliders) -- an explicit opt-in via
    ScenarioController.set_wave_single_plane(), NOT automatic on setup_async() (changed 2026-10-30; see
    that method's own docstring). gerstner.grid_tile_layout(nx, ny, tile_size_m, tile_resolution) is a
    simple rectangular tiling
    preset. Both (and any future layout, e.g. a river-shaped polyline of tiles) are just a
    list[WaveTileSpec] -- set_layout() doesn't care how the list was built.

    The shared wave:* physical attrs on WAVE_PATH (amplitude/wavelength/speed/steepness/num_waves) are
    preserved across a re-layout -- only seeded from state.wave the very first time WAVE_PATH is
    created, never overwritten here, so switching layouts doesn't reset the current sea state."""
    stage = self.context.stage()
    root = self._ensure_root(stage)
    if not root.GetAttribute("wave:amplitude").IsValid():
      self._seed_shared_params(root, self.context.manager.state_ref.wave)
    self._ensure_material(stage)

    if stage.GetPrimAtPath(TILES_PATH):
      stage.RemovePrim(TILES_PATH)
    shared = self._read_shared_params(root)
    for spec in tile_specs:
      self._build_tile_mesh(stage, spec, shared)

    self.tiles = {spec.tile_id: spec for spec in tile_specs}
    self.tile_active = {tile_id: True for tile_id in self.tiles}
    log.info("maritime_scenario(wave): layout set to %d tile(s): %s",
              len(self.tiles), sorted(self.tiles))

  def set_tile_active(self, tile_id, active):
    """Manual override -- update_tile_activity() (below) is what actually drives this every frame now
    (2026-10-30). An inactive tile keeps its last-set points (frozen), it isn't hidden/removed."""
    if tile_id not in self.tiles:
      log.warning("maritime_scenario(wave): set_tile_active: unknown tile_id %r", tile_id)
      return
    self.tile_active[tile_id] = bool(active)

  def set_active_radius(self, radius_m):
    self.active_radius_m = float(radius_m)

  def update_tile_activity(self):
    """Distance-based LOD: tiles whose center is farther than active_radius_m (+ their own half-size, so
    a tile doesn't go inactive while the ship is still near ITS edge, not just its center) from the own
    ship stop being re-animated every frame -- set_tile_active()'s own docstring: an inactive tile keeps
    its last-set points/normals frozen, it isn't hidden/removed. This is what actually saves the
    per-vertex Python trig cost (core.gerstner.grid_points()/grid_normals(), called every frame per
    ACTIVE tile) that scales with total active tiled area -- the O(n_tiles) distance arithmetic here is
    cheap, not the expensive part. Called every frame from _on_update(), before _set_points_all().
    No-op (leaves whatever's currently set) if there's no ship yet -- today's prior default (everything
    active) stays in effect until a ship exists to measure distance from."""
    ship_backend = self.context.manager.get_backend("ship")
    if ship_backend is None:
      return
    pos = ship_backend.get_position()
    if pos is None:
      return
    ship_x, ship_y = pos
    for tile_id, spec in self.tiles.items():
      dist = math.hypot(spec.center_x - ship_x, spec.center_y - ship_y)
      self.tile_active[tile_id] = dist <= self.active_radius_m + spec.size_m / 2.0

  def list_tiles(self):
    return dict(self.tiles)

  def generate_tile_id(self, prefix="tile"):
    """A fresh tile_id not already in self.tiles -- the UI/API layer calls this before constructing a
    new WaveTileSpec (frozen, so it needs its id up front, unlike set_layout()'s callers which build
    their own ids directly, e.g. grid_tile_layout()'s "tile_{i}_{j}")."""
    while True:
      self._next_tile_num += 1
      candidate = f"{prefix}_{self._next_tile_num}"
      if candidate not in self.tiles:
        return candidate

  def add_tile(self, spec: gerstner.WaveTileSpec):
    """Adds ONE tile without touching any other tile -- O(1), unlike set_layout()'s full clear-and-
    rebuild of every tile, and doesn't reset other tiles' tile_active state. The right primitive for
    incremental placement (mouse-click tile laying, or any future "append a tile" caller)."""
    if spec.tile_id in self.tiles:
      log.warning("maritime_scenario(wave): add_tile: tile_id %r already exists, ignoring", spec.tile_id)
      return
    stage = self.context.stage()
    root = self._ensure_root(stage)
    if not root.GetAttribute("wave:amplitude").IsValid():
      self._seed_shared_params(root, self.context.manager.state_ref.wave)
    self._ensure_material(stage)
    shared = self._read_shared_params(root)
    self._build_tile_mesh(stage, spec, shared)
    self.tiles[spec.tile_id] = spec
    self.tile_active[spec.tile_id] = True

  def remove_tile(self, tile_id):
    if tile_id not in self.tiles:
      log.warning("maritime_scenario(wave): remove_tile: unknown tile_id %r", tile_id)
      return
    stage = self.context.stage()
    tile_path = self._tile_path(tile_id)
    if stage.GetPrimAtPath(tile_path):
      stage.RemovePrim(tile_path)
    del self.tiles[tile_id]
    self.tile_active.pop(tile_id, None)

  @staticmethod
  def _tile_path(tile_id):
    return TILES_PATH + f"/{tile_id}"

  def _ensure_root(self, stage):
    from pxr import UsdGeom
    root = stage.GetPrimAtPath(WAVE_PATH)
    if root:
      return root
    return UsdGeom.Xform.Define(stage, WAVE_PATH).GetPrim()

  def _ensure_material(self, stage):
    from pxr import Sdf, UsdShade
    material_prim = stage.GetPrimAtPath(MATERIAL_PATH)
    if material_prim:
      return UsdShade.Material(material_prim)
    material = UsdShade.Material.Define(stage, MATERIAL_PATH)
    shader = UsdShade.Shader.Define(stage, MATERIAL_PATH + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set((0.1, 0.35, 0.55))
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.6)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.1)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material

  def _seed_shared_params(self, root, params: gerstner.WaveParams):
    from pxr import Sdf
    root.CreateAttribute("wave:amplitude", Sdf.ValueTypeNames.Float).Set(params.amplitude_m)
    root.CreateAttribute("wave:wavelength", Sdf.ValueTypeNames.Float).Set(params.wavelength_m)
    root.CreateAttribute("wave:speed", Sdf.ValueTypeNames.Float).Set(params.speed_mps)
    root.CreateAttribute("wave:steepness", Sdf.ValueTypeNames.Float).Set(params.steepness)
    root.CreateAttribute("wave:num_waves", Sdf.ValueTypeNames.Int).Set(params.num_waves)

  def _read_shared_params(self, root):
    return {field: root.GetAttribute(attr).Get() for field, attr in _SHARED_FIELD_TO_ATTR.items()}

  def _build_tile_mesh(self, stage, spec: gerstner.WaveTileSpec, shared):
    from pxr import Gf, Sdf, UsdGeom, UsdShade

    mesh = UsdGeom.Mesh.Define(stage, self._tile_path(spec.tile_id))
    prim = mesh.GetPrim()
    # grid_points()'s own docstring: it returns LOCAL-frame points (always spanning -size/2..+size/2
    # around the prim's own origin) and relies on the prim's own world transform to actually position
    # the tile -- origin_xy only shifts the Gerstner PHASE, never the output coordinates. This translate
    # op is that transform; without it every tile renders at world (0,0,0) regardless of spec.center_x/
    # center_y (found 2026-10-30: the mouse-placed tiles all appeared stacked at the origin -- this was
    # missing from the very first tile-registry version, not something the mouse-placement feature
    # itself broke; the single default "main" tile never exposed it since its own center IS (0,0)).
    UsdGeom.Xformable(prim).AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(
      Gf.Vec3d(spec.center_x, spec.center_y, 0.0))
    # Explicit per-vertex normals (see _set_tile_points) -- without this the renderer computes normals
    # from each tile's own local topology, which disagrees with its neighbor's independently-computed
    # normals right at the shared boundary even though the vertex POSITIONS already match exactly,
    # showing up as a visible lighting/brightness seam between tiles (found 2026-10-30).
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    # UsdGeomMesh defaults to subdivisionScheme="catmullClark" when unset -- the renderer would then
    # refine each tile's raw quads into a smoothed subdivision surface using ONLY that tile's own
    # internal topology, pulling each tile's boundary independently of its neighbor's. That refined
    # (not the raw authored) surface is what actually gets rendered, so even perfectly matching points
    # + normals at the boundary can still show a seam, because the SUBDIVIDED edges don't actually
    # agree between two separately-subdivided tiles (found 2026-10-30, after the position+normal fixes
    # still left a visible boundary line). "none" renders the authored polygons as-is -- flat per-face
    # facets at this resolution, no refinement, so there's no neighbor-unaware smoothing left to disagree.
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    prim.CreateAttribute("tile:center_x", Sdf.ValueTypeNames.Float).Set(spec.center_x)
    prim.CreateAttribute("tile:center_y", Sdf.ValueTypeNames.Float).Set(spec.center_y)
    prim.CreateAttribute("tile:size", Sdf.ValueTypeNames.Float).Set(spec.size_m)
    prim.CreateAttribute("tile:resolution", Sdf.ValueTypeNames.Int).Set(spec.resolution)

    counts, indices = gerstner.build_topology(spec.resolution)
    mesh.GetFaceVertexCountsAttr().Set(counts)
    mesh.GetFaceVertexIndicesAttr().Set(indices)

    material = UsdShade.Material(stage.GetPrimAtPath(MATERIAL_PATH))
    UsdShade.MaterialBindingAPI(prim).Bind(material)

    tile_params = gerstner.WaveParams(size_m=spec.size_m, resolution=spec.resolution, **shared)
    self._set_tile_points(mesh, 0.0, tile_params, spec)

  def _set_tile_points(self, mesh, t, params: gerstner.WaveParams, spec: gerstner.WaveTileSpec):
    """The one shared per-frame/per-build path (_build_tile_mesh() and _set_points_all() both route
    through this) -- uses the vectorized numpy grid math (2026-10-30, measured ~30x faster end-to-end
    than the scalar grid_points()/grid_normals() + Gf.Vec3f list-comprehension pair this replaced: a
    single 256m/resolution-129 tile went from ~74ms to ~2.5ms -- see core.gerstner.
    grid_points_and_normals_np()'s own docstring for the full numbers, this is what actually fixed the
    live lag distance-based tile activation alone wasn't enough for)."""
    origin_xy = (spec.center_x, spec.center_y)
    points, normals = gerstner.grid_points_and_normals_np(t, params, origin_xy=origin_xy)
    mesh.GetPointsAttr().Set(_to_vt_vec3f_array(points))
    mesh.GetNormalsAttr().Set(_to_vt_vec3f_array(normals))

  def _set_points_all(self, stage, t):
    """Recomputes points for every ACTIVE tile -- the one place both apply_state() (a wave slider
    changed) and _on_update() (a playing frame ticked) push new geometry.

    GPU batching (2026-10-30): active tiles are grouped into buckets keyed by (resolution, size_m) --
    tiles at the same quadtree level always share both, by construction of resolution_for_level(), and
    keying on the pair (not resolution alone) stays correct even for a manually-placed tile via the API
    with a mismatched size. When self.use_gpu and a bucket has more than one tile, the whole bucket is
    computed in ONE gerstner_gpu.compute_batch() launch -- measured ~3.6x faster than calling
    _set_tile_points() once per tile in that bucket (see gerstner_gpu.py's docstring for the numbers). A
    singleton bucket, or any bucket when use_gpu is False, falls back to the existing per-tile
    _set_tile_points() NumPy path unchanged -- batching a single tile isn't worth the GPU round-trip."""
    from pxr import UsdGeom
    root = stage.GetPrimAtPath(WAVE_PATH)
    if not root:
      return
    shared = self._read_shared_params(root)

    buckets = {}   # (resolution, size_m) -> list[(tile_id, spec, tile_prim)]
    for tile_id, spec in self.tiles.items():
      if not self.tile_active.get(tile_id, True):
        continue
      tile_prim = stage.GetPrimAtPath(self._tile_path(tile_id))
      if not tile_prim:
        continue
      buckets.setdefault((spec.resolution, spec.size_m), []).append((tile_id, spec, tile_prim))

    for (resolution, size_m), entries in buckets.items():
      tile_params = gerstner.WaveParams(size_m=size_m, resolution=resolution, **shared)
      if self.use_gpu and len(entries) > 1:
        origins = [(spec.center_x, spec.center_y) for _tile_id, spec, _prim in entries]
        results = gerstner_gpu.compute_batch(t, tile_params, origins)
        for (tile_id, spec, tile_prim), (points, normals) in zip(entries, results):
          mesh = UsdGeom.Mesh(tile_prim)
          mesh.GetPointsAttr().Set(_to_vt_vec3f_array(points))
          mesh.GetNormalsAttr().Set(_to_vt_vec3f_array(normals))
      else:
        for tile_id, spec, tile_prim in entries:
          self._set_tile_points(UsdGeom.Mesh(tile_prim), t, tile_params, spec)

  def _apply_salt_water(self, stage):
    from isaacsim.sensors.rtx.impl.nonvisual_materials import apply_nonvisual_material
    from pxr import UsdShade

    material_prim = stage.GetPrimAtPath(MATERIAL_PATH)
    if not material_prim:
      raise RuntimeError(f"{MATERIAL_PATH} not found -- set_layout() should have created it")
    apply_nonvisual_material(UsdShade.Material(material_prim).GetPrim(), "salt_water")
    log.info("maritime_scenario(wave): salt_water applied to %s", MATERIAL_PATH)

  async def _restart_stage_async(self):
    import omni.usd
    context = omni.usd.get_context()
    # save_as_stage_async's docstring claims (bool, str), but its real callback resolves THREE values
    # (result, err_msg, saved_layers) -- confirmed in boat_mesh_pipeline's own restart_stage.py by
    # reading Isaac Sim's source after this mismatch threw "ValueError: too many values to unpack".
    ok, err, _saved_layers = await context.save_as_stage_async(self.restart_save_path)
    if not ok:
      raise RuntimeError(f"save_as_stage_async failed: {err}")
    ok, err = await context.open_stage_async(self.restart_save_path)
    if not ok:
      raise RuntimeError(f"open_stage_async failed: {err}")

  # ------------------------------------------------------------ Backend interface
  def apply_state(self, state, changed):
    if "wave" not in changed or not self._built:
      return
    stage = self.context.stage()
    root = stage.GetPrimAtPath(WAVE_PATH)
    if not root:
      return
    params = state.wave
    # Shared physical attrs apply to every tile uniformly -- same real sea state regardless of how the
    # water is tiled.
    for field, attr in _SHARED_FIELD_TO_ATTR.items():
      root.GetAttribute(attr).Set(getattr(params, field))
    # size_m/resolution only drive the "main" tile, and only while it's still the (single-tile) default
    # layout -- a grid/other layout's tiles were sized explicitly via their own layout call, independent
    # of these sliders, so a slider drag is a no-op for THEIR size while a non-default layout is active.
    if set(self.tiles) == {"main"}:
      from pxr import UsdGeom
      tile_prim = stage.GetPrimAtPath(self._tile_path("main"))
      if tile_prim:
        # resolution/size change the mesh's point COUNT, so topology has to be rebuilt to match -- not
        # just a live attribute set (see core.gerstner.build_topology()'s own docstring).
        mesh = UsdGeom.Mesh(tile_prim)
        counts, indices = gerstner.build_topology(params.resolution)
        mesh.GetFaceVertexCountsAttr().Set(counts)
        mesh.GetFaceVertexIndicesAttr().Set(indices)
        tile_prim.GetAttribute("tile:size").Set(params.size_m)
        tile_prim.GetAttribute("tile:resolution").Set(params.resolution)
        self.tiles["main"] = gerstner.WaveTileSpec("main", 0.0, 0.0, params.size_m, params.resolution)
    t = self.context.timeline().get_current_time() if self.context.timeline().is_playing() else 0.0
    self._set_points_all(stage, t)

  def _start_update(self):
    import omni.kit.app
    if self._sub is None:
      self._sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(self._on_update)

  def _on_update(self, e):
    timeline = self.context.timeline()
    if not timeline.is_playing():
      return
    stage = self.context.stage()
    if not stage.GetPrimAtPath(WAVE_PATH):
      return
    t = timeline.get_current_time()
    self.update_tile_activity()
    self._set_points_all(stage, t)

  def height_at(self, x, y):
    """World-space wave height at (x, y) right now -- what the vessels/ship backends and the UI's
    ghost-placement preview sample to float something on the surface. Unaffected by tiling: this is a
    pure function of the shared wave:* attrs on WAVE_PATH + world (x,y), it never reads tile/mesh
    geometry at all."""
    stage = self.context.stage()
    prim = stage.GetPrimAtPath(WAVE_PATH)
    if not prim:
      return 0.0
    from pxr import Usd, UsdGeom
    shared = self._read_shared_params(prim)
    params = gerstner.WaveParams(**shared)
    origin = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
    t = self.context.timeline().get_current_time()
    return gerstner.wave_height(x, y, t, params, origin_xyz=(origin[0], origin[1], origin[2]))

  def detach(self):
    sub, self._sub = self._sub, None
    if sub is not None:
      del sub
    self._built = False
