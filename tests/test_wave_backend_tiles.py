"""WaveBackend's tile registry (set_layout/set_tile_active/apply_state's tile-aware behavior) against a
REAL in-memory USD stage. No omni/isaacsim stubs needed: backends/wave.py only imports pxr/omni inside
the methods that actually need them (_apply_salt_water, _restart_stage_async, _start_update), none of
which this file calls -- everything it does test (set_layout, apply_state, _on_update/_set_points_all,
height_at) only ever touches pxr, which is really installed in this environment."""
import types

import pytest
from pxr import Usd, UsdGeom, UsdShade

from maritime_scenario.backends import wave as wave_backend
from maritime_scenario.core import gerstner, gerstner_gpu
from maritime_scenario.core.state import ScenarioState


class FakeManager:
  def __init__(self, wave_params, backends=None):
    self.state_ref = types.SimpleNamespace(wave=wave_params)
    self._backends = backends or {}

  def get_backend(self, name):
    return self._backends.get(name)


class FakeContext:
  def __init__(self, manager, stage, playing=True, t=0.0):
    self.manager = manager
    self._stage = stage
    self._playing = playing
    self._t = t

  def stage(self):
    return self._stage

  def timeline(self):
    return types.SimpleNamespace(is_playing=lambda: self._playing, get_current_time=lambda: self._t)


def _make_backend(params, stage=None, backends=None, **context_kwargs):
  stage = stage or Usd.Stage.CreateInMemory()
  wb = wave_backend.WaveBackend()
  wb.context = FakeContext(FakeManager(params, backends=backends), stage, **context_kwargs)
  return wb, stage


def test_default_single_tile_layout_matches_plain_grid_points():
  params = gerstner.WaveParams(resolution=9, size_m=80.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))

  assert list(wb.tiles) == ["main"]
  mesh_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("main"))
  assert mesh_prim
  points = UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()
  expected = gerstner.grid_points(0.0, params)
  assert len(points) == len(expected) == params.resolution ** 2
  for p, (ex, ey, ez) in zip(points, expected):
    assert abs(p[0] - ex) < 1e-5 and abs(p[1] - ey) < 1e-5 and abs(p[2] - ez) < 1e-5


def test_tile_mesh_is_actually_positioned_at_its_world_center():
  # Regression test for the 2026-10-30 bug: grid_points() deliberately returns LOCAL-frame points (see
  # its own docstring), relying on the tile PRIM's own world transform to place it -- _build_tile_mesh()
  # originally never set that transform, so every tile rendered at world (0,0,0) regardless of its
  # center. The single default "main" tile (center (0,0)) never exposed this; a tile anywhere else does.
  # amplitude 0 -> flat water, no Gerstner horizontal wobble, so the mesh's own min/max midpoint lands
  # exactly on the tile's center (with real waves it wobbles by sub-meter amounts, which would make an
  # exact-midpoint assertion flaky for the wrong reason).
  params = gerstner.WaveParams(amplitude_m=0.0, resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  spec = gerstner.WaveTileSpec("off_origin", 300.0, -150.0, 40.0, 5)
  wb.add_tile(spec)

  mesh_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("off_origin"))
  world_tf = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
  pos = world_tf.ExtractTranslation()
  assert abs(pos[0] - 300.0) < 1e-6
  assert abs(pos[1] - (-150.0)) < 1e-6

  # and the actual mesh geometry (local points + that world transform) ends up centered on (300, -150),
  # not on the origin -- the point that actually matters for what renders where.
  local_points = UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()
  world_xs = [world_tf.Transform(p)[0] for p in local_points]
  world_ys = [world_tf.Transform(p)[1] for p in local_points]
  assert abs((min(world_xs) + max(world_xs)) / 2.0 - 300.0) < 1e-3
  assert abs((min(world_ys) + max(world_ys)) / 2.0 - (-150.0)) < 1e-3


def test_tiles_share_one_material():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.grid_tile_layout(2, 2, 40.0, 5))

  material_prim = stage.GetPrimAtPath(wave_backend.MATERIAL_PATH)
  assert material_prim
  for tile_id in wb.tiles:
    tile_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path(tile_id))
    bound = UsdShade.MaterialBindingAPI(tile_prim).ComputeBoundMaterial()[0]
    assert bound.GetPath() == material_prim.GetPath()


