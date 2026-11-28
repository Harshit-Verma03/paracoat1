"""Explainable heuristic geometric parcel-fit estimator.

This is a space-fit estimate only. It is not a load-safety certification.
"""
import itertools
import time
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


def _stacking_issues(placed):
    """Return support and configured stack-load violations for a full layout."""
    unsupported=[]
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
    exceeded=[]
    unknown=[]
    for index,p in enumerate(placed):
        pid,stackable,max_stack=p[6],p[7],p[9]
        if load_above[index]<=1e-6 or not stackable:continue
        if max_stack<=0:unknown.append(pid)
        elif load_above[index]>max_stack+1e-6:exceeded.append(pid)
    return sorted(set(unsupported)),sorted(set(exceeded)),sorted(set(unknown))


def _positions(placed, L, W, H):
    """Generate extreme-point candidates at faces of already placed parcels."""
    candidates={(0.0,0.0,0.0)}
    for p in placed:
        x,y,z,l,w,h=p[:6]
        candidates.update({
            (x+l,y,z),(x,y+w,z),(x,y,z+h),
            (x+l,y+w,z),(x+l,y,z+h),(x,y+w,z+h),(x+l,y+w,z+h),
        })
    return sorted(candidates,key=lambda point:(point[2],point[1],point[0]))


