"""VesselsBackend._random_spawn_point()/_water_tiles() -- pure Python, no pxr touched (backends/
vessels.py only imports pxr/omni inside the methods that need them, same convention as backends/wave.py,
so this doesn't need the carb/omni stubbing ui/tile_placement.py's tests do)."""
import types

from maritime_scenario.backends import vessels as vessels_backend
from maritime_scenario.core.gerstner import WaveTileSpec


class FakeWaveBackend:
  def __init__(self, tiles):
    self._tiles = tiles

  def list_tiles(self):
    return dict(self._tiles)


class FakeManager:
  def __init__(self, wave_backend=None, area_m=200.0):
    self._wave_backend = wave_backend
    self.state_ref = types.SimpleNamespace(general=types.SimpleNamespace(area_m=area_m))

  def get_backend(self, name):
    return self._wave_backend if name == "wave" else None


class FakeContext:
  def __init__(self, manager):
    self.manager = manager


def _make(wave_backend=None, area_m=200.0):
  backend = vessels_backend.VesselsBackend()
  backend.context = FakeContext(FakeManager(wave_backend=wave_backend, area_m=area_m))
  return backend


def test_water_tiles_returns_empty_list_when_no_wave_backend():
  backend = _make(wave_backend=None)
  assert backend._water_tiles() == []


def test_water_tiles_returns_the_wave_backends_current_tiles():
  tile = WaveTileSpec("main", 10.0, 20.0, 40.0, 5)
  backend = _make(wave_backend=FakeWaveBackend({"main": tile}))
  assert backend._water_tiles() == [tile]


def test_random_spawn_point_falls_back_to_area_m_square_with_no_tiles():
  backend = _make(area_m=50.0)
  for _ in range(20):
    x, y = backend._random_spawn_point([])
    assert -50.0 <= x <= 50.0
    assert -50.0 <= y <= 50.0


def test_random_spawn_point_stays_within_the_chosen_tiles_footprint():
  tiles = [WaveTileSpec("a", 100.0, 100.0, 40.0, 5), WaveTileSpec("b", -500.0, -500.0, 20.0, 5)]
  backend = _make()
  for _ in range(100):
    x, y = backend._random_spawn_point(tiles)
    # must land inside SOME tile's footprint, not a fixed square unrelated to where tiles actually are
    inside_any = any(
      abs(x - t.center_x) <= t.size_m / 2.0 and abs(y - t.center_y) <= t.size_m / 2.0 for t in tiles)
    assert inside_any, f"({x}, {y}) landed outside every placed tile"


def test_random_spawn_point_weights_tile_choice_by_area_not_count():
  # a single big tile should be picked far more often than a single small one, roughly proportional to
  # area (size_m**2) -- not 50/50 just because there's one of each.
  big = WaveTileSpec("big", 0.0, 0.0, 200.0, 5)      # area 40000
  small = WaveTileSpec("small", 10000.0, 10000.0, 20.0, 5)   # area 400, far away so footprints never overlap
  backend = _make()
  big_hits = 0
  n = 2000
  for _ in range(n):
    x, y = backend._random_spawn_point([big, small])
    if abs(x - big.center_x) <= big.size_m / 2.0:
      big_hits += 1
  ratio = big_hits / n
  assert ratio > 0.9   # expected ~99%, generous margin for randomness
