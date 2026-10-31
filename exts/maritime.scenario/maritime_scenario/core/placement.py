"""2D oriented-bounding-box collision math -- pure Python, no pxr. Extracted verbatim from
boat_mesh_pipeline/06_scenario_editor/other_boats.py's _ob_obb_corners()/_ob_obb_overlap() (itself
already a numpy-free port of 03_isaac_sim/spawn_train_capture.py's obb_overlap()), used by the vessels
backend's set_count() random swarm to keep initial placements collision-free."""
import math


def obb_corners(cx, cy, dx, dy, yaw_rad):
  hx, hy = dx / 2.0, dy / 2.0
  local = ((hx, hy), (hx, -hy), (-hx, -hy), (-hx, hy))
  c, s = math.cos(yaw_rad), math.sin(yaw_rad)
  return [(cx + c * lx - s * ly, cy + s * lx + c * ly) for lx, ly in local]


def obb_overlap(box_a, box_b):
  """2D separating-axis test between two oriented rectangles (cx, cy, dx, dy, yaw_rad)."""
  corners_a, corners_b = obb_corners(*box_a), obb_corners(*box_b)
  for corners in (corners_a, corners_b):
    for i in range(4):
      x1, y1 = corners[i]
      x2, y2 = corners[(i + 1) % 4]
      axis_x, axis_y = -(y2 - y1), (x2 - x1)
      norm = math.hypot(axis_x, axis_y)
      if norm == 0:
        continue
      axis_x, axis_y = axis_x / norm, axis_y / norm
      proj_a = [px * axis_x + py * axis_y for px, py in corners_a]
      proj_b = [px * axis_x + py * axis_y for px, py in corners_b]
      if max(proj_a) < min(proj_b) or max(proj_b) < min(proj_a):
        return False   # separating axis found -> no overlap
  return True
