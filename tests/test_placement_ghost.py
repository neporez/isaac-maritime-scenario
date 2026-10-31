"""PlacementController._ensure_ghost() -- the mouse-placement preview must show SOMETHING even for the
fallback placeholder box (no real assets registered), not just for a real referenced mesh. ui/placement.py
imports carb.input/omni.appwindow/omni.kit.app/omni.usd at module level (same situation as
ui/tile_placement.py), so those need stubbing before import -- omni.usd's stub additionally needs a
working get_context().get_stage() returning a real in-memory stage, since _ensure_ghost() actually
builds prims on it (unlike tile_placement.py's pure grid-math methods, which never touched a stage)."""
import sys
import types

from pxr import Usd, UsdGeom


def _stub(name):
  if name not in sys.modules:
    sys.modules[name] = types.ModuleType(name)
  return sys.modules[name]


_carb = _stub("carb")
_carb.input = _stub("carb.input")
_omni = _stub("omni")
_omni.kit = _stub("omni.kit")
_omni.kit.app = _stub("omni.kit.app")
_omni.appwindow = _stub("omni.appwindow")

_STAGE = Usd.Stage.CreateInMemory()


class _FakeUsdContext:
  def get_stage(self):
    return _STAGE


_omni.usd = _stub("omni.usd")
_omni.usd.get_context = lambda: _FakeUsdContext()

from maritime_scenario.ui import placement
from maritime_scenario.backends import meshes as mesh_backend


def _make():
  return placement.PlacementController(controller=None)


def test_ensure_ghost_fallback_creates_a_visible_cube():
  # Regression target (2026-11-03, found live): _ensure_ghost() used to only ever
  # visual.GetReferences().AddReference(measured.usd_path), which is "" for the fallback box -- adding an
  # empty reference left the ghost's visual prim with no geometry at all, so "Place with mouse" showed no
  # preview while no real boat assets were registered.
  pc = _make()
  measured = mesh_backend.fallback_measured()
  pc._ensure_ghost(measured)

  visual = _STAGE.GetPrimAtPath(placement.GHOST_PATH + "/visual")
  assert visual.GetTypeName() == "Cube"
  scale_ops = [op for op in UsdGeom.Xformable(visual).GetOrderedXformOps()
               if op.GetOpType() == UsdGeom.XformOp.TypeScale]
  assert len(scale_ops) == 1
  assert tuple(scale_ops[0].Get()) == (measured.dx, measured.dy, measured.dz)


def test_ensure_ghost_fallback_still_gets_the_translucent_ghost_material():
  pc = _make()
  pc._ensure_ghost(mesh_backend.fallback_measured())
  visual = _STAGE.GetPrimAtPath(placement.GHOST_PATH + "/visual")
  from pxr import UsdShade
  bound = UsdShade.MaterialBindingAPI(visual).ComputeBoundMaterial()[0]
  assert bound.GetPath() == placement.GHOST_MATERIAL_PATH


def test_ensure_ghost_switches_cleanly_from_fallback_to_a_real_reference(tmp_path):
  real_usd_path = str(tmp_path / "boat1.usd")
  real_stage = Usd.Stage.CreateNew(real_usd_path)
  real_stage.GetRootLayer().Save()
  del real_stage

  pc = _make()
  pc._ensure_ghost(mesh_backend.fallback_measured())
  assert _STAGE.GetPrimAtPath(placement.GHOST_PATH + "/visual").GetTypeName() == "Cube"

  real_measured = mesh_backend.MeasuredMesh(
    mesh_id="boat1", usd_path=real_usd_path, scale=1.0, rot_x_deg=0.0,
    dx=5.0, dy=2.0, dz=1.5, local_center=(0.0, 0.0, 0.0),
  )
  pc._ensure_ghost(real_measured)
  visual = _STAGE.GetPrimAtPath(placement.GHOST_PATH + "/visual")
  assert visual.GetTypeName() == "Xform"
  assert visual.HasAuthoredReferences()
