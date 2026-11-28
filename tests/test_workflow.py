import io
import json
import os
import sqlite3
import tempfile
import unittest

import pandas as pd

from paracoat.dispatch import (build_dispatch, reassign_order, change_parcel_orientation,
    move_parcel_position, move_stop, _two_opt_distance_order)
from paracoat.packing import check_fit, orientation_options
from paracoat.loading_optimizer import optimize_fleet_loading
from paracoat.planner import optimize, _fallback_plan
from paracoat.routing import build_matrix
from paracoat.sample_data import (sample_depot, sample_fleet, sample_orders, sample_parcels,
                                  extended_demo_orders, extended_demo_parcels, extended_demo_fleet)
from paracoat.validation import validate_inputs
from paracoat.workbooks import workbook_template, extended_demo_workbook, export_plan, load_planning_workbook
from paracoat.repository import (initialize, list_shipments, get_shipment_lines, approve_plan,
                                 get_products, save_product, archive_product, save_shipment,
                                 get_plan_history, get_plan_detail, update_shipment,
                                 cancel_shipment, update_shipment_statuses, save_vehicle, get_vehicles)


def prepared():
    orders, parcels, fleet, depot, errors, _ = validate_inputs(
        sample_orders(), sample_parcels(), sample_fleet(), sample_depot()
    )
    if errors:
        raise AssertionError(errors)
    points = [(float(depot.iloc[0].Latitude), float(depot.iloc[0].Longitude))]
    points += [(float(r.Latitude), float(r.Longitude)) for _, r in orders.iterrows()]
    distances, durations, _ = build_matrix(points, "Straight-line estimate")
    return orders, parcels, fleet, depot, distances, durations


