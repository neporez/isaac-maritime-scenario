"""
Independent damped-oscillator heave/roll/pitch ship-motion model -- naval-architecture style, not a
4-probe spring scheme. Extracted verbatim (same formulas, same rule-of-thumb coefficients) from
boat_mesh_pipeline/06_scenario_editor's float_mesh_test.py and other_boats.py, which both duplicated
this exact math. Pure Python + math only, no omni/pxr -- this is what makes it unit-testable outside
Isaac Sim at all, which the original scattered-script version never was.

Model, per frame while playing (the per-frame force/torque application itself lives in the ship backend,
which calls into this module for the target values and gains):
  - heave:  target_z = (resting CoM height for the current draft) + wave height at the boat's (x,y).
            F_z = k_heave*(target_z - z) - c_heave*v_z, applied at the CoM (no torque).
  - roll:   target_roll = atan(local wave SLOPE in the beam direction) -- the water surface's own tilt
            right under the hull, sampled at the CoM's (x,y), not from any point fixed to the (possibly
            heeled-over) hull -- so this stays meaningful at any roll angle.
            torque = k_roll*(target_roll - roll) - c_roll*roll_rate, about the hull's own forward axis.
  - pitch:  same idea with the slope in the forward direction, torque about the hull's own beam axis.
  - k_heave = rho_water * g * waterplane_area (standard hydrostatic heave stiffness).
  - k_roll  = mass * g * GM, k_pitch = mass * g * GML (GM/GML = transverse/longitudinal metacentric
    height). Ixx/Iyy/Izz from naval rule-of-thumb radii of gyration. GM, GML, and the gyration ratios
    are rule-of-thumb constants, NOT validated naval figures for any specific hull -- real values come
    from a hull-form calculation this project doesn't have.
  - mass is derived from draft (not an arbitrary density): mass = rho_water * waterplane_area * draft,
    i.e. "whatever draft the ratio asks for is already the hydrostatic equilibrium".
"""
import math
from dataclasses import dataclass


GRAVITY = 9.81
RHO_WATER = 1000.0   # kg/m^3, real seawater is closer to 1025 but this project's own convention uses 1000


@dataclass(frozen=True)
class ShipPhysicsCoeffs:
  """Rule-of-thumb naval-architecture coefficients -- tunable, not validated for any specific hull.
  Matches 06_scenario_editor's PHYSICS_COEFFS exactly (same names/defaults/ranges), just grouped into
  one dataclass instead of loose module globals shared across two files by hand."""
  draft_to_beam_ratio: float = 0.05    # T/B -- typical small-craft range ~0.3-0.45, but lowered to 0.05
                                        # from the earlier 0.1 default (2026-10-31, user's own call: a
                                        # better match for this project's own boats)
  waterplane_coeff: float = 0.75       # waterplane area ~= this x length x beam
  gm_coeff: float = 0.15               # transverse metacentric height ~= this x beam
  gml_coeff: float = 0.6               # longitudinal metacentric height ~= this x length (always >> GM)
  roll_gyration_coeff: float = 0.35    # roll radius of gyration ~= this x beam
  pitch_gyration_coeff: float = 0.25   # pitch radius of gyration ~= this x length
  yaw_gyration_coeff: float = 0.25     # yaw radius of gyration ~= this x length
  damping_ratio: float = 0.4           # 0 = bounces forever, 1 = no bobbing


@dataclass(frozen=True)
class ShipPhysicsParams:
  """Derived, ready-to-apply physics for one hull of size (dx, dy, dz) under some ShipPhysicsCoeffs --
  what set_mesh_source()/recompute_ship_physics()/_spawn_one() in the original scripts all computed
  inline, now a single pure function (derive()) returning a plain dataclass."""
  draft_m: float
  mass_kg: float
  ixx: float
  iyy: float
  izz: float
  k_heave: float
  c_heave: float
  k_roll: float
  c_roll: float
  k_pitch: float
  c_pitch: float
  max_force_n: float
  max_torque_nm: float


def target_draft(beam_m, height_m, ratio):
  """Draft can't exceed 0.8x the hull height (a boat can't sink past its own deck) or go below 1cm."""
  return max(min(ratio * beam_m, 0.8 * height_m), 0.01)


