"""Explainable heuristic geometric parcel-fit estimator.

This is a space-fit estimate only. It is not a load-safety certification.
"""
import itertools
import pandas as pd


def _orientations(row):
    dims = (float(row.Length_cm), float(row.Width_cm), float(row.Height_cm))
    rule = str(row.get("Rotation", "Any")).strip().lower()
    if rule in ("none", "upright only", "upright"):
        choices = {(dims[0], dims[1], dims[2])}
    elif rule in ("no height rotation", "base only"):
        choices = {(dims[0], dims[1], dims[2]), (dims[1], dims[0], dims[2])}
    else:
        choices = set(itertools.permutations(dims))
    return sorted(choices, key=lambda d: (-d[2], -d[0]*d[1]))


def orientation_options(row):
    """Return the parcel's allowed axis labels and oriented dimensions."""
    dimensions=(float(row.Length_cm),float(row.Width_cm),float(row.Height_cm))
    axes=("L","W","H")
    options=[]
    for dims in _orientations(row):
        permutation=next(indexes for indexes in itertools.permutations(range(3))
                         if tuple(dimensions[index] for index in indexes)==dims)
        options.append(("-".join(axes[index] for index in permutation),dims))
    return options


def _intersects(a, b):
    return all(a[k] < b[k]+b[k+3]-1e-6 and b[k] < a[k]+a[k+3]-1e-6 for k in range(3))


def _covered_area(x,y,length,width,rectangles):
    """Area of a target footprint covered by the union of support rectangles."""
    x_end=x+length;y_end=y+width
    clipped=[]
    for rx,ry,rw,rh in rectangles:
        left=max(x,rx);right=min(x_end,rx+rw)
        bottom=max(y,ry);top=min(y_end,ry+rh)
        if right>left and top>bottom:clipped.append((left,bottom,right,top))
    x_edges=sorted({edge for rect in clipped for edge in (rect[0],rect[2])})
    area=0.0
    for left,right in zip(x_edges,x_edges[1:]):
        if right<=left:continue
        mid=(left+right)/2
        intervals=sorted((bottom,top) for x1,bottom,x2,top in clipped if x1<=mid<x2)
        if not intervals:continue
        covered=0.0;low,high=intervals[0]
        for next_low,next_high in intervals[1:]:
            if next_low<=high:
                high=max(high,next_high)
            else:
                covered+=high-low;low,high=next_low,next_high
        area+=(right-left)*(covered+high-low)
    return area


def _positions(placed, L, W, H):
    """Generate bounded candidate points at existing parcel faces."""
    candidates={(0.0,0.0,0.0)}
    for p in placed:
        x,y,z,l,w,h=p[:6]
        candidates.update({
            (x+l,y,z),(x,y+w,z),(x,y,z+h),
            (x+l,y+w,z),(x+l,y,z+h),(x,y+w,z+h),(x+l,y+w,z+h),
        })
    return sorted(candidates,key=lambda point:(point[2],point[1],point[0]))


