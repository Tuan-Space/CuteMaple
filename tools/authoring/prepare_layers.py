"""Export registered source-pixel layers and a PSD for Maple's editable rig.

Artwork edits are the two ImageGen underpaint plates. This exporter registers
those plates, limits them to newly occluded regions, and separates unchanged
source pixels into named layers. Original artwork is never overwritten.
All polygons and pivots are explicit and reviewable in source coordinates.
"""
from __future__ import annotations

import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'assets/authoring'
LAYERS = OUT / 'layers'
SIZE = 1024


def polygon(points):
    image = Image.new('L', (SIZE, SIZE))
    ImageDraw.Draw(image).polygon(points, fill=255)
    return np.array(image) > 0


def ellipse(box):
    image = Image.new('L', (SIZE, SIZE))
    ImageDraw.Draw(image).ellipse(box, fill=255)
    return np.array(image) > 0


def rect(box):
    image = Image.new('L', (SIZE, SIZE))
    ImageDraw.Draw(image).rectangle(box, fill=255)
    return np.array(image) > 0


def register_plate(name, original):
    plate = cv2.imread(str(OUT / 'generated' / name))
    source = cv2.cvtColor(original[:, :, :3], cv2.COLOR_RGB2GRAY)
    target = cv2.cvtColor(plate, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=6000)
    ka, da = sift.detectAndCompute(source, original[:, :, 3])
    kb, db = sift.detectAndCompute(target, None)
    matches = cv2.BFMatcher().knnMatch(db, da, k=2)
    good = [m for m, n in matches if m.distance < .68 * n.distance]
    if len(good) < 8:
        raise RuntimeError(f'Cannot register {name}: {len(good)} matches')
    a = np.float32([kb[m.queryIdx].pt for m in good])
    b = np.float32([ka[m.trainIdx].pt for m in good])
    matrix, inliers = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC,
                                               ransacReprojThreshold=3)
    aligned = cv2.warpAffine(plate, matrix, (SIZE, SIZE), flags=cv2.INTER_CUBIC)
    print(name, 'registration inliers', int(inliers.sum()), 'of', len(good))
    return cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB), matrix.tolist()