def derive(length_m, beam_m, height_m, coeffs: ShipPhysicsCoeffs) -> ShipPhysicsParams:
  """dx=length, dy=beam, dz=height (box-local convention this whole project uses). Same formula as
  float_mesh_test.py's set_mesh_source()/recompute_ship_physics() and other_boats.py's
  _ob_ship_motion_params(), unified into one function both the ship and vessel backends call."""
  draft = target_draft(beam_m, height_m, coeffs.draft_to_beam_ratio)
  waterplane_area = coeffs.waterplane_coeff * length_m * beam_m
  mass = RHO_WATER * waterplane_area * draft
  gm = coeffs.gm_coeff * beam_m
  gml = coeffs.gml_coeff * length_m
  ixx = mass * (coeffs.roll_gyration_coeff * beam_m) ** 2
  iyy = mass * (coeffs.pitch_gyration_coeff * length_m) ** 2
  izz = mass * (coeffs.yaw_gyration_coeff * length_m) ** 2
  k_heave = RHO_WATER * GRAVITY * waterplane_area
  c_heave = 2.0 * coeffs.damping_ratio * math.sqrt(k_heave * mass)
  k_roll = mass * GRAVITY * gm
  c_roll = 2.0 * coeffs.damping_ratio * math.sqrt(k_roll * ixx)
  k_pitch = mass * GRAVITY * gml
  c_pitch = 2.0 * coeffs.damping_ratio * math.sqrt(k_pitch * iyy)
  max_force = 50.0 * mass
  max_torque = 50.0 * mass * max(length_m, beam_m)
  return ShipPhysicsParams(
    draft_m=draft, mass_kg=mass, ixx=ixx, iyy=iyy, izz=izz,
    k_heave=k_heave, c_heave=c_heave, k_roll=k_roll, c_roll=c_roll, k_pitch=k_pitch, c_pitch=c_pitch,
    max_force_n=max_force, max_torque_nm=max_torque,
  )


def heave_force(params: ShipPhysicsParams, z, target_z, v_z):
  """F_z = k*(target - z) - c*v -- the heave spring-damper, PhysX force units (Newtons)."""
  return params.k_heave * (target_z - z) - params.c_heave * v_z


def heave_pitch_roll_targets(bow_h, stern_h, plus_beam_h, minus_beam_h, length_m, beam_m):
  """Fits a rigid plank to 4 wave-height probes fixed to the hull's own bow/stern/port/starboard, in
  the hull's own forward/beam directions (not world x/y) -- replaces sampling the water's instantaneous
  slope at a single point under the hull's CENTER and applying that angle to the whole rigid body.

  That single-point-slope approach (this project's original design, ship.py/vessels.py's old
  wave_backend.slope_at() call) has two bugs for anything longer than a small dinghy:
    1. It samples in WORLD x/y, not the hull's own forward/beam -- so "pitch" and "roll" silently swap
       meaning whenever the hull yaws away from world-axis-aligned.
    2. Applying a point-sampled angle as a RIGID rotation about the center means the ends of a long hull
       swing by ~half_length*sin(angle) -- e.g. a 20m hull pitching 10 deg (an ordinary wave-slope
       magnitude) puts the bow ~1.7m deeper than its heave draft alone would ever predict, independent of
       how low draft_to_beam_ratio is set. This is what "draft 조절해도 똑같이 잠긴다" (2026-10-30) turned
       out to be: not a draft/mass tuning problem, a target-angle-derivation problem.

  Probing at the hull's actual bow/stern/port/starboard and fitting a plane through those 4 points fixes
  both: the probes are already in the hull's own forward/beam frame, and a hull much longer than the
  wave's wavelength naturally averages across multiple crests (the way a real ship rides swell) instead
  of pivoting to match the water's tilt under one point. Each central-difference slope (e.g.
  (bow_h-stern_h)/length_m) is the same atan()-based formula the old point-slope code used, just with the
  finite-difference step widened from a fixed small epsilon to the hull's own length/beam and reoriented
  into the hull's own axes -- so this is a drop-in replacement for the (target_roll, target_pitch) pair,
  plus an extra averaged-height term for heave that was previously just the single center-point height.
  NOT a 4-probe SPRING scheme (each probe its own spring/damper) -- still one heave spring and one
  roll/pitch torque pair per hull, just driven by a better target."""
  avg_h = (bow_h + stern_h + plus_beam_h + minus_beam_h) / 4.0
  target_pitch = math.atan2(bow_h - stern_h, length_m) if length_m > 1e-6 else 0.0
  target_roll = math.atan2(plus_beam_h - minus_beam_h, beam_m) if beam_m > 1e-6 else 0.0
  return avg_h, target_pitch, target_roll