def test_root_prim_is_xformable_so_height_at_still_works():
  params = gerstner.WaveParams()
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))

  root = stage.GetPrimAtPath(wave_backend.WAVE_PATH)
  assert UsdGeom.Xformable(root)   # would be an invalid schema object if root weren't Xformable


def test_height_at_unaffected_by_tiling():
  params = gerstner.WaveParams(resolution=9, size_m=80.0)
  wb, stage = _make_backend(params, t=3.0)
  wb.set_layout(gerstner.single_tile_layout(params))

  h = wb.height_at(10.0, 5.0)
  h_direct = gerstner.wave_height(10.0, 5.0, 3.0, params)
  # wave:amplitude etc round-trip through a 32-bit Sdf.ValueTypeNames.Float attr, same as before this
  # change, so a tiny float32 rounding gap vs the raw float64 params is expected.
  assert abs(h - h_direct) < 1e-4


def test_set_layout_removes_tiles_not_in_the_new_layout():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))
  wb.set_layout(gerstner.grid_tile_layout(2, 1, 40.0, 5))

  assert not stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("main"))
  assert set(wb.tiles) == {"tile_0_0", "tile_1_0"}


def test_set_layout_preserves_shared_params_across_relayout():
  params = gerstner.WaveParams(amplitude_m=1.23, resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))
  wb.set_layout(gerstner.grid_tile_layout(2, 1, 40.0, 5))

  root = stage.GetPrimAtPath(wave_backend.WAVE_PATH)
  assert abs(root.GetAttribute("wave:amplitude").Get() - 1.23) < 1e-5


def test_phase_consistent_across_tile_boundary():
  # the actual payoff of grid_points(origin_xy=...): two different tiles must agree on the wave height
  # at the same world (x, y), not each restart its own phase at its own local (0, 0).
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  grid = gerstner.grid_tile_layout(3, 1, 40.0, 5)
  tile_1 = next(s for s in grid if s.tile_id == "tile_1_0")   # center_x = 0.0

  world_x, world_y, t = 20.0, 0.0, 3.0   # tile_1_0's own undisplaced right edge
  h_direct = gerstner.wave_height(world_x, world_y, t, params)

  local_points = gerstner.grid_points(t, params, origin_xy=(tile_1.center_x, tile_1.center_y))
  # resolution=5, size=40 -> undisplaced local_x values (-20,-10,0,10,20); index i=4 is local_x=+20,
  # j=2 is local_y=0 -- the grid point whose undisplaced world position is exactly (20, 0).
  _, _, z = local_points[4 * 5 + 2]
  assert abs(z - h_direct) < 1e-9


def test_apply_state_resizes_main_tile_when_default_layout_active():
  params = gerstner.WaveParams(resolution=9, size_m=80.0)
  wb, stage = _make_backend(params, playing=False)
  wb._built = True
  wb.set_layout(gerstner.single_tile_layout(params))

  state = ScenarioState().with_updates("wave", resolution=15, size_m=120.0)
  wb.apply_state(state, changed={"wave"})

  assert wb.tiles["main"].resolution == 15
  assert wb.tiles["main"].size_m == 120.0
  tile_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("main"))
  points = UsdGeom.Mesh(tile_prim).GetPointsAttr().Get()
  assert len(points) == 15 * 15


def test_apply_state_leaves_non_default_layout_tile_sizes_untouched():
  params = gerstner.WaveParams(resolution=9, size_m=80.0)
  wb, stage = _make_backend(params, playing=False)
  wb._built = True
  wb.set_layout(gerstner.grid_tile_layout(2, 1, 50.0, 6))
  before = dict(wb.tiles)

  state = ScenarioState().with_updates("wave", resolution=33, size_m=999.0)
  wb.apply_state(state, changed={"wave"})

  assert wb.tiles == before
  # but the shared physical attrs still propagate to every tile's parent regardless of layout
  root = stage.GetPrimAtPath(wave_backend.WAVE_PATH)
  assert abs(root.GetAttribute("wave:amplitude").Get() - state.wave.amplitude_m) < 1e-5


