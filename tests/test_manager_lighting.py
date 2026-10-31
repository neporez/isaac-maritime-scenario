"""ScenarioManager.ensure_default_light() against a REAL in-memory USD stage -- no omni/carb stubs
needed, runtime/manager.py only imports pxr inside the method itself."""
from pxr import Usd, UsdLux

from maritime_scenario.runtime.manager import ScenarioManager


def test_ensure_default_light_adds_a_light_to_an_empty_stage():
  stage = Usd.Stage.CreateInMemory()
  ScenarioManager.ensure_default_light(stage)
  light_prim = stage.GetPrimAtPath(ScenarioManager.DEFAULT_LIGHT_PATH)
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


def test_ensure_default_light_upgrades_a_stale_light_at_its_own_path():
  # Regression target #1 (found live, 2026-11-04): an earlier version of this function created a
  # DistantLight at DEFAULT_LIGHT_PATH -- a stage that already had THAT from a previous "Build scene"
  # never got upgraded to DomeLight, because the old light satisfied a "does any light exist anywhere"
  # check and the function returned before ever reaching the Define() call (see regression #2 below for
  # why that whole-stage check was removed entirely, not just patched for this one path).
  stage = Usd.Stage.CreateInMemory()
  UsdLux.DistantLight.Define(stage, ScenarioManager.DEFAULT_LIGHT_PATH)

  ScenarioManager.ensure_default_light(stage)

  light_prim = stage.GetPrimAtPath(ScenarioManager.DEFAULT_LIGHT_PATH)
  assert light_prim.GetTypeName() == "DomeLight"
  lights = [p for p in Usd.PrimRange(stage.GetPseudoRoot()) if p.HasAPI(UsdLux.LightAPI)]
  assert len(lights) == 1   # the stale DistantLight was replaced, not left behind alongside the new one


def test_ensure_default_light_still_creates_its_own_even_with_another_light_elsewhere():
  # Regression target #2 (found live, 2026-11-04): the previous version additionally scanned the WHOLE
  # stage and skipped if ANY light existed anywhere -- which meant Isaac Sim's own stock default stage
  # (an "Environment" Xform with its own built-in DistantLight, nothing to do with this extension) also
  # made this function skip, so "still showing DistantLight" persisted even after the path-upgrade fix
  # above. There's no reliable way to tell a throwaway template light apart from a deliberate one by
  # scanning the stage, so this function now only ever looks at its own well-known path -- a light
  # anywhere else, under any name, must not stop it from ensuring its own DomeLight too.
  stage = Usd.Stage.CreateInMemory()
  UsdLux.DistantLight.Define(stage, "/World/Environment/DefaultLight")   # Isaac Sim's own stock light

  ScenarioManager.ensure_default_light(stage)

  light_prim = stage.GetPrimAtPath(ScenarioManager.DEFAULT_LIGHT_PATH)
  assert light_prim.GetTypeName() == "DomeLight"
  # the other light is untouched, not removed -- this function only ever owns its own path
  assert stage.GetPrimAtPath("/World/Environment/DefaultLight").GetTypeName() == "DistantLight"
