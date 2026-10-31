"""Owns the ScenarioState and fans changes out to backends -- same role weather_fx.runtime.manager.
WeatherManager plays there, with one structural difference: this project's per-frame physics (ship
heave/roll/pitch, vessel thrust/steering) needs the REAL simulation dt tied to Isaac Sim's own timeline,
not wall-clock time (weather-fx's UpdateTicker deliberately uses time.perf_counter(), which is fine for
a visual effect but would desync physics from whatever rate PhysX is actually stepping at). So each
backend here subscribes to the app update stream ITSELF in attach() and reads dt off the event payload,
exactly like 06_scenario_editor's own _on_ob_update()/_on_float_update()/_on_wave_update() always did --
ScenarioManager does not run a central ticker, it only fans out apply_state() on change and routes the
vessel/own-ship/save-load calls to whichever backend owns that piece."""
import logging

from ..core.events import Signal
from ..core.scenario_io import scenario_from_dict, scenario_to_dict
from ..core.state import SECTION_TYPES, ScenarioState
from .context import ScenarioContext

log = logging.getLogger("maritime_scenario")


class ScenarioManager:
  def __init__(self):
    self._state = ScenarioState()
    self._backends = {}
    self.context = ScenarioContext(self)
    self.state_changed = Signal()   # (state, changed_sections)
    self.ready = False   # True once setup_async() has completed -- the UI gates vessel/ship controls on this

  # ------------------------------------------------------------ one-time async setup
  async def setup_async(self):
    """The real-world ordering main.py's _run() coroutine used in 06_scenario_editor: build the wave
    + salt_water material, await the save-as-stage+reopen restart (REQUIRED for salt_water to take
    effect, and which reopens the stage out from under anything built before it), THEN build() the
    ship and vessels backends fresh on that reopened stage. Registering a backend already calls its
    apply_state() once (see register_backend()); build() is the separate, stage-mutating half that
    has to happen on the post-restart stage specifically."""
    from ..backends.ship import ShipBackend
    from ..backends.vessels import VesselsBackend
    from ..backends.wave import WaveBackend

    if "wave" not in self._backends:
      self.register_backend(WaveBackend())
    await self.get_backend("wave").setup_async()

    if "ship" not in self._backends:
      self.register_backend(ShipBackend())
    if "vessels" not in self._backends:
      self.register_backend(VesselsBackend())
    self.get_backend("ship").build()
    self.get_backend("vessels").build()
    self.ready = True
    log.info("maritime_scenario: setup complete -- press Play")

  # ------------------------------------------------------------ state
  @property
  def state_ref(self):
    """Live state -- frozen, so safe to hand out directly (no accidental in-place mutation risk)."""
    return self._state

  def get_state(self):
    return self._state

  def set_state(self, new_state, force=False):
    changed = set(SECTION_TYPES) if force else self._state.diff(new_state)
    if not changed:
      return
    self._state = new_state
    for backend in self._backends.values():
      try:
        backend.apply_state(self._state, changed)
      except Exception:
        log.exception("maritime_scenario: backend %s failed to apply state", backend.name)
    self.state_changed.emit(self._state, changed)

  def update_sections(self, **sections):
    state = self._state
    for name, values in sections.items():
      state = state.with_updates(name, **values)
    self.set_state(state)

  # ------------------------------------------------------------ backends
  def register_backend(self, backend):
    if backend.name in self._backends:
      raise ValueError(f"backend {backend.name!r} already registered")
    backend.attach(self.context)
    self._backends[backend.name] = backend
    backend.apply_state(self._state, set(SECTION_TYPES))

  def unregister_backend(self, name):
    backend = self._backends.pop(name, None)
    if backend is not None:
      backend.detach()

  def get_backend(self, name):
    return self._backends.get(name)

  @property
  def backends(self):
    return dict(self._backends)

  # ------------------------------------------------------------ vessels (delegated to the "vessels" backend)
  def set_vessel_count(self, n):
    self._require_backend("vessels").set_count(n)

  def spawn_vessel(self, vessel):
    return self._require_backend("vessels").spawn(vessel)

  def list_vessels(self):
    return self._require_backend("vessels").list_vessels()

  def clear_vessels(self):
    self._require_backend("vessels").clear()

  def set_vessel_speed(self, linear=None, angular=None):
    self._require_backend("vessels").set_all_speed(linear=linear, angular=angular)

  # ------------------------------------------------------------ own ship (delegated to the "ship" backend)
  def set_own_ship_mesh(self, mesh_id):
    self._require_backend("ship").set_mesh(mesh_id)

  def get_own_ship_mesh_id(self):
    backend = self.get_backend("ship")
    return backend.mesh_id if backend is not None else None

  # ------------------------------------------------------------ save / load
  def save_scenario(self, path):
    vessels = self.list_vessels()
    data = scenario_to_dict(self._state, self.get_own_ship_mesh_id(), vessels)
    import json
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
      json.dump(data, f, indent=2)
    log.info("maritime_scenario: saved %d vessel(s) -> %s", len(vessels), path)

  def load_scenario(self, path):
    import json
    with open(path) as f:
      data = json.load(f)
    state, own_ship_mesh_id, vessels = scenario_from_dict(data)
    self.set_state(state, force=True)
    if own_ship_mesh_id is not None:
      self.set_own_ship_mesh(own_ship_mesh_id)
    self.clear_vessels()
    for vessel in vessels:
      self.spawn_vessel(vessel)
    log.info("maritime_scenario: loaded %d vessel(s) <- %s", len(vessels), path)

  # ------------------------------------------------------------ misc
  def stats(self):
    out = {}
    for name, backend in self._backends.items():
      try:
        out[name] = backend.stats()
      except Exception:
        out[name] = {"error": "stats failed"}
    return out

  def shutdown(self):
    for name in list(self._backends):
      self.unregister_backend(name)
    self.ready = False

  def _require_backend(self, name):
    backend = self.get_backend(name)
    if backend is None:
      raise RuntimeError(f"no {name!r} backend registered -- is the extension fully started?")
    return backend
