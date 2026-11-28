"""Fleet-level shipment assignment followed by orientation-aware 3D placement.

OR-Tools CP-SAT assigns whole shipments to vehicles. A deterministic geometric
packer then validates each complete vehicle load and returns parcel-unit
orientations and coordinates. This is a bounded best-found process, not a proof
of globally optimal 3D packing or cargo safety.
"""
import math
import time

import pandas as pd

from paracoat.packing import check_fit


def _available(fleet):
    return fleet[fleet.Available_Today.astype(str).str.lower().isin(
        ["yes", "true", "1", "available"]
    )].reset_index(drop=True)


def _weight_demand_grams(value):
    """Round each freight contribution upward to avoid payload undercounts."""
    return int(math.ceil(float(value)*1000-1e-9))


def _capacity_grams(value):
    """Round vehicle payload capacity downward for conservative constraints."""
    return int(math.floor(float(value)*1000+1e-9))


def _volume_cm3(length,width,height):
    return int(round(float(length) * float(width) * float(height)))


def _vehicle_volume_cm3(vehicle):
    """Use configured usable volume when present, otherwise derive the box volume."""
    configured=float(vehicle.get("Volume_m3",0) or 0)
    if configured>0:
        return int(round(configured*1_000_000))
    return _volume_cm3(vehicle.Interior_Length_cm,vehicle.Interior_Width_cm,vehicle.Interior_Height_cm)


def _route_proxy_coefficients(orders,vehicles,distances,durations,objective):
    """Return normalized per-shipment round-trip proxies for assignment.

    The detailed stop sequence is optimized afterward by the route solver.
    These bounded integer proxies keep the CP-SAT objective valid at larger
    shipment counts without quadratic pair variables.
    """
    route_metric=durations if str(objective).lower().startswith("travel") else distances
    depot_index=0
    nodes={str(row.Order_ID):int(getattr(row,"_matrix_index",index+1)) for index,row in orders.reset_index(drop=True).iterrows()}
    cost_raw={};route_raw={};pair_route_raw={};pair_distance_raw={}
    configured_costs={str(row.Vehicle_ID):max(0,float(row.get("Cost_Per_Km",0) or 0)) for _,row in vehicles.iterrows()}
    has_cost=any(value>0 for value in configured_costs.values())
    for _,vehicle in vehicles.iterrows():
        vehicle_id=str(vehicle.Vehicle_ID)
        configured=configured_costs[vehicle_id]
        rate=(configured if configured>0 else 1.0) if has_cost else 0.0
        for order_id,node in nodes.items():
            distance_trip=float(distances[depot_index][node])
            route_trip=float(route_metric[depot_index][node])
            cost_raw[(order_id,vehicle_id)]=max(0,2*distance_trip*rate)
            route_raw[(order_id,vehicle_id)]=max(0,2*route_trip)
    order_ids=orders.Order_ID.astype(str).tolist()
    neighbor_pairs=set()
    for order_id in order_ids:
        node=nodes[order_id]
        nearest=sorted((other for other in order_ids if other!=order_id),
                       key=lambda other:route_metric[node][nodes[other]])[:3]
        for other in nearest:neighbor_pairs.add(tuple(sorted((order_id,other))))
    for left,right in neighbor_pairs:
        li=nodes[left];ri=nodes[right]
        saving=max(0,float(route_metric[depot_index][li])+float(route_metric[depot_index][ri])-float(route_metric[li][ri]))
        pair_route_raw[(left,right)]=saving
        distance_saving=max(0,float(distances[depot_index][li])+float(distances[depot_index][ri])-float(distances[li][ri]))
        pair_distance_raw[(left,right)]=distance_saving
    cost_max=max(cost_raw.values(),default=0)
    route_max=max(route_raw.values(),default=0)
    cost_base={key:(int(round(value*10/cost_max)) if cost_max else 0) for key,value in cost_raw.items()}
    route_base={key:(int(round(value*10/route_max)) if route_max else 0) for key,value in route_raw.items()}
    route_save_max=max(pair_route_raw.values(),default=0)
    route_saving={key:(int(round(value*10/route_save_max)) if route_save_max else 0)
                  for key,value in pair_route_raw.items()}
    cost_pair_raw={}
    if has_cost:
        for pair,distance_save in pair_distance_raw.items():
            for vehicle_id,rate in configured_costs.items():
                cost_pair_raw[(pair[0],pair[1],vehicle_id)]=distance_save*(rate if rate>0 else 1.0)
    cost_pair_max=max(cost_pair_raw.values(),default=0)
    cost_saving={key:(int(round(value*10/cost_pair_max)) if cost_pair_max else 0)
                 for key,value in cost_pair_raw.items()}
    return cost_base,route_base,cost_saving,route_saving,has_cost


