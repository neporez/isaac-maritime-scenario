"""Mouse-click vessel placement in the viewport -- ported from
boat_mesh_pipeline/06_scenario_editor/scenario_ui.py. Its own docstring called this "the least-verified
part of this whole project (no running Isaac Sim available to test it against while writing it),
debugged purely from console logs the user pasted back" -- every quirk fixed below was found the same
way there, against a REAL running Isaac Sim this port has no way to re-verify against. Ported as
faithfully as possible for exactly that reason: this is proven-in-practice code, not something to
improve on blind.

Known, hard-won gotchas preserved verbatim:
  - ViewportCameraState has no compute_ray() on the Kit version this was built against -- unprojects by
    hand off ViewportAPI's own view/projection matrices instead (_mouse_to_ground_xy).
  - The viewport widget never delivers mouse-MOVE events while just hovering (confirmed: 0 calls logged
    across a whole session) -- live hover-follow instead POLLS the global mouse pixel position every
    frame via carb.input, the same low-level mechanism the ship backend's keyboard control already uses.
  - carb.input's global mouse-pixel reading and the widget's own click-event (x, y) do NOT agree exactly
    (a DPI-scale or window-chrome offset) -- the first real click calibrates the difference, and every
    live-follow sample afterward corrects by that measured offset instead of trusting the raw
    subtraction.
  - set_mouse_pressed_fn fires more than once for one physical click (confirmed: 3-4 identical calls) --
    debounced by elapsed TIME, not a mouse-released edge (that event is exactly as unverified as
    mouse-moved was, and depending on an edge that might never fire would permanently deadlock after
    one click).
  - purpose=guide debug geometry does not render under Isaac Sim's RTX viewport even with "Show Guide"
    on -- ghost/markers/path use plain visibility instead.
  - UsdGeom.BasisCurves threw omni.fabric.plugin VtValue-extraction warnings and never rendered for the
    waypoint path line -- replaced with a chain of thin Cube segments (same prim type the collider
    already uses successfully everywhere else in this project).
"""
import math
import time

import carb.input
import omni.appwindow
import omni.kit.app
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

try:
  from omni.kit.viewport.utility import get_active_viewport_window
  VIEWPORT_PICKING_AVAILABLE = True
  _import_error = None
except ImportError as exc:
  VIEWPORT_PICKING_AVAILABLE = False
  _import_error = exc

GHOST_PATH = "/World/ScenarioPlacementGhost"
GHOST_MATERIAL_PATH = "/World/ScenarioPlacementGhostMaterial"
# Real floating physics only submerges a vessel by its draft (a small fraction of total hull height),
# so a ghost centered exactly at wave height looks mostly underwater/barely visible -- lifting it by a
# fraction of its own height instead approximates where it'll actually settle.
GHOST_ABOVE_WATER_FRACTION = 0.4
WAYPOINT_MARKER_ROOT = "/World/ScenarioPathPreview"       # in-progress preview, cleared on finish/cancel
PERSISTENT_PATHS_ROOT = "/World/ScenarioPaths"             # one child group per spawned follow_path vessel
PRESS_DEBOUNCE_SEC = 0.3
YAW_STEP_DEG_DEFAULT = 15.0


