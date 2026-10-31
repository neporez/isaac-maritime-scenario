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
  hand-picked per boat. Drive it with the on-screen Forward/Left/Back/Right buttons, set its spawn
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
2. In Isaac Sim: **Window → Extensions → ☰ → Settings → Extension Search Paths**, add
   `<repo>/exts` (the path to this cloned repo's `exts` folder).
3. Find **"Maritime Scenario"** in the extension list and enable it. The panel opens automatically
   (`open_window_on_startup`).

## How to use

1. Click **Build scene** to set up the wave infrastructure and an empty vessel pool. This does *not*
   place any visible water or spawn an own ship by itself.
2. Set the sea state and lay down water — see [Water & Waves](docs/water-and-waves.md).
3. Register boat assets (or just use the built-in placeholder box) and spawn an own ship + vessels — see
   [`assets/models/README.md`](assets/models/README.md).
4. Press **Play**.

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
docs/          screenshot-driven usage guides (water & waves; boat assets/scenarios live in assets/models/)
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
