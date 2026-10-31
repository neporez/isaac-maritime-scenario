"""Mouse-click water-tile placement -- snap-to-grid "paint" mode, now power-of-2 "quadtree-style"
(2026-10-30, replacing the earlier free-size version): tile size is restricted to
size_for_level(level) = BASE_TILE_SIZE_M * 2**level (32/64/128/256/512m/...), and placing a bigger tile
over an area automatically removes (coarsens away) any smaller tiles it now fully covers -- so a scene
can have fine detail in some spots and coarse coverage elsewhere, a stepping stone toward extending
water coverage far without rebuilding the whole area at one uniform resolution (see CLAUDE.md's
multi-tile-layout-foundation note). A completed 2x2 quartet of same-level tiles also auto-merges UPWARD
into one bigger tile (_try_merge_upward()/_find_merge_group(), uncapped except for MAX_TILE_LEVEL's
practical ceiling, 2026-10-30, user's own request -- "무한대로"). The merge check is GEOMETRIC (actual
tile positions/sizes), not based on any fixed global grid index/phase -- an earlier index-based version
(`parent_i = i // 2`) never merged tiles built symmetrically outward from the world origin, which is
exactly what the own ship's spawn point invites a user to do (see _try_merge_upward()'s own docstring
for the full story, found live 2026-10-30: "안합쳐지는데?").

This is NOT an actual tree data structure -- at the tile counts this interactive tool will ever see
(tens to a few hundred), a flat dict (WaveBackend.tiles, already existed) plus an O(n) geometric
overlap/containment scan per hover tick is simpler and plenty fast. The "quadtree" part is purely the
SIZE semantics (powers of 2, corner-aligned so each level's cells nest exactly inside the next level's),
not a literal node tree.

The grid itself: corner-aligned, not center-aligned per cell (changed from the previous version
specifically so levels nest) -- cell (i, j) at level L covers the world rect
[i*size - LEVEL_SHIFTS_M[L], (i+1)*size - LEVEL_SHIFTS_M[L]] x [... same in y], center
((i+0.5)*size - LEVEL_SHIFTS_M[L], (j+0.5)*size - LEVEL_SHIFTS_M[L]). A 64m cell's area is then EXACTLY
covered by four 32m cells, corner-for-corner, which is what makes "does placing this bigger tile fully
contain these existing smaller ones" a plain rectangle-containment check.

LEVEL_SHIFTS_M (2026-11-02, user's own call, second pass): per-level grid shifts so the world origin
(0,0) -- where the own ship always spawns -- sits as close to CENTERED as possible at every level, not
just not-on-a-corner. The first pass (2026-11-01) used one constant shift (BASE_TILE_SIZE_M/2 = 16m) at
every level, which exactly centers level 0 but does nothing for bigger ones: found live immediately
after, placing the single biggest (2048m) tile left the ship at 98% of the way to a corner, because a
fixed 16m shift is negligible against a 2048m cell. The nesting property above requires
`LEVEL_SHIFTS_M[L+1] - LEVEL_SHIFTS_M[L]` to be a multiple of `size_for_level(L)` (so every coarser
boundary coincides with a finer one -- set-subset relations compose transitively, so checking each
CONSECUTIVE pair is sufficient for every pair, not just adjacent ones) -- which makes PERFECT centering
at every level simultaneously impossible: centering level L exactly needs `shift_L = size_L/2`, but two
consecutive exact centers differ by exactly `size_L/2`, never a multiple of `size_L`. `_compute_level_shifts()`
greedily picks, at each level, whichever valid shift (differing from the previous level's by a multiple
of the previous level's size) lands closest to that level's own ideal center -- computed, not hand-tuned,
so it stays correct if BASE_TILE_SIZE_M/MAX_TILE_LEVEL ever change. Measured result for the current
BASE_TILE_SIZE_M=32/MAX_TILE_LEVEL=6: worst case 50% of the way to an edge (level 1), every other level
25-38%, vs the single-constant version's 0%/50%/75%/87.5%/93.75%/96.9%/98.4% (exponentially worse at
each successive level) -- origin never exactly centered past level 0, but nowhere close to a corner
either, at any level.

REFINING an area a bigger tile already covers (the opposite of coarsening) is deliberately NOT automatic
-- carving a hole out of an existing tile to fit a smaller one inside it was considered and rejected by
the user as unneeded complexity ("그런거 생각 안해도되잖아", 2026-10-30). Instead there's an explicit
DELETE mode (self.mode = MODE_DELETE): hover over any existing tile (whatever size) and click to remove
it outright, then place smaller tiles in the now-empty area. PLACE and DELETE are two modes on the SAME
controller (not two separate classes) specifically so they can share one mouse-event-wiring stack
instead of a third copy of it.

Reuses ui/placement.py's PlacementController.mouse_to_ground_xy directly (a pure @staticmethod, no
vessel-specific state). Everything else here (live hover-follow via carb.input polling, the debounced
click handler, the viewport mouse-handler lifecycle) is DUPLICATED from PlacementController rather than
shared through a common base class -- see that module's own docstring for why (these are independent,
mutually exclusive placement modes, and the quirks being duplicated are exactly as hard-won/unverified
as they were for vessels).

BUG FOUND + FIXED, 2026-10-30 (earlier this session, preserved for context): a first version's click
handler independently re-derived ground position from set_mouse_pressed_fn's own (x, y) args instead of
trusting live-follow's (confirmed-working) position, and every click landed at world (0, 0) regardless
of where the ghost was. Fixed by removing the click handler's own ray-cast entirely -- a click always
acts on whatever self._hovered holds, set only by live-follow. That design is unchanged here.

Unlike a vessel, a tile is an axis-aligned square -- no yaw to nudge, so no keyboard handling here at
all (a deliberate simplification, not an oversight).

T-JUNCTION CRACKS, FOUND + FIXED LIVE, 2026-10-30: placing differently-sized tiles next to each other
(the whole point of the coarsening behavior above) showed real triangular gaps at their shared
boundaries, not just a shading seam -- a classic LOD-terrain problem: a coarser tile's edge is one long
straight segment between its own (widely-spaced) vertices, which the actual (wavy) surface deviates from
in between, while a finer neighbor samples that same stretch more densely and follows the true surface
more closely, leaving a gap. Fixed via resolution_for_level() -- see its own docstring -- which the user
arrived at independently of (then confirmed over) a "skirt" geometry alternative."""
import time

