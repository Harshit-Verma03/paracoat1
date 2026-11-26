"""Validated dispatch planning and editing workflow."""
import pandas as pd

from paracoat.packing import check_fit, orientation_options
from paracoat.planner import optimize
from paracoat.loading_optimizer import optimize_fleet_loading


def order_demands(parcels):
    frame = parcels.assign(_line_weight=parcels.Weight_kg * parcels.Quantity)
    return frame.groupby("Order_ID")["_line_weight"].sum().to_dict()


def _clock_minutes(value, default=0):
    try:
        hours,minutes=map(int,str(value).split(":")[:2])
        return hours*60+minutes
    except Exception:
        return default


def route_metrics(routes, orders, distances, durations, departure_time="08:00"):
    """Recompute route legs and totals from the saved matrix and stop order."""
    rows = [dict(row) for row in routes]
    order_by_id = {str(row.Order_ID): row for _, row in orders.iterrows()}
    total_km = total_minutes = 0.0
    departure=_clock_minutes(departure_time,480)
    for vehicle_id in sorted({str(row["Vehicle_ID"]) for row in rows}):
        group = sorted((row for row in rows if str(row["Vehicle_ID"]) == vehicle_id), key=lambda row: int(row["Stop"]))
        previous, elapsed, clock = 0, 0.0, float(departure)
        for stop, row in enumerate(group, 1):
            node = int(row["_matrix_index"])
            leg_km, leg_min = float(distances[previous][node]), float(durations[previous][node])
            source=order_by_id[str(row["Order_ID"])]
            arrival=clock+leg_min
            start,end=str(source.get("Window_Start","") or "").strip(),str(source.get("Window_End","") or "").strip()
            if start and end:
                start_min,end_min=_clock_minutes(start),_clock_minutes(end)
                arrival=max(arrival,float(start_min))
                if arrival>end_min+1e-6:
                    raise ValueError(f"Route violates {row['Order_ID']} delivery window ({start}–{end}).")
            service = float(source.get("Service_Minutes", 0) or 0)
            clock=arrival+service
            elapsed=clock-departure
            row["Stop"] = stop
            row["Leg_km"] = round(leg_km, 2)
            row["Cumulative_Estimated_Minutes"] = round(elapsed, 1)
            previous = node
            total_km += leg_km
        if group:
            total_km += float(distances[previous][0])
            total_minutes += elapsed + float(durations[previous][0])
    return rows, total_km, total_minutes


def _make_route_rows(assigned, orders, fleet, distances, durations, objective, departure_time="08:00"):
    output = []
    for vehicle_id, ids in assigned.items():
        if not ids:
            continue
        frame = orders[orders.Order_ID.astype(str).isin(ids)].copy().reset_index(drop=True)
        global_indices = frame["_matrix_index"].astype(int).tolist()
        points = [0] + global_indices
        sub_dist = [[distances[i][j] for j in points] for i in points]
        sub_dur = [[durations[i][j] for j in points] for i in points]
        frame["_matrix_index"] = range(1, len(frame) + 1)
        truck = fleet[fleet.Vehicle_ID.astype(str) == str(vehicle_id)].copy().assign(Available_Today="Yes")
        planned = optimize(frame, truck, sub_dist, sub_dur, objective=objective, time_limit_seconds=1, departure_time=departure_time)
        for row in planned["routes"]:
            original = frame[frame.Order_ID.astype(str) == str(row["Order_ID"])].iloc[0]
            row["Vehicle_ID"] = str(vehicle_id)
            row["Latitude"], row["Longitude"] = float(original.Latitude), float(original.Longitude)
            row["_matrix_index"] = global_indices[int(original._matrix_index) - 1]
        output.extend(planned["routes"])
    return output