def main():
    LAYERS.mkdir(parents=True, exist_ok=True)
    source = np.array(Image.open(ROOT / 'assets/master/美腻枫_标准立绘_v1.png').convert('RGBA'))
    face_paint, fm = register_plate('face-underpaint.png', source)
    body_paint, bm = register_plate('costume-underpaint.png', source)
    visible = source[:, :, 3] > 0
    masks = {}
    # Natural garment occlusion lines, in original 1024 px canvas coordinates.
    masks['ribbon_l'] = polygon([(368,218),(405,233),(401,286),(391,363),(378,425),
        (366,479),(358,529),(349,577),(332,652),(324,699),(309,749),(297,800),
        (298,853),(312,895),(307,940),(237,940),(226,855),(254,767),(273,704),
        (296,619),(299,576),(283,512),(292,453),(313,357),(334,283)]) & visible
    masks['ribbon_r'] = polygon([(659,221),(629,242),(641,322),(665,402),(677,476),
        (690,540),(705,620),(720,688),(737,749),(750,802),(749,855),(731,891),
        (737,943),(805,938),(811,862),(787,779),(773,720),(752,659),(742,601),
        (750,539),(747,484),(723,400),(704,318),(684,255)]) & visible
    masks['ornament_l'] = polygon([(374,265),(400,265),(416,307),(416,371),
        (403,431),(381,454),(366,413),(369,341)]) & visible
    masks['ornament_r'] = polygon([(633,265),(659,264),(674,337),(678,406),
        (659,449),(640,426),(623,359),(624,305)]) & visible
    masks['face_base'] = polygon([(438,252),(464,219),(481,181),(515,161),(545,176),
        (572,210),(600,249),(616,288),(621,322),(606,347),(574,369),(534,382),
        (493,377),(455,358),(433,335),(424,304)]) & visible
    masks['arm_l'] = polygon([(441,386),(469,411),(490,458),(516,500),(516,534),
        (489,553),(456,576),(440,610),(416,655),(399,694),(372,710),(358,695),
        (365,648),(379,595),(388,547),(395,493),(410,436)]) & visible
    masks['arm_r'] = polygon([(565,381),(593,403),(623,449),(642,499),(649,548),
        (664,608),(682,667),(679,709),(652,708),(626,674),(604,625),(580,584),
        (549,559),(526,535),(521,505),(549,465)]) & visible
    masks['hand_l'] = polygon([(503,508),(519,517),(529,531),(522,550),
        (507,549),(494,538),(496,525)]) & visible
    masks['hand_r'] = polygon([(522,515),(538,507),(549,519),(548,534),
        (537,548),(522,550),(520,537)]) & visible
    masks['leg_l'] = rect((425,882,520,978)) & visible
    masks['leg_r'] = rect((521,882,615,978)) & visible
    # Make a strict source-pixel partition, keeping nearer parts over their bases.
    priority = ['hand_l','hand_r','ornament_l','ornament_r','ribbon_l','ribbon_r',
                'face_base','arm_l','arm_r','leg_l','leg_r']
    remaining = visible.copy()
    for name in priority:
        masks[name] &= remaining
        remaining &= ~masks[name]
    masks['hair_front'] = remaining & rect((0,0,1023,385))
    remaining &= ~masks['hair_front']
    masks['torso'] = remaining & rect((0,0,1023,558))
    masks['skirt'] = remaining & ~masks['torso']

    entries = []
    arrays = {}

    def add(name, role, data, mask=None, order=100, opacity=1., pivot=None):
        result = data.copy()
        if result.shape[2] == 3:
            result = np.dstack((result, np.full((SIZE,SIZE),255,dtype=np.uint8)))
        if mask is not None:
            result[:,:,3] = np.where(mask,result[:,:,3],0)
        result[result[:,:,3] == 0,:3] = 0
        image = Image.fromarray(result)
        image.save(LAYERS / f'{name}.png')
        entry = dict(id=name,role=role,file=f'layers/{name}.png',draw_order=order,opacity=opacity)
        if pivot is not None:
            entry['pivot'] = [v/SIZE for v in pivot]
        entries.append(entry)
        arrays[name] = image

    # Trace the generated *fabric silhouette*, then register it to the source.
    # Saturation cannot be used as alpha: it wrongly removes the white bodice
    # and creates holes when the original sleeves rise. Keep a solid hidden
    # costume under the source layers, bounded away from generated checkerboard.
    plate_silhouette = [(603,456),(657,456),(702,488),(716,525),(730,568),
        (743,616),(761,674),(780,727),(787,764),(784,789),(803,840),
        (825,890),(850,948),(852,1001),(802,1045),(711,1078),(626,1088),
        (541,1079),(453,1053),(413,1015),(423,945),(455,866),(485,791),
        (484,751),(499,697),(517,631),(532,577),(544,528),(562,494)]
    aligned_silhouette = np.asarray(plate_silhouette) @ np.asarray(bm)[:,:2].T + np.asarray(bm)[:,2]
    under_mask = polygon([tuple(map(round, p)) for p in aligned_silhouette])
    add('costume_underlay','torso',body_paint,under_mask,order=60,pivot=(519,440))

    hole_masks = {
        'eye_l':ellipse((435,268,509,320)), 'eye_r':ellipse((533,268,606,320)),
        'eyebrow_l':rect((440,251,497,269)), 'eyebrow_r':rect((548,251,606,269)),
        'mouth':ellipse((504,331,538,350)),
    }
    holes = np.logical_or.reduce(list(hole_masks.values()))
    face = source.copy()
    face[holes,:3] = face_paint[holes]
    add('face_base','face_base',face,masks['face_base'],order=180,pivot=(523,364))
    for side,cx in [('l',474),('r',568)]:
        eye_mask = hole_masks[f'eye_{side}'] & masks['face_base']
        pupil_mask = ellipse((cx-20,273,cx+20,315)) & eye_mask
        white = source.copy()
        # The hidden white below the original pupil is a simple solid-color shape.
        white[pupil_mask,:3] = (255,246,243)
        add(f'eye_{side}',f'eye_{side}',white,eye_mask,order=220,pivot=(cx,294))
        add(f'pupil_{side}',f'pupil_{side}',source,pupil_mask,order=230,pivot=(cx,294))
        add(f'eyebrow_{side}',f'eyebrow_{side}',source,
            hole_masks[f'eyebrow_{side}'] & masks['face_base'],order=225,pivot=(cx,265))
        # An editable vector curve converted to a layer, matching the original lash color.
        closed = Image.new('RGBA',(SIZE,SIZE))
        draw = ImageDraw.Draw(closed)
        pts = [(cx-25+i, 292+round(6*np.sin(np.pi*i/50))) for i in range(51)]
        draw.line(pts, fill=(84,43,36,255),width=3)
        draw.line([(cx-24,292),(cx-28,288)], fill=(84,43,36,255),width=2)
        draw.line([(cx+24,292),(cx+28,288)], fill=(84,43,36,255),width=2)
        add(f'eye_closed_{side}',f'eye_closed_{side}',np.array(closed),order=232,opacity=0,pivot=(cx,294))
    add('mouth','mouth',source,hole_masks['mouth'] & masks['face_base'],order=225,pivot=(521,341))

    spec = {
        'torso':('torso',80,(519,440)), 'skirt':('clothing',90,(520,555)),
        'arm_l':('arm_l',130,(444,399)), 'arm_r':('arm_r',135,(581,399)),
        'hand_l':('hand_l',150,(507,530)), 'hand_r':('hand_r',152,(536,530)),
        'leg_l':('leg_l',70,(480,870)), 'leg_r':('leg_r',72,(553,870)),
        'hair_front':('hair_front',200,(520,351)),
        'ribbon_l':('hair_back',110,(372,243)), 'ribbon_r':('hair_back',112,(663,243)),
        'ornament_l':('hair_side',210,(392,269)), 'ornament_r':('hair_side',212,(650,269)),
    }
    for name,(role,order,pivot) in spec.items():
        add(name,role,source,masks[name],order=order,pivot=pivot)

    # Small authored vector props; hidden in the model's neutral pose.
    glasses=Image.new('RGBA',(SIZE,SIZE)); d=ImageDraw.Draw(glasses)
    for cx in [474,568]:
        d.ellipse((cx-32,269,cx+32,324),outline=(157,110,55,255),width=3)
    d.arc((501,284,541,311),180,360,fill=(157,110,55,255),width=3)
    d.line([(436,283),(424,279)],fill=(157,110,55,255),width=3)
    d.line([(600,283),(613,279)],fill=(157,110,55,255),width=3)
    add('glasses','accessory',np.array(glasses),order=245,opacity=0,pivot=(523,294))
    maple=Image.new('RGBA',(SIZE,SIZE)); d=ImageDraw.Draw(maple)
    leaf=[(562,540),(542,526),(552,525),(543,507),(559,516),(570,491),(577,515),
          (593,504),(589,522),(601,524),(579,540),(575,558),(567,560)]
    d.polygon(leaf,fill=(230,141,70,255),outline=(171,106,58,255),width=2)
    d.line([(572,508),(571,558),(549,574)],fill=(148,99,59,255),width=2)
    add('maple','accessory',np.array(maple),order=165,opacity=0,pivot=(548,558))
    fan=Image.new('RGBA',(SIZE,SIZE)); d=ImageDraw.Draw(fan)
    d.line([(548,543),(574,478)],fill=(183,142,76,255),width=5)
    d.ellipse((535,445,613,519),fill=(225,247,254,249),outline=(204,166,91,255),width=3)
    for yy in [467,489]:
        d.arc((548,yy-6,600,yy+14),0,180,fill=(121,185,211,255),width=2)
    for x,y in [(566,462),(586,477),(560,494)]:
        d.ellipse((x-4,y-4,x+4,y+4),fill=(252,224,219,255))
    add('fan','accessory',np.array(fan),order=170,opacity=0,pivot=(548,543))
    swing=Image.new('RGBA',(SIZE,SIZE)); d=ImageDraw.Draw(swing)
    d.line([(362,0),(392,769)],fill=(183,144,83,255),width=7)
    d.line([(671,0),(647,769)],fill=(183,144,83,255),width=7)
    d.rounded_rectangle((375,757,665,780),7,fill=(171,128,77,255),outline=(133,97,61,255),width=2)
    add('swing','accessory',np.array(swing),order=55,opacity=0,pivot=(520,0))
    blush=Image.new('RGBA',(SIZE,SIZE)); d=ImageDraw.Draw(blush)
    d.ellipse((439,316,470,331),fill=(245,142,134,95));d.ellipse((577,316,609,331),fill=(245,142,134,95))
    blush=blush.filter(ImageFilter.GaussianBlur(4))
    add('blush','blush',np.array(blush),order=240,opacity=0,pivot=(523,320))

    entries.sort(key=lambda x:x['draw_order'])
    manifest = dict(width=SIZE,height=SIZE,layers=entries,source='assets/master/美腻枫_标准立绘_v1.png',
        registration={'face':fm,'costume':bm},
        provenance={'underpainting':'Built-in image_gen; original visible source pixels retained in named layers',
                    'props':'Locally authored simple vector shapes'})
    (OUT/'layers.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    psd=PSDImage.new('RGB',(SIZE,SIZE))
    composite=Image.new('RGBA',(SIZE,SIZE))
    for entry in entries:
        layer=PixelLayer.frompil(arrays[entry['id']],psd,name=entry['id'])
        layer.visible=entry['opacity']>0
        if entry['opacity']>0:
            composite=Image.alpha_composite(composite,arrays[entry['id']])
    psd.save(OUT/'Maple.psd')
    composite.save(OUT/'neutral-composite.png')
    report=dict(layers=len(entries),visible_layers=sum(e['opacity']>0 for e in entries),
                source_pixels_preserved_outside_generated_occlusions=True)
    (OUT/'layer-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    main()
