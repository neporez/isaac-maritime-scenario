"""Optional GPU (NVIDIA Warp) batched Gerstner computation -- same resilience pattern as this project's
other optional-capability modules (ui/placement.py's VIEWPORT_PICKING_AVAILABLE): import/initialize at
module load inside a try/except, expose GPU_AVAILABLE, and let callers (backends/wave.py) fall back to
the always-available core.gerstner NumPy path if this is False. A machine/environment without a GPU (or
without `warp` installed) never even imports `warp` successfully, and nothing here is required for the
extension to work -- this is purely an additive fast path.

Why this exists (2026-10-30, measured, not guessed -- see CLAUDE.md for the full numbers): NumPy
vectorization alone (core.gerstner.grid_points_and_normals_np()) got a single active tile from ~74ms to
~1ms/frame, which is fine at the scale tried so far, but the user wants confidence this stays smooth
when extending water out toward ~10km (this project's actual goal) with more active tiles at once. Calling
core.gerstner.grid_points_and_normals_np() once PER active tile pays a Python-level call/array-alloc
overhead per tile; batching every active same-resolution tile into ONE GPU kernel launch (this module's
compute_batch()) amortizes that over however many tiles are in the batch instead of paying it per tile --
measured ~3.6x faster than the current per-tile NumPy path for a realistic 15-tile active set (~4.5ms ->
~1.25ms), with the gap widening further as the active tile count grows.

Only computes POINTS + NORMALS for the full per-tile-mesh grid (same scope as
grid_points_and_normals_np()) -- single-point sampling (height_at(), ship/vessel physics) stays on the
plain scalar core.gerstner functions, unaffected and unchanged, same reasoning as why those weren't
moved to NumPy either (per-call overhead for ONE point would make a GPU round-trip even less worth it).
"""
import math

import numpy as np

from .gerstner import WAVE_DIRECTIONS, WaveParams

try:
  import warp as wp
  wp.init()
  GPU_AVAILABLE = wp.get_cuda_device_count() > 0 and wp.get_device().is_cuda
  _import_error = None
except Exception as exc:   # not just ImportError -- wp.init()/device queries can fail other ways (no
                            # CUDA driver, etc.), and must degrade to the NumPy path cleanly either way,
                            # not crash extension load.
  GPU_AVAILABLE = False
  _import_error = exc


if GPU_AVAILABLE:
  # WaveParams.num_waves is UI-adjustable from 1 to 4 (ui/window.py's "Number of wave trains" slider),
  # cycling through WAVE_DIRECTIONS' 2 directions -- the kernel below takes 4 full sets of per-train
  # coefficients (not just 2) specifically so it stays correct for every value that slider allows, not
  # just the num_waves=2 default. An earlier draft of this kernel capped at 2 trains, silently dropping
  # wave trains 3/4 for anyone who raised that slider -- caught before shipping, fixed by just carrying
  # all 4 (unused trains beyond num_waves are simply never read, the loop bound is num_waves itself).
  @wp.kernel
  def _gerstner_batch_kernel(
    base: wp.array(dtype=wp.float32), resolution: int,
    origins: wp.array(dtype=wp.vec2), t: wp.float32,
    amps: wp.vec4, wls: wp.vec4, sps: wp.vec4, dirs_x: wp.vec4, dirs_y: wp.vec4,
    steepness: wp.float32, num_waves: int,
    points: wp.array(dtype=wp.vec3), normals: wp.array(dtype=wp.vec3),
  ):
    """One thread per (tile, grid-point) pair -- tile = tid // (resolution*resolution), so one launch
    computes however many same-resolution tiles are in the batch at once. Field-for-field the same
    Gerstner formula as core.gerstner's scalar/numpy versions (ported from the prototype measured
    correct during planning: max error 2.78e-06 points / 9.83e-08 normals vs the scalar functions) --
    see compute_batch() for how the per-train coefficient vectors are derived to match
    core.gerstner.wave_offset()'s own formula exactly."""
    tid = wp.tid()
    per_tile = resolution * resolution
    tile = tid // per_tile
    local = tid % per_tile
    i = local // resolution
    j = local % resolution
    origin = origins[tile]
    bx = base[i]
    by = base[j]
    wx = bx + origin[0]
    wy = by + origin[1]

    dx = wp.float32(0.0)
    dy = wp.float32(0.0)
    dz = wp.float32(0.0)
    gx = wp.float32(0.0)
    gy = wp.float32(0.0)
    for w in range(num_waves):
      a = amps[w]
      wl = wls[w]
      sp = sps[w]
      ddx = dirs_x[w]
      ddy = dirs_y[w]
      k = wp.float32(2.0 * 3.14159265358979) / wl
      omega = sp * k
      phase = k * (ddx * wx + ddy * wy) - omega * t
      cosp = wp.cos(phase)
      sinp = wp.sin(phase)
      q = wp.float32(0.0)
      if a > wp.float32(0.0):
        q = steepness / (k * a)
      dx += q * a * ddx * cosp
      dy += q * a * ddy * cosp
      dz += a * sinp
      c = a * k * cosp
      gx += c * ddx
      gy += c * ddy

    points[tid] = wp.vec3(bx + dx, by + dy, dz)
    length = wp.sqrt(gx * gx + gy * gy + wp.float32(1.0))
    normals[tid] = wp.vec3(-gx / length, -gy / length, wp.float32(1.0) / length)


