"""Interface every backend implements -- same four-method lifecycle weather-fx's SensorBackend uses
(attach/apply_state/update/detach), simplified since maritime.scenario only ever has the three backends below
(wave/ship/vessels), no nested per-backend "effects" the way weather-fx's viewport backend has."""
from abc import ABC, abstractmethod


class Backend(ABC):
  name = "base"

  def attach(self, context):
    """Called once when registered. context is a runtime.context.ScenarioContext."""
    self.context = context

  @abstractmethod
  def apply_state(self, state, changed):
    """React to a state change. changed is a set of modified ScenarioState section names."""

  def update(self, dt, t):
    """Called every tick while the manager's auto-update ticker is running."""

  def build(self):
    """Create this backend's own prims on the CURRENT stage. Separate from attach() because the wave
    backend's one-time salt_water+restart dance (see wave.py) reopens the stage out from under
    anything built before it -- ship/vessels must build() AFTER that restart, not at attach() time.
    See runtime/manager.py's setup_async() for the actual ordering. A no-op by default (the wave
    backend doesn't need it -- its own setup_async() both builds and does the restart)."""

  def detach(self):
    """Undo every change this backend made (prims, subscriptions)."""

  def stats(self):
    return {}
