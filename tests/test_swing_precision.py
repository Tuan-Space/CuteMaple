"""The editable-source patch must never rewrite artwork or unrelated precision."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import pytest

pytest.importorskip('pydantic')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/authoring'))
from refine_swing_precision import patch_xml, PARAMETERS
from image2live2d.irr.schema import Parameter, Keyform
from image2live2d.backends.live2d.cmo3.model_xml import _param_source


def test_patch_preserves_all_non_precision_bytes_and_is_idempotent():
    blocks = []
    for name in sorted(PARAMETERS | {'ParamUntouched'}):
        blocks.append(f'<CParameterSource><i xs.n="decimalPlaces">1</i>'
                      f'<f xs.n="snapEpsilon">0.1</f><CParameterId idstr="{name}" />'
                      '<geometry>unchanged</geometry></CParameterSource>'.encode())
    original = b'<root>' + b''.join(blocks) + b'<texture>unchanged</texture></root>'
    updated, names = patch_xml(original)
    expected = b'<root>' + b''.join(b.replace(b'>1<', b'>5<').replace(b'>0.1<', b'>0.00001<')
        if b'ParamUntouched' not in b else b for b in blocks) + b'<texture>unchanged</texture></root>'
    assert updated == expected and set(names) == PARAMETERS
    assert patch_xml(updated)[0] == updated
    with pytest.raises(ValueError, match='Missing or duplicate'):
        patch_xml(b'<root/>')


@pytest.mark.parametrize('name', sorted(PARAMETERS))
def test_generator_cannot_reintroduce_swing_center_snap(name):
    parameter = Parameter(id=name, min=-1, max=1, keyforms=[Keyform(value=v) for v in [-1, 0, 1]])
    parent = ET.Element('parameters')
    _param_source(parent, parameter, '#1', '#2')
    assert float(parent.find("CParameterSource/f[@xs.n='snapEpsilon']").text) <= .00001
    assert int(parent.find("CParameterSource/i[@xs.n='decimalPlaces']").text) >= 5
