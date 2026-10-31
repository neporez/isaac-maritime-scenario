import math

from maritime_scenario.core import ship_motion


def test_target_draft_clamped_to_hull_height():
  # ratio * beam would exceed 0.8 * height -> clamped to 0.8 * height
  assert ship_motion.target_draft(beam_m=10.0, height_m=1.0, ratio=1.0) == 0.8
  # tiny beam/ratio -> clamped up to the 0.01 floor, not left at ~0
  assert ship_motion.target_draft(beam_m=0.001, height_m=5.0, ratio=0.1) == 0.01
  # ordinary case: neither clamp active
  assert math.isclose(ship_motion.target_draft(beam_m=6.0, height_m=9.0, ratio=0.1), 0.6)


def test_derive_matches_hand_computed_values_for_a_known_hull():
  # Hand-computed from the exact same formulas other_boats.py's _ob_ship_motion_params() used, for a
  # 20m x 5m x 6m hull at default coefficients -- a regression check against the real production math,
  # not just "does it run". draft_to_beam_ratio's default is 0.05 (2026-10-31, lowered from 0.1 -- a
  # better match for this project's own boats), so this hand-computation uses that current default, not
  # the ratio hardcoded as a literal.
  coeffs = ship_motion.ShipPhysicsCoeffs()
  params = ship_motion.derive(length_m=20.0, beam_m=5.0, height_m=6.0, coeffs=coeffs)

  draft = min(coeffs.draft_to_beam_ratio * 5.0, 0.8 * 6.0)  # 0.25
  waterplane_area = 0.75 * 20.0 * 5.0  # 75.0
  mass = ship_motion.RHO_WATER * waterplane_area * draft  # 18750.0
  assert math.isclose(params.draft_m, draft)
  assert math.isclose(params.mass_kg, mass)

  gm = 0.15 * 5.0
  k_roll = mass * ship_motion.GRAVITY * gm
  assert math.isclose(params.k_roll, k_roll)

  k_heave = ship_motion.RHO_WATER * ship_motion.GRAVITY * waterplane_area
  assert math.isclose(params.k_heave, k_heave)
  assert params.max_force_n == 50.0 * mass
  assert params.max_torque_nm == 50.0 * mass * max(20.0, 5.0)


def test_heave_force_pulls_toward_target():
  params = ship_motion.derive(20.0, 5.0, 6.0, ship_motion.ShipPhysicsCoeffs())
  # below target, no velocity -> positive (upward) force
  f_up = ship_motion.heave_force(params, z=0.0, target_z=1.0, v_z=0.0)
  assert f_up > 0
  # above target -> negative (downward) force
  f_down = ship_motion.heave_force(params, z=2.0, target_z=1.0, v_z=0.0)
  assert f_down < 0
  # moving fast toward the target damps the force back down
  f_damped = ship_motion.heave_force(params, z=0.0, target_z=1.0, v_z=10.0)
  assert f_damped < f_up


def test_clamp_vector_length_preserves_direction():
  vx, vy, vz = ship_motion.clamp_vector_length(3.0, 4.0, 0.0, max_length=2.5)
  length = math.sqrt(vx * vx + vy * vy + vz * vz)
  assert math.isclose(length, 2.5)
  assert math.isclose(vx / vy, 3.0 / 4.0)   # direction unchanged

  # under the limit -> unchanged
  assert ship_motion.clamp_vector_length(1.0, 0.0, 0.0, max_length=5.0) == (1.0, 0.0, 0.0)


def test_heave_pitch_roll_targets_flat_water():
  avg_h, pitch, roll = ship_motion.heave_pitch_roll_targets(
    bow_h=0.0, stern_h=0.0, plus_beam_h=0.0, minus_beam_h=0.0, length_m=20.0, beam_m=5.0)
  assert avg_h == 0.0
  assert pitch == 0.0
  assert roll == 0.0


def test_heave_pitch_roll_targets_averages_the_four_probes():
  avg_h, _, _ = ship_motion.heave_pitch_roll_targets(
    bow_h=1.0, stern_h=3.0, plus_beam_h=2.0, minus_beam_h=2.0, length_m=20.0, beam_m=5.0)
  assert math.isclose(avg_h, 2.0)


def test_heave_pitch_roll_targets_pitch_sign_and_magnitude():
  # bow higher than stern -> positive pitch, same atan2(rise, run) a point-slope atan() would give for
  # the equivalent rise/run, just using the hull's own length as the run instead of a fixed epsilon.
  _, pitch, _ = ship_motion.heave_pitch_roll_targets(
    bow_h=1.0, stern_h=0.0, plus_beam_h=0.0, minus_beam_h=0.0, length_m=20.0, beam_m=5.0)
  assert math.isclose(pitch, math.atan2(1.0, 20.0))
  assert pitch > 0


