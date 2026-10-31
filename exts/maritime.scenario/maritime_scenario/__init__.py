"""Maritime Scenario: maritime scenario building for Isaac Sim -- waves, a controllable own ship, and
other-vessel traffic with mouse placement, waypoint paths, and scenario save/load.

The ``core`` package is plain Python and can be imported anywhere (tests, tooling). Everything that
touches Omniverse lives in ``runtime``, ``backends``, ``ui`` and ``api`` and is only imported inside Kit.
"""
__version__ = "0.1.0"

try:
  import omni.ext  # noqa: F401
except ImportError:  # plain Python: core only
  pass
else:
  from .extension import MaritimeScenarioExtension  # noqa: F401
