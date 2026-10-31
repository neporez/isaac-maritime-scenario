"""The controllable own ship -- ported from boat_mesh_pipeline/06_scenario_editor/float_mesh_test.py,
web meshes only (that file could also float this project's own private "ours" usd_out meshes; this
extension doesn't carry that pool at all -- see core/meshes.py's own docstring). All the heave/roll/
pitch spring-damper math lives in core.ship_motion; this module is the pxr plumbing + keyboard control
around it.

I/J/K/L control forward/back/left/right, NOT WASD or the arrow keys -- both of those are Isaac Sim's
own default viewport fly-camera shortcuts and never reach this subscription (confirmed in
06_scenario_editor, 2026-10-21: WASD controls were dead; switched to I/J/K/L, which nothing in Isaac
Sim's default viewport binds).
"""
import logging
import math

from ..core import ship_motion
from .base import Backend
from . import meshes as mesh_backend

log = logging.getLogger("maritime_scenario")

ROOT_PATH = "/World/OwnShip"
BODY_PATH = ROOT_PATH + "/body"
VISUAL_PATH = BODY_PATH + "/visual"
COLLIDER_PATH = BODY_PATH + "/collider_proxy"

SPAWN_X, SPAWN_Y = 0.0, 0.0
DROP_HEIGHT_M = 1.5   # spawn this many meters above its settled depth, so it visibly drops+settles
LINEAR_DAMPING = 0.8

DEFAULT_THRUST_FRACTION = 0.3   # fraction of mass*gravity -> key-held forward/back thrust, same role
                                 # core.vessel.DEFAULT_LINEAR_SPEED plays for vessels
KEY_TURN_FRACTION = 0.01   # fraction of max_torque_nm -> key-held left/right turn torque, same role
                            # core.vessel.DEFAULT_ANGULAR_SPEED plays for vessels


