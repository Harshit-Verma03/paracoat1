"""Repeatable synthetic benchmarks for fleet assignment and parcel packing.

Run from the repository root with:
    python scripts/benchmark_optimizer.py --sizes 24 50 100

The scenarios are synthetic and are intended for relative performance and
regression checks, not as operational load plans.
"""
import argparse
import json
import math
import platform
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paracoat.loading_optimizer import optimize_fleet_loading
from paracoat.packing import check_fit
from paracoat.routing import build_matrix
from paracoat.sample_data import extended_demo_orders, extended_demo_parcels, extended_demo_fleet


def _scaled_scenario(size):
    source_orders = extended_demo_orders()
    source_parcels = extended_demo_parcels()
    order_frames=[]
    parcel_frames=[]
    for index in range(size):
        source=source_orders.iloc[index % len(source_orders)].copy()
        batch=index // len(source_orders)
        source_id=str(source.Order_ID)
        order_id="B{:02d}-{}".format(batch+1,source_id)
        source["Order_ID"]=order_id
        source["Customer_Name"]="Benchmark {}".format(order_id)
        source["Latitude"]=float(source.Latitude)+(batch % 7)*0.003
        source["Longitude"]=float(source.Longitude)+(batch % 7)*0.003
        order_frames.append(source)
        lines=source_parcels[source_parcels.Order_ID.astype(str)==source_id].copy()
        lines["Order_ID"]=order_id
        parcel_frames.append(lines)
    orders=pd.DataFrame(order_frames).reset_index(drop=True)
    orders["_matrix_index"]=range(1,len(orders)+1)
    parcels=pd.concat(parcel_frames,ignore_index=True)

    base=extended_demo_fleet()
    # Keep enough fleet payload for the generated freight, with at least the
    # original eight vehicle types represented once.
    freight_kg=float((parcels.Weight_kg*parcels.Quantity).sum())
    required=max(len(base),int(math.ceil(freight_kg/450.0*1.20)))
    vehicles=[]
    for index in range(required):
        row=base.iloc[index % len(base)].copy()
        row["Vehicle_ID"]="BENCH-{:02d}".format(index+1)
        vehicles.append(row)
    fleet=pd.DataFrame(vehicles).reset_index(drop=True)
    return orders,parcels,fleet


