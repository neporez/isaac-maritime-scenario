"""The "Maritime Scenario" panel -- ported from boat_mesh_pipeline/06_scenario_editor/scenario_ui.py,
talking to the controller/manager instead of reaching into a shared Script-Editor globals dict.

Mouse-click placement (ghost preview, click-to-place, click-to-add-a-waypoint) lives in
ui/placement.py's PlacementController, kept separate from the widget layout here since it owns its own
USD prims (ghost, waypoint markers) and viewport/keyboard subscriptions -- this window just wires its
buttons/fields to that controller's methods and reflects its callbacks (status text, waypoint count,
live yaw) back into the UI. Numeric X/Y/Yaw fields + a waypoints text field stay available alongside
mouse placement as a fallback (same as the original scenario_ui.py: typing always worked even when
viewport picking was unavailable or broken).

2026-10-31 redesign (user's own call, prepping the GitHub readme/guide writeup): explanatory prose
(what each control does/why) is deliberately NOT in this panel anymore -- it belongs in the README/
asset guide instead, so the live UI only carries labels/controls, not paragraphs. Every section except
the top Setup/status/Build bar is wrapped in an omni.ui.CollapsableFrame for a compact, scannable panel
(matching another installed extension's Weather FX panel), ordered to match the two-step workflow the
guide documents: (1) water tiles -> (2) own ship / vessels / ship physics -> Gerstner sea-state last.
"""
import os

import omni.ui as ui
import omni.kit.async_engine

from .placement import PlacementController
from .tile_placement import MAX_TILE_LEVEL, TilePlacementController, size_for_level

BEHAVIORS = ["forward", "backward", "turn_left", "turn_right", "stationary", "follow_path"]
NO_ASSETS_WARNING = ("WARNING: no boat assets folder set (or it's empty) -- spawning a placeholder box "
                      "instead. Use \"Choose folder...\" above to set one.")

# Base resolution slider (2026-10-31, user's own call): shown as clean powers of 2 (8/16/32/64) even
# though the real resolution passed to TilePlacementController is displayed+1 (9/17/33/65) -- odd counts
# are what resolution_for_level()'s "(base-1)*2**level + 1" formula needs to land exactly on a shared
# vertex at every tile boundary (see its own docstring), so the UI shows the tidy number but still drives
# the real one underneath. Index 1 (displayed 16 / real 17) is the default, matching
# tile_placement.DEFAULT_BASE_RESOLUTION.
BASE_RES_DISPLAY_STEPS = [8, 16, 32, 64]