class PlacementController:
  """Owns the ghost/waypoint-marker prims and the viewport mouse/keyboard subscriptions for one
  ScenarioWindow. One instance per window; call teardown() when the window is destroyed."""

  def __init__(self, controller, on_status=None, on_waypoint_count=None, on_yaw_changed=None):
    self.sc = controller   # api.ScenarioController
    self.on_status = on_status or (lambda text: None)
    self.on_waypoint_count = on_waypoint_count or (lambda n: None)
    self.on_yaw_changed = on_yaw_changed or (lambda yaw_deg: None)

    self.active = False
    self.mesh_id = None
    self.behavior = "forward"
    self.waypoints = []
    self.yaw_deg = 0.0
    self.yaw_step_deg = YAW_STEP_DEG_DEFAULT
    self.linear_speed = None
    self.angular_speed = None
    self.show_paths = True
    self._measured = None

    self._placement_update_sub = None
    self._mouse_device = None
    self._live_follow_broken = False
    self._global_to_local_offset = {"x": 0.0, "y": 0.0, "calibrated": False}
    self._placement_keyboard_sub = None
    self._last_press_time = 0.0
    self._viewport_window = None

  # ------------------------------------------------------------ stage helpers
  @staticmethod
  def _stage():
    return omni.usd.get_context().get_stage()

  def _wave_height_at(self, x, y):
    return self.sc.wave_height_at(x, y)

  # ------------------------------------------------------------ ghost preview
  def _get_ghost_material(self, stage):
    """A dedicated translucent material, force-bound onto every Gprim under the ghost -- plain
    displayOpacity/displayColor primvars don't work (the referenced boat mesh's own bound Material/
    Shader overrides those). Binding a real material directly ON each Gprim is the strongest/most-
    specific binding in USD's resolution order, so it wins over whatever the reference itself bound."""
    existing = stage.GetPrimAtPath(GHOST_MATERIAL_PATH)
    if existing:
      return UsdShade.Material(existing)
    material = UsdShade.Material.Define(stage, GHOST_MATERIAL_PATH)
    shader = UsdShade.Shader.Define(stage, GHOST_MATERIAL_PATH + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.3, 0.85, 1.0))
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.5)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.4)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material

  def _ensure_ghost(self, measured):
    stage = self._stage()
    if stage.GetPrimAtPath(GHOST_PATH):
      stage.RemovePrim(GHOST_PATH)
    ghost = stage.DefinePrim(GHOST_PATH, "Xform")
    visual_path = GHOST_PATH + "/visual"
    if not measured.usd_path:
      # No real asset registered (measured came from backends.meshes.fallback_measured()) -- same plain
      # scaled Cube backends.meshes.build_visual() uses for the real spawn, added as its own branch here
      # rather than folded into the reference path below (found live, 2026-11-03: AddReference("") left
      # the ghost empty/invisible for this case). The reference branch below is unchanged -- this
      # module's own docstring calls it "proven in practice... not something to improve on blind", so
      # this fix is additive, not a restructure of that path.
      cube_prim = stage.DefinePrim(visual_path, "Cube")
      UsdGeom.Cube(cube_prim).CreateSizeAttr(1.0)
      xform = UsdGeom.Xformable(cube_prim)
      xform.ClearXformOpOrder()
      xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(measured.dx, measured.dy, measured.dz))
    else:
      visual = stage.DefinePrim(visual_path, "Xform")
      visual.GetReferences().AddReference(measured.usd_path)
      for prim in Usd.PrimRange(ghost):
        if prim.IsA(UsdGeom.Mesh):
          UsdGeom.Mesh(prim).GetNormalsAttr().Block()   # see backends/meshes.py's strip_bad_normals()
      scale, rot_x = measured.scale, measured.rot_x_deg
      lcx, lcy, lcz = measured.local_center
      xform = UsdGeom.Xformable(visual)
      xform.ClearXformOpOrder()
      xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(-scale * lcx, -scale * lcy, -scale * lcz))
      xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(scale, scale, scale))
      if rot_x != 0.0:
        xform.AddRotateXOp(UsdGeom.XformOp.PrecisionFloat, opSuffix="unitsResolve").Set(float(rot_x))
    ghost_material = self._get_ghost_material(stage)
    for prim in Usd.PrimRange(ghost):
      if prim.IsA(UsdGeom.Gprim):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(ghost_material)
    self._measured = measured
    return ghost

  def _update_ghost_pose(self, x, y, yaw_deg):
    stage = self._stage()
    ghost = stage.GetPrimAtPath(GHOST_PATH)
    if not ghost:
      return
    dz = self._measured.dz if self._measured else 0.0
    z = self._wave_height_at(x, y) + dz * GHOST_ABOVE_WATER_FRACTION
    half = math.radians(yaw_deg) / 2.0
    qw, qz = math.cos(half), math.sin(half)
    xform = UsdGeom.Xformable(ghost)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(x, y, z))
    xform.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Quatf(float(qw), 0.0, 0.0, float(qz)))

  def _remove_ghost(self):
    stage = self._stage()
    if stage.GetPrimAtPath(GHOST_PATH):
      stage.RemovePrim(GHOST_PATH)

  # ------------------------------------------------------------ waypoint markers / path lines
  def _clear_waypoint_markers(self):
    stage = self._stage()
    if stage.GetPrimAtPath(WAYPOINT_MARKER_ROOT):
      stage.RemovePrim(WAYPOINT_MARKER_ROOT)
    stage.DefinePrim(WAYPOINT_MARKER_ROOT, "Xform")

  def _draw_path_geometry(self, root, points):
    """(Re)draws waypoint markers (spheres) + connecting segments (thin Cubes) under root, clearing
    whatever was there first. Used both for the live in-progress preview (WAYPOINT_MARKER_ROOT) and a
    spawned follow_path vessel's PERSISTENT path (PERSISTENT_PATHS_ROOT/<name>)."""
    stage = self._stage()
    if stage.GetPrimAtPath(root):
      stage.RemovePrim(root)
    if not points:
      return
    stage.DefinePrim(root, "Xform")
    for i, (x, y) in enumerate(points):
      marker = stage.DefinePrim(f"{root}/wp_{i}", "Sphere")
      UsdGeom.Xformable(marker).AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(x, y, self._wave_height_at(x, y) + 1.5))
      UsdGeom.Xformable(marker).AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(1.2, 1.2, 1.2))
      UsdGeom.Gprim(marker).CreateDisplayColorAttr().Set([Gf.Vec3f(1.0, 0.9, 0.1)])
    for i in range(len(points) - 1):
      (x1, y1), (x2, y2) = points[i], points[i + 1]
      length = math.hypot(x2 - x1, y2 - y1)
      if length < 1e-6:
        continue
      yaw_deg = math.degrees(math.atan2(y2 - y1, x2 - x1))
      mid_x, mid_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
      mid_z = (self._wave_height_at(x1, y1) + self._wave_height_at(x2, y2)) / 2.0 + 1.5
      seg = stage.DefinePrim(f"{root}/seg_{i}", "Cube")
      half = math.radians(yaw_deg) / 2.0
      qw, qz = math.cos(half), math.sin(half)
      xform = UsdGeom.Xformable(seg)
      xform.ClearXformOpOrder()
      xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(mid_x, mid_y, mid_z))
      xform.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Quatf(float(qw), 0.0, 0.0, float(qz)))
      xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(length / 2.0, 0.4, 0.4))
      UsdGeom.Gprim(seg).CreateDisplayColorAttr().Set([Gf.Vec3f(1.0, 0.9, 0.1)])

  def _draw_persistent_path(self, vessel_name, points):
    stage = self._stage()
    if not stage.GetPrimAtPath(PERSISTENT_PATHS_ROOT):
      stage.DefinePrim(PERSISTENT_PATHS_ROOT, "Xform")
    self._draw_path_geometry(f"{PERSISTENT_PATHS_ROOT}/{vessel_name}", points)
    UsdGeom.Imageable(stage.GetPrimAtPath(PERSISTENT_PATHS_ROOT)).CreateVisibilityAttr().Set(
      UsdGeom.Tokens.inherited if self.show_paths else UsdGeom.Tokens.invisible)

  def redraw_all_persistent_paths(self):
    """Call after load_scenario() -- restored follow_path vessels have no path drawn yet, only their
    live steering state (other_boats.py's save/load had this exact gap, same fix)."""
    stage = self._stage()
    if stage.GetPrimAtPath(PERSISTENT_PATHS_ROOT):
      stage.RemovePrim(PERSISTENT_PATHS_ROOT)
    for i, vessel in enumerate(self.sc.list_vessels()):
      if vessel.behavior != "follow_path" or not vessel.waypoints:
        continue
      full_points = [(vessel.x, vessel.y)] + list(vessel.waypoints)
      self._draw_persistent_path(vessel.name or f"vessel_{i}", full_points)

  def set_show_paths(self, value):
    self.show_paths = bool(value)
    stage = self._stage()
    root = stage.GetPrimAtPath(PERSISTENT_PATHS_ROOT)
    if root:
      UsdGeom.Imageable(root).CreateVisibilityAttr().Set(
        UsdGeom.Tokens.inherited if value else UsdGeom.Tokens.invisible)

  def clear_all(self):
    self.sc.clear_vessels()
    stage = self._stage()
    if stage.GetPrimAtPath(PERSISTENT_PATHS_ROOT):
      stage.RemovePrim(PERSISTENT_PATHS_ROOT)

  # ------------------------------------------------------------ mouse -> ground point
  @staticmethod
  def mouse_to_ground_xy(local_x, local_y, widget_w, widget_h, viewport_api):
    """Unprojects a viewport-local pixel to the z=0 ground plane by hand off the viewport's own view/
    projection matrices (no compute_ray() on the Kit version this was built against). Unprojects at
    NDC depths 0.0 and 1.0 (not -1.0/1.0) specifically because 0.0 is a valid in-frustum point under
    BOTH the OpenGL-style [-1,1] and the Vulkan/DX-style [0,1] NDC-depth convention, so this works
    regardless of which one Kit's projection matrix actually uses, without needing to know that. Static
    + takes viewport_api directly so the ray math itself is testable without a live Kit viewport."""
    if not widget_w or not widget_h:
      return None
    ndc_x = (local_x / widget_w) * 2.0 - 1.0
    ndc_y = 1.0 - (local_y / widget_h) * 2.0

    view = viewport_api.view            # Gf.Matrix4d, world -> camera
    proj = viewport_api.projection      # Gf.Matrix4d, camera -> clip
    inv_view_proj = (view * proj).GetInverse()   # row-vector convention, same as the rest of this project

    def _unproject(ndc_z):
      clip = Gf.Vec4d(ndc_x, ndc_y, ndc_z, 1.0)
      world = clip * inv_view_proj
      if world[3] == 0.0:
        return None
      return Gf.Vec3d(world[0] / world[3], world[1] / world[3], world[2] / world[3])

    p0, p1 = _unproject(0.0), _unproject(1.0)
    if p0 is None or p1 is None:
      return None
    origin = p0
    direction = p1 - p0
    if direction.GetLength() < 1e-9:
      return None
    direction = direction.GetNormalized()
    if abs(direction[2]) < 1e-9:
      return None
    t = -origin[2] / direction[2]
    if t < 0:
      return None
    return origin[0] + direction[0] * t, origin[1] + direction[1] * t

  def _mouse_to_ground_xy(self, local_x, local_y, widget_w, widget_h):
    viewport_window = get_active_viewport_window()
    if viewport_window is None:
      return None
    return self.mouse_to_ground_xy(local_x, local_y, widget_w, widget_h, viewport_window.viewport_api)

  # ------------------------------------------------------------ live hover-follow
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
      # Mouse is over the UI panel, not the 3D viewport -- skip, don't drag the ghost off to wherever
      # that out-of-bounds point projects to.
      if not w or not h or local_x < 0 or local_x > w or local_y < 0 or local_y > h:
        return
      hit = self._mouse_to_ground_xy(local_x, local_y, w, h)
      if hit is None:
        return
      gx, gy = hit
      self._update_ghost_pose(gx, gy, self.yaw_deg)
    except Exception as exc:
      print(f"[maritime_scenario] live mouse-follow failed ({exc}) -- falling back to click-only placement")
      self._live_follow_broken = True
      self._placement_update_sub = None

  def _start_live_follow(self):
    if self._placement_update_sub is None and not self._live_follow_broken:
      self._placement_update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
        self._on_placement_update)

  def _stop_live_follow(self):
    self._placement_update_sub = None

  # ------------------------------------------------------------ yaw nudge ([ / ] while placing)
  def nudge_yaw(self, sign):
    self.yaw_deg += sign * self.yaw_step_deg
    self.on_yaw_changed(self.yaw_deg)

  def set_yaw(self, yaw_deg):
    self.yaw_deg = float(yaw_deg)

  def set_yaw_step(self, step_deg):
    self.yaw_step_deg = float(step_deg)

  def _on_placement_keyboard(self, e):
    if not self.active or e.type != carb.input.KeyboardEventType.KEY_PRESS:
      return True
    # Not Q/E -- those are Isaac Sim's own default viewport manipulator shortcuts (Select/Move/Rotate/
    # Scale are commonly Q/W/E/R in Kit), confirmed eating keypresses before this subscription saw them.
    if e.input == carb.input.KeyboardInput.LEFT_BRACKET:
      self.nudge_yaw(-1.0)
    elif e.input == carb.input.KeyboardInput.RIGHT_BRACKET:
      self.nudge_yaw(1.0)
    return True   # don't consume -- let other shortcuts/camera controls still work

  def _start_placement_keyboard(self):
    if self._placement_keyboard_sub is None:
      appwindow = omni.appwindow.get_default_app_window()
      self._placement_keyboard_sub = carb.input.acquire_input_interface().subscribe_to_keyboard_events(
        appwindow.get_keyboard(), self._on_placement_keyboard)

  def _stop_placement_keyboard(self):
    self._placement_keyboard_sub = None

  # ------------------------------------------------------------ enter / exit placement mode
  def start(self, mesh_id, behavior, linear_speed=None, angular_speed=None):
    if not VIEWPORT_PICKING_AVAILABLE:
      self.on_status(f"viewport picking unavailable ({_import_error}) -- use the numeric X/Y/Yaw fields instead")
      return False
    from ..backends import meshes as mesh_backend
    measured = mesh_backend.measure(mesh_id)

    self.active = True
    self.mesh_id = mesh_id
    self.behavior = behavior
    self.waypoints = []
    self.linear_speed = linear_speed
    self.angular_speed = angular_speed
    self._last_press_time = 0.0
    self._clear_waypoint_markers()
    self._ensure_ghost(measured)

    try:
      vw = get_active_viewport_window()
      if vw is None:
        self.on_status("no active viewport window found")
        self.active = False
        self._remove_ghost()
        return False
      self._viewport_window = vw
      vw.frame.set_mouse_moved_fn(lambda *a: None)   # kept attached in case a future Kit config does
                                                       # deliver hover-move; currently inert, live-follow
                                                       # above is what actually drives the ghost
      vw.frame.set_mouse_pressed_fn(self._on_viewport_mouse_pressed)
      vw.frame.set_mouse_released_fn(lambda *a: None)   # kept for symmetry; debounce doesn't depend on it
    except Exception as exc:
      self.on_status(f"couldn't attach viewport mouse handlers ({exc})")
      self.active = False
      self._remove_ghost()
      return False

    self._start_live_follow()
    self._start_placement_keyboard()
    if behavior == "follow_path":
      self.on_status("0 waypoint(s) placed -- click in the viewport to add, then Finish Path")
      self.on_waypoint_count(0)
    else:
      self.on_status("click a spot in the viewport to spawn there")
    return True

  def _on_viewport_mouse_pressed(self, *args):
    now = time.monotonic()
    if now - self._last_press_time < PRESS_DEBOUNCE_SEC:
      return
    self._last_press_time = now
    if not self.active or len(args) < 3 or args[2] != 0:   # args[2] = button, 0 = left
      return
    try:
      vw = get_active_viewport_window()
      w, h = vw.frame.computed_width, vw.frame.computed_height
      # Calibrate the carb.input global->local offset off this click's own known-correct local (x, y).
      try:
        g = carb.input.acquire_input_interface().get_mouse_coords_pixel(self._get_mouse_device())
        self._global_to_local_offset["x"] = g[0] - args[0]
        self._global_to_local_offset["y"] = g[1] - args[1]
        self._global_to_local_offset["calibrated"] = True
      except Exception:
        pass   # non-fatal -- live-follow just keeps using the uncalibrated subtraction
      hit = self._mouse_to_ground_xy(args[0], args[1], w, h)
    except Exception as exc:
      self.on_status(f"click picking failed ({exc})")
      return
    if hit is None:
      self.on_status("click didn't hit the ground plane (camera looking away from the water?)")
      return
    gx, gy = hit
    self._update_ghost_pose(gx, gy, self.yaw_deg)
    if self.behavior == "follow_path":
      self.waypoints.append((gx, gy))
      self._draw_path_geometry(WAYPOINT_MARKER_ROOT, self.waypoints)
      self.on_waypoint_count(len(self.waypoints))
    else:
      self.sc.spawn_vessel(self.mesh_id, gx, gy, self.yaw_deg, self.behavior,
                            linear_speed=self.linear_speed, angular_speed=self.angular_speed)
      self.stop()

  def finish_path(self):
    if not self.active or self.behavior != "follow_path":
      self.on_status("not currently placing a path -- pick behavior=follow_path and Place with mouse first")
      return
    if len(self.waypoints) < 2:
      self.on_status(f"need at least 2 waypoints (start + >=1 target), have {len(self.waypoints)}")
      return
    start, rest = self.waypoints[0], self.waypoints[1:]
    vessel = self.sc.spawn_vessel(self.mesh_id, start[0], start[1], None, "follow_path", waypoints=rest,
                                   linear_speed=self.linear_speed, angular_speed=self.angular_speed)
    full_points = list(self.waypoints)
    self._clear_waypoint_markers()   # the in-progress preview -- the persistent copy below replaces it
    if vessel is not None:
      self._draw_persistent_path(vessel.name, full_points)
    self.stop()

  def cancel(self):
    self._clear_waypoint_markers()
    self.stop()

  def stop(self):
    self.active = False
    self._stop_live_follow()
    self._stop_placement_keyboard()
    self._remove_ghost()
    try:
      if self._viewport_window is not None:
        self._viewport_window.frame.set_mouse_moved_fn(None)
        self._viewport_window.frame.set_mouse_pressed_fn(None)
        self._viewport_window.frame.set_mouse_released_fn(None)
    except Exception as exc:
      print(f"[maritime_scenario] (non-fatal) couldn't detach viewport mouse handlers: {exc}")
    self.on_status("")

  def teardown(self):
    """Call when the owning window is destroyed."""
    self.stop()
