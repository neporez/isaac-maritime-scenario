"""
ScenarioState -- the single source of truth for the CONTINUOUS, globally-shared settings (wave, ship
physics coefficients, a couple of general knobs), same role weather_fx.core.state.WeatherState plays
there. Deliberately does NOT hold the vessel list: vessels are discrete entities spawned/despawned by
explicit actions, not a single diffable value the way a fog density is -- see core/vessel.py's
docstring and runtime/manager.py for that split.

with_updates(section, **kwargs) returns a NEW ScenarioState (frozen dataclasses throughout) with that
one section replaced -- mirrors weather-fx's immutable-update pattern closely enough to keep the same
mental model, without pulling in its full param()-metadata/UI-generation machinery (this project's UI
layer wires sliders by hand, same as 06_scenario_editor's scenario_ui.py already did).
"""
from dataclasses import dataclass, field, replace

from .gerstner import WaveParams
from .ship_motion import ShipPhysicsCoeffs

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GeneralParams:
  area_m: float = 200.0   # spawn/wrap half-extent -- matches the wave's own size/2 by convention


SECTION_TYPES = {
  "wave": WaveParams,
  "ship_physics": ShipPhysicsCoeffs,
  "general": GeneralParams,
}


@dataclass(frozen=True)
class ScenarioState:
  wave: WaveParams = field(default_factory=WaveParams)
  ship_physics: ShipPhysicsCoeffs = field(default_factory=ShipPhysicsCoeffs)
  general: GeneralParams = field(default_factory=GeneralParams)

  def with_updates(self, section, **kwargs):
    if section not in SECTION_TYPES:
      raise KeyError(f"unknown state section {section!r}, must be one of {list(SECTION_TYPES)}")
    current = getattr(self, section)
    updated = replace(current, **kwargs)
    return replace(self, **{section: updated})

  def diff(self, other):
    """Names of sections whose values differ between self and other -- frozen dataclasses compare by
    value, so this is just an equality check per section, no deepcopy/asdict needed (unlike
    weather-fx's mutable-dataclass version of the same method)."""
    return {name for name in SECTION_TYPES if getattr(self, name) != getattr(other, name)}

  def to_dict(self):
    return {
      "wave": vars(self.wave).copy(),
      "ship_physics": vars(self.ship_physics).copy(),
      "general": vars(self.general).copy(),
    }

  @classmethod
  def from_dict(cls, data):
    kwargs = {}
    for section, section_type in SECTION_TYPES.items():
      section_data = data.get(section)
      if section_data is None:
        continue
      known_fields = {f for f in section_type.__dataclass_fields__}
      filtered = {k: v for k, v in section_data.items() if k in known_fields}
      unknown = set(section_data) - known_fields
      if unknown:
        print(f"[maritime_scenario] from_dict: ignoring unknown {section!r} field(s) {sorted(unknown)}")
      kwargs[section] = section_type(**filtered)
    return cls(**kwargs)
