"""
Gerstner wave math -- extracted verbatim from boat_mesh_pipeline/06_scenario_editor/
spawn_gerstner_wave.py (visual mesh displacement) and other_boats.py/float_mesh_test.py (the
z-only height sampled for ship-motion physics; both of those duplicated this same formula by hand
from the wave script). Pure Python + math (+ numpy for the vectorized grid functions below, a
deliberate, scoped exception for one performance-critical path -- see grid_points_and_normals_np()'s
own docstring), no omni/pxr.

Two wave trains summed together by default (WAVE_DIRECTIONS), each successive index a bit smaller/
longer/slower, alternating between the two directions -- the same convention this project has used
since its earliest buoyancy prototype.
"""
import math
from dataclasses import dataclass

import numpy as np


WAVE_DIRECTIONS = [(1.0, 0.0), (0.6, 0.8)]


@dataclass(frozen=True)
class WaveParams:
  amplitude_m: float = 0.8
  wavelength_m: float = 30.0
  speed_mps: float = 5.0
  steepness: float = 0.25
  num_waves: int = 2
  size_m: float = 400.0        # visual mesh plane extent
  resolution: int = 64         # visual mesh points per side


def gerstner_offset(x, y, t, amp, wavelength, speed, steepness, direction):
  """Full (dx, dy, dz) Gerstner displacement for ONE wave train -- used for the visual mesh only (the
  physics height sampling below only needs the z component, see wave_height)."""
  k = 2 * math.pi / wavelength
  omega = speed * k
  dx, dy = direction
  phase = k * (dx * x + dy * y) - omega * t
  q = steepness / (k * amp) if amp > 0 else 0.0
  return (
    q * amp * dx * math.cos(phase),
    q * amp * dy * math.cos(phase),
    amp * math.sin(phase),
  )


def gerstner_z(x, y, t, amp, wavelength, speed, direction):
  """z-only Gerstner displacement for one wave train -- this is all ship-motion physics needs (never
  the horizontal dx/dy), same simplification other_boats.py's own _ob_gerstner_z already makes."""
  k = 2 * math.pi / wavelength
  omega = speed * k
  dx, dy = direction
  phase = k * (dx * x + dy * y) - omega * t
  return amp * math.sin(phase)


def wave_offset(x, y, t, params: WaveParams):
  """Full (dx, dy, dz) displacement at a point, summed over params.num_waves trains -- for the visual
  mesh (spawn_gerstner_wave.py's update_points())."""
  total = [0.0, 0.0, 0.0]
  for i in range(params.num_waves):
    direction = WAVE_DIRECTIONS[i % len(WAVE_DIRECTIONS)]
    a = params.amplitude_m * (1.0 - i * 0.15)
    wl = params.wavelength_m * (1.0 + i * 0.3)
    sp = params.speed_mps * (1.0 - i * 0.1)
    ox, oy, oz = gerstner_offset(x, y, t, a, wl, sp, params.steepness, direction)
    total[0] += ox
    total[1] += oy
    total[2] += oz
  return tuple(total)


def wave_height(x, y, t, params: WaveParams, origin_xyz=(0.0, 0.0, 0.0)):
  """z-only height at a WORLD point, relative to the wave prim's own world position -- what ship-motion
  physics reads every frame (other_boats.py's _ob_wave_height_at() / float_mesh_test.py's
  _float_wave_height_at()). origin_xyz is the wave mesh's own world translation (it may not sit at the
  stage origin), same role as those functions' wave_pos parameter."""
  ox, oy, oz = origin_xyz
  rel_x, rel_y = x - ox, y - oy
  z = 0.0
  for i in range(params.num_waves):
    direction = WAVE_DIRECTIONS[i % len(WAVE_DIRECTIONS)]
    a = params.amplitude_m * (1.0 - i * 0.15)
    wl = params.wavelength_m * (1.0 + i * 0.3)
    sp = params.speed_mps * (1.0 - i * 0.1)
    z += gerstner_z(rel_x, rel_y, t, a, wl, sp, direction)
  return z + oz


