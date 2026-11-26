"""Fleet-level shipment assignment followed by orientation-aware 3D placement.

OR-Tools CP-SAT assigns whole shipments to vehicles. A deterministic geometric
packer then validates each complete vehicle load and returns parcel-unit
orientations and coordinates. This is a bounded best-found process, not a proof
of globally optimal 3D packing or cargo safety.
"""
import time

import pandas as pd

from paracoat.packing import check_fit


def _available(fleet):
    return fleet[fleet.Available_Today.astype(str).str.lower().isin(
        ["yes", "true", "1", "available"]
    )].reset_index(drop=True)


def _weight_grams(value):
    return int(round(float(value) * 1000))


def _volume_cm3(length,width,height):
    return int(round(float(length) * float(width) * float(height)))


def _vehicle_volume_cm3(vehicle):
    """Use configured usable volume when present, otherwise derive the box volume."""
    configured=float(vehicle.get("Volume_m3",0) or 0)
    if configured>0:
        return int(round(configured*1_000_000))
    return _volume_cm3(vehicle.Interior_Length_cm,vehicle.Interior_Width_cm,vehicle.Interior_Height_cm)


def _route_proxy_coefficients(orders,vehicles,distances,durations,objective):
    """Approximate vehicle-specific route cost with insertion savings.

    Vehicle assignment is solved before route sequencing. This estimate clusters
    nearby orders and can reflect per-km cost; the route solver computes the
    actual stop order and route totals afterward.
    """
    route_metric=durations if str(objective).lower().startswith("travel") else distances
    depot_index=0
    nodes={str(row.Order_ID):int(getattr(row,"_matrix_index",index+1)) for index,row in orders.reset_index(drop=True).iterrows()}
    cost_base={};route_base={};cost_saving={};route_saving={}
    configured_costs={str(row.Vehicle_ID):max(0,float(row.get("Cost_Per_Km",0) or 0)) for _,row in vehicles.iterrows()}
    has_cost=any(value>0 for value in configured_costs.values())
    for i,left in enumerate(orders.Order_ID.astype(str).tolist()):
        li=nodes[left]
        for right in orders.Order_ID.astype(str).tolist()[i+1:]:
            ri=nodes[right]
            distance_save=max(0,float(distances[depot_index][li])+float(distances[depot_index][ri])-float(distances[li][ri]))
            route_save=max(0,float(route_metric[depot_index][li])+float(route_metric[depot_index][ri])-float(route_metric[li][ri]))
            cost_saving[(left,right)]=int(round(distance_save*10))
            route_saving[(left,right)]=int(round(route_save*10))
    for _,vehicle in vehicles.iterrows():
        vehicle_id=str(vehicle.Vehicle_ID)
        configured=configured_costs[vehicle_id]
        rate=(configured if configured>0 else 1.0) if has_cost else 0.0
        for order_id,node in nodes.items():
            distance_trip=float(distances[depot_index][node])
            route_trip=float(route_metric[depot_index][node])
            cost_base[(order_id,vehicle_id)]=max(0,int(round(2*distance_trip*rate*10)))
            route_base[(order_id,vehicle_id)]=max(0,int(round(2*route_trip*10)))
    return cost_base,route_base,cost_saving,route_saving,has_cost


def _unassigned_reason(order_id,parcels,vehicles,demand,volume):
    lines=parcels[parcels.Order_ID.astype(str)==str(order_id)]
    if vehicles.empty:return "No vehicles are available."
    if not any(demand[order_id]<=_weight_grams(vehicle.Capacity_kg) for _,vehicle in vehicles.iterrows()):
        return "Shipment payload exceeds the capacity of every available vehicle."
    volume_eligible=[vehicle for _,vehicle in vehicles.iterrows()
                     if demand[order_id]<=_weight_grams(vehicle.Capacity_kg)
                     and volume[order_id]<=_vehicle_volume_cm3(vehicle)]
    if not volume_eligible:return "Shipment volume exceeds the usable cargo volume of every payload-eligible vehicle."
    reasons=[]
    for vehicle in volume_eligible:
        fit=check_fit(lines,vehicle)
        if fit["fits"]:return "Shipment could not be included in a feasible fleet combination within the optimizer search limit."
        reasons.append(fit["reason"])
    return reasons[0] if reasons else "No eligible vehicle can carry this shipment."