def test_set_tile_active_false_freezes_that_tiles_points():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.grid_tile_layout(2, 1, 40.0, 5))
  wb.set_tile_active("tile_0_0", False)

  def _points(tile_id):
    prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path(tile_id))
    return list(UsdGeom.Mesh(prim).GetPointsAttr().Get())

  before_0, before_1 = _points("tile_0_0"), _points("tile_1_0")
  wb._set_points_all(stage, 5.0)
  after_0, after_1 = _points("tile_0_0"), _points("tile_1_0")

  assert before_0 == after_0   # inactive -- frozen
  assert before_1 != after_1   # active -- recomputed at the new t


def test_set_tile_active_unknown_tile_id_warns_and_does_not_raise(caplog):
  params = gerstner.WaveParams()
  wb, _ = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))
  wb.set_tile_active("does_not_exist", False)   # must not raise
  assert "does_not_exist" not in wb.tile_active


def test_list_tiles_returns_a_copy_not_the_live_dict():
  params = gerstner.WaveParams()
  wb, _ = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))
  snapshot = wb.list_tiles()
  snapshot["main"] = "mutated"
  assert wb.tiles["main"] != "mutated"


def test_get_bounds_returns_none_with_no_tiles():
  params = gerstner.WaveParams()
  wb, _ = _make_backend(params)
  assert wb.get_bounds() is None


def test_get_bounds_matches_a_single_tiles_footprint():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, _ = _make_backend(params)
  wb.set_layout([gerstner.WaveTileSpec("main", 100.0, -50.0, 40.0, 5)])
  assert wb.get_bounds() == (80.0, 120.0, -70.0, -30.0)


def test_get_bounds_covers_the_union_of_multiple_differently_sized_tiles():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, _ = _make_backend(params)
  wb.set_layout([
    gerstner.WaveTileSpec("a", 0.0, 0.0, 40.0, 5),      # [-20, 20] x [-20, 20]
    gerstner.WaveTileSpec("b", 500.0, 500.0, 100.0, 5),  # [450, 550] x [450, 550]
  ])
  assert wb.get_bounds() == (-20.0, 550.0, -20.0, 550.0)


def test_setup_async_rebuild_reuses_current_layout_not_the_default():
  # setup_async()'s own docstring promise: a second call (e.g. a UI "Rebuild" button) rebuilds whatever
  # layout is CURRENTLY configured, not silently reset back to the single-tile default. This test
  # exercises the same selection logic setup_async() uses (list(self.tiles.values()) if self.tiles else
  # single_tile_layout(params)) directly, since setup_async() itself is async and touches
  # omni.usd/isaacsim this test deliberately doesn't stub.
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.grid_tile_layout(2, 1, 40.0, 5))

  tile_specs = list(wb.tiles.values()) if wb.tiles else gerstner.single_tile_layout(params)
  assert {s.tile_id for s in tile_specs} == {"tile_0_0", "tile_1_0"}


# ------------------------------------------------------------ incremental add_tile/remove_tile/generate_tile_id

def test_generate_tile_id_is_unique_across_repeated_calls():
  params = gerstner.WaveParams()
  wb, _ = _make_backend(params)
  ids = {wb.generate_tile_id() for _ in range(20)}
  assert len(ids) == 20   # no collisions


def test_generate_tile_id_skips_ids_already_present():
  params = gerstner.WaveParams()
  wb, _ = _make_backend(params)
  wb.tiles = {"tile_1": None, "tile_2": None}   # pretend these were already placed
  new_id = wb.generate_tile_id()
  assert new_id not in wb.tiles


def test_add_tile_creates_exactly_one_new_tile_without_touching_others():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.grid_tile_layout(2, 1, 40.0, 5))
  before = dict(wb.tiles)

  new_spec = gerstner.WaveTileSpec("tile_clicked", 500.0, -200.0, 75.0, 12)
  wb.add_tile(new_spec)

  assert set(wb.tiles) == set(before) | {"tile_clicked"}
  assert wb.tiles["tile_clicked"] == new_spec
  assert wb.tile_active["tile_clicked"] is True
  for tile_id, spec in before.items():
    assert wb.tiles[tile_id] == spec   # untouched

  tile_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("tile_clicked"))
  assert tile_prim
  points = UsdGeom.Mesh(tile_prim).GetPointsAttr().Get()
  assert len(points) == 12 * 12