class ScenarioWindow:
  def __init__(self, controller):
    self._sc = controller
    self._mesh_choices = []   # [(label, mesh_id), ...], refreshed by _refresh_mesh_choices()
    self._no_real_assets = True   # becomes accurate once _refresh_mesh_choices() runs (after Build)
    self._mesh_dir_dialog = None
    self._placement = PlacementController(
      controller,
      on_status=self._on_placement_status,
      on_waypoint_count=self._on_waypoint_count,
      on_yaw_changed=self._on_placement_yaw_changed,
    )
    self._tile_placement = TilePlacementController(controller, on_status=self._on_tile_status)
    self._window = ui.Window("Maritime Scenario", width=420, height=560)
    with self._window.frame:
      with ui.ScrollingFrame(vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
                              horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
        with ui.VStack(spacing=0, style={"margin": 10}):
          self._build_setup_section()
          ui.Spacer(height=10)
          with ui.CollapsableFrame("Water Tiles", height=0, collapsed=False):
            with ui.VStack(spacing=0, style={"margin": 6}):
              self._build_wave_tiles_section()
          with ui.CollapsableFrame("Own Ship", height=0, collapsed=False):
            with ui.VStack(spacing=0, style={"margin": 6}):
              self._build_own_ship_section()
          with ui.CollapsableFrame("Vessels", height=0, collapsed=False):
            with ui.VStack(spacing=0, style={"margin": 6}):
              self._build_vessel_section()
          with ui.CollapsableFrame("Ship Physics", height=0, collapsed=True):
            with ui.VStack(spacing=0, style={"margin": 6}):
              self._build_ship_physics_section()
          with ui.CollapsableFrame("Gerstner Wave", height=0, collapsed=False):
            with ui.VStack(spacing=0, style={"margin": 6}):
              self._build_wave_section()
          with ui.CollapsableFrame("Save / Load Scenario", height=0, collapsed=True):
            with ui.VStack(spacing=0, style={"margin": 6}):
              self._build_save_load_section()

  # ------------------------------------------------------------ Build / status / assets folder
  def _build_setup_section(self):
    ui.Button("Build scene", height=32, clicked_fn=self._on_build_clicked)
    self._status_label = ui.Label(self._status_text(), height=18, word_wrap=True,
                                   style={"color": 0xFFFFCC66})
    with ui.HStack(spacing=6):
      ui.Label("Boat assets:", width=80)
      self._mesh_dir_label = ui.Label(self._sc.get_mesh_dir() or "(not set)", word_wrap=True)
    ui.Button("Choose folder...", height=26, clicked_fn=self._on_choose_mesh_dir)

  def _status_text(self):
    return "status: ready" if self._sc.ready else "status: not built yet -- press Build scene"

  def _on_build_clicked(self):
    async def _run():
      self._status_label.text = "status: building..."
      try:
        await self._sc.setup_async()
      except Exception as exc:
        self._status_label.text = f"status: build failed ({exc}) -- see console"
        raise
      self._status_label.text = self._status_text()
      self._refresh_mesh_choices()

    omni.kit.async_engine.run_coroutine(_run())

  def _on_choose_mesh_dir(self):
    import omni.kit.window.filepicker as filepicker

    def _on_select(filename, dirname):
      # Folder-only pick: navigate into the target folder, then click Select without choosing a
      # specific file -- dirname is the chosen folder in that case. If a file got picked anyway, fall
      # back to its containing folder instead.
      chosen = dirname if not filename else os.path.dirname(os.path.join(dirname, filename))
      self._sc.set_mesh_dir(chosen)
      self._mesh_dir_label.text = chosen
      self._refresh_mesh_choices()
      self._mesh_dir_dialog.hide()

    def _on_cancel(filename, dirname):
      self._mesh_dir_dialog.hide()

    if self._mesh_dir_dialog is None:
      self._mesh_dir_dialog = filepicker.FilePickerDialog(
        "Select boat assets folder", apply_button_label="Select",
        click_apply_handler=_on_select, click_cancel_handler=_on_cancel,
      )
    self._mesh_dir_dialog.show(self._sc.get_mesh_dir())

  def _refresh_mesh_choices(self):
    choices = self._sc.list_mesh_choices()
    self._mesh_choices = [(m.mesh_id, m.mesh_id) for m in choices]
    self._no_real_assets = not choices or all(not m.usd_path for m in choices)
    if hasattr(self, "_own_ship_mesh_combo"):
      self._rebuild_combo(self._own_ship_mesh_combo, [label for label, _ in self._mesh_choices])
    if hasattr(self, "_vessel_mesh_combo"):
      self._rebuild_combo(self._vessel_mesh_combo, [label for label, _ in self._mesh_choices])

  @staticmethod
  def _rebuild_combo(combo, items):
    model = combo.model
    for item in list(model.get_item_children()):
      model.remove_item(item)
    for item in items:
      model.append_child_item(None, ui.SimpleStringModel(item))

  # ------------------------------------------------------------ Wave
  def _build_wave_section(self):
    wave = self._sc.state.wave
    self._wave_slider("Amplitude (m)", "amplitude_m", 0.0, 5.0, wave.amplitude_m)
    self._wave_slider("Wavelength (m)", "wavelength_m", 1.0, 60.0, wave.wavelength_m, fmt="{:.1f}")
    self._wave_slider("Speed (m/s)", "speed_mps", 0.0, 15.0, wave.speed_mps, fmt="{:.1f}")
    self._wave_slider("Steepness (0-1)", "steepness", 0.0, 1.0, wave.steepness)
    self._wave_slider("Wave trains", "num_waves", 1, 4, wave.num_waves, is_int=True, fmt="{}")

    with ui.HStack(spacing=6):
      ui.Label("Active radius (m)", width=150)
      active_radius_slider = ui.FloatSlider(min=50.0, max=2000.0, height=20)
      active_radius_slider.model.set_value(150.0)
      active_radius_value_label = ui.Label("150", width=55)

      def _on_active_radius_changed(m):
        radius = m.get_value_as_float()
        active_radius_value_label.text = f"{radius:.0f}"
        self._sc.set_wave_active_radius(radius)

      active_radius_slider.model.add_value_changed_fn(_on_active_radius_changed)

  def _wave_slider(self, label, field, lo, hi, init, is_int=False, fmt="{:.2f}"):
    with ui.HStack(spacing=6):
      ui.Label(label, width=150)
      s = ui.IntSlider(min=lo, max=hi, height=20) if is_int else ui.FloatSlider(min=lo, max=hi, height=20)
      s.model.set_value(init)
      value_label = ui.Label(fmt.format(init), width=55)

      def _on_changed(m):
        val = m.get_value_as_int() if is_int else m.get_value_as_float()
        value_label.text = fmt.format(val)
        self._sc.set_wave(**{field: val})

      s.model.add_value_changed_fn(_on_changed)

  # ------------------------------------------------------------ Water tiles
  def _build_wave_tiles_section(self):
    with ui.HStack(spacing=6):
      ui.Label("Tile size", width=90)
      self._tile_level_slider = ui.IntSlider(min=0, max=MAX_TILE_LEVEL, height=20)
      self._tile_level_slider.model.set_value(self._tile_placement.tile_level)
      self._tile_level_label = ui.Label("", width=110)

      def _refresh_tile_level_label():
        level = self._tile_level_slider.model.get_value_as_int()
        res = self._tile_placement.resolution_for_level(level)
        self._tile_level_label.text = f"{size_for_level(level):.0f}m ({res}x{res})"

      def _on_level_changed(m):
        self._tile_placement.set_tile_level(m.get_value_as_int())
        _refresh_tile_level_label()

      self._tile_level_slider.model.add_value_changed_fn(_on_level_changed)
      _refresh_tile_level_label()
    with ui.HStack(spacing=6):
      ui.Label("Base resolution", width=90)
      self._base_res_slider = ui.IntSlider(min=0, max=len(BASE_RES_DISPLAY_STEPS) - 1, height=20)
      current_display = self._tile_placement.base_resolution - 1   # real = displayed + 1, see constant
      initial_idx = (BASE_RES_DISPLAY_STEPS.index(current_display)
                     if current_display in BASE_RES_DISPLAY_STEPS else 1)
      self._base_res_slider.model.set_value(initial_idx)
      self._base_res_label = ui.Label(str(BASE_RES_DISPLAY_STEPS[initial_idx]), width=40)

      def _on_base_resolution_changed(m):
        display = BASE_RES_DISPLAY_STEPS[m.get_value_as_int()]
        self._base_res_label.text = str(display)
        self._tile_placement.set_base_resolution(display + 1)
        _refresh_tile_level_label()

      self._base_res_slider.model.add_value_changed_fn(_on_base_resolution_changed)
    with ui.HStack(spacing=6):
      ui.Button("Place tiles with mouse", height=30, clicked_fn=self._on_place_tile_with_mouse)
      ui.Button("Delete tile with mouse", height=30, clicked_fn=self._on_delete_tile_with_mouse)
      ui.Button("Stop", height=30, clicked_fn=lambda: self._tile_placement.cancel())
    with ui.HStack(spacing=6):
      ui.Button("Undo last tile", height=28, clicked_fn=lambda: self._tile_placement.undo_last())
      ui.Button("Clear all tiles", height=28, clicked_fn=lambda: self._sc.clear_water_tiles())
    self._tile_status_label = ui.Label("", height=18, word_wrap=True, style={"color": 0xFFFFCC66})

  def _on_place_tile_with_mouse(self):
    self._placement.cancel()   # mutual exclusion -- only one mouse-placement mode listens at a time
    self._tile_placement.start(mode="place")

  def _on_delete_tile_with_mouse(self):
    self._placement.cancel()   # mutual exclusion -- only one mouse-placement mode listens at a time
    self._tile_placement.start(mode="delete")

  def _on_tile_status(self, text):
    self._tile_status_label.text = text

  # ------------------------------------------------------------ Own ship
  def _build_own_ship_section(self):
    labels = [label for label, _ in self._mesh_choices] or ["(press Build scene first)"]
    self._own_ship_mesh_combo = ui.ComboBox(0, *labels, height=24)

    def _apply():
      if not self._mesh_choices:
        return
      idx = self._own_ship_mesh_combo.model.get_item_value_model().get_value_as_int()
      _, mesh_id = self._mesh_choices[idx]
      used_fallback = self._sc.set_own_ship_mesh(mesh_id)
      self._status_label.text = NO_ASSETS_WARNING if used_fallback else self._status_text()

    ui.Button("Apply to own ship", height=28, clicked_fn=_apply)

    with ui.HStack(spacing=6):
      ui.Label("Linear", width=50)
      self._ship_linear_field = ui.FloatField(height=24)
      self._ship_linear_field.model.set_value(0.3)
      self._ship_linear_field.model.add_value_changed_fn(
        lambda m: self._sc.set_ship_speed(linear=m.get_value_as_float()))
      ui.Label("Angular", width=55)
      self._ship_angular_field = ui.FloatField(height=24)
      self._ship_angular_field.model.set_value(0.01)
      self._ship_angular_field.model.add_value_changed_fn(
        lambda m: self._sc.set_ship_speed(angular=m.get_value_as_float()))

    with ui.HStack(spacing=0):
      ui.Spacer(width=70)
      self._make_hold_button("Forward (I)", "forward", width=100)
      ui.Spacer(width=70)
    with ui.HStack(spacing=0):
      self._make_hold_button("Left (J)", "left", width=70)
      self._make_hold_button("Back (K)", "back", width=100)
      self._make_hold_button("Right (L)", "right", width=70)
    ui.Label("Keyboard: I/J/K/L also works -- click into the 3D viewport first (not this panel).",
             height=16, word_wrap=True, style={"color": 0xFF888888})

  def _make_hold_button(self, label, control_name, width=80):
    """A button that drives ScenarioController.ship_control(control_name, pressed) for as long as it's
    held down -- the UI-button equivalent of holding one of the I/J/K/L keys (backends/ship.py's
    ShipBackend._controls), not a single discrete nudge. NOT yet confirmed live (same standing caveat as
    every other Kit-API-dependent piece of this UI) whether set_mouse_pressed_fn/set_mouse_released_fn's
    (x, y, button, modifier) callback signature is exactly right for this project's installed Kit
    version -- button index 0 is treated as the primary/left mouse button."""
    btn = ui.Button(label, height=28, width=width)

    def _pressed(x, y, button, modifier):
      if button == 0:
        self._sc.ship_control(control_name, True)

    def _released(x, y, button, modifier):
      if button == 0:
        self._sc.ship_control(control_name, False)

    btn.set_mouse_pressed_fn(_pressed)
    btn.set_mouse_released_fn(_released)
    return btn

  # ------------------------------------------------------------ Ship physics
  def _build_ship_physics_section(self):
    sp = self._sc.state.ship_physics
    self._physics_slider("Draft / beam ratio", "draft_to_beam_ratio", 0.05, 1.0, sp.draft_to_beam_ratio)
    self._physics_slider("Waterplane coeff", "waterplane_coeff", 0.3, 1.0, sp.waterplane_coeff)
    self._physics_slider("GM coeff (roll)", "gm_coeff", 0.05, 0.3, sp.gm_coeff)
    self._physics_slider("GML coeff (pitch)", "gml_coeff", 0.2, 1.0, sp.gml_coeff)
    self._physics_slider("Roll gyration", "roll_gyration_coeff", 0.1, 0.6, sp.roll_gyration_coeff)
    self._physics_slider("Pitch gyration", "pitch_gyration_coeff", 0.1, 0.5, sp.pitch_gyration_coeff)
    self._physics_slider("Yaw gyration", "yaw_gyration_coeff", 0.1, 0.5, sp.yaw_gyration_coeff)
    self._physics_slider("Damping ratio", "damping_ratio", 0.0, 1.0, sp.damping_ratio)

  def _physics_slider(self, label, field, lo, hi, init, fmt="{:.2f}"):
    with ui.HStack(spacing=6):
      ui.Label(label, width=150)
      s = ui.FloatSlider(min=lo, max=hi, height=20)
      s.model.set_value(init)
      value_label = ui.Label(fmt.format(init), width=55)

      def _on_changed(m):
        val = m.get_value_as_float()
        value_label.text = fmt.format(val)
        self._sc.set_ship_physics(**{field: val})

      s.model.add_value_changed_fn(_on_changed)

  # ------------------------------------------------------------ Vessels
  def _build_vessel_section(self):
    labels = [label for label, _ in self._mesh_choices] or ["(press Build scene first)"]

    with ui.HStack(spacing=6):
      ui.Label("Mesh", width=60)
      self._vessel_mesh_combo = ui.ComboBox(0, *labels, height=24)
    with ui.HStack(spacing=6):
      ui.Label("Behavior", width=60)
      self._behavior_combo = ui.ComboBox(0, *BEHAVIORS, height=24)
    with ui.HStack(spacing=6):
      ui.Label("Linear", width=50)
      self._linear_field = ui.FloatField(height=24)
      self._linear_field.model.set_value(0.3)
      ui.Label("Angular", width=55)
      self._angular_field = ui.FloatField(height=24)
      self._angular_field.model.set_value(0.01)
      ui.Label("([ / ] rotate while placing)", style={"color": 0xFF888888})

    with ui.HStack(spacing=6):
      ui.Button("Place with mouse", height=30, clicked_fn=self._on_place_with_mouse)
      ui.Button("Finish Path", height=30, clicked_fn=lambda: self._placement.finish_path())
      ui.Button("Cancel", height=30, clicked_fn=lambda: self._placement.cancel())
    self._path_status_label = ui.Label("", height=18, style={"color": 0xFFFFCC66})

    with ui.HStack(spacing=6):
      ui.Label("X (m)", width=40)
      self._x_field = ui.FloatField(height=24)
      ui.Label("Y (m)", width=40)
      self._y_field = ui.FloatField(height=24)
    with ui.HStack(spacing=6):
      ui.Label("Yaw (deg)", width=70)
      self._yaw_field = ui.FloatField(height=24)
      self._yaw_field.model.add_value_changed_fn(lambda m: self._placement.set_yaw(m.get_value_as_float()))
      ui.Button("-", width=28, height=24, clicked_fn=lambda: self._placement.nudge_yaw(-1.0))
      ui.Button("+", width=28, height=24, clicked_fn=lambda: self._placement.nudge_yaw(1.0))
    with ui.HStack(spacing=6):
      ui.Label("Step (deg)", width=70)
      self._yaw_step_field = ui.FloatField(height=24)
      self._yaw_step_field.model.set_value(15.0)
      self._yaw_step_field.model.add_value_changed_fn(
        lambda m: self._placement.set_yaw_step(m.get_value_as_float()))

    with ui.HStack(spacing=6):
      ui.Label("Waypoints", width=70)
      self._waypoints_field = ui.StringField(height=24)
    ui.Button("Spawn vessel (typed position)", height=30, clicked_fn=self._on_spawn_vessel)

    with ui.HStack(spacing=6):
      show_paths_checkbox = ui.CheckBox(width=24, height=24)
      show_paths_checkbox.model.set_value(True)
      show_paths_checkbox.model.add_value_changed_fn(
        lambda m: self._placement.set_show_paths(m.get_value_as_bool()))
      ui.Label("Show paths", height=24)

    with ui.HStack(spacing=6):
      ui.Label("Random swarm count", width=120)
      self._count_field = ui.IntField(height=24)
      self._count_field.model.set_value(5)
      ui.Button("Set", width=60, height=24, clicked_fn=self._on_set_count)
    ui.Button("Clear all vessels", height=28, clicked_fn=lambda: self._placement.clear_all())

  def _on_place_with_mouse(self):
    if not self._mesh_choices:
      return
    if self._no_real_assets:
      self._status_label.text = NO_ASSETS_WARNING
    idx = self._vessel_mesh_combo.model.get_item_value_model().get_value_as_int()
    _, mesh_id = self._mesh_choices[idx]
    behavior = BEHAVIORS[self._behavior_combo.model.get_item_value_model().get_value_as_int()]
    self._placement.set_yaw(self._yaw_field.model.get_value_as_float())
    self._placement.set_yaw_step(self._yaw_step_field.model.get_value_as_float())
    self._tile_placement.cancel()   # mutual exclusion -- only one mouse-placement mode listens at a time
    self._placement.start(mesh_id, behavior, linear_speed=self._linear_field.model.get_value_as_float(),
                           angular_speed=self._angular_field.model.get_value_as_float())

  def _on_placement_status(self, text):
    self._path_status_label.text = text

  def _on_waypoint_count(self, n):
    self._path_status_label.text = f"{n} waypoint(s) placed -- click to add more, then Finish Path"

  def _on_placement_yaw_changed(self, yaw_deg):
    self._yaw_field.model.set_value(yaw_deg)

  def _on_spawn_vessel(self):
    if not self._mesh_choices:
      return
    if self._no_real_assets:
      self._status_label.text = NO_ASSETS_WARNING
    idx = self._vessel_mesh_combo.model.get_item_value_model().get_value_as_int()
    _, mesh_id = self._mesh_choices[idx]
    behavior = BEHAVIORS[self._behavior_combo.model.get_item_value_model().get_value_as_int()]
    waypoints = self._parse_waypoints(self._waypoints_field.model.get_value_as_string())
    linear = self._linear_field.model.get_value_as_float()
    angular = self._angular_field.model.get_value_as_float()

    if behavior == "follow_path":
      if len(waypoints) < 2:
        print("[maritime_scenario] follow_path needs >=2 points in the waypoints field (first = spawn point)")
        return
      start, rest = waypoints[0], waypoints[1:]
      vessel = self._sc.spawn_vessel(mesh_id, start[0], start[1], None, behavior, waypoints=rest,
                                      linear_speed=linear, angular_speed=angular)
      if vessel is not None:
        self._placement._draw_persistent_path(vessel.name, waypoints)   # typed path needs drawing too
    else:
      self._sc.spawn_vessel(mesh_id, self._x_field.model.get_value_as_float(),
                             self._y_field.model.get_value_as_float(),
                             self._yaw_field.model.get_value_as_float(), behavior,
                             linear_speed=linear, angular_speed=angular)

  @staticmethod
  def _parse_waypoints(text):
    points = []
    for chunk in text.split(";"):
      chunk = chunk.strip()
      if not chunk:
        continue
      x_str, _, y_str = chunk.partition(",")
      try:
        points.append((float(x_str), float(y_str)))
      except ValueError:
        print(f"[maritime_scenario] skipping malformed waypoint {chunk!r} (expected 'x,y')")
    return points

  def _on_set_count(self):
    if self._no_real_assets:
      self._status_label.text = NO_ASSETS_WARNING
    self._sc.set_vessel_count(self._count_field.model.get_value_as_int())

  # ------------------------------------------------------------ Save / load
  def _build_save_load_section(self):
    self._filename_field = ui.StringField(height=24)
    self._filename_field.model.set_value("scenario1.json")
    with ui.HStack(spacing=6):
      ui.Button("Save", height=30, clicked_fn=lambda: self._sc.save(self._filename_field.model.get_value_as_string()))
      ui.Button("Load", height=30, clicked_fn=self._on_load_clicked)

  def _on_load_clicked(self):
    self._sc.load(self._filename_field.model.get_value_as_string())
    self._refresh_mesh_choices()
    self._placement.redraw_all_persistent_paths()

  # ------------------------------------------------------------ window lifecycle
  @property
  def visible(self):
    return self._window.visible

  @visible.setter
  def visible(self, value):
    self._window.visible = value

  def destroy(self):
    self._placement.teardown()
    self._tile_placement.teardown()
    self._window.destroy()
