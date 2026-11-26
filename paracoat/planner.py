"""Vehicle routing and capacity planning using OR-Tools when installed."""
from collections import defaultdict


def _fallback_plan(orders, fleet, distances, durations, depot_index=0, objective="Distance"):
    """Greedy fallback for local evaluation if OR-Tools is not installed."""
    available = fleet[fleet.Available_Today.astype(str).str.lower().isin(["yes","true","1","available"])].reset_index(drop=True)
    remaining = {i:float(r.Capacity_kg) for i,r in available.iterrows()}
    routes = {i:[] for i in range(len(available))}
    unassigned=[]
    demand_col="Demand_kg"
    pending=orders.sort_values(demand_col,ascending=False).reset_index(drop=True)
    for _,order in pending.iterrows():
        demand=float(order[demand_col]); oi=int(order._matrix_index)
        candidates=[i for i in routes if remaining[i]>=demand]
        if not candidates:
            unassigned.append({"Order_ID":order.Order_ID,"Reason":"Available fleet capacity is insufficient."}); continue
        best=None
        for v in candidates:
            prev=routes[v][-1]["_matrix_index"] if routes[v] else depot_index
            # Include the return leg so the heuristic prefers consolidating nearby stops.
            cost=distances[prev][oi]+distances[oi][depot_index]-distances[prev][depot_index]
            if best is None or cost<best[0]: best=(cost,v)
        v=best[1]; routes[v].append(order.to_dict()); remaining[v]-=demand
    return _format(routes,available,remaining,distances,durations,unassigned,objective)


def _format(routes, fleet, remaining, distances, durations, unassigned, objective):
    output=[]; total_km=total_min=0.0
    for vi, stops in routes.items():
        if not stops: continue
        vehicle=fleet.iloc[vi]; prev=0; km=minutes=load=0.0
        ordered=list(stops)
        if objective=="Distance":
            remaining_stops=ordered[:]; ordered=[]; current=0
            while remaining_stops:
                nxt=min(remaining_stops,key=lambda s:distances[current][int(s["_matrix_index"])])
                ordered.append(nxt); remaining_stops.remove(nxt); current=int(nxt["_matrix_index"])
        for seq, stop in enumerate(ordered,1):
            idx=int(stop["_matrix_index"]); km+=distances[prev][idx]; minutes+=durations[prev][idx]
            service=float(stop.get("Service_Minutes",0) or 0); minutes+=service; load+=float(stop["Demand_kg"])
            output.append({"Vehicle_ID":vehicle.Vehicle_ID,"Stop":seq,"Order_ID":stop["Order_ID"],"Customer_Name":stop["Customer_Name"],"Address":stop["Address"],"Demand_kg":stop["Demand_kg"],"Cumulative_Estimated_Minutes":round(minutes,1),"Leg_km":round(distances[prev][idx],2),"Latitude":stop["Latitude"],"Longitude":stop["Longitude"],"_matrix_index":idx}); prev=idx
        km+=distances[prev][0]; minutes+=durations[prev][0]; total_km+=km; total_min+=minutes
    return {"routes":output,"unassigned":unassigned,"total_km":total_km,"total_minutes":total_min,"vehicles_used":len(set(r["Vehicle_ID"] for r in output)),"status":"Feasible heuristic plan" if not unassigned else "Partial plan — orders unassigned","method":"Greedy capacity assignment (fallback)","objective":objective}