import carb.input
import omni.appwindow
import omni.kit.app
import omni.usd
from pxr import Gf, UsdGeom

try:
  from omni.kit.viewport.utility import get_active_viewport_window
  VIEWPORT_PICKING_AVAILABLE = True
  _import_error = None
except ImportError as exc:
  VIEWPORT_PICKING_AVAILABLE = False
  _import_error = exc

from .placement import PlacementController

GHOST_PATH = "/World/ScenarioTilePlacementGhost"
PRESS_DEBOUNCE_SEC = 0.3
GHOST_THICKNESS_M = 0.2     # thin flat slab -- a highlighted cell/tile, not a floating box
GHOST_ABOVE_WATER_M = 0.1   # lift slightly above the water surface so it doesn't z-fight
COLOR_AVAILABLE = Gf.Vec3f(0.2, 0.9, 0.3)   # green -- empty, click to place
COLOR_OCCUPIED = Gf.Vec3f(0.9, 0.2, 0.2)    # red -- place: blocked: delete: this is what you'd remove

BASE_TILE_SIZE_M = 32.0   # level 0's size -- size_for_level(level) = BASE_TILE_SIZE_M * 2**level
MAX_TILE_LEVEL = 6        # a practical UI-slider ceiling (32m * 2**6 = 2048m), lowered from 10
                           # (2026-11-01, user's own call: resolution_for_level()'s constant-vertex-
                           # spacing formula means vertex COUNT grows with tile AREA -- level 10's
                           # ~32.8km tile at the default base_resolution=17 works out to a (16*2**10+1)^2
                           # ~= 268M-vertex single mesh, which breaks (confirmed live: "30000미터면
                           # 터진다"). Level 6's ~1025x1025 (~1.05M vertices) is the practical ceiling
                           # found to actually work -- not a structural limit anywhere else in the math,
                           # so this can be raised again later if a real fix for the vertex-count blowup
                           # (e.g. capping resolution_for_level() independent of size, not just this UI
                           # ceiling) is worth doing.