class ShipBackend(Backend):
  name = "ship"

  def __init__(self):
    self.mesh_id = None
    self._measured = None
    self._params = None   # core.ship_motion.ShipPhysicsParams, for the CURRENT mesh
    self._prev = {"z": None, "roll": None, "pitch": None}
    self._controls = {"forward": False, "back": False, "left": False, "right": False}
    self._thrust_n = 0.0
    # Live-adjustable versions of the two module constants above (2026-11-01, user's own call: own ship
    # had no Linear/Angular speed control at all, unlike vessels' per-spawn linear_speed/angular_speed --
    # these are the same fractions, just instance state instead of hardcoded, set via set_speed() from
    # the UI's Own Ship Linear/Angular fields or ScenarioController.set_ship_speed()).
    self._linear_fraction = DEFAULT_THRUST_FRACTION
    self._angular_fraction = KEY_TURN_FRACTION
    # Live-adjustable spawn position (2026-11-01, user's own call: was hardcoded to SPAWN_X/SPAWN_Y/
    # DROP_HEIGHT_M module constants) -- set via set_spawn_position()/the UI's Own Ship X/Y/Z fields or
    # ScenarioController.set_ship_spawn_position(). Z keeps the original DROP_HEIGHT_M semantics (meters
    # ABOVE the ship's own settled equilibrium depth, not an absolute world Z -- see set_mesh()/reset()),
    # just adjustable instead of fixed, so it still visibly drops+settles at any height you pick.
    self._spawn_x = SPAWN_X
    self._spawn_y = SPAWN_Y
    self._spawn_z = DROP_HEIGHT_M
    # Invisible wall at the edge of the placed water (2026-11-02, user's own call) -- see _on_update()'s
    # own comment for the full mechanism; same default-on convention as VesselsBackend._boundary_enabled.
    self._boundary_enabled = True
    self._update_sub = None
    self._keyboard_sub = None

  # ------------------------------------------------------------ Backend interface
  def build(self):
    """Deliberately does NOT spawn any mesh (2026-10-31, user's own call): "Build scene" used to drop a
    default-mesh own ship into the air immediately, before any water layout even existed -- confusing as
    the very first thing a new user sees, and backwards for the intended workflow of (1) set up the wave/
    water layout, THEN (2) spawn the own ship + vessels. set_mesh() (below) is what actually spawns the
    ship now, called from the UI's "Own Ship" panel ("Apply to own ship") or the API
    (ScenarioController.set_own_ship_mesh()) -- build() only starts the update/keyboard subscriptions so
    those are ready the moment a mesh IS set. Every per-frame/position method already guards on
    self._measured/self._params being None (get_position(), _on_update(), reset()), so a ship that hasn't
    been spawned yet is simply inert, not broken."""
    self._start_update()
    self._start_keyboard()

  def apply_state(self, state, changed):
    if "ship_physics" in changed and self._measured is not None:
      self._recompute_physics(state.ship_physics)

  def detach(self):
    for attr in ("_update_sub", "_keyboard_sub"):
      sub = getattr(self, attr)
      if sub is not None:
        del sub
        setattr(self, attr, None)

  # ------------------------------------------------------------ mesh / physics
  def set_mesh(self, mesh_id):
    """Rebuilds the visual reference, collider scale/mass/inertia, and heave/roll/pitch K/C/mass/I for
    this mesh, then re-drops the ship at the new equilibrium -- same as float_mesh_test.py's
    set_mesh_source(), minus the "ours"/web toggle (web-only here). Returns True if this mesh_id resolved
    to the fallback placeholder box (no real boat assets registered) rather than a real asset -- the UI's
    "Apply to own ship" uses this to surface a warning without build_visual()/mesh_backend having to know
    anything about the UI."""
    self._measured = mesh_backend.measure(mesh_id)
    self.mesh_id = mesh_id
    coeffs = self.context.manager.state_ref.ship_physics
    self._params = ship_motion.derive(self._measured.dx, self._measured.dy, self._measured.dz, coeffs)

    stage = self.context.stage()
    bottom_z = -self._measured.dz / 2.0
    spawn_z = self._spawn_z - self._params.draft_m - bottom_z

    import omni.usd
    from pxr import Gf, UsdGeom, UsdPhysics, PhysxSchema

    body_prim = stage.DefinePrim(BODY_PATH, "Xform")
    self._set_xform(body_prim, self._spawn_x, self._spawn_y, spawn_z, 0.0)

    mesh_backend.build_visual(stage, VISUAL_PATH, self._measured)

    collider_prim = stage.DefinePrim(COLLIDER_PATH, "Cube")
    UsdGeom.Xformable(collider_prim).AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
      Gf.Vec3d(self._measured.dx / 2.0, self._measured.dy / 2.0, self._measured.dz / 2.0))
    UsdGeom.Imageable(collider_prim).MakeInvisible()
    UsdPhysics.CollisionAPI.Apply(collider_prim)
    mass_api = UsdPhysics.MassAPI.Apply(collider_prim)
    mass_api.CreateMassAttr().Set(self._params.mass_kg)
    mass_api.CreateCenterOfMassAttr().Set(Gf.Vec3f(0.0, 0.0, 0.0))
    mass_api.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(self._params.ixx, self._params.iyy, self._params.izz))

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
    rigid_body_api.GetVelocityAttr().Set(Gf.Vec3f(0, 0, 0))
    rigid_body_api.GetAngularVelocityAttr().Set(Gf.Vec3f(0, 0, 0))

    self._prev = {"z": None, "roll": None, "pitch": None}
    log.info("maritime_scenario(ship): mesh -> %s  dx=%.2f dy=%.2f dz=%.2f  mass=%.0fkg",
              mesh_id, self._measured.dx, self._measured.dy, self._measured.dz, self._params.mass_kg)
    return not self._measured.usd_path

  def _recompute_physics(self, coeffs):
    """Re-derives mass/K/C/inertia for the CURRENT mesh without re-spawning -- wired to the UI's Ship
    Physics sliders so dragging one updates the live collider immediately."""
    if self._measured is None:
      return
    self._params = ship_motion.derive(self._measured.dx, self._measured.dy, self._measured.dz, coeffs)
    stage = self.context.stage()
    collider = stage.GetPrimAtPath(COLLIDER_PATH)
    if collider:
      from pxr import Gf, UsdPhysics
      mass_api = UsdPhysics.MassAPI(collider)
      mass_api.GetMassAttr().Set(self._params.mass_kg)
      mass_api.GetDiagonalInertiaAttr().Set(Gf.Vec3f(self._params.ixx, self._params.iyy, self._params.izz))
    log.info("maritime_scenario(ship): physics recomputed, mass=%.0fkg", self._params.mass_kg)

  def reset(self):
    """Re-drop at the spawn pose without re-measuring the mesh or recreating prims."""
    if self._measured is None or self._params is None:
      return
    stage = self.context.stage()
    body = stage.GetPrimAtPath(BODY_PATH)
    if not body:
      return
    bottom_z = -self._measured.dz / 2.0
    spawn_z = self._spawn_z - self._params.draft_m - bottom_z
    from pxr import Gf, UsdPhysics
    self._set_xform(body, self._spawn_x, self._spawn_y, spawn_z, 0.0)
    rb = UsdPhysics.RigidBodyAPI(body)
    rb.GetVelocityAttr().Set(Gf.Vec3f(0, 0, 0))
    rb.GetAngularVelocityAttr().Set(Gf.Vec3f(0, 0, 0))
    self._prev = {"z": None, "roll": None, "pitch": None}

  def get_position(self):
    """(x, y) world position of the own ship's body, or None before it's been built -- used by
    WaveBackend for distance-based tile activation (2026-10-30)."""
    if self._measured is None:
      return None
    stage = self.context.stage()
    body = stage.GetPrimAtPath(BODY_PATH)
    if not body:
      return None
    from pxr import Usd, UsdGeom
    world_tf = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    pos = world_tf.ExtractTranslation()
    return pos[0], pos[1]

  def set_control(self, name, pressed):
    if name in self._controls:
      self._controls[name] = bool(pressed)

  def set_thrust(self, newtons):
    """Steady cruise thrust along the hull's own +x (bow) axis, on top of any held forward/back key."""
    self._thrust_n = float(newtons)

  def set_speed(self, linear=None, angular=None):
    """Overrides the key-held forward/back thrust fraction and/or left/right turn-torque fraction --
    the same (mass*gravity)/(max_torque) fraction convention as core.vessel's linear_speed/angular_speed,
    just for the own ship. Either arg can be omitted to leave that one unchanged."""
    if linear is not None:
      self._linear_fraction = float(linear)
    if angular is not None:
      self._angular_fraction = float(angular)

  def set_spawn_position(self, x=None, y=None, z=None):
    """Overrides where the own ship spawns/re-drops from -- x/y are world-space meters, z is the same
    "meters above its own settled equilibrium depth" DROP_HEIGHT_M always meant (not an absolute world
    Z), so it still visibly drops+settles at whatever height you pick. Takes effect the next time
    set_mesh() (re)spawns it or reset() re-drops it -- not live on an already-standing ship. Any arg can
    be omitted to leave that one unchanged."""
    if x is not None:
      self._spawn_x = float(x)
    if y is not None:
      self._spawn_y = float(y)
    if z is not None:
      self._spawn_z = float(z)

  def set_boundary_enabled(self, flag):
    self._boundary_enabled = bool(flag)
    log.info("maritime_scenario(ship): area boundary %s", "enabled" if flag else "disabled")

  @staticmethod
  def _set_xform(prim, x, y, z, yaw_deg):
    from pxr import Gf, UsdGeom
    half = math.radians(yaw_deg) / 2.0
    qw, qz = math.cos(half), math.sin(half)
    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(x, y, z))
    xform.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Quatf(float(qw), 0.0, 0.0, float(qz)))

  # ------------------------------------------------------------ per-frame physics
  def _start_update(self):
    import omni.kit.app
    if self._update_sub is None:
      self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(self._on_update)

  def _start_keyboard(self):
    import carb.input
    import omni.appwindow
    if self._keyboard_sub is not None:
      return
    key_to_control = {
      carb.input.KeyboardInput.I: "forward",
      carb.input.KeyboardInput.K: "back",
      carb.input.KeyboardInput.J: "left",
      carb.input.KeyboardInput.L: "right",
    }

    def _on_keyboard(e):
      name = key_to_control.get(e.input)
      if name is None:
        return True
      if e.type == carb.input.KeyboardEventType.KEY_PRESS:
        self.set_control(name, True)
      elif e.type == carb.input.KeyboardEventType.KEY_RELEASE:
        self.set_control(name, False)
      return True   # don't consume -- let the viewport's own camera keys etc. still work

    appwindow = omni.appwindow.get_default_app_window()
    self._keyboard_sub = carb.input.acquire_input_interface().subscribe_to_keyboard_events(
      appwindow.get_keyboard(), _on_keyboard)

  def _on_update(self, e):
    timeline = self.context.timeline()
    stage = self.context.stage()
    body = stage.GetPrimAtPath(BODY_PATH)
    wave_backend = self.context.manager.get_backend("wave")
    if not timeline.is_playing() or not body or self._params is None or wave_backend is None:
      return
    dt = float(e.payload.get("dt", 1.0 / 60.0)) if getattr(e, "payload", None) else 1.0 / 60.0
    if dt <= 0.0:
      return

    from pxr import Gf, UsdGeom, Usd, PhysxSchema

    world_tf = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    pos = world_tf.ExtractTranslation()
    forward = world_tf.TransformDir(Gf.Vec3d(1.0, 0.0, 0.0)).GetNormalized()
    beam = world_tf.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0)).GetNormalized()
    up = world_tf.TransformDir(Gf.Vec3d(0.0, 0.0, 1.0)).GetNormalized()

    bottom_z = -self._measured.dz / 2.0
    eq_z = -self._params.draft_m - bottom_z
    half_len, half_beam = self._measured.dx / 2.0, self._measured.dy / 2.0
    bow_h = wave_backend.height_at(pos[0] + forward[0] * half_len, pos[1] + forward[1] * half_len)
    stern_h = wave_backend.height_at(pos[0] - forward[0] * half_len, pos[1] - forward[1] * half_len)
    plus_beam_h = wave_backend.height_at(pos[0] + beam[0] * half_beam, pos[1] + beam[1] * half_beam)
    minus_beam_h = wave_backend.height_at(pos[0] - beam[0] * half_beam, pos[1] - beam[1] * half_beam)
    avg_h, target_pitch, target_roll = ship_motion.heave_pitch_roll_targets(
      bow_h, stern_h, plus_beam_h, minus_beam_h, self._measured.dx, self._measured.dy)
    target_z = eq_z + avg_h
    v_z = 0.0 if self._prev["z"] is None else (pos[2] - self._prev["z"]) / dt
    self._prev["z"] = pos[2]
    f_heave = ship_motion.heave_force(self._params, pos[2], target_z, v_z)

    roll = math.asin(max(-1.0, min(1.0, beam[2])))
    roll_rate = 0.0 if self._prev["roll"] is None else (roll - self._prev["roll"]) / dt
    self._prev["roll"] = roll
    torque_roll = ship_motion.roll_torque(self._params, roll, target_roll, roll_rate)

    pitch = -math.asin(max(-1.0, min(1.0, forward[2])))
    pitch_rate = 0.0 if self._prev["pitch"] is None else (pitch - self._prev["pitch"]) / dt
    self._prev["pitch"] = pitch
    torque_pitch = ship_motion.pitch_torque(self._params, pitch, target_pitch, pitch_rate)

    total_force = Gf.Vec3f(0.0, 0.0, f_heave)
    total_torque = Gf.Vec3f(*forward) * torque_roll + Gf.Vec3f(*beam) * torque_pitch

    key_thrust_n = self._linear_fraction * self._params.mass_kg * ship_motion.GRAVITY
    thrust = self._thrust_n
    if self._controls["forward"]:
      thrust += key_thrust_n
    if self._controls["back"]:
      thrust -= key_thrust_n
    if thrust != 0.0:
      total_force += Gf.Vec3f(*forward) * thrust

    if self._controls["left"] or self._controls["right"]:
      key_turn_nm = self._angular_fraction * self._params.max_torque_nm
      turn = key_turn_nm if self._controls["left"] else -key_turn_nm
      total_torque += Gf.Vec3f(*up) * turn

    fx, fy, fz = ship_motion.clamp_vector_length(
      total_force[0], total_force[1], total_force[2], self._params.max_force_n)
    tx, ty, tz = ship_motion.clamp_vector_length(
      total_torque[0], total_torque[1], total_torque[2], self._params.max_torque_nm)

    PhysxSchema.PhysxForceAPI(body).GetForceAttr().Set(Gf.Vec3f(fx, fy, fz))
    PhysxSchema.PhysxForceAPI(body).GetTorqueAttr().Set(Gf.Vec3f(tx, ty, tz))

    if self._boundary_enabled:
      area = self.context.manager.state_ref.general.area_m
      margin = math.hypot(self._measured.dx, self._measured.dy) / 2.0   # hull half-diagonal -- see clamp_to_bounds()
      clamped_x, clamped_y = ship_motion.clamp_to_bounds(pos[0], pos[1], wave_backend.get_bounds(), area, margin)
      if clamped_x != pos[0] or clamped_y != pos[1]:
        # Invisible wall at the edge of the actual placed water, not a teleport -- same mechanism and
        # reasoning as vessels.py's own boundary (2026-11-02, user's own call: "자선도 나는 막히기를
        # 바라고 있고" -- the own ship never had ANY boundary before this, unlike vessels which at least
        # had a (now-replaced) wraparound). prev z/roll/pitch stay untouched since this only ever moves
        # the body a tiny amount back to the boundary, not a long-distance jump.
        yaw_deg = world_tf.ExtractRotation().Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))[2]
        self._set_xform(body, clamped_x, clamped_y, pos[2], yaw_deg)