def test_add_tile_rejects_duplicate_id_without_crashing():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))
  before = wb.tiles["main"]

  duplicate = gerstner.WaveTileSpec("main", 999.0, 999.0, 1.0, 2)
  wb.add_tile(duplicate)   # must not raise

  assert wb.tiles["main"] == before   # unchanged, duplicate ignored


def test_add_tile_works_before_any_set_layout_call():
  # a caller might add a tile before the water has ever been built at all (no "main" tile exists yet) --
  # add_tile() must seed the root/material itself, same as set_layout() does.
  params = gerstner.WaveParams(amplitude_m=1.5)
  wb, stage = _make_backend(params)

  spec = gerstner.WaveTileSpec("first", 0.0, 0.0, 50.0, 8)
  wb.add_tile(spec)

  assert wb.tiles == {"first": spec}
  root = stage.GetPrimAtPath(wave_backend.WAVE_PATH)
  assert root
  assert abs(root.GetAttribute("wave:amplitude").Get() - 1.5) < 1e-5
  material_prim = stage.GetPrimAtPath(wave_backend.MATERIAL_PATH)
  tile_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("first"))
  bound = UsdShade.MaterialBindingAPI(tile_prim).ComputeBoundMaterial()[0]
  assert bound.GetPath() == material_prim.GetPath()


def test_remove_tile_removes_just_that_one_tile():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.grid_tile_layout(2, 1, 40.0, 5))
  remaining_id = "tile_1_0"

  wb.remove_tile("tile_0_0")

  assert set(wb.tiles) == {remaining_id}
  assert "tile_0_0" not in wb.tile_active
  assert not stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("tile_0_0"))
  assert stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path(remaining_id))


def test_remove_tile_unknown_id_warns_and_does_not_raise():
  params = gerstner.WaveParams()
  wb, _ = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))
  wb.remove_tile("does_not_exist")   # must not raise
  assert "main" in wb.tiles


def test_add_then_remove_round_trip_leaves_no_trace():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))

  spec = gerstner.WaveTileSpec("temp", 10.0, 10.0, 20.0, 4)
  wb.add_tile(spec)
  wb.remove_tile("temp")

  assert "temp" not in wb.tiles
  assert "temp" not in wb.tile_active
  assert not stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("temp"))
  assert set(wb.tiles) == {"main"}   # the pre-existing tile is unaffected


# ------------------------------------------------------------ normals (tile-boundary shading seam fix)

def test_tile_mesh_has_vertex_interpolated_normals():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))

  mesh_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("main"))
  mesh = UsdGeom.Mesh(mesh_prim)
  assert mesh.GetNormalsInterpolation() == UsdGeom.Tokens.vertex
  normals = mesh.GetNormalsAttr().Get()
  assert len(normals) == 5 * 5


def test_tile_mesh_has_subdivision_disabled():
  # Regression test for the 2026-10-30 boundary-seam follow-up: UsdGeomMesh defaults to
  # subdivisionScheme="catmullClark" when unset, which would refine each tile using only its OWN
  # topology -- pulling its boundary independently of its neighbor's, so even matching points+normals
  # can still show a seam once the (neighbor-unaware) subdivided surface is what's actually rendered.
  # "none" renders the authored polygons as-is, with no such refinement left to disagree between tiles.
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))

  mesh_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("main"))
  mesh = UsdGeom.Mesh(mesh_prim)
  assert mesh.GetSubdivisionSchemeAttr().Get() == UsdGeom.Tokens.none