def _pack_order(cargo, vehicle, orientation_overrides, position_overrides, strategy, max_backtracks, deadline=None):
    """Try one deterministic parcel order, backtracking across bounded choices."""
    L,W,H = (float(vehicle.Interior_Length_cm),float(vehicle.Interior_Width_cm),float(vehicle.Interior_Height_cm))
    door_w=float(vehicle.get("Door_Width_cm",0) or 0)
    door_h=float(vehicle.get("Door_Height_cm",0) or 0)
    placed=[]
    placed_items=[]
    attempts=0
    invalid_layout=None
    fixed_items=[item for item in cargo if item["unit_id"] in position_overrides]
    movable_items=[item for item in cargo if item["unit_id"] not in position_overrides]
    for item in fixed_items:
        row=item["row"]
        x,y,z=(float(value) for value in position_overrides[item["unit_id"]])
        forced=orientation_overrides.get(item["unit_id"])
        orientations=orientation_options(row)
        if forced:orientations=[option for option in orientations if option[0]==forced]
        found=None
        for orientation,dims in orientations:
            l,w,h=dims
            if (door_w>0 and w>door_w+1e-6) or (door_h>0 and h>door_h+1e-6):continue
            candidate=(x,y,z,l,w,h)
            if x+l>L+1e-6 or y+w>W+1e-6 or z+h>H+1e-6:continue
            if any(_intersects(candidate,p[:6]) for p in placed):continue
            stop=item["delivery_stop"]
            order_ok=True
            for other,other_item in zip(placed,placed_items):
                other_stop=other_item["delivery_stop"]
                if stop is None or other_stop is None or stop==other_stop:continue
                ox,oy,oz,ol,ow,oh=other[:6]
                cross_section=(y<oy+ow-1e-6 and oy<y+w-1e-6 and z<oz+oh-1e-6 and oz<z+h-1e-6)
                if cross_section and ((stop<other_stop and x<ox+ol-1e-6) or (stop>other_stop and ox<x+l-1e-6)):
                    order_ok=False;break
            if not order_ok:continue
            found=(dims,orientation)
            break
        if found is None:return None,attempts,None
        dims,_=found
        stackable=str(row.get("Stackable","Yes")).lower() in ("yes","true","1")
        placed.append((x,y,z,*dims,str(row.Parcel_ID),stackable,float(row.Weight_kg),float(row.get("Max_Stack_Weight_kg",0) or 0)))
        placed_items.append(item)
    def candidates_for(item):
        row=item["row"]
        candidates=[]
        for x,y,z in _positions(placed,L,W,H):
            forced=orientation_overrides.get(item["unit_id"])
            orientations=orientation_options(row)
            if forced:orientations=[option for option in orientations if option[0]==forced]
            for orientation,dims in orientations:
                l,w,h=dims
                if (door_w>0 and w>door_w+1e-6) or (door_h>0 and h>door_h+1e-6):continue
                if x+l>L+1e-6 or y+w>W+1e-6 or z+h>H+1e-6:continue
                candidate=(x,y,z,l,w,h)
                if any(_intersects(candidate,p[:6]) for p in placed):continue
                candidates.append((x,y,z,dims,orientation))
        orientation_rank={label:index for index,(label,_) in enumerate(orientation_options(row))}
        candidates.sort(key=lambda c:(c[2],c[1],c[0],orientation_rank.get(c[4],99)))
        if strategy=="high" and len(candidates)>1:
            candidates.sort(key=lambda c:(-c[2],c[1],c[0],c[4]))
        elif strategy=="compact" and len(candidates)>1:
            candidates.sort(key=lambda c:((c[0]+c[3][0])*(c[1]+c[3][1]),c[2],c[4]))
        # Rear doors are at X=vehicle length. Earliest delivery stops should
        # occupy the rear-most accessible position; later stops go deeper.
        stop=item["delivery_stop"]
        if stop is not None and candidates:
            existing_rank={candidate:index for index,candidate in enumerate(candidates)}
            candidates.sort(key=lambda c:((-c[0] if stop==1 else c[0]),existing_rank.get((c[0],c[1],c[2],c[3],c[4]),0)))
        return candidates

    def delivery_order_ok(candidate,item):
        stop=item["delivery_stop"]
        if stop is None:return True
        x,y,z,l,w,h=candidate
        for other,placed_item in zip(placed,placed_items):
            other_stop=placed_item["delivery_stop"]
            if other_stop is None or other_stop==stop:continue
            ox,oy,oz,ol,ow,oh=other[:6]
            cross_section=(y<oy+ow-1e-6 and oy<y+w-1e-6 and z<oz+oh-1e-6 and oz<z+h-1e-6)
            if not cross_section:continue
            # Lower stop number is delivered first. X increases toward the
            # rear door, so it must be wholly behind later stops along X.
            if stop<other_stop and x<ox+ol-1e-6:return False
            if stop>other_stop and ox<x+l-1e-6:return False
        return True

    def search(index):
        nonlocal attempts,invalid_layout
        if deadline is not None and time.monotonic()>=deadline:return False
        if index==len(movable_items):
            unsupported,exceeded,unknown=_stacking_issues(placed)
            if unsupported or exceeded or unknown:
                if invalid_layout is None:invalid_layout=(placed.copy(),unsupported,exceeded,unknown)
                return False
            return True
        item=movable_items[index]
        row=item["row"]
        for x,y,z,dims,orientation in candidates_for(item):
            if not delivery_order_ok((x,y,z,*dims),item):continue
            stackable=str(row.get("Stackable","Yes")).lower() in ("yes","true","1")
            placed.append((x,y,z,*dims,str(row.Parcel_ID),stackable,float(row.Weight_kg),float(row.get("Max_Stack_Weight_kg",0) or 0)))
            placed_items.append(item)
            if search(index+1):return True
            placed.pop()
            placed_items.pop()
            attempts+=1
            if attempts>=max_backtracks:return False
        return False

    # Bounded depth-first backtracking avoids committing to an early placement
    # that blocks later units. Exhausting the bound is reported as unknown fit.
    found=search(0)
    if not found and invalid_layout is None:return None,attempts,None
    if found:unsupported,exceeded,unknown=[],[],[]
    else:placed,unsupported,exceeded,unknown=invalid_layout
    rows=[]
    for item,p in zip(placed_items,placed):
        x,y,z,l,w,h,pid,stackable,weight,max_stack=p
        row=item["row"]
        axes=next((perm for perm in itertools.permutations(range(3))
                   if tuple((float(row.Length_cm),float(row.Width_cm),float(row.Height_cm))[i] for i in perm)==(l,w,h)),(0,1,2))
        rows.append({"Parcel_Unit_ID":item["unit_id"],"Order_ID":item["order_id"],"Parcel_ID":pid,
            "Orientation":"-".join(("L","W","H")[i] for i in axes),"X_cm":round(x,1),"Y_cm":round(y,1),"Z_cm":round(z,1),
            "Placed_Length_cm":round(l,1),"Placed_Width_cm":round(w,1),"Placed_Height_cm":round(h,1),"Weight_kg":weight})
        if item["delivery_stop"] is not None:
            rows[-1]["Delivery_Stop"]=item["delivery_stop"]
    layout=(placed,rows,unsupported,exceeded,unknown)
    return (layout if found else None),attempts,(None if found else layout)


