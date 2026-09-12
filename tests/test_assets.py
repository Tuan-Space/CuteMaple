from pathlib import Path
from PIL import Image
import json
ROOT=Path(__file__).resolve().parents[1]

def test_icon_files_exist():
    icon_path = ROOT / "assets" / "icon.png"
    assert icon_path.is_file()
    assert (ROOT / "assets" / "美腻枫.ico").is_file()
    icon = Image.open(icon_path).convert("RGBA")
    left, top, right, bottom = icon.getchannel("A").getbbox()
    assert right - left >= 225
    assert bottom - top >= 225

def test_runtime_has_only_native_assets():
    assert not (ROOT/'assets/sprites_v2').exists()
    model=json.loads((ROOT/'assets/live2d/Maple/Maple.model3.json').read_text(encoding='utf-8-sig'))
    assert len(model['FileReferences']['Motions'])==19
    assert not any(s.startswith('clean_') for s in model['FileReferences']['Motions'])