def _unassigned_reason(order_id,parcels,vehicles,demand,volume,check_geometry=True):
    lines=parcels[parcels.Order_ID.astype(str)==str(order_id)]
    if vehicles.empty:return "No vehicles are available."
    if not any(demand[order_id]<=_capacity_grams(vehicle.Capacity_kg) for _,vehicle in vehicles.iterrows()):
        return "Shipment payload exceeds the capacity of every available vehicle."
    volume_eligible=[vehicle for _,vehicle in vehicles.iterrows()
                     if demand[order_id]<=_capacity_grams(vehicle.Capacity_kg)
                     and volume[order_id]<=_vehicle_volume_cm3(vehicle)]
    if not volume_eligible:return "Shipment volume exceeds the usable cargo volume of every payload-eligible vehicle."
    if not check_geometry:return "No feasible delivery-order load was confirmed within the optimizer search budget."
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
    minimize active vehicles, then optimize the selected route metric, configured
    operating cost (when supplied), and active cargo volume as a tie-break.
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
    started=time.monotonic()
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
        demand[order_id]=sum(_weight_demand_grams(row.Weight_kg)*int(row.Quantity) for _,row in lines.iterrows())
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
        cap_weight=_capacity_grams(vehicle.Capacity_kg)
        cap_volume=_vehicle_volume_cm3(vehicle)
        model.Add(sum(demand[order_id]*assign[(order_id,vehicle_id)] for order_id in order_ids)<=cap_weight)
        model.Add(sum(volume[order_id]*assign[(order_id,vehicle_id)] for order_id in order_ids)<=cap_volume)
        for order_id in order_ids:
            if demand[order_id]>cap_weight or volume[order_id]>cap_volume:
                model.Add(assign[(order_id,vehicle_id)]==0)

    cost_base,route_base,cost_saving,route_saving,has_cost=_route_proxy_coefficients(orders,available,distances,durations,objective)

    cost_terms=[];route_terms=[];cost_bounds=[];route_bounds=[]
    for (order_id,vehicle_id),variable in assign.items():
        cost_coefficient=cost_base[(order_id,vehicle_id)]
        route_coefficient=route_base[(order_id,vehicle_id)]
        cost_terms.append(cost_coefficient*variable);cost_bounds.append(abs(cost_coefficient))
        route_terms.append(route_coefficient*variable);route_bounds.append(abs(route_coefficient))
    pair_together={}
    for vehicle_index,vehicle_id in enumerate(vehicle_ids):
        for pair_index,(left,right) in enumerate(sorted(route_saving)):
            together=model.NewBoolVar("nearby_{}_{}".format(vehicle_index,pair_index))
            left_var=assign[(left,vehicle_id)];right_var=assign[(right,vehicle_id)]
            model.Add(together<=left_var);model.Add(together<=right_var)
            model.Add(together>=left_var+right_var-1)
            pair_together[(left,right,vehicle_id)]=together
            route_coefficient=route_saving[(left,right)]
            route_terms.append(-route_coefficient*together);route_bounds.append(abs(route_coefficient))
            if has_cost:
                cost_coefficient=cost_saving.get((left,right,vehicle_id),0)
                cost_terms.append(-cost_coefficient*together);cost_bounds.append(abs(cost_coefficient))
    cost_bound=sum(cost_bounds);route_bound=sum(route_bounds)
    raw_space={str(row.Vehicle_ID):_vehicle_volume_cm3(row) for _,row in available.iterrows()}
    max_space=max(raw_space.values(),default=1)
    space_cost={vehicle_id:max(1,int(round(raw_space[vehicle_id]*10/max_space)))
                for _,row in available.iterrows() for vehicle_id in [str(row.Vehicle_ID)]}
    space_bound=sum(space_cost.values())
    space_weight=space_bound+1
    space_terms=[space_cost[vehicle_id]*use[vehicle_id] for vehicle_id in vehicle_ids]
    if has_cost:
        # The selected distance/time objective must outrank configured cost.
        # Every multiplier is derived from a full bound on all lower-priority
        # terms, preserving the lexicographic order without giant constants.
        cost_weight=space_bound+1
        route_weight=cost_bound*cost_weight+space_bound+1
        lower_priority=route_weight*sum(route_terms)+cost_weight*sum(cost_terms)+sum(space_terms)
        lower_priority_bound=route_bound*route_weight+cost_bound*cost_weight+space_bound
    else:
        lower_priority=space_weight*sum(route_terms)+sum(space_terms)
        lower_priority_bound=route_bound*space_weight+space_bound
    vehicle_penalty=lower_priority_bound+1
    unassigned_penalty=(len(vehicle_ids)+1)*vehicle_penalty+lower_priority_bound+1
    objective_expression=(unassigned_penalty*sum(unassigned.values())+
                         vehicle_penalty*sum(use.values())+lower_priority)
    model.Minimize(objective_expression)

    model_validation_error=model.Validate()
    if model_validation_error:
        concise_error=model_validation_error.splitlines()[0]
        return {"assignments":{},"placements":[],"fit_results":{},
            "unassigned":[{"Order_ID":order_id,"Reason":"Optimizer model is invalid: {}".format(concise_error)}
                          for order_id in order_ids],"vehicles_used":0,
            "method":"Google OR-Tools CP-SAT + orientation-aware geometric packing",
            "status":"Optimizer model invalid","termination_reason":"model_invalid",
            "solver_status":"MODEL_INVALID","model_validation_error":concise_error,
            "elapsed_seconds":0.0,"packing_search_bounded":True,"candidates_checked":0,
            "vehicle_count_optimal":False,"objective_optimal":False,"candidate_validation":None}

    solver=cp_model.CpSolver()
    solver.parameters.num_search_workers=1
    solver.parameters.random_seed=0
    failures_by_order={}
    candidates_checked=0
    candidate_diagnostics=[]
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
    termination_reason="search_limit"
    solver_status=None
    had_rejections=False
    # Build a bounded, route-checked feasible seed so a hard CP-SAT/packing
    # search can still return usable vehicle loads instead of an empty plan.
    seed_assignments={}
    seed_placements=[]
    seed_fit={}
    seed_routes=[]
    # Keep distinct budgets for the initial seed, candidate exploration, and
    # repair. A slow packing check must not consume the entire planning run.
    overall_deadline=started+max(.1,float(time_limit_seconds))
    search_deadline=started+max(.1,float(time_limit_seconds)*.60)
    seed_deadline=min(search_deadline,
                      time.monotonic()+max(.1,float(time_limit_seconds)*.25))
    longest_dimension={order_id:max(
        max(float(row.Length_cm),float(row.Width_cm),float(row.Height_cm))
        for _,row in parcel_lines[parcel_lines.Order_ID==order_id].iterrows())
        for order_id in order_ids}
    seed_orders=[
        sorted(order_ids,key=lambda value:(volume[value],demand[value]),reverse=True),
        sorted(order_ids,key=lambda value:(demand[value],volume[value]),reverse=True),
        sorted(order_ids,key=lambda value:(longest_dimension[value],volume[value],demand[value]),reverse=True),
    ]
    best_seed_score=(0,0)
    for seed_index,order_priority in enumerate(seed_orders):
        remaining_seed=seed_deadline-time.monotonic()
        if remaining_seed<=.05:break
        trial_deadline=min(seed_deadline,time.monotonic()+remaining_seed/(len(seed_orders)-seed_index))
        trial_assignments={};trial_fit={};trial_placements=[];trial_routes=[]
        for order_index,order_id in enumerate(order_priority):
            if trial_deadline-time.monotonic()<=.05:break
            eligible=[]
            for vehicle_id in vehicle_ids:
                vehicle=available[available.Vehicle_ID.astype(str)==vehicle_id].iloc[0]
                current=trial_assignments.get(vehicle_id,[])
                proposed=current+[order_id]
                if sum(demand[value] for value in proposed)>_capacity_grams(vehicle.Capacity_kg):continue
                if sum(volume[value] for value in proposed)>_vehicle_volume_cm3(vehicle):continue
                weight_fill=sum(demand[value] for value in proposed)/_capacity_grams(vehicle.Capacity_kg)
                volume_fill=sum(volume[value] for value in proposed)/_vehicle_volume_cm3(vehicle)
                eligible.append((max(weight_fill,volume_fill),weight_fill+volume_fill,vehicle_id,vehicle,proposed))
            eligible.sort(key=lambda item:(item[0],item[1],item[2]))
            attempt_budget=(trial_deadline-time.monotonic())/max(1,len(eligible))
            for _,_,vehicle_id,vehicle,proposed in eligible:
                if trial_deadline-time.monotonic()<=.05:break
                attempt_deadline=min(trial_deadline,time.monotonic()+attempt_budget)
                attempt_remaining=attempt_deadline-time.monotonic()
                if attempt_remaining<=.05:continue
                validation=(candidate_validator({vehicle_id:proposed},attempt_remaining)
                            if candidate_validator is not None else None)
                if validation is not None and not validation.get("feasible",False):continue
                stop_map={str(row["Order_ID"]):int(row["Stop"])
                          for row in (validation or {}).get("routes",[])}
                lines=parcel_lines[parcel_lines.Order_ID.isin(proposed)].copy()
                if stop_map:lines["Delivery_Stop"]=lines.Order_ID.map(stop_map)
                fit=(validation or {}).get("fit_results",{}).get(vehicle_id)
                if fit is None:
                    fit=check_fit(lines,vehicle,deadline=attempt_deadline)
                if not fit["fits"]:continue
                trial_assignments[vehicle_id]=proposed
                trial_fit[vehicle_id]=fit
                trial_placements=[row for row in trial_placements
                                  if str(row.get("Vehicle_ID"))!=vehicle_id]
                trial_placements.extend(dict(row,Vehicle_ID=vehicle_id)
                                        for row in fit["placement_rows"])
                trial_routes=[row for row in trial_routes
                              if str(row.get("Vehicle_ID"))!=vehicle_id]
                trial_routes.extend((validation or {}).get("routes",[]))
                break
        score=(sum(len(group) for group in trial_assignments.values()),-len(trial_assignments))
        if score>best_seed_score:
            best_seed_score=score
            seed_assignments=trial_assignments
            seed_fit=trial_fit
            seed_placements=trial_placements
            seed_routes=trial_routes
    best_assignments={key:list(value) for key,value in seed_assignments.items()}
    best_placements=list(seed_placements)
    best_fit=dict(seed_fit)
    if seed_assignments:
        final_status="Feasible heuristic seed; CP-SAT search continues."
        termination_reason="heuristic_seed"
        final_validation={"feasible":True,"failed_groups":[],"routes":seed_routes}
        seed_order_ids={order_id for ids in seed_assignments.values() for order_id in ids}
        for vehicle_id in vehicle_ids:
            model.AddHint(use[vehicle_id],int(vehicle_id in seed_assignments))
            for order_id in order_ids:
                model.AddHint(assign[(order_id,vehicle_id)],int(order_id in seed_assignments.get(vehicle_id,[])))
        for order_id in order_ids:
            model.AddHint(unassigned[order_id],int(order_id not in seed_order_ids))

    def assignment_score(groups):
        served=sum(len(items) for items in groups.values())
        used=sum(bool(items) for items in groups.values())
        return served,-used

    requested_floor=2 if float(time_limit_seconds)>=4 else 1
    candidate_limit=min(max_candidate_iterations,max(requested_floor,
        int(math.ceil(max(.1,float(time_limit_seconds))/6.0))))
    while candidates_checked<candidate_limit:
        remaining_time=search_deadline-time.monotonic()
        if remaining_time<=.1:break
        # Give each proposed assignment a fair slice for route and packing
        # validation. Previously one difficult candidate could starve all
        # alternatives and the final repair pass.
        iterations_left=max(1,candidate_limit-candidates_checked)
        candidate_budget=remaining_time/iterations_left
        candidate_deadline=min(search_deadline,time.monotonic()+candidate_budget)
        solver.parameters.max_time_in_seconds=max(.02,candidate_budget*.2)
        status=solver.Solve(model)
        solver_status=solver.StatusName(status)
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            final_status="No further assignment candidate satisfies the fleet constraints."
            termination_reason="model_exhausted" if status==cp_model.INFEASIBLE else "solver_ended_without_candidate"
            solver_details=solver.ResponseStats().splitlines()
            if status==cp_model.MODEL_INVALID:
                termination_reason="model_invalid"
                final_status="Optimizer model invalid"
                model_validation_error=next((line.strip() for line in solver_details if "invalid" in line.lower() or "overflow" in line.lower()),"CP-SAT rejected the model; see solver response details.")
            break
        candidates_checked+=1
        assignments={vehicle_id:[] for vehicle_id in vehicle_ids}
        for order_id in order_ids:
            for vehicle_id in vehicle_ids:
                if solver.Value(assign[(order_id,vehicle_id)]):assignments[vehicle_id].append(order_id)
        assignments={vehicle_id:items for vehicle_id,items in assignments.items() if items}
        # Route first: packing receives the exact stop ranks that will be
        # shown to the dispatcher, so cargo accessibility follows delivery order.
        remaining_time=max(0.0,candidate_deadline-time.monotonic())
        validation=(candidate_validator(assignments,remaining_time) if candidate_validator is not None and assignments
                    else None)
        failed_groups=[]
        if validation is not None and not validation.get("feasible",False):
            failed_groups.extend(validation.get("failed_groups",[]))
            for _,failed_orders,reason in validation.get("failed_groups",[]):
                for order_id in failed_orders:failures_by_order[str(order_id)]=reason
        stop_by_order={str(row["Order_ID"]):int(row["Stop"]) for row in (validation or {}).get("routes",[])}
        fit_results={}
        placements=[]
        feasible_assignments={}
        feasible_fits={}
        route_failed={vehicle_id for vehicle_id,_,_ in failed_groups}
        group_count=max(1,len(assignments))
        for group_index,(vehicle_id,assigned_orders) in enumerate(assignments.items()):
            if vehicle_id in route_failed:continue
            vehicle=available[available.Vehicle_ID.astype(str)==vehicle_id].iloc[0]
            vehicle_lines=parcel_lines[parcel_lines.Order_ID.isin(assigned_orders)].copy()
            if stop_by_order:
                vehicle_lines["Delivery_Stop"]=vehicle_lines.Order_ID.map(stop_by_order)
            remaining_for_packing=max(0.0,candidate_deadline-time.monotonic())
            group_deadline=min(candidate_deadline,
                               time.monotonic()+remaining_for_packing/max(1,group_count-group_index))
            result=(validation or {}).get("fit_results",{}).get(vehicle_id)
            if result is None:
                result=check_fit(vehicle_lines,vehicle,max_backtracks=5000,deadline=group_deadline)
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
        candidate_diagnostics.append({
            "candidate":candidates_checked,
            "assigned_shipments":sum(len(ids) for ids in assignments.values()),
            "unassigned_shipments":sum(int(solver.Value(unassigned[order_id])) for order_id in order_ids),
            "packed_shipments":sum(len(ids) for ids in feasible_assignments.values()),
            "vehicles_proposed":len(assignments),
            "failed_groups":[{"Vehicle_ID":vehicle_id,"Order_IDs":list(ids),"Reason":reason}
                            for vehicle_id,ids,reason in failed_groups],
        })
        if not failed_groups:
            if assignment_score(assignments)>=assignment_score(best_assignments):
                best_assignments={vehicle_id:list(ids) for vehicle_id,ids in assignments.items()}
                best_placements=list(placements)
                best_fit=dict(fit_results)
                final_validation=validation
            final_objective_optimal=status==cp_model.OPTIMAL and not had_rejections
            final_status="Optimal assignment found" if final_objective_optimal else "Feasible assignment found"
            termination_reason="optimal" if final_objective_optimal else "feasible_within_limit"
            if status==cp_model.OPTIMAL:break
            # Keep the best feasible packing while asking CP-SAT for a strictly
            # better assignment. This lets it improve partial shipment coverage
            # instead of stopping at the first valid greedy-like candidate.
            current_objective=int(round(solver.ObjectiveValue()))
            model.Add(objective_expression<=current_objective-1)
            continue
        had_rejections=True
        failed_vehicle_ids={vehicle_id for vehicle_id,_,_ in failed_groups}
        partial_assignments={vehicle_id:assigned_orders for vehicle_id,assigned_orders in feasible_assignments.items() if vehicle_id not in failed_vehicle_ids}
        partial_placements=[row for row in placements if row.get("Vehicle_ID") not in failed_vehicle_ids]
        partial_fit={vehicle_id:result for vehicle_id,result in feasible_fits.items() if vehicle_id not in failed_vehicle_ids}
        final_validation=validation
        partial_unassigned=[{"Order_ID":order_id,"Reason":"No feasible vehicle assignment was found."}
                            for order_id in order_ids if solver.Value(unassigned[order_id])]
        for _,failed_orders,reason in failed_groups:
            partial_unassigned.extend({"Order_ID":order_id,"Reason":reason} for order_id in failed_orders)
        if assignment_score(partial_assignments)>assignment_score(best_assignments):
            best_assignments={vehicle_id:list(ids) for vehicle_id,ids in partial_assignments.items()}
            best_placements=list(partial_placements)
            best_fit=dict(partial_fit)
            best_order_ids={order_id for ids in best_assignments.values() for order_id in ids}
            best_validation_routes=[row for row in (validation or {}).get("routes",[])
                                    if str(row.get("Order_ID")) in best_order_ids]
            final_validation={"feasible":True,"failed_groups":[],"routes":best_validation_routes}
            final_status="Partial feasible plan; best packable vehicle groups found so far."
            termination_reason="partial_search_limit"
        for vehicle_id,failed_orders,_reason in failed_groups:
            # Forbid this exact set on this vehicle; alternatives remain available.
            failed_set=set(failed_orders)
            signed=sum(assign[(order_id,vehicle_id)] for order_id in failed_orders)-sum(
                assign[(order_id,vehicle_id)] for order_id in order_ids if order_id not in failed_set)
            model.Add(signed<=len(failed_orders)-1)

    # Repair the best validated assignment by trying to insert each omitted
    # shipment into every promising existing or unused vehicle. This is a
    # bounded best-fit-decreasing pass; every accepted move is route-checked and
    # geometrically repacked before it is retained.
    repair_deadline=overall_deadline
    repaired_assignments={key:list(value) for key,value in best_assignments.items()}
    repaired_fit=dict(best_fit)
    repaired_placements=list(best_placements)
    route_rows_by_vehicle={}
    for row in (final_validation or {}).get("routes",[]):
        route_rows_by_vehicle.setdefault(str(row.get("Vehicle_ID")),[]).append(dict(row))
    repaired_ids={order_id for group in repaired_assignments.values() for order_id in group}
    repair_added=[]
    repair_attempts=0
    repair_rejections=[]
    pending=sorted((order_id for order_id in order_ids if order_id not in repaired_ids),
                   key=lambda value:(volume[value],demand[value]),reverse=True)
    for pending_index,order_id in enumerate(pending):
        remaining_orders=len(pending)-pending_index
        remaining_repair=repair_deadline-time.monotonic()
        if remaining_repair<=.05:break
        order_deadline=time.monotonic()+remaining_repair/max(1,remaining_orders)
        direct_deadline=time.monotonic()+(order_deadline-time.monotonic())*.55
        vehicle_candidates=[]
        for vehicle_id in vehicle_ids:
            vehicle=available[available.Vehicle_ID.astype(str)==vehicle_id].iloc[0]
            current=repaired_assignments.get(vehicle_id,[])
            if demand[order_id]+sum(demand[value] for value in current)>_capacity_grams(vehicle.Capacity_kg):continue
            if volume[order_id]+sum(volume[value] for value in current)>_vehicle_volume_cm3(vehicle):continue
            post_weight=(sum(demand[value] for value in current)+demand[order_id])/_capacity_grams(vehicle.Capacity_kg)
            post_volume=(sum(volume[value] for value in current)+volume[order_id])/_vehicle_volume_cm3(vehicle)
            # Best-fit ordering keeps tighter compatible spaces available for
            # shipments that have fewer choices.
            vehicle_candidates.append((max(post_weight,post_volume),post_weight+post_volume,vehicle_id,vehicle,current))
        vehicle_candidates.sort(key=lambda item:(item[0],item[1],item[2]))
        # Do not prune empty/larger vehicles just because a currently loaded
        # vehicle has a tighter volume score. Shape and stop-access constraints
        # can make a nominally worse-fit vehicle the only feasible destination.
        attempt_budget=(direct_deadline-time.monotonic())/max(1,len(vehicle_candidates))
        for _,_,vehicle_id,vehicle,current in vehicle_candidates:
            if direct_deadline-time.monotonic()<=.05:break
            attempt_deadline=min(direct_deadline,time.monotonic()+attempt_budget)
            proposed=current+[order_id]
            attempt_remaining=attempt_deadline-time.monotonic()
            if attempt_remaining<=.05:continue
            repair_attempts+=1
            validation=(candidate_validator({vehicle_id:proposed},attempt_remaining)
                        if candidate_validator is not None else None)
            if validation is not None and not validation.get("feasible",False):
                repair_rejections.append({"Order_ID":order_id,"Vehicle_ID":vehicle_id,
                    "Reason":str((validation.get("failed_groups") or [(None,None,"Route validation failed")])[0][2])})
                continue
            stop_by_order={str(row["Order_ID"]):int(row["Stop"])
                           for row in (validation or {}).get("routes",[])}
            lines=parcel_lines[parcel_lines.Order_ID.isin(proposed)].copy()
            if stop_by_order:lines["Delivery_Stop"]=lines.Order_ID.map(stop_by_order)
            fit=(validation or {}).get("fit_results",{}).get(vehicle_id)
            if fit is None:
                fit=check_fit(lines,vehicle,max_backtracks=10000,deadline=attempt_deadline)
            if not fit["fits"]:
                repair_rejections.append({"Order_ID":order_id,"Vehicle_ID":vehicle_id,
                    "Reason":fit["reason"]})
                continue
            repaired_assignments[vehicle_id]=proposed
            repaired_fit[vehicle_id]=fit
            repaired_placements=[row for row in repaired_placements if str(row.get("Vehicle_ID"))!=vehicle_id]
            repaired_placements.extend(dict(row,Vehicle_ID=vehicle_id) for row in fit["placement_rows"])
            if validation is not None:
                route_rows_by_vehicle[vehicle_id]=[dict(row) for row in validation.get("routes",[])]
            repaired_ids.add(order_id)
            repair_added.append(order_id)
            break

        if order_id in repaired_ids or order_deadline-time.monotonic()<=.1:
            continue

        # One-order ejection chains address the common case where a valid
        # shipment fits a van alone but cannot be inserted beside its current
        # cargo. Replace one incumbent shipment in that van, then place the
        # incumbent into a second van; commit only after both loads validate.
        def evaluate_repair_group(vehicle_id,proposed,deadline):
            nonlocal repair_attempts
            if deadline-time.monotonic()<=.05:return None,None,"Search time expired."
            vehicle=available[available.Vehicle_ID.astype(str)==str(vehicle_id)].iloc[0]
            if sum(demand[value] for value in proposed)>_capacity_grams(vehicle.Capacity_kg):
                return None,None,"Payload limit exceeded."
            if sum(volume[value] for value in proposed)>_vehicle_volume_cm3(vehicle):
                return None,None,"Cargo volume limit exceeded."
            repair_attempts+=1
            validation=(candidate_validator({str(vehicle_id):proposed},deadline-time.monotonic())
                        if candidate_validator is not None else None)
            if validation is not None and not validation.get("feasible",False):
                reason=str((validation.get("failed_groups") or [(None,None,"Route validation failed")])[0][2])
                return None,validation,reason
            stops={str(row["Order_ID"]):int(row["Stop"])
                   for row in (validation or {}).get("routes",[])}
            lines=parcel_lines[parcel_lines.Order_ID.isin(proposed)].copy()
            if stops:lines["Delivery_Stop"]=lines.Order_ID.map(stops)
            fit=(validation or {}).get("fit_results",{}).get(str(vehicle_id))
            if fit is None:
                fit=check_fit(lines,vehicle,max_backtracks=10000,deadline=deadline)
            if not fit["fits"]:return None,validation,fit["reason"]
            return fit,validation,None

        chain_orders=sorted((value for value in pending if value not in repaired_ids),
                            key=lambda value:(volume[value],demand[value]),reverse=True)
        for target_vehicle in vehicle_ids:
            if order_deadline-time.monotonic()<=.1:break
            target_current=repaired_assignments.get(target_vehicle,[])
            if not target_current:continue
            target_truck=available[available.Vehicle_ID.astype(str)==target_vehicle].iloc[0]
            evict_options=sorted(target_current,key=lambda value:(volume[value],demand[value]),reverse=True)[:2]
            for evicted in evict_options:
                if order_deadline-time.monotonic()<=.1:break
                target_group=[value for value in target_current if value!=evicted]+[order_id]
                chain_deadline=order_deadline
                target_deadline=time.monotonic()+(chain_deadline-time.monotonic())*.5
                target_fit,target_validation,target_reason=evaluate_repair_group(
                    target_vehicle,target_group,target_deadline)
                if target_fit is None:
                    repair_rejections.append({"Order_ID":order_id,"Vehicle_ID":target_vehicle,
                        "Reason":"Swap target: {}".format(target_reason)})
                    continue
                destinations=[]
                for destination_id in vehicle_ids:
                    if destination_id==target_vehicle:continue
                    destination=available[available.Vehicle_ID.astype(str)==destination_id].iloc[0]
                    current_destination=repaired_assignments.get(destination_id,[])
                    proposed_destination=current_destination+[evicted]
                    if sum(demand[value] for value in proposed_destination)>_capacity_grams(destination.Capacity_kg):continue
                    if sum(volume[value] for value in proposed_destination)>_vehicle_volume_cm3(destination):continue
                    post_weight=sum(demand[value] for value in proposed_destination)/_capacity_grams(destination.Capacity_kg)
                    post_volume=sum(volume[value] for value in proposed_destination)/_vehicle_volume_cm3(destination)
                    destinations.append((max(post_weight,post_volume),post_weight+post_volume,
                                         destination_id,destination,proposed_destination))
                destinations.sort(key=lambda item:(item[0],item[1],item[2]))
                for _,_,destination_id,destination,proposed_destination in destinations[:3]:
                    if order_deadline-time.monotonic()<=.1:break
                    destination_fit,destination_validation,destination_reason=evaluate_repair_group(
                        destination_id,proposed_destination,order_deadline)
                    if destination_fit is None:
                        repair_rejections.append({"Order_ID":order_id,"Vehicle_ID":destination_id,
                            "Reason":"Swap destination: {}".format(destination_reason)})
                        continue
                    repaired_assignments[target_vehicle]=target_group
                    repaired_assignments[destination_id]=proposed_destination
                    repaired_fit[target_vehicle]=target_fit
                    repaired_fit[destination_id]=destination_fit
                    repaired_placements=[row for row in repaired_placements
                        if str(row.get("Vehicle_ID")) not in (target_vehicle,destination_id)]
                    repaired_placements.extend(dict(row,Vehicle_ID=target_vehicle)
                                               for row in target_fit["placement_rows"])
                    repaired_placements.extend(dict(row,Vehicle_ID=destination_id)
                                               for row in destination_fit["placement_rows"])
                    for vid,validation_item in ((target_vehicle,target_validation),
                                                (destination_id,destination_validation)):
                        if validation_item is not None:
                            route_rows_by_vehicle[vid]=[dict(row) for row in validation_item.get("routes",[])]
                    repaired_ids.add(order_id)
                    repair_added.append(order_id)
                    break
                if order_id in repaired_ids:break
            if order_id in repaired_ids:break

    if len(repaired_ids)>len({order_id for group in best_assignments.values() for order_id in group}):
        best_assignments=repaired_assignments
        best_fit=repaired_fit
        best_placements=repaired_placements
        if candidate_validator is not None:
            final_validation={"feasible":True,"failed_groups":[],
                              "routes":[row for vehicle_id in vehicle_ids
                                        for row in route_rows_by_vehicle.get(vehicle_id,[])]}
        final_status="Feasible assignment improved by bounded repair" if len(repaired_ids)==len(order_ids) else "Partial assignment improved by bounded repair"
        termination_reason="repair_complete" if len(repaired_ids)==len(order_ids) else "repair_search_limit"

    # If no fully packable candidate was found, keep individually feasible shipments
    # out of route planning rather than presenting a known-bad load.
    unassigned_rows=[]
    if best_assignments:
        assigned_set={order_id for group in best_assignments.values() for order_id in group}
        for order_id in order_ids:
            if order_id in assigned_set:continue
            reason=_unassigned_reason(order_id,parcel_lines,available,demand,volume,check_geometry=False)
            if order_id in failures_by_order:
                reason=("No validated vehicle assignment was found within the search budget. "
                        "Last candidate issue: {}".format(failures_by_order[order_id]))
            unassigned_rows.append({"Order_ID":order_id,"Reason":reason})
    elif partial_assignments:
        best_assignments=partial_assignments
        best_placements=partial_placements
        best_fit=partial_fit
        unassigned_rows=partial_unassigned
        final_status="Partial feasible plan; bounded search did not fit every shipment."
        termination_reason="partial_search_limit"
    else:
        for order_id in order_ids:
            if order_id in failures_by_order:
                reason=("No validated vehicle assignment was found within the search budget. "
                        "Last candidate issue: {}".format(failures_by_order[order_id]))
            else:
                reason=_unassigned_reason(order_id,parcel_lines,available,demand,volume,check_geometry=False)
            unassigned_rows.append({"Order_ID":order_id,"Reason":reason})

    if best_assignments and unassigned_rows and not final_objective_optimal:
        final_status="Partial feasible plan; best packable coverage found before search ended."
    elapsed_seconds=time.monotonic()-started
    return {"assignments":best_assignments,"placements":best_placements,"fit_results":{
        vehicle_id:{key:value for key,value in result.items() if key not in ("placed","placement_rows")}
        for vehicle_id,result in best_fit.items()},"unassigned":unassigned_rows,
        "vehicles_used":len(best_assignments),"method":"Google OR-Tools CP-SAT + orientation-aware geometric packing",
        "status":final_status,"termination_reason":termination_reason,
        "solver_status":solver_status,"elapsed_seconds":round(elapsed_seconds,3),
        "candidate_diagnostics":candidate_diagnostics,
        "repair_shipments_added":len(repair_added),
        "repair_attempts":repair_attempts,
        "repair_rejections":repair_rejections,
        "model_validation_error":model_validation_error if solver_status=="MODEL_INVALID" else None,
        "packing_search_bounded":True,"candidates_checked":candidates_checked,
        "vehicle_count_optimal":final_objective_optimal,
        "objective_optimal":final_objective_optimal,"candidate_validation":final_validation}