def roll_torque(params: ShipPhysicsParams, roll_rad, target_roll_rad, roll_rate):
  return params.k_roll * (target_roll_rad - roll_rad) - params.c_roll * roll_rate


def pitch_torque(params: ShipPhysicsParams, pitch_rad, target_pitch_rad, pitch_rate):
  return params.k_pitch * (target_pitch_rad - pitch_rad) - params.c_pitch * pitch_rate


def clamp_vector_length(vx, vy, vz, max_length):
  """Clamps a 3-vector to at most max_length, preserving direction -- same clamp float_mesh_test.py/
  other_boats.py apply to total_force/total_torque before pushing them into PhysxForceAPI."""
  length = math.sqrt(vx * vx + vy * vy + vz * vz)
  if length <= max_length or length == 0.0:
    return vx, vy, vz
  scale = max_length / length
  return vx * scale, vy * scale, vz * scale


# ─────────────────────────────────────────────────────────────────────────────
# follow_path steering -- PD controller on yaw error, extracted from other_boats.py's _on_ob_update()
# follow_path block. Pure math: given the current pose/waypoint, returns a steer value in [-1, 1] (0 =
# no turn, +-1 = full turn_torque) and whether this waypoint has been reached (caller advances the index).
# ─────────────────────────────────────────────────────────────────────────────
WAYPOINT_RADIUS_M = 20.0   # see note below -- bumped from an earlier 5.0
# Bumped from 5.0 (06_scenario_editor, user: "turns too sharply right at the waypoint, doesn't move like
# a boat") -- with a small radius the boat steers straight at a point almost until it's on top of it,
# then the target bearing swings fast in the final few meters, producing a last-second snap turn. A
# bigger radius switches to the NEXT waypoint well before actually reaching this one, so the turn happens
# gradually while still approaching, more like a real vessel cutting a corner.
PATH_TURN_FULL_ERROR_DEG = 45.0    # yaw error at/beyond which follow_path steers at full turn_torque
PATH_TURN_RATE_FULL_DEG_S = 30.0   # yaw-rate damping scale, makes this a PD (not just P) controller


def has_arrived(x, y, waypoint_xy, radius_m=WAYPOINT_RADIUS_M):
  tx, ty = waypoint_xy
  return math.hypot(tx - x, ty - y) < radius_m


def path_steer(x, y, current_yaw_deg, waypoint_xy, prev_yaw_deg, dt):
  """Returns (steer, desired_yaw_deg) -- steer in [-1, 1], PD on yaw error same as other_boats.py's
  follow_path block (proportional term alone overshoots/oscillates; damping against the current turn
  rate eases off as it approaches alignment, same K/C spirit as the heave/roll/pitch springs)."""
  tx, ty = waypoint_xy
  desired_yaw = math.degrees(math.atan2(ty - y, tx - x))
  yaw_error = (desired_yaw - current_yaw_deg + 180.0) % 360.0 - 180.0   # wrap to [-180, 180]
  if prev_yaw_deg is None or dt <= 0.0:
    yaw_rate = 0.0
  else:
    yaw_rate = ((current_yaw_deg - prev_yaw_deg + 180.0) % 360.0 - 180.0) / dt
  steer = yaw_error / PATH_TURN_FULL_ERROR_DEG - yaw_rate / PATH_TURN_RATE_FULL_DEG_S
  steer = max(-1.0, min(1.0, steer))
  return steer, desired_yaw
