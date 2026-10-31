"""The other-vessel pool -- ported from boat_mesh_pipeline/06_scenario_editor/other_boats.py, web
meshes only (see core/meshes.py's docstring for why "ours" is dropped entirely). Two ways to populate
the scene, both usable together:
  - set_count(n) -- the original random-swarm behavior: rebuilds the whole pool at n vessels, random
    mesh/position/heading, all behavior="forward", collision-checked at spawn via core.placement.
  - spawn(vessel) -- adds exactly one Vessel at an exact pose/behavior, without touching anything
    already in the scene.

Each live vessel pairs a core.vessel.Vessel (the saveable definition) with its own runtime simulation
state (prev_z/prev_roll/prev_pitch/prev_path_yaw/wp_index/path_done, plus its measured mesh/derived
physics) -- see core/vessel.py's own docstring for why that split exists. list_vessels() reads back the
CURRENT live world pose (and the REMAINING waypoints from wp_index onward) into fresh Vessel objects,
same "what's live right now, not what was first spawned" convention other_boats.py's save_scenario()
already used.
"""
import logging
import math
import random

from ..core import placement, ship_motion
from ..core.vessel import Vessel, DEFAULT_ANGULAR_SPEED, DEFAULT_LINEAR_SPEED
from .base import Backend
from . import meshes as mesh_backend

log = logging.getLogger("maritime_scenario")

ROOT_PATH = "/World/Vessels"
AREA_M = 200.0            # fallback spawn/boundary half-extent if general.area_m isn't set yet
PLACEMENT_TRIES = 50
LINEAR_DAMPING = 0.8


class _Live:
  """Runtime state for one spawned vessel -- not saved, rebuilt fresh by _spawn_prim() every time."""
  __slots__ = ("vessel", "body_path", "measured", "params", "max_force", "max_torque", "turn_torque",
               "prev_z", "prev_roll", "prev_pitch", "prev_path_yaw", "wp_index", "path_done")