def compute_batch(t, params: WaveParams, origins):
  """Batched equivalent of calling core.gerstner.grid_points_and_normals_np(t, params, origin_xy) once
  per entry in `origins` -- returns a list of (points, normals) numpy float32 arrays, same order as
  `origins`, each shaped (resolution*resolution, 3), i.e. exactly what the caller would have gotten
  calling the per-tile function in a loop, just computed together in ONE kernel launch + ONE
  device-to-host copy instead of one of each per tile. Only call this when GPU_AVAILABLE is True (no
  internal fallback -- backends/wave.py decides whether to call this or the NumPy path, this module
  doesn't second-guess that)."""
  resolution, size = params.resolution, params.size_m
  base_np = (np.arange(resolution, dtype=np.float32) / (resolution - 1) - 0.5) * size
  base = wp.array(base_np, dtype=wp.float32)
  origins_np = np.array(origins, dtype=np.float32)
  origins_wp = wp.array(origins_np, dtype=wp.vec2)

  n_tiles = len(origins)
  n = n_tiles * resolution * resolution
  points = wp.zeros(n, dtype=wp.vec3)
  normals = wp.zeros(n, dtype=wp.vec3)

  # Same per-train coefficient formulas as core.gerstner.wave_offset()/wave_height_gradient(), for all
  # 4 possible trains (WaveParams.num_waves is UI-adjustable 1-4) -- WAVE_DIRECTIONS only has 2 entries,
  # cycled via i % len(WAVE_DIRECTIONS), same as every scalar/numpy function in core.gerstner. Slots at
  # or beyond params.num_waves are never read by the kernel (its loop bound IS num_waves), left at 0
  # here only for clarity/safety, not correctness.
  amps = [0.0, 0.0, 0.0, 0.0]
  wls = [30.0, 30.0, 30.0, 30.0]   # never 0 -- unread slots still get divided by in the kernel's k=2pi/wl
  sps = [0.0, 0.0, 0.0, 0.0]
  dirs_x = [1.0, 1.0, 1.0, 1.0]
  dirs_y = [0.0, 0.0, 0.0, 0.0]
  for i in range(min(params.num_waves, 4)):
    direction = WAVE_DIRECTIONS[i % len(WAVE_DIRECTIONS)]
    amps[i] = params.amplitude_m * (1.0 - i * 0.15)
    wls[i] = params.wavelength_m * (1.0 + i * 0.3)
    sps[i] = params.speed_mps * (1.0 - i * 0.1)
    dirs_x[i] = direction[0]
    dirs_y[i] = direction[1]

  wp.launch(_gerstner_batch_kernel, dim=n, inputs=[
    base, resolution, origins_wp, float(t),
    wp.vec4(*amps), wp.vec4(*wls), wp.vec4(*sps), wp.vec4(*dirs_x), wp.vec4(*dirs_y),
    params.steepness, min(params.num_waves, 4),
  ], outputs=[points, normals])

  points_np = points.numpy()
  normals_np = normals.numpy()
  per_tile = resolution * resolution
  return [(points_np[k * per_tile:(k + 1) * per_tile], normals_np[k * per_tile:(k + 1) * per_tile])
          for k in range(n_tiles)]
