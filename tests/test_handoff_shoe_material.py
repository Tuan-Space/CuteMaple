"""Regression for the pointed left shoe during wall-to-rope transfer."""
from pathlib import Path
import sys
import math
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools/authoring'))
from climb_refinement import _handoff_map, handoff_material_projection

SOURCE=((.50,.60),(.50,.69),(.50,.78))
RIGHT=((.55,.68),(.64,.69),(.69,.80))
LEFT=tuple((1.016-x,y) for x,y in RIGHT)

def test_left_start_preserves_reflected_painted_shoe_in_its_local_frame():
    # Points below the trouser/ankle blend are actual shoe material.
    for x,y in ((.487,.80),(.510,.80),(.49,.81),(.52,.815)):
        right=_handoff_map((x,y),SOURCE,RIGHT,-90,1)
        left=_handoff_map((x,y),SOURCE,LEFT,90,-1)
        np.testing.assert_allclose(left,(1.016-right[0],right[1]),atol=1e-12)

@pytest.mark.parametrize('near',[False,True])
def test_visible_left_shoe_retains_width_before_tucking_under_the_skirt(near):
    for seconds in np.linspace(0,.85,18):
        assert handoff_material_projection(seconds,near,-1)==-1.
    assert handoff_material_projection(1.3,near,-1)==1.

@pytest.mark.parametrize('near',[False,True])
def test_ankle_rotation_never_stretches_or_shears_the_toe_axis(near):
    a=np.array([.50,.805]); b=np.array([.50,.825]); width=np.array([.52,.805])
    for seconds in np.linspace(0,1.8,181):
        projection=handoff_material_projection(seconds,near,-1)
        angle=90*(1-min(seconds/1.3,1))
        pa,pb,pw=[np.array(_handoff_map(p,SOURCE,LEFT,angle,projection)) for p in (a,b,width)]
        assert abs(np.linalg.norm(pb-pa)-.02)<1e-12
        assert abs(np.dot(pb-pa,pw-pa))<1e-12
        assert np.linalg.norm(pw-pa)<=.02+1e-12
        assert handoff_material_projection(seconds,near,1)==1.
