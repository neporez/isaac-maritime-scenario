"""Kit entry point: menu item + window, sharing the API controller -- same pattern as weather-fx's own
extension.py."""
import carb
import carb.settings
import omni.ext

from . import api

SETTING_OPEN_ON_STARTUP = "/exts/maritime.scenario/open_window_on_startup"
MENU_PARENT = "Window"


class MaritimeScenarioExtension(omni.ext.IExt):
  def on_startup(self, ext_id):
    self._controller = api.get_controller()
    self._window = None
    self._menu_items = []
    try:
      from omni.kit.menu.utils import MenuItemDescription, add_menu_items

      self._menu_items = [MenuItemDescription(name="Maritime Scenario", onclick_fn=self.show_window)]
      add_menu_items(self._menu_items, MENU_PARENT)
    except Exception as exc:  # headless apps may have no menu
      carb.log_info(f"maritime_scenario: no menu ({exc})")
    if carb.settings.get_settings().get(SETTING_OPEN_ON_STARTUP):
      self.show_window()

  def show_window(self, *_):
    if self._window is None:
      from .ui.window import ScenarioWindow

      self._window = ScenarioWindow(self._controller)
    self._window.visible = True

  def on_shutdown(self):
    if self._menu_items:
      try:
        from omni.kit.menu.utils import remove_menu_items

        remove_menu_items(self._menu_items, MENU_PARENT)
      except Exception:
        pass
    if self._window is not None:
      self._window.destroy()
      self._window = None
    api.shutdown_controller()
