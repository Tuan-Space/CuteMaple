"""Time-based Cubism motion construction, independent of image frame counts.

The original AnimationSpec delays define one semantic cycle's duration. Smooth
Bezier parameter curves define the movement within that time. The application,
not these files, counts repeated petting/swing cycles and changes state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TransitionSpec:
    delays_ms: tuple[int, ...] = (1800,)
    playback: str = "one_shot"


TRANSITIONS = {f"climb_to_top_{side}": TransitionSpec() for side in ("left", "right")}
TRANSITION_EVENTS = [(0.32, "top_grab"), (0.85, "wall_release"), (1.65, "settled")]
V5_CLEAN_TRANSITIONS = {f'{state}_{suffix}':TransitionSpec((duration,))
    for state in ('clean_ground','clean_climb_left','clean_climb_right','clean_top')
    for suffix,duration in [('enter',700),('exit',650)]}


def _monotone_tangents(points, loop=False):
    """Shape-preserving C1 slopes; periodic endpoints share one derivative."""
    slopes = [(b[1]-a[1])/(b[0]-a[0]) for a,b in zip(points,points[1:])]
    tangents = [0.] * len(points)
    for i in range(1,len(points)-1):
        before,after=slopes[i-1],slopes[i]
        if before*after>0:
            tangents[i]=2*before*after/(before+after)
    if loop and len(slopes)>1 and slopes[0]*slopes[-1]>0:
        tangents[0]=tangents[-1]=2*slopes[0]*slopes[-1]/(slopes[0]+slopes[-1])
    return tangents


def _curve(parameter: str, points: list[tuple[float, float]], tangents=None, *, loop=False) -> dict:
    """Restricted cubic Beziers with continuous velocity through moving keys."""
    if any(b[0]<=a[0] for a,b in zip(points,points[1:])):
        raise ValueError(f"Non-increasing motion times for {parameter}")
    tangents = _monotone_tangents(points,loop) if tangents is None else tangents
    if len(tangents)!=len(points):
        raise ValueError(f"Wrong tangent count for {parameter}")
    segments: list[float | int] = [round(points[0][0], 6), round(points[0][1], 6)]
    for i,((ta, va), (tb, vb)) in enumerate(zip(points, points[1:])):
        dt = (tb - ta) / 3
        segments.extend([1, round(ta + dt, 6), round(va+dt*tangents[i], 6),
                         round(tb - dt, 6), round(vb-dt*tangents[i+1], 6), round(tb, 6), round(vb, 6)])
    return {"Target": "Parameter", "Id": parameter, "Segments": segments}


def build_motion(state: str, spec, defaults: dict[str, float]) -> dict:
    if state in V5_CLEAN_TRANSITIONS:
        return build_clean_transition(state,spec,defaults)
    duration = sum(spec.delays_ms) / 1000.0
    lanes: dict[str, list[tuple[float, float]]] = {
        p: [(0.0, value), (duration, value)] for p, value in defaults.items()
    }
    derivatives = {}
    v4 = "ParamHeadTurn" in defaults

    def values(param, vals, fractions=None):
        if param not in defaults:
            return
        fractions = fractions or [i / (len(vals) - 1) for i in range(len(vals))]
        lanes[param] = [(round(t * duration, 6), float(v)) for t, v in zip(fractions, vals)]
        derivatives.pop(param,None)

    def fixed(param, value):
        values(param, [value, value])

    def wave(param, amplitude, offset=0, phase=0, cycles=1, steps=8):
        steps=max(steps,8*cycles)
        vals = [offset + amplitude * math.sin(phase + i / steps * math.tau * cycles)
                for i in range(steps + 1)]
        vals[-1] = vals[0]
        values(param, vals)
        if param in defaults:
            omega=math.tau*cycles/duration
            derivatives[param]=[amplitude*omega*math.cos(phase+i/steps*math.tau*cycles)
                                for i in range(steps+1)]
            derivatives[param][-1]=derivatives[param][0]

    # A subtle chest cycle is present in every state. The runtime adds breathing
    # at its own phase; amplitudes leave headroom below the parameter maximum.
    wave("ParamBreath", .10, .12, -math.pi / 2)
    if state in TRANSITIONS:
        fixed("ParamArmPoseMode",1)
        sign = -1 if state.endswith("left") else 1
        values("ParamTransitionProgress", [0,1])
        if "ParamTransitionProgress" in defaults:
            derivatives["ParamTransitionProgress"]=[1/duration,1/duration]
        values("ParamHeadTurn", [sign,sign,.65*sign,0,0], [0,.18,.45,.83,1])
        values("ParamBodyTurn", [sign,sign,.65*sign,0,0], [0,.18,.45,.83,1])
        values("ParamPoseClimb", [sign,0])
        derivatives["ParamPoseClimb"]=[-sign/duration,-sign/duration]
        fixed("ParamPoseTop",0)
        values("ParamPoseSwing", [0,0,.6,1,1], [0,.18,.67,.92,1])
        values("ParamSwingVisible", [0,1,1], [0,.32/1.8,1])
        for side in ("L","R"):
            near=(side=="L" and sign>0) or (side=="R" and sign<0)
            times=([0,.85/1.8,1.45/1.8,1] if near else [0,.32/1.8,1])
            shape=([-1,-1,1,1] if near else [-1,1,1])
            values(f"ParamHand{side}Shape",shape,times)
            values(f"ParamArm{side}A", [-4 if side=="L" else 4,0,0], [0,.47,1])
            values(f"ParamArm{side}B", [3,1,0], [0,.47,1])
            values(f"ParamLeg{side}B", [0,4,6], [0,.47,1])
        fixed("ParamMouthForm", .7)
    elif state == "idle":
        wave("ParamBodyAngleZ", .45)
        wave("ParamAngleZ", .7, phase=math.pi / 2)
    elif state.startswith("walk_"):
        sign = 1 if state.endswith("right") else -1
        fixed("ParamAngleX", sign * 8)
        fixed("ParamBodyAngleX", sign * 2)
        if v4:
            fixed("ParamBodyTurn", sign*.18)
            fixed("ParamHeadTurn", sign*.12)
        wave("ParamLegLA", 7)
        wave("ParamLegRA", -7)
        wave("ParamLegLB", 3, 3)
        wave("ParamLegRB", -3, 3)
        wave("ParamArmLA", -2.8)
        wave("ParamArmRA", 2.8)
        wave("ParamBounce", .09, .09, -math.pi / 2, cycles=2)
        wave("ParamBodyAngleZ", 1.2)
    elif state.startswith("drag_"):
        sign = -1 if state.endswith("left") else 1
        fixed("ParamBodyAngleZ", sign * 5)
        wave("ParamAngleZ", 2, sign * -6)
        wave("ParamArmLA", 2, -6)
        wave("ParamArmRA", -2, 6)
        wave("ParamLegLA", 3, -2)
        wave("ParamLegRA", -3, 2)
        fixed("ParamMouthOpenY", .2)
    elif state == "happy":
        fixed("ParamMouthForm", 1)
        fixed("ParamCheek", .65)
        fixed("ParamEyeLSmile", .65)
        fixed("ParamEyeRSmile", .65)
        # A single smile and head tilt followed by a quiet settle.
        values("ParamAngleZ", [0, -2.8, -2.8, 0], [0, .28, .62, 1])
        values("ParamAngleY", [0, 1.5, 0], [0, .38, 1])
        values("ParamMouthOpenY", [0, .22, .12, 0], [0, .25, .64, 1])
    elif state == "talk":
        fixed("ParamMouthForm", .6)
        values("ParamMouthOpenY", [.05, .6, .15, .75, .05])
        wave("ParamAngleY", 1.5)
        wave("ParamArmRB", 1.8, 2)
    elif state == "petting":
        fixed("ParamCheek", .8)
        fixed("ParamMouthForm", 1)
        fixed("ParamEyeLSmile", 1)
        fixed("ParamEyeRSmile", 1)
        values("ParamEyeLOpen", [.55, .12, .55])
        values("ParamEyeROpen", [.55, .12, .55])
        wave("ParamAngleZ", 4, -2)
        wave("ParamAngleY", 2, -3)
    elif state == "fall_float":
        wave("ParamBodyAngleZ", 2.5)
        fixed("ParamArmLA", -8)
        fixed("ParamArmRA", 8)
        fixed("ParamLegLB", 5)
        fixed("ParamLegRB", 4)
        fixed("ParamMouthOpenY", .45)
        fixed("ParamAngleY", 5)
    elif state == "land":
        values("ParamCrouch", [.05, .75, .3, 0], [0, .22, .48, 1])
        values("ParamBounce", [0, 0, .15, 0], [0, .22, .58, 1])
        values("ParamArmLA", [-5, -7, -2, 0])
        values("ParamArmRA", [5, 7, 2, 0])
        values("ParamAngleY", [3, -5, 1, 0])
    elif "climb" in state:
        fixed("ParamArmPoseMode",1)
        sign = -1 if state.endswith("left") else 1
        fixed("ParamPoseClimb", sign)
        fixed("ParamAngleX", sign * (2 if v4 else 11))
        fixed("ParamHeadTurn", sign)
        fixed("ParamBodyTurn", sign)
        fixed("ParamHandLShape", -1)
        fixed("ParamHandRShape", -1)
        fixed("ParamBodyAngleZ", sign * 2)
        wave("ParamArmLA", 3.5, -4)
        wave("ParamArmRA", -3.5, 4)
        wave("ParamArmLB", 2.5, 3)
        wave("ParamArmRB", -2.5, 3)
        wave("ParamLegLA", 4)
        wave("ParamLegRA", -4)
        wave("ParamCrouch", .12, .2)
        if state.startswith("clean_"):
            fixed("ParamCleaning", 1)
            wave("ParamArmRB" if sign > 0 else "ParamArmLB", 3.0, 4)
    elif state.startswith("swing_"):
        fixed("ParamArmPoseMode",1)
        fixed("ParamPoseSwing", 1)
        fixed("ParamSwingVisible", 1)
        fixed("ParamHandLShape", 1)
        fixed("ParamHandRShape", 1)
        # PoseSwing spreads the wrists to the authored rope positions. Extra
        # independent shoulder offsets here would pull the hands off the ropes.
        fixed("ParamLegLB", 6)
        fixed("ParamLegRB", 6)
        amount = 1 if state == "swing_cycle" else .20
        wave("ParamSwing", amount)
        wave("ParamAngleZ", 3 * amount, phase=math.pi)
        wave("ParamLegLA", 4 * amount)
        wave("ParamLegRA", 4 * amount)
        fixed("ParamMouthForm", .7)
    elif state.startswith("sleep_"):
        if state == "sleep_enter":
            values("ParamPoseSleep", [0, .25, 1], [0, .35, 1])
            values("ParamEyeLOpen", [1, .65, 0], [0, .4, 1])
            values("ParamEyeROpen", [1, .65, 0], [0, .4, 1])
            values("ParamAngleZ", [0, -3, -10])
            values("ParamAngleY", [0, -2, -5])
        elif state == "sleep_exit":
            values("ParamPoseSleep", [1, .8, 0], [0, .25, 1])
            values("ParamEyeLOpen", [0, 0, .65, 1], [0, .25, .6, 1])
            values("ParamEyeROpen", [0, 0, .65, 1], [0, .25, .6, 1])
            values("ParamAngleZ", [-10, -6, 0])
            values("ParamAngleY", [-5, -3, 0])
        else:
            fixed("ParamPoseSleep", 1)
            fixed("ParamEyeLOpen", 0)
            fixed("ParamEyeROpen", 0)
            fixed("ParamAngleZ", -10)
            wave("ParamAngleY", .6, -5)
            wave("ParamBreath", .15, .2, -math.pi / 2)
    elif state == "clean_ground":
        fixed("ParamCleaning", 1)
        fixed("ParamCrouch", .65)
        fixed("ParamAngleY", -7)
        wave("ParamArmLA", 3, 2)
        wave("ParamArmRA", -4, 2)
        wave("ParamArmRB", 4, 3)
        wave("ParamBodyAngleX", 2)
    elif state == "clean_top":
        fixed("ParamArmPoseMode",1)
        fixed("ParamCleaning", 1)
        fixed("ParamPoseTop", .35 if v4 else 1)
        fixed("ParamArmLA", 0 if v4 else -7)
        fixed("ParamArmRA", 5 if v4 else 7)
        if v4:
            fixed("ParamPoseSwing",1)
            fixed("ParamSwingVisible",1)
            fixed("ParamHandLShape",1)
            fixed("ParamHandRShape",-1)
            fixed("ParamLegLB",6)
            fixed("ParamLegRB",6)
        fixed("ParamAngleY", 7)
        if not v4:
            wave("ParamArmLB", 2, -3)
        wave("ParamArmRB", 3, 4)
        wave("ParamBodyAngleZ", 2)
    else:
        raise ValueError(f"No choreography for state {state!r}")

    v5 = "ParamRibbonLX" in defaults
    if v5:
        fixed('ParamArmSupportBlend', int(state in TRANSITIONS or
              state.startswith(('climb_', 'swing_', 'clean_'))))
        if state in ("drag_left","drag_right","fall_float","land"):
            # Free arms keep the same painted sleeves through pickup and
            # landing. Their own pose field unfolds the cloth and wrists.
            fixed("ParamHandLShape",0)
            fixed("ParamHandRShape",0)
            fixed("ParamArmPoseMode",0 if "ParamFreeArmActive" in defaults else 1)
        if "ParamFreeArmActive" in defaults and state in ("drag_left", "drag_right", "fall_float", "land"):
            fixed("ParamFreeArmActive", 1)
            fixed("ParamFreeArmPose", 1)
            for side in ("L", "R"):
                fixed(f"ParamArm{side}A", 0)
                fixed(f"ParamArm{side}B", 0)
            if state == "fall_float":
                fixed("ParamBodyAngleZ", 0)
                fixed("ParamAngleY", 3)
                fixed("ParamMouthOpenY", .24)
            elif state == "land":
                values("ParamCrouch", [0, .6, .6, 0, 0], [0, .16, .28, .74, 1])
                fixed("ParamBounce", 0)
                values("ParamAngleY", [3, -3, -3, 0, 0], [0, .18, .28, .78, 1])
                values("ParamLegLB", [5, 7, 0, 0], [0, .18, .74, 1])
                values("ParamLegRB", [4, 6, 0, 0], [0, .18, .74, 1])
                values("ParamMouthOpenY", [.24, .12, 0], [0, .28, 1])
                values("ParamFreeArmPose", [1, .95, .4, 0, 0], [0, .22, .52, .78, 1])
            if "ParamFreeArmBrace" in defaults:
                # v5.1 separates the hanging sleeve from the elbow's landing
                # preparation. The fall loop is stationary in pose; its entry
                # fade handles the approach without an authored loop seam.
                fixed("ParamFreeArmBrace", 1 if state in ("fall_float", "land") else 0)
                if state == "land":
                    values("ParamFreeArmBrace", [1, 1, .35, 0, 0], [0, .18, .35, .50, 1])
                    # Release the brace first, then lower both relaxed hands
                    # into their original rest cuffs as one connected pose.
                    values("ParamFreeArmPose", [1, 1, .6, 0, 0], [0, .50, .67, .85, 1])
        if state in ("climb_left","climb_right"):
            fixed("ParamClimbRefine",1)
            fixed("ParamClimbDrapeBlend",1)
            fixed("ParamClimbActive",1)
            fixed("ParamClimbDirection",-1 if state.endswith("left") else 1)
            values("ParamClimbPhase",[0,1])
            derivatives["ParamClimbPhase"]=[1/duration,1/duration]
            # Actual planted contact is driven by the phase field, not by
            # independent arm waves which slide the palms off their support.
            for side in ("L","R"):
                fixed(f"ParamArm{side}A",0)
                fixed(f"ParamArm{side}B",0)
                fixed(f"ParamLeg{side}A",0)
                fixed(f"ParamLeg{side}B",0)
            fixed("ParamCrouch",0)
            fixed("ParamBodyAngleZ",0)
            fixed("ParamAngleX",0)
        elif state in TRANSITIONS:
            fixed("ParamClimbHandoff",1)
            # Cloth reaches the receiving silhouette before its independent
            # material exchange; body/support refinement keeps its own timing.
            values("ParamClimbDrapeBlend",[1,1,0,0],[0,.40/1.8,.52/1.8,1])
            values("ParamClimbRefine",[1,1,0,0],[0,.10/1.8,.85/1.8,1])
            fixed("ParamClimbDirection",-1 if state.endswith("left") else 1)
            values("ParamClimbActive",[1,1,0,0],[0,.32/1.8,.85/1.8,1])
            fixed("ParamClimbPhase",0)
            for side in ("L","R"):
                fixed(f"ParamArm{side}A",0)
                fixed(f"ParamArm{side}B",0)
        if state.startswith("sleep_"):
            fixed("ParamAngleY",0)
            fixed("ParamAngleZ",0)
        if state.startswith("clean_"):
            side="L" if state=="clean_climb_left" else "R"
            fixed("ParamArmPoseMode",1)
            fixed("ParamCleanFan"+side,1)
            fixed("ParamHand"+side+"Shape",1)
            fixed("ParamPoseTop",0)
            fixed("ParamBodyAngleX",0)
            fixed("ParamBodyAngleZ",0)
            fixed("ParamCrouch",0)
            for limb in ("L","R"):
                fixed(f"ParamArm{limb}A",0)
                fixed(f"ParamArm{limb}B",0)
                fixed(f"ParamLeg{limb}A",0)
            values("ParamCleaningSweep",[-.65,-.65,1,.35,-.65],[0,.18,.46,.68,1])
            if state=="clean_ground":
                fixed("ParamCleanGround",1)
            elif state.startswith("clean_climb"):
                fixed("ParamClimbRefine",1)
                fixed("ParamClimbActive",1)
                fixed("ParamClimbDirection",-1 if state.endswith("left") else 1)
                fixed("ParamClimbPhase",0)
            # Keep the other palm on the wall or rope, through the fan sweep.
            elif state=="clean_top":
                fixed("ParamHandLShape",1)
            if "ParamCleanBrushR" in defaults:
                # Four distinct articulated poses share only the semantic
                # sweep phase. The context has no effect with CleanPose=0.
                fixed("ParamCleanContext", {"clean_ground": 0, "clean_top": 2,
                      "clean_climb_left": -1, "clean_climb_right": 1}[state])
                fixed("ParamCleaningSweep", 0)
                fixed("ParamCleanGround", 0)
                if "ParamClimbDrapeBlend" in defaults:
                    fixed("ParamClimbDrapeBlend", int(state in ("clean_climb_left", "clean_climb_right")))
                for limb in ("L", "R"):
                    fixed("ParamCleanFan" + limb, 0)
                    fixed("ParamCleanBrush" + limb, int(limb == side))
                    fixed("ParamCleanPose" + limb, int(limb == side or state == "clean_ground"))
                if state == "clean_ground":
                    # Keep the original long sleeves from idle through pickup
                    # and stow. The brush has its own original-cuff attachment;
                    # exchanging supported donor sleeves caused raised wings.
                    fixed("ParamArmPoseMode", 0)
                    fixed("ParamArmSupportBlend", 0)
                    fixed("ParamHandLShape", 0)
                    fixed("ParamHandRShape", 1)
                # Prepare with the brush beside the hip, sweep outwards in a
                # short elbow/wrist arc, then return to the same ready pose.
                values("ParamCleanStroke", [0, -1, -1, 1, 0], [0, .16, .25, .62, 1])

    looping = spec.playback != "one_shot"
    if looping:
        for param, pts in lanes.items():
            cyclic_phase=v5 and state in ("climb_left","climb_right") and param=="ParamClimbPhase"
            if not cyclic_phase and abs(pts[0][1] - pts[-1][1]) > 1e-8:
                raise ValueError(f"Loop seam in {state}/{param}")
    curves = [_curve(param, pts, derivatives.get(param),loop=looping) for param, pts in lanes.items()]
    if "ParamCleanBrushR" in defaults and state.startswith("clean_"):
        for curve in curves:
            if curve['Id'] == 'ParamCleanContext':
                curve.update(FadeInTime=0., FadeOutTime=0.)
    nseg = sum(len(pts) - 1 for pts in lanes.values())
    events = [{"Time":time,"Value":value} for time,value in TRANSITION_EVENTS] if state in TRANSITIONS else []
    if v5 and state.startswith("clean_"):
        sweep_phase = .62 if "ParamCleanBrushR" in defaults else .46
        events.append({"Time":round(duration*sweep_phase,6),"Value":"clean_sweep"})
    return {
        "Version": 3,
        "Meta": {"Duration": duration, "Fps": 60.0, "Loop": looping,
                 "AreBeziersRestricted": True, "CurveCount": len(curves),
                 "TotalSegmentCount": nseg, "TotalPointCount": len(curves) + 3 * nseg,
                 "UserDataCount": len(events), "UserDataSize": sum(len(e['Value'].encode('utf8')) for e in events)},
        "FadeInTime": .18 if state.startswith(("drag", "fall", "land")) else .28,
        "FadeOutTime": .22,
        "Curves": curves,
        "UserData": events,
    }


def motion_references(animations) -> dict:
    return {state: [{"File": f"motions/{state}.motion3.json"}] for state in animations}


def build_clean_transition(state,spec,defaults):
    """Take/stow the tool with the corresponding support posture retained."""
    public,suffix=state.rsplit('_',1)
    base={'clean_ground':'idle','clean_top':'swing_idle',
          'clean_climb_left':'climb_left','clean_climb_right':'climb_right'}[public]
    # Compare actual authored cycle endpoints, not a second hand-written pose
    # dictionary which could silently disagree with the loop being entered.
    sample_spec=TransitionSpec((1000,),'loop')
    start={c['Id']:c['Segments'][1] for c in build_motion(base,sample_spec,defaults)['Curves']}
    finish={c['Id']:c['Segments'][1] for c in build_motion(public,sample_spec,defaults)['Curves']}
    if suffix=='exit':start,finish=finish,start
    duration=sum(spec.delays_ms)/1000
    if "ParamCleanBrushR" in defaults:
        return _build_brush_transition(public, suffix, duration, defaults, start, finish)
    fractions=[0,.15,.45,.80,1]
    curves=[]
    for param in defaults:
        a,b=start[param],finish[param]
        progression=[0,.06,.42,.92,1]
        if param.startswith('ParamCleanFan'):
            progression=[0,0,.55,1,1] if suffix=='enter' else [0,.05,.55,1,1]
        points=[(round(duration*t,6),a+(b-a)*v) for t,v in zip(fractions,progression)]
        curves.append(_curve(param,points))
    segments=len(curves)*(len(fractions)-1)
    return {'Version':3,'Meta':{'Duration':duration,'Fps':60.,'Loop':False,
        'AreBeziersRestricted':True,'CurveCount':len(curves),'TotalSegmentCount':segments,
        'TotalPointCount':len(curves)+3*segments,'UserDataCount':0,'UserDataSize':0},
        'FadeInTime':.12,'FadeOutTime':.12,'Curves':curves,'UserData':[]}


def _build_brush_transition(public, suffix, duration, defaults, start, finish):
    """Stage the grip separately from the arm so no tool grows out of a cuff."""
    context = {'clean_ground': 0, 'clean_top': 2,
               'clean_climb_left': -1, 'clean_climb_right': 1}[public]
    curves = []
    for param in defaults:
        a, b = start[param], finish[param]
        times = [0, .15, .45, .80, 1]
        progression = [0, .06, .42, .92, 1]
        if param.startswith('ParamCleanPose'):
            if suffix == 'enter':
                progression = [0, 0, .45, 1, 1]
            else:
                times, progression = [0, .35, .55, .85, 1], [0, 0, .35, 1, 1]
        elif param.startswith('ParamCleanBrush'):
            if suffix == 'enter':
                times, progression = [0, .80, .84, .93, 1], [0, 0, .1, 1, 1]
            else:
                times, progression = [0, .15, .35, .80, 1], [0, .45, 1, 1, 1]
        elif public == 'clean_ground' and param == 'ParamHandRShape':
            # The interlocked standing hand is an occluded cutout. Exchange
            # it for the complete relaxed hand before the wrists separate,
            # and restore the cutout only after they return to the rest cuff.
            if suffix == 'enter':
                times, progression = [0, .06, .15, .80, 1], [0, 0, 1, 1, 1]
            else:
                times, progression = [0, .80, .86, .96, 1], [0, 0, 0, 1, 1]
        elif param in ('ParamCleaningSweep', 'ParamCleanStroke', 'ParamCleanGround') or param.startswith('ParamCleanFan'):
            a = b = 0
        elif param == 'ParamCleanContext':
            a = b = context
        points = [(round(duration * t, 6), a + (b - a) * value)
                  for t, value in zip(times, progression)]
        curve = _curve(param, points)
        if param == 'ParamCleanContext':
            curve.update(FadeInTime=0., FadeOutTime=0.)
        curves.append(curve)
    segments = len(curves) * 4
    return {'Version': 3, 'Meta': {'Duration': duration, 'Fps': 60., 'Loop': False,
            'AreBeziersRestricted': True, 'CurveCount': len(curves), 'TotalSegmentCount': segments,
            'TotalPointCount': len(curves) + 3 * segments, 'UserDataCount': 0, 'UserDataSize': 0},
            'FadeInTime': .12, 'FadeOutTime': .12, 'Curves': curves, 'UserData': []}
