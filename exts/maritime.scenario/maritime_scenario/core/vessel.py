"""
Vessel entity data model -- pure data, no pxr. A Vessel is one placed boat: which mesh, where, how it
moves. Extracted from boat_mesh_pipeline/06_scenario_editor/other_boats.py's per-boat dict (the one
_spawn_one() built and _on_ob_update()/save_scenario() read), now a typed dataclass instead of a loose
dict with string keys.

Live simulation fields (params/prev_z/prev_roll/.../wp_index/path_done -- i.e. everything that changes
frame-to-frame once spawned) deliberately stay OUT of this dataclass: Vessel is the part worth saving/
loading/diffing (a scenario definition), the runtime layer tracks live per-vessel simulation state
alongside it (see runtime/manager.py), same split weather-fx's state/backend separation makes.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

BEHAVIORS = ["forward", "backward", "turn_left", "turn_right", "stationary", "follow_path"]

DEFAULT_LINEAR_SPEED = 0.3     # fraction of the vessel's own weight -> forward thrust
DEFAULT_ANGULAR_SPEED = 0.01   # fraction of the vessel's own max_torque -> turn torque


@dataclass
class Vessel:
  mesh_id: str
  x: float
  y: float
  yaw_deg: float
  behavior: str = "forward"
  waypoints: List[Tuple[float, float]] = field(default_factory=list)   # only meaningful for "follow_path"
  linear_speed: float = DEFAULT_LINEAR_SPEED
  angular_speed: float = DEFAULT_ANGULAR_SPEED
  name: Optional[str] = None   # stable id for this vessel instance, assigned by the runtime at spawn time

  def __post_init__(self):
    if self.behavior not in BEHAVIORS:
      raise ValueError(f"behavior must be one of {BEHAVIORS}, got {self.behavior!r}")
