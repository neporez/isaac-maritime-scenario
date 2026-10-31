import pytest

from maritime_scenario.core.state import ScenarioState, GeneralParams
from maritime_scenario.core.gerstner import WaveParams
from maritime_scenario.core.ship_motion import ShipPhysicsCoeffs
from maritime_scenario.core.vessel import Vessel
from maritime_scenario.core import scenario_io


def test_with_updates_returns_new_state_leaves_original_untouched():
  s1 = ScenarioState()
  s2 = s1.with_updates("wave", amplitude_m=2.5)
  assert s1.wave.amplitude_m == 0.8   # unchanged -- frozen dataclass, s1 untouched
  assert s2.wave.amplitude_m == 2.5
  assert s2.ship_physics == s1.ship_physics   # other sections untouched by the update


def test_with_updates_unknown_section_raises():
  with pytest.raises(KeyError):
    ScenarioState().with_updates("not_a_real_section", x=1)


def test_with_updates_unknown_field_raises():
  # dataclasses.replace raises TypeError for an unknown field -- strict by default, same spirit as
  # weather-fx's with_updates being strict (core/state.py's own docstring)
  with pytest.raises(TypeError):
    ScenarioState().with_updates("wave", not_a_real_field=1)


def test_state_roundtrip_dict():
  s1 = ScenarioState(
    wave=WaveParams(amplitude_m=1.5, num_waves=3),
    ship_physics=ShipPhysicsCoeffs(draft_to_beam_ratio=0.2),
    general=GeneralParams(area_m=150.0),
  )
  d = s1.to_dict()
  s2 = ScenarioState.from_dict(d)
  assert s1 == s2


def test_state_from_dict_lenient_to_unknown_fields(capsys):
  d = {"wave": {"amplitude_m": 1.0, "not_a_real_field": 999}}
  state = ScenarioState.from_dict(d)
  assert state.wave.amplitude_m == 1.0   # known field still applied
  assert "not_a_real_field" in capsys.readouterr().out   # warned, not raised


def test_diff_detects_only_changed_sections():
  s1 = ScenarioState()
  s2 = s1.with_updates("wave", amplitude_m=5.0)
  assert s1.diff(s2) == {"wave"}
  assert s1.diff(s1) == set()
  s3 = s2.with_updates("general", area_m=99.0)
  assert s2.diff(s3) == {"general"}
  assert s1.diff(s3) == {"wave", "general"}


def test_state_from_dict_missing_sections_use_defaults():
  state = ScenarioState.from_dict({})
  assert state == ScenarioState()


def test_vessel_rejects_unknown_behavior():
  with pytest.raises(ValueError):
    Vessel(mesh_id="boat1", x=0, y=0, yaw_deg=0, behavior="fly")


def test_vessel_roundtrip_dict_with_waypoints():
  v1 = Vessel(mesh_id="boat3", x=1.5, y=-2.5, yaw_deg=90.0, behavior="follow_path",
              waypoints=[(10.0, 20.0), (30.0, 40.0)], linear_speed=0.4, angular_speed=0.02, name="boat_0")
  d = scenario_io.vessel_to_dict(v1)
  v2 = scenario_io.vessel_from_dict(d)
  assert v1 == v2


def test_full_scenario_roundtrip():
  state = ScenarioState(wave=WaveParams(amplitude_m=1.2))
  vessels = [
    Vessel(mesh_id="boat1", x=0, y=0, yaw_deg=0, behavior="forward"),
    Vessel(mesh_id="boat5", x=50, y=-30, yaw_deg=180, behavior="follow_path", waypoints=[(1, 2), (3, 4)]),
  ]
  d = scenario_io.scenario_to_dict(state, own_ship_mesh_id="boat2", vessels=vessels)
  assert d["schema_version"] == 1
  assert d["own_ship_mesh_id"] == "boat2"

  state2, own_ship_mesh_id2, vessels2 = scenario_io.scenario_from_dict(d)
  assert state2 == state
  assert own_ship_mesh_id2 == "boat2"
  assert vessels2 == vessels


def test_scenario_from_dict_missing_own_ship_mesh_id_is_none():
  _, own_ship_mesh_id, vessels = scenario_io.scenario_from_dict({"vessels": []})
  assert own_ship_mesh_id is None
  assert vessels == []
