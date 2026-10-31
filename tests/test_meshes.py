import os

import pytest

from maritime_scenario.core import meshes


def test_all_13_web_meshes_registered():
  assert len(meshes.WEB_MESHES) == 13
  ids = {m.mesh_id for m in meshes.WEB_MESHES}
  assert ids == {f"boat{i}" for i in range(1, 14)}


def test_get_returns_matching_entry():
  entry = meshes.get("boat5")
  assert entry.mesh_id == "boat5"
  assert entry.scale == 0.1
  assert entry.usd_path.endswith("boat5.usd")


def test_get_unknown_id_raises():
  with pytest.raises(KeyError):
    meshes.get("not_a_real_boat")


def test_exists():
  assert meshes.exists("boat1")
  assert not meshes.exists("ours_mesh_0")   # no "ours" meshes in this registry at all


def test_web_model_dir_not_hardcoded_to_one_machine():
  # the default must resolve relative to the repo, not to any one person's home directory -- this was
  # a real bug caught after the fact: an earlier version hardcoded /home/<user>/boat_model, which
  # broke the extension for anyone else who cloned the repo (the files aren't bundled, see the module
  # docstring, but the PATH to them still has to be portable)
  assert meshes.WEB_MODEL_DIR.endswith(os.path.join("assets", "models"))


def test_list_available_empty_when_no_files_present(tmp_path, monkeypatch):
  monkeypatch.setattr(meshes, "WEB_MODEL_DIR", str(tmp_path))
  monkeypatch.setattr(meshes, "WEB_MESHES",
                       [meshes.MeshEntry(mid, os.path.join(str(tmp_path), fname), scale)
                        for mid, fname, scale in meshes._MODEL_SPECS])
  assert meshes.list_available() == []


def test_list_available_only_includes_existing_files(tmp_path, monkeypatch):
  (tmp_path / "boat1.usd").write_text("fake")
  monkeypatch.setattr(meshes, "WEB_MODEL_DIR", str(tmp_path))
  monkeypatch.setattr(meshes, "WEB_MESHES",
                       [meshes.MeshEntry(mid, os.path.join(str(tmp_path), fname), scale)
                        for mid, fname, scale in meshes._MODEL_SPECS])
  available = meshes.list_available()
  assert len(available) == 1
  assert available[0].mesh_id == "boat1"