def build_dispatch(orders, parcels, fleet, distances, durations, objective="Distance", time_limit_seconds=8, departure_time="08:00"):
    """Optimize shipment-to-vehicle loading, then sequence each assigned route."""
    demand = order_demands(parcels)
    planning = orders.copy().reset_index(drop=True)
    planning["Demand_kg"] = planning.Order_ID.map(demand).astype(float)
    planning["_matrix_index"] = range(1, len(planning) + 1)
    available = fleet[fleet.Available_Today.astype(str).str.lower().isin(["yes", "true", "1", "available"])].copy()
    def validate_route_candidate(proposed):
        proposed_routes=_make_route_rows(proposed,planning,fleet,distances,durations,objective,departure_time)
        routed_ids={str(row["Order_ID"]) for row in proposed_routes}
        failed=[]
        for vehicle_id,ids in proposed.items():
            missing_ids=[str(value) for value in ids if str(value) not in routed_ids]
            if missing_ids:
                failed.append((str(vehicle_id),[str(value) for value in ids],"Route optimizer could not sequence every stop within the delivery constraints."))
        if failed:return {"feasible":False,"failed_groups":failed,"routes":proposed_routes}
        try:route_metrics(proposed_routes,planning,distances,durations,departure_time)
        except ValueError as exc:
            return {"feasible":False,"failed_groups":[(str(vehicle_id),[str(value) for value in ids],str(exc)) for vehicle_id,ids in proposed.items()],"routes":proposed_routes}
        return {"feasible":True,"failed_groups":[],"routes":proposed_routes}

    load_plan=optimize_fleet_loading(planning,parcels,available,distances,durations,objective,time_limit_seconds,
                                     candidate_validator=validate_route_candidate)
    assignments={str(vehicle_id):[str(value) for value in ids] for vehicle_id,ids in load_plan["assignments"].items()}
    unassigned={str(item["Order_ID"]):item["Reason"] for item in load_plan["unassigned"]}

    validation=load_plan.get("candidate_validation") or {}
    routes=validation.get("routes")
    if routes is None:
        routes=_make_route_rows(assignments, planning, fleet, distances, durations, objective, departure_time)
    # Rebuild rows using a full matrix-index map. Keep solver stop sequence but recalculate
    # every leg against the original depot + order matrix.
    index_by_order = {str(row.Order_ID): int(row._matrix_index) for _, row in planning.iterrows()}
    order_by_id = {str(row.Order_ID): row for _, row in planning.iterrows()}
    route_ids={str(row["Order_ID"]) for row in routes}
    assigned_ids={order_id for ids in assignments.values() for order_id in ids}
    for missing_id in sorted(assigned_ids-route_ids):
        unassigned[missing_id]="The route solver could not produce a sequence for this vehicle assignment."
    assignments={vehicle_id:[order_id for order_id in ids if order_id in route_ids] for vehicle_id,ids in assignments.items() if any(order_id in route_ids for order_id in ids)}
    for row in routes:
        source = order_by_id[str(row["Order_ID"])]
        row["_matrix_index"] = index_by_order[str(row["Order_ID"])]
        row["Demand_kg"] = float(source.Demand_kg)
    # Recompute stop order with nearest next stop from depot when distance is the objective.
    has_windows = planning.Window_Start.fillna("").astype(str).str.strip().ne("").any()
    if objective == "Distance" and not has_windows:
        for vehicle_id in assignments:
            group = [row for row in routes if str(row["Vehicle_ID"]) == vehicle_id]
            pending, sequence, current = group[:], [], 0
            while pending:
                row = min(pending, key=lambda r: float(distances[current][int(r["_matrix_index"])]))
                sequence.append(row)
                pending.remove(row)
                current = int(row["_matrix_index"])
            for stop, row in enumerate(sequence, 1):
                row["Stop"] = stop
    routes, total_km, total_minutes = route_metrics(routes, planning, distances, durations, departure_time)
    fit_results=load_plan["fit_results"]
    exceptions = [{"Order_ID": order_id, "Reason": reason} for order_id, reason in unassigned.items()]
    status="Feasible plan" if not exceptions else "Review required — some shipments are unassigned"
    return {
        "routes": routes,
        "unassigned": exceptions,
        "total_km": total_km,
        "total_minutes": total_minutes,
        "vehicles_used": len(assignments),
        "status": status,
        "method": load_plan["method"]+"; route sequencing via OR-Tools",
        "objective": objective,
        "fit_results": fit_results,
        "assignments": assignments,
        "placements":load_plan["placements"],
        "load_optimizer_status":load_plan["status"],
        "load_candidates_checked":load_plan["candidates_checked"],
        "vehicle_count_optimal":load_plan["vehicle_count_optimal"],
        "objective_optimal":load_plan["objective_optimal"],
    }


