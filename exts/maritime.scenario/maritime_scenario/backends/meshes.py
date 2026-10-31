"""The pxr-dependent half of mesh handling -- core.meshes.MeshEntry is pure data (usd path, scale,
rot_x); this module opens each one's actual USD file once via UsdGeom.BBoxCache to measure its real-
world (dx, dy, dz) and local_center, exactly as 06_scenario_editor's other_boats.py/float_mesh_test.py
did inline for their WEB_TARGET_OBJECTS pool (_ob_build_web_mesh_pool() / _float_web_pool()).

Two gotchas preserved verbatim from that port (CLAUDE.md, boat_mesh_pipeline):
  (1) USD reference composition ignores metersPerUnit, so these models' raw (~cm-ish) vertex units
      need exactly their hand-tuned `scale` constant, never a metadata-based correction.
  (2) Most of these models' local origin sits at their base, not their bbox center -- local_center is
      MEASURED (not assumed (0,0,0)), and placement must translate by -scale*local_center so the
      mesh's own bbox center lands at the body's origin regardless of where its native origin sits.
"""
from dataclasses import dataclass

from ..core import meshes as mesh_registry


@dataclass(frozen=True)
class MeasuredMesh:
  mesh_id: str
  usd_path: str
  scale: float
  rot_x_deg: float
  dx: float
  dy: float
  dz: float
  local_center: tuple   # (x, y, z), native (pre-scale) units


_cache = {}   # mesh_id -> MeasuredMesh, measured once per process (USD files don't change at runtime)

FALLBACK_MESH_ID = "__default_box__"
FALLBACK_DX, FALLBACK_DY, FALLBACK_DZ = 8.0, 2.5, 1.8   # generic small-boat-ish proportions (length > beam > height)


def fallback_measured():
  """A synthetic elongated-box stand-in, used when no real boat assets are registered at all (mesh
  folder unset/empty) -- see build_visual()'s own docstring for why this exists. No real usd_path (the
  empty string is the signal build_visual()/the UI check for), no pxr touched here at all."""
  return MeasuredMesh(mesh_id=FALLBACK_MESH_ID, usd_path="", scale=1.0, rot_x_deg=0.0,
                       dx=FALLBACK_DX, dy=FALLBACK_DY, dz=FALLBACK_DZ, local_center=(0.0, 0.0, 0.0))


def clear_cache():
  """Measured meshes are cached by mesh_id assuming the underlying file never changes mid-process --
  true unless the mesh FOLDER itself changes (ScenarioController.set_mesh_dir(), 2026-10-31), which
  reuses the same mesh_ids against different paths. Called from there, not needed otherwise."""
  _cache.clear()


def measure(mesh_id):
  if mesh_id == FALLBACK_MESH_ID:
    return fallback_measured()
  if mesh_id in _cache:
    return _cache[mesh_id]
  import os
  from pxr import Usd, UsdGeom

  entry = mesh_registry.get(mesh_id)
  if not os.path.exists(entry.usd_path):
    raise FileNotFoundError(
      f"mesh {mesh_id!r} not found at {entry.usd_path!r} -- this repo doesn't bundle any 3D models "
      f"(see core/meshes.py's module docstring). Either supply your own .usd there, or point "
      f"MARITIME_SCENARIO_MESH_DIR at wherever you keep them."
    )
  cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], useExtentsHint=False)
  ref_stage = Usd.Stage.Open(entry.usd_path)
  rng = cache.ComputeWorldBound(ref_stage.GetPseudoRoot()).ComputeAlignedRange()
  lo, hi = rng.GetMin(), rng.GetMax()
  local_center = ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, (lo[2] + hi[2]) / 2.0)
  measured = MeasuredMesh(
    mesh_id=mesh_id, usd_path=entry.usd_path, scale=entry.scale, rot_x_deg=entry.rot_x_deg,
    dx=(hi[0] - lo[0]) * entry.scale, dy=(hi[1] - lo[1]) * entry.scale, dz=(hi[2] - lo[2]) * entry.scale,
    local_center=local_center,
  )
  _cache[mesh_id] = measured
  return measured


