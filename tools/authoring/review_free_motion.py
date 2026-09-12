"""Render the actual layered rig for pose review; not native Cubism evidence."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
from build_v5 import V5Builder
from diagnose_rig import DiagnosticRenderer, motion_parameters
from maple_motions import build_motion
from tools.authoring.animation_specs import ANIMATIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.source/'layers.json').read_text(encoding='utf8'))
    builder = V5Builder(manifest, args.source)
    rig = builder.build()
    renderer = DiagnosticRenderer(rig, args.source, 384)
    defaults = {p.id: p.default for p in rig.parameters}
    records = []
    for state, phases in [('happy', [0, .3, .6, 1]), ('drag_left', [0]),
                          ('drag_right', [0]), ('fall_float', [0, .5]),
                          ('land', [0, .16, .35, .58, .86, .94, 1]),
                          ('sleep_exit', [0, .15, .3, .5, .75, 1])]:
        motion = args.output/(state+'.motion3.json')
        motion.write_text(json.dumps(build_motion(state, ANIMATIONS[state], defaults)), encoding='utf8')
        images = []
        for phase in phases:
            params = motion_parameters(motion, phase)
            frame, stats = renderer.render(params)
            frame.save(args.output/f'{state}-{phase:.2f}.png')
            images.append((phase, frame))
            records.append({'state': state, 'phase': phase, 'parameters': params, 'stats': stats})
        sheet = Image.new('RGB', (384*len(images), 412), (245, 245, 245))
        pen = ImageDraw.Draw(sheet)
        for i, (phase, frame) in enumerate(images):
            sheet.paste(frame, (i*384, 0), frame)
            pen.text((i*384+8, 388), f'IR {state} {phase:.2f}', fill=(30, 30, 30))
        sheet.save(args.output/(state+'-sheet.png'))
        print(f'{state}: {len(images)} offline frames', flush=True)
    (args.output/'report.json').write_text(json.dumps({'nativeEvidence': False, 'records': records}, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
