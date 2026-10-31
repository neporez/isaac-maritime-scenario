"""What backends may know about the app: just a fresh-stage getter. Every script in the original
06_scenario_editor port re-fetches the stage fresh on every single use rather than caching it at
attach() time -- reloading/reopening the stage (e.g. the wave backend's own save-as-stage+reopen
restart) leaves a cached reference stale, and calling methods on it throws a confusing Boost.Python
argument-type error instead of a clean "invalid stage" one. ScenarioContext.stage() is that one place
to get it right, instead of every backend repeating `omni.usd.get_context().get_stage()` by hand."""


class ScenarioContext:
  def __init__(self, manager):
    self.manager = manager

  def stage(self):
    import omni.usd
    return omni.usd.get_context().get_stage()

  def timeline(self):
    import omni.timeline
    return omni.timeline.get_timeline_interface()
