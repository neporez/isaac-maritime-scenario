import math

from maritime_scenario.core import gerstner


def test_wave_height_is_zero_at_origin_time_zero():
  params = gerstner.WaveParams()
  # at t=0, x=0, y=0: phase = -omega*0 = 0 for every train (k*(dx*0+dy*0)=0), sin(0)=0 -> height 0
  assert math.isclose(gerstner.wave_height(0.0, 0.0, 0.0, params), 0.0, abs_tol=1e-9)


def test_wave_height_bounded_by_amplitude_sum():
  params = gerstner.WaveParams(amplitude_m=1.0, num_waves=2)
  # train 0 amp=1.0, train 1 amp=1.0*(1-0.15)=0.85 -> max possible height ~1.85
  max_possible = 1.0 + 1.0 * (1 - 0.15)
  for t in (0.0, 1.3, 5.7, 12.0):
    for x, y in [(0, 0), (10, -5), (-30, 40)]:
      h = gerstner.wave_height(x, y, t, params)
      assert abs(h) <= max_possible + 1e-9


def test_wave_height_respects_origin_offset():
  params = gerstner.WaveParams()
  # height at (x,y) relative to origin (ox,oy,oz) should match height at (x-ox, y-oy) relative to
  # the default origin, shifted by oz
  h_shifted = gerstner.wave_height(15.0, 15.0, 2.0, params, origin_xyz=(10.0, 10.0, 3.0))
  h_direct = gerstner.wave_height(5.0, 5.0, 2.0, params, origin_xyz=(0.0, 0.0, 0.0))
  assert math.isclose(h_shifted, h_direct + 3.0, abs_tol=1e-9)


def test_wave_slope_zero_for_flat_water():
  params = gerstner.WaveParams(amplitude_m=0.0)   # zero amplitude -> perfectly flat
  slope_x, slope_y = gerstner.wave_slope(5.0, -3.0, 1.0, params)
  assert math.isclose(slope_x, 0.0, abs_tol=1e-9)
  assert math.isclose(slope_y, 0.0, abs_tol=1e-9)


def test_wave_height_gradient_zero_for_flat_water():
  params = gerstner.WaveParams(amplitude_m=0.0)
  gx, gy = gerstner.wave_height_gradient(5.0, -3.0, 1.0, params)
  assert gx == 0.0 and gy == 0.0


def test_wave_normal_is_straight_up_for_flat_water():
  params = gerstner.WaveParams(amplitude_m=0.0)
  nx, ny, nz = gerstner.wave_normal(12.0, 7.0, 3.0, params)
  assert math.isclose(nx, 0.0, abs_tol=1e-9)
  assert math.isclose(ny, 0.0, abs_tol=1e-9)
  assert math.isclose(nz, 1.0, abs_tol=1e-9)


def test_wave_normal_is_a_unit_vector():
  params = gerstner.WaveParams(amplitude_m=1.5, steepness=0.5)
  for x, y, t in [(0.0, 0.0, 0.0), (23.0, -11.0, 4.5), (-300.0, 150.0, 12.0)]:
    nx, ny, nz = gerstner.wave_normal(x, y, t, params)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    assert math.isclose(length, 1.0, abs_tol=1e-9)


def test_wave_height_gradient_matches_finite_difference():
  # cross-check the analytic (closed-form) gradient against a numeric central difference of
  # wave_height() itself -- same quantity, two different ways of computing it, should agree closely.
  params = gerstner.WaveParams(amplitude_m=1.2, wavelength_m=18.0, steepness=0.4, num_waves=2)
  x, y, t = 37.0, -14.0, 6.3
  eps = 1e-4
  numeric_gx = (gerstner.wave_height(x + eps, y, t, params) - gerstner.wave_height(x - eps, y, t, params)) / (2 * eps)
  numeric_gy = (gerstner.wave_height(x, y + eps, t, params) - gerstner.wave_height(x, y - eps, t, params)) / (2 * eps)
  gx, gy = gerstner.wave_height_gradient(x, y, t, params)
  assert math.isclose(gx, numeric_gx, abs_tol=1e-3)
  assert math.isclose(gy, numeric_gy, abs_tol=1e-3)


def test_wave_normal_respects_origin_offset():
  params = gerstner.WaveParams(amplitude_m=1.0, steepness=0.5)
  n_shifted = gerstner.wave_normal(15.0, 15.0, 2.0, params, origin_xyz=(10.0, 10.0, 3.0))
  n_direct = gerstner.wave_normal(5.0, 5.0, 2.0, params, origin_xyz=(0.0, 0.0, 0.0))
  for a, b in zip(n_shifted, n_direct):
    assert math.isclose(a, b, abs_tol=1e-9)   # the oz component of origin_xyz doesn't affect slope/normal