def _known_feasible_packing_cases():
    """Layouts with a direct grid proof that every unit fits the container."""
    cases=[]
    for name,vehicle_dims,parcel_dims,quantity in [
        ("cube_grid_8",(100,100,100),(50,50,50),8),
        ("layer_grid_12",(120,100,40),(40,50,40),6),
        ("mixed_layers_8",(120,100,60),(60,50,30),8),
        ("wide_grid_24",(120,100,60),(30,25,30),24),
        ("long_grid_12",(180,80,60),(60,40,30),12),
        ("compact_grid_18",(120,90,60),(40,30,20),18),
        ("upright_grid_16",(160,80,80),(40,40,20),16),
        ("flat_grid_16",(200,100,30),(50,25,30),20),
        ("deep_grid_12",(120,120,60),(30,40,30),12),
        ("near_full_grid_31",(120,100,60),(30,25,30),31),
        ("small_grid_30",(120,100,60),(20,20,20),30),
        ("mixed_depth_grid_10",(150,100,80),(50,50,40),10),
        ("mixed_orientation_grid_12",(120,100,60),(40,50,30),12),
    ]:
        length,width,height=vehicle_dims
        parcel_length,parcel_width,parcel_height=parcel_dims
        grid_capacity=(length//parcel_length)*(width//parcel_width)*(height//parcel_height)
        quantity=min(quantity,int(grid_capacity))
        parcels=pd.DataFrame([{
            "Order_ID":"KNOWN-{}".format(name),"Parcel_ID":"UNIT-{}".format(index+1),
            "Quantity":1,"Length_cm":parcel_length,"Width_cm":parcel_width,
            "Height_cm":parcel_height,"Weight_kg":1,"Stackable":"Yes",
            "Max_Stack_Weight_kg":1000,"Rotation":"Upright only",
        } for index in range(quantity)])
        vehicle=pd.Series({"Interior_Length_cm":length,"Interior_Width_cm":width,
                           "Interior_Height_cm":height})
        start=time.perf_counter()
        result=check_fit(parcels,vehicle)
        cases.append({"case":name,"units":quantity,"grid_capacity_proof":int(grid_capacity),
                      "fits":bool(result["fits"]),"placed_units":len(result["placement_rows"]),
                      "backtracks":int(result.get("backtracks",0)),"seconds":round(time.perf_counter()-start,4),
                      "reason":result["reason"]})
    rng=random.Random(20261128)
    for trial in range(1,31):
        rows=[]
        parcel_index=1
        for band in range(10):
            remaining=10
            widths=[]
            while remaining:
                options=[value for value in (2,3,4,5)
                         if value<=remaining and (remaining-value==0 or remaining-value>=2)]
                choice=rng.choice(options)
                widths.append(choice)
                remaining-=choice
            for width_units in widths:
                rows.append({"Order_ID":"TILING-{:02d}".format(trial),
                    "Parcel_ID":"BOX-{:03d}".format(parcel_index),"Quantity":1,
                    "Length_cm":width_units*10,"Width_cm":10,"Height_cm":20,
                    "Weight_kg":1,"Stackable":"No","Max_Stack_Weight_kg":0,
                    "Rotation":"No height rotation"})
                parcel_index+=1
        parcels=pd.DataFrame(rows)
        vehicle=pd.Series({"Interior_Length_cm":100,"Interior_Width_cm":100,
                           "Interior_Height_cm":20})
        start=time.perf_counter()
        result=check_fit(parcels,vehicle)
        cases.append({"case":"random_floor_tiling_{:02d}".format(trial),
                      "units":len(parcels),"grid_capacity_proof":len(parcels),
                      "fits":bool(result["fits"]),"placed_units":len(result["placement_rows"]),
                      "backtracks":int(result.get("backtracks",0)),"seconds":round(time.perf_counter()-start,4),
                      "reason":result["reason"]})
    stack_rng=random.Random(20261129)
    for trial in range(1,9):
        rows=[]
        parcel_index=1
        for column in range(4):
            remaining=12
            while remaining:
                options=[value for value in (2,3,4,6)
                         if value<=remaining and (remaining-value==0 or remaining-value>=2)]
                height_units=stack_rng.choice(options)
                remaining-=height_units
                rows.append({"Order_ID":"STACK-{:02d}".format(trial),
                    "Parcel_ID":"BOX-{:03d}".format(parcel_index),"Quantity":1,
                    "Length_cm":40,"Width_cm":40,"Height_cm":height_units*10,
                    "Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":100,
                    "Rotation":"Upright only"})
                parcel_index+=1
        parcels=pd.DataFrame(rows)
        vehicle=pd.Series({"Interior_Length_cm":80,"Interior_Width_cm":80,
                           "Interior_Height_cm":120})
        start=time.perf_counter()
        result=check_fit(parcels,vehicle)
        cases.append({"case":"known_vertical_stacks_{:02d}".format(trial),
                      "units":len(parcels),"grid_capacity_proof":len(parcels),
                      "fits":bool(result["fits"]),"placed_units":len(result["placement_rows"]),
                      "backtracks":int(result.get("backtracks",0)),"seconds":round(time.perf_counter()-start,4),
                      "reason":result["reason"]})
    return cases


def _benchmark_size(size,time_limit,candidate_iterations):
    orders,parcels,fleet=_scaled_scenario(size)
    points=[(28.5355,77.2732)]
    points.extend((float(row.Latitude),float(row.Longitude)) for _,row in orders.iterrows())
    distances,durations,_=build_matrix(points,"Straight-line estimate")
    start=time.perf_counter()
    plan=optimize_fleet_loading(orders,parcels,fleet,distances,durations,
        objective="Distance",time_limit_seconds=time_limit,
        max_candidate_iterations=candidate_iterations)
    wall_seconds=time.perf_counter()-start
    assigned={str(order_id) for group in plan["assignments"].values() for order_id in group}
    return {
        "shipments":int(len(orders)),"parcel_lines":int(len(parcels)),
        "parcel_units":int(parcels.Quantity.sum()),"fleet_size":int(len(fleet)),
        "assigned_shipments":len(assigned),"unassigned_shipments":len(plan["unassigned"]),
        "vehicles_used":int(plan["vehicles_used"]),"assignment_candidates_checked":int(plan["candidates_checked"]),
        "wall_seconds":round(wall_seconds,3),"optimizer_seconds":plan.get("elapsed_seconds"),
        "solver_status":plan.get("solver_status"),"termination_reason":plan.get("termination_reason"),
        "model_validation_error":plan.get("model_validation_error"),
        "optimality_proven":bool(plan["objective_optimal"]),
        "time_limit_seconds":float(time_limit),
        "unassigned_reasons":sorted({str(row.get("Reason","")) for row in plan["unassigned"]}),
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes",nargs="+",type=int,default=[24,50,100])
    parser.add_argument("--time-limit-seconds",type=float,default=8.0)
    parser.add_argument("--candidate-iterations",type=int,default=6)
    parser.add_argument("--repeats",type=int,default=1)
    parser.add_argument("--output",type=Path,default=ROOT/"benchmarks"/"optimizer_benchmark.json")
    args=parser.parse_args()
    results=[]
    for size in args.sizes:
        for repetition in range(1,max(1,args.repeats)+1):
            result=_benchmark_size(size,args.time_limit_seconds,args.candidate_iterations)
            result["repetition"]=repetition
            results.append(result)
    report={"generated_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_revision":subprocess.run(["git","rev-parse","--short","HEAD"],cwd=ROOT,
                capture_output=True,text=True,check=False).stdout.strip() or "unknown",
            "working_tree_dirty":bool(subprocess.run(["git","status","--porcelain"],cwd=ROOT,
                capture_output=True,text=True,check=False).stdout.strip()),
            "python":platform.python_version(),"platform":platform.platform(),
            "scenario_note":"Scaled synthetic Delhi demo data; benchmark results are machine-specific and not operational claims.",
            "packing_search_cases":_known_feasible_packing_cases(),"fleet_benchmarks":results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    packing_cases=report["packing_search_cases"]
    packing_backtracks=[case["backtracks"] for case in packing_cases]
    print(json.dumps({"generated_at_utc":report["generated_at_utc"],
        "git_revision":report["git_revision"],"working_tree_dirty":report["working_tree_dirty"],
        "packing_cases":len(packing_cases),
        "packing_cases_passed":sum(1 for case in packing_cases if case["fits"]),
        "packing_cases_with_backtracking":sum(1 for value in packing_backtracks if value>0),
        "packing_total_backtracks":sum(packing_backtracks),
        "packing_slowest_case_seconds":max(case["seconds"] for case in packing_cases),
        "packing_known_feasible_misses":[case["case"] for case in packing_cases if not case["fits"]],
        "fleet_benchmarks":results},indent=2))
    print("\nSaved benchmark report to {}".format(args.output))


if __name__=="__main__":
    main()
