"""
Scenario file I/O -- pure dict/JSON-shaped Python, no pxr, no filesystem access of its own (callers
pass already-loaded dicts in and get dicts to write out, so this stays testable without touching disk
or needing an event loop). One file holds the WHOLE scenario now: wave + ship physics + own ship's mesh
+ every placed vessel -- a deliberate improvement over boat_mesh_pipeline/06_scenario_editor's
save_scenario()/load_scenario(), which only ever saved the vessel list (wave/physics stayed whatever
the session's sliders happened to be at, not part of the file).

Also a deliberate schema change from that original: a saved vessel now stores just mesh_id (a stable
key into core/meshes.py), not the mesh's raw usd path/dx/dy/dz/scale/local_center -- those are
re-derived from the registry + the actual USD file at load time (see backends/meshes.py), so the
registry is the one source of truth for a mesh's geometry, not a cached copy baked into every saved
scenario file. This also means a scenario file written for this extension is NOT directly compatible
with 06_scenario_editor's old scenario1.json (different schema, and that one could reference this
project's own private "ours" meshes, which this extension doesn't carry at all).
"""
from dataclasses import asdict

from .state import ScenarioState, SCHEMA_VERSION
from .vessel import Vessel


def vessel_to_dict(vessel: Vessel):
  d = asdict(vessel)
  d["waypoints"] = [list(wp) for wp in d["waypoints"]]
  return d


def vessel_from_dict(data):
  return Vessel(
    mesh_id=data["mesh_id"],
    x=float(data["x"]),
    y=float(data["y"]),
    yaw_deg=float(data["yaw_deg"]),
    behavior=data.get("behavior", "forward"),
    waypoints=[tuple(wp) for wp in data.get("waypoints", [])],
    linear_speed=float(data.get("linear_speed", 0.3)),
    angular_speed=float(data.get("angular_speed", 0.01)),
    name=data.get("name"),
  )


def scenario_to_dict(state: ScenarioState, own_ship_mesh_id, vessels):
  return {
    "schema_version": SCHEMA_VERSION,
    "own_ship_mesh_id": own_ship_mesh_id,
    **state.to_dict(),
    "vessels": [vessel_to_dict(v) for v in vessels],
  }


def scenario_from_dict(data):
  """Returns (ScenarioState, own_ship_mesh_id, [Vessel, ...]). Lenient like weather-fx's own
  from_dict: an older or newer file (missing/extra sections, missing own_ship_mesh_id) still loads,
  with sensible fallbacks, rather than raising."""
  state = ScenarioState.from_dict(data)
  own_ship_mesh_id = data.get("own_ship_mesh_id")
  vessels = [vessel_from_dict(v) for v in data.get("vessels", [])]
  return state, own_ship_mesh_id, vessels