def gerstner_dz(x, y, t, amp, wavelength, speed, direction):
  """Analytic (closed-form, NOT finite-difference) partial derivatives of gerstner_z w.r.t. x and y, for
  ONE wave train: d(z)/dx = amp*k*cos(phase)*dir_x, d(z)/dy = amp*k*cos(phase)*dir_y -- the plain chain
  rule on gerstner_z's own amp*sin(phase), phase = k*(dir_x*x+dir_y*y) - omega*t."""
  k = 2 * math.pi / wavelength
  omega = speed * k
  dx, dy = direction
  phase = k * (dx * x + dy * y) - omega * t
  c = amp * k * math.cos(phase)
  return c * dx, c * dy


def wave_height_gradient(x, y, t, params: WaveParams, origin_xyz=(0.0, 0.0, 0.0)):
  """(dz/dx, dz/dy) at a WORLD point, summed over params.num_waves trains -- same structure as
  wave_height(), but analytic rather than finite-differenced (unlike wave_slope() below), so it's both
  cheaper (no extra wave_height() evaluations) and exact. This is what makes wave_normal() a pure
  function of world (x, y, t): two different tiles sampling the SAME world point get the IDENTICAL
  gradient, so vertex normals line up exactly at a shared tile boundary -- see wave_normal()'s own
  docstring for why that matters (2026-10-30: per-tile-independent normal computation was causing a
  visible lighting seam at tile boundaries, even though vertex POSITIONS already matched exactly)."""
  ox, oy, _oz = origin_xyz
  rel_x, rel_y = x - ox, y - oy
  gx, gy = 0.0, 0.0
  for i in range(params.num_waves):
    direction = WAVE_DIRECTIONS[i % len(WAVE_DIRECTIONS)]
    a = params.amplitude_m * (1.0 - i * 0.15)
    wl = params.wavelength_m * (1.0 + i * 0.3)
    sp = params.speed_mps * (1.0 - i * 0.1)
    dzx, dzy = gerstner_dz(rel_x, rel_y, t, a, wl, sp, direction)
    gx += dzx
    gy += dzy
  return gx, gy


def wave_normal(x, y, t, params: WaveParams, origin_xyz=(0.0, 0.0, 0.0)):
  """Analytic unit surface normal at a WORLD point: normalize((-dz/dx, -dz/dy, 1)) -- the standard
  heightfield-to-normal conversion, ignoring the Gerstner horizontal (dx, dy) wobble's own contribution
  to the normal (a common simplification for real-time water normals: the full Jacobian-based normal
  would also need wave_offset's horizontal-displacement derivatives, which is more work for a difference
  that's small at this project's steepness range). Used to author per-vertex mesh normals explicitly
  (grid_normals() below) instead of leaving the renderer to compute them from each tile's own local
  topology, which is what caused the boundary seam in the first place."""
  gx, gy = wave_height_gradient(x, y, t, params, origin_xyz)
  length = math.sqrt(gx * gx + gy * gy + 1.0)
  return -gx / length, -gy / length, 1.0 / length


WAVE_SLOPE_EPS_M = 1.0   # finite-difference step for slope sampling below


def wave_slope(x, y, t, params: WaveParams, origin_xyz=(0.0, 0.0, 0.0), eps=WAVE_SLOPE_EPS_M):
  """Returns (slope_x, slope_y) -- the water surface's own local tilt in each direction, central-
  differenced around (x, y). Roll reads slope_y (beam direction), pitch reads slope_x (forward
  direction) -- see ship_motion.py's module docstring for why this is sampled at the CoM's (x,y) rather
  than from points fixed to the hull."""
  slope_x = (
    wave_height(x + eps, y, t, params, origin_xyz) - wave_height(x - eps, y, t, params, origin_xyz)
  ) / (2.0 * eps)
  slope_y = (
    wave_height(x, y + eps, t, params, origin_xyz) - wave_height(x, y - eps, t, params, origin_xyz)
  ) / (2.0 * eps)
  return slope_x, slope_y


