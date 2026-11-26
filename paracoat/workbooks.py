"""Workbook template and dispatch plan export helpers."""
import io
from datetime import datetime

import pandas as pd

from paracoat.packing import check_fit
from paracoat.sample_data import (sample_depot, sample_fleet, sample_orders, sample_parcels,
    extended_demo_orders, extended_demo_parcels, extended_demo_fleet)


def workbook_template():
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sample_orders().to_excel(writer, index=False, sheet_name="Orders")
        sample_parcels().to_excel(writer, index=False, sheet_name="Parcels")
        sample_fleet().to_excel(writer, index=False, sheet_name="Fleet")
        sample_depot().to_excel(writer, index=False, sheet_name="Depot")
    return output.getvalue()


def extended_demo_workbook():
    """Workbook containing the larger artificial multi-vehicle scenario."""
    output=io.BytesIO()
    with pd.ExcelWriter(output,engine="openpyxl") as writer:
        extended_demo_orders().to_excel(writer,index=False,sheet_name="Orders")
        extended_demo_parcels().drop(columns=["SKU"],errors="ignore").to_excel(writer,index=False,sheet_name="Parcels")
        extended_demo_fleet().to_excel(writer,index=False,sheet_name="Fleet")
        sample_depot().to_excel(writer,index=False,sheet_name="Depot")
    return output.getvalue()


def load_planning_workbook(upload):
    book = pd.ExcelFile(upload)
    required = ["Orders", "Parcels", "Fleet", "Depot"]
    missing = [sheet for sheet in required if sheet not in book.sheet_names]
    if missing:
        raise ValueError("Workbook is missing required sheets: " + ", ".join(missing))
    return {sheet: pd.read_excel(upload, sheet_name=sheet) for sheet in required}


def export_plan(plan, parcels, fleet, plan_id, plan_date, edits=0, approver=""):
    output = io.BytesIO()
    routes = pd.DataFrame(plan["routes"])
    unassigned = pd.DataFrame(plan["unassigned"])
    if not routes.empty:
        summary = routes.groupby("Vehicle_ID").agg(Stops=("Order_ID", "count"), Assigned_Weight_kg=("Demand_kg", "sum"), Route_km=("Leg_km", "sum")).reset_index()
        if plan.get("distances"):
            return_legs=[]
            for vehicle_id, group in routes.groupby("Vehicle_ID"):
                last=group.sort_values("Stop").iloc[-1]
                return_legs.append({"Vehicle_ID":vehicle_id,"Return_km":float(plan["distances"][int(last["_matrix_index"])][0])})
            summary=summary.merge(pd.DataFrame(return_legs),on="Vehicle_ID",how="left")
            summary["Route_km"]=(summary["Route_km"]+summary["Return_km"].fillna(0)).round(2)
            summary=summary.drop(columns=["Return_km"])
        capacity = fleet.set_index("Vehicle_ID")["Capacity_kg"]
        summary["Capacity_kg"] = summary.Vehicle_ID.map(capacity)
        summary["Utilization_pct"] = (100 * summary.Assigned_Weight_kg / summary.Capacity_kg).round(1)
        fit_results = []
        for vehicle_id in summary.Vehicle_ID:
            ids = routes.loc[routes.Vehicle_ID == vehicle_id, "Order_ID"].astype(str).tolist()
            truck = fleet[fleet.Vehicle_ID.astype(str) == str(vehicle_id)].iloc[0]
            fit = check_fit(parcels[parcels.Order_ID.astype(str).isin(ids)], truck)
            fit_results.append({"Vehicle_ID": vehicle_id, "Geometric_Fit": "Pass" if fit["fits"] else "Review", "Fit_Detail": fit["reason"], "Space_Utilization_pct": round(fit.get("volume_used_pct", 0), 1)})
        summary = summary.merge(pd.DataFrame(fit_results), on="Vehicle_ID", how="left")
    else:
        summary = pd.DataFrame(columns=["Vehicle_ID", "Stops", "Assigned_Weight_kg", "Route_km", "Capacity_kg", "Utilization_pct", "Geometric_Fit", "Fit_Detail", "Space_Utilization_pct"])
    audit = pd.DataFrame([{"Plan_ID": plan_id, "Plan_Date": str(plan_date), "Created_At": datetime.now().isoformat(timespec="seconds"), "Approval_Status": "Approved" if approver else "Draft", "Approved_By": approver or "", "Optimization_Method": plan["method"], "Solver_Status": plan["status"], "Objective": plan["objective"], "Manual_Edits": edits, "Limitations": "Estimates only; geometric fit is not a load-safety certification."}])
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        audit.to_excel(writer, index=False, sheet_name="Plan Summary")
        summary.to_excel(writer, index=False, sheet_name="Vehicle Summary")
        routes.drop(columns=["_matrix_index"],errors="ignore").to_excel(writer, index=False, sheet_name="Vehicle Routes")
        unassigned.to_excel(writer, index=False, sheet_name="Exceptions")
        parcels.to_excel(writer, index=False, sheet_name="Parcel Inputs")
    return output.getvalue()
