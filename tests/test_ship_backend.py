"""ShipBackend.set_speed()/set_spawn_position() -- pure Python, no pxr touched (unlike build()/
_on_update(), which this project's standing caveat already covers: no live Isaac Sim available while
building this)."""
from maritime_scenario.backends import ship as ship_backend


def test_set_speed_updates_linear_and_angular_independently():
  backend = ship_backend.ShipBackend()
  assert backend._linear_fraction == ship_backend.DEFAULT_THRUST_FRACTION
  assert backend._angular_fraction == ship_backend.KEY_TURN_FRACTION

  backend.set_speed(linear=0.5)
  assert backend._linear_fraction == 0.5
  assert backend._angular_fraction == ship_backend.KEY_TURN_FRACTION   # untouched

  backend.set_speed(angular=0.02)
  assert backend._linear_fraction == 0.5   # untouched
  assert backend._angular_fraction == 0.02


def test_set_speed_with_no_args_changes_nothing():
  backend = ship_backend.ShipBackend()
  backend.set_speed()
  assert backend._linear_fraction == ship_backend.DEFAULT_THRUST_FRACTION
  assert backend._angular_fraction == ship_backend.KEY_TURN_FRACTION


def test_set_spawn_position_updates_xyz_independently():
  backend = ship_backend.ShipBackend()
  assert (backend._spawn_x, backend._spawn_y, backend._spawn_z) == (
    ship_backend.SPAWN_X, ship_backend.SPAWN_Y, ship_backend.DROP_HEIGHT_M)

  backend.set_spawn_position(x=100.0)
  assert (backend._spawn_x, backend._spawn_y, backend._spawn_z) == (
    100.0, ship_backend.SPAWN_Y, ship_backend.DROP_HEIGHT_M)

  backend.set_spawn_position(y=-50.0, z=3.0)
  assert (backend._spawn_x, backend._spawn_y, backend._spawn_z) == (100.0, -50.0, 3.0)


def test_set_spawn_position_with_no_args_changes_nothing():
  backend = ship_backend.ShipBackend()
  backend.set_spawn_position()
  assert (backend._spawn_x, backend._spawn_y, backend._spawn_z) == (
    ship_backend.SPAWN_X, ship_backend.SPAWN_Y, ship_backend.DROP_HEIGHT_M)
