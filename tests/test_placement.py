from maritime_scenario.core import placement


def test_obb_corners_axis_aligned():
  corners = placement.obb_corners(10.0, 5.0, 4.0, 2.0, 0.0)
  expected = sorted([(12.0, 6.0), (12.0, 4.0), (8.0, 4.0), (8.0, 6.0)])
  assert sorted(corners) == expected


def test_obb_overlap_identical_boxes():
  box = (0.0, 0.0, 4.0, 2.0, 0.0)
  assert placement.obb_overlap(box, box)


def test_obb_overlap_far_apart_boxes_dont_overlap():
  box_a = (0.0, 0.0, 4.0, 2.0, 0.0)
  box_b = (100.0, 100.0, 4.0, 2.0, 0.0)
  assert not placement.obb_overlap(box_a, box_b)


def test_obb_overlap_touching_edge_counts_as_overlapping():
  # box_a spans x in [-2, 2], box_b spans x in [2, 6] -- share only the boundary line. The separating-
  # axis test uses a strict "<" (max(proj_a) < min(proj_b)), so an exact touch does NOT count as a
  # separating axis -- the original other_boats.py algorithm treats this as overlapping (conservative:
  # a placement retry should look for an actual gap, not accept zero clearance), and this port keeps
  # that behavior exactly, not "fixed" to the more permissive <=.
  box_a = (0.0, 0.0, 4.0, 2.0, 0.0)
  box_b = (4.0, 0.0, 4.0, 2.0, 0.0)
  assert placement.obb_overlap(box_a, box_b)

  # nudged apart by a hair -> genuinely separated now
  box_c = (4.001, 0.0, 4.0, 2.0, 0.0)
  assert not placement.obb_overlap(box_a, box_c)


def test_obb_overlap_rotated_boxes():
  # two boxes centered at the same point but rotated 45 deg from each other -- must overlap
  box_a = (0.0, 0.0, 4.0, 1.0, 0.0)
  box_b = (0.0, 0.0, 4.0, 1.0, 0.7853981633974483)   # 45 deg
  assert placement.obb_overlap(box_a, box_b)

  # rotated box far enough away not to overlap
  box_c = (20.0, 20.0, 4.0, 1.0, 0.7853981633974483)
  assert not placement.obb_overlap(box_a, box_c)