def reassign_order(plan, order_id, target_vehicle, orders, parcels, fleet, distances, durations, departure_time="08:00"):
    """Reassign one whole order and reject violations without mutating the source plan."""
    order_id, target_vehicle = str(order_id), str(target_vehicle)
    was_assigned = any(str(row["Order_ID"]) == order_id for row in plan["routes"])
    was_unassigned = any(str(row.get("Order_ID")) == order_id for row in plan.get("unassigned", []))
    if not was_assigned and not was_unassigned:
        raise ValueError("Order is not present in this planning run.")
    available = fleet[fleet.Available_Today.astype(str).str.lower().isin(["yes", "true", "1", "available"])].copy()
    target = available[available.Vehicle_ID.astype(str) == target_vehicle]
    if target.empty:
        raise ValueError("Choose a vehicle that is marked available.")
    proposed = [dict(row) for row in plan["routes"] if str(row["Order_ID"]) != order_id]
    order_rows = orders[orders.Order_ID.astype(str) == order_id]
    if order_rows.empty:
        raise ValueError("Order details were not found.")
    order = order_rows.iloc[0]
    parcel_rows = parcels[parcels.Order_ID.astype(str) == order_id]
    demand = float((parcel_rows.Weight_kg * parcel_rows.Quantity).sum())
    group = [row for row in proposed if str(row["Vehicle_ID"]) == target_vehicle]
    target_load = sum(float(row["Demand_kg"]) for row in group) + demand
    truck = target.iloc[0]
    if target_load > float(truck.Capacity_kg):
        raise ValueError(f"Reassignment exceeds {target_vehicle} payload capacity ({target_load:.1f} kg / {float(truck.Capacity_kg):.1f} kg).")
    assigned_ids = [str(row["Order_ID"]) for row in group] + [order_id]
    fit = check_fit(parcels[parcels.Order_ID.astype(str).isin(assigned_ids)], truck)
    if not fit["fits"]:
        raise ValueError(f"Reassignment fails geometric parcel fit: {fit['reason']}")
    row = {
        "Vehicle_ID": target_vehicle, "Stop": len(group) + 1, "Order_ID": order.Order_ID,
        "Customer_Name": order.Customer_Name, "Address": order.Address, "Demand_kg": demand,
        "Cumulative_Estimated_Minutes": 0, "Leg_km": 0,
        "Latitude": float(order.Latitude), "Longitude": float(order.Longitude),
        "_matrix_index": orders.Order_ID.astype(str).tolist().index(order_id) + 1,
    }
    proposed.append(row)
    # Resequence every remaining route against current travel and time-window constraints.
    demand = order_demands(parcels)
    planning = orders.copy().reset_index(drop=True)
    planning["Demand_kg"] = planning.Order_ID.map(demand).astype(float)
    planning["_matrix_index"] = range(1, len(planning)+1)
    assignments={}
    for item in proposed: assignments.setdefault(str(item["Vehicle_ID"]),[]).append(str(item["Order_ID"]))
    revised=_make_route_rows(assignments,planning,fleet,distances,durations,plan.get("objective","Distance"),departure_time)
    expected={str(item["Order_ID"]) for item in proposed}
    actual={str(item["Order_ID"]) for item in revised}
    if expected != actual:
        raise ValueError("The reassigned routes could not be sequenced feasibly with the configured time windows.")
    has_windows=planning.Window_Start.fillna("").astype(str).str.strip().ne("").any()
    if plan.get("objective","Distance")=="Distance" and not has_windows:
        for vehicle_id in assignments:
            group=[item for item in revised if str(item["Vehicle_ID"])==vehicle_id]
            pending=group[:]; sequence=[]; current=0
            while pending:
                item=min(pending,key=lambda r:float(distances[current][int(r["_matrix_index"])]))
                sequence.append(item); pending.remove(item); current=int(item["_matrix_index"])
            for stop,item in enumerate(sequence,1):item["Stop"]=stop
    revised,total_km,total_minutes=route_metrics(revised,planning,distances,durations,departure_time)
    result = dict(plan)
    result.pop("approver",None)
    result["routes"] = revised
    result["total_km"] = total_km
    result["total_minutes"] = total_minutes
    result["vehicles_used"] = len({str(item["Vehicle_ID"]) for item in proposed})
    result["fit_results"] = dict(plan.get("fit_results", {}))
    result["placements"]=[]
    for vehicle_id in {str(item["Vehicle_ID"]) for item in proposed}:
        ids = [str(item["Order_ID"]) for item in proposed if str(item["Vehicle_ID"]) == vehicle_id]
        truck = available[available.Vehicle_ID.astype(str) == vehicle_id].iloc[0]
        current_fit = check_fit(parcels[parcels.Order_ID.astype(str).isin(ids)], truck)
        result["fit_results"][vehicle_id] = {key: value for key, value in current_fit.items() if key not in ("placed","placement_rows")}
        for placement in current_fit["placement_rows"]:
            placement["Vehicle_ID"]=vehicle_id
            result["placements"].append(placement)
    result["assignments"] = {}
    for item in proposed:
        result["assignments"].setdefault(str(item["Vehicle_ID"]), []).append(str(item["Order_ID"]))
    result["unassigned"] = [item for item in plan.get("unassigned", []) if str(item.get("Order_ID")) != order_id]
    result["status"]="Review required — some orders are unassigned" if result["unassigned"] else "Feasible plan"
    return result