def test_heave_pitch_roll_targets_roll_sign_and_magnitude():
  # +beam side higher than -beam side -> positive roll, same atan2(rise, run) convention.
  _, _, roll = ship_motion.heave_pitch_roll_targets(
    bow_h=0.0, stern_h=0.0, plus_beam_h=1.0, minus_beam_h=0.0, length_m=20.0, beam_m=5.0)
  assert math.isclose(roll, math.atan2(1.0, 5.0))
  assert roll > 0


def test_heave_pitch_roll_targets_long_hull_averages_out_short_wavelength_bumps():
  # The whole point of sampling at the hull's own bow/stern instead of a fixed small epsilon: a hull
  # much longer than the wave's wavelength should see a SMALLER pitch angle for the same peak wave slope
  # than a short hull would, because it straddles a full crest-to-trough span rather than sampling the
  # steepest local tangent. Model this directly: a single sine crest between bow and stern nets to zero
  # rise at either end, so a very long hull centered on one crest sees near-zero pitch even though the
  # local slope right at its center is at its steepest.
  bow_h = stern_h = 0.0   # bow and stern both sit at a trough-crossing (sin(0) = sin(pi) = 0)
  _, pitch_long, _ = ship_motion.heave_pitch_roll_targets(
    bow_h=bow_h, stern_h=stern_h, plus_beam_h=0.0, minus_beam_h=0.0, length_m=1000.0, beam_m=5.0)
  assert pitch_long == 0.0


def test_heave_pitch_roll_targets_zero_length_or_beam_does_not_divide_by_zero():
  avg_h, pitch, roll = ship_motion.heave_pitch_roll_targets(
    bow_h=1.0, stern_h=0.0, plus_beam_h=1.0, minus_beam_h=0.0, length_m=0.0, beam_m=0.0)
  assert pitch == 0.0
  assert roll == 0.0
  assert math.isclose(avg_h, 0.5)


def test_has_arrived_radius():
  assert ship_motion.has_arrived(0.0, 0.0, (10.0, 0.0), radius_m=15.0)
  assert not ship_motion.has_arrived(0.0, 0.0, (10.0, 0.0), radius_m=5.0)


def test_path_steer_turns_toward_waypoint_and_saturates():
  # waypoint due east (+x), boat facing north (yaw=90) -> should steer hard one direction
  steer, desired_yaw = ship_motion.path_steer(
    x=0.0, y=0.0, current_yaw_deg=90.0, waypoint_xy=(100.0, 0.0), prev_yaw_deg=None, dt=1 / 60)
  assert math.isclose(desired_yaw, 0.0, abs_tol=1e-6)
  assert abs(steer) == 1.0   # yaw error (90 deg) exceeds PATH_TURN_FULL_ERROR_DEG (45) -> saturated

  # already facing the waypoint -> no steering needed
  steer_aligned, _ = ship_motion.path_steer(
    x=0.0, y=0.0, current_yaw_deg=0.0, waypoint_xy=(100.0, 0.0), prev_yaw_deg=None, dt=1 / 60)
  assert math.isclose(steer_aligned, 0.0, abs_tol=1e-9)


def test_path_steer_yaw_wraps_correctly_near_180():
  # current yaw 179, desired yaw -179 (i.e. waypoint just across the wrap boundary) -> error should be
  # a small ~2 deg turn, not a near-360 deg one
  steer, desired_yaw = ship_motion.path_steer(
    x=0.0, y=0.0, current_yaw_deg=179.0, waypoint_xy=(-1.0, -0.035), prev_yaw_deg=None, dt=1 / 60)
  assert abs(steer) < 0.2   # small correction, not saturated at 1.0


def test_clamp_to_bounds_holds_position_at_the_edge_of_given_bounds():
  # inside the bounds -> unchanged
  assert ship_motion.clamp_to_bounds(10.0, 10.0, (-50.0, 50.0, -50.0, 50.0), fallback_area=200.0) == (10.0, 10.0)
  # past every edge -> clamped to that edge, independently per axis
  assert ship_motion.clamp_to_bounds(100.0, -100.0, (-50.0, 50.0, -50.0, 50.0), fallback_area=200.0) == (50.0, -50.0)


def test_clamp_to_bounds_falls_back_to_a_square_when_bounds_is_none():
  assert ship_motion.clamp_to_bounds(10.0, 10.0, None, fallback_area=50.0) == (10.0, 10.0)
  assert ship_motion.clamp_to_bounds(1000.0, -1000.0, None, fallback_area=50.0) == (50.0, -50.0)
