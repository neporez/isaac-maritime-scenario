"""ScenarioManager.ensure_default_light() against a REAL in-memory USD stage -- no omni/carb stubs
needed, runtime/manager.py only imports pxr inside the method itself."""
from pxr import Usd, UsdLux

from maritime_scenario.runtime.manager import ScenarioManager


def test_ensure_default_light_adds_a_light_to_an_empty_stage():
  stage = Usd.Stage.CreateInMemory()
  ScenarioManager.ensure_default_light(stage)
  light_prim = stage.GetPrimAtPath("/World/DefaultLight")
  assert light_prim
  assert light_prim.GetTypeName() == "DomeLight"
  assert light_prim.HasAPI(UsdLux.LightAPI)
  assert UsdLux.DomeLight(light_prim).GetIntensityAttr().Get() == 1000.0


def test_ensure_default_light_is_idempotent():
  stage = Usd.Stage.CreateInMemory()
  ScenarioManager.ensure_default_light(stage)
  ScenarioManager.ensure_default_light(stage)
  lights = [p for p in Usd.PrimRange(stage.GetPseudoRoot()) if p.HasAPI(UsdLux.LightAPI)]
  assert len(lights) == 1


def test_ensure_default_light_does_nothing_if_any_light_already_exists():
  stage = Usd.Stage.CreateInMemory()
  UsdLux.DistantLight.Define(stage, "/World/UserSun")
  ScenarioManager.ensure_default_light(stage)
  assert not stage.GetPrimAtPath("/World/DefaultLight")
  lights = [p for p in Usd.PrimRange(stage.GetPseudoRoot()) if p.HasAPI(UsdLux.LightAPI)]
  assert len(lights) == 1
  assert lights[0].GetPath() == "/World/UserSun"


def test_ensure_default_light_respects_an_unrelated_light_type_too():
  # Not just DomeLight specifically -- any concrete light type should count, since LightAPI is what's
  # actually checked, not a hardcoded list of type names.
  stage = Usd.Stage.CreateInMemory()
  UsdLux.SphereLight.Define(stage, "/World/SomeSphereLight")
  ScenarioManager.ensure_default_light(stage)
  assert not stage.GetPrimAtPath("/World/DefaultLight")


def test_ensure_default_light_upgrades_a_stale_light_at_its_own_path():
  # Regression target (found live, 2026-11-04): an earlier version of this function created a
  # DistantLight at DEFAULT_LIGHT_PATH -- a stage that already had THAT from a previous "Build scene"
  # never got upgraded to DomeLight, because the old light satisfied the "does any light exist" check and
  # the function returned before ever reaching the Define() call.
  stage = Usd.Stage.CreateInMemory()
  UsdLux.DistantLight.Define(stage, ScenarioManager.DEFAULT_LIGHT_PATH)

  ScenarioManager.ensure_default_light(stage)

  light_prim = stage.GetPrimAtPath(ScenarioManager.DEFAULT_LIGHT_PATH)
  assert light_prim.GetTypeName() == "DomeLight"
  lights = [p for p in Usd.PrimRange(stage.GetPseudoRoot()) if p.HasAPI(UsdLux.LightAPI)]
  assert len(lights) == 1   # the stale DistantLight was replaced, not left behind alongside the new one
