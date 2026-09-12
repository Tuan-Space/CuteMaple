"""Mechanically register and separate approved v4 art into editable layers.

No image generation is performed here. Magenta is a dedicated background key;
white garments and skin are never classified as background by luminance.
Source crops/polygons and registrations are recorded for review.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[2]
SIZE=1024


def polygon(points, size=SIZE):
    mask=Image.new("L",(size,size))
    ImageDraw.Draw(mask).polygon(points,fill=255)
    return np.asarray(mask)>0


def rectangle(box,size=SIZE):
    left,top,right,bottom=box
    return polygon([(left,top),(right,top),(right,bottom),(left,bottom)],size)


def ellipse(box,size=SIZE):
    mask=Image.new("L",(size,size))
    ImageDraw.Draw(mask).ellipse(box,fill=255)
    return np.asarray(mask)>0


def rgba(path):
    return np.array(Image.open(path).convert("RGBA"))


def magenta_alpha(pixels):
    color=pixels[:,:,:3].astype(np.int16)
    key=(color[:,:,0]>180)&(color[:,:,2]>180)&(color[:,:,1]<100)&\
        (color[:,:,0]-color[:,:,1]>100)&(color[:,:,2]-color[:,:,1]>100)
    result=pixels.copy()
    result[:,:,3]=np.where(key,0,result[:,:,3])
    # Dedicated matte contamination is confined to pixels touching the keyed
    # background. Interior pink flowers and skin are never color-keyed.
    boundary=cv2.dilate(key.astype(np.uint8),np.ones((5,5),np.uint8))>0
    spill=np.minimum(color[:,:,0]-color[:,:,1],color[:,:,2]-color[:,:,1])
    fringe=boundary&~key&(spill>35)&(color[:,:,1]<160)
    coverage=np.clip(1-(spill.astype(float)-25)/215,.0,1.)
    result[:,:,3]=np.where(fringe,result[:,:,3]*coverage,result[:,:,3]).astype(np.uint8)
    result[fringe,0]=np.minimum(result[fringe,0],result[fringe,1]+35)
    result[fringe,2]=np.minimum(result[fringe,2],result[fringe,1]+8)
    result[result[:,:,3]==0,:3]=0
    return result


def warp(pixels,matrix):
    # Premultiply before resampling to avoid bright/colored matte fringes.
    data=pixels.astype(np.float32)/255
    data[:,:,:3]*=data[:,:,3:4]
    data=cv2.warpAffine(data,np.array(matrix,dtype=float),(SIZE,SIZE),flags=cv2.INTER_LINEAR)
    data[:,:,:3]=np.divide(data[:,:,:3],data[:,:,3:4],out=np.zeros_like(data[:,:,:3]),where=data[:,:,3:4]>1e-7)
    return np.uint8(np.clip(data*255,0,255))


def transfer(source, mask):
    result=source.copy()
    result[:,:,3]=np.where(mask,result[:,:,3],0)
    result[result[:,:,3]==0,:3]=0
    return result


class LayerExporter:
    def __init__(self,folder):
        self.folder=Path(folder)
        self.generated=self.folder/"generated"
        self.output=self.folder/"layers"
        self.output.mkdir(parents=True,exist_ok=True)
        self.layers=[]
        self.art={}
        self.audit={"mechanicalExtractionOnly":True,"registrations":{},"polygons":{}}

    def add(self,name,role,pixels,order,**metadata):
        pixels=pixels.copy()
        pixels[pixels[:,:,3]<8]=0  # Remove subvisible source matte speckles.
        if not np.any(pixels[:,:,3]):
            raise ValueError(f"Empty extracted part {name}")
        path=self.output/f"{name}.png"
        Image.fromarray(pixels).save(path)
        self.layers.append({"id":name,"role":role,"file":f"layers/{name}.png",
                            "draw_order":order,"opacity":metadata.pop("opacity",1),**metadata})
        self.art[name]=pixels

    def front(self):
        old=json.loads((ROOT/"assets/authoring/layers.json").read_text(encoding="utf8"))
        source=rgba(ROOT/"assets/master/美腻枫_标准立绘_v1.png")
        blank=magenta_alpha(rgba(self.generated/"front-costume-base.png"))
        matrix=[[.823,0,0],[0,.823,0]]
        body=warp(transfer(blank,rectangle((350,400,910,1110),blank.shape[0])),matrix)
        self.audit["registrations"]["front-costume-base.png"]=matrix
        self.add("neck","neck",transfer(source,polygon([(491,350),(549,350),(556,371),(522,405),(486,373)])),78,
                 family="neck",view="front",zone="body")
        torso_mask=rectangle((380,365,650,565))
        self.add("costume_underlay","torso",transfer(body,rectangle((0,0,1023,615))),60,
                 family="costume_underlay",view="front",zone="body")
        self.add("costume_underlay_skirt","clothing",transfer(body,rectangle((0,616,1023,1023))),61,
                 family="costume_underlay_skirt",view="front",zone="body",has_seated_variant=True)
        hand_region=polygon([(491,514),(520,510),(548,512),(557,542),(541,558),(507,560),(488,542)])
        left_hand=hand_region&polygon([(490,510),(521,512),(532,535),(523,559),(491,550)])
        right_hand=hand_region&~left_hand
        for layer in old["layers"]:
            name,role=layer["id"],layer["role"]
            if name in ("swing","costume_underlay","hand_l","hand_r"):
                continue
            data=rgba(ROOT/"assets/authoring"/layer["file"])
            settings={k:v for k,v in layer.items() if k not in ("id","role","file","draw_order")}
            if name=="torso":
                data=transfer(data,torso_mask&~hand_region)
            elif name=="skirt":
                data=transfer(data,~hand_region)
                settings["has_seated_variant"]=True
            elif name.startswith("arm_"):
                data=transfer(data,~hand_region)
                settings.update(side=name[-1],segment="lower",cloth=True,occlusion_side=name[-1],near_order=158,far_order=68,arm_variant="rest")
            if name.startswith("leg_"):
                settings["has_seated_variant"]=True
            if role.startswith(("eye_","pupil_","eyebrow_")) or role in ("face_base","mouth","blush") or role.startswith("hair_"):
                settings.update(zone="head",family=name,view="front")
            elif role in ("torso","clothing","neck") or role.startswith("leg_"):
                settings.update(zone="body",family=name,view="front")
            if name.startswith("pupil_"):
                settings["clip_to"]=["eye_"+name[-1]]
            self.add(name,role,data,layer["draw_order"],**settings)
        for side,mask in [("l",left_hand),("r",right_hand)]:
            self.add("hand_"+side,"hand_"+side,transfer(source,mask),160 if side=="l" else 162,
                     side=side,segment="hand",hand_shape="rest",arm_variant="rest",occlusion_side=side,
                     near_order=164,far_order=74,pivot=[.495,.518] if side=="l" else [.526,.518])
        self.audit["polygons"]["removedOriginalHandRegion"]=[[491,514],[520,510],[548,512],[557,542],[541,558],[507,560],[488,542]]

    def profile(self,view,mid=False):
        right=view=="right"
        kind="mid" if mid else "profile"
        base=rgba(self.generated/f"{kind}-{view}-base.png")
        if not right or mid:
            base=magenta_alpha(base)
        blank=magenta_alpha(rgba(self.generated/f"{kind}-{view}-face-blank.png"))
        if base.shape!=blank.shape:
            raise ValueError("Profile blank and donor must have matching canvas size")
        # The approved blank face also supplies a clean silhouette for the
        # right donor's baked checkerboard; never remove neutral gray globally.
        base[:,:,3]=np.minimum(base[:,:,3],blank[:,:,3])
        base[base[:,:,3]==0,:3]=0
        size=base.shape[0]
        if not (mid and right):
            # The approved middle-right donor already paints this shoulder
            # in pale blue cloth. Register that small cloth panel over only
            # the exposed shoulder skin in the other plates, preserving the
            # original blue seam and all surrounding costume decoration.
            cloth=magenta_alpha(rgba(self.generated/"mid-right-base.png"))
            src=(569,493,623,583)
            dst=((603,501,648,565) if right else
                 (622,490,663,562) if mid else (605,486,649,559))
            sx=(dst[2]-dst[0])/(src[2]-src[0]);sy=(dst[3]-dst[1])/(src[3]-src[1])
            registration=np.array([[sx,0,dst[0]-sx*src[0]],[0,sy,dst[1]-sy*src[1]]],float)
            panel=cv2.warpAffine(cloth,registration,(size,size),flags=cv2.INTER_LINEAR)
            color=base[:,:,:3].astype(int)
            skin=ellipse(dst,size)&(color[:,:,0]>190)&(color[:,:,2]>120)&\
                 (color[:,:,0]-color[:,:,1]>10)&(color[:,:,1]-color[:,:,2]>2)&(panel[:,:,3]>240)
            base[skin,:3]=panel[skin,:3]
            self.audit["registrations"][f"{kind}-{view}-shoulder-cloth"]={
                "source":"mid-right-base.png","matrix":registration.tolist(),
                "skinOnlyPixels":int(skin.sum()),"sourceBounds":src,"targetBounds":dst}
        # The blank donor contains a few magenta-tinted ghost strands in the
        # empty neck foreground. They are outside the anatomical neck and
        # must not become a second outline when the head turns.
        yy,xx=np.mgrid[:size,:size]
        neck_void=((xx>702)&(xx<817)&(yy>452)&(yy<558)) if right else ((xx>414)&(xx<543)&(yy>443)&(yy<558))
        for donor in (base,blank):
            rgb=donor[:,:,:3].astype(int)
            pollution=neck_void&(rgb[:,:,0]-rgb[:,:,1]>28)&(rgb[:,:,2]-rgb[:,:,1]>18)
            donor[pollution]=0
        matrix=[[.79,0,-54 if right else 74],[0,.79,20]]
        self.audit["registrations"][f"{kind}-{view}"]=matrix
        rawmask=base[:,:,3]>0
        def poly(points): return polygon(points,size)&rawmask
        if right:
            face_pts=[(595,345),(641,336),(673,295),(710,227),(783,210),(814,265),(822,328),
                      (801,365),(814,386),(800,416),(778,447),(752,455),(707,450),(669,437),(627,417),(599,394)]
            neck_pts=[(630,413),(710,441),(704,478),(733,506),(648,483),(611,493),(620,446)]
            ear_box=(590,340,651,411)
            near_box=(695,311,780,389); near_pupil=(727,327,764,380)
            far_box=(797,319,819,369); far_pupil=(800,331,811,362)
            mouth_box=(770,406,805,430)
            near_brow=(703,290,773,317);far_brow=(798,302,821,322)
            ornament_box=(522,284,589,555)
            ribbons_pts=[(456,314),(613,431),(612,681),(551,839),(448,1087),(401,1169),(335,1159),
                         (329,1052),(404,861),(399,722),(437,508)]
            hair_back_pts=[(409,173),(597,143),(658,308),(656,446),(592,486),(441,378)]
            near_side="l";far_side="r";split_x=508
            leg_split=666
        else:
            face_pts=[(441,224),(513,238),(553,302),(595,337),(655,339),(655,391),(619,420),
                      (584,433),(550,442),(475,445),(448,425),(431,400),(433,377),(449,363),(426,326),(418,273)]
            neck_pts=[(544,430),(603,416),(630,458),(649,492),(569,487),(537,481),(548,446)]
            ear_box=(596,335,655,403)
            near_box=(464,304,553,379);near_pupil=(480,322,522,372)
            far_box=(426,311,449,369);far_pupil=(433,327,445,357)
            mouth_box=(436,394,476,421)
            near_brow=(477,285,548,309);far_brow=(424,296,449,320)
            ornament_box=(659,282,730,559)
            ribbons_pts=[(709,284),(819,388),(865,540),(874,694),(865,843),(930,1071),(926,1159),
                         (839,1164),(791,1064),(757,922),(699,798),(665,660),(663,429)]
            hair_back_pts=[(651,145),(808,172),(821,373),(707,459),(631,448),(607,317)]
            near_side="r";far_side="l";split_x=797
            leg_split=582
        if mid and right:
            face_pts=[(545,340),(602,336),(653,287),(691,215),(725,198),(771,226),(797,284),
                      (791,376),(769,422),(737,446),(683,446),(636,430),(605,411),(578,402),(546,377)]
            neck_pts=[(608,414),(670,440),(676,466),(720,510),(610,485),(573,493),(583,450)]
            ear_box=(542,334,610,404)
            near_box=(629,315,718,383);near_pupil=(665,326,699,376)
            far_box=(744,321,782,380);far_pupil=(754,331,774,377)
            mouth_box=(704,392,749,426)
            near_brow=(641,290,704,315);far_brow=(749,293,782,321)
            ornament_box=(471,285,534,551)
            ribbons_pts=[(407,280),(551,420),(554,620),(516,815),(364,1130),(304,1135),
                         (298,1050),(394,846),(380,684),(397,486)]
            hair_back_pts=[(379,178),(514,157),(597,309),(599,427),(550,475),(401,367)]
            split_x=630;leg_split=646
        elif mid:
            face_pts=[(423,239),(466,207),(514,238),(548,292),(584,321),(655,342),(688,340),
                      (697,376),(672,397),(638,410),(605,425),(573,437),(520,440),(479,426),(449,408),(432,377),(421,327)]
            neck_pts=[(556,430),(615,416),(634,454),(646,493),(568,487),(531,483),(545,447)]
            ear_box=(642,331,699,406)
            near_box=(500,311,588,379);near_pupil=(522,326,560,373)
            far_box=(440,308,479,376);far_pupil=(449,321,471,372)
            mouth_box=(476,389,517,422)
            near_brow=(512,288,578,313);far_brow=(439,285,477,308)
            ornament_box=(701,274,781,555)
            ribbons_pts=[(763,280),(839,396),(870,626),(883,821),(934,1066),(931,1159),
                         (852,1170),(814,1070),(745,956),(699,808),(699,634),(712,422)]
            hair_back_pts=[(648,158),(818,166),(852,369),(708,461),(631,448),(607,317)]
            split_x=600;leg_split=551
        face=poly(face_pts)
        # Keep the real jaw underpaint behind the separate face. A hard
        # complementary cut cannot survive an independently turning head.
        neck=poly(neck_pts)
        ears=rectangle(ear_box,size)&face
        face &= ~ears
        ornaments=rectangle(ornament_box,size)&rawmask&~face&~neck&~ears
        far_ornaments=np.zeros_like(ornaments)
        ribbons=poly(ribbons_pts)
        if mid:
            far_ornaments=rectangle((714,414,777,556) if right else (402,353,466,506),size)&rawmask&~face&~neck&~ears
            far_points=([(737,400),(781,473),(860,654),(830,724),(907,990),(944,1060),(926,1130),
                         (892,1130),(870,1070),(807,951),(776,878),(760,692),(711,564)] if right else
                        [(435,342),(479,420),(466,574),(452,685),(390,832),(360,1012),(371,1074),
                         (331,1115),(292,1088),(298,1010),(346,919),(382,771),(393,635),(411,464)])
            ribbons |= poly(far_points)
            # Disconnected ribbon tails belong to their hair-attached ribbon
            # mesh. Rectangle fallthrough below must not absorb them into
            # the torso or garment underpaint when a turn changes clothing.
            tails=([(354,484),(401,482),(405,679),(381,775),(337,939),(402,1015),
                    (393,1054),(305,1039),(297,971),(329,867),(351,737)] if right else
                   [(667,479),(708,477),(716,658),(692,693),(668,641)])
            ribbons |= poly(tails)
            if right:
                ribbons |= poly([(849,771),(883,767),(939,984),(945,1039),(918,1056),
                                 (885,997),(880,928),(849,863)])
            else:
                ribbons |= poly([(314,875),(357,881),(348,1007),(309,1010),(298,971)])
                ribbons |= poly([(899,945),(937,941),(946,1059),(921,1080),(902,1012)])
        ribbons &= ~ornaments&~far_ornaments&~face&~neck
        hair_back=poly(hair_back_pts)&~face&~neck&~ears&~ornaments&~far_ornaments&~ribbons
        head_zone=rectangle((0,0,size-1,489),size)
        hair_front=rawmask&head_zone&~face&~neck&~ears&~ornaments&~far_ornaments&~ribbons&~hair_back
        used=face|neck|ears|ornaments|far_ornaments|ribbons|hair_back|hair_front
        torso=rawmask&rectangle((0,440,size-1,644),size)&~used
        used |= torso
        skirt=rawmask&rectangle((0,600,size-1,1124),size)&~used
        legs=rawmask&rectangle((420,1090,805,1225),size)&~used&~skirt
        self.audit["polygons"][kind+view+"Face"]=face_pts
        prefix=kind+"_"+("r" if right else "l")+"_"
        def add(family,role,pixels,mask,order,zone="head",**metadata):
            name=prefix+family
            self.add(name,role,warp(transfer(pixels,mask),matrix),order,
                     family=family,zone=zone,view=view+"_mid" if mid else view,opacity=0,**metadata)
            return name
        add("face_base","face_base",blank,face,180)
        add("ear_near","face_base",blank,ears,178)
        add("neck","neck",blank,neck,78,zone="body")
        add("hair_front","hair_front",blank,hair_front,200)
        add("hair_back","hair_back",blank,hair_back,172)
        add("ornament_"+near_side,"hair_side",blank,ornaments,212)
        if mid:
            add("ornament_"+far_side,"hair_side",blank,far_ornaments,210)
        left_half=rectangle((0,0,split_x,size-1),size)
        add("ribbon_l","hair_back",base,ribbons&left_half,110)
        add("ribbon_r","hair_back",base,ribbons&~left_half,112)
        add("torso","torso",base,torso,80,zone="body")
        add("skirt","clothing",base,skirt,90,zone="body",has_seated_variant=True)
        add("costume_underlay","torso",base,torso,60,zone="body")
        add("costume_underlay_skirt","clothing",base,skirt,61,zone="body",has_seated_variant=True)
        # Follow the visible overlap between two complete shoes. A vertical
        # canvas split cuts through the near shoe; opposite gait rotations
        # then tear that single painted shoe into two moving halves.
        shoe_outline=(
            [(588,1090),(659,1090),(665,1126),(678,1141),(701,1160),(713,1185),
             (703,1208),(686,1215),(643,1215),(601,1200),(595,1176),(603,1150)] if right else
            [(583,1090),(651,1090),(650,1140),(663,1170),(655,1200),(615,1215),
             (570,1215),(537,1202),(537,1184),(549,1168),(573,1148),(585,1130)])
        if mid:
            shoe_outline=(
                [(557,1090),(629,1090),(630,1130),(643,1148),(661,1174),(673,1196),
                 (664,1218),(580,1217),(551,1190),(552,1150)] if right else
                [(594,1090),(660,1090),(663,1150),(666,1190),(634,1225),(573,1225),
                 (548,1204),(553,1179),(578,1155),(594,1133)])
        near_leg=legs&polygon(shoe_outline,size)
        far_leg=legs&~near_leg
        for side,mask,order in [(near_side,near_leg,72),(far_side,far_leg,70)]:
            add("leg_"+side,"leg_"+side,base,mask,order,zone="body",has_seated_variant=True)
        self.audit["polygons"][prefix+"near_shoe"]=shoe_outline
        # At 78 degrees only the near eye is exposed. The far-side source
        # region is cheek/nose skin, not an eye, and must not be cut out as one.
        eye_sources=[(near_side,near_box,near_pupil,near_brow)]
        if mid:
            eye_sources.append((far_side,far_box,far_pupil,far_brow))
        for side,eye_box,pupil_box,brow_box in eye_sources:
            eye_mask=ellipse(eye_box,size)&(face|ears)
            pupil_mask=ellipse(pupil_box,size)&eye_mask
            eye=base.copy()
            eye[pupil_mask,:3]=(255,246,243)
            center=((eye_box[0]+eye_box[2])/2,(eye_box[1]+eye_box[3])/2)
            transformed=np.array(matrix)@np.array([*center,1.])
            pivot=(transformed/SIZE).tolist()
            eye_name=add("eye_"+side,"eye_"+side,eye,eye_mask,220,pivot=pivot)
            add("pupil_"+side,"pupil_"+side,base,pupil_mask,230,pivot=pivot,clip_to=[eye_name])
            add("eyebrow_"+side,"eyebrow_"+side,base,rectangle(brow_box,size)&face,225,pivot=pivot)
            closed=Image.new("RGBA",(size,size));draw=ImageDraw.Draw(closed)
            cx,cy=center;width=(eye_box[2]-eye_box[0])*.67
            pts=[(cx-width/2+i*width/30,cy+5*math.sin(math.pi*i/30)) for i in range(31)]
            draw.line(pts,fill=(84,43,36,255),width=3)
            add("eye_closed_"+side,"eye_closed_"+side,np.array(closed),np.asarray(closed)[:,:,3]>0,232,pivot=pivot)
        add("mouth","mouth",base,ellipse(mouth_box,size)&face,225)

    def hand_donors(self):
        source=magenta_alpha(rgba(self.generated/"hands-sheet.png"))
        # All active variants share one wrist. The original folded hands stay
        # in rest-arm mode; their surrounding white cuff must never become an
        # airborne polygon while changing between support and grip shapes.
        for side,x,wrist in [("l",375,(.495,.518)),("r",878,(.526,.518))]:
            cx=348 if side=="l" else 904
            relaxed_box=(277,62,510,354) if side=="l" else (742,62,973,354)
            relaxed_matrix=[[-.115,0,wrist[0]*SIZE+.115*cx],[0,-.115,wrist[1]*SIZE+.115*96]]
            self.add(f"hand_{side}_relaxed",f"hand_{side}",
                     warp(transfer(source,rectangle(relaxed_box,source.shape[0])),relaxed_matrix),164,
                     opacity=0,side=side,segment="hand",hand_shape="rest",arm_variant="active",
                     occlusion_side=side,near_order=164,far_order=74,pivot=list(wrist))
            self.audit["registrations"][f"hand_{side}_relaxed"]=relaxed_matrix
            offset=0 if side=="l" else 505
            roi=(225+offset,430,526+offset,734)
            scale=.115
            matrix=[[scale,0,wrist[0]*SIZE-scale*x],[0,scale,wrist[1]*SIZE-scale*699]]
            support=warp(transfer(source,rectangle(roi,source.shape[0])),matrix)
            self.add(f"hand_{side}_support",f"hand_{side}",support,164,opacity=0,
                     side=side,segment="hand",hand_shape="support",occlusion_side=side,
                     near_order=164,far_order=74,pivot=list(wrist))
            cx,cy=(356,1110) if side=="l" else (895,1110)
            box=(294,820,455,1157) if side=="l" else (793,820,958,1157)
            matrix=[[.11,0,wrist[0]*SIZE-.11*cx],[0,.11,wrist[1]*SIZE-.11*cy]]
            whole=transfer(source,rectangle(box,source.shape[0]))
            # Palm/back and curled fingers are complementary pixel ownership.
            # Rope is drawn between them, leaving the held line visible at
            # the top and bottom but covered by the fingers at the grip.
            finger_mask=rectangle((box[0],868,box[2],1060),source.shape[0])
            for suffix,mask,order in [("palm",~finger_mask,152),("fingers",finger_mask,166)]:
                self.add(f"hand_{side}_grip_{suffix}",f"hand_{side}",warp(transfer(whole,mask),matrix),order,
                         opacity=0,side=side,segment="hand",hand_shape="grip",pivot=list(wrist))
            self.audit["registrations"][f"hand_{side}_grip"]=matrix

    def sleeve_donors(self):
        source=magenta_alpha(rgba(self.generated/"sleeves-sheet.png"))
        h,w=source.shape[:2]
        yy,xx=np.mgrid[:h,:w]
        for side,start,end,shoulder,elbow,wrist in [
                ("l",(645,225),(60,260),(.434,.390),(.354,.390),(.274,.390)),
                ("r",(890,225),(1470,260),(.568,.390),(.648,.390),(.728,.390))]:
            # The texture lives on a horizontal canonical source arm. Mapping
            # it into the near-vertical resting arm here discarded almost all
            # longitudinal samples before the runtime deformation could use
            # them. Both cloth pieces now share this one registration field;
            # build_v4._active_arm owns the canonical-to-target joint mapping.
            u=(xx-start[0])/(end[0]-start[0])
            mask=(xx<750 if side=="l" else xx>780)
            ax=(wrist[0]-shoulder[0])*SIZE/(end[0]-start[0])
            slope=(end[1]-start[1])/(end[0]-start[0])
            gravity=.22
            matrix=[[ax,0,shoulder[0]*SIZE-ax*start[0]],
                    [-gravity*slope,gravity,
                     shoulder[1]*SIZE-gravity*(start[1]-slope*start[0])]]
            # Share a two-source-pixel guard on either side of the same elbow
            # cut. Independent bilinear alpha sampling must not open a seam.
            overlap=2/abs(end[0]-start[0])
            for segment,t0,t1,a in [("upper",-.08,.5+overlap,shoulder),("lower",.5-overlap,1.06,elbow)]:
                part=transfer(source,mask&(u>=t0)&(u<t1))
                # warp() accepts rectangular input, unlike polygon helpers.
                name=f"sleeve_{side}_{segment}"
                self.add(name,f"arm_{side}",warp(part,matrix),158,opacity=0,
                         side=side,segment=segment,cloth=True,arm_variant="active",
                         occlusion_side=side,near_order=158,far_order=68,pivot=list(a))
                self.audit["registrations"][name]=matrix

    def seated_donors(self):
        source=magenta_alpha(rgba(self.generated/"seated-skirt.png"))
        # Belt stays at the original waist. Raised knees produce the central
        # short hem, while side panels drape beside the wooden seat.
        # Existing torso owns the upper belt, so the donor begins directly
        # below its lower seam; duplicate stacked gold belts are excluded.
        source=transfer(source,rectangle((0,343,1253,1253),source.shape[0]))
        matrix=[[.35,0,520-.35*630],[0,.34,557-.34*343]]
        self.add("skirt_swing","clothing",warp(source,matrix),96,opacity=0,pose="swing",pivot=[.508,.54])
        self.audit["registrations"]["seated-skirt"]=matrix
        source=magenta_alpha(rgba(self.generated/"legs-sheet.png"))
        for side,box,hip,knee,ankle,target in [
                ("l",(213,150,560,1097),(462,190),(409,538),(352,930),(.469,.690)),
                ("r",(697,150,1044,1097),(793,190),(842,538),(911,930),(.548,.690))]:
            # Full trouser leg remains under the skirt; lower leg and shoe
            # are visible below its short middle hem. Authored knee, not the
            # old ankle pivot, drives seated kicking.
            scale=.23
            matrix=[[scale,0,target[0]*SIZE-scale*knee[0]],
                    [0,scale,target[1]*SIZE-scale*knee[1]]]
            self.add(f"leg_{side}_swing",f"leg_{side}",warp(transfer(source,rectangle(box,source.shape[0])),matrix),82,
                     opacity=0,pose="swing",segment="lower",side=side,pivot=list(target),
                     hip=[target[0]+scale*(hip[0]-knee[0])/SIZE,target[1]+scale*(hip[1]-knee[1])/SIZE],
                     ankle=[target[0]+scale*(ankle[0]-knee[0])/SIZE,target[1]+scale*(ankle[1]-knee[1])/SIZE])
            self.audit["registrations"][f"leg_{side}_swing"]=matrix

    def suspension(self):
        for side,top,bottom in [("l",(.355,0),(.383,.75)),("r",(.657,0),(.633,.75))]:
            image=Image.new("RGBA",(SIZE,SIZE));draw=ImageDraw.Draw(image)
            draw.line([tuple(round(v*SIZE) for v in top),tuple(round(v*SIZE) for v in bottom)],fill=(166,128,73,255),width=6)
            self.add("rope_"+side,"accessory",np.array(image),154,opacity=0)
        seat=Image.new("RGBA",(SIZE,SIZE));draw=ImageDraw.Draw(seat)
        draw.rounded_rectangle((375,759,665,783),7,fill=(171,128,77,255),outline=(133,97,61,255),width=2)
        self.add("seat","accessory",np.array(seat),65,opacity=0)

    def finish(self):
        from psd_tools import PSDImage
        from psd_tools.api.layers import PixelLayer
        manifest={"version":4,"width":SIZE,"height":SIZE,"source":"assets/master/美腻枫_标准立绘_v1.png",
                  "layers":sorted(self.layers,key=lambda l:l["draw_order"]),"provenance":self.audit}
        (self.folder/"layers.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf8")
        (self.folder/"layer-extraction-audit.json").write_text(json.dumps(self.audit,ensure_ascii=False,indent=2),encoding="utf8")
        psd=PSDImage.new("RGB",(SIZE,SIZE))
        composite=Image.new("RGBA",(SIZE,SIZE))
        for entry in manifest["layers"]:
            pixels=Image.fromarray(self.art[entry["id"]])
            layer=PixelLayer.frompil(pixels,psd,name=entry["id"])
            layer.visible=entry["opacity"]>0
            if layer.visible:
                composite.alpha_composite(pixels)
        psd.save(self.folder/"Maple.psd")
        composite.save(self.folder/"neutral-composite.png")
        return manifest


def prepare(folder):
    exporter=LayerExporter(folder)
    exporter.front()
    exporter.profile("right")
    exporter.profile("left")
    for view in ("right","left"):
        if (exporter.generated/f"mid-{view}-face-blank.png").exists():
            exporter.profile(view,mid=True)
    exporter.hand_donors()
    exporter.sleeve_donors()
    exporter.seated_donors()
    exporter.suspension()
    return exporter.finish()


if __name__=="__main__":
    import math
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder",type=Path,default=ROOT/"assets/authoring/revisions/v4")
    args=parser.parse_args()
    print(json.dumps({"layers":len(prepare(args.folder)["layers"])}))
