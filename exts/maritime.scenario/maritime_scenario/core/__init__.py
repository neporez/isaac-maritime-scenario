"""Pure Python, no omni/pxr imports anywhere in this package -- see the top-level CLAUDE.md's
'core/Kit boundary' note. Safe to `import maritime_scenario.core` from plain Python/pytest."""
from . import events, gerstner, meshes, placement, scenario_io, ship_motion, state, vessel

__all__ = ["events", "gerstner", "meshes", "placement", "scenario_io", "ship_motion", "state", "vessel"]