def test_grid_normals_matches_per_point_wave_normal():
  resolution, size, t = 5, 40.0, 2.5
  params = gerstner.WaveParams(amplitude_m=1.0, steepness=0.4, resolution=resolution, size_m=size)
  normals = gerstner.grid_normals(t, params, origin_xy=(100.0, -50.0))
  assert len(normals) == resolution * resolution
  # spot-check one grid point directly
  i, j = 3, 1
  base_x = (i / (resolution - 1) - 0.5) * size
  base_y = (j / (resolution - 1) - 0.5) * size
  expected = gerstner.wave_normal(base_x + 100.0, base_y - 50.0, t, params)
  got = normals[i * resolution + j]
  for a, b in zip(got, expected):
    assert math.isclose(a, b, abs_tol=1e-9)


def test_grid_normals_continuous_across_tile_boundary():
  # The actual payoff: two "tiles" (different origin_xy) sampling the SAME world point must compute the
  # IDENTICAL normal, or the renderer shows a lighting seam at the shared boundary even though vertex
  # positions already match (found live, 2026-10-30, after the position bug was already fixed).
  resolution, size, t = 5, 40.0, 1.7
  params = gerstner.WaveParams(amplitude_m=1.0, steepness=0.5, resolution=resolution, size_m=size)
  # tile_a centered at (0,0): its right edge (i=resolution-1) sits at world x=+20
  # tile_b centered at (40,0): its left edge (i=0) sits at world x=40-20=+20 -- same world point
  normals_a = gerstner.grid_normals(t, params, origin_xy=(0.0, 0.0))
  normals_b = gerstner.grid_normals(t, params, origin_xy=(40.0, 0.0))
  for j in range(resolution):
    edge_a = normals_a[(resolution - 1) * resolution + j]
    edge_b = normals_b[0 * resolution + j]
    for a, b in zip(edge_a, edge_b):
      assert math.isclose(a, b, abs_tol=1e-9)


def test_build_topology_triangle_count():
  resolution = 5
  counts, indices = gerstner.build_topology(resolution)
  n_quads = (resolution - 1) ** 2
  assert len(counts) == n_quads * 2          # 2 triangles per quad
  assert all(c == 3 for c in counts)
  assert len(indices) == n_quads * 2 * 3
  assert max(indices) < resolution * resolution   # every index is a valid grid point


def test_grid_points_count_and_center():
  resolution = 9
  params = gerstner.WaveParams(amplitude_m=0.0, resolution=resolution, size_m=80.0)   # flat, easy to check
  points = gerstner.grid_points(t=0.0, params=params)
  assert len(points) == resolution * resolution
  xs = [p[0] for p in points]
  assert math.isclose(min(xs), -40.0, abs_tol=1e-9)
  assert math.isclose(max(xs), 40.0, abs_tol=1e-9)
  # flat water (amplitude 0) -> every z is exactly 0
  assert all(math.isclose(p[2], 0.0, abs_tol=1e-9) for p in points)


def test_grid_points_default_origin_matches_no_origin_arg():
  # backward compatibility: origin_xy=(0,0) (the default) must reproduce the exact pre-existing output.
  params = gerstner.WaveParams(resolution=7, size_m=60.0)
  assert gerstner.grid_points(2.5, params) == gerstner.grid_points(2.5, params, origin_xy=(0.0, 0.0))


def test_grid_points_origin_keeps_phase_consistent_with_wave_height():
  # the whole point of origin_xy: a grid centered away from (0,0) must show the SAME continuous wave
  # field a direct wave_height() call would give at the matching WORLD (x, y) -- this is what keeps
  # adjacent tiles' crests/troughs aligned at a shared boundary instead of each one restarting its own
  # phase at local (0,0).
  resolution, size = 5, 40.0
  params = gerstner.WaveParams(resolution=resolution, size_m=size)
  origin_x, origin_y, t = 100.0, -50.0, 3.0
  points = gerstner.grid_points(t, params, origin_xy=(origin_x, origin_y))
  # grid index (resolution-1, (resolution-1)//2) -> local_x = +size/2, local_y = 0 (undisplaced)
  i, j = resolution - 1, (resolution - 1) // 2
  local_x = (i / (resolution - 1) - 0.5) * size
  local_y = (j / (resolution - 1) - 0.5) * size
  _, _, z = points[i * resolution + j]
  z_direct = gerstner.wave_height(origin_x + local_x, origin_y + local_y, t, params)
  assert math.isclose(z, z_direct, abs_tol=1e-9)


