import math
import pytest
from freecad_mcp.assembly import geometry as g


def test_known_rotation_translation_and_mating_gap():
    # Moving pattern lies on YZ plane; mating must rotate into XY plane.
    moving = [(8, 4, 7), (8, 14, 7), (8, 4, 27)]
    fixed = [(1, 2, 3), (11, 2, 3), (1, -18, 3)]
    p = g.solve(fixed, moving, (0, 0, 1), (1, 0, 0), gap_mm=2)
    for a, b in zip(moving, fixed):
        assert g.transform(p, a) == pytest.approx(g.add(b, (0, 0, 2)))
    assert g.rotate(p['rotation'], (1, 0, 0)) == pytest.approx((0, 0, -1))
    r = p['rotation']
    assert g.dot(r[0], g.cross(r[1], r[2])) == pytest.approx(1)  # proper rotation, no reflection


def test_incompatible_pattern_fails_instead_of_accepting_first_pair():
    with pytest.raises(ValueError, match='cannot be aligned'):
        g.solve([(0, 0, 0), (10, 0, 0), (0, 12, 0)],
                [(0, 0, 0), (10, 0, 0), (0, -11, 0)], (0, 0, 1), (0, 0, 1))


@pytest.mark.parametrize('bad', [0, -1, math.inf, math.nan, True])
def test_invalid_tolerances_rejected(bad):
    with pytest.raises(ValueError):
        g.solve([(0,0,0), (1,0,0)], [(0,0,0), (1,0,0)], (0,0,1), (0,0,-1), tolerance_mm=bad)


def test_single_hole_and_degenerate_clocking_rejected():
    with pytest.raises(ValueError):
        g.solve([(0,0,0)], [(0,0,0)], (0,0,1), (0,0,1))
    with pytest.raises(ValueError):
        g.solve([(0,0,0), (0,0,2)], [(0,0,0), (0,0,2)], (0,0,1), (0,0,1))