def optimize(orders, fleet, distances, durations, objective="Distance", depot_index=0, time_limit_seconds=8, departure_time="08:00"):
    available=fleet[fleet.Available_Today.astype(str).str.lower().isin(["yes","true","1","available"])].reset_index(drop=True)
    if available.empty: return {"routes":[],"unassigned":[{"Order_ID":r.Order_ID,"Reason":"No available vehicles."} for _,r in orders.iterrows()],"total_km":0,"total_minutes":0,"vehicles_used":0,"status":"No available vehicles","method":"None","objective":objective}
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError:
        has_windows=("Window_Start" in orders.columns and orders.Window_Start.fillna("").astype(str).str.strip().ne("").any())
        if has_windows:
            return {"routes":[],"unassigned":[{"Order_ID":str(row.Order_ID),"Reason":"Mandatory delivery windows require the OR-Tools solver, which is not installed."} for _,row in orders.iterrows()],"total_km":0,"total_minutes":0,"vehicles_used":0,"status":"No feasible plan — install OR-Tools to plan time windows","method":"No solver available","objective":objective}
        return _fallback_plan(orders,fleet,distances,durations,depot_index,objective)
    n=len(orders)+1; vehicles=len(available)
    manager=pywrapcp.RoutingIndexManager(n,vehicles,[0]*vehicles,[0]*vehicles)
    routing=pywrapcp.RoutingModel(manager)
    dist_cb=routing.RegisterTransitCallback(lambda a,b:int(distances[manager.IndexToNode(a)][manager.IndexToNode(b)]*1000))
    def time_cb(a,b):
        origin=manager.IndexToNode(a); destination=manager.IndexToNode(b)
        service=0.0 if origin==0 else float(orders.iloc[origin-1].get("Service_Minutes",0) or 0)
        return int((durations[origin][destination]+service)*60)
    time_cb_index=routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(dist_cb if objective=="Distance" else time_cb_index)
    demands=[0]+[int(round(float(v))) for v in orders.Demand_kg]
    def demand_cb(index): return demands[manager.IndexToNode(index)]
    demand_idx=routing.RegisterUnaryTransitCallback(demand_cb)
    capacities=[int(round(float(v))) for v in available.Capacity_kg]
    routing.AddDimensionWithVehicleCapacity(demand_idx,0,capacities,True,"Capacity")
    try:
        departure_hours,departure_minutes=map(int,str(departure_time).split(":")[:2])
        departure_seconds=(departure_hours*60+departure_minutes)*60
    except Exception:
        departure_seconds=8*60*60
    routing.AddDimension(time_cb_index,24*3600,24*3600,False,"Time")
    time_dim=routing.GetDimensionOrDie("Time")
    for v in range(vehicles): time_dim.CumulVar(routing.Start(v)).SetRange(departure_seconds,departure_seconds)
    for i,row in orders.reset_index(drop=True).iterrows():
        idx=manager.NodeToIndex(i+1)
        start,end=row.get("Window_Start"),row.get("Window_End")
        if isinstance(start,str) and start.strip() and isinstance(end,str) and end.strip():
            try:
                def mins(x):
                    h,m=map(int,x.split(":")[:2]); return (h*60+m)*60
                time_dim.CumulVar(idx).SetRange(mins(start),mins(end))
            except Exception: pass
        routing.AddDisjunction([idx],10**8)
    params=pywrapcp.DefaultRoutingSearchParameters(); params.first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC; params.local_search_metaheuristic=routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH; params.time_limit.FromSeconds(int(time_limit_seconds))
    solution=routing.SolveWithParameters(params)
    if solution is None:
        return {"routes":[],"unassigned":[{"Order_ID":str(row.Order_ID),"Reason":"No feasible route found under the configured constraints."} for _,row in orders.iterrows()],"total_km":0,"total_minutes":0,"vehicles_used":0,"status":"No feasible plan","method":"Google OR-Tools (time-limited search)","objective":objective}
    routes=[]; unassigned=[]; total_km=total_minutes=0.0
    for v in range(vehicles):
        idx=routing.Start(v); seq=0; km=minutes=load=0.0; prev_node=0
        while not routing.IsEnd(idx):
            nxt=solution.Value(routing.NextVar(idx)); node=manager.IndexToNode(nxt)
            if node>0:
                row=orders.iloc[node-1]; seq+=1; leg_km=distances[prev_node][node]; leg_min=durations[prev_node][node]
                minutes+=leg_min+float(row.get("Service_Minutes",0) or 0); km+=leg_km; load+=float(row.Demand_kg)
                routes.append({"Vehicle_ID":available.iloc[v].Vehicle_ID,"Stop":seq,"Order_ID":row.Order_ID,"Customer_Name":row.Customer_Name,"Address":row.Address,"Demand_kg":row.Demand_kg,"Cumulative_Estimated_Minutes":round(minutes,1),"Leg_km":round(leg_km,2),"Latitude":row.Latitude,"Longitude":row.Longitude,"_matrix_index":node})
                prev_node=node
            else:
                if not routing.IsEnd(nxt): unassigned.append(manager.IndexToNode(nxt))
            idx=nxt
        if seq:
            km+=distances[prev_node][0]; minutes+=durations[prev_node][0]; total_km+=km; total_minutes+=minutes
    # Use dropped-node penalty test to surface any unassigned orders.
    for node in range(1,n):
        if solution.Value(routing.NextVar(manager.NodeToIndex(node)))==manager.NodeToIndex(node):
            row=orders.iloc[node-1]; unassigned.append({"Order_ID":row.Order_ID,"Reason":"Could not fit available capacity or delivery constraints."})
    return {"routes":routes,"unassigned":unassigned,"total_km":total_km,"total_minutes":total_minutes,"vehicles_used":len(set(r["Vehicle_ID"] for r in routes)),"status":"Feasible plan" if not unassigned else "Partial plan — orders unassigned","method":"Google OR-Tools (time-limited search)","objective":objective}