def build_topology(resolution):
  """Triangle-strip-as-triangles face/vertex-index topology for a resolution x resolution point grid --
  extracted from spawn_gerstner_wave.py's build_mesh()/rebuild_wave() (both built this inline,
  identically). Returns (face_vertex_counts, face_vertex_indices) ready for UsdGeom.Mesh."""
  faces = []
  for i in range(resolution - 1):
    for j in range(resolution - 1):
      idx = i * resolution + j
      faces.extend([idx, idx + 1, idx + resolution + 1, idx, idx + resolution + 1, idx + resolution])
  return [3] * (len(faces) // 3), faces


def grid_points(t, params: WaveParams, origin_xy=(0.0, 0.0)):
  """(x, y, z) for every point in the resolution x resolution grid, displaced by wave_offset() -- the
  pure-math half of spawn_gerstner_wave.py's update_points() (that function also pushes the result into
  a live USD attribute, which belongs in the wave backend, not here).

  origin_xy is the grid's own world-space center (e.g. a tile's placement in a multi-tile layout --
  see WaveTileSpec/grid_tile_layout() below). It shifts the Gerstner PHASE only, not the output points
  themselves, which stay in this grid's own local frame (no origin added to the returned x/y) -- exactly
  mirroring how wave_height(x, y, t, params, origin_xyz=...) already separates "world phase" from
  "position relative to the prim's own transform". This is what lets two tiles at different world
  positions, each with its own small local grid, show the SAME continuous wave field (matching
  crests/troughs at a shared boundary) instead of each one restarting its own phase at local (0,0), which
  would make every tile seam a visible discontinuity. Default (0.0, 0.0) reproduces the exact output of
  every pre-existing caller -- backward compatible."""
  resolution, size = params.resolution, params.size_m
  ox, oy = origin_xy
  points = []
  for i in range(resolution):
    for j in range(resolution):
      base_x = (i / (resolution - 1) - 0.5) * size
      base_y = (j / (resolution - 1) - 0.5) * size
      dx, dy, dz = wave_offset(base_x + ox, base_y + oy, t, params)
      points.append((base_x + dx, base_y + dy, dz))
  return points


def grid_normals(t, params: WaveParams, origin_xy=(0.0, 0.0)):
  """Per-vertex analytic normals matching grid_points()'s exact iteration order/indexing -- sampled at
  the SAME undisplaced world position grid_points() uses for its own phase (base_x+ox, base_y+oy), so a
  tile's boundary-vertex normals match its neighbor's exactly (see wave_normal()'s docstring)."""
  resolution, size = params.resolution, params.size_m
  ox, oy = origin_xy
  normals = []
  for i in range(resolution):
    for j in range(resolution):
      base_x = (i / (resolution - 1) - 0.5) * size
      base_y = (j / (resolution - 1) - 0.5) * size
      normals.append(wave_normal(base_x + ox, base_y + oy, t, params))
  return normals


def grid_points_and_normals_np(t, params: WaveParams, origin_xy=(0.0, 0.0)):
  """Vectorized (numpy) equivalent of calling grid_points() then grid_normals() -- same formulas, same
  row-major (i, j) iteration order (idx = i*resolution + j, matching build_topology()'s indexing and
  the scalar grid_points()/grid_normals()), computed together in ONE pass over the whole
  resolution x resolution grid at once (numpy array ops + a small Python loop over params.num_waves,
  NOT over every vertex) instead of two separate nested-for-loop scalar passes.

  Why this exists (2026-10-30, measured, not guessed): for a 256m tile at resolution=129 (16,641
  vertices -- an ordinary size this project's own resolution_for_level() produces), the scalar
  grid_points()+grid_normals() pair cost ~40ms combined, which alone exceeds a 60fps frame budget
  (16.6ms) for a SINGLE active tile, every frame -- confirmed the actual cause of live, reported lag
  even with distance-based tile activation already turned down to 150m. This vectorized version measured
  ~1.7ms for the same grid (~23x faster) -- the per-vertex trig calls become per-grid numpy calls (each
  resolves in C, not a Python loop), and points+normals share the same per-wave-train phase/cos terms
  computed once instead of twice.

  Returns (points, normals), each a numpy float32 array of shape (resolution*resolution, 3) -- NOT a
  list of tuples like the scalar functions, so the caller (backends/wave.py) can hand it straight to
  Vt.Vec3fArray.FromNumpy() instead of a Python list comprehension (measured separately: ~40x faster
  than `[Gf.Vec3f(*p) for p in points]` for the same vertex count).

  Deliberately a NEW function, not a replacement for grid_points()/grid_normals(): those (and every
  other scalar function in this module) stay as plain Python+math, since single-point sampling
  (height_at(), ship/vessel physics, called once or a few times per frame, never a full grid) would be
  SLOWER through numpy's per-call array-construction overhead, not faster -- vectorizing only pays off
  for the full-grid per-tile-mesh path this function is for."""
  resolution, size = params.resolution, params.size_m
  ox, oy = origin_xy
  idx = np.arange(resolution, dtype=np.float64)
  base = (idx / (resolution - 1) - 0.5) * size
  X, Y = np.meshgrid(base, base, indexing="ij")   # X[i,j]=base[i], Y[i,j]=base[j] -> flattens to i*res+j

  wx = X + ox
  wy = Y + oy
  dx_total = np.zeros_like(X)
  dy_total = np.zeros_like(X)
  dz_total = np.zeros_like(X)
  gx_total = np.zeros_like(X)
  gy_total = np.zeros_like(X)
  for i in range(params.num_waves):
    direction = WAVE_DIRECTIONS[i % len(WAVE_DIRECTIONS)]
    a = params.amplitude_m * (1.0 - i * 0.15)
    wl = params.wavelength_m * (1.0 + i * 0.3)
    sp = params.speed_mps * (1.0 - i * 0.1)
    dir_x, dir_y = direction
    k = 2 * math.pi / wl
    omega = sp * k
    phase = k * (dir_x * wx + dir_y * wy) - omega * t
    cosp = np.cos(phase)
    q = params.steepness / (k * a) if a > 0 else 0.0
    dx_total += q * a * dir_x * cosp
    dy_total += q * a * dir_y * cosp
    dz_total += a * np.sin(phase)
    c = a * k * cosp
    gx_total += c * dir_x
    gy_total += c * dir_y

  points = np.stack([X + dx_total, Y + dy_total, dz_total], axis=-1).reshape(-1, 3).astype(np.float32)
  length = np.sqrt(gx_total * gx_total + gy_total * gy_total + 1.0)
  normals = np.stack([-gx_total / length, -gy_total / length, 1.0 / length],
                      axis=-1).reshape(-1, 3).astype(np.float32)
  return points, normals


# ─────────────────────────────────────────────────────────────────────────────
# Multi-tile layouts -- presets for how WaveBackend arranges one or more wave meshes. "single_tile_layout"
# reproduces today's exact one-mesh behavior (the default); "grid_tile_layout" is a simple rectangular
# tiling preset, useful on its own (e.g. a long strip of tiles for a river-shaped body of water) and as
# the foundation for later, larger-scale layouts (a non-rectangular/polyline layout would be built the
# same way: a list of WaveTileSpecs, just placed differently). Deliberately NOT deciding here which tiles
# should be active/rendered at a given distance from the ship or camera -- that's WaveBackend's
# set_tile_active() hook, wired up by a later change, not this one.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class WaveTileSpec:
  tile_id: str
  center_x: float
  center_y: float
  size_m: float
  resolution: int


def single_tile_layout(params: WaveParams):
  """Today's exact single-mesh behavior, expressed as a 1-tile layout -- WaveBackend's default."""
  return [WaveTileSpec("main", 0.0, 0.0, params.size_m, params.resolution)]


def grid_tile_layout(nx, ny, tile_size_m, tile_resolution):
  """A simple nx x ny rectangular arrangement of equally-sized tiles, centered as a whole on (0, 0),
  spaced tile_size_m apart (so adjacent tiles are edge-to-edge with no gap and no overlap)."""
  tiles = []
  for i in range(nx):
    for j in range(ny):
      center_x = (i - (nx - 1) / 2.0) * tile_size_m
      center_y = (j - (ny - 1) / 2.0) * tile_size_m
      tiles.append(WaveTileSpec(f"tile_{i}_{j}", center_x, center_y, tile_size_m, tile_resolution))
  return tiles
