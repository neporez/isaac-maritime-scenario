"""backends/meshes.py's fallback-box path (2026-10-31) against a REAL in-memory USD stage -- no omni/
isaacsim stubs needed, same pattern as test_wave_backend_tiles.py (only pxr is actually touched)."""
from pxr import Usd, UsdGeom

from maritime_scenario.backends import meshes as mesh_backend


def test_fallback_measured_has_no_usd_path():
  measured = mesh_backend.fallback_measured()
  assert measured.usd_path == ""
  assert measured.mesh_id == mesh_backend.FALLBACK_MESH_ID
  assert measured.dx > measured.dy > measured.dz > 0   # length > beam > height, boat-ish proportions


def test_measure_returns_fallback_for_fallback_mesh_id():
  measured = mesh_backend.measure(mesh_backend.FALLBACK_MESH_ID)
  assert measured == mesh_backend.fallback_measured()


def test_build_visual_fallback_creates_a_scaled_cube():
  stage = Usd.Stage.CreateInMemory()
  measured = mesh_backend.fallback_measured()
  prim = mesh_backend.build_visual(stage, "/World/visual", measured)

  assert prim.GetTypeName() == "Cube"
  scale_ops = [op for op in UsdGeom.Xformable(prim).GetOrderedXformOps()
               if op.GetOpType() == UsdGeom.XformOp.TypeScale]
  assert len(scale_ops) == 1
  sx, sy, sz = scale_ops[0].Get()
  assert (sx, sy, sz) == (measured.dx, measured.dy, measured.dz)


def test_build_visual_switches_cleanly_from_fallback_to_a_reference(tmp_path):
  # a body going fallback -> real mesh (or vice versa) must end up with the type/ops matching whichever
  # was built LAST -- DefinePrim(path, type) re-declaring the type each call is what guarantees this.
  real_usd_path = str(tmp_path / "boat1.usd")
  real_stage = Usd.Stage.CreateNew(real_usd_path)
  real_stage.GetRootLayer().Save()
  del real_stage

  stage = Usd.Stage.CreateInMemory()
  mesh_backend.build_visual(stage, "/World/visual", mesh_backend.fallback_measured())
  assert stage.GetPrimAtPath("/World/visual").GetTypeName() == "Cube"

  real_measured = mesh_backend.MeasuredMesh(
    mesh_id="boat1", usd_path=real_usd_path, scale=1.0, rot_x_deg=0.0,
    dx=5.0, dy=2.0, dz=1.5, local_center=(0.0, 0.0, 0.0),
  )
  mesh_backend.build_visual(stage, "/World/visual", real_measured)
  prim = stage.GetPrimAtPath("/World/visual")
  assert prim.GetTypeName() == "Xform"
  assert prim.HasAuthoredReferences()


def test_clear_cache_empties_the_measured_mesh_cache():
  mesh_backend._cache["some_id"] = object()
  mesh_backend.clear_cache()
  assert mesh_backend._cache == {}


def test_list_all_falls_back_to_a_single_placeholder_when_no_real_assets(monkeypatch):
  from maritime_scenario.core import meshes as mesh_registry
  monkeypatch.setattr(mesh_registry, "list_available", lambda: [])
  choices = mesh_backend.list_all()
  assert len(choices) == 1
  assert choices[0].mesh_id == mesh_backend.FALLBACK_MESH_ID