DEFAULT_TILE_LEVEL = 1    # 64m -- a reasonable middle default
DEFAULT_BASE_RESOLUTION = 17   # 16 intervals across the smallest (level 0) tile -- see resolution_for_level()


def size_for_level(level):
  return BASE_TILE_SIZE_M * (2 ** level)


def _compute_level_shifts(max_level):
  """See the module docstring's own LEVEL_SHIFTS_M note for the full derivation/why. Greedily builds a
  per-level grid shift list: level 0 is exactly centered (size_for_level(0)/2), and each subsequent level
  picks whichever shift satisfying the nesting constraint (differs from the previous level's by a
  multiple of the previous level's size) lands closest to THAT level's own ideal center."""
  sizes = [size_for_level(level) for level in range(max_level + 1)]
  shifts = [sizes[0] / 2.0]
  for level in range(1, max_level + 1):
    prev_shift, prev_size = shifts[-1], sizes[level - 1]
    ideal = sizes[level] / 2.0
    k0 = round((ideal - prev_shift) / prev_size)
    candidates = (prev_shift + k * prev_size for k in (k0 - 1, k0, k0 + 1))
    shifts.append(min(candidates, key=lambda c: abs(c - ideal)))
  return shifts


LEVEL_SHIFTS_M = _compute_level_shifts(MAX_TILE_LEVEL)

MODE_PLACE = "place"
MODE_DELETE = "delete"