def check_fit(parcels, vehicle, orientation_overrides=None):
    orientation_overrides=orientation_overrides or {}
    cargo = []
    for line_index, row in parcels.reset_index(drop=True).iterrows():
        order_id=str(row.get("Order_ID","ORDER"))
        parcel_id=str(row.Parcel_ID)
        line_id=row.get("Parcel_Unit_ID","")
        if pd.isna(line_id):line_id=""
        for unit_index in range(1,int(row.Quantity)+1):
            unit_id="{}#{}".format(str(line_id).strip() or "{}:{}".format(order_id,parcel_id),unit_index)
            cargo.append({"row":row,"unit_id":unit_id,"order_id":order_id})
    cargo.sort(key=lambda item: (max(float(item["row"].Length_cm),float(item["row"].Width_cm),float(item["row"].Height_cm)), float(item["row"].Length_cm)*float(item["row"].Width_cm)*float(item["row"].Height_cm)), reverse=True)
    L,W,H = (float(vehicle.Interior_Length_cm),float(vehicle.Interior_Width_cm),float(vehicle.Interior_Height_cm))
    door_w=float(vehicle.get("Door_Width_cm",0) or 0)
    door_h=float(vehicle.get("Door_Height_cm",0) or 0)
    placed, failed = [], []
    placement_rows=[]
    failures=[]
    for item in cargo:
        row=item["row"]
        found = None
        door_blocked=False
        for x,y,z in _positions(placed,L,W,H):
            forced=orientation_overrides.get(item["unit_id"])
            orientations=orientation_options(row)
            if forced:
                orientations=[option for option in orientations if option[0]==forced]
            for orientation,dims in orientations:
                l,w,h=dims
                if (door_w>0 and w>door_w+1e-6) or (door_h>0 and h>door_h+1e-6):
                    door_blocked=True
                    continue
                if x+l>L+1e-6 or y+w>W+1e-6 or z+h>H+1e-6: continue
                candidate=(x,y,z,l,w,h)
                if any(_intersects(candidate, (p[0],p[1],p[2],p[3],p[4],p[5])) for p in placed): continue
                found=(x,y,z,dims); break
            if found: break
        if not found:
            failed.append(item["unit_id"])
            failures.append({"Parcel_Unit_ID":item["unit_id"],"Reason":"No allowed orientation clears the vehicle door." if door_blocked else "No non-overlapping placement found in this vehicle."})
            continue
        x,y,z,dims=found
        parcel_name=str(row.Parcel_ID)
        stackable=bool(str(row.get("Stackable","Yes")).lower() in ("yes","true","1"))
        weight=float(row.Weight_kg)
        max_stack=float(row.get("Max_Stack_Weight_kg",0) or 0)
        placed.append((x,y,z,*dims,parcel_name,stackable,weight,max_stack))
        base_dims=(float(row.Length_cm),float(row.Width_cm),float(row.Height_cm))
        axis_names=("L","W","H")
        axes=next((p for p in itertools.permutations(range(3)) if tuple(base_dims[index] for index in p)==dims),(0,1,2))
        placement_rows.append({"Parcel_Unit_ID":item["unit_id"],"Order_ID":item["order_id"],"Parcel_ID":parcel_name,
            "Orientation":"-".join(axis_names[index] for index in axes),"X_cm":round(x,1),"Y_cm":round(y,1),"Z_cm":round(z,1),
            "Placed_Length_cm":round(dims[0],1),"Placed_Width_cm":round(dims[1],1),"Placed_Height_cm":round(dims[2],1),"Weight_kg":weight})
    # Stack loads flow from upper parcels to the parcels directly supporting
    # them; a missing catalog limit is reported as unverified, not unlimited.
    unsupported=[];exceeded_stack=[];unknown_stack_limits=[]
    load_above=[0.0]*len(placed)
    for index in sorted(range(len(placed)),key=lambda value:placed[value][2],reverse=True):
        x,y,z,l,w,h,pid,_,weight,_=placed[index]
        if z<=1e-6:continue
        contacts=[]
        for base_index,q in enumerate(placed):
            qx,qy,qz,ql,qw,qh,qid,qstack,qweight,qmax=q
            if not qstack or abs(qz+qh-z)>1e-6:continue
            area=max(0,min(x+l,qx+ql)-max(x,qx))*max(0,min(y+w,qy+qw)-max(y,qy))
            if area>1e-9:contacts.append((base_index,area,(qx,qy,ql,qw)))
        covered=_covered_area(x,y,l,w,[rect for _,_,rect in contacts])
        if covered<l*w-1e-6:
            unsupported.append(pid)
            continue
        total_contact=sum(area for _,area,_ in contacts)
        transmitted=weight+load_above[index]
        for base_index,area,_ in contacts:
            load_above[base_index]+=transmitted*area/total_contact
    for index,p in enumerate(placed):
        pid,stackable,max_stack=p[6],p[7],p[9]
        if load_above[index]<=1e-6:continue
        if not stackable:continue
        if max_stack<=0:unknown_stack_limits.append(pid)
        elif load_above[index]>max_stack+1e-6:exceeded_stack.append(pid)
    total_weight=sum(float(r.Weight_kg)*int(r.Quantity) for _,r in parcels.iterrows())
    unsupported_note=(f" Stacking support is not verified for: {', '.join(sorted(set(unsupported))[:5])}." if unsupported else "")
    weight_note=(f" Maximum stacking load exceeded for: {', '.join(sorted(set(exceeded_stack))[:5])}." if exceeded_stack else "")
    unknown_note=(f" Maximum stacking load is unknown for: {', '.join(sorted(set(unknown_stack_limits))[:5])}." if unknown_stack_limits else "")
    reason=("All parcels fit geometrically." if not failed else f"Could not place {len(failed)} parcel(s): {', '.join(failed[:5])}")+unsupported_note+weight_note+unknown_note
    if failures:
        reason += " " + " ".join("{}: {}".format(row["Parcel_Unit_ID"],row["Reason"]) for row in failures[:5])
    return {"fits":not failed and not unsupported and not exceeded_stack and not unknown_stack_limits,"reason":reason,"placed":placed,"placement_rows":placement_rows,"failed":failed,"failures":failures,"unsupported_stack":unsupported,"exceeded_stack":sorted(set(exceeded_stack)),"unknown_stack_limits":sorted(set(unknown_stack_limits)),"weight_kg":total_weight,"volume_used_pct":100*sum(float(r.Length_cm)*float(r.Width_cm)*float(r.Height_cm)*int(r.Quantity) for _,r in parcels.iterrows())/(L*W*H)}