def change_parcel_orientation(plan, parcel_unit_id, orientation, parcels, fleet):
    """Apply one parcel-unit orientation and repack its vehicle, preserving the draft only on success."""
    if plan.get("approver"):
        raise ValueError("Approved plans are locked and cannot be edited.")
    parcel_unit_id=str(parcel_unit_id)
    placement=next((row for row in plan.get("placements",[])
                    if str(row.get("Parcel_Unit_ID"))==parcel_unit_id),None)
    if placement is None:
        raise ValueError("Parcel unit is not present in this plan.")
    vehicle_id=str(placement["Vehicle_ID"])
    order_id=str(placement["Order_ID"])
    parcel_id=str(placement["Parcel_ID"])
    source=parcels[(parcels.Order_ID.astype(str)==order_id)&(parcels.Parcel_ID.astype(str)==parcel_id)]
    if source.empty:
        raise ValueError("Parcel details were not found.")
    allowed={label for label,_ in orientation_options(source.iloc[0])}
    orientation=str(orientation)
    if orientation not in allowed:
        raise ValueError("That orientation is not allowed for this parcel type.")
    available=fleet[fleet.Available_Today.astype(str).str.lower().isin(["yes","true","1","available"])]
    vehicle=available[available.Vehicle_ID.astype(str)==vehicle_id]
    if vehicle.empty:
        raise ValueError("The assigned vehicle is no longer available.")
    assigned_orders=[str(row["Order_ID"]) for row in plan.get("routes",[])
                     if str(row.get("Vehicle_ID"))==vehicle_id]
    vehicle_parcels=parcels[parcels.Order_ID.astype(str).isin(assigned_orders)]
    overrides={str(row["Parcel_Unit_ID"]):str(row["Orientation"])
               for row in plan.get("placements",[]) if str(row.get("Vehicle_ID"))==vehicle_id}
    overrides[parcel_unit_id]=orientation
    fit=check_fit(vehicle_parcels,vehicle.iloc[0],orientation_overrides=overrides)
    if not fit["fits"]:
        raise ValueError("Orientation change fails geometric fit: {}".format(fit["reason"]))
    result=dict(plan)
    result.pop("approver",None)
    result["placements"]=[row for row in plan.get("placements",[])
                          if str(row.get("Vehicle_ID"))!=vehicle_id]
    result["placements"].extend(dict(row,Vehicle_ID=vehicle_id) for row in fit["placement_rows"])
    result["fit_results"]=dict(plan.get("fit_results",{}))
    result["fit_results"][vehicle_id]={key:value for key,value in fit.items()
                                      if key not in ("placed","placement_rows")}
    result["status"]="Feasible plan" if not result.get("unassigned") else "Review required — some orders are unassigned"
    return result


def move_stop(plan, order_id, direction, orders, distances, durations, departure_time="08:00"):
    """Move a stop one position and reject the edit if a hard time window fails."""
    result={key:(dict(value) if isinstance(value,dict) else value) for key,value in plan.items()}
    result.pop("approver",None)
    routes=[dict(row) for row in plan["routes"]]
    matches=[row for row in routes if str(row["Order_ID"])==str(order_id)]
    if not matches: raise ValueError("Order is not assigned to a route.")
    vehicle_id=str(matches[0]["Vehicle_ID"])
    group=sorted([row for row in routes if str(row["Vehicle_ID"])==vehicle_id],key=lambda row:int(row["Stop"]))
    index=next(i for i,row in enumerate(group) if str(row["Order_ID"])==str(order_id))
    target=index+(-1 if direction=="up" else 1)
    if target<0 or target>=len(group): raise ValueError("This stop is already at that end of the route.")
    group[index],group[target]=group[target],group[index]
    for stop,row in enumerate(group,1):row["Stop"]=stop
    updated=[row for row in routes if str(row["Vehicle_ID"])!=vehicle_id]+group
    updated,total_km,total_minutes=route_metrics(updated,orders,distances,durations,departure_time)
    result["routes"]=updated; result["total_km"]=total_km; result["total_minutes"]=total_minutes
    return result