def optimize_fleet_loading(orders,parcels,fleet,distances,durations,objective="Distance",
                           time_limit_seconds=8,max_candidate_iterations=12,candidate_validator=None):
    """Assign shipment groups to the smallest, lowest-cost packable fleet.

    The objective is lexicographic: maximize complete shipments served, then
    minimize active vehicles, then minimize configured operating cost (when
    supplied), route proxy, and active cargo volume as a utilization tie-break.
    Failed geometric combinations receive exact-assignment no-good cuts.
    """
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return {"assignments":{},"placements":[],"fit_results":{},"unassigned":[
            {"Order_ID":str(value),"Reason":"OR-Tools CP-SAT is not installed; fleet loading optimization is unavailable."}
            for value in orders.Order_ID.astype(str)],"vehicles_used":0,"method":"No fleet optimizer",
            "status":"Fleet optimizer unavailable","candidates_checked":0,"vehicle_count_optimal":False,
            "objective_optimal":False,"candidate_validation":None}

    available=_available(fleet)
    order_ids=orders.Order_ID.astype(str).tolist()
    if not order_ids:
        return {"assignments":{},"placements":[],"fit_results":{},"unassigned":[],"vehicles_used":0,
                "method":"OR-Tools CP-SAT + geometric packer","status":"No shipments selected","candidates_checked":0,
                "vehicle_count_optimal":False,"objective_optimal":False,"candidate_validation":None}
    if available.empty:
        return {"assignments":{},"placements":[],"fit_results":{},"unassigned":[
            {"Order_ID":order_id,"Reason":"No vehicles are available."} for order_id in order_ids],
            "vehicles_used":0,"method":"OR-Tools CP-SAT + geometric packer","status":"No available vehicles","candidates_checked":0,
            "vehicle_count_optimal":False,"objective_optimal":False,"candidate_validation":None}

    parcel_lines=parcels.copy()
    parcel_lines["Order_ID"]=parcel_lines.Order_ID.astype(str)
    missing=sorted(set(order_ids)-set(parcel_lines.Order_ID))
    if missing:
        return {"assignments":{},"placements":[],"fit_results":{},"unassigned":[
            {"Order_ID":order_id,"Reason":"Shipment has no parcel lines."} for order_id in missing],
            "vehicles_used":0,"method":"OR-Tools CP-SAT + geometric packer","status":"Invalid shipment loads","candidates_checked":0,
            "vehicle_count_optimal":False,"objective_optimal":False,"candidate_validation":None}

    demand={}
    volume={}
    for order_id in order_ids:
        lines=parcel_lines[parcel_lines.Order_ID==order_id]
        demand[order_id]=sum(_weight_grams(row.Weight_kg)*int(row.Quantity) for _,row in lines.iterrows())
        volume[order_id]=sum(_volume_cm3(row.Length_cm,row.Width_cm,row.Height_cm)*int(row.Quantity) for _,row in lines.iterrows())

    vehicle_ids=available.Vehicle_ID.astype(str).tolist()
    model=cp_model.CpModel()
    use={vehicle_id:model.NewBoolVar("use_"+str(index)) for index,vehicle_id in enumerate(vehicle_ids)}
    assign={(order_id,vehicle_id):model.NewBoolVar("assign_{}_{}".format(i,j))
            for i,order_id in enumerate(order_ids) for j,vehicle_id in enumerate(vehicle_ids)}
    unassigned={order_id:model.NewBoolVar("unassigned_{}".format(index)) for index,order_id in enumerate(order_ids)}

    for order_id in order_ids:
        model.Add(sum(assign[(order_id,vehicle_id)] for vehicle_id in vehicle_ids)+unassigned[order_id]==1)
    for vehicle_id in vehicle_ids:
        vehicle=available[available.Vehicle_ID.astype(str)==vehicle_id].iloc[0]
        model.Add(sum(assign[(order_id,vehicle_id)] for order_id in order_ids)<=len(order_ids)*use[vehicle_id])
        model.Add(use[vehicle_id]<=sum(assign[(order_id,vehicle_id)] for order_id in order_ids))
        cap_weight=_weight_grams(vehicle.Capacity_kg)
        cap_volume=_vehicle_volume_cm3(vehicle)
        model.Add(sum(demand[order_id]*assign[(order_id,vehicle_id)] for order_id in order_ids)<=cap_weight)
        model.Add(sum(volume[order_id]*assign[(order_id,vehicle_id)] for order_id in order_ids)<=cap_volume)
        for order_id in order_ids:
            if demand[order_id]>cap_weight or volume[order_id]>cap_volume:
                model.Add(assign[(order_id,vehicle_id)]==0)

    cost_base,route_base,cost_saving,route_saving,has_cost=_route_proxy_coefficients(orders,available,distances,durations,objective)
    pair_together={}
    for vehicle_index,vehicle_id in enumerate(vehicle_ids):
        for i,left in enumerate(order_ids):
            for right in order_ids[i+1:]:
                together=model.NewBoolVar("together_{}_{}_{}".format(vehicle_index,i,order_ids.index(right)))
                left_var=assign[(left,vehicle_id)];right_var=assign[(right,vehicle_id)]
                model.Add(together<=left_var);model.Add(together<=right_var)
                model.Add(together>=left_var+right_var-1)
                pair_together[(left,right,vehicle_id)]=together

    cost_terms=[];route_terms=[];cost_bounds=[];route_bounds=[]
    for (order_id,vehicle_id),variable in assign.items():
        cost_coefficient=cost_base[(order_id,vehicle_id)]
        route_coefficient=route_base[(order_id,vehicle_id)]
        cost_terms.append(cost_coefficient*variable);cost_bounds.append(abs(cost_coefficient))
        route_terms.append(route_coefficient*variable);route_bounds.append(abs(route_coefficient))
    for (left,right,vehicle_id),variable in pair_together.items():
        vehicle=available[available.Vehicle_ID.astype(str)==vehicle_id].iloc[0]
        configured=max(0,float(vehicle.get("Cost_Per_Km",0) or 0))
        rate=(configured if configured>0 else 1.0) if has_cost else 0.0
        cost_coefficient=int(round(cost_saving[(left,right)]*rate))
        route_coefficient=route_saving[(left,right)]
        cost_terms.append(-cost_coefficient*variable);cost_bounds.append(abs(cost_coefficient))
        route_terms.append(-route_coefficient*variable);route_bounds.append(abs(route_coefficient))
    cost_bound=sum(cost_bounds);route_bound=sum(route_bounds)
    space_cost={vehicle_id:max(1,int(round(_vehicle_volume_cm3(row)/1_000_000)))
                for _,row in available.iterrows() for vehicle_id in [str(row.Vehicle_ID)]}
    space_bound=sum(space_cost.values())
    space_weight=space_bound+1
    route_weight=route_bound*space_weight+space_bound+1
    lower_priority_bound=(cost_bound*route_weight+route_bound*space_weight+space_bound
                          if has_cost else route_bound*space_weight+space_bound)
    vehicle_penalty=lower_priority_bound+1
    unassigned_penalty=(len(vehicle_ids)+1)*vehicle_penalty+lower_priority_bound+1
    space_terms=[space_cost[vehicle_id]*use[vehicle_id] for vehicle_id in vehicle_ids]
    if has_cost:
        lower_priority=route_weight*sum(cost_terms)+space_weight*sum(route_terms)+sum(space_terms)
    else:
        lower_priority=space_weight*sum(route_terms)+sum(space_terms)
    model.Minimize(unassigned_penalty*sum(unassigned.values())+
                   vehicle_penalty*sum(use.values())+lower_priority)

    started=time.monotonic()
    solver=cp_model.CpSolver()
    solver.parameters.num_search_workers=1
    solver.parameters.random_seed=0
    failures_by_order={}
    candidates_checked=0
    best_assignments={}
    best_placements=[]
    best_fit={}
    partial_assignments={}
    partial_placements=[]
    partial_fit={}
    partial_unassigned=[]
    final_status="No complete load passed geometric packing."
    final_objective_optimal=False
    final_validation=None
    while candidates_checked<max_candidate_iterations:
        remaining_time=float(time_limit_seconds)-(time.monotonic()-started)
        if remaining_time<=.1:break
        solver.parameters.max_time_in_seconds=remaining_time
        status=solver.Solve(model)
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            final_status="No further assignment candidate satisfies the fleet constraints."
            break
        candidates_checked+=1
        assignments={vehicle_id:[] for vehicle_id in vehicle_ids}
        for order_id in order_ids:
            for vehicle_id in vehicle_ids:
                if solver.Value(assign[(order_id,vehicle_id)]):assignments[vehicle_id].append(order_id)
        assignments={vehicle_id:items for vehicle_id,items in assignments.items() if items}
        fit_results={}
        placements=[]
        failed_groups=[]
        feasible_assignments={}
        feasible_fits={}
        for vehicle_id,assigned_orders in assignments.items():
            vehicle=available[available.Vehicle_ID.astype(str)==vehicle_id].iloc[0]
            result=check_fit(parcel_lines[parcel_lines.Order_ID.isin(assigned_orders)],vehicle)
            fit_results[vehicle_id]=result
            if result["fits"]:
                feasible_assignments[vehicle_id]=assigned_orders
                feasible_fits[vehicle_id]=result
                for row in result["placement_rows"]:
                    row["Vehicle_ID"]=vehicle_id
                placements.extend(result["placement_rows"])
            else:
                failed_groups.append((vehicle_id,assigned_orders,result["reason"]))
                for order_id in assigned_orders:failures_by_order[order_id]=result["reason"]
        validation=None
        route_assignments=assignments if not failed_groups else feasible_assignments
        if candidate_validator is not None and route_assignments:
            validation=candidate_validator(route_assignments)
            if not validation.get("feasible",False):
                failed_groups.extend(validation.get("failed_groups",[]))
                for _,failed_orders,reason in validation.get("failed_groups",[]):
                    for order_id in failed_orders:failures_by_order[str(order_id)]=reason
        if not failed_groups:
            best_assignments=assignments
            best_placements=placements
            best_fit=fit_results
            final_status="Feasible load assignment"
            final_objective_optimal=status==cp_model.OPTIMAL
            final_validation=validation
            break
        failed_vehicle_ids={vehicle_id for vehicle_id,_,_ in failed_groups}
        partial_assignments={vehicle_id:assigned_orders for vehicle_id,assigned_orders in feasible_assignments.items() if vehicle_id not in failed_vehicle_ids}
        partial_placements=[row for row in placements if row.get("Vehicle_ID") not in failed_vehicle_ids]
        partial_fit={vehicle_id:result for vehicle_id,result in feasible_fits.items() if vehicle_id not in failed_vehicle_ids}
        final_validation=validation
        partial_unassigned=[{"Order_ID":order_id,"Reason":"No feasible vehicle assignment was found."}
                            for order_id in order_ids if solver.Value(unassigned[order_id])]
        for _,failed_orders,reason in failed_groups:
            partial_unassigned.extend({"Order_ID":order_id,"Reason":reason} for order_id in failed_orders)
        for vehicle_id,failed_orders,reason in failed_groups:
            # Forbid this exact set on this vehicle; alternatives remain available.
            failed_set=set(failed_orders)
            signed=sum(assign[(order_id,vehicle_id)] for order_id in failed_orders)-sum(
                assign[(order_id,vehicle_id)] for order_id in order_ids if order_id not in failed_set)
            model.Add(signed<=len(failed_orders)-1)

    # If no fully packable candidate was found, keep individually feasible shipments
    # out of route planning rather than presenting a known-bad load.
    unassigned_rows=[]
    if best_assignments:
        assigned_set={order_id for group in best_assignments.values() for order_id in group}
        for order_id in order_ids:
            if order_id in assigned_set:continue
            reason=_unassigned_reason(order_id,parcel_lines,available,demand,volume)
            if order_id in failures_by_order:reason=failures_by_order[order_id]
            unassigned_rows.append({"Order_ID":order_id,"Reason":reason})
    elif partial_assignments:
        best_assignments=partial_assignments
        best_placements=partial_placements
        best_fit=partial_fit
        unassigned_rows=partial_unassigned
        final_status="Partial feasible loads; some shipments could not be packed."
    else:
        for order_id in order_ids:
            reason=failures_by_order.get(order_id,_unassigned_reason(order_id,parcel_lines,available,demand,volume))
            unassigned_rows.append({"Order_ID":order_id,"Reason":reason})

    return {"assignments":best_assignments,"placements":best_placements,"fit_results":{
        vehicle_id:{key:value for key,value in result.items() if key not in ("placed","placement_rows")}
        for vehicle_id,result in best_fit.items()},"unassigned":unassigned_rows,
        "vehicles_used":len(best_assignments),"method":"Google OR-Tools CP-SAT + orientation-aware geometric packing",
        "status":final_status,"candidates_checked":candidates_checked,
        "vehicle_count_optimal":final_objective_optimal,
        "objective_optimal":final_objective_optimal,"candidate_validation":final_validation}