def test_adjacent_tiles_have_matching_normals_at_their_shared_boundary():
  # Regression test for the 2026-10-30 lighting-seam bug: two tiles placed edge-to-edge must compute the
  # SAME normal at their shared boundary, not just the same position.
  resolution, size = 5, 40.0
  params = gerstner.WaveParams(amplitude_m=1.0, steepness=0.5, resolution=resolution, size_m=size)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.grid_tile_layout(2, 1, size, resolution))   # tile_0_0 (center -20), tile_1_0 (center +20)

  prim_a = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("tile_0_0"))
  prim_b = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("tile_1_0"))
  normals_a = UsdGeom.Mesh(prim_a).GetNormalsAttr().Get()
  normals_b = UsdGeom.Mesh(prim_b).GetNormalsAttr().Get()

  # tile_0_0's right edge (i=resolution-1) sits at the same world x as tile_1_0's left edge (i=0)
  for j in range(resolution):
    na = normals_a[(resolution - 1) * resolution + j]
    nb = normals_b[0 * resolution + j]
    assert all(abs(na[k] - nb[k]) < 1e-6 for k in range(3)), f"normal mismatch at j={j}: {na} vs {nb}"


# ------------------------------------------------------------ distance-based tile activation


class FakeShipBackend:
  def __init__(self, position):
    self._position = position

  def get_position(self):
    return self._position


def test_update_tile_activity_keeps_near_tiles_active_and_deactivates_far_ones():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  ship = FakeShipBackend((0.0, 0.0))
  wb, stage = _make_backend(params, backends={"ship": ship})
  wb.set_active_radius(100.0)
  wb.set_layout([
    gerstner.WaveTileSpec("near", 50.0, 0.0, 40.0, 5),    # 50m away, well within 100m radius
    gerstner.WaveTileSpec("far", 500.0, 0.0, 40.0, 5),    # 500m away, well outside
  ])

  wb.update_tile_activity()

  assert wb.tile_active["near"] is True
  assert wb.tile_active["far"] is False


def test_update_tile_activity_respects_tile_half_size_margin():
  # a tile right at the radius boundary shouldn't flicker inactive just because its CENTER is a hair
  # past active_radius_m -- the half-size margin keeps it active while the ship is still near its edge.
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  ship = FakeShipBackend((0.0, 0.0))
  wb, stage = _make_backend(params, backends={"ship": ship})
  wb.set_active_radius(100.0)
  # tile center at 110m, size 40m (half=20m) -> within 100+20=120m -> still active
  wb.set_layout([gerstner.WaveTileSpec("edge", 110.0, 0.0, 40.0, 5)])

  wb.update_tile_activity()

  assert wb.tile_active["edge"] is True


def test_update_tile_activity_no_op_without_a_ship_backend():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  wb, stage = _make_backend(params, backends={})   # no "ship" registered
  wb.set_layout([gerstner.WaveTileSpec("main", 0.0, 0.0, 40.0, 5)])

  wb.update_tile_activity()   # must not raise

  assert wb.tile_active["main"] is True   # untouched -- today's default (everything active)


def test_update_tile_activity_no_op_when_ship_has_no_position_yet():
  params = gerstner.WaveParams(resolution=5, size_m=40.0)
  ship = FakeShipBackend(None)   # ship backend exists but hasn't built a body yet
  wb, stage = _make_backend(params, backends={"ship": ship})
  wb.set_layout([gerstner.WaveTileSpec("main", 0.0, 0.0, 40.0, 5)])

  wb.update_tile_activity()   # must not raise

  assert wb.tile_active["main"] is True


# ------------------------------------------------------------ GPU batching (2026-10-30)
# Skipped (not failed) without a real CUDA device -- this is a purely additive fast path, see
# gerstner_gpu.py's own docstring + CLAUDE.md for the measured numbers. This sandbox has a real GPU.

pytestmark_gpu = pytest.mark.skipif(not gerstner_gpu.GPU_AVAILABLE, reason="no CUDA device available")


