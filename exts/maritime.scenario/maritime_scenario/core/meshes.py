"""
Web-sourced boat mesh registry -- pure data, no pxr. Same mesh_id list/scale/rot_x as
boat_mesh_pipeline/06_scenario_editor's WEB_TARGET_OBJECTS (and 03_isaac_sim/spawn_ship.py,
spawn_train_capture_web.py before it) -- this project's own reconstructed meshes (usd_out/,
test_mesh_out/) are deliberately NOT included here (user, 2026-10-30: this is a standalone, shareable
extension, so it ships only with generic web-sourced models, not boat_mesh_pipeline's own private
LiDAR-derived reconstructions).

NO MODEL FILES ARE BUNDLED IN THIS REPO (user, 2026-10-30, a second, separate catch): the .usd files
themselves are third-party assets of unclear/unknown license, acquired for a private project -- this
registry only ever stores a PATH, so nothing of theirs ends up in git regardless, but that path must
not be hardcoded to one person's machine either, or the extension is simply broken for anyone else who
clones the repo. WEB_MODEL_DIR below resolves, in order: the MARITIME_SCENARIO_MESH_DIR env var, else
<repo>/assets/models (gitignored -- see assets/models/README.md for exactly what to put there and
why the scale/rot_x constants below are tuned the way they are). Supply your own boat1.usd..boat13.usd
(or fewer/more -- see below) there before building the scene; backends/meshes.py raises a clear error
naming the missing file and the env var if one isn't found, rather than failing silently.

Per CLAUDE.md's hard-won gotchas (boat_mesh_pipeline): (1) USD reference composition ignores
metersPerUnit, so a model's raw (~cm-ish) vertex units need a hand-tuned `scale` constant, never a
metadata-based correction -- if you supply your own differently-scaled models, these numbers will be
wrong for them and need re-tuning (place one, measure it in the viewport, adjust); (2) a model's local
origin usually sits at its base, not its bbox center, so placement needs a measured local_center offset
(computed in the runtime/backend layer, which has pxr -- see backends/meshes.py).
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MeshEntry:
  mesh_id: str     # stable key, e.g. "boat1" -- used in Vessel.mesh_id / scenario JSON, not the raw path
  usd_path: str
  scale: float
  rot_x_deg: float = 0.0


def _default_model_dir():
  # <this file>/../../../../assets/models -- i.e. <repo root>/assets/models, independent of where the
  # repo happens to be checked out (not hardcoded to any one person's home directory).
  here = os.path.dirname(os.path.abspath(__file__))
  repo_root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
  return os.path.join(repo_root, "assets", "models")


WEB_MODEL_DIR = os.environ.get("MARITIME_SCENARIO_MESH_DIR", _default_model_dir())

# mesh_id -> (filename, scale, rot_x_deg). Scale is hand-tuned per model (see module docstring) --
# these specific values were tuned for one particular set of 13 web-sourced models and will need
# re-tuning for a different set. Edit this list (add/remove/rename entries) to match whatever models
# you actually place in WEB_MODEL_DIR; mesh_id is just a stable key, it doesn't have to be "boatN".
_MODEL_SPECS = [
  ("boat1",  "boat1.usd",  0.03),
  ("boat2",  "boat2.usd",  0.03),
  ("boat3",  "boat3.usd",  0.04),
  ("boat4",  "boat4.usd",  0.04),
  ("boat5",  "boat5.usd",  0.1),
  ("boat6",  "boat6.usd",  0.04),
  ("boat7",  "boat7.usd",  0.03),
  ("boat8",  "boat8.usd",  0.04),
  ("boat9",  "boat9.usd",  0.03),
  ("boat10", "boat10.usd", 0.03),
  ("boat11", "boat11.usd", 0.03),
  ("boat12", "boat12.usd", 0.05),
  ("boat13", "boat13.usd", 0.015),
]

WEB_MESHES = [MeshEntry(mesh_id, os.path.join(WEB_MODEL_DIR, filename), scale)
              for mesh_id, filename, scale in _MODEL_SPECS]

_BY_ID = {m.mesh_id: m for m in WEB_MESHES}


def get(mesh_id):
  return _BY_ID[mesh_id]


def exists(mesh_id):
  return mesh_id in _BY_ID


def list_available():
  """Registered meshes whose .usd file actually exists on disk right now -- what the UI should offer,
  rather than the full registry (which may list models you haven't supplied yet)."""
  return [m for m in WEB_MESHES if os.path.exists(m.usd_path)]
