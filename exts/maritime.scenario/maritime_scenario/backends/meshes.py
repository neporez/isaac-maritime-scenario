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


def measure(mesh_id):
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
  populated assets/models/ doesn't crash the picker, it just offers fewer choices (with a console
  warning so it's not silently empty)."""
  available = mesh_registry.list_available()
  if not available:
    import logging
    logging.getLogger("maritime_scenario").warning(
      "maritime_scenario: no mesh files found in %s -- see core/meshes.py's module docstring "
      "(this repo doesn't bundle any 3D models, you need to supply your own)", mesh_registry.WEB_MODEL_DIR)
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
