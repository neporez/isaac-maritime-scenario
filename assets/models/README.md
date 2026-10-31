# Boat models go here

This repo does not bundle any 3D models -- the ones this project was originally developed against are
third-party web-sourced assets of unclear license, so they can't be redistributed here. You need to
supply your own.

## What's expected

`core/meshes.py` registers 13 mesh slots (`boat1`..`boat13`), each pointing at `<this folder>/boatN.usd`
with a hand-tuned `scale` constant. Put your own `.usd`/`.usdz`/`.usdc` boat models here, named to match
(or edit `core/meshes.py`'s `_MODEL_SPECS` list to match whatever you actually have -- `mesh_id` is just
a stable key, it doesn't have to be `boatN`, and you can have fewer or more than 13).

## Scale matters -- read this before you spawn anything

USD reference composition ignores `metersPerUnit` metadata. If your model's raw vertex coordinates are
in centimeters (common for web-sourced assets), a `scale` of 1.0 will spawn a boat the size of a building.
Each registry entry's `scale` is a hand-tuned constant for THAT specific model, found empirically (place
it, look at it next to the wave/own ship, adjust `scale` until its size looks right, repeat) -- there's
no reliable way to derive it from the file alone. `core/meshes.py`'s own module docstring has the full
gotcha list (origin offset, rot_x, etc.) if a model looks right-sized but sits oddly or faces sideways.

## Alternative: point elsewhere entirely

Set the `MARITIME_SCENARIO_MESH_DIR` environment variable to any directory instead of using this one --
`core/meshes.py` reads that first, falling back to this folder only if it's unset.