def test_grid_points_origin_does_not_shift_output_xy():
  # origin_xy shifts the PHASE used to compute displacement, not the output point positions themselves
  # -- output points stay in the grid's own local frame (the prim's own world transform is what
  # positions a tile in the scene, same as everything else in this codebase).
  params = gerstner.WaveParams(amplitude_m=0.0, resolution=5, size_m=40.0)   # flat -> no displacement
  pts_no_origin = gerstner.grid_points(0.0, params)
  pts_with_origin = gerstner.grid_points(0.0, params, origin_xy=(500.0, -200.0))
  assert pts_no_origin == pts_with_origin


def test_single_tile_layout_matches_todays_single_mesh_defaults():
  params = gerstner.WaveParams(size_m=123.0, resolution=17)
  layout = gerstner.single_tile_layout(params)
  assert layout == [gerstner.WaveTileSpec("main", 0.0, 0.0, 123.0, 17)]


def test_grid_tile_layout_count_and_spacing():
  tiles = gerstner.grid_tile_layout(nx=3, ny=2, tile_size_m=50.0, tile_resolution=8)
  assert len(tiles) == 6
  assert {t.tile_id for t in tiles} == {f"tile_{i}_{j}" for i in range(3) for j in range(2)}
  assert all(t.size_m == 50.0 and t.resolution == 8 for t in tiles)
  xs = sorted({t.center_x for t in tiles})
  assert xs == [-50.0, 0.0, 50.0]   # edge-to-edge, no gap/overlap, centered as a whole on (0,0)
  ys = sorted({t.center_y for t in tiles})
  assert ys == [-25.0, 25.0]


def test_grid_tile_layout_single_tile_is_centered_at_origin():
  tiles = gerstner.grid_tile_layout(nx=1, ny=1, tile_size_m=200.0, tile_resolution=10)
  assert tiles == [gerstner.WaveTileSpec("tile_0_0", 0.0, 0.0, 200.0, 10)]


def test_matching_vertex_spacing_eliminates_boundary_gaps_between_different_tile_sizes():
  # The general property behind ui/tile_placement.py's resolution_for_level() (found live, 2026-10-30:
  # a bigger tile placed adjacent to two smaller tiles showed real triangular gaps at the shared
  # boundary, not just a shading seam -- a classic T-junction crack). If two tiles use the SAME vertex
  # spacing (size_m / (resolution - 1)), a bigger tile's edge contains EXACTLY the same world (y, z)
  # points that two smaller tiles covering the same span, placed edge-to-edge, produce -- not merely
  # close, the identical set -- so there's never a missing vertex exactly where the smaller tiles' own
  # shared boundary meets the bigger tile.
  t = 3.0
  spacing = 2.0
  small_size, big_size = 32.0, 64.0
  res_small = int(round(small_size / spacing)) + 1    # 17
  res_big = int(round(big_size / spacing)) + 1         # 33
  params_small = gerstner.WaveParams(amplitude_m=1.0, steepness=0.4, size_m=small_size, resolution=res_small)
  params_big = gerstner.WaveParams(amplitude_m=1.0, steepness=0.4, size_m=big_size, resolution=res_big)

  # big tile centered at (0, 0): right edge (i=res_big-1) sits at world x=+32, y spanning [-32, 32].
  pts_big = gerstner.grid_points(t, params_big, origin_xy=(0.0, 0.0))
  big_edge = {(round(pts_big[(res_big - 1) * res_big + j][1], 6), round(pts_big[(res_big - 1) * res_big + j][2], 6))
              for j in range(res_big)}

  # two small tiles adjacent to the big tile's right edge, stacked to cover the same [-32, 32] span:
  # C centered at (48, 16) spans y in [0, 32], D centered at (48, -16) spans y in [-32, 0]. Their LEFT
  # edges (i=0) both sit at world x=+32.
  pts_c = gerstner.grid_points(t, params_small, origin_xy=(48.0, 16.0))
  pts_d = gerstner.grid_points(t, params_small, origin_xy=(48.0, -16.0))
  small_edge = set()
  for j in range(res_small):
    lx, ly, z = pts_c[0 * res_small + j]
    small_edge.add((round(ly + 16.0, 6), round(z, 6)))
  for j in range(res_small):
    lx, ly, z = pts_d[0 * res_small + j]
    small_edge.add((round(ly - 16.0, 6), round(z, 6)))

  assert len(big_edge) == res_big
  assert len(small_edge) == res_small * 2 - 1 == res_big   # one point (y=0) shared between C and D
  assert big_edge == small_edge


# ------------------------------------------------------------ vectorized (numpy) grid math


