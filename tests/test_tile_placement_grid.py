"""TilePlacementController's grid math (_cell_index/_cell_center/_cell_center_at_level) -- pure Python,
no pxr/omni touched by these specific methods. Unlike backends/wave.py (which deliberately imports
pxr/omni only inside the methods that need them, specifically so it stays testable without a real Isaac
Sim), ui/tile_placement.py imports carb.input/omni.appwindow/omni.kit.app/omni.usd at MODULE level (same
as ui/placement.py) -- neither carb nor omni exist in this environment, so those imports must be stubbed
out before the module can even be imported, purely to reach the pure-math methods below. This is why
this project's own CLAUDE.md only ever listed this module's grid math as covered by an ad-hoc scratch
script, never a committed pytest, until now."""
import sys
import types


def _stub(name):
  if name not in sys.modules:
    sys.modules[name] = types.ModuleType(name)
  return sys.modules[name]


_carb = _stub("carb")
_carb.input = _stub("carb.input")
_omni = _stub("omni")
_omni.kit = _stub("omni.kit")
_omni.kit.app = _stub("omni.kit.app")
_omni.appwindow = _stub("omni.appwindow")
_omni.usd = _stub("omni.usd")

from maritime_scenario.ui import tile_placement


def _make():
  return tile_placement.TilePlacementController(controller=None)


def test_origin_falls_strictly_inside_one_cell_at_every_level():
  # Regression target (2026-11-01, found live): before any shift, the world origin -- where the own ship
  # always spawns -- sat exactly on the corner shared by 4 cells at every level, so placing water there
  # always required 4 separate clicks just to cover the ship's spawn point.
  tp = _make()
  for level in range(tile_placement.MAX_TILE_LEVEL + 1):
    tp.tile_level = level
    i, j = tp._cell_index(0.0, 0.0)
    size = tile_placement.size_for_level(level)
    cx, cy = tp._cell_center(i, j)
    # origin must be strictly inside [cx - size/2, cx + size/2) x [cy - size/2, cy + size/2)
    assert cx - size / 2.0 <= 0.0 < cx + size / 2.0
    assert cy - size / 2.0 <= 0.0 < cy + size / 2.0


def test_origin_is_exactly_centered_at_the_smallest_level():
  tp = _make()
  tp.tile_level = 0
  i, j = tp._cell_index(0.0, 0.0)
  cx, cy = tp._cell_center(i, j)
  assert (cx, cy) == (0.0, 0.0)


def test_origin_never_more_than_halfway_to_an_edge_at_any_level():
  # Regression target (2026-11-02, found live): a single constant shift (the first pass at this problem)
  # exactly centers level 0 but does nothing for bigger levels -- by level 6 (2048m) the origin sat at
  # 98% of the way to a corner, which still visually reads as "basically the corner" even though it's
  # technically inside the cell. LEVEL_SHIFTS_M's per-level greedy picks guarantee this ratio never
  # exceeds 50% at any level (the mathematically-proven best possible given the nesting constraint --
  # see the module docstring's own derivation for why 0% at every level is impossible).
  tp = _make()
  for level in range(tile_placement.MAX_TILE_LEVEL + 1):
    tp.tile_level = level
    size = tile_placement.size_for_level(level)
    i, j = tp._cell_index(0.0, 0.0)
    cx, cy = tp._cell_center(i, j)
    ratio = max(abs(cx), abs(cy)) / (size / 2.0)
    assert ratio <= 0.5 + 1e-9, f"level {level}: origin at {ratio:.1%} of the way to an edge"


def test_coarser_level_boundaries_are_a_subset_of_the_next_finer_level():
  # The nesting property _try_merge_upward()/coarsening depend on: a level-(L+1) cell's rectangle must
  # exactly equal the union of 4 level-L cells, corner-for-corner -- true iff every level-(L+1) boundary
  # coincides with a level-L boundary. Checking only CONSECUTIVE level pairs is sufficient for every pair
  # (subset relations compose transitively), which is also how LEVEL_SHIFTS_M itself is built.
  for level in range(tile_placement.MAX_TILE_LEVEL):
    size_l = tile_placement.size_for_level(level)
    size_l1 = tile_placement.size_for_level(level + 1)
    shift_l = tile_placement.LEVEL_SHIFTS_M[level]
    shift_l1 = tile_placement.LEVEL_SHIFTS_M[level + 1]
    for k in range(-3, 4):
      boundary = size_l1 * k - shift_l1
      m = (boundary + shift_l) / size_l
      assert abs(m - round(m)) < 1e-9, f"level {level}->{level+1} boundary {boundary} not a subset"


def test_adjacent_cells_tile_without_gaps_or_overlaps():
  tp = _make()
  tp.tile_level = 1
  size = tile_placement.size_for_level(1)
  for i in range(-2, 3):
    cx, _ = tp._cell_center(i, 0)
    cx_next, _ = tp._cell_center(i + 1, 0)
    assert abs((cx_next - size / 2.0) - (cx + size / 2.0)) < 1e-9   # i's right edge == i+1's left edge