def list_all():
  """Every mesh whose .usd file actually exists right now, measured -- the UI's mesh picker source.
  Uses core.meshes.list_available() rather than the full registry, so an unpopulated/partially-
  populated assets/models/ doesn't crash the picker. NEVER returns empty (2026-10-31): falls back to
  [fallback_measured()] alone when no real assets are registered, so the UI always has SOMETHING to
  spawn -- a visible box instead of a disabled picker or a silent no-op -- see build_visual()."""
  available = mesh_registry.list_available()
  if not available:
    import logging
    logging.getLogger("maritime_scenario").warning(
      "maritime_scenario: no mesh files found in %s -- see core/meshes.py's module docstring "
      "(this repo doesn't bundle any 3D models, you need to supply your own). Using a placeholder box "
      "mesh for now.", mesh_registry.WEB_MODEL_DIR)
    return [fallback_measured()]
  return [measure(m.mesh_id) for m in available]


def strip_bad_normals(root_prim):
  """Some web-sourced meshes have malformed normals data Isaac Sim's RTX Hydra delegate segfaults on
  (confirmed in 06_scenario_editor, 2026-10-21: boat7.usd's .../Object_7/Object_5 submesh authors 8662
  vertex-interpolated normals matching its point count exactly -- a legitimate USD mesh -- but Hydra's
  delegate expected 30282 (facevarying, matching faceVertexIndices) and crashed the whole app instead
  of upsampling or erroring gracefully, a renderer-level crash no Python try/except can catch. Block()ing
  (not Clear(), which wouldn't reach an opinion authored in a weaker/referenced layer) the normals
  attribute on every Mesh under the reference makes Hydra compute normals from topology instead,
  sidestepping the mismatch entirely rather than trying to fix the upstream asset's data."""
  from pxr import Usd, UsdGeom

  for prim in Usd.PrimRange(root_prim):
    if prim.IsA(UsdGeom.Mesh):
      UsdGeom.Mesh(prim).GetNormalsAttr().Block()


def set_visual_xform(prim, measured: MeasuredMesh):
  """visual_prim's LOCAL transform (relative to its body_prim parent, which already carries the world
  position/yaw): translate-then-scale-then-rotateX places the mesh's own bbox center at the body's
  origin regardless of where the mesh's native origin sits."""
  from pxr import Gf, UsdGeom

  lcx, lcy, lcz = measured.local_center
  xform = UsdGeom.Xformable(prim)
  xform.ClearXformOpOrder()
  xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(
    Gf.Vec3d(-measured.scale * lcx, -measured.scale * lcy, -measured.scale * lcz))
  xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
    Gf.Vec3d(measured.scale, measured.scale, measured.scale))
  if measured.rot_x_deg != 0.0:
    xform.AddRotateXOp(UsdGeom.XformOp.PrecisionFloat, opSuffix="unitsResolve").Set(float(measured.rot_x_deg))


def build_visual(stage, path, measured: MeasuredMesh):
  """Builds the actual visible geometry for one spawned body (own ship or a vessel) at `path` -- a real
  mesh reference for a registered asset, or a plain elongated box when no real boat assets are set up
  yet (measured.usd_path == "", i.e. measured came from fallback_measured()), so "Apply to own ship" and
  vessel spawning always produce something visible and physically reasonable instead of failing or
  spawning nothing (2026-10-31, user's own call: don't block on art assets to test the simulation).
  Shared by ship.py's set_mesh() and vessels.py's _spawn_prim() -- both previously duplicated the same
  three-line reference+strip+xform sequence inline; this also meant the fallback case only had to be
  written once. DefinePrim(path, ...) re-declares the type every call, so switching a body between a
  real mesh and the fallback box (or between two real meshes) always starts from a clean prim type."""
  from pxr import Gf, UsdGeom

  if not measured.usd_path:
    cube_prim = stage.DefinePrim(path, "Cube")
    cube = UsdGeom.Cube(cube_prim)
    cube.CreateSizeAttr(1.0)   # unit cube (-0.5..0.5 per axis) -- scaled to real half-size below
    cube.CreateDisplayColorAttr([Gf.Vec3f(0.55, 0.35, 0.15)])   # hull-brown, visually a placeholder
    xform = UsdGeom.Xformable(cube_prim)
    xform.ClearXformOpOrder()
    xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
      Gf.Vec3d(measured.dx, measured.dy, measured.dz))
    return cube_prim

  visual_prim = stage.DefinePrim(path, "Xform")
  visual_prim.GetReferences().ClearReferences()
  visual_prim.GetReferences().AddReference(measured.usd_path)
  strip_bad_normals(visual_prim)
  set_visual_xform(visual_prim, measured)
  return visual_prim
