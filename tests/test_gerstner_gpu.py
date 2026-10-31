"""gerstner_gpu.compute_batch() against the always-available scalar/numpy core.gerstner path --
skipped (not failed) on a machine/environment without a real CUDA device, since gerstner_gpu is a purely
additive optional fast path (see its own module docstring + CLAUDE.md for the measured numbers that
motivated it). This sandbox has a real GPU, so these actually execute here, not just skip."""
import numpy as np
import pytest

from maritime_scenario.core import gerstner, gerstner_gpu

pytestmark = pytest.mark.skipif(not gerstner_gpu.GPU_AVAILABLE, reason="no CUDA device available")


def test_compute_batch_matches_scalar_numpy_path_for_a_single_origin():
  params = gerstner.WaveParams(resolution=9, size_m=80.0)
  origins = [(0.0, 0.0)]
  (gpu_points, gpu_normals), = gerstner_gpu.compute_batch(3.0, params, origins)
  ref_points, ref_normals = gerstner.grid_points_and_normals_np(3.0, params, origin_xy=(0.0, 0.0))
  assert np.max(np.abs(gpu_points - ref_points)) < 1e-4
  assert np.max(np.abs(gpu_normals - ref_normals)) < 1e-4


def test_compute_batch_matches_scalar_numpy_path_for_multiple_origins():
  params = gerstner.WaveParams(amplitude_m=1.2, steepness=0.35, resolution=17, size_m=64.0)
  origins = [(0.0, 0.0), (300.0, -150.0), (-640.0, 820.0)]
  results = gerstner_gpu.compute_batch(5.5, params, origins)
  assert len(results) == len(origins)
  for (ox, oy), (gpu_points, gpu_normals) in zip(origins, results):
    ref_points, ref_normals = gerstner.grid_points_and_normals_np(5.5, params, origin_xy=(ox, oy))
    assert np.max(np.abs(gpu_points - ref_points)) < 1e-4
    assert np.max(np.abs(gpu_normals - ref_normals)) < 1e-4


@pytest.mark.parametrize("num_waves", [1, 2, 3, 4])
def test_compute_batch_correct_for_every_num_waves_slider_value(num_waves):
  # WaveParams.num_waves is UI-adjustable 1-4 (ui/window.py's "Number of wave trains" slider) -- the
  # kernel's loop bound is num_waves itself, so this must stay correct at every value the slider allows,
  # not just the default. Regression target: an earlier draft of the kernel hardcoded only 2 trains.
  params = gerstner.WaveParams(
    amplitude_m=1.2, steepness=0.35, resolution=13, size_m=64.0, num_waves=num_waves,
    wavelength_m=20.0, speed_mps=3.0,
  )
  origins = [(0.0, 0.0), (120.0, -40.0)]
  results = gerstner_gpu.compute_batch(2.5, params, origins)
  for (ox, oy), (gpu_points, gpu_normals) in zip(origins, results):
    ref_points, ref_normals = gerstner.grid_points_and_normals_np(2.5, params, origin_xy=(ox, oy))
    assert np.max(np.abs(gpu_points - ref_points)) < 1e-4
    assert np.max(np.abs(gpu_normals - ref_normals)) < 1e-4


def test_compute_batch_output_shapes():
  params = gerstner.WaveParams(resolution=11, size_m=40.0)
  origins = [(0.0, 0.0), (50.0, 50.0), (100.0, -100.0), (150.0, 0.0)]
  results = gerstner_gpu.compute_batch(1.0, params, origins)
  assert len(results) == 4
  for points, normals in results:
    assert points.shape == (11 * 11, 3)
    assert normals.shape == (11 * 11, 3)
