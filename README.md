# Maritime Scenario

An [NVIDIA Isaac Sim](https://developer.nvidia.com/isaac/sim) extension for building maritime
simulation scenes: physically-based Gerstner waves, a controllable own ship with naval-architecture-style
buoyancy, and autonomous vessel traffic — driven from a UI panel or a Python API.

![Maritime Scenario panel and scene overview](images/hero.png)

## Features

- **Gerstner wave simulation** — amplitude/wavelength/speed/steepness/wave-train count, all tunable live.
  Water is built from independent tiles (not one fixed plane), placed by mouse in a power-of-2
  quadtree-style grid with automatic T-junction-free LOD: place a few fine tiles near the ship and coarser
  ones further out, and same-size quartets auto-merge upward into bigger tiles. Distance-based tile
  activation plus an optional NVIDIA Warp GPU batching path keep this fast even with the water extending
  far past where any one tile would be practical alone.
- **Own ship** — naval-architecture-style heave/roll/pitch (a damped oscillator per axis, not a simple
  4-corner spring rig), with mass/stiffness/inertia *derived* from the hull's own measured size, not
  hand-picked per boat. Drive it with I/J/K/L (or the matching on-screen buttons), set its spawn
  position/speed, swap its mesh live.
- **Vessel traffic** — spawn a random collision-free swarm, or place vessels one at a time (by mouse,
  typed position, or a waypoint path), with `forward`/`backward`/`turn_left`/`turn_right`/`stationary`/
  `follow_path` behaviors. Vessels (and the own ship) are held at the edge of whatever water is actually
  placed by an invisible wall, instead of sailing off the map.
- **Bring your own boat assets** — point the extension at any folder of `.usd`/`.usdz`/`.usdc` models from
  the UI (no restart needed); with none registered yet, every mesh picker falls back to a simple
  physically-simulated placeholder box so you can test behavior/buoyancy/scenario layout before any real
  art asset exists. See [`assets/models/README.md`](assets/models/README.md) for the full guide
  (registering a model, how buoyancy is computed, every way to spawn a boat).
- **Save / load scenarios** as JSON — sea state, ship physics coefficients, own ship mesh, and every
  vessel's current position/behavior/remaining waypoints.

## Screenshots

![Water tiles placed around the own ship](images/water-tiles.png)
![Own ship riding the waves](images/own-ship.png)
![Vessel traffic with waypoint paths](images/vessel-traffic.png)

## Requirements

- NVIDIA Isaac Sim (Kit 107+)
- Python 3.10+ (Isaac Sim's own bundled Python)
- [`pytest`](https://pypi.org/project/pytest/) if you want to run the test suite outside Isaac Sim (the
  `core/` logic and several backend modules are pure-Python/pxr-only and testable without a running sim)

## Installation

1. Clone this repo somewhere Isaac Sim can see it:
   ```bash
   git clone https://github.com/neporez/isaac-maritime-scenario.git
   ```
2. In Isaac Sim, open **Window → Extensions**, click the gear icon, and add
   `isaac-maritime-scenario/exts` to the extension search paths.
3. Find **"Maritime Scenario"** in the extension list and enable it. The panel opens automatically
   (`open_window_on_startup`).

## Quick start

1. **Build scene** — sets up the wave infrastructure and an empty vessel pool. This does *not* place any
   visible water or spawn an own ship by itself.
2. **Water Tiles** — click "Place tiles with mouse" and click in the viewport to lay down water around
   where you want to work.
3. **Own Ship** — pick a mesh, optionally set a spawn X/Y/Z, and click "Apply to own ship" to spawn it.
4. **Vessels** — use "Random swarm count" for a quick AI-driven crowd, or "Place with mouse" /
   typed-position spawning for exact placement and waypoint paths.
5. Press **Play**. Drive the own ship with I/J/K/L (click into the 3D viewport first).

No boat models of your own yet? Every step above still works — see
[`assets/models/README.md`](assets/models/README.md) for how the placeholder-box fallback works and how
to register real assets once you have them.

### Python API

```python
from maritime_scenario import api as scenario

sc = scenario.get_controller()
await sc.setup_async()                 # wave infrastructure + empty vessel pool -- once per session
sc.set_wave(amplitude_m=1.2)
sc.set_own_ship_mesh("boat3")          # spawns the own ship
sc.spawn_vessel("boat1", x=50, y=20, behavior="forward")
sc.set_vessel_count(10)                # or: a random collision-free swarm of 10
```

## Project structure

```
exts/maritime.scenario/maritime_scenario/
  core/        pure-Python logic: Gerstner wave math, ship physics, placement/collision, save/load
  backends/    the pxr/PhysX plumbing around core/ -- wave tiles, own ship, vessel traffic, mesh assets
  ui/          the UI panel + mouse-click placement controllers
  runtime/     ScenarioManager -- owns state, wires backends together
  api.py       the public Python API (the UI panel uses exactly this, nothing more)
tests/         pytest suite for everything that doesn't require a running Isaac Sim
assets/models/ where you put your own boat assets (not bundled -- see its own README)
```

For the full engineering log — every design decision, measured number, live bug, and why things are built
the way they are — see [`CLAUDE.md`](CLAUDE.md). It's written as a working document for AI-assisted
development sessions, not polished prose, but it's the most complete record of *why* this project looks
the way it does.

## Built with Claude Code

This project was built in close collaboration with [Claude Code](https://claude.com/claude-code) —
design discussions, implementation, debugging, and the test suite were all developed together across many
sessions. Commits are attributed accordingly (`Co-Authored-By: Claude`). `CLAUDE.md` is the actual working
log from that process: hypotheses stated before testing them, live bugs found and fixed, and the reasoning
behind each design choice — published as-is rather than cleaned up, since that process is as much a part
of this project as the code it produced.

## License

[MIT](LICENSE). No 3D model assets are bundled — see
[`assets/models/README.md`](assets/models/README.md) for why and how to supply your own.