class VesselsBackend(Backend):
  name = "vessels"

  def __init__(self):
    self._live = []
    self._next_idx = 0
    self._update_sub = None
    self._boundary_enabled = True

  # ------------------------------------------------------------ Backend interface
  def build(self):
    stage = self.context.stage()
    stage.DefinePrim(ROOT_PATH, "Xform")
    self._start_update()

  def apply_state(self, state, changed):
    if "ship_physics" in changed:
      self._recompute_all_physics(state.ship_physics)

  def detach(self):
    if self._update_sub is not None:
      del self._update_sub
      self._update_sub = None

  def stats(self):
    return {"count": len(self._live)}

  # ------------------------------------------------------------ population
  def set_count(self, n):
    """Rebuilds the whole pool at exactly n vessels (random mesh/position/heading each), collision-
    checked against each other at spawn time only -- vessels can still drift into each other
    afterward as they move."""
    n = max(0, int(n))
    stage = self.context.stage()
    if stage.GetPrimAtPath(ROOT_PATH):
      stage.RemovePrim(ROOT_PATH)
    stage.DefinePrim(ROOT_PATH, "Xform")
    self._next_idx = 0
    self._live = []

    all_meshes = mesh_backend.list_all()   # never empty -- falls back to a placeholder box (see its own docstring)

    tiles = self._water_tiles()
    placed_obbs = []
    n_crowded = 0
    for _ in range(n):
      measured = random.choice(all_meshes)
      x = y = yaw_deg = 0.0
      for _try in range(PLACEMENT_TRIES):
        x, y = self._random_spawn_point(tiles)
        yaw_deg = random.uniform(0.0, 360.0)
        candidate = (x, y, measured.dx, measured.dy, math.radians(yaw_deg))
        if not any(placement.obb_overlap(candidate, other) for other in placed_obbs):
          break
      else:
        n_crowded += 1
      placed_obbs.append((x, y, measured.dx, measured.dy, math.radians(yaw_deg)))
      vessel = Vessel(mesh_id=measured.mesh_id, x=x, y=y, yaw_deg=yaw_deg, behavior="forward")
      self._live.append(self._spawn_prim(vessel))

    msg = f"maritime_scenario(vessels): now {n} vessel(s)"
    if n_crowded:
      msg += f" -- {n_crowded} couldn't find a collision-free spot after {PLACEMENT_TRIES} tries, placed anyway"
    log.info(msg)

  def _water_tiles(self):
    wave_backend = self.context.manager.get_backend("wave")
    return list(wave_backend.list_tiles().values()) if wave_backend is not None else []

  def _random_spawn_point(self, tiles):
    """Uniform-random (x, y) within one of the currently PLACED water tiles, area-weighted so a tile
    isn't picked as often as a much smaller one just because there's one of each (2026-11-02, user's own
    call: random swarm spawning used to sample a fixed `general.area_m` square independent of where
    water actually is -- fine back when water was always one classic plane covering that whole square,
    but with tile-based water the placed footprint can be smaller, bigger, or a totally different shape,
    so a boat could land on dry land). Falls back to the old fixed-square sampling if no water tile is
    placed at all yet -- still better than refusing to spawn anything."""
    if not tiles:
      area = self.context.manager.state_ref.general.area_m
      return random.uniform(-area, area), random.uniform(-area, area)
    tile = random.choices(tiles, weights=[t.size_m ** 2 for t in tiles])[0]
    half = tile.size_m / 2.0
    return (random.uniform(tile.center_x - half, tile.center_x + half),
            random.uniform(tile.center_y - half, tile.center_y + half))

  def spawn(self, vessel: Vessel):
    """Adds exactly ONE vessel on top of whatever's already in the scene -- never clears ROOT_PATH."""
    if vessel.yaw_deg is None:
      if vessel.waypoints:
        wx, wy = vessel.waypoints[0]
        vessel.yaw_deg = math.degrees(math.atan2(wy - vessel.y, wx - vessel.x))
      else:
        vessel.yaw_deg = 0.0
    live = self._spawn_prim(vessel)
    self._live.append(live)
    log.info("maritime_scenario(vessels): spawned %s at (%.1f, %.1f) yaw=%.0f behavior=%s -- %d total",
              vessel.mesh_id, vessel.x, vessel.y, vessel.yaw_deg, vessel.behavior, len(self._live))
    return vessel

  def clear(self):
    self.set_count(0)

  def list_vessels(self):
    """Current Vessel definitions, read back from LIVE world pose (not the originally-spawned one) --
    waypoints are only the REMAINING ones ahead of wp_index, same convention other_boats.py's
    save_scenario() used, so reloading resumes the path from here rather than restarting it."""
    stage = self.context.stage()
    out = []
    for live in self._live:
      body = stage.GetPrimAtPath(live.body_path)
      if not body:
        continue
      from pxr import Usd, UsdGeom, Gf
      world_tf = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
      pos = world_tf.ExtractTranslation()
      yaw_deg = world_tf.ExtractRotation().Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))[2]
      out.append(Vessel(
        mesh_id=live.vessel.mesh_id, x=pos[0], y=pos[1], yaw_deg=yaw_deg, behavior=live.vessel.behavior,
        waypoints=list(live.vessel.waypoints[live.wp_index:]),
        linear_speed=live.vessel.linear_speed, angular_speed=live.vessel.angular_speed,
        name=live.body_path.rsplit("/", 1)[-1],
      ))
    return out

  def set_boundary_enabled(self, flag):
    self._boundary_enabled = bool(flag)
    log.info("maritime_scenario(vessels): area boundary %s", "enabled" if flag else "disabled")

  def set_all_speed(self, linear=None, angular=None):
    for live in self._live:
      if linear is not None:
        live.vessel.linear_speed = float(linear)
      if angular is not None:
        live.vessel.angular_speed = float(angular)
        live.turn_torque = live.vessel.angular_speed * live.max_torque
    log.info("maritime_scenario(vessels): speed updated for all %d current vessel(s)", len(self._live))

  # ------------------------------------------------------------ prim building
  def _spawn_prim(self, vessel: Vessel) -> _Live:
    measured = mesh_backend.measure(vessel.mesh_id)
    coeffs = self.context.manager.state_ref.ship_physics
    params = ship_motion.derive(measured.dx, measured.dy, measured.dz, coeffs)
    bottom_z = -measured.dz / 2.0
    z = -params.draft_m - bottom_z

    idx = self._next_idx
    self._next_idx += 1
    body_path = f"{ROOT_PATH}/vessel_{idx}"
    vessel.name = body_path.rsplit("/", 1)[-1]   # so spawn()'s caller gets a usable name immediately,
                                                   # without a round-trip through list_vessels()

    stage = self.context.stage()
    from pxr import Gf, UsdGeom, UsdPhysics, PhysxSchema

    body_prim = stage.DefinePrim(body_path, "Xform")
    self._set_xform(body_prim, vessel.x, vessel.y, z, vessel.yaw_deg)

    mesh_backend.build_visual(stage, body_path + "/visual", measured)

    collider_prim = stage.DefinePrim(body_path + "/collider_proxy", "Cube")
    UsdGeom.Xformable(collider_prim).AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
      Gf.Vec3d(measured.dx / 2.0, measured.dy / 2.0, measured.dz / 2.0))
    UsdGeom.Imageable(collider_prim).MakeInvisible()
    UsdPhysics.CollisionAPI.Apply(collider_prim)
    mass_api = UsdPhysics.MassAPI.Apply(collider_prim)
    mass_api.CreateMassAttr().Set(params.mass_kg)
    mass_api.CreateCenterOfMassAttr().Set(Gf.Vec3f(0.0, 0.0, 0.0))
    mass_api.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(params.ixx, params.iyy, params.izz))

    rigid_body_api = UsdPhysics.RigidBodyAPI.Apply(body_prim)
    rigid_body_api.CreateRigidBodyEnabledAttr(True)
    physx_rigid = PhysxSchema.PhysxRigidBodyAPI.Apply(body_prim)
    physx_rigid.CreateLinearDampingAttr(LINEAR_DAMPING)
    physx_rigid.CreateAngularDampingAttr(0.1)
    force_api = PhysxSchema.PhysxForceAPI.Apply(body_prim)
    force_api.CreateForceEnabledAttr().Set(True)
    force_api.CreateForceAttr().Set(Gf.Vec3f(0, 0, 0))
    force_api.CreateTorqueAttr().Set(Gf.Vec3f(0, 0, 0))
    force_api.CreateModeAttr().Set("force")
    force_api.CreateWorldFrameEnabledAttr().Set(True)

    live = _Live()
    live.vessel = vessel
    live.body_path = body_path
    live.measured = measured
    live.params = params
    live.max_force = params.max_force_n
    live.max_torque = params.max_torque_nm
    live.turn_torque = vessel.angular_speed * live.max_torque
    live.prev_z = live.prev_roll = live.prev_pitch = live.prev_path_yaw = None
    live.wp_index = 0
    live.path_done = False
    return live

  def _recompute_all_physics(self, coeffs):
    stage = self.context.stage()
    for live in self._live:
      params = ship_motion.derive(live.measured.dx, live.measured.dy, live.measured.dz, coeffs)
      live.params = params
      live.max_force = params.max_force_n
      live.max_torque = params.max_torque_nm
      live.turn_torque = live.vessel.angular_speed * live.max_torque
      collider = stage.GetPrimAtPath(live.body_path + "/collider_proxy")
      if collider:
        from pxr import Gf, UsdPhysics
        mass_api = UsdPhysics.MassAPI(collider)
        mass_api.GetMassAttr().Set(params.mass_kg)
        mass_api.GetDiagonalInertiaAttr().Set(Gf.Vec3f(params.ixx, params.iyy, params.izz))
    log.info("maritime_scenario(vessels): physics recomputed for %d vessel(s)", len(self._live))

  @staticmethod
  def _set_xform(prim, x, y, z, yaw_deg):
    from pxr import Gf, UsdGeom
    half = math.radians(yaw_deg) / 2.0
    qw, qz = math.cos(half), math.sin(half)
    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(x, y, z))
    xform.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Quatf(float(qw), 0.0, 0.0, float(qz)))

  # ------------------------------------------------------------ per-frame physics + behavior
  def _start_update(self):
    import omni.kit.app
    if self._update_sub is None:
      self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(self._on_update)

  def _on_update(self, e):
    timeline = self.context.timeline()
    wave_backend = self.context.manager.get_backend("wave")
    if not timeline.is_playing() or not self._live or wave_backend is None:
      return
    dt = float(e.payload.get("dt", 1.0 / 60.0)) if getattr(e, "payload", None) else 1.0 / 60.0
    if dt <= 0.0:
      return

    stage = self.context.stage()
    from pxr import Gf, Usd, UsdGeom, PhysxSchema

    for live in self._live:
      body = stage.GetPrimAtPath(live.body_path)
      if not body:
        continue
      world_tf = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
      pos = world_tf.ExtractTranslation()
      forward = world_tf.TransformDir(Gf.Vec3d(1.0, 0.0, 0.0)).GetNormalized()
      beam = world_tf.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0)).GetNormalized()
      up = world_tf.TransformDir(Gf.Vec3d(0.0, 0.0, 1.0)).GetNormalized()

      bottom_z = -live.measured.dz / 2.0
      eq_z = -live.params.draft_m - bottom_z
      half_len, half_beam = live.measured.dx / 2.0, live.measured.dy / 2.0
      bow_h = wave_backend.height_at(pos[0] + forward[0] * half_len, pos[1] + forward[1] * half_len)
      stern_h = wave_backend.height_at(pos[0] - forward[0] * half_len, pos[1] - forward[1] * half_len)
      plus_beam_h = wave_backend.height_at(pos[0] + beam[0] * half_beam, pos[1] + beam[1] * half_beam)
      minus_beam_h = wave_backend.height_at(pos[0] - beam[0] * half_beam, pos[1] - beam[1] * half_beam)
      avg_h, target_pitch, target_roll = ship_motion.heave_pitch_roll_targets(
        bow_h, stern_h, plus_beam_h, minus_beam_h, live.measured.dx, live.measured.dy)
      target_z = eq_z + avg_h
      v_z = 0.0 if live.prev_z is None else (pos[2] - live.prev_z) / dt
      live.prev_z = pos[2]
      f_heave = ship_motion.heave_force(live.params, pos[2], target_z, v_z)

      roll = math.asin(max(-1.0, min(1.0, beam[2])))
      roll_rate = 0.0 if live.prev_roll is None else (roll - live.prev_roll) / dt
      live.prev_roll = roll
      torque_roll = ship_motion.roll_torque(live.params, roll, target_roll, roll_rate)

      pitch = -math.asin(max(-1.0, min(1.0, forward[2])))
      pitch_rate = 0.0 if live.prev_pitch is None else (pitch - live.prev_pitch) / dt
      live.prev_pitch = pitch
      torque_pitch = ship_motion.pitch_torque(live.params, pitch, target_pitch, pitch_rate)

      total_force = Gf.Vec3f(0.0, 0.0, f_heave)
      total_torque = Gf.Vec3f(*forward) * torque_roll + Gf.Vec3f(*beam) * torque_pitch

      speed_n = live.vessel.linear_speed * live.params.mass_kg * ship_motion.GRAVITY
      behavior = live.vessel.behavior
      if behavior == "forward":
        total_force += Gf.Vec3f(*forward) * speed_n
      elif behavior == "backward":
        total_force += Gf.Vec3f(*forward) * -speed_n
      elif behavior == "turn_left":
        total_force += Gf.Vec3f(*forward) * speed_n
        total_torque += Gf.Vec3f(*up) * live.turn_torque
      elif behavior == "turn_right":
        total_force += Gf.Vec3f(*forward) * speed_n
        total_torque += Gf.Vec3f(*up) * -live.turn_torque
      elif behavior == "follow_path" and not live.path_done:
        wps = live.vessel.waypoints
        if not wps:
          live.path_done = True
        else:
          target = wps[live.wp_index]
          if ship_motion.has_arrived(pos[0], pos[1], target):
            live.wp_index += 1
          if live.wp_index >= len(wps):
            live.path_done = True
          else:
            target = wps[live.wp_index]
            current_yaw = world_tf.ExtractRotation().Decompose(
              Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))[2]
            steer, _ = ship_motion.path_steer(pos[0], pos[1], current_yaw, target, live.prev_path_yaw, dt)
            live.prev_path_yaw = current_yaw
            total_force += Gf.Vec3f(*forward) * speed_n
            total_torque += Gf.Vec3f(*up) * (live.turn_torque * steer)
      # "stationary" (and "follow_path" once path_done): no thrust/turn torque -- still floats/bobs

      fx, fy, fz = ship_motion.clamp_vector_length(total_force[0], total_force[1], total_force[2], live.max_force)
      tx, ty, tz = ship_motion.clamp_vector_length(total_torque[0], total_torque[1], total_torque[2], live.max_torque)
      PhysxSchema.PhysxForceAPI(body).GetForceAttr().Set(Gf.Vec3f(fx, fy, fz))
      PhysxSchema.PhysxForceAPI(body).GetTorqueAttr().Set(Gf.Vec3f(tx, ty, tz))

      if not self._boundary_enabled:
        continue
      area = self.context.manager.state_ref.general.area_m
      margin = math.hypot(live.measured.dx, live.measured.dy) / 2.0   # hull half-diagonal -- see clamp_to_bounds()
      clamped_x, clamped_y = ship_motion.clamp_to_bounds(pos[0], pos[1], wave_backend.get_bounds(), area, margin)
      if clamped_x != pos[0] or clamped_y != pos[1]:
        # Invisible wall, not a teleport (2026-11-02, replacing the old wraparound -- user, live: "그냥
        # 벽에 박아서 이동못하는 듯한 연출처럼", a boat that's run into the edge should look stuck there,
        # not jump to the far side), at the edge of the ACTUAL placed water
        # (WaveBackend.get_bounds()) -- not the separate general.area_m value, which has no connection
        # to what's actually been built (same live follow-up: "지금 깔려있는 타일 기준으로 막힌게 한거
        # 맞아?" -- it wasn't, yet, this fixes that). Holding x/y at the boundary while leaving
        # prev_z/prev_roll/prev_pitch untouched (unlike the old wraparound's reset, which was needed
        # there specifically because teleporting a long distance would otherwise read back as a bogus
        # huge z/roll/pitch-rate spike) keeps heave/roll/pitch continuous -- the hull still bobs on the
        # wave normally, it just can't progress past the edge. Thrust keeps being applied into the wall
        # every frame (untouched above), which is what gives the "straining against it, pushed from
        # behind" look rather than a clean stop. `margin` (the hull's own half-diagonal) stops the HULL's
        # edge at the wall, not its origin point -- without it, the bow/side could already poke past the
        # wall before anything stopped it (found live: "선박의 중앙점까지 넘어가야 막히는 느낌").
        yaw_deg = world_tf.ExtractRotation().Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))[2]
        self._set_xform(body, clamped_x, clamped_y, pos[2], yaw_deg)
