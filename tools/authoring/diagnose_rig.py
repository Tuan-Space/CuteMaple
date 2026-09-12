"""Offline IR diagnostic, NOT Cubism runtime/export validation.

Evaluates authored mesh offsets and each parent warp, using bilinear lattice
interpolation and linear extrapolation outside the lattice. Cubism's own warp
interpolator may differ. Rasterizes original PNG textures through the actual
mesh triangles with premultiplied-alpha sampling. Never alters source artwork.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from build_rig import ROOT, Rig


def interpolate_keys(parameter, value):
    keys=sorted(parameter.keyforms,key=lambda k:k.value)
    if value<=keys[0].value:
        return [(keys[0],1.)]
    if value>=keys[-1].value:
        return [(keys[-1],1.)]
    for first,last in zip(keys,keys[1:]):
        if first.value<=value<=last.value:
            weight=(value-first.value)/(last.value-first.value)
            return [(first,1-weight),(last,weight)]
    raise ValueError(parameter.id)


def warp_points(points,grid,rows,cols):
    """Parent-local points -> parent coordinates, full-canvas rest grid."""
    points=np.asarray(points,dtype=np.float64)
    grid=np.asarray(grid,dtype=np.float64).reshape(rows,cols,2)
    gx,gy=points[:,0]*(cols-1),points[:,1]*(rows-1)
    ix=np.clip(np.floor(gx).astype(int),0,cols-2)
    iy=np.clip(np.floor(gy).astype(int),0,rows-2)
    tx,ty=(gx-ix)[:,None],(gy-iy)[:,None]
    return ((grid[iy,ix]*(1-tx)+grid[iy,ix+1]*tx)*(1-ty)
            +(grid[iy+1,ix]*(1-tx)+grid[iy+1,ix+1]*tx)*ty)


class DiagnosticRenderer:
    def __init__(self,rig,asset_root,res=512):
        self.rig,self.asset_root,self.res=rig,Path(asset_root),res
        self.meshes={m.part_id:m for m in rig.meshes}
        self.nodes={d.id:d for d in rig.deformers}
        self.textures={}
        for texture in rig.textures:
            pixels=np.asarray(Image.open(self.asset_root/texture.path).convert("RGBA"),dtype=np.float32)/255
            pixels[:,:,:3]*=pixels[:,:,3:4]
            self.textures[texture.id]=pixels

    def evaluate(self,settings):
        mesh_vertices={name:np.array(m.vertices,dtype=float) for name,m in self.meshes.items()}
        grids={name:np.array(d.grid_vertices,dtype=float) for name,d in self.nodes.items()
               if getattr(d,'type','warp') != 'rotation'}
        node_opacities={name:1. for name in self.nodes}
        node_offset_weights={name:1. for name in self.nodes}
        opacities={p.id:p.opacity for p in self.rig.parts}
        self.draw_orders={p.id:p.draw_order for p in self.rig.parts}
        for parameter in self.rig.parameters:
            selection=interpolate_keys(parameter,settings.get(parameter.id,parameter.default))
            opacity={}
            orders={}
            node_alpha={}
            node_weights={}
            for key,weight in selection:
                for name,delta in key.mesh_offsets.items():
                    mesh_vertices[name]+=np.array(delta)*weight
                for name,delta in key.deformer_offsets.items():
                    grids[name]+=np.array(delta)*weight
                for name,value in key.opacity_overrides.items():
                    opacity[name]=opacity.get(name,0)+weight*value
                for name,value in key.deformer_opacity_overrides.items():
                    node_alpha[name]=node_alpha.get(name,0)+weight*value
                for name,value in key.deformer_offset_weights.items():
                    node_weights[name]=node_weights.get(name,0)+weight*value
                for name,value in key.draw_order_overrides.items():
                    orders[name]=orders.get(name,0)+weight*value
            for name,value in opacity.items():
                opacities[name]*=value
            self.draw_orders.update(orders)
            for name,value in node_alpha.items():
                node_opacities[name]*=value
            for name,value in node_weights.items():
                node_offset_weights[name]*=value
        for name,weight in node_offset_weights.items():
            if name not in grids:
                continue
            base=np.array(self.nodes[name].grid_vertices,dtype=float)
            grids[name]=base+(grids[name]-base)*weight
        def transform(points,parent,seen=None):
            if parent is None:
                return points
            seen=set() if seen is None else seen
            if parent in seen:
                raise ValueError("Cyclic deformer hierarchy")
            seen=seen|{parent}
            node=self.nodes[parent]
            if getattr(node,'type','warp') == 'rotation':
                # Static CMO rotation forms use a normalized source-space
                # pivot, angle 0 and scale 1. Keep the old warp evaluator for
                # all existing parts. For this rigid child, carry its origin
                # through that same path and estimate the inherited angle
                # from a small upward tangent. This authoring preview is an
                # approximation, not Core's native angle/warp interpolation.
                pivot=np.asarray(node.pivot,dtype=float)
                probes=np.array([pivot,pivot+(0.,-1e-5)])
                origin,tip=transform(probes,node.parent,seen)
                direction=tip-origin
                length=np.linalg.norm(direction)
                if length < 1e-12:
                    raise ValueError(f"Collapsed rotation tangent: {parent}")
                sine,negative_cosine=direction/length
                matrix=np.array([[-negative_cosine,-sine],
                                 [sine,-negative_cosine]])
                return origin+(points-pivot)@matrix.T
            return transform(warp_points(points,grids[parent],node.grid_rows,node.grid_cols),
                             node.parent,seen)
        output={}
        for part in self.rig.parts:
            points=mesh_vertices[part.id]
            parent=part.parent_deformer
            inherited_opacity=1.
            seen=set()
            while parent:
                if parent in seen:
                    raise ValueError("Cyclic deformer hierarchy")
                seen.add(parent)
                node=self.nodes[parent]
                inherited_opacity*=node_opacities[parent]
                parent=node.parent
            points=transform(points,part.parent_deformer)
            output[part.id]=(points,opacities[part.id]*inherited_opacity)
        return output

    def render(self,settings):
        evaluated=self.evaluate(settings)
        canvas=np.zeros((self.res,self.res,4),dtype=np.float32)
        stats={"invertedTriangles":{},"visibleLayers":[]}
        rasters={}
        ordered=sorted(self.rig.parts,key=lambda p:self.draw_orders[p.id])
        for part in ordered:
            vertices,opacity=evaluated[part.id]
            if part.id in settings.get("__hiddenLayers",[]):
                continue
            if opacity<1e-5:
                continue
            stats["visibleLayers"].append(part.id)
            mesh=self.meshes[part.id]
            pixels=self.textures[part.texture_id]
            uv=np.array(mesh.uvs)*[pixels.shape[1],pixels.shape[0]]-.5
            positions=vertices*self.res
            warped=np.zeros_like(canvas)
            clip_mask=None
            if part.clip_to:
                missing=[name for name in part.clip_to if name not in rasters]
                if missing:
                    raise ValueError(f"Diagnostic clipping source must be drawn before target: {part.id}: {missing}")
                clip_mask=np.maximum.reduce([rasters[name][:,:,3:4] for name in part.clip_to])
                if part.invert_clipping:
                    clip_mask=1-clip_mask
            map_x=np.full((self.res,self.res),-1.,dtype=np.float32)
            map_y=map_x.copy()
            covered=np.zeros((self.res,self.res),dtype=bool)
            pending_bounds=None

            def composite_batch():
                nonlocal pending_bounds
                if pending_bounds is None:
                    return
                left,top,right,bottom=pending_bounds
                sx=map_x[top:bottom,left:right]
                sy=map_y[top:bottom,left:right]
                sample=cv2.remap(pixels,sx,sy,cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT)*opacity
                if clip_mask is not None:
                    sample*=clip_mask[top:bottom,left:right]
                region=warped[top:bottom,left:right]
                region[:]=sample+region*(1-sample[:,:,3:4])
                sx.fill(-1.)
                sy.fill(-1.)
                covered[top:bottom,left:right]=False
                pending_bounds=None

            inverted=0
            for triangle in mesh.triangles:
                dest=positions[list(triangle)]
                source=uv[list(triangle)]
                left,top=np.maximum(0,np.floor(dest.min(axis=0)).astype(int))
                right,bottom=np.minimum(self.res,np.ceil(dest.max(axis=0)).astype(int))
                if right<=left or bottom<=top:
                    continue
                edge_a,edge_b=dest[1]-dest[0],dest[2]-dest[0]
                det=edge_a[0]*edge_b[1]-edge_a[1]*edge_b[0]
                inverted+=int(det<0)
                if abs(det)<1e-8:
                    continue
                # Render both windings, but use a consistent edge convention.
                # Shared edges belong to one triangle so translucent seams do
                # not get composited twice.
                if det<0:
                    dest=dest[[0,2,1]]
                    source=source[[0,2,1]]
                    edge_a,edge_b=dest[1]-dest[0],dest[2]-dest[0]
                    det=-det
                yy,xx=np.mgrid[top:bottom,left:right]
                dx,dy=xx+.5-dest[0,0],yy+.5-dest[0,1]
                b=(dx*edge_b[1]-dy*edge_b[0])/det
                c=(edge_a[0]*dy-edge_a[1]*dx)/det
                a=1-b-c
                mask=np.ones(a.shape,dtype=bool)
                for weight,start,end in ((a,dest[1],dest[2]),(b,dest[2],dest[0]),(c,dest[0],dest[1])):
                    edge=end-start
                    top_left=edge[1]<0 or (edge[1]==0 and edge[0]>0)
                    mask&=(weight>1e-6)|((np.abs(weight)<=1e-6)&top_left)
                if not mask.any():
                    continue
                sx=a*source[0,0]+b*source[1,0]+c*source[2,0]
                sy=a*source[0,1]+b*source[1,1]+c*source[2,1]
                # Batch disjoint triangles for one texture sample. Flush before
                # an overlap so each covering fragment is blended in order;
                # later transparent UVs cannot erase earlier painted texels.
                if np.any(covered[top:bottom,left:right]&mask):
                    composite_batch()
                map_x[top:bottom,left:right][mask]=sx[mask]
                map_y[top:bottom,left:right][mask]=sy[mask]
                covered[top:bottom,left:right]|=mask
                if pending_bounds is None:
                    pending_bounds=(left,top,right,bottom)
                else:
                    a,b,c,d=pending_bounds
                    pending_bounds=(min(a,left),min(b,top),max(c,right),max(d,bottom))
            composite_batch()
            if inverted:
                stats["invertedTriangles"][part.id]=inverted
            rasters[part.id]=warped
            canvas=warped+canvas*(1-warped[:,:,3:4])
        rgb=np.divide(canvas[:,:,:3],canvas[:,:,3:4],out=np.zeros_like(canvas[:,:,:3]),where=canvas[:,:,3:4]>1e-7)
        rgba=np.concatenate([rgb,canvas[:,:,3:4]],axis=2)
        return Image.fromarray(np.uint8(np.clip(rgba*255,0,255))),stats


def motion_parameters(path,phase):
    document=json.loads(Path(path).read_text(encoding="utf-8"))
    time=phase*document["Meta"]["Duration"]
    result={}
    for curve in document["Curves"]:
        data=curve["Segments"]
        ta,va=data[:2]
        value=va
        cursor=2
        while cursor<len(data):
            kind=data[cursor]
            if kind!=1:
                raise ValueError("Diagnostic expects builder's restricted Bezier curves")
            t1,v1,t2,v2,tb,vb=data[cursor+1:cursor+7]
            if time<=tb:
                u=max(0,min(1,(time-ta)/(tb-ta)))
                value=(1-u)**3*va+3*(1-u)**2*u*v1+3*(1-u)*u*u*v2+u**3*vb
                break
            ta,va=tb,vb
            value=vb
            cursor+=7
        result[curve["Id"]]=value
    return result


def diagnose(rig_path,asset_root,output,runtime,res=512):
    rig=Rig.model_validate_json(Path(rig_path).read_text(encoding="utf-8"))
    renderer=DiagnosticRenderer(rig,asset_root,res)
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    poses=[("Neutral","idle",0,{}),
           ("Gaze left","idle",0,{"ParamAngleX":-10,"ParamEyeBallX":-1}),
           ("Gaze right","idle",0,{"ParamAngleX":10,"ParamEyeBallX":1}),
           ("Closed eyes","idle",0,{"ParamEyeLOpen":0,"ParamEyeROpen":0}),
           ("Climb left","climb_left",.35,{}),
           ("Climb right","climb_right",.35,{}),
           ("Clean top","clean_top",.35,{}),
           ("Seated sleep","sleep_loop",.5,{}),
           ("Typing","idle",0,{"ParamTyping":1,"ParamTypingPulse":1}),
           ("Listening","idle",0,{"ParamListening":1}),
           ("Drag right","drag_right",.35,{}),
           ("Swing","swing_cycle",.25,{})]
    if any(p.id=="ParamHeadTurn" for p in rig.parameters):
        for turn in [-.65,-.35,.35,.65]:
            poses.append((f"Turn {turn:+.2f}","idle",0,{"ParamHeadTurn":turn,"ParamBodyTurn":turn}))
        poses.extend([
            ("Torso no arms","idle",0,{"__hiddenLayers":[p.id for p in rig.parts if p.semantic_role.value.startswith(("arm_","hand_"))]}),
            ("Transfer grab","climb_to_top_right",.32/1.8,{}),
            ("Transfer release","climb_to_top_right",.85/1.8,{}),
            ("Transfer settled","climb_to_top_right",1,{})])
        for turn in [-.51,-.50,-.49,.49,.50,.51]:
            poses.append((f"Exchange {turn:+.2f}","idle",0,{"ParamHeadTurn":turn,"ParamBodyTurn":turn}))
        if any(part.id=="mid_l_face_base" for part in rig.parts):
            for turn in [-.751,-.749,-.251,-.249,.249,.251,.749,.751]:
                poses.append((f"Join {turn:+.3f}","idle",0,{"ParamHeadTurn":turn,"ParamBodyTurn":turn}))
    tile=320
    sheet=Image.new("RGB",(tile*4,(tile+30)*math.ceil(len(poses)/4)+65),(235,239,244))
    draw=ImageDraw.Draw(sheet)
    draw.text((12,9),"OFFLINE IR DIAGNOSTIC - approximate bilinear warps; NOT Cubism export validation",fill=(30,40,55))
    draw.text((12,29),"Source PNGs unchanged. Mesh triangles, parameter offsets and parent hierarchy evaluated.",fill=(50,60,75))
    report={"status":"offline-approximation-not-runtime-validation","poses":{}}
    for index,(label,state,phase,overrides) in enumerate(poses):
        settings=motion_parameters(Path(runtime)/"motions"/f"{state}.motion3.json",phase)
        settings.update(overrides)
        render,stats=renderer.render(settings)
        filename=f"{index:02d}-{label.lower().replace(' ','-')}.png"
        render.save(output/filename)
        render.getchannel("A").save(output/(filename.removesuffix(".png")+"-alpha.png"))
        for suffix,color in [("black",(0,0,0,255)),("white",(255,255,255,255))]:
            background=Image.new("RGBA",render.size,color)
            background.alpha_composite(render)
            background.convert("RGB").save(output/(filename.removesuffix(".png")+f"-{suffix}.png"))
        x,y=(index%4)*tile,(index//4)*(tile+30)+65
        display=Image.new("RGBA",(tile,tile),(235,239,244,255))
        display.alpha_composite(render.resize((tile,tile),Image.Resampling.LANCZOS))
        sheet.paste(display.convert("RGB"),(x,y))
        draw.text((x+8,y+tile+6),label,fill=(25,35,50))
        report["poses"][label]={"file":filename,"state":state,"phase":phase,"parameters":settings,**stats}
    sheet.save(output/"contact-sheet.png")
    (output/"diagnostic.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig",type=Path,default=ROOT/"assets/authoring/Maple.rig.json")
    parser.add_argument("--assets",type=Path,default=ROOT/"assets/authoring")
    parser.add_argument("--output",type=Path,default=ROOT/".cache/authoring-diagnostics")
    parser.add_argument("--runtime",type=Path,default=ROOT/"assets/live2d/Maple")
    args=parser.parse_args()
    report=diagnose(args.rig,args.assets,args.output,args.runtime)
    print(json.dumps({name:pose["invertedTriangles"] for name,pose in report["poses"].items()},indent=2))