class WorkflowTests(unittest.TestCase):
    def test_extended_demo_dataset_validates_and_workbook_roundtrips(self):
        orders,parcels,fleet,depot,errors,_=validate_inputs(
            extended_demo_orders(),extended_demo_parcels(),extended_demo_fleet(),sample_depot())
        self.assertFalse(errors)
        self.assertEqual(len(orders),24)
        self.assertEqual(int(parcels.Quantity.sum()),147)
        self.assertEqual(len(fleet),8)
        self.assertGreater(float((parcels.Weight_kg*parcels.Quantity).sum()),3000)
        workbook=load_planning_workbook(io.BytesIO(extended_demo_workbook()))
        self.assertEqual(len(workbook["Orders"]),24)
        self.assertEqual(len(workbook["Fleet"]),8)
        self.assertEqual(int(workbook["Parcels"].Quantity.sum()),147)

    def test_extended_demo_load_is_assigned_across_multiple_vehicles(self):
        orders,parcels,fleet,depot,errors,_=validate_inputs(
            extended_demo_orders(),extended_demo_parcels(),extended_demo_fleet(),sample_depot())
        self.assertFalse(errors)
        points=[(float(depot.iloc[0].Latitude),float(depot.iloc[0].Longitude))]
        points.extend((float(row.Latitude),float(row.Longitude)) for _,row in orders.iterrows())
        distances,durations,_=build_matrix(points,"Straight-line estimate")
        orders=orders.copy().reset_index(drop=True)
        orders["_matrix_index"]=range(1,len(orders)+1)
        result=optimize_fleet_loading(orders,parcels,fleet,distances,durations,time_limit_seconds=8)
        self.assertFalse(result["unassigned"])
        self.assertGreaterEqual(result["vehicles_used"],2)
        self.assertEqual(len(result["placements"]),int(parcels.Quantity.sum()))

    def test_unassigned_reason_explains_candidate_failure_without_claiming_impossibility(self):
        orders=pd.DataFrame([{"Order_ID":"S1","Demand_kg":1,"_matrix_index":1}])
        parcels=pd.DataFrame([{"Order_ID":"S1","Parcel_ID":"P1","Quantity":1,
            "Length_cm":10,"Width_cm":10,"Height_cm":10,"Weight_kg":1,
            "Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"}])
        fleet=pd.DataFrame([{"Vehicle_ID":"V1","Available_Today":"Yes","Capacity_kg":10,
            "Interior_Length_cm":20,"Interior_Width_cm":20,"Interior_Height_cm":20}])
        def reject_candidate(assignments,remaining_seconds):
            return {"feasible":False,"failed_groups":[("V1",["S1"],"Test route validation failure")],"routes":[]}
        result=optimize_fleet_loading(orders,parcels,fleet,[[0,1],[1,0]],[[0,1],[1,0]],
            time_limit_seconds=2,candidate_validator=reject_candidate)
        self.assertEqual(len(result["unassigned"]),1)
        reason=result["unassigned"][0]["Reason"]
        self.assertIn("within the search budget",reason)
        self.assertIn("Last candidate issue: Test route validation failure",reason)

    def test_shipment_can_be_edited_and_soft_cancelled(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"shipment-edit.db")
            initialize(path)
            shipment_id=str(list_shipments(path).iloc[0].Shipment_ID)
            lines=get_shipment_lines(path,[shipment_id])
            original=lines.iloc[0]
            data={"customer_name":"Updated customer","address":"Updated address","latitude":19.2,"longitude":72.8,
                  "required_date":"2026-11-27","priority":"High","window_start":"09:00","window_end":"12:00",
                  "service_minutes":20,"notes":"Updated instructions"}
            update_shipment(path,shipment_id,data,[(str(original.SKU),int(original.Quantity)+1)])
            changed=list_shipments(path)
            row=changed[changed.Shipment_ID==shipment_id].iloc[0]
            self.assertEqual(row.Customer_Name,"Updated customer")
            self.assertEqual(int(get_shipment_lines(path,[shipment_id]).iloc[0].Quantity),int(original.Quantity)+1)
            cancel_shipment(path,shipment_id)
            self.assertEqual(list_shipments(path).set_index("Shipment_ID").loc[shipment_id,"Status"],"Cancelled")

    def test_shipment_edit_cancel_and_status_board_respect_plan_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"shipment-lock.db")
            initialize(path)
            shipment_id=str(list_shipments(path).iloc[0].Shipment_ID)
            line=get_shipment_lines(path,[shipment_id]).iloc[0]
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?)",("PLAN-LOCK","now","today","Draft","{}","{}"))
            approve_plan(path,"PLAN-LOCK","Dispatcher",pd.DataFrame([{"Order_ID":shipment_id,"Parcel_ID":line.SKU,"Quantity":1}]))
            data={"customer_name":"Changed","address":"Changed","required_date":"2026-11-27"}
            with self.assertRaisesRegex(ValueError,"approved plan allocation"):
                update_shipment(path,shipment_id,data,[(str(line.SKU),1)])
            with self.assertRaisesRegex(ValueError,"approved plan allocation"):
                cancel_shipment(path,shipment_id)
            with self.assertRaisesRegex(ValueError,"lifecycle statuses"):
                update_shipment_statuses(path,[(shipment_id,"Planned")])

    def test_plan_history_retrieves_saved_plan_inputs_and_events(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"history.db")
            initialize(path)
            plan={"method":"OR-Tools","objective":"Distance","status":"Feasible plan","vehicles_used":1,
                "total_km":12.5,"total_minutes":40,"approver":"Dispatcher","manual_edits":2,
                "routes":[{"Vehicle_ID":"V1","Stop":1,"Order_ID":"S1","Customer_Name":"Customer",
                           "Address":"Test Rd","Demand_kg":5,"Leg_km":4,"_matrix_index":1}],"unassigned":[]}
            parcel_rows=[{"Order_ID":"S1","Parcel_ID":"BOX","Quantity":1,"Length_cm":20,"Width_cm":20,
                           "Height_cm":20,"Weight_kg":5,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"}]
            fleet_rows=[{"Vehicle_ID":"V1","Available_Today":"Yes","Capacity_kg":100,"Interior_Length_cm":100,
                          "Interior_Width_cm":100,"Interior_Height_cm":100,"Depot":"D1"}]
            inputs={"orders":[{"Order_ID":"S1","Customer_Name":"Customer"}],"parcels":parcel_rows,"fleet":fleet_rows}
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?)",("PLAN-HISTORY","2026-11-26T10:00:00","2026-11-26","Approved",json.dumps(plan),json.dumps(inputs)))
                db.execute("INSERT INTO events(plan_id,event_at,event_type,details) VALUES(?,?,?,?)",("PLAN-HISTORY","2026-11-26T10:01:00","Approved",json.dumps({"approved_by":"Dispatcher"})))
            history=get_plan_history(path)
            self.assertEqual(history.iloc[0].Plan_ID,"PLAN-HISTORY")
            self.assertEqual(int(history.iloc[0].Orders_Assigned),1)
            detail=get_plan_detail(path,"PLAN-HISTORY")
            self.assertEqual(detail["plan"]["approver"],"Dispatcher")
            self.assertEqual(detail["inputs"]["parcels"][0]["Parcel_ID"],"BOX")
            self.assertEqual(detail["events"].iloc[0].Event,"Approved")
            export=export_plan(detail["plan"],pd.DataFrame(detail["inputs"]["parcels"]),
                pd.DataFrame(detail["inputs"]["fleet"]),detail["plan_id"],detail["plan_date"],
                detail["plan"].get("manual_edits",0),detail["plan"].get("approver",""))
            self.assertEqual(pd.ExcelFile(io.BytesIO(export)).sheet_names[0],"Plan Summary")

    def test_catalog_product_and_shipment_creation_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"catalog.db")
            initialize(path)
            save_product(path,{"sku":"BOX-CUSTOM","description":"Custom carton","category":"Carton",
                "length_cm":61,"width_cm":42,"height_cm":29,"weight_kg":8.5,"stackable":"No",
                "max_stack_weight_kg":0,"rotation":"Upright only","handling":"Keep dry"})
            product=get_products(path)
            row=product[product.SKU=="BOX-CUSTOM"].iloc[0]
            self.assertEqual(float(row.Length_cm),61)
            shipment={"shipment_id":"SHIP-CUSTOM","customer_name":"Example customer","address":"1 Test Road",
                "latitude":19.1,"longitude":72.9,"required_date":"2026-11-26","priority":"Normal",
                "window_start":"","window_end":"","service_minutes":10,"status":"Ready","notes":""}
            save_shipment(path,shipment,[("BOX-CUSTOM",3)])
            items=get_shipment_lines(path,["SHIP-CUSTOM"])
            self.assertEqual(int(items.iloc[0].Quantity),3)
            self.assertEqual(float(items.iloc[0].Weight_kg),8.5)
            archive_product(path,"BOX-CUSTOM")
            self.assertNotIn("BOX-CUSTOM",get_products(path).SKU.tolist())
            self.assertIn("BOX-CUSTOM",get_shipment_lines(path,["SHIP-CUSTOM"]).SKU.tolist())

    def test_vehicle_catalog_rejects_duplicate_registration_and_invalid_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"fleet.db")
            initialize(path,seed=False)
            base={"vehicle_id":"UNIQUE-VEH","name":"Unique vehicle","registration":"TEST-123",
                  "vehicle_type":"Van","available":True,"capacity_kg":500,
                  "interior_length_cm":300,"interior_width_cm":160,"interior_height_cm":150,
                  "door_width_cm":120,"door_height_cm":130,"volume_m3":5.0}
            save_vehicle(path,base)
            duplicate=dict(base,vehicle_id="DUPLICATE-VEH")
            with self.assertRaisesRegex(ValueError,"already assigned"):
                save_vehicle(path,duplicate)
            too_wide=dict(base,vehicle_id="TOO-WIDE",registration="",door_width_cm=161)
            with self.assertRaisesRegex(ValueError,"Door clear dimensions"):
                save_vehicle(path,too_wide)
            too_much_volume=dict(base,vehicle_id="TOO-MUCH-VOLUME",registration="",volume_m3=8.0)
            with self.assertRaisesRegex(ValueError,"geometric volume"):
                save_vehicle(path,too_much_volume)
            self.assertEqual(get_vehicles(path).Vehicle_ID.tolist(),["UNIQUE-VEH"])

    def test_catalog_approval_allocates_partial_quantity_and_preserves_remainder(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"catalog.db")
            initialize(path)
            shipments=list_shipments(path)
            shipment_id=str(shipments.iloc[0].Shipment_ID)
            lines=get_shipment_lines(path,[shipment_id])
            line=lines.iloc[0]
            original=int(line.Quantity)
            plan_id="PLAN-PARTIAL"
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?)",(plan_id,"now","today","Draft","{}","{}"))
            allocation=pd.DataFrame([{"Order_ID":shipment_id,"Parcel_ID":line.SKU,"Quantity":1}])
            approve_plan(path,plan_id,"Dispatcher",allocation)
            remaining=get_shipment_lines(path,[shipment_id])
            after=remaining.loc[remaining.SKU==line.SKU,"Quantity"]
            self.assertEqual(int(after.iloc[0]) if not after.empty else 0,original-1)
            with sqlite3.connect(path) as db:
                status=db.execute("SELECT status FROM plans WHERE id=?",(plan_id,)).fetchone()[0]
                allocated=db.execute("SELECT SUM(quantity) FROM shipment_allocations WHERE plan_id=?",(plan_id,)).fetchone()[0]
            self.assertEqual(status,"Approved")
            self.assertEqual(allocated,1)
            with self.assertRaisesRegex(ValueError,"already been approved"):
                approve_plan(path,plan_id,"Dispatcher",allocation)

    def test_catalog_approval_rejects_stale_quantity_without_partial_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"catalog.db")
            initialize(path)
            shipment_id=str(list_shipments(path).iloc[0].Shipment_ID)
            line=get_shipment_lines(path,[shipment_id]).iloc[0]
            plan_id="PLAN-STALE"
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?)",(plan_id,"now","today","Draft","{}","{}"))
            too_many=pd.DataFrame([{"Order_ID":shipment_id,"Parcel_ID":line.SKU,"Quantity":int(line.Quantity)+1}])
            with self.assertRaisesRegex(ValueError,"no longer has"):
                approve_plan(path,plan_id,"Dispatcher",too_many)
            with sqlite3.connect(path) as db:
                remaining=db.execute("SELECT quantity FROM shipment_items WHERE shipment_id=? AND sku=?",(shipment_id,line.SKU)).fetchone()[0]
                allocations=db.execute("SELECT COUNT(*) FROM shipment_allocations WHERE plan_id=?",(plan_id,)).fetchone()[0]
                status=db.execute("SELECT status FROM plans WHERE id=?",(plan_id,)).fetchone()[0]
            self.assertEqual(remaining,int(line.Quantity))
            self.assertEqual(allocations,0)
            self.assertEqual(status,"Draft")

    def test_catalog_approval_fully_allocates_and_marks_shipment_planned(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,"catalog.db")
            initialize(path)
            shipment_id=str(list_shipments(path).iloc[0].Shipment_ID)
            lines=get_shipment_lines(path,[shipment_id])
            plan_id="PLAN-FULL"
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?)",(plan_id,"now","today","Draft","{}","{}"))
            allocations=lines.rename(columns={"Shipment_ID":"Order_ID","SKU":"Parcel_ID"})[["Order_ID","Parcel_ID","Quantity"]]
            approve_plan(path,plan_id,"Dispatcher",allocations)
            shipment=list_shipments(path)
            self.assertEqual(shipment.loc[shipment.Shipment_ID==shipment_id,"Status"].iloc[0],"Planned")
            self.assertTrue(get_shipment_lines(path,[shipment_id]).empty)

    def test_demo_inputs_validate_and_fit(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        self.assertEqual(len(orders), 5)
        self.assertEqual(int(parcels.Quantity.sum()), 9)
        self.assertTrue(check_fit(parcels, fleet.iloc[0])["fits"])

    def test_geometric_fit_never_returns_partial_placements_as_a_feasible_load(self):
        parcels=pd.DataFrame([
            {"Order_ID":"O1","Parcel_ID":"SAME","Quantity":1,"Length_cm":10,"Width_cm":10,"Height_cm":10,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Upright only"},
            {"Order_ID":"O2","Parcel_ID":"SAME","Quantity":1,"Length_cm":11,"Width_cm":10,"Height_cm":10,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Upright only"},
        ])
        vehicle=pd.Series({"Interior_Length_cm":10,"Interior_Width_cm":10,"Interior_Height_cm":10})
        result=check_fit(parcels,vehicle)
        self.assertFalse(result["fits"])
        self.assertEqual(result["placement_rows"],[])
        self.assertEqual(len(result["failed"]),2)
        self.assertIn("bounded placement search",result["reason"])

    def test_loading_order_places_earliest_delivery_nearest_rear_door(self):
        parcels=pd.DataFrame([
            {"Order_ID":"FIRST","Delivery_Stop":1,"Parcel_ID":"A","Quantity":1,"Length_cm":30,"Width_cm":40,"Height_cm":40,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Upright only"},
            {"Order_ID":"SECOND","Delivery_Stop":2,"Parcel_ID":"B","Quantity":1,"Length_cm":30,"Width_cm":40,"Height_cm":40,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Upright only"},
        ])
        vehicle=pd.Series({"Interior_Length_cm":60,"Interior_Width_cm":40,"Interior_Height_cm":40})
        result=check_fit(parcels,vehicle)
        self.assertTrue(result["fits"],result["reason"])
        by_stop={row["Delivery_Stop"]:row for row in result["placement_rows"]}
        self.assertGreaterEqual(by_stop[1]["X_cm"],by_stop[2]["X_cm"]+by_stop[2]["Placed_Length_cm"])
        self.assertEqual({row["Delivery_Stop"] for row in result["placement_rows"]},{1,2})

    def test_loading_optimizer_assigns_stable_parcel_units_with_orientations(self):
        orders,parcels,fleet,_,distances,durations=prepared()
        orders=orders.copy().reset_index(drop=True)
        orders["_matrix_index"]=range(1,len(orders)+1)
        result=optimize_fleet_loading(orders,parcels,fleet,distances,durations,time_limit_seconds=4)
        self.assertFalse(result["unassigned"])
        self.assertTrue(result["vehicle_count_optimal"])
        self.assertEqual(result["vehicles_used"],1)
        expected=int(parcels.Quantity.sum())
        self.assertEqual(len(result["placements"]),expected)
        self.assertEqual(len({row["Parcel_Unit_ID"] for row in result["placements"]}),expected)
        expected_unit_ids={"{}:{}#{}".format(row.Order_ID,row.Parcel_ID,unit)
                           for _,row in parcels.iterrows() for unit in range(1,int(row.Quantity)+1)}
        self.assertEqual({row["Parcel_Unit_ID"] for row in result["placements"]},expected_unit_ids)
        for placement in result["placements"]:
            self.assertIn(placement["Orientation"],["L-W-H","L-H-W","W-L-H","W-H-L","H-L-W","H-W-L"])
            source=parcels[(parcels.Order_ID.astype(str)==placement["Order_ID"])&(parcels.Parcel_ID.astype(str)==placement["Parcel_ID"])].iloc[0]
            expected_vehicle=next(vehicle_id for vehicle_id,ids in result["assignments"].items() if placement["Order_ID"] in ids)
            self.assertEqual(placement["Vehicle_ID"],expected_vehicle)
            self.assertGreater(float(source.Weight_kg),0)

    def test_configured_usable_volume_limits_vehicle_assignment(self):
        orders,parcels,fleet,_,distances,durations=prepared()
        orders=orders.iloc[:1].copy().reset_index(drop=True)
        orders["_matrix_index"]=[1]
        parcels=parcels[parcels.Order_ID.astype(str)==str(orders.iloc[0].Order_ID)].copy()
        fleet=fleet.iloc[:1].copy()
        cargo_volume=sum(float(row.Length_cm)*float(row.Width_cm)*float(row.Height_cm)*int(row.Quantity)
                         for _,row in parcels.iterrows())
        fleet["Volume_m3"]=max(.001,cargo_volume/2_000_000)
        result=optimize_fleet_loading(orders,parcels,fleet,distances[:2] if len(distances)==2 else [row[:2] for row in distances[:2]],
                                      durations[:2] if len(durations)==2 else [row[:2] for row in durations[:2]],time_limit_seconds=2)
        self.assertEqual(result["assignments"],{})
        self.assertEqual(len(result["unassigned"]),1)
        self.assertIn("usable cargo volume",result["unassigned"][0]["Reason"])

    def test_loading_optimizer_adds_vehicle_when_combined_geometry_does_not_fit(self):
        orders=pd.DataFrame([
            {"Order_ID":"A","Customer_Name":"A","Address":"A","Latitude":0,"Longitude":0,"_matrix_index":1},
            {"Order_ID":"B","Customer_Name":"B","Address":"B","Latitude":0,"Longitude":1,"_matrix_index":2},
        ])
        parcels=pd.DataFrame([
            {"Order_ID":"A","Parcel_ID":"A-BOX","Quantity":1,"Length_cm":6,"Width_cm":6,"Height_cm":6,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":20,"Rotation":"Any"},
            {"Order_ID":"B","Parcel_ID":"B-BOX","Quantity":1,"Length_cm":6,"Width_cm":6,"Height_cm":6,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":20,"Rotation":"Any"},
        ])
        fleet=pd.DataFrame([
            {"Vehicle_ID":"V1","Available_Today":"Yes","Capacity_kg":10,"Interior_Length_cm":10,"Interior_Width_cm":10,"Interior_Height_cm":10},
            {"Vehicle_ID":"V2","Available_Today":"Yes","Capacity_kg":10,"Interior_Length_cm":10,"Interior_Width_cm":10,"Interior_Height_cm":10},
        ])
        distances=[[0,1,2],[1,0,1],[2,1,0]]
        result=optimize_fleet_loading(orders,parcels,fleet,distances,distances,time_limit_seconds=4)
        self.assertFalse(result["unassigned"])
        self.assertEqual(result["vehicles_used"],2)
        self.assertGreaterEqual(result["candidates_checked"],2)

    def test_synthetic_twenty_shipment_load_is_repeatable_and_geometrically_valid(self):
        count=20
        orders=pd.DataFrame([
            {"Order_ID":"S{:02d}".format(index),"Customer_Name":"Site {}".format(index),
             "Address":"{} Test Road".format(index),"Latitude":0.0,"Longitude":index/100,
             "_matrix_index":index+1}
            for index in range(count)])
        parcels=pd.DataFrame([
            {"Order_ID":"S{:02d}".format(index),"Parcel_ID":"BOX-{:02d}".format(index),
             "Quantity":1,"Length_cm":20,"Width_cm":20,"Height_cm":20,"Weight_kg":1,
             "Stackable":"Yes","Max_Stack_Weight_kg":100,"Rotation":"Any"}
            for index in range(count)])
        fleet=pd.DataFrame([
            {"Vehicle_ID":"LARGE-{}".format(index),"Available_Today":"Yes","Capacity_kg":50,
             "Interior_Length_cm":200,"Interior_Width_cm":100,"Interior_Height_cm":100}
            for index in range(3)])
        matrix=[[0 if left==right else abs(left-right)+1 for right in range(count+1)]
                for left in range(count+1)]
        first=optimize_fleet_loading(orders,parcels,fleet,matrix,matrix,time_limit_seconds=4)
        second=optimize_fleet_loading(orders,parcels,fleet,matrix,matrix,time_limit_seconds=4)
        self.assertFalse(first["unassigned"])
        self.assertEqual(first["assignments"],second["assignments"])
        manifest=lambda plan:sorted((row["Parcel_Unit_ID"],row["Vehicle_ID"],row["Orientation"],
                                     row["X_cm"],row["Y_cm"],row["Z_cm"]) for row in plan["placements"])
        self.assertEqual(manifest(first),manifest(second))
        self.assertEqual(len(first["placements"]),count)
        self.assertEqual(len({row["Parcel_Unit_ID"] for row in first["placements"]}),count)
        for vehicle_id in first["assignments"]:
            loaded=[row for row in first["placements"] if row["Vehicle_ID"]==vehicle_id]
            for item in loaded:
                self.assertLessEqual(item["X_cm"]+item["Placed_Length_cm"],200.001)
                self.assertLessEqual(item["Y_cm"]+item["Placed_Width_cm"],100.001)
                self.assertLessEqual(item["Z_cm"]+item["Placed_Height_cm"],100.001)
            for index,left in enumerate(loaded):
                for right in loaded[index+1:]:
                    overlap=(left["X_cm"]<right["X_cm"]+right["Placed_Length_cm"]-1e-6 and
                             right["X_cm"]<left["X_cm"]+left["Placed_Length_cm"]-1e-6 and
                             left["Y_cm"]<right["Y_cm"]+right["Placed_Width_cm"]-1e-6 and
                             right["Y_cm"]<left["Y_cm"]+left["Placed_Width_cm"]-1e-6 and
                             left["Z_cm"]<right["Z_cm"]+right["Placed_Height_cm"]-1e-6 and
                             right["Z_cm"]<left["Z_cm"]+left["Placed_Height_cm"]-1e-6)
                    self.assertFalse(overlap)

    def test_dispatch_load_optimizer_retries_when_shared_route_breaks_time_windows(self):
        orders=pd.DataFrame([
            {"Order_ID":"A","Customer_Name":"A","Address":"A","Latitude":1,"Longitude":1,"Priority":"Normal","Window_Start":"08:00","Window_End":"08:15","Service_Minutes":0},
            {"Order_ID":"B","Customer_Name":"B","Address":"B","Latitude":2,"Longitude":2,"Priority":"Normal","Window_Start":"08:00","Window_End":"08:15","Service_Minutes":0},
        ])
        parcels=pd.DataFrame([
            {"Order_ID":"A","Parcel_ID":"A-BOX","Quantity":1,"Length_cm":10,"Width_cm":10,"Height_cm":10,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"},
            {"Order_ID":"B","Parcel_ID":"B-BOX","Quantity":1,"Length_cm":10,"Width_cm":10,"Height_cm":10,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"},
        ])
        fleet=pd.DataFrame([
            {"Vehicle_ID":"V1","Available_Today":"Yes","Capacity_kg":100,"Interior_Length_cm":100,"Interior_Width_cm":100,"Interior_Height_cm":100},
            {"Vehicle_ID":"V2","Available_Today":"Yes","Capacity_kg":100,"Interior_Length_cm":100,"Interior_Width_cm":100,"Interior_Height_cm":100},
        ])
        distances=[[0,10,10],[10,0,20],[10,20,0]]
        plan=build_dispatch(orders,parcels,fleet,distances,distances,objective="Travel time",time_limit_seconds=4,departure_time="08:00")
        assigned=pd.DataFrame(plan["routes"]).groupby("Order_ID").Vehicle_ID.nunique()
        self.assertEqual(set(assigned.index.astype(str)),{"A","B"})
        self.assertEqual(int(assigned.sum()),2)
        self.assertEqual(plan["vehicles_used"],2)
        self.assertFalse(plan["unassigned"])

    def test_door_clearance_can_select_orientation_or_reject_parcel(self):
        parcels=pd.DataFrame([{"Order_ID":"O1","Parcel_ID":"P1","Quantity":1,"Length_cm":120,"Width_cm":50,"Height_cm":40,"Weight_kg":4,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"}])
        vehicle=pd.Series({"Interior_Length_cm":120,"Interior_Width_cm":60,"Interior_Height_cm":50,"Door_Width_cm":45,"Door_Height_cm":50})
        fit=check_fit(parcels,vehicle)
        self.assertTrue(fit["fits"])
        self.assertEqual(fit["placement_rows"][0]["Orientation"],"L-H-W")
        vehicle["Door_Width_cm"]=35
        self.assertFalse(check_fit(parcels,vehicle)["fits"])

    def test_fleet_optimizer_uses_configured_vehicle_cost_after_vehicle_count(self):
        orders=pd.DataFrame([{"Order_ID":"A","Customer_Name":"A","Address":"A","Latitude":1,"Longitude":1,"_matrix_index":1}])
        parcels=pd.DataFrame([{"Order_ID":"A","Parcel_ID":"A-BOX","Quantity":1,"Length_cm":10,"Width_cm":10,"Height_cm":10,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"}])
        fleet=pd.DataFrame([
            {"Vehicle_ID":"COSTLY","Available_Today":"Yes","Capacity_kg":100,"Interior_Length_cm":100,"Interior_Width_cm":100,"Interior_Height_cm":100,"Cost_Per_Km":20},
            {"Vehicle_ID":"ECONOMICAL","Available_Today":"Yes","Capacity_kg":100,"Interior_Length_cm":100,"Interior_Width_cm":100,"Interior_Height_cm":100,"Cost_Per_Km":2},
        ])
        distances=[[0,5],[5,0]];durations=[[0,20],[20,0]]
        result=optimize_fleet_loading(orders,parcels,fleet,distances,durations,objective="Travel time",time_limit_seconds=3)
        self.assertEqual(list(result["assignments"]),["ECONOMICAL"])

    def test_selected_travel_time_objective_outweighs_vehicle_cost(self):
        order_ids=["A","B","C","D"]
        orders=pd.DataFrame([{"Order_ID":value,"_matrix_index":index+1} for index,value in enumerate(order_ids)])
        parcels=pd.DataFrame([{"Order_ID":value,"Parcel_ID":value,"Quantity":1,
            "Length_cm":10,"Width_cm":10,"Height_cm":10,"Weight_kg":1,
            "Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"} for value in order_ids])
        fleet=pd.DataFrame([
            {"Vehicle_ID":"ECONOMICAL","Available_Today":"Yes","Capacity_kg":2,
             "Interior_Length_cm":100,"Interior_Width_cm":100,"Interior_Height_cm":100,"Cost_Per_Km":1},
            {"Vehicle_ID":"COSTLY","Available_Today":"Yes","Capacity_kg":2,
             "Interior_Length_cm":100,"Interior_Width_cm":100,"Interior_Height_cm":100,"Cost_Per_Km":100},
        ])
        distances=[[0,100,100,1,1],[100,0,200,100,100],[100,200,0,100,100],
                   [1,100,100,0,1],[1,100,100,1,0]]
        durations=[[0,10,10,10,10],[10,0,20,1,20],[10,20,0,20,1],
                   [10,1,20,0,20],[10,20,1,20,0]]
        result=optimize_fleet_loading(orders,parcels,fleet,distances,durations,
            objective="Travel time",time_limit_seconds=3)
        groups={frozenset(ids) for ids in result["assignments"].values()}
        self.assertEqual(groups,{frozenset({"A","C"}),frozenset({"B","D"})})

    def test_route_capacity_preserves_fractional_kilograms(self):
        orders=pd.DataFrame([
            {"Order_ID":"A","Customer_Name":"A","Address":"A","Latitude":1,"Longitude":1,"Demand_kg":0.7,"_matrix_index":1},
            {"Order_ID":"B","Customer_Name":"B","Address":"B","Latitude":2,"Longitude":2,"Demand_kg":0.7,"_matrix_index":2},
        ])
        fleet=pd.DataFrame([{"Vehicle_ID":"V1","Available_Today":"Yes","Capacity_kg":1.4}])
        matrix=[[0,1,2],[1,0,1],[2,1,0]]
        result=optimize(orders,fleet,matrix,matrix,time_limit_seconds=1)
        self.assertEqual({str(row["Order_ID"]) for row in result["routes"]},{"A","B"})
        self.assertFalse(result["unassigned"])

    def test_route_fallback_uses_travel_time_metric_when_requested(self):
        orders=pd.DataFrame([
            {"Order_ID":"A","Customer_Name":"A","Address":"A","Latitude":0,"Longitude":0,"Demand_kg":1,"_matrix_index":1},
            {"Order_ID":"B","Customer_Name":"B","Address":"B","Latitude":0,"Longitude":0,"Demand_kg":1,"_matrix_index":2},
        ])
        fleet=pd.DataFrame([
            {"Vehicle_ID":"V1","Available_Today":"Yes","Capacity_kg":1},
            {"Vehicle_ID":"V2","Available_Today":"Yes","Capacity_kg":1},
        ])
        distances=[[0,10,10],[10,0,1],[10,1,0]]
        durations=[[0,10,1],[10,0,100],[1,100,0]]
        result=_fallback_plan(orders,fleet,distances,durations,objective="Travel time")
        assignment={str(row["Order_ID"]):str(row["Vehicle_ID"]) for row in result["routes"]}
        self.assertEqual(assignment["A"],"V1")
        self.assertEqual(assignment["B"],"V2")

    def test_two_opt_refines_nearest_neighbor_route_without_increasing_distance(self):
        import math
        import time
        points=[(0,0),(-4,-10),(-11,20),(-19,-6),(-6,8),(-11,-3)]
        matrix=[[math.hypot(a[0]-b[0],a[1]-b[1]) for b in points] for a in points]
        order_ids=["A","B","C","D","E"]
        order_by_id={order_id:pd.Series({"_matrix_index":index+1})
                     for index,order_id in enumerate(order_ids)}
        nearest=["D","E","C","A","B"]
        def cost(sequence):
            nodes=[0]+[int(order_by_id[value]._matrix_index) for value in sequence]+[0]
            return sum(matrix[left][right] for left,right in zip(nodes,nodes[1:]))
        refined=_two_opt_distance_order(nearest,order_by_id,matrix,time.monotonic()+1)
        self.assertLess(cost(refined),cost(nearest))
        self.assertEqual(sorted(refined),sorted(nearest))

    def test_rotation_rules_and_maximum_stack_weight(self):
        parcel=pd.DataFrame([{"Parcel_ID":"ROT-1","Order_ID":"R1","Quantity":1,"Length_cm":190,"Width_cm":40,"Height_cm":30,"Weight_kg":10,"Stackable":"Yes","Max_Stack_Weight_kg":50,"Rotation":"Any"}])
        vehicle=pd.Series({"Interior_Length_cm":100,"Interior_Width_cm":200,"Interior_Height_cm":100})
        self.assertTrue(check_fit(parcel,vehicle)["fits"])
        parcel.loc[0,"Rotation"]="Upright only"
        self.assertFalse(check_fit(parcel,vehicle)["fits"])
        stacked=pd.DataFrame([
            {"Parcel_ID":"BASE","Order_ID":"S1","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":50,"Weight_kg":5,"Stackable":"Yes","Max_Stack_Weight_kg":5,"Rotation":"Upright only"},
            {"Parcel_ID":"TOP","Order_ID":"S2","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":30,"Weight_kg":10,"Stackable":"No","Max_Stack_Weight_kg":50,"Rotation":"Upright only"},
        ])
        small_vehicle=pd.Series({"Interior_Length_cm":50,"Interior_Width_cm":50,"Interior_Height_cm":100})
        result=check_fit(stacked,small_vehicle)
        self.assertFalse(result["fits"])
        self.assertTrue(result["exceeded_stack"])

    def test_stacking_support_propagates_cumulative_load_and_reports_unknown_limit(self):
        stacked=pd.DataFrame([
            {"Parcel_ID":"BASE","Order_ID":"S1","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":50,"Weight_kg":5,"Stackable":"Yes","Max_Stack_Weight_kg":8,"Rotation":"Upright only"},
            {"Parcel_ID":"MIDDLE","Order_ID":"S2","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":50,"Weight_kg":5,"Stackable":"Yes","Max_Stack_Weight_kg":8,"Rotation":"Upright only"},
            {"Parcel_ID":"TOP","Order_ID":"S3","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":50,"Weight_kg":5,"Stackable":"Yes","Max_Stack_Weight_kg":8,"Rotation":"Upright only"},
        ])
        vehicle=pd.Series({"Interior_Length_cm":50,"Interior_Width_cm":50,"Interior_Height_cm":150})
        overloaded=check_fit(stacked,vehicle)
        self.assertFalse(overloaded["fits"])
        self.assertTrue(overloaded["exceeded_stack"])
        unknown_parcels=pd.DataFrame([
            {"Parcel_ID":"UNKNOWN-BASE","Order_ID":"U1","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":30,"Weight_kg":2,"Stackable":"Yes","Max_Stack_Weight_kg":0,"Rotation":"Upright only"},
            {"Parcel_ID":"NO-STACK-TOP","Order_ID":"U2","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":30,"Weight_kg":5,"Stackable":"No","Max_Stack_Weight_kg":0,"Rotation":"Upright only"},
        ])
        unknown=check_fit(unknown_parcels,pd.Series({"Interior_Length_cm":50,"Interior_Width_cm":50,"Interior_Height_cm":60}))
        self.assertFalse(unknown["fits"])
        self.assertIn("UNKNOWN-BASE",unknown["unknown_stack_limits"])
        self.assertIn("unknown",unknown["reason"].lower())

    def test_parcel_with_partial_base_support_is_rejected(self):
        parcels=pd.DataFrame([
            {"Parcel_ID":"BASE","Order_ID":"S1","Quantity":1,"Length_cm":40,"Width_cm":50,"Height_cm":50,"Weight_kg":2,"Stackable":"Yes","Max_Stack_Weight_kg":20,"Rotation":"Upright only"},
            {"Parcel_ID":"OVERHANG","Order_ID":"S2","Quantity":1,"Length_cm":50,"Width_cm":50,"Height_cm":40,"Weight_kg":2,"Stackable":"No","Max_Stack_Weight_kg":20,"Rotation":"Upright only"},
        ])
        vehicle=pd.Series({"Interior_Length_cm":50,"Interior_Width_cm":50,"Interior_Height_cm":90})
        result=check_fit(parcels,vehicle)
        self.assertFalse(result["fits"])
        self.assertIn("BASE",result["unsupported_stack"])

    def test_dispatch_assigns_each_order_once_within_capacity_and_fit(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        result = build_dispatch(orders, parcels, fleet, distances, durations,time_limit_seconds=8)
        assigned = [str(r["Order_ID"]) for r in result["routes"]]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), set(orders.Order_ID.astype(str)))
        self.assertFalse(result["unassigned"])
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["placements"]),int(parcels.Quantity.sum()))
        route_stops={(str(row["Order_ID"]),str(row["Vehicle_ID"])):int(row["Stop"]) for row in result["routes"]}
        self.assertTrue(all(row["Delivery_Stop"]==route_stops[(str(row["Order_ID"]),str(row["Vehicle_ID"]))]
                            for row in result["placements"]))
        for vehicle_id in result["assignments"]:
            load_rows=sorted((row for row in result["placements"] if row["Vehicle_ID"]==vehicle_id),
                             key=lambda row:row["Load_Sequence"])
            self.assertEqual([row["Delivery_Stop"] for row in load_rows],
                             sorted((row["Delivery_Stop"] for row in load_rows),reverse=True))
        self.assertTrue(all(row["Vehicle_ID"] in result["assignments"] for row in result["placements"]))
        for vehicle_id, group in pd.DataFrame(result["routes"]).groupby("Vehicle_ID"):
            truck = fleet[fleet.Vehicle_ID.astype(str) == str(vehicle_id)].iloc[0]
            self.assertLessEqual(group.Demand_kg.sum(), truck.Capacity_kg)
            ids = set(group.Order_ID.astype(str))
            self.assertTrue(check_fit(parcels[parcels.Order_ID.astype(str).isin(ids)], truck)["fits"])

    def test_dispatch_with_unavailable_fleet_is_incomplete_and_cannot_be_called_feasible(self):
        orders,parcels,fleet,_,distances,durations=prepared()
        plan=build_dispatch(orders,parcels,fleet.iloc[0:0],distances,durations,time_limit_seconds=2)
        self.assertFalse(plan["complete"])
        self.assertTrue(plan["unassigned"])
        self.assertTrue(plan["status"].startswith("Review required"))

    def test_reassignment_rejects_over_capacity_and_preserves_source(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        plan = build_dispatch(orders, parcels, fleet, distances, durations,time_limit_seconds=8)
        assigned = str(plan["routes"][0]["Order_ID"])
        too_small = fleet[fleet.Vehicle_ID == "VAN-02"].copy()
        too_small.loc[:, "Capacity_kg"] = 1
        revised_fleet = pd.concat([fleet[fleet.Vehicle_ID != "VAN-02"], too_small])
        before = list(plan["routes"])
        with self.assertRaisesRegex(ValueError, "exceeds"):
            reassign_order(plan, assigned, "VAN-02", orders, parcels, revised_fleet, distances, durations)
        self.assertEqual(plan["routes"], before)

    def test_reassignment_recomputes_totals_and_keeps_one_copy(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        plan = build_dispatch(orders, parcels, fleet, distances, durations,time_limit_seconds=8)
        order_id = str(plan["routes"][0]["Order_ID"])
        target = "VAN-02"
        revised_fleet=fleet.copy()
        revised_fleet.loc[revised_fleet.Vehicle_ID==target,["Capacity_kg","Interior_Length_cm","Interior_Width_cm","Interior_Height_cm"]]=[900,600,300,250]
        updated = reassign_order(plan, order_id, target, orders, parcels, revised_fleet, distances, durations)
        matches = [r for r in updated["routes"] if str(r["Order_ID"]) == order_id]
        self.assertEqual(len(matches), 1)
        self.assertEqual(str(matches[0]["Vehicle_ID"]), target)
        self.assertGreater(updated["total_km"], 0)
        self.assertFalse(updated["unassigned"])
        self.assertEqual(len(updated["placements"]),int(parcels.Quantity.sum()))

    def test_manual_parcel_orientation_repacks_vehicle_and_keeps_assignment(self):
        orders,parcels,fleet,_,distances,durations=prepared()
        plan=build_dispatch(orders,parcels,fleet,distances,durations,time_limit_seconds=8)
        placement=next(row for row in plan["placements"]
                       if len({dims for _,dims in orientation_options(parcels[
                           (parcels.Order_ID.astype(str)==row["Order_ID"])&
                           (parcels.Parcel_ID.astype(str)==row["Parcel_ID"])].iloc[0])})>1)
        source=parcels[(parcels.Order_ID.astype(str)==placement["Order_ID"])&
                       (parcels.Parcel_ID.astype(str)==placement["Parcel_ID"])].iloc[0]
        choices=[label for label,_ in orientation_options(source)]
        alternate=next(label for label in choices if label!=placement["Orientation"])
        before_routes=list(plan["routes"])
        updated=change_parcel_orientation(plan,placement["Parcel_Unit_ID"],alternate,parcels,fleet)
        changed=next(row for row in updated["placements"] if row["Parcel_Unit_ID"]==placement["Parcel_Unit_ID"])
        self.assertEqual(changed["Orientation"],alternate)
        self.assertEqual(updated["routes"],before_routes)
        self.assertEqual(len(updated["placements"]),int(parcels.Quantity.sum()))
        self.assertTrue(updated["fit_results"][placement["Vehicle_ID"]]["fits"])
        with self.assertRaisesRegex(ValueError,"not allowed"):
            change_parcel_orientation(plan,placement["Parcel_Unit_ID"],"X-Y-Z",parcels,fleet)

    def test_manual_parcel_position_pins_unit_and_revalidates_vehicle(self):
        parcels=pd.DataFrame([{"Order_ID":"O1","Parcel_ID":"P1","Quantity":1,"Length_cm":10,"Width_cm":10,"Height_cm":10,"Weight_kg":1,"Stackable":"Yes","Max_Stack_Weight_kg":10,"Rotation":"Any"}])
        fleet=pd.DataFrame([{"Vehicle_ID":"V1","Available_Today":"Yes","Capacity_kg":100,"Interior_Length_cm":100,"Interior_Width_cm":100,"Interior_Height_cm":100}])
        fit=check_fit(parcels,fleet.iloc[0])
        placement=dict(fit["placement_rows"][0],Vehicle_ID="V1",Delivery_Stop=1)
        routes=[{"Vehicle_ID":"V1","Order_ID":"O1","Stop":1}]
        plan={"routes":routes,"placements":[placement],"unassigned":[],"fit_results":{"V1":{"fits":True}}}
        original_routes=list(plan["routes"])
        original_positions={placement["Parcel_Unit_ID"]:(placement["X_cm"],placement["Y_cm"],placement["Z_cm"])}
        updated=move_parcel_position(plan,placement["Parcel_Unit_ID"],5,0,0,parcels,fleet)
        moved=next(row for row in updated["placements"]
                   if row["Parcel_Unit_ID"]==placement["Parcel_Unit_ID"])
        self.assertEqual((moved["X_cm"],moved["Y_cm"],moved["Z_cm"]),(5,0,0))
        self.assertEqual(updated["routes"],original_routes)
        self.assertEqual(len(updated["placements"]),int(parcels.Quantity.sum()))
        self.assertTrue(updated["fit_results"]["V1"]["fits"])
        self.assertEqual(plan["placements"],[
            dict(row,X_cm=original_positions[row["Parcel_Unit_ID"]][0],
                 Y_cm=original_positions[row["Parcel_Unit_ID"]][1],
                 Z_cm=original_positions[row["Parcel_Unit_ID"]][2]) for row in plan["placements"]])
        with self.assertRaisesRegex(ValueError,"full vehicle-load validation"):
            move_parcel_position(plan,placement["Parcel_Unit_ID"],9999,0,0,parcels,fleet)
        with self.assertRaisesRegex(ValueError,"non-negative"):
            move_parcel_position(plan,placement["Parcel_Unit_ID"],-1,0,0,parcels,fleet)
        approved=dict(plan,approver="Dispatcher")
        with self.assertRaisesRegex(ValueError,"locked"):
            move_parcel_position(approved,placement["Parcel_Unit_ID"],5,0,0,parcels,fleet)

    def test_approved_plan_rejects_parcel_orientation_edits(self):
        orders,parcels,fleet,_,distances,durations=prepared()
        plan=build_dispatch(orders,parcels,fleet,distances,durations,time_limit_seconds=8)
        plan["approver"]="Dispatcher"
        placement=plan["placements"][0]
        with self.assertRaisesRegex(ValueError,"locked"):
            change_parcel_orientation(plan,placement["Parcel_Unit_ID"],placement["Orientation"],parcels,fleet)

    def test_unassigned_order_can_be_assigned_manually(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        plan = build_dispatch(orders, parcels, fleet, distances, durations,time_limit_seconds=8)
        order_id = str(plan["routes"][0]["Order_ID"])
        draft = dict(plan)
        draft["routes"] = [r for r in plan["routes"] if str(r["Order_ID"]) != order_id]
        draft["unassigned"] = list(plan["unassigned"])+[{"Order_ID":order_id,"Reason":"Test hold"}]
        revised_fleet=fleet.copy()
        revised_fleet.loc[revised_fleet.Vehicle_ID=="VAN-02",["Capacity_kg","Interior_Length_cm","Interior_Width_cm","Interior_Height_cm"]]=[900,600,300,250]
        updated = reassign_order(draft, order_id, "VAN-02", orders, parcels, revised_fleet, distances, durations)
        self.assertNotIn(order_id, [str(r["Order_ID"]) for r in updated["unassigned"]])
        self.assertIn(order_id, [str(r["Order_ID"]) for r in updated["routes"]])

    def test_move_stop_recomputes_estimates(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        plan = build_dispatch(orders, parcels, fleet, distances, durations,time_limit_seconds=8)
        group = sorted(plan["routes"], key=lambda r: int(r["Stop"]))
        moved = move_stop(plan, str(group[-1]["Order_ID"]), "up", orders, distances, durations,
                          parcels=parcels,fleet=fleet)
        self.assertEqual(len(moved["routes"]), len(plan["routes"]))
        self.assertGreater(moved["total_km"], 0)
        ranks={(str(row["Order_ID"]),str(row["Vehicle_ID"])):int(row["Stop"]) for row in moved["routes"]}
        self.assertTrue(all(row["Delivery_Stop"]==ranks[(str(row["Order_ID"]),str(row["Vehicle_ID"]))]
                            for row in moved["placements"]))

    def test_stop_move_rejects_delivery_window_violation(self):
        orders=pd.DataFrame([
            {"Order_ID":"A","Service_Minutes":0,"Window_Start":"08:00","Window_End":"08:10"},
            {"Order_ID":"B","Service_Minutes":0,"Window_Start":"08:20","Window_End":"09:00"},
        ])
        routes=[
            {"Vehicle_ID":"V1","Stop":1,"Order_ID":"A","_matrix_index":1,"Demand_kg":1},
            {"Vehicle_ID":"V1","Stop":2,"Order_ID":"B","_matrix_index":2,"Demand_kg":1},
        ]
        plan={"routes":routes,"unassigned":[],"objective":"Distance"}
        distances=[[0,5,30],[5,0,5],[30,5,0]]
        durations=[[0,5,30],[5,0,5],[30,5,0]]
        with self.assertRaisesRegex(ValueError,"delivery window"):
            move_stop(plan,"B","up",orders,distances,durations,"08:00")

    def test_infeasible_time_window_is_reported(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        orders.loc[:, "Window_Start"] = "06:00"
        orders.loc[:, "Window_End"] = "07:00"
        orders["Demand_kg"] = parcels.assign(_load=parcels.Weight_kg*parcels.Quantity).groupby("Order_ID")["_load"].sum().reindex(orders.Order_ID).values
        orders["_matrix_index"] = range(1,len(orders)+1)
        result=optimize(orders,fleet,distances,durations,time_limit_seconds=1,departure_time="08:00")
        self.assertTrue(result["unassigned"])

    def test_validation_rejects_fractional_quantity_and_bad_coordinates(self):
        orders, parcels, fleet, depot = sample_orders(), sample_parcels(), sample_fleet(), sample_depot()
        parcels["Quantity"]=parcels.Quantity.astype(float)
        parcels.loc[0, "Quantity"] = 1.5
        orders.loc[0, "Latitude"] = 120
        *_, errors, _ = validate_inputs(orders, parcels, fleet, depot)
        self.assertTrue(any("whole number" in error for error in errors))
        self.assertTrue(any("decimal degrees" in error for error in errors))

    def test_validation_allows_same_parcel_sku_across_orders(self):
        orders,parcels,fleet,depot=sample_orders(),sample_parcels(),sample_fleet(),sample_depot()
        parcels.loc[1,"Parcel_ID"]=parcels.loc[0,"Parcel_ID"]
        *_,errors,_=validate_inputs(orders,parcels,fleet,depot)
        self.assertFalse(any("duplicate Parcel_ID" in error for error in errors),errors)

    def test_validation_rejects_duplicate_parcel_line_within_same_order(self):
        orders,parcels,fleet,depot=sample_orders(),sample_parcels(),sample_fleet(),sample_depot()
        parcels.loc[1,"Order_ID"]=parcels.loc[0,"Order_ID"]
        parcels.loc[1,"Parcel_ID"]=parcels.loc[0,"Parcel_ID"]
        *_,errors,_=validate_inputs(orders,parcels,fleet,depot)
        self.assertTrue(any("duplicate Parcel_ID within Order_ID" in error for error in errors),errors)

    def test_validation_rejects_nonfinite_optimizer_inputs_and_invalid_optional_fleet_limits(self):
        orders,parcels,fleet,depot=sample_orders(),sample_parcels(),sample_fleet(),sample_depot()
        parcels["Weight_kg"]=parcels.Weight_kg.astype(float)
        fleet["Capacity_kg"]=fleet.Capacity_kg.astype(float)
        parcels.loc[0,"Weight_kg"]=float("inf")
        fleet.loc[0,"Capacity_kg"]=float("inf")
        fleet.loc[0,"Door_Width_cm"]=999
        fleet.loc[0,"Volume_m3"]=999
        *_,errors,_=validate_inputs(orders,parcels,fleet,depot)
        self.assertTrue(any("Weight_kg must contain finite positive" in error for error in errors))
        self.assertTrue(any("Capacity_kg must contain finite positive" in error for error in errors))
        self.assertTrue(any("door clear dimensions" in error.lower() for error in errors),errors)
        self.assertTrue(any("usable volume cannot exceed" in error for error in errors),errors)

    def test_validation_rejects_malformed_optional_route_and_stack_inputs(self):
        orders,parcels,fleet,depot=sample_orders(),sample_parcels(),sample_fleet(),sample_depot()
        orders["Service_Minutes"]=0
        orders["Service_Minutes"]=orders.Service_Minutes.astype(object)
        orders.loc[0,"Service_Minutes"]="fast"
        parcels["Max_Stack_Weight_kg"]=0
        parcels["Max_Stack_Weight_kg"]=parcels.Max_Stack_Weight_kg.astype(object)
        parcels.loc[0,"Max_Stack_Weight_kg"]="unknown"
        orders["Latitude"]=orders.Latitude.astype(object)
        orders.loc[0,"Latitude"]=float("nan")
        orders.loc[0,"Longitude"]=float("nan")
        orders.loc[0,"Address"]="1 Example Road"
        orders.loc[0,"Latitude"]="north"
        *_,errors,_=validate_inputs(orders,parcels,fleet,depot)
        self.assertTrue(any("Service_Minutes must be numeric" in error for error in errors))
        self.assertTrue(any("Max_Stack_Weight_kg must be numeric" in error for error in errors))
        self.assertTrue(any("Latitude must contain valid decimal-degree" in error for error in errors))

    def test_template_workbook_round_trip(self):
        payload=workbook_template()
        book = pd.ExcelFile(io.BytesIO(payload))
        self.assertEqual(book.sheet_names, ["Orders", "Parcels", "Fleet", "Depot"])
        self.assertEqual(set(load_planning_workbook(io.BytesIO(payload))),{"Orders","Parcels","Fleet","Depot"})

    def test_dispatch_export_round_trip(self):
        orders, parcels, fleet, _, distances, durations = prepared()
        plan = build_dispatch(orders, parcels, fleet, distances, durations,time_limit_seconds=8)
        payload = export_plan(plan, parcels, fleet, "TEST-001", "2026-11-26")
        book = pd.ExcelFile(io.BytesIO(payload))
        self.assertEqual(book.sheet_names, ["Plan Summary", "Vehicle Summary", "Vehicle Routes", "Exceptions", "Parcel Inputs"])
        route_sheet = pd.read_excel(io.BytesIO(payload), sheet_name="Vehicle Routes")
        self.assertEqual(len(route_sheet), len(orders))


if __name__ == "__main__":
    unittest.main()