class TilePlacementController:
  """Owns the ghost-highlight prim and viewport mouse subscription for painting (and deleting) water
  tiles on a power-of-2, corner-aligned grid. One instance per window; call teardown() when the window
  is destroyed."""

  def __init__(self, controller, on_status=None):
    self.sc = controller   # api.ScenarioController
    self.on_status = on_status or (lambda text: None)

    self.active = False
    self.mode = MODE_PLACE
    self.tile_level = DEFAULT_TILE_LEVEL
    self.base_resolution = DEFAULT_BASE_RESOLUTION
    self._placed_order = []   # tile_ids ADDED (not deleted) this session, in order -- for undo_last();
                               # see undo_last()'s own docstring for what it does NOT undo
    self._hovered = None      # dict describing what the ghost currently shows -- see _update_hover_*()

    self._placement_update_sub = None
    self._mouse_device = None
    self._live_follow_broken = False
    self._global_to_local_offset = {"x": 0.0, "y": 0.0, "calibrated": False}
    self._last_press_time = 0.0
    self._viewport_window = None

  @property
  def tile_size_m(self):
    return size_for_level(self.tile_level)

  def resolution_for_level(self, level):
    """Vertex resolution for a given level such that vertex SPACING stays constant across every level
    -- R_L = (base_resolution - 1) * 2**level + 1, NOT just base_resolution*2**level. This is what
    eliminates T-junction cracks between differently-sized adjacent tiles (found 2026-10-30, live, with
    mixed-size tiles placed next to each other): with this formula, two level-L tiles placed
    edge-to-edge span exactly the same world positions a single level-(L+1) tile covering that same
    area would have (half of the bigger tile's vertices land exactly on the smaller tiles' shared
    boundary) -- so a coarser tile's edge ALREADY HAS a real vertex everywhere finer neighbors need one,
    instead of approximating that stretch with one long straight segment that the (wavy) actual surface
    deviates from. User's own proposal, independently arriving at the same fix: "resolution을 가장 작은
    타일 기준으로 정하고 큰 타일은 비례해서 늘리면 되는거 아니야" (2026-10-30)."""
    return (self.base_resolution - 1) * (2 ** level) + 1

  # ------------------------------------------------------------ stage helpers
  @staticmethod
  def _stage():
    return omni.usd.get_context().get_stage()

  def _wave_height_at(self, x, y):
    return self.sc.wave_height_at(x, y)

  # ------------------------------------------------------------ grid math (corner-aligned, per level,
  # shifted by LEVEL_SHIFTS_M[level] -- see the module docstring's own note for why)
  def _cell_index(self, x, y):
    size = self.tile_size_m
    shift = LEVEL_SHIFTS_M[self.tile_level]
    return int((x + shift) // size), int((y + shift) // size)

  def _cell_center(self, i, j):
    return self._cell_center_at_level(self.tile_level, i, j)

  @staticmethod
  def _cell_center_at_level(level, i, j):
    """Same corner-aligned (shifted) math as _cell_center(), but for an EXPLICIT level rather than the
    UI slider's currently-selected one -- needed by _try_merge_upward(), which operates on the level a
    tile was actually placed at, not whatever the slider happens to show right now."""
    size = size_for_level(level)
    shift = LEVEL_SHIFTS_M[level]
    return (i + 0.5) * size - shift, (j + 0.5) * size - shift

  @staticmethod
  def _fmt_index(n):
    """USD prim names may not contain '-' (hyphen) -- a negative cell index formatted as e.g. "tile_-1_-1"
    is an INVALID prim name and crashes UsdGeom.Mesh.Define (found 2026-10-30, trying to place a tile at
    cell (-1, -1): 'Path must be an absolute path: <>'). "n1" instead of "-1" keeps the id a valid
    identifier ([A-Za-z_][A-Za-z0-9_]*) in every direction."""
    return str(n) if n >= 0 else f"n{-n}"

  @classmethod
  def _cell_tile_id(cls, level, i, j):
    # level baked into the id so e.g. 32m cell (2,3) and 64m cell (2,3) -- different places -- never
    # collide.
    return f"tile_L{level}_{cls._fmt_index(i)}_{cls._fmt_index(j)}"

  # ------------------------------------------------------------ geometric overlap/containment
  @staticmethod
  def _rect(center_x, center_y, size_m):
    half = size_m / 2.0
    return center_x - half, center_x + half, center_y - half, center_y + half

  @staticmethod
  def _overlaps(a, b):
    axmin, axmax, aymin, aymax = a
    bxmin, bxmax, bymin, bymax = b
    return not (axmax <= bxmin or axmin >= bxmax or aymax <= bymin or aymin >= bymax)

  @staticmethod
  def _contained_in(inner, outer, eps=1e-6):
    ixmin, ixmax, iymin, iymax = inner
    oxmin, oxmax, oymin, oymax = outer
    return ixmin >= oxmin - eps and ixmax <= oxmax + eps and iymin >= oymin - eps and iymax <= oymax + eps

  def _placement_check(self, cx, cy, size_m):
    """Returns (blocked, contained_tile_ids) for placing a size_m tile centered at (cx, cy).
    blocked=True (an equal-or-larger existing tile overlaps and isn't fully inside the new footprint,
    including an exact duplicate) means placement must be refused -- delete it first. contained_tile_ids
    are existing tiles strictly smaller than and fully inside the new footprint; these get removed
    (coarsened away) if placement proceeds."""
    new_rect = self._rect(cx, cy, size_m)
    blocked = False
    contained = []
    for tile_id, spec in self.sc.list_water_tiles().items():
      other_rect = self._rect(spec.center_x, spec.center_y, spec.size_m)
      if not self._overlaps(new_rect, other_rect):
        continue
      other_in_new = self._contained_in(other_rect, new_rect)
      new_in_other = self._contained_in(new_rect, other_rect)
      if other_in_new and new_in_other:
        blocked = True   # exact duplicate (same cell, same level) -- already there
      elif other_in_new:
        contained.append(tile_id)   # strictly smaller, fully inside -- will be coarsened away
      else:
        blocked = True   # an equal-or-larger tile is in the way

    return blocked, contained

  @staticmethod
  def _find_merge_group(cx, cy, size_m, existing, eps=1e-6):
    """Whether the tile at (cx, cy, size_m) has 3 same-size neighbors arranged so the four of them form
    one clean 2x-bigger square, in ANY of the 4 possible roles this tile could play in that square
    (bottom-left/bottom-right/top-left/top-right -- (dx, dy) below is "which direction the other 3 sit
    in"). Returns (partner_tile_ids, merged_center_x, merged_center_y, merged_size) on success, else
    None. Purely geometric (reads actual positions/sizes off `existing`, a list_water_tiles() snapshot)
    -- no assumption about which global grid "phase" the tiles happen to be placed on, which is what
    makes this work regardless of where in the world the tiles are (see _try_merge_upward()'s own
    docstring for why that matters)."""
    for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
      needed = [(cx + dx * size_m, cy), (cx, cy + dy * size_m), (cx + dx * size_m, cy + dy * size_m)]
      partner_ids = []
      for px, py in needed:
        match = next((tid for tid, spec in existing.items()
                      if abs(spec.center_x - px) < eps and abs(spec.center_y - py) < eps
                      and abs(spec.size_m - size_m) < eps), None)
        if match is None:
          partner_ids = None
          break
        partner_ids.append(match)
      if partner_ids is not None:
        return partner_ids, cx + dx * size_m / 2.0, cy + dy * size_m / 2.0, size_m * 2.0
    return None

  def _try_merge_upward(self, tile_id, cx, cy, size_m, level):
    """After placing a tile (tile_id, centered at (cx, cy), size size_m, at the given level), checks
    whether it now has 3 same-size neighbors forming a clean 2x-bigger square (_find_merge_group(),
    tried in all 4 possible arrangements, not just one fixed "global grid phase" -- see that method's
    own docstring), and if so merges all 4 into one tile at level+1 -- then repeats the check for the
    NEW bigger tile, for as many levels as keep completing (bounded only by MAX_TILE_LEVEL's practical
    ceiling, i.e. "무한대로" within that, 2026-10-30, user's own request).

    GEOMETRIC, not index-based, specifically because an earlier (index-based, `parent_i = i // 2`)
    version never merged tiles built symmetrically outward from the world origin -- found live,
    2026-10-30, user: "안합쳐지는데?" with tile ids `tile_L1_0_0`/`tile_L1_0_n1`/`tile_L1_n1_0`/
    `tile_L1_n1_n1`. Root cause: `i // 2`-style pairing groups index 0 with 1 and index -1 with -2 (NOT
    with 0) -- a fixed global phase that has nothing special about the origin, even though the own
    ship spawns exactly there and building symmetrically outward from spawn is the most natural thing a
    user would do. This geometric version has no such phase: it only asks "do 4 actual tiles form a
    square together", so it merges the user's origin-centered quartet (and every other valid quartet,
    anywhere) the same way. Only ever fires on an EXACT quartet of same-size tiles -- deliberately
    doesn't try to detect/merge a "logically complete but differently-subdivided" area, matching how
    coarsening-on-placement is already scoped. Returns the id of the final (possibly multiply-merged)
    tile if ANY merge happened, else None."""
    merged_id = None
    while level < MAX_TILE_LEVEL:
      existing = self.sc.list_water_tiles()
      group = self._find_merge_group(cx, cy, size_m, existing)
      if group is None:
        return merged_id
      partner_ids, new_cx, new_cy, new_size = group
      self.sc.remove_water_tile(tile_id)
      for pid in partner_ids:
        self.sc.remove_water_tile(pid)
      level += 1
      spec = self.sc.add_water_tile(new_cx, new_cy, size_m=new_size, resolution=self.resolution_for_level(level))
      if spec is None:
        return merged_id
      merged_id = spec.tile_id
      tile_id, cx, cy, size_m = spec.tile_id, new_cx, new_cy, new_size
    return merged_id

  def _find_tile_containing(self, x, y):
    """Whatever existing tile's footprint contains world point (x, y), for DELETE mode -- any size, not
    snapped to the CURRENT level's grid (you should be able to delete a 256m tile while the level slider
    is set to 32m)."""
    for tile_id, spec in self.sc.list_water_tiles().items():
      half = spec.size_m / 2.0
      if abs(x - spec.center_x) <= half and abs(y - spec.center_y) <= half:
        return tile_id, spec
    return None

  # ------------------------------------------------------------ ghost preview
  def _ensure_ghost(self):
    stage = self._stage()
    if not stage.GetPrimAtPath(GHOST_PATH):
      stage.DefinePrim(GHOST_PATH, "Cube")

  def _draw_ghost(self, cx, cy, size_m, color):
    stage = self._stage()
    ghost = stage.GetPrimAtPath(GHOST_PATH)
    if not ghost:
      return
    UsdGeom.Imageable(ghost).MakeVisible()
    z = self._wave_height_at(cx, cy) + GHOST_ABOVE_WATER_M
    xform = UsdGeom.Xformable(ghost)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(cx, cy, z))
    xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
      Gf.Vec3d(size_m / 2.0, size_m / 2.0, GHOST_THICKNESS_M / 2.0))
    UsdGeom.Gprim(ghost).CreateDisplayColorAttr().Set([color])

  def _hide_ghost(self):
    """DELETE mode, nothing under the cursor -- hide rather than remove, so re-showing it on the next
    hover tick doesn't need to recreate the prim."""
    stage = self._stage()
    ghost = stage.GetPrimAtPath(GHOST_PATH)
    if ghost:
      UsdGeom.Imageable(ghost).MakeInvisible()

  def _remove_ghost(self):
    stage = self._stage()
    if stage.GetPrimAtPath(GHOST_PATH):
      stage.RemovePrim(GHOST_PATH)
    self._hovered = None

  # ------------------------------------------------------------ hover (branches on self.mode)
  def _update_hover(self, x, y):
    if self.mode == MODE_DELETE:
      self._update_hover_delete(x, y)
    else:
      self._update_hover_place(x, y)

  def _update_hover_place(self, x, y):
    i, j = self._cell_index(x, y)
    h = self._hovered
    if h is not None and h.get("mode") == MODE_PLACE and h.get("level") == self.tile_level \
       and h.get("i") == i and h.get("j") == j:
      return   # same cell as last tick, same level -- nothing to recompute
    cx, cy = self._cell_center(i, j)
    size_m = self.tile_size_m
    tile_id = self._cell_tile_id(self.tile_level, i, j)
    blocked, contained = self._placement_check(cx, cy, size_m)
    self._hovered = {"mode": MODE_PLACE, "i": i, "j": j, "level": self.tile_level, "cx": cx, "cy": cy,
                      "size_m": size_m, "tile_id": tile_id, "blocked": blocked, "contained": contained}
    self._draw_ghost(cx, cy, size_m, COLOR_OCCUPIED if blocked else COLOR_AVAILABLE)

  def _update_hover_delete(self, x, y):
    found = self._find_tile_containing(x, y)
    found_id = found[0] if found else None
    h = self._hovered
    if h is not None and h.get("mode") == MODE_DELETE and h.get("tile_id") == found_id:
      return
    if found is None:
      self._hovered = {"mode": MODE_DELETE, "tile_id": None}
      self._hide_ghost()
      return
    tile_id, spec = found
    self._hovered = {"mode": MODE_DELETE, "tile_id": tile_id, "cx": spec.center_x, "cy": spec.center_y,
                      "size_m": spec.size_m}
    self._draw_ghost(spec.center_x, spec.center_y, spec.size_m, COLOR_OCCUPIED)

  # ------------------------------------------------------------ mouse -> ground point
  def _mouse_to_ground_xy(self, local_x, local_y, widget_w, widget_h):
    viewport_window = get_active_viewport_window()
    if viewport_window is None:
      return None
    return PlacementController.mouse_to_ground_xy(
      local_x, local_y, widget_w, widget_h, viewport_window.viewport_api)

  # ------------------------------------------------------------ live hover-follow (the ONLY mouse-position path)
  def _get_mouse_device(self):
    if self._mouse_device is None:
      self._mouse_device = omni.appwindow.get_default_app_window().get_mouse()
    return self._mouse_device

  def _on_placement_update(self, e):
    if not self.active or self._live_follow_broken:
      return
    try:
      coords = carb.input.acquire_input_interface().get_mouse_coords_pixel(self._get_mouse_device())
      vw = get_active_viewport_window()
      if vw is None:
        return
      if self._global_to_local_offset["calibrated"]:
        local_x = coords[0] - self._global_to_local_offset["x"]
        local_y = coords[1] - self._global_to_local_offset["y"]
      else:
        local_x = coords[0] - vw.frame.screen_position_x
        local_y = coords[1] - vw.frame.screen_position_y
      w, h = vw.frame.computed_width, vw.frame.computed_height
      if not w or not h or local_x < 0 or local_x > w or local_y < 0 or local_y > h:
        return
      hit = self._mouse_to_ground_xy(local_x, local_y, w, h)
      if hit is None:
        return
      gx, gy = hit
      self._update_hover(gx, gy)
    except Exception as exc:
      print(f"[maritime_scenario] tile live mouse-follow failed ({exc}) -- falling back disabled "
            "(no fallback click-time ray-cast -- see module docstring)")
      self._live_follow_broken = True
      self._placement_update_sub = None

  def _start_live_follow(self):
    if self._placement_update_sub is None and not self._live_follow_broken:
      self._placement_update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
        self._on_placement_update)

  def _stop_live_follow(self):
    self._placement_update_sub = None

  # ------------------------------------------------------------ tile level/resolution
  def set_tile_level(self, level):
    self.tile_level = max(0, min(int(level), MAX_TILE_LEVEL))
    self._hovered = None   # grid changed -- force a re-snap on the next hover tick

  def set_base_resolution(self, resolution):
    """Base (level-0, smallest-tile) resolution -- every other level's resolution is DERIVED from this
    via resolution_for_level(), not independently settable, specifically so levels stay crack-free."""
    self.base_resolution = max(2, int(resolution))
    self._hovered = None   # a placement's reported resolution-for-level changed -- force a re-snap

  # ------------------------------------------------------------ enter / exit placement mode
  def start(self, mode=MODE_PLACE):
    if not VIEWPORT_PICKING_AVAILABLE:
      self.on_status(f"viewport picking unavailable ({_import_error})")
      return False

    if self.active and self.mode != mode:
      self.stop()   # switch mode cleanly rather than running place+delete at once
    self.mode = mode
    self.active = True
    self._placed_order = []
    self._hovered = None
    self._last_press_time = 0.0
    self._ensure_ghost()

    try:
      vw = get_active_viewport_window()
      if vw is None:
        self.on_status("no active viewport window found")
        self.active = False
        self._remove_ghost()
        return False
      self._viewport_window = vw
      vw.frame.set_mouse_pressed_fn(self._on_viewport_mouse_pressed)
    except Exception as exc:
      self.on_status(f"couldn't attach viewport mouse handlers ({exc})")
      self.active = False
      self._remove_ghost()
      return False

    self._start_live_follow()
    if mode == MODE_DELETE:
      self.on_status("hover over a tile and click to delete it")
    else:
      self.on_status(f"level {self.tile_level} ({self.tile_size_m:.0f}m, "
                      f"{self.resolution_for_level(self.tile_level)}x resolution) -- "
                      "green = place, red = blocked (something equal-or-larger is already there)")
    return True

  def _on_viewport_mouse_pressed(self, *args):
    now = time.monotonic()
    if now - self._last_press_time < PRESS_DEBOUNCE_SEC:
      return
    self._last_press_time = now
    if not self.active or len(args) < 3 or args[2] != 0:   # args[2] = button, 0 = left
      return
    if self.mode == MODE_DELETE:
      self._handle_delete_click()
    else:
      self._handle_place_click()

  def _handle_place_click(self):
    h = self._hovered
    if h is None or h.get("mode") != MODE_PLACE:
      self.on_status("move the mouse over the water first")
      return
    if h["blocked"]:
      self.on_status(f"{h['tile_id']} is blocked -- something equal-or-larger is already there, "
                      "switch to Delete mode to remove it first")
      return
    spec = self.sc.add_water_tile(h["cx"], h["cy"], size_m=h["size_m"],
                                   resolution=self.resolution_for_level(h["level"]), tile_id=h["tile_id"])
    if spec is None:
      self.on_status("couldn't place -- is the scene built yet?")
      return
    for contained_id in h["contained"]:
      self.sc.remove_water_tile(contained_id)
    self._placed_order.append(spec.tile_id)
    merged_id = self._try_merge_upward(spec.tile_id, spec.center_x, spec.center_y, spec.size_m, h["level"])
    if merged_id is not None:
      self._placed_order[-1] = merged_id   # the just-placed tile got absorbed into this bigger one
    cx, cy = h["cx"], h["cy"]
    self._hovered = None   # force a recheck -- occupancy just changed
    self._update_hover_place(cx, cy)
    replaced_note = f" (replaced {len(h['contained'])} smaller tile(s))" if h["contained"] else ""
    merged_note = f" -- auto-merged up to {merged_id}" if merged_id is not None else ""
    self.on_status(f"{len(self._placed_order)} tile(s) placed this session{replaced_note}{merged_note} -- "
                    "click to keep going, Stop when done")

  def _handle_delete_click(self):
    h = self._hovered
    if h is None or h.get("mode") != MODE_DELETE or h.get("tile_id") is None:
      self.on_status("hover over a tile to delete it")
      return
    tile_id = h["tile_id"]
    self.sc.remove_water_tile(tile_id)
    x, y = h["cx"], h["cy"]
    self._hovered = None
    self._update_hover_delete(x, y)
    self.on_status(f"deleted {tile_id}")

  def undo_last(self):
    """Only undoes the tile THIS controller most recently ADDED -- does NOT restore any smaller tiles a
    placement coarsened away OR auto-merged upward (known limitation, 2026-10-30: restoring them would
    need remembering their full specs, not just that they existed, which isn't tracked -- revisit if
    this turns out to matter in practice), and has no effect on deletions made in Delete mode. If the
    last placement triggered an auto-merge, _placed_order already points at the final merged tile (see
    _handle_place_click()), so this removes THAT (undoing the visible outcome of the click), not the
    original small tile that got absorbed into it."""
    if not self._placed_order:
      self.on_status("nothing to undo")
      return
    tile_id = self._placed_order.pop()
    self.sc.remove_water_tile(tile_id)
    if self.mode == MODE_PLACE and self._hovered is not None and self._hovered.get("mode") == MODE_PLACE:
      cx, cy = self._hovered["cx"], self._hovered["cy"]
      self._hovered = None
      self._update_hover_place(cx, cy)
    self.on_status(f"removed {tile_id} -- {len(self._placed_order)} tile(s) placed")

  def stop(self):
    self.active = False
    self._stop_live_follow()
    self._remove_ghost()
    try:
      if self._viewport_window is not None:
        self._viewport_window.frame.set_mouse_pressed_fn(None)
    except Exception as exc:
      print(f"[maritime_scenario] (non-fatal) couldn't detach viewport mouse handlers: {exc}")
    self.on_status("")

  def cancel(self):
    self.stop()

  def teardown(self):
    """Call when the owning window is destroyed."""
    self.stop()
