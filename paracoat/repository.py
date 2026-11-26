"""SQLite catalog and dispatch repository for the local Paracoat prototype."""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from paracoat.sample_data import (sample_depot, sample_fleet, sample_orders, sample_parcels,
                                  extended_demo_orders, extended_demo_parcels, extended_demo_catalog, extended_demo_fleet)


def connect(path):
    db=sqlite3.connect(str(path),timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize(path,seed=True):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS vehicles(
            vehicle_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', registration TEXT DEFAULT '', vehicle_type TEXT DEFAULT 'Truck',
            available INTEGER NOT NULL DEFAULT 1, capacity_kg REAL NOT NULL, interior_length_cm REAL NOT NULL,
            interior_width_cm REAL NOT NULL, interior_height_cm REAL NOT NULL, door_width_cm REAL DEFAULT 0, door_height_cm REAL DEFAULT 0,
            volume_m3 REAL DEFAULT 0, pallet_capacity REAL DEFAULT 0, fuel_type TEXT DEFAULT '', cost_per_km REAL DEFAULT 0,
            depot TEXT DEFAULT 'Main Depot', notes TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parcel_catalog(
            sku TEXT PRIMARY KEY, description TEXT NOT NULL, category TEXT DEFAULT '', length_cm REAL NOT NULL, width_cm REAL NOT NULL,
            height_cm REAL NOT NULL, weight_kg REAL NOT NULL, stackable TEXT NOT NULL DEFAULT 'Yes', max_stack_weight_kg REAL DEFAULT 0,
            rotation TEXT NOT NULL DEFAULT 'Any', handling TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shipments(
            shipment_id TEXT PRIMARY KEY, customer_name TEXT NOT NULL, address TEXT NOT NULL, latitude REAL, longitude REAL,
            required_date TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'Normal', window_start TEXT DEFAULT '', window_end TEXT DEFAULT '',
            service_minutes REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'Ready', notes TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shipment_items(
            shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id) ON DELETE CASCADE,
            sku TEXT NOT NULL REFERENCES parcel_catalog(sku), quantity INTEGER NOT NULL CHECK(quantity>0),
            PRIMARY KEY(shipment_id,sku)
        );
        CREATE TABLE IF NOT EXISTS shipment_allocations(
            plan_id TEXT NOT NULL, shipment_id TEXT NOT NULL, sku TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity>0), allocated_at TEXT NOT NULL,
            PRIMARY KEY(plan_id,shipment_id,sku)
        );
        CREATE TABLE IF NOT EXISTS depot_settings(
            id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, address TEXT NOT NULL, latitude REAL, longitude REAL,
            loading_cutoff TEXT DEFAULT '', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings(
            setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plans(
            id TEXT PRIMARY KEY, created_at TEXT, plan_date TEXT, status TEXT, summary_json TEXT, inputs_json TEXT
        );
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT, event_at TEXT, event_type TEXT, details TEXT
        );
        """)
    if seed:
        seed_sample_catalog(path)


def seed_sample_catalog(path):
    with connect(path) as db:
        if db.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]==0:
            for _,v in sample_fleet().iterrows():
                save_vehicle(path,{
                    "vehicle_id":v.Vehicle_ID,"name":v.Vehicle_ID,"registration":"","vehicle_type":"Van",
                    "available":str(v.Available_Today).lower() in ("yes","true","1"),"capacity_kg":v.Capacity_kg,
                    "interior_length_cm":v.Interior_Length_cm,"interior_width_cm":v.Interior_Width_cm,
                    "interior_height_cm":v.Interior_Height_cm,"door_width_cm":0,"door_height_cm":0,
                    "volume_m3":v.Interior_Length_cm*v.Interior_Width_cm*v.Interior_Height_cm/1_000_000,
                    "pallet_capacity":0,"fuel_type":"","cost_per_km":0,"depot":v.Depot,"notes":"Sample vehicle"})
        if db.execute("SELECT COUNT(*) FROM parcel_catalog").fetchone()[0]==0:
            for _,p in sample_parcels().iterrows():
                save_product(path,{
                    "sku":p.Parcel_ID,"description":p.Parcel_ID,"category":"Sample","length_cm":p.Length_cm,
                    "width_cm":p.Width_cm,"height_cm":p.Height_cm,"weight_kg":p.Weight_kg,
                    "stackable":p.Stackable,"max_stack_weight_kg":p.Max_Stack_Weight_kg,"rotation":p.Rotation,
                    "handling":"Sample catalog item"})
        if db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]==0:
            orders=sample_orders(); parcels=sample_parcels()
            for _,o in orders.iterrows():
                shipment={"shipment_id":o.Order_ID,"customer_name":o.Customer_Name,"address":o.Address,
                    "latitude":o.Latitude,"longitude":o.Longitude,"required_date":date.today().isoformat(),
                    "priority":o.Priority,"window_start":o.Window_Start,"window_end":o.Window_End,
                    "service_minutes":o.Service_Minutes,"status":"Ready","notes":"Synthetic demonstration shipment"}
                items=[(str(p.Parcel_ID),int(p.Quantity)) for _,p in parcels[parcels.Order_ID==o.Order_ID].iterrows()]
                save_shipment(path,shipment,items)
        if db.execute("SELECT COUNT(*) FROM depot_settings").fetchone()[0]==0:
            d=sample_depot().iloc[0]
            save_depot(path,{"name":d.Depot,"address":d.Address,"latitude":d.Latitude,"longitude":d.Longitude,"loading_cutoff":""})
        defaults={"routing_url":"https://router.project-osrm.org","default_objective":"Distance","default_departure":"08:00","planning_timezone":"Local"}
        for key,value in defaults.items():
            db.execute("INSERT OR IGNORE INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?)",(key,str(value),datetime.now().isoformat(timespec="seconds")))


def seed_extended_demo_catalog(path):
    """Append missing synthetic fleet, SKU, and shipment records without overwriting user data."""
    db=connect(path)
    try:
        existing_vehicles={str(row[0]) for row in db.execute("SELECT vehicle_id FROM vehicles")}
        existing_products={str(row[0]) for row in db.execute("SELECT sku FROM parcel_catalog")}
        existing_shipments={str(row[0]) for row in db.execute("SELECT shipment_id FROM shipments")}
    finally:
        db.close()
    added={"vehicles":0,"products":0,"shipments":0}
    for _,v in extended_demo_fleet().iterrows():
        vehicle_id=str(v.Vehicle_ID)
        if vehicle_id in existing_vehicles:continue
        save_vehicle(path,{
            "vehicle_id":vehicle_id,"name":vehicle_id,"registration":"","vehicle_type":"Long van" if "LONG" in vehicle_id else "Van",
            "available":True,"capacity_kg":v.Capacity_kg,"interior_length_cm":v.Interior_Length_cm,
            "interior_width_cm":v.Interior_Width_cm,"interior_height_cm":v.Interior_Height_cm,
            "door_width_cm":v.Door_Width_cm,"door_height_cm":v.Door_Height_cm,"volume_m3":v.Volume_m3,
            "pallet_capacity":v.Pallet_Capacity,"fuel_type":v.Fuel_Type,"cost_per_km":v.Cost_Per_Km,
            "depot":v.Depot,"notes":"Extended synthetic demo vehicle; replace with verified data."})
        added["vehicles"]+=1
    for _,p in extended_demo_catalog().iterrows():
        sku=str(p.Parcel_ID)
        if sku in existing_products:continue
        save_product(path,{
            "sku":sku,"description":p.Description,"category":p.Category,"length_cm":p.Length_cm,
            "width_cm":p.Width_cm,"height_cm":p.Height_cm,"weight_kg":p.Weight_kg,
            "stackable":p.Stackable,"max_stack_weight_kg":p.Max_Stack_Weight_kg,
            "rotation":p.Rotation,"handling":p.Handling})
        added["products"]+=1
    order_rows=extended_demo_orders()
    parcel_rows=extended_demo_parcels()
    for _,o in order_rows.iterrows():
        shipment_id=str(o.Order_ID)
        if shipment_id in existing_shipments:continue
        lines=parcel_rows[parcel_rows.Order_ID.astype(str)==shipment_id]
        items=[(str(row.SKU),int(row.Quantity)) for _,row in lines.iterrows()]
        save_shipment(path,{
            "shipment_id":shipment_id,"customer_name":o.Customer_Name,"address":o.Address,
            "latitude":o.Latitude,"longitude":o.Longitude,"required_date":date.today().isoformat(),
            "priority":o.Priority,"window_start":o.Window_Start,"window_end":o.Window_End,
            "service_minutes":o.Service_Minutes,"status":"Ready",
            "notes":"Synthetic extended demo shipment; replace with verified order data."},items)
        added["shipments"]+=1
    return added


def _rows(db,sql,args=()):
    return pd.DataFrame([dict(row) for row in db.execute(sql,args).fetchall()])


def get_vehicles(path,active_only=False):
    where="WHERE active=1" if active_only else ""
    with connect(path) as db: frame=_rows(db,f"SELECT * FROM vehicles {where} ORDER BY vehicle_id")
    if frame.empty:return frame
    frame["Available_Today"]=frame.available.map(lambda x:"Yes" if x else "No")
    return frame.rename(columns={"vehicle_id":"Vehicle_ID","name":"Vehicle_Name","registration":"Registration","vehicle_type":"Vehicle_Type","capacity_kg":"Capacity_kg","interior_length_cm":"Interior_Length_cm","interior_width_cm":"Interior_Width_cm","interior_height_cm":"Interior_Height_cm","door_width_cm":"Door_Width_cm","door_height_cm":"Door_Height_cm","volume_m3":"Volume_m3","pallet_capacity":"Pallet_Capacity","fuel_type":"Fuel_Type","cost_per_km":"Cost_Per_Km","depot":"Depot","notes":"Notes"})


def get_fleet(path,available_only=False):
    vehicles=get_vehicles(path,active_only=True)
    columns=["Vehicle_ID","Available_Today","Capacity_kg","Interior_Length_cm","Interior_Width_cm","Interior_Height_cm","Door_Width_cm","Door_Height_cm","Volume_m3","Pallet_Capacity","Cost_Per_Km","Fuel_Type","Depot"]
    if vehicles.empty:return pd.DataFrame(columns=columns)
    if available_only:vehicles=vehicles[vehicles.Available_Today=="Yes"]
    return vehicles[columns].reset_index(drop=True)


def save_vehicle(path,data):
    keys=["vehicle_id","name","registration","vehicle_type","available","capacity_kg","interior_length_cm","interior_width_cm","interior_height_cm","door_width_cm","door_height_cm","volume_m3","pallet_capacity","fuel_type","cost_per_km","depot","notes"]
    values=[data.get(key,"") for key in keys]
    values[4]=int(bool(values[4])); values.extend([1,datetime.now().isoformat(timespec="seconds")])
    with connect(path) as db:
        vehicle_id=str(data.get("vehicle_id","")).strip()
        registration=str(data.get("registration","")).strip()
        capacity=float(data.get("capacity_kg",0) or 0)
        length=float(data.get("interior_length_cm",0) or 0)
        width=float(data.get("interior_width_cm",0) or 0)
        height=float(data.get("interior_height_cm",0) or 0)
        door_width=float(data.get("door_width_cm",0) or 0)
        door_height=float(data.get("door_height_cm",0) or 0)
        volume=float(data.get("volume_m3",0) or 0)
        if not vehicle_id or min(capacity,length,width,height)<=0:
            raise ValueError("Enter a vehicle ID and positive payload and compartment dimensions.")
        if (door_width and door_width>width) or (door_height and door_height>height):
            raise ValueError("Door clear dimensions cannot exceed the interior compartment dimensions.")
        geometric_volume=length*width*height/1_000_000
        if volume<0 or volume>geometric_volume+1e-9:
            raise ValueError("Usable volume must be between zero and the compartment's geometric volume ({:.2f} m³).".format(geometric_volume))
        if registration:
            duplicate=db.execute("SELECT vehicle_id FROM vehicles WHERE lower(trim(registration))=lower(?) AND vehicle_id<>? AND active=1 LIMIT 1",(registration,vehicle_id)).fetchone()
            if duplicate:
                raise ValueError("Registration / plate is already assigned to vehicle {}.".format(duplicate["vehicle_id"]))
        db.execute("""INSERT INTO vehicles(vehicle_id,name,registration,vehicle_type,available,capacity_kg,interior_length_cm,interior_width_cm,interior_height_cm,door_width_cm,door_height_cm,volume_m3,pallet_capacity,fuel_type,cost_per_km,depot,notes,active,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(vehicle_id) DO UPDATE SET name=excluded.name,registration=excluded.registration,vehicle_type=excluded.vehicle_type,available=excluded.available,capacity_kg=excluded.capacity_kg,interior_length_cm=excluded.interior_length_cm,interior_width_cm=excluded.interior_width_cm,interior_height_cm=excluded.interior_height_cm,door_width_cm=excluded.door_width_cm,door_height_cm=excluded.door_height_cm,volume_m3=excluded.volume_m3,pallet_capacity=excluded.pallet_capacity,fuel_type=excluded.fuel_type,cost_per_km=excluded.cost_per_km,depot=excluded.depot,notes=excluded.notes,active=1,updated_at=excluded.updated_at""",values)


def archive_vehicle(path,vehicle_id):
    with connect(path) as db: db.execute("UPDATE vehicles SET active=0,updated_at=? WHERE vehicle_id=?",(datetime.now().isoformat(timespec="seconds"),vehicle_id))


def get_products(path,active_only=True):
    where="WHERE active=1" if active_only else ""
    with connect(path) as db: return _rows(db,f"SELECT * FROM parcel_catalog {where} ORDER BY sku").rename(columns={"sku":"SKU","description":"Description","category":"Category","length_cm":"Length_cm","width_cm":"Width_cm","height_cm":"Height_cm","weight_kg":"Weight_kg","stackable":"Stackable","max_stack_weight_kg":"Max_Stack_Weight_kg","rotation":"Rotation","handling":"Handling"})


def save_product(path,data):
    keys=["sku","description","category","length_cm","width_cm","height_cm","weight_kg","stackable","max_stack_weight_kg","rotation","handling"]
    values=[data.get(key,"") for key in keys]; values.extend([1,datetime.now().isoformat(timespec="seconds")])
    with connect(path) as db:
        db.execute("""INSERT INTO parcel_catalog(sku,description,category,length_cm,width_cm,height_cm,weight_kg,stackable,max_stack_weight_kg,rotation,handling,active,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sku) DO UPDATE SET description=excluded.description,category=excluded.category,length_cm=excluded.length_cm,width_cm=excluded.width_cm,height_cm=excluded.height_cm,weight_kg=excluded.weight_kg,stackable=excluded.stackable,max_stack_weight_kg=excluded.max_stack_weight_kg,rotation=excluded.rotation,handling=excluded.handling,active=1,updated_at=excluded.updated_at""",values)


def archive_product(path,sku):
    with connect(path) as db: db.execute("UPDATE parcel_catalog SET active=0,updated_at=? WHERE sku=?",(datetime.now().isoformat(timespec="seconds"),sku))


def list_shipments(path,statuses=None):
    with connect(path) as db:
        frame=_rows(db,"SELECT * FROM shipments ORDER BY required_date,shipment_id")
        counts=_rows(db,"SELECT shipment_id,COUNT(*) AS Parcel_Lines,SUM(quantity) AS Parcel_Units FROM shipment_items GROUP BY shipment_id")
    if frame.empty:return frame
    if statuses is not None:frame=frame[frame.status.isin(statuses)]
    if not counts.empty:frame=frame.merge(counts,left_on="shipment_id",right_on="shipment_id",how="left")
    return frame.rename(columns={"shipment_id":"Shipment_ID","customer_name":"Customer_Name","address":"Address","latitude":"Latitude","longitude":"Longitude","required_date":"Required_Date","priority":"Priority","window_start":"Window_Start","window_end":"Window_End","service_minutes":"Service_Minutes","status":"Status","notes":"Notes"}).reset_index(drop=True)


def get_shipment_lines(path,shipment_ids):
    if not shipment_ids:return pd.DataFrame()
    marks=",".join("?" for _ in shipment_ids)
    sql=f"""SELECT si.shipment_id AS Shipment_ID,s.customer_name AS Customer_Name,si.sku AS SKU,pc.description AS Description,
        si.quantity AS Quantity,pc.length_cm AS Length_cm,pc.width_cm AS Width_cm,pc.height_cm AS Height_cm,pc.weight_kg AS Weight_kg,
        pc.stackable AS Stackable,pc.max_stack_weight_kg AS Max_Stack_Weight_kg,pc.rotation AS Rotation,pc.handling AS Handling
        FROM shipment_items si JOIN shipments s ON s.shipment_id=si.shipment_id JOIN parcel_catalog pc ON pc.sku=si.sku
        WHERE si.shipment_id IN ({marks}) ORDER BY si.shipment_id,si.sku"""
    with connect(path) as db:return _rows(db,sql,tuple(shipment_ids))


def save_shipment(path,data,items):
    with connect(path) as db:
        db.execute("""INSERT INTO shipments(shipment_id,customer_name,address,latitude,longitude,required_date,priority,window_start,window_end,service_minutes,status,notes,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(data["shipment_id"],data["customer_name"],data["address"],data.get("latitude"),data.get("longitude"),str(data["required_date"]),data.get("priority","Normal"),data.get("window_start",""),data.get("window_end",""),float(data.get("service_minutes",0)),data.get("status","Ready"),data.get("notes",""),datetime.now().isoformat(timespec="seconds")))
        db.executemany("INSERT INTO shipment_items(shipment_id,sku,quantity) VALUES(?,?,?)",[(data["shipment_id"],sku,int(quantity)) for sku,quantity in items if int(quantity)>0])


def update_shipment(path,shipment_id,data,items):
    """Update an unallocated shipment and replace its parcel lines atomically."""
    with connect(path) as db:
        record=db.execute("SELECT status FROM shipments WHERE shipment_id=?",(str(shipment_id),)).fetchone()
        if record is None:
            raise ValueError("Shipment was not found.")
        allocated=db.execute("SELECT COUNT(*) FROM shipment_allocations WHERE shipment_id=?",(str(shipment_id),)).fetchone()[0]
        if allocated:
            raise ValueError("This shipment has an approved plan allocation and can no longer be edited.")
        if record["status"] in ("Dispatched","Cancelled"):
            raise ValueError("Dispatched or cancelled shipments cannot be edited.")
        db.execute("""UPDATE shipments SET customer_name=?,address=?,latitude=?,longitude=?,required_date=?,priority=?,window_start=?,window_end=?,service_minutes=?,notes=? WHERE shipment_id=?""",
            (data["customer_name"],data["address"],data.get("latitude"),data.get("longitude"),str(data["required_date"]),data.get("priority","Normal"),data.get("window_start",""),data.get("window_end",""),float(data.get("service_minutes",0)),data.get("notes",""),str(shipment_id)))
        db.execute("DELETE FROM shipment_items WHERE shipment_id=?",(str(shipment_id),))
        db.executemany("INSERT INTO shipment_items(shipment_id,sku,quantity) VALUES(?,?,?)",[(str(shipment_id),sku,int(quantity)) for sku,quantity in items if int(quantity)>0])


def shipment_has_allocations(path,shipment_id):
    with connect(path) as db:
        return db.execute("SELECT 1 FROM shipment_allocations WHERE shipment_id=? LIMIT 1",(str(shipment_id),)).fetchone() is not None


def cancel_shipment(path,shipment_id):
    """Soft-cancel an unallocated shipment without deleting its audit data."""
    with connect(path) as db:
        record=db.execute("SELECT status FROM shipments WHERE shipment_id=?",(str(shipment_id),)).fetchone()
        if record is None:
            raise ValueError("Shipment was not found.")
        allocated=db.execute("SELECT COUNT(*) FROM shipment_allocations WHERE shipment_id=?",(str(shipment_id),)).fetchone()[0]
        if allocated:
            raise ValueError("This shipment has an approved plan allocation and cannot be cancelled here.")
        if record["status"]=="Dispatched":
            raise ValueError("A dispatched shipment cannot be cancelled here.")
        db.execute("UPDATE shipments SET status='Cancelled' WHERE shipment_id=?",(str(shipment_id),))


def update_shipment_statuses(path,changes):
    with connect(path) as db:
        for shipment_id,status in changes:
            record=db.execute("SELECT status FROM shipments WHERE shipment_id=?",(str(shipment_id),)).fetchone()
            if record is None:raise ValueError("Shipment {} was not found.".format(shipment_id))
            if status==record["status"]:continue
            if record["status"] in ("Planned","Dispatched","Cancelled") or status in ("Planned","Dispatched","Cancelled"):
                raise ValueError("Planned, dispatched, and cancelled are lifecycle statuses; use plan approval or the shipment cancellation action.")
            if db.execute("SELECT 1 FROM shipment_allocations WHERE shipment_id=? LIMIT 1",(str(shipment_id),)).fetchone():
                raise ValueError("Shipment {} has approved allocations and its status cannot be changed here.".format(shipment_id))
            if status not in ("Ready","Open","On hold"):
                raise ValueError("Choose Ready, Open, or On hold for a manual status update.")
        db.executemany("UPDATE shipments SET status=? WHERE shipment_id=?",[(status,shipment_id) for shipment_id,status in changes if status in ("Ready","Open","On hold")])


def approve_plan(path,plan_id,approver,parcels=None):
    """Atomically approve a plan and consume its catalog quantities, if any.

    A primary key on plan/shipment/SKU makes approval idempotent. The transaction
    rolls back in full if any selected quantity is no longer available.
    """
    now=datetime.now().isoformat(timespec="seconds")
    allocations=[]
    if parcels is not None:
        for _,row in parcels.iterrows():
            quantity=int(row["Quantity"])
            if quantity<=0:
                raise ValueError("Approved allocation quantities must be positive whole numbers.")
            allocations.append((str(plan_id),str(row["Order_ID"]),str(row["Parcel_ID"]),quantity,now))
    with connect(path) as db:
        saved=db.execute("SELECT status,summary_json FROM plans WHERE id=?",(str(plan_id),)).fetchone()
        if saved is None:
            raise ValueError("Plan record was not found. Save the plan before approval.")
        if saved["status"]=="Approved":
            raise ValueError("This plan has already been approved.")
        for pid,shipment_id,sku,quantity,allocated_at in allocations:
            current=db.execute("SELECT quantity FROM shipment_items WHERE shipment_id=? AND sku=?",(shipment_id,sku)).fetchone()
            if current is None or int(current["quantity"])<quantity:
                raise ValueError("Shipment {} no longer has {} units of {} available.".format(shipment_id,quantity,sku))
        db.executemany("INSERT INTO shipment_allocations(plan_id,shipment_id,sku,quantity,allocated_at) VALUES(?,?,?,?,?)",allocations)
        for _,shipment_id,sku,quantity,_ in allocations:
            current=db.execute("SELECT quantity FROM shipment_items WHERE shipment_id=? AND sku=?",(shipment_id,sku)).fetchone()
            if int(current["quantity"])==quantity:
                db.execute("DELETE FROM shipment_items WHERE shipment_id=? AND sku=?",(shipment_id,sku))
            else:
                db.execute("UPDATE shipment_items SET quantity=quantity-? WHERE shipment_id=? AND sku=?",(quantity,shipment_id,sku))
        for shipment_id in sorted({item[1] for item in allocations}):
            remaining=db.execute("SELECT COALESCE(SUM(quantity),0) FROM shipment_items WHERE shipment_id=?",(shipment_id,)).fetchone()[0]
            next_status="Planned" if int(remaining)==0 else "Ready"
            db.execute("UPDATE shipments SET status=? WHERE shipment_id=?",(next_status,shipment_id))
        summary=json.loads(saved["summary_json"] or "{}")
        summary["approver"]=str(approver).strip()
        db.execute("UPDATE plans SET status='Approved',summary_json=? WHERE id=?",(json.dumps(summary,default=str),str(plan_id)))
        db.execute("INSERT INTO events(plan_id,event_at,event_type,details) VALUES(?,?,?,?)",(str(plan_id),now,"Approved",json.dumps({"approved_by":str(approver).strip(),"allocated_lines":len(allocations)})))


def get_plan_history(path):
    """Return saved plans with a few useful summary fields for the history table."""
    with connect(path) as db:
        records=db.execute("SELECT id,created_at,plan_date,status,summary_json,inputs_json FROM plans ORDER BY created_at DESC,id DESC").fetchall()
    rows=[]
    for record in records:
        summary=json.loads(record["summary_json"] or "{}")
        routes=summary.get("routes",[])
        rows.append({
            "Plan_ID":record["id"],"Created":record["created_at"],"Planning_Date":record["plan_date"],
            "Approval_Status":record["status"],"Vehicles_Used":summary.get("vehicles_used",0),
            "Orders_Assigned":len({str(row.get("Order_ID")) for row in routes}),
            "Estimated_km":round(float(summary.get("total_km",0) or 0),1),
            "Method":summary.get("method",""),"Approved_By":summary.get("approver","")
        })
    return pd.DataFrame(rows,columns=["Plan_ID","Created","Planning_Date","Approval_Status","Vehicles_Used","Orders_Assigned","Estimated_km","Method","Approved_By"])


def get_plan_detail(path,plan_id):
    """Load an immutable historical plan and its original input snapshot."""
    with connect(path) as db:
        record=db.execute("SELECT * FROM plans WHERE id=?",(str(plan_id),)).fetchone()
        events=db.execute("SELECT event_at AS Event_Time,event_type AS Event,details AS Details FROM events WHERE plan_id=? ORDER BY id",(str(plan_id),)).fetchall()
    if record is None:
        return None
    return {
        "plan_id":record["id"],"created_at":record["created_at"],"plan_date":record["plan_date"],
        "status":record["status"],"plan":json.loads(record["summary_json"] or "{}"),
        "inputs":json.loads(record["inputs_json"] or "{}"),"events":pd.DataFrame([dict(row) for row in events])
    }


def get_depot(path):
    with connect(path) as db: row=db.execute("SELECT * FROM depot_settings WHERE id=1").fetchone()
    if row is None:return {"name":"Main Depot","address":"","latitude":None,"longitude":None,"loading_cutoff":""}
    return dict(row)


def save_depot(path,data):
    with connect(path) as db:db.execute("INSERT INTO depot_settings(id,name,address,latitude,longitude,loading_cutoff,updated_at) VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,address=excluded.address,latitude=excluded.latitude,longitude=excluded.longitude,loading_cutoff=excluded.loading_cutoff,updated_at=excluded.updated_at",(data["name"],data["address"],data.get("latitude"),data.get("longitude"),data.get("loading_cutoff",""),datetime.now().isoformat(timespec="seconds")))


def get_settings(path):
    with connect(path) as db:rows=db.execute("SELECT setting_key,setting_value FROM app_settings").fetchall()
    return {row["setting_key"]:row["setting_value"] for row in rows}


def save_settings(path,values):
    with connect(path) as db:db.executemany("INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at",[(key,str(value),datetime.now().isoformat(timespec="seconds")) for key,value in values.items()])