def test_grid_points_and_normals_np_matches_scalar_functions_exactly():
  # Regression/equivalence test for the 2026-10-30 performance rewrite: grid_points_and_normals_np()
  # must produce the SAME values (within float32 precision) as calling the scalar grid_points() and
  # grid_normals() separately -- this is what lets every pre-existing correctness property (crack
  # elimination, boundary normal matching) carry over to the faster path without re-deriving them.
  cases = [
    dict(amplitude_m=0.0, steepness=0.25, num_waves=2, size_m=40.0, resolution=9, origin=(0.0, 0.0), t=1.0),
    dict(amplitude_m=0.8, steepness=0.25, num_waves=2, size_m=400.0, resolution=33, origin=(0.0, 0.0), t=3.0),
    dict(amplitude_m=1.5, steepness=0.6, num_waves=3, size_m=128.0, resolution=17, origin=(48.0, -16.0), t=7.5),
    dict(amplitude_m=1.0, steepness=0.4, num_waves=1, size_m=64.0, resolution=17, origin=(-1000.0, 2000.0), t=12.3),
  ]
  for case in cases:
    params = gerstner.WaveParams(amplitude_m=case["amplitude_m"], steepness=case["steepness"],
                                  num_waves=case["num_waves"], size_m=case["size_m"],
                                  resolution=case["resolution"])
    pts_scalar = gerstner.grid_points(case["t"], params, origin_xy=case["origin"])
    nrm_scalar = gerstner.grid_normals(case["t"], params, origin_xy=case["origin"])
    pts_np, nrm_np = gerstner.grid_points_and_normals_np(case["t"], params, origin_xy=case["origin"])

    n = case["resolution"] ** 2
    assert pts_np.shape == (n, 3)
    assert nrm_np.shape == (n, 3)
    for k in range(n):
      for c in range(3):
        assert math.isclose(float(pts_np[k][c]), pts_scalar[k][c], abs_tol=1e-4), case
        assert math.isclose(float(nrm_np[k][c]), nrm_scalar[k][c], abs_tol=1e-5), case


def test_grid_points_and_normals_np_preserves_crack_elimination():
  # Same property as test_matching_vertex_spacing_eliminates_boundary_gaps_between_different_tile_sizes
  # above, re-checked for the vectorized function specifically -- this is the property that actually
  # matters for live rendering, so it must survive the performance rewrite, not just "run without error".
  t = 3.0
  spacing = 2.0
  small_size, big_size = 32.0, 64.0
  res_small = int(round(small_size / spacing)) + 1
  res_big = int(round(big_size / spacing)) + 1
  params_small = gerstner.WaveParams(amplitude_m=1.0, steepness=0.4, size_m=small_size, resolution=res_small)
  params_big = gerstner.WaveParams(amplitude_m=1.0, steepness=0.4, size_m=big_size, resolution=res_big)

  pts_big, _ = gerstner.grid_points_and_normals_np(t, params_big, origin_xy=(0.0, 0.0))
  pts_c, _ = gerstner.grid_points_and_normals_np(t, params_small, origin_xy=(48.0, 16.0))
  pts_d, _ = gerstner.grid_points_and_normals_np(t, params_small, origin_xy=(48.0, -16.0))

  # tile_big's right edge (i=res_big-1) vs tile C/D's left edge (i=0), matched by INDEX (not by
  # rounding to a dict key, which is too sensitive once values are float32 -- see this test's own
  # commit message/CLAUDE.md note on why): tile C covers the upper half (j=0..res_small-1 -> world y
  # 0..32), tile D the lower half (world y -32..0), together spanning the same res_big points tile_big's
  # edge has, in order.
  small_z_in_order = [float(pts_d[0 * res_small + j][2]) for j in range(res_small - 1)] + \
                      [float(pts_c[0 * res_small + j][2]) for j in range(res_small)]
  big_z_in_order = [float(pts_big[(res_big - 1) * res_big + j][2]) for j in range(res_big)]

  assert len(small_z_in_order) == len(big_z_in_order) == res_big
  for a, b in zip(small_z_in_order, big_z_in_order):
    assert math.isclose(a, b, abs_tol=1e-4)


def test_grid_points_and_normals_np_preserves_boundary_normal_matching():
  t = 1.7
  resolution, size = 5, 40.0
  params = gerstner.WaveParams(amplitude_m=1.0, steepness=0.5, resolution=resolution, size_m=size)
  _, nrm_a = gerstner.grid_points_and_normals_np(t, params, origin_xy=(0.0, 0.0))
  _, nrm_b = gerstner.grid_points_and_normals_np(t, params, origin_xy=(size, 0.0))
  for j in range(resolution):
    na = nrm_a[(resolution - 1) * resolution + j]
    nb = nrm_b[0 * resolution + j]
    for c in range(3):
      assert math.isclose(float(na[c]), float(nb[c]), abs_tol=1e-5)
