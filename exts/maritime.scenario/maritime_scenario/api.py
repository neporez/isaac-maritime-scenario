"""Public Python API. The UI panel uses exactly the same controller.

Example (Script Editor or standalone script)::

    from maritime_scenario import api as scenario

    sc = scenario.get_controller()
    await sc.setup_async()                 # builds the wave infrastructure + empty vessel pool -- once;
                                            # does NOT spawn an own ship by itself
    sc.set_wave(amplitude_m=1.2)
    sc.set_own_ship_mesh("boat3")          # spawns the own ship (also how you swap its mesh later)
    sc.spawn_vessel("boat1", x=50, y=20, behavior="forward")
    sc.set_vessel_count(10)                # random swarm, replaces whatever's there
"""
from typing import Optional

from .runtime.manager import ScenarioManager

_controller: Optional["ScenarioController"] = None


def get_controller() -> "ScenarioController":
  """Return the shared controller, creating it on first use."""
  global _controller
  if _controller is None:
    _controller = ScenarioController()
  return _controller


def shutdown_controller() -> None:
  global _controller
  if _controller is not None:
    _controller.shutdown()
    _controller = None


class ScenarioController:
  def __init__(self):
    self._manager = ScenarioManager()

  # ------------------------------------------------------------ state
  @property
  def state(self):
    return self._manager.get_state()

  @property
  def ready(self) -> bool:
    """True once setup_async() has completed -- vessel/ship calls need this first."""
    return self._manager.ready

  def configure(self, **sections) -> None:
    """Change several sections in one update: configure(wave={...}, ship_physics={...})."""
    self._manager.update_sections(**sections)

  def set_wave(self, **values) -> None:
    self.configure(wave=values)

  def set_ship_physics(self, **values) -> None:
    self.configure(ship_physics=values)

  def set_general(self, **values) -> None:
    self.configure(general=values)

  async def setup_async(self) -> None:
    """Build the wave + salt_water material (with the required stage restart), then the own ship and
    vessel pool. Call this once per session, before anything else below. See
    runtime/manager.py's ScenarioManager.setup_async() for the full ordering/why."""
    await self._manager.setup_async()

  # ------------------------------------------------------------ own ship
  def set_own_ship_mesh(self, mesh_id: str) -> bool:
    """Returns True if mesh_id resolved to the fallback placeholder box (no real boat assets registered
    under the current mesh folder) rather than a real asset."""
    return self._manager.set_own_ship_mesh(mesh_id)

  @property
  def own_ship_mesh_id(self):
    return self._manager.get_own_ship_mesh_id()

  def ship_control(self, name: str, pressed: bool) -> None:
    """name: "forward"/"back"/"left"/"right" -- same as a held I/J/K/L key."""
    backend = self._manager.get_backend("ship")
    if backend is not None:
      backend.set_control(name, pressed)

  def ship_thrust(self, newtons: float) -> None:
    backend = self._manager.get_backend("ship")
    if backend is not None:
      backend.set_thrust(newtons)

  def set_ship_speed(self, linear=None, angular=None) -> None:
    """Overrides the own ship's key-held forward/back thrust fraction and/or left/right turn-torque
    fraction -- the same convention as spawn_vessel()'s linear_speed/angular_speed, just for the own
    ship (which has exactly one instance, not spawned per-call)."""
    self._manager.set_ship_speed(linear=linear, angular=angular)

  def set_ship_spawn_position(self, x=None, y=None, z=None) -> None:
    """Overrides where set_own_ship_mesh()/ship_reset() (re)spawn the own ship -- x/y are world-space
    meters, z is meters above the ship's own settled equilibrium depth (not an absolute world Z), same
    as the original fixed drop height. Takes effect on the NEXT spawn/reset, not live on a standing ship.
    Any arg can be omitted to leave that one unchanged."""
    self._manager.set_ship_spawn_position(x=x, y=y, z=z)

  def ship_reset(self) -> None:
    backend = self._manager.get_backend("ship")
    if backend is not None:
      backend.reset()

  # ------------------------------------------------------------ vessels
  def set_vessel_count(self, n: int) -> None:
    """Random swarm of n vessels, replacing whatever's currently in the scene."""
    self._manager.set_vessel_count(n)

  def spawn_vessel(self, mesh_id, x, y, yaw_deg=None, behavior="forward", waypoints=None,
                    linear_speed=None, angular_speed=None):
    """Adds exactly one vessel on top of whatever's already there. yaw_deg=None with waypoints given
    faces the first waypoint automatically."""
    from .core.vessel import DEFAULT_ANGULAR_SPEED, DEFAULT_LINEAR_SPEED, Vessel

    vessel = Vessel(
      mesh_id=mesh_id, x=float(x), y=float(y), yaw_deg=float(yaw_deg) if yaw_deg is not None else 0.0,
      behavior=behavior, waypoints=list(waypoints) if waypoints else [],
      linear_speed=float(linear_speed) if linear_speed is not None else DEFAULT_LINEAR_SPEED,
      angular_speed=float(angular_speed) if angular_speed is not None else DEFAULT_ANGULAR_SPEED,
    )
    if yaw_deg is None and waypoints:
      vessel.yaw_deg = None   # let the vessels backend compute it facing the first waypoint
    return self._manager.spawn_vessel(vessel)

  def list_vessels(self):
    return self._manager.list_vessels()

  def clear_vessels(self) -> None:
    self._manager.clear_vessels()

  def set_vessel_area_boundary(self, enabled: bool) -> None:
    """When enabled (the default), a vessel reaching +/- general.area_m in x or y is held there -- an
    invisible wall, not a teleport (renamed from set_vessel_wraparound(), 2026-11-02: it used to
    teleport a vessel to the opposite edge instead)."""
    backend = self._manager.get_backend("vessels")
    if backend is not None:
      backend.set_boundary_enabled(enabled)

  def set_vessel_speed(self, linear=None, angular=None) -> None:
    """Bulk-overwrites every CURRENT vessel's speed -- for spawn-time-only per-vessel control, pass
    linear_speed/angular_speed to spawn_vessel() instead."""
    self._manager.set_vessel_speed(linear=linear, angular=angular)

  # ------------------------------------------------------------ meshes
  def list_mesh_choices(self):
    """Every registered web mesh, measured (mesh_id, usd_path, scale, dx, dy, dz, local_center) -- the
    UI's mesh picker source. Never empty: falls back to a single synthetic placeholder-box entry (empty
    usd_path) when no real assets are registered under the current mesh folder."""
    from .backends import meshes as mesh_backend
    return mesh_backend.list_all()

  def set_mesh_dir(self, path: str) -> None:
    """Points boat asset loading at `path` instead of the default assets/models folder (or whatever
    MARITIME_SCENARIO_MESH_DIR was set to at startup) -- the UI's "Choose folder..." button calls this.
    Takes effect for the NEXT set_own_ship_mesh()/spawn_vessel() call; anything already spawned keeps
    its current mesh until re-applied."""
    self._manager.set_mesh_dir(path)

  def get_mesh_dir(self) -> str:
    return self._manager.get_mesh_dir()

  def wave_height_at(self, x, y) -> float:
    backend = self._manager.get_backend("wave")
    return backend.height_at(x, y) if backend is not None else 0.0

  # ------------------------------------------------------------ wave tiles
  def set_wave_single_plane(self) -> None:
    """Replaces the CURRENT tile layout with the classic single NxN wave plane (today's long-standing
    default, now an explicit opt-in rather than automatic on setup_async() -- see WaveBackend.
    setup_async()'s own docstring, 2026-10-30), sized from the current wave state's Size/Resolution
    sliders. Wipes out any tiles placed via add_water_tile()/the mouse, same "set_layout() replaces
    everything" semantics as clear_water_tiles()."""
    backend = self._manager.get_backend("wave")
    if backend is None:
      return
    from .core.gerstner import single_tile_layout
    backend.set_layout(single_tile_layout(self.state.wave))

  def set_wave_active_radius(self, radius_m) -> None:
    """Tiles farther than this from the own ship stop being re-animated every frame (distance-based LOD,
    2026-10-30 -- see WaveBackend.update_tile_activity()'s own docstring for what this actually saves)."""
    backend = self._manager.get_backend("wave")
    if backend is not None:
      backend.set_active_radius(radius_m)

  def add_water_tile(self, center_x, center_y, size_m=None, resolution=None, tile_id=None):
    """Adds ONE water tile on top of whatever's already in the layout. size_m/resolution default to
    the current wave state's own values (what the default single-tile layout uses) if not given.
    Returns the WaveTileSpec that was added, or None if the wave backend isn't built yet."""
    backend = self._manager.get_backend("wave")
    if backend is None:
      return None
    from .core.gerstner import WaveTileSpec

    wave_params = self.state.wave
    spec = WaveTileSpec(
      tile_id=tile_id or backend.generate_tile_id(),
      center_x=float(center_x), center_y=float(center_y),
      size_m=float(size_m) if size_m is not None else wave_params.size_m,
      resolution=int(resolution) if resolution is not None else wave_params.resolution,
    )
    backend.add_tile(spec)
    return spec

  def remove_water_tile(self, tile_id) -> None:
    backend = self._manager.get_backend("wave")
    if backend is not None:
      backend.remove_tile(tile_id)

  def list_water_tiles(self):
    backend = self._manager.get_backend("wave")
    return backend.list_tiles() if backend is not None else {}

  def clear_water_tiles(self) -> None:
    """Removes every tile, including the default "main" one -- same semantics as the vessel section's
    Clear button."""
    backend = self._manager.get_backend("wave")
    if backend is not None:
      backend.set_layout([])

  # ------------------------------------------------------------ save / load
  def save(self, path: str) -> None:
    self._manager.save_scenario(path)

  def load(self, path: str) -> None:
    self._manager.load_scenario(path)

  # ------------------------------------------------------------ events, info
  def on_change(self, fn):
    """fn(state, changed_sections) is called after every wave/ship_physics/general change."""
    return self._manager.state_changed.connect(fn)

  def remove_on_change(self, token) -> None:
    self._manager.state_changed.disconnect(token)

  def stats(self) -> dict:
    return self._manager.stats()

  @property
  def manager(self) -> ScenarioManager:
    return self._manager

  def shutdown(self) -> None:
    self._manager.shutdown()