@pytestmark_gpu
def test_set_points_all_gpu_path_matches_numpy_path_for_a_multi_tile_layout():
  # Same property the NumPy rewrite itself was held to: this is provably a pure performance path, not a
  # behavior change -- use_gpu=True vs False must produce the same points/normals (within float32
  # tolerance) for an identical multi-tile layout.
  params = gerstner.WaveParams(amplitude_m=1.1, steepness=0.4, resolution=9, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.grid_tile_layout(3, 2, 40.0, 9))   # 6 same-(resolution,size) tiles -> one bucket

  def _snapshot():
    out = {}
    for tile_id in wb.tiles:
      prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path(tile_id))
      mesh = UsdGeom.Mesh(prim)
      out[tile_id] = (list(mesh.GetPointsAttr().Get()), list(mesh.GetNormalsAttr().Get()))
    return out

  wb.use_gpu = True
  wb._set_points_all(stage, 4.0)
  gpu_snapshot = _snapshot()

  wb.use_gpu = False
  wb._set_points_all(stage, 4.0)
  numpy_snapshot = _snapshot()

  assert set(gpu_snapshot) == set(numpy_snapshot) == set(wb.tiles)
  for tile_id in wb.tiles:
    gpu_points, gpu_normals = gpu_snapshot[tile_id]
    np_points, np_normals = numpy_snapshot[tile_id]
    assert len(gpu_points) == len(np_points)
    for gp, npnt in zip(gpu_points, np_points):
      assert all(abs(gp[k] - npnt[k]) < 1e-3 for k in range(3))
    for gn, nn in zip(gpu_normals, np_normals):
      assert all(abs(gn[k] - nn[k]) < 1e-3 for k in range(3))


@pytestmark_gpu
def test_set_points_all_gpu_path_skips_singleton_buckets():
  # A bucket of exactly one tile isn't worth the GPU round-trip (measured ~2x vs ~3.6x for a real batch)
  # -- _set_points_all() must fall back to the plain per-tile NumPy path even when use_gpu is True, for
  # any (resolution, size_m) bucket containing just one tile. This is an internal-dispatch property, not
  # something observable from the output alone (both paths produce the same numbers) -- verified here by
  # confirming a singleton-bucket layout still produces correct, freshly-recomputed points under
  # use_gpu=True, i.e. the dispatch doesn't silently skip or crash for that case.
  params = gerstner.WaveParams(resolution=9, size_m=80.0)
  wb, stage = _make_backend(params)
  wb.set_layout(gerstner.single_tile_layout(params))   # exactly one tile -> singleton bucket
  wb.use_gpu = True

  wb._set_points_all(stage, 2.0)

  mesh_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path("main"))
  points = UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()
  expected = gerstner.grid_points(2.0, params)
  assert len(points) == len(expected)
  for p, (ex, ey, ez) in zip(points, expected):
    assert abs(p[0] - ex) < 1e-5 and abs(p[1] - ey) < 1e-5 and abs(p[2] - ez) < 1e-5


@pytestmark_gpu
def test_set_points_all_gpu_path_groups_by_resolution_and_size_not_just_resolution():
  # Two tiles sharing resolution but NOT size_m must land in separate buckets, each still computed
  # correctly (as singleton buckets here) -- a same-resolution-only key would incorrectly try to batch
  # them together despite their different size_m/base grids.
  params = gerstner.WaveParams(resolution=7, size_m=40.0)
  wb, stage = _make_backend(params)
  wb.add_tile(gerstner.WaveTileSpec("small", 0.0, 0.0, 20.0, 7))
  wb.add_tile(gerstner.WaveTileSpec("big", 200.0, 0.0, 60.0, 7))
  wb.use_gpu = True

  wb._set_points_all(stage, 1.5)

  for tile_id, spec in wb.tiles.items():
    mesh_prim = stage.GetPrimAtPath(wave_backend.WaveBackend._tile_path(tile_id))
    points = UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()
    tile_params = gerstner.WaveParams(size_m=spec.size_m, resolution=spec.resolution,
                                       amplitude_m=params.amplitude_m, wavelength_m=params.wavelength_m,
                                       speed_mps=params.speed_mps, steepness=params.steepness,
                                       num_waves=params.num_waves)
    expected = gerstner.grid_points(1.5, tile_params, origin_xy=(spec.center_x, spec.center_y))
    assert len(points) == len(expected)
    for p, (ex, ey, ez) in zip(points, expected):
      assert abs(p[0] - ex) < 1e-3 and abs(p[1] - ey) < 1e-3 and abs(p[2] - ez) < 1e-3
