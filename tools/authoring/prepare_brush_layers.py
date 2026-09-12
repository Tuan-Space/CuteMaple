"""Produce a new code-drawn brush and reference unmodified closed-fist art.

Only an explicitly selected v5.1 revision may be written. Existing character
PNGs are read/referenced verbatim; no recolouring, cutting or repainting occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


def brush_art(size, wrist, grip):
    """Small wood-handled dusting brush, newly drawn on a blank canvas."""
    image = Image.new('RGBA', (size, size))
    draw = ImageDraw.Draw(image)
    x, y = grip[0]*size, grip[1]*size
    # Visible handle extends both below the closed fist and into the ferrule.
    draw.rounded_rectangle((x-3, y-24, x+3, y+8), radius=2,
                           fill=(166, 112, 62, 255), outline=(102, 65, 38, 255), width=1)
    draw.line((x-1, y-20, x-1, y+6), fill=(219, 171, 102, 255), width=1)
    # Blue wrap and warm metal edge repeat the costume's palette without
    # reusing or altering any piece of the character's actual ornamentation.
    draw.rounded_rectangle((x-12, y-29, x+12, y-20), radius=3,
                           fill=(123, 180, 200, 255), outline=(129, 94, 49, 255), width=1)
    draw.line((x-11, y-22, x+11, y-22), fill=(229, 187, 102, 255), width=2)
    # A broad, slightly uneven bristle edge reads as a brush at pet scale.
    bristles = [(x-12, y-27), (x-16, y-35), (x-16, y-45),
                (x-11, y-43), (x-8, y-47), (x-3, y-45),
                (x+1, y-48), (x+6, y-45), (x+11, y-47),
                (x+16, y-44), (x+16, y-35), (x+12, y-27)]
    draw.polygon(bristles, fill=(226, 198, 148, 255), outline=(133, 97, 53, 255))
    for offset in (-12, -8, -4, 0, 4, 8, 12):
        draw.line((x+offset*.8, y-28, x+offset, y-42-abs(offset)%3),
                  fill=(167, 132, 82, 230), width=1)
    return image, (x/size, (y-41)/size)


def prepare_brush_layers(manifest_path):
    path = Path(manifest_path).resolve()
    if not path.parent.name.startswith('v5-1-') or path.name != 'layers.json':
        raise ValueError('Brush assets must use an explicit v5-1 revision')
    data = json.loads(path.read_text(encoding='utf8'))
    if data.get('version') != 5 or data['width'] != data['height'] or data['width'] != 1024:
        raise ValueError('Expected the current square 1024-pixel v5 source manifest')
    by_id = {layer['id']: layer for layer in data['layers']}
    if any(name.startswith(('clean_brush_', 'clean_hand_')) for name in by_id):
        raise ValueError('Brush layers are already installed; do not overwrite reviewed assets')
    provenance = {'source': 'new code-drawn brush; unmodified original fist references',
                  'generatorSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'hands': {}}
    for side in ('l', 'r'):
        wrist = (.495, .518) if side == 'l' else (.526, .518)
        # Both chosen points lie inside the unchanged finger paint, where the
        # narrow wooden handle is covered by the curl rather than stuck to a wrist.
        grip = (wrist[0]+(2 if side == 'l' else -2)/1024, wrist[1]-16/1024)
        art, tip = brush_art(data['width'], wrist, grip)
        name = 'clean_brush_'+side
        destination = path.parent/'layers'/(name+'.png')
        if destination.exists():
            raise ValueError(f'Refuse to replace preexisting asset: {destination}')
        art.save(destination)
        data['layers'].append({'id': name, 'role': 'accessory', 'file': 'layers/'+name+'.png',
                               'draw_order': 165, 'opacity': 0, 'side': side,
                               'clean_brush': True, 'gripSourceUv': list(grip),
                               'tipSourceUv': list(tip), 'wristSourceUv': list(wrist)})
        for suffix, order in (('palm', 164), ('fingers', 166)):
            source = by_id[f'hand_{side}_grip_{suffix}']
            image_path = path.parent/source['file']
            name = f'clean_hand_{side}_{suffix}'
            provenance['hands'][name] = {'sourceId': source['id'], 'sourceFile': source['file'],
                'sha256': hashlib.sha256(image_path.read_bytes()).hexdigest(), 'rgbaUnchanged': True}
            data['layers'].append({'id': name, 'role': 'accessory', 'file': source['file'],
                'draw_order': order, 'opacity': 0, 'side': side, 'clean_brush_hand': True,
                'gripSourceUv': list(grip), 'wristSourceUv': list(wrist)})
    data['brushRefinementVersion'] = 1
    data['brushProvenance'] = provenance
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    return provenance


def revise_brush_layers(manifest_path):
    """Regenerate only the two code-native tools after their prior archive.

    Character PNGs and unchanged fist references are never rewritten. The
    material grip stays fixed while the wider brush's new bristle point is
    recorded for metadata and native triangle verification.
    """
    path = Path(manifest_path).resolve()
    if not path.parent.name.startswith('v5-1-') or path.name != 'layers.json':
        raise ValueError('Brush revision requires the explicit v5-1 manifest')
    data = json.loads(path.read_text(encoding='utf8'))
    if data.get('brushRefinementVersion') != 1 or data.get('brushGeometryRevision', 1) != 1:
        raise ValueError('Expected the original installed brush revision')
    if data.get('width') != 1024 or data.get('height') != 1024:
        raise ValueError('Expected original material coordinates')
    by_id = {layer['id']: layer for layer in data['layers']}
    provenance = data['brushProvenance']
    for hand in provenance['hands'].values():
        if hashlib.sha256((path.parent/hand['sourceFile']).read_bytes()).hexdigest() != hand['sha256']:
            raise ValueError('Original hand material changed before tool revision')
    previous = {}
    for side in ('l', 'r'):
        layer = by_id['clean_brush_'+side]
        if layer['file'] != f'layers/clean_brush_{side}.png':
            raise ValueError('Refuse to write outside the generated tool assets')
        destination = path.parent/layer['file']
        previous[layer['file']] = hashlib.sha256(destination.read_bytes()).hexdigest()
        art, tip = brush_art(1024, layer['wristSourceUv'], layer['gripSourceUv'])
        art.save(destination)
        layer['tipSourceUv'] = list(tip)
    provenance['previousToolSha256'] = previous
    provenance['generatorSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    data['brushGeometryRevision'] = 2
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    return provenance


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--revise-tool', action='store_true')
    args = parser.parse_args()
    function = revise_brush_layers if args.revise_tool else prepare_brush_layers
    print(json.dumps(function(args.manifest), indent=2))