def check_fit(parcels, vehicle, orientation_overrides=None, max_backtracks=1000, position_overrides=None, deadline=None):
    orientation_overrides=orientation_overrides or {}
    position_overrides=position_overrides or {}
    cargo = []
    for line_index, row in parcels.reset_index(drop=True).iterrows():
        order_id=str(row.get("Order_ID","ORDER"))
        parcel_id=str(row.Parcel_ID)
        line_id=row.get("Parcel_Unit_ID","")
        if pd.isna(line_id):line_id=""
        for unit_index in range(1,int(row.Quantity)+1):
            unit_id="{}#{}".format(str(line_id).strip() or "{}:{}".format(order_id,parcel_id),unit_index)
            raw_stop=row.get("Delivery_Stop",None)
            try:delivery_stop=int(raw_stop) if pd.notna(raw_stop) else None
            except (TypeError,ValueError):delivery_stop=None
            cargo.append({"row":row,"unit_id":unit_id,"order_id":order_id,"delivery_stop":delivery_stop})
    def parcel_metrics(item):
        row=item["row"]
        stackable=str(row.get("Stackable","Yes")).lower() in ("yes","true","1")
        max_stack=float(row.get("Max_Stack_Weight_kg",0) or 0)
        support_rank=0 if stackable and max_stack>0 else 1
        footprint=float(row.Length_cm)*float(row.Width_cm)
        volume=footprint*float(row.Height_cm)
        max_dimension=max(float(row.Length_cm),float(row.Width_cm),float(row.Height_cm))
        # Verified, strong bases should usually be placed early. Keep several
        # deterministic item orderings available because a single fixed order
        # can create false negatives in irregular mixed-size cargo.
        delivery_rank=-(item["delivery_stop"] or 0)
        return footprint,volume,max_dimension,support_rank,max_stack,delivery_rank
    base_order=sorted(cargo,key=lambda item:(-parcel_metrics(item)[0],
        -parcel_metrics(item)[2],-parcel_metrics(item)[1],parcel_metrics(item)[3],
        -parcel_metrics(item)[4] if parcel_metrics(item)[3]==0 else 0,parcel_metrics(item)[5]))
    volume_order=sorted(cargo,key=lambda item:(-parcel_metrics(item)[1],
        -parcel_metrics(item)[0],-parcel_metrics(item)[2],parcel_metrics(item)[3],
        -parcel_metrics(item)[4] if parcel_metrics(item)[3]==0 else 0,parcel_metrics(item)[5]))
    longest_order=sorted(cargo,key=lambda item:(-parcel_metrics(item)[2],
        -parcel_metrics(item)[0],-parcel_metrics(item)[1],parcel_metrics(item)[3],
        -parcel_metrics(item)[4] if parcel_metrics(item)[3]==0 else 0,parcel_metrics(item)[5]))
    # Warehouse loading is reverse delivery order: deepest/later stops are
    # placed first, then earlier stops are kept nearest the rear door. Grouping
    # by stop gives the access constraint a dedicated search ordering instead
    # of treating stop rank as a final tie-break behind parcel size.
    reverse_stop_order=sorted(cargo,key=lambda item:(
        -(int(item["delivery_stop"]) if item["delivery_stop"] is not None else 0),
        -parcel_metrics(item)[0],-parcel_metrics(item)[2],-parcel_metrics(item)[1],
        parcel_metrics(item)[3],
        -parcel_metrics(item)[4] if parcel_metrics(item)[3]==0 else 0))
    L,W,H = (float(vehicle.Interior_Length_cm),float(vehicle.Interior_Width_cm),float(vehicle.Interior_Height_cm))
    packed=None
    invalid_layout=None
    backtracks=0
    timed_out=False
    search_variants=[(ordered,strategy) for ordered in
                     (base_order,volume_order,longest_order,reverse_stop_order)
                     for strategy in ("low","high","compact")]
    for variant_index,(ordered,strategy) in enumerate(search_variants):
        if deadline is not None and time.monotonic()>=deadline:
            timed_out=True;break
        remaining_backtracks=max_backtracks-backtracks
        if remaining_backtracks<=0:break
        variants_left=len(search_variants)-variant_index
        variant_budget=max(1,remaining_backtracks//max(1,variants_left))
        packed,used,invalid=_pack_order(ordered,vehicle,orientation_overrides,position_overrides,
                                        strategy,variant_budget,deadline)
        backtracks+=used
        if packed:break
        if invalid_layout is None and invalid is not None:invalid_layout=invalid
    if packed:
        placed,placement_rows,unsupported,exceeded_stack,unknown_stack_limits=packed
        failed=[];failures=[]
    elif invalid_layout is not None:
        placed,placement_rows,unsupported,exceeded_stack,unknown_stack_limits=invalid_layout
        failed=[];failures=[]
    else:
        placed=[];placement_rows=[]
        failed=[item["unit_id"] for item in cargo]
        failures=[{"Parcel_Unit_ID":unit,"Reason":"No placement found within the bounded backtracking search."} for unit in failed[:10]]
    # Stack loads flow from upper parcels to the parcels directly supporting
    # them; a missing catalog limit is reported as unverified, not unlimited.
    unsupported,exceeded_stack,unknown_stack_limits=_stacking_issues(placed)
    total_weight=sum(float(r.Weight_kg)*int(r.Quantity) for _,r in parcels.iterrows())
    unsupported_note=(f" Stacking support is not verified for: {', '.join(sorted(set(unsupported))[:5])}." if unsupported else "")
    weight_note=(f" Maximum stacking load exceeded for: {', '.join(sorted(set(exceeded_stack))[:5])}." if exceeded_stack else "")
    unknown_note=(f" Maximum stacking load is unknown for: {', '.join(sorted(set(unknown_stack_limits))[:5])}." if unknown_stack_limits else "")
    reason=("All parcels fit geometrically." if not failed else f"Could not place every parcel with the bounded placement search; {len(failed)} parcel unit(s) remain unresolved: {', '.join(failed[:5])}")+unsupported_note+weight_note+unknown_note
    if timed_out:reason="Packing search stopped at its time limit; fit is undetermined."
    if failures:
        reason += " " + " ".join("{}: {}".format(row["Parcel_Unit_ID"],row["Reason"]) for row in failures[:5])
    return {"fits":not timed_out and not failed and not unsupported and not exceeded_stack and not unknown_stack_limits,"reason":reason,"placed":placed,"placement_rows":placement_rows,"failed":failed,"failures":failures,"unsupported_stack":unsupported,"exceeded_stack":sorted(set(exceeded_stack)),"unknown_stack_limits":sorted(set(unknown_stack_limits)),"backtracks":backtracks,"packing_strategy":strategy if packed or invalid_layout else None,"timed_out":timed_out,"weight_kg":total_weight,"volume_used_pct":100*sum(float(r.Length_cm)*float(r.Width_cm)*float(r.Height_cm)*int(r.Quantity) for _,r in parcels.iterrows())/(L*W*H)}
