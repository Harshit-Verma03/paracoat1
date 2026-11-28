"""Paracoat Dispatch Studio — Streamlit prototype."""
from datetime import date, datetime, time as dt_time
import io
import json
import os
import sqlite3
from pathlib import Path
import pandas as pd
import streamlit as st
from paracoat.validation import validate_inputs
from paracoat.routing import build_matrix
from paracoat.routing import build_route_geometry
from paracoat.packing import check_fit, orientation_options
from paracoat.dispatch import build_dispatch, reassign_order, change_parcel_orientation, move_parcel_position, move_stop
from paracoat.workbooks import workbook_template, extended_demo_workbook, export_plan, load_planning_workbook
from paracoat.sample_data import extended_demo_orders, extended_demo_parcels, extended_demo_fleet, sample_depot
from paracoat.repository import initialize as initialize_catalog, seed_extended_demo_catalog, get_depot, get_fleet, get_settings, get_shipment_lines, list_shipments, approve_plan, get_plan_history, get_plan_detail
from paracoat.catalog_views import render_overview, render_fleet, render_parcels, render_shipments, render_settings
from paracoat.loading_views import render_loading_layer, render_loading_3d

ROOT=Path(__file__).parent
DB_PATH=Path(os.environ.get("PARACOAT_DB_PATH",str(ROOT/"paracoat.db")))
st.set_page_config(page_title="Paracoat Dispatch Studio",page_icon="🚚",layout="wide",initial_sidebar_state="expanded")

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
html,body,[class*="css"]{font-family:'DM Sans',sans-serif} h1,h2,h3{font-family:'Manrope',sans-serif;letter-spacing:-.035em}
.block-container{padding-top:1.35rem;padding-bottom:2.2rem;max-width:1480px}.eyebrow{color:#16745b;font-size:.69rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}
.stMetric{background:#fff;border:1px solid #e4ebe6;border-radius:12px;padding:10px 14px;box-shadow:0 2px 8px rgba(22,35,30,.025)}.stMetric label{font-size:.78rem;color:#718078;font-weight:600}.stMetric [data-testid="stMetricValue"]{font-family:'Manrope',sans-serif;font-weight:750;color:#16231e}
.hero-panel{background:linear-gradient(112deg,#103c31,#16745b 68%,#23866c);border-radius:20px;padding:30px 34px;color:white;margin:12px 0 22px;box-shadow:0 14px 34px rgba(16,60,49,.16)}.hero-kicker{font-size:.7rem;letter-spacing:.16em;font-weight:800;color:#bfe3d5}.hero-title{font:800 1.8rem 'Manrope',sans-serif;letter-spacing:-.04em;margin:8px 0}.hero-copy{color:#e0f1e9;max-width:700px;font-size:.94rem}
.stButton>button{border-radius:9px;font-weight:650;min-height:2.45rem}.stDownloadButton>button{border-radius:9px;font-weight:650}.small-note{font-size:.82rem;color:#69776f}.step{color:#63746a;font-size:.9rem}
</style>""",unsafe_allow_html=True)

def init_db():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created_at TEXT, plan_date TEXT, status TEXT, summary_json TEXT, inputs_json TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT, event_at TEXT, event_type TEXT, details TEXT)")

def read_upload(upload, sheet=None):
    if upload is None: return None
    try:
        name=upload.name.lower()
        if name.endswith(".csv"): return pd.read_csv(upload)
        book=pd.ExcelFile(upload)
        target=sheet or book.sheet_names[0]
        return pd.read_excel(upload,sheet_name=target)
    except Exception as exc:
        st.error(f"Could not read {upload.name}: {exc}")
        return None

def read_workbook(upload):
    return load_planning_workbook(upload)

@st.cache_data(ttl=3600,show_spinner=False)
def cached_route_geometry(points,base_url):
    return build_route_geometry(list(points),base_url)

def save_plan(plan_id,plan_date,plan,inputs,status):
    with sqlite3.connect(DB_PATH) as conn:
        cursor=conn.execute("INSERT INTO plans VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET plan_date=excluded.plan_date,status=excluded.status,summary_json=excluded.summary_json,inputs_json=excluded.inputs_json WHERE plans.status!='Approved'",(plan_id,datetime.now().isoformat(timespec="seconds"),str(plan_date),status,json.dumps(plan,default=str),json.dumps(inputs,default=str)))
        if cursor.rowcount==0:
            raise ValueError("Approved plans are locked and cannot be overwritten.")
        conn.execute("INSERT INTO events(plan_id,event_at,event_type,details) VALUES (?,?,?,?)",(plan_id,datetime.now().isoformat(timespec="seconds"),status,json.dumps({"method":plan["method"],"status":plan["status"]})))

init_db()
initialize_catalog(DB_PATH)
if "plan" not in st.session_state: st.session_state.plan=None
if "edits" not in st.session_state: st.session_state.edits=0

with st.sidebar:
    st.markdown("## 🟢 PARACOAT")
    st.caption("LOGISTICS CONTROL CENTER")
    st.divider()
    if "_navigate_to" in st.session_state:
        st.session_state["workspace_nav"]=st.session_state.pop("_navigate_to")
    page=st.radio("WORKSPACE",["Overview","Plan dispatch","Shipments","Parcel catalog","Fleet","Plan history","Settings","About"],label_visibility="visible",key="workspace_nav")
    st.divider()
    st.markdown("<div class='small-note'>Designed for dispatcher review. Every plan requires human approval before use.</div>",unsafe_allow_html=True)
    st.download_button("Download input workbook template",data=workbook_template(),file_name="paracoat_input_template.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",width="stretch")
    st.download_button("Download extended demo data",data=extended_demo_workbook(),file_name="paracoat_extended_demo.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",width="stretch")
    if st.button("Add demo data to catalogs",help="Append the synthetic vehicles, parcel types, and shipments. Existing records are kept.",width="stretch"):
        st.session_state["extended_demo_seed_result"]=seed_extended_demo_catalog(DB_PATH)
        st.rerun()
    seed_result=st.session_state.pop("extended_demo_seed_result",None)
    if seed_result:
        st.sidebar.success("Added {} vehicles, {} parcel types, and {} shipments. Existing records were kept.".format(
            seed_result["vehicles"],seed_result["products"],seed_result["shipments"]))

if page=="Overview":render_overview(DB_PATH);st.stop()
if page=="Fleet":render_fleet(DB_PATH);st.stop()
if page=="Parcel catalog":render_parcels(DB_PATH);st.stop()
if page=="Shipments":render_shipments(DB_PATH);st.stop()
if page=="Settings":render_settings(DB_PATH);st.stop()
if page=="About":
    st.markdown("<div class='eyebrow'>PARACOAT · OPERATIONS PLATFORM</div>",unsafe_allow_html=True)
    st.title("Dispatch planning workspace")
    st.write("Maintain fleet and parcel master data, build customer shipments, select today’s shipment lines, and prepare dispatcher-approved routes.")
    st.info("This initial release includes route planning, a rotatable 3D parcel-box preview, and geometric fit estimates. Advanced 3D VFX and certified loading-sequence analysis are not included; this application does not certify cargo safety.")
    st.markdown("### Included workspaces\n\n- **Fleet:** vehicle capacity, cargo dimensions, door clearance, availability, fuel, cost and notes.\n- **Parcel catalog:** SKU dimensions, weight, stackability, maximum stack load, orientation and handling.\n- **Shipments:** customer delivery records, parcel lines and quantities, requested dates, time windows and lifecycle status.\n- **Daily dispatch:** choose shipment lines for today, optimize vehicle routes, review capacity and geometric fit in 3D/2D, edit, approve and export.\n- **Extended demo:** 24 synthetic deliveries, 147 variable-size parcel units, and 8 available vehicles; demo data can also be downloaded as a workbook.")
    st.markdown("### Current operational boundaries\n\nSingle depot and one-day plans. Address geocoding, live GPS, dynamic traffic, multi-day optimization, split deliveries, ERP connections and certified load-safety checks are not included yet.")
    st.stop()

if page=="Plan history":
    st.markdown("<div class='eyebrow'>Records · Local prototype database</div>",unsafe_allow_html=True)
    st.title("Plan history")
    history=get_plan_history(DB_PATH)
    if history.empty: st.info("No planning runs saved yet. Create a plan to see it here.")
    else:
        st.dataframe(history,width="stretch",hide_index=True)
        labels={str(row.Plan_ID):"{} · {} · {}".format(row.Plan_ID,row.Planning_Date,row.Approval_Status) for _,row in history.iterrows()}
        selected_plan=st.selectbox("Open saved plan",history.Plan_ID.astype(str).tolist(),format_func=lambda value:labels[value])
        detail=get_plan_detail(DB_PATH,selected_plan)
        if detail:
            saved=detail["plan"]
            st.markdown("### Plan details")
            a,b,c,d=st.columns(4)
            a.metric("Status",detail["status"])
            b.metric("Vehicles used",int(saved.get("vehicles_used",0) or 0))
            c.metric("Estimated distance", "{:.1f} km".format(float(saved.get("total_km",0) or 0)))
            d.metric("Estimated duration", "{:.1f} h".format(float(saved.get("total_minutes",0) or 0)/60))
            st.caption("{} · {} · {} · {}".format(detail["plan_id"],saved.get("method","Unknown method"),saved.get("objective",""),saved.get("matrix_note","")))
            details_tabs=st.tabs(["Route stops","Exceptions","Original inputs","Approval & activity"])
            routes=pd.DataFrame(saved.get("routes",[]))
            with details_tabs[0]:
                if routes.empty:st.info("This saved plan has no assigned route stops.")
                else:st.dataframe(routes.drop(columns=["_matrix_index"],errors="ignore"),width="stretch",hide_index=True)
            with details_tabs[1]:
                exceptions=pd.DataFrame(saved.get("unassigned",[]))
                if exceptions.empty:st.success("No unassigned orders were recorded.")
                else:st.dataframe(exceptions,width="stretch",hide_index=True)
            with details_tabs[2]:
                inputs=detail["inputs"]
                input_tabs=st.tabs(["Orders","Parcels","Fleet"])
                for tab,key in zip(input_tabs,["orders","parcels","fleet"]):
                    with tab:
                        frame=pd.DataFrame(inputs.get(key,[]))
                        if frame.empty:st.info("No saved {} snapshot.".format(key))
                        else:st.dataframe(frame,width="stretch",hide_index=True)
            with details_tabs[3]:
                if saved.get("approver"):st.success("Approved by {}".format(saved["approver"]))
                else:st.info("This plan has not been approved.")
                events=detail["events"].copy()
                if not events.empty:
                    events["Details"]=events.Details.map(lambda value:json.dumps(json.loads(value),ensure_ascii=False) if value else "")
                    st.dataframe(events,width="stretch",hide_index=True)
            inputs=detail["inputs"]
            parcels=pd.DataFrame(inputs.get("parcels",[]));fleet=pd.DataFrame(inputs.get("fleet",[]))
            if not parcels.empty and not fleet.empty:
                export=export_plan(saved,parcels,fleet,detail["plan_id"],detail["plan_date"],int(saved.get("manual_edits",0) or 0),saved.get("approver",""))
                st.download_button("Download this saved dispatch workbook",data=export,file_name="paracoat_{}.xlsx".format(detail["plan_id"]),mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",key="history_export_"+str(detail["plan_id"]))
            else:st.warning("This saved plan does not have enough input data to recreate its dispatch workbook.")
    st.caption("History is stored locally in paracoat.db. Back it up before removing local project data.")
    st.stop()

if page!="Plan dispatch":st.stop()

st.markdown("<div class='eyebrow'>DAILY OPERATIONS · DISPATCH DESK</div>",unsafe_allow_html=True)
st.title("Plan dispatch")
st.write("Choose the shipments and parcel quantities leaving today, then build a capacity-aware vehicle and route plan.")
left,right=st.columns([1.15,.85],gap="large")
with left:
    st.markdown("### 1 · Select today’s freight")
    source=st.radio("Planning source",["Saved shipment catalog","Extended demo scenario","Upload workbook / CSV"],horizontal=True)
    # Initialize every frame before branching so empty queues and partial uploads
    # reach the readiness check without an UnboundLocalError/NameError.
    orders=parcels=fleet=depot=None
    if source=="Saved shipment catalog":
        vehicles=get_fleet(DB_PATH)
        depot_record=get_depot(DB_PATH)
        depot=pd.DataFrame([{"Depot":depot_record["name"],"Address":depot_record["address"],"Latitude":depot_record["latitude"],"Longitude":depot_record["longitude"]}])
        fleet=vehicles
        queue=list_shipments(DB_PATH,statuses=["Ready","Open","Planned"])
        if queue.empty:
            st.info("No ready shipments. Create one from the Shipments workspace.")
            orders=parcels=None
        else:
            queue_ids=queue.Shipment_ID.astype(str).tolist()
            due=queue[(queue.Status.isin(["Ready","Open"]))&(queue.Required_Date.astype(str)<=date.today().isoformat())].Shipment_ID.astype(str).tolist()
            defaults=due or queue[queue.Status.isin(["Ready","Open"])].Shipment_ID.astype(str).tolist()
            prefill=st.session_state.pop("dispatch_prefill_shipments",None)
            selected_default=[value for value in (prefill if prefill is not None else defaults) if value in queue_ids]
            selected_shipments=st.multiselect("Shipments to plan",queue_ids,default=selected_default,format_func=lambda value:f"{value} · {queue.loc[queue.Shipment_ID.astype(str)==value,'Customer_Name'].iloc[0]}",key="dispatch_shipment_select")
            st.caption("Choose shipments, then set the quantity of each catalog parcel to load today. Set quantity to zero to hold a line back.")
            lines=get_shipment_lines(DB_PATH,selected_shipments)
            if lines.empty:
                st.info("Select one or more shipments with parcel lines.");orders=parcels=None
            else:
                editor=lines.rename(columns={"Quantity":"Available_Quantity"}).copy()
                editor["Quantity_Today"]=editor.Available_Quantity
                editable_lines=st.data_editor(editor,key="today_shipment_lines_"+str(abs(hash(tuple(selected_shipments)))),width="stretch",hide_index=True,disabled=[c for c in editor.columns if c!="Quantity_Today"],column_config={"Quantity_Today":st.column_config.NumberColumn("Quantity today",min_value=0,max_value=100000,step=1,format="%d")})
                if (editable_lines.Quantity_Today<0).any() or ((editable_lines.Quantity_Today%1)!=0).any() or (editable_lines.Quantity_Today>editable_lines.Available_Quantity).any():
                    st.error("Today’s quantity must be a whole number from zero up to the shipment quantity.");orders=parcels=None
                else:
                    sending=editable_lines[editable_lines.Quantity_Today>0].copy()
                    active_ids=sending.Shipment_ID.astype(str).unique().tolist()
                    all_orders=queue[queue.Shipment_ID.astype(str).isin(active_ids)].copy()
                    orders=all_orders.rename(columns={"Shipment_ID":"Order_ID"})[["Order_ID","Customer_Name","Address","Latitude","Longitude","Priority","Window_Start","Window_End","Service_Minutes"]].reset_index(drop=True)
                    parcels=sending.rename(columns={"Shipment_ID":"Order_ID","SKU":"Parcel_ID","Quantity_Today":"Quantity"})[["Order_ID","Parcel_ID","Quantity","Length_cm","Width_cm","Height_cm","Weight_kg","Stackable","Max_Stack_Weight_kg","Rotation"]].reset_index(drop=True)
                    st.caption(f"Selected freight · {len(orders)} shipments · {int(parcels.Quantity.sum())} parcel units · {float((parcels.Weight_kg*parcels.Quantity).sum()):,.1f} kg")
                    if orders.empty:orders=parcels=None
    elif source=="Extended demo scenario":
        orders=extended_demo_orders()
        all_demo_parcels=extended_demo_parcels()
        fleet=extended_demo_fleet()
        depot=sample_depot()
        demo_ids=orders.Order_ID.astype(str).tolist()
        selected_demo=st.multiselect("Synthetic shipments",demo_ids,default=demo_ids,
            format_func=lambda value:"{} · {}".format(value,orders.loc[orders.Order_ID.astype(str)==value,"Customer_Name"].iloc[0]),
            key="extended_demo_shipments")
        parcels=all_demo_parcels[all_demo_parcels.Order_ID.astype(str).isin(selected_demo)].copy()
        if not parcels.empty:
            st.caption("Synthetic dataset · 24 delivery points · 8 available vehicles · variable parcel sizes, quantities, orientations, priorities, and delivery windows. These records are not saved to your catalog.")
            demo_editor=parcels[["Order_ID","Parcel_ID","SKU","Quantity","Length_cm","Width_cm","Height_cm","Weight_kg","Stackable","Rotation"]].copy()
            edited_demo=st.data_editor(demo_editor,key="extended_demo_quantities_"+str(abs(hash(tuple(selected_demo)))),width="stretch",hide_index=True,
                disabled=[column for column in demo_editor.columns if column!="Quantity"])
            if (edited_demo.Quantity<0).any() or ((edited_demo.Quantity%1)!=0).any():
                st.error("Demo parcel quantities must be whole non-negative numbers.");orders=parcels=None
            else:
                parcels["Quantity"]=edited_demo.Quantity.astype(int).values
                parcels=parcels[parcels.Quantity>0].copy()
                active_ids=parcels.Order_ID.astype(str).unique().tolist()
                orders=orders[orders.Order_ID.astype(str).isin(active_ids)].reset_index(drop=True)
                if parcels.empty:orders=parcels=None
                else:
                    st.caption("Selected synthetic freight · {} shipments · {} parcel units · {:,.1f} kg".format(
                        len(orders),int(parcels.Quantity.sum()),float((parcels.Weight_kg*parcels.Quantity).sum())))
        else:
            st.info("Select one or more synthetic shipments.");orders=parcels=None
    else:
        source_format=st.radio("Upload format",["One Excel workbook","Four CSV files"],horizontal=True)
        if source_format=="One Excel workbook":
            upload=st.file_uploader("Planning workbook",type=["xlsx"],key="planning_workbook",help="Workbook sheets: Orders, Parcels, Fleet and Depot.")
            if upload:
                try:
                    data=read_workbook(upload)
                    orders,parcels,fleet,depot=(data[name] for name in ["Orders","Parcels","Fleet","Depot"])
                except Exception as exc:
                    st.error("Could not read this workbook. Check the four required sheet names. Details: "+str(exc));orders=parcels=fleet=depot=None
            else:orders=parcels=fleet=depot=None
        else:
            up_orders=st.file_uploader("Orders CSV",type=["csv"],key="orders")
            up_parcels=st.file_uploader("Parcels CSV",type=["csv"],key="parcels")
            up_fleet=st.file_uploader("Fleet CSV",type=["csv"],key="fleet")
            up_depot=st.file_uploader("Depot CSV",type=["csv"],key="depot")
            orders=read_upload(up_orders) if up_orders else None;parcels=read_upload(up_parcels) if up_parcels else None;fleet=read_upload(up_fleet) if up_fleet else None;depot=read_upload(up_depot) if up_depot else None
        if all(x is not None for x in [orders,parcels,fleet,depot]):st.caption("Units: kg and cm. Use verified decimal-degree coordinates.")
with right:
    st.markdown("### 2 · Set planning rules")
    plan_date=st.date_input("Planning date",value=date.today())
    planning_defaults=get_settings(DB_PATH)
    objective=st.selectbox("Primary objective",["Distance","Travel time"],index=0 if planning_defaults.get("default_objective","Distance")=="Distance" else 1,help="The optimizer focuses on one primary objective and respects configured hard constraints.")
    routing_mode=st.selectbox("Travel estimate source",["Road estimate (OSRM demo server)","Straight-line estimate"],help="The public demo router is for prototyping only. Configure an approved provider before deployment.")
    routing_url=st.text_input("Routing service URL",value=planning_defaults.get("routing_url","https://router.project-osrm.org"),help="Use a company-approved routing endpoint for real planning.").strip().rstrip("/")
    try:departure_default=datetime.strptime(planning_defaults.get("default_departure","08:00"),"%H:%M").time()
    except ValueError:departure_default=dt_time(8,0)
    departure_time=st.time_input("Route departure time",value=departure_default,help="Used for delivery-window checks.").strftime("%H:%M")
    time_limit=st.slider("Optimizer search time",2,30,20,help="The time limit covers assignment, delivery-order packing, and route checks. Larger scenarios may return a partial plan when the search ends.")
    st.caption("Capacity is checked in kilograms. Parcel dimensions use centimetres. Window times use local 24-hour HH:MM.")

if orders is None or parcels is None or fleet is None or depot is None:
    st.info("Choose shipment records or upload all planning files before building a plan.")
    st.markdown("**Tip:** add vehicles in Fleet, create delivery records in Shipments, and keep reusable dimensions in Parcel catalog.")
    st.stop()

orders,parcels,fleet,depot,errors,warnings=validate_inputs(orders,parcels,fleet,depot)
input_signature=(tuple(int(pd.util.hash_pandas_object(frame,index=True).sum()) for frame in (orders,parcels,fleet,depot)),str(plan_date),objective,routing_mode,routing_url,departure_time,int(time_limit))
if st.session_state.get("plan") and st.session_state.get("plan_signature") != input_signature:
    st.session_state.plan=None
    st.session_state.plan_signature=None
    st.warning("Planning inputs or settings changed. Build a fresh plan before reviewing or exporting.")
with st.expander("Review uploaded data",expanded=False):
    tabs=st.tabs(["Orders","Parcels","Fleet","Depot"])
    for tab,frame in zip(tabs,[orders,parcels,fleet,depot]):
        with tab: st.dataframe(frame,width="stretch",hide_index=True)
if errors:
    st.error("Resolve the following input issues before planning:\n\n"+"\n".join("• "+x for x in errors))
    st.stop()
for warning in warnings: st.warning(warning)
st.success(f"Inputs ready · {len(orders)} orders · {int(parcels.Quantity.sum())} parcels · {int(fleet.Available_Today.astype(str).str.lower().isin(['yes','true','1','available']).sum())} available vehicles")

st.markdown("### 3 · Generate and review the plan")
if st.button("Build dispatch plan",type="primary",width="stretch"):
    missing=orders[orders.Latitude.isna()|orders.Longitude.isna()]
    depot_lat=pd.to_numeric(depot.get("Latitude",pd.Series(dtype=float)),errors="coerce")
    depot_lon=pd.to_numeric(depot.get("Longitude",pd.Series(dtype=float)),errors="coerce")
    if not missing.empty or depot.empty or depot_lat.empty or depot_lon.empty or pd.isna(depot_lat.iloc[0]) or pd.isna(depot_lon.iloc[0]):
        st.error("Planning requires coordinates for every delivery and the depot. Add verified coordinates to the input files before generating routes.")
        st.stop()
    dep=(float(depot_lat.iloc[0]),float(depot_lon.iloc[0]))
    points=[dep]+[(float(r.Latitude),float(r.Longitude)) for _,r in orders.iterrows()]
    with st.spinner("Building travel estimates and optimizing vehicle routes…"):
        distances,durations,matrix_note=build_matrix(points,routing_mode,routing_url)
        plan=build_dispatch(orders,parcels,fleet,distances,durations,objective,time_limit,departure_time)
        plan["matrix_note"]=matrix_note
        plan["distances"]=distances; plan["durations"]=durations
        plan["departure_time"]=departure_time; plan["routing_mode"]=routing_mode; plan["routing_url"]=routing_url
        plan["signature"]=input_signature
        plan["map_points"]={str(r.Order_ID):[float(r.Latitude),float(r.Longitude)] for _,r in orders.iterrows()}; plan["map_depot"]=[dep[0],dep[1]]
        st.session_state.plan=plan; st.session_state.edits=0; st.session_state.plan_id="PAR-"+datetime.now().strftime("%y%m%d-%H%M%S-%f")
        st.session_state.plan_signature=input_signature
        st.session_state.inputs={"orders":orders.to_dict("records"),"parcels":parcels.to_dict("records"),"fleet":fleet.to_dict("records"),"catalog_source":source=="Saved shipment catalog"}; save_plan(st.session_state.plan_id,plan_date,plan,st.session_state.inputs,"Draft")
        st.rerun()

plan=st.session_state.plan
if not plan:
    st.caption("Your plan will appear here after generation. The dispatcher stays in control of every assignment.")
    st.stop()
st.caption(f"Plan {st.session_state.get('plan_id','')} · {plan['method']} · {plan['matrix_note']}")
if str(plan["status"]).startswith("Review required"):
    st.error(plan["status"])
else: st.success(plan["status"])
routes=pd.DataFrame(plan["routes"]); unassigned=pd.DataFrame(plan["unassigned"])
total_parcels=int(parcels.Quantity.sum()); total_weight=float((parcels.Weight_kg*parcels.Quantity).sum())
metrics=st.columns(5)
values=[(str(plan["vehicles_used"]),"Vehicles used"),(str(len(routes.Order_ID.unique()) if not routes.empty else 0),"Orders assigned"),(str(len(unassigned)),"Unassigned orders"),(f"{plan['total_km']:.1f} km","Estimated distance"),(f"{total_parcels} · {total_weight:.0f} kg","Parcels · total weight")]
for col,(val,label) in zip(metrics,values): col.markdown(f"<div class='metric-card'><div class='metric-label'>{label}</div><div class='metric-value'>{val}</div></div>",unsafe_allow_html=True)

route_tab,map_tab,load_tab,edit_tab,export_tab=st.tabs(["Routes & capacity","Route map","Load check","Review & edit","Approve & export"])
with route_tab:
    if routes.empty: st.info("No orders are currently assigned.")
    else:
        summary=routes.groupby("Vehicle_ID").agg(Stops=("Order_ID","count"),Orders=("Order_ID",lambda x:", ".join(map(str,x))),Assigned_kg=("Demand_kg","sum"),Route_km=("Leg_km","sum")).reset_index()
        summary["Capacity_kg"]=summary.Vehicle_ID.map(fleet.set_index("Vehicle_ID").Capacity_kg)
        if plan.get("distances"):
            returns=[]
            for vehicle_id,group in routes.groupby("Vehicle_ID"):
                last=group.sort_values("Stop").iloc[-1]
                returns.append({"Vehicle_ID":vehicle_id,"Return_km":float(plan["distances"][int(last["_matrix_index"])][0])})
            summary=summary.merge(pd.DataFrame(returns),on="Vehicle_ID",how="left")
            summary["Route_km"]=(summary["Route_km"]+summary["Return_km"].fillna(0)).round(2)
            summary=summary.drop(columns=["Return_km"])
        summary["Weight_utilization_pct"]=(100*summary.Assigned_kg/summary.Capacity_kg).round(1)
        summary["Parcel_fit"]=["Pass" if plan.get("fit_results",{}).get(str(v),{}).get("fits",False) else "Review" for v in summary.Vehicle_ID]
        st.dataframe(summary,width="stretch",hide_index=True)
        st.dataframe(routes.drop(columns=["Latitude","Longitude","_matrix_index"],errors="ignore"),width="stretch",hide_index=True)
        st.caption(f"Estimated route duration: {plan['total_minutes']/60:.1f} hours across all vehicle routes. Arrival times exclude time zone and departure-time assumptions.")
    if not unassigned.empty:
        st.markdown("#### Exceptions")
        st.dataframe(unassigned,width="stretch",hide_index=True)
with map_tab:
    if routes.empty: st.info("A route map will appear when orders are assigned.")
    else:
        try:
            import folium
            from streamlit_folium import st_folium
            center=plan["map_depot"]; fmap=folium.Map(location=center,zoom_start=10,tiles="OpenStreetMap")
            folium.Marker(center,tooltip="Depot",icon=folium.Icon(color="darkgreen",icon="home",prefix="fa")).add_to(fmap)
            palette=["#16745b","#df8c24","#4968a8","#9b5ab4","#d35454"]
            geometry_fallback=[]
            for i,(vid,group) in enumerate(routes.groupby("Vehicle_ID")):
                color=palette[i%len(palette)]; coords=[center]
                for _,row in group.iterrows():
                    pt=[float(row.Latitude),float(row.Longitude)]; coords.append(pt)
                    folium.Marker(pt,tooltip=f"Stop {int(row.Stop)} · {row.Customer_Name}",popup=f"{vid} · {row.Order_ID}",icon=folium.DivIcon(html=f"<div style='background:{color};color:white;border-radius:50%;width:27px;height:27px;text-align:center;line-height:27px;font-weight:bold;border:2px solid white'>{int(row.Stop)}</div>")).add_to(fmap)
                coords.append(center)
                if plan.get("routing_mode","").startswith("Road"):
                    try: line=cached_route_geometry(tuple(tuple(x) for x in coords),plan.get("routing_url","https://router.project-osrm.org"))
                    except Exception: line=None
                    if line is None: line=coords; geometry_fallback.append(str(vid))
                else: line=coords
                folium.PolyLine(line,color=color,weight=4,opacity=.8,tooltip=vid).add_to(fmap)
            st_folium(fmap,width=900,height=500,key="route_map")
            if not plan.get("routing_mode","").startswith("Road"):
                st.caption("Route connectors are schematic straight lines; numbered markers show the recommended visit order.")
            elif geometry_fallback:
                st.caption("Road geometry was unavailable for: "+", ".join(geometry_fallback)+". Their map connectors are schematic straight lines.")
        except ImportError: st.warning("Install folium and streamlit-folium to enable the interactive route map.")
with load_tab:
    if plan.get("load_optimizer_status"):
        st.caption("Fleet loading: {} · {} assignment candidate(s) checked · {} vehicle(s) used · {:.1f}s · CP-SAT {}{}".format(
            plan["load_optimizer_status"],plan.get("load_candidates_checked",0),plan.get("vehicles_used",0),
            float(plan.get("load_elapsed_seconds") or 0),plan.get("load_solver_status") or "N/A",
            " · CP-SAT found an optimal assignment under this model" if plan.get("vehicle_count_optimal") else " · best result within search limit"))
        st.caption("Cargo placement is validated by a deterministic geometric heuristic; its result is not a proof of globally optimal 3D packing.")
        st.caption("Repair pass: {} additional shipment(s) placed after {} incremental vehicle-load checks.".format(
            plan.get("load_repair_shipments_added",0),plan.get("load_repair_attempts",0)))
        diagnostics=plan.get("load_candidate_diagnostics",[])
        if diagnostics:
            with st.expander("Optimizer candidate diagnostics",expanded=False):
                summary=pd.DataFrame([{ "Candidate":row.get("candidate"),
                    "Shipments proposed":row.get("assigned_shipments"),
                    "Shipments packed":row.get("packed_shipments"),
                    "Vehicles proposed":row.get("vehicles_proposed"),
                    "Vehicles with validation failures":len(row.get("failed_groups",[])) }
                    for row in diagnostics])
                st.dataframe(summary,width="stretch",hide_index=True)
                group_rows=[{"Candidate":row.get("candidate"),
                    "Vehicle_ID":group.get("Vehicle_ID"),
                    "Order_IDs":", ".join(map(str,group.get("Order_IDs",[]))),
                    "Candidate validation issue":group.get("Reason","")}
                    for row in diagnostics for group in row.get("failed_groups",[])]
                if group_rows:
                    st.caption("A listed issue describes that candidate only. It does not prove that a shipment cannot fit any vehicle.")
                    st.dataframe(pd.DataFrame(group_rows),width="stretch",hide_index=True)
                repair_rows=plan.get("load_repair_rejections",[])
                if repair_rows:
                    st.markdown("**Repair attempts that did not fit**")
                    st.dataframe(pd.DataFrame(repair_rows),width="stretch",hide_index=True)
    if not plan.get("fit_results"): st.info("No parcel-fit results are available yet.")
    else:
        for vid,result in plan["fit_results"].items():
            vehicle=fleet[fleet.Vehicle_ID.astype(str)==str(vid)].iloc[0]
            vehicle_routes=routes[routes.Vehicle_ID.astype(str)==str(vid)] if not routes.empty else pd.DataFrame()
            loaded_kg=float(vehicle_routes.Demand_kg.sum()) if not vehicle_routes.empty else float(result.get("weight_kg",0))
            weight_pct=100*loaded_kg/float(vehicle.Capacity_kg) if float(vehicle.Capacity_kg)>0 else 0
            with st.expander("{} · {} · {:.0f}% payload".format(vid,"Load placed" if result.get("fits") else "Review required",weight_pct),expanded=not result.get("fits",False)):
                st.write(result.get("reason",""))
                st.caption("Payload {:.1f} / {:.1f} kg · cargo volume {:.1f}% used. Geometric placement estimates do not prove a safe loading sequence.".format(
                    loaded_kg,float(vehicle.Capacity_kg),float(result.get("volume_used_pct",0))))
                placements=pd.DataFrame([row for row in plan.get("placements",[]) if str(row.get("Vehicle_ID"))==str(vid)])
                if not placements.empty and "Load_Sequence" in placements:
                    placements=placements.sort_values("Load_Sequence")
                if placements.empty:st.info("No parcel placements were produced for this vehicle.")
                else:
                    columns=["Load_Sequence","Delivery_Stop","Vehicle_ID","Parcel_Unit_ID","Order_ID","Parcel_ID","Orientation","X_cm","Y_cm","Z_cm","Placed_Length_cm","Placed_Width_cm","Placed_Height_cm","Weight_kg"]
                    st.dataframe(placements[[c for c in columns if c in placements.columns]],width="stretch",hide_index=True)
                    st.caption("Loading plan: pack in reverse route order. Stop 1 is nearest the rear door (right end of the diagram); later stops are placed deeper toward the cab.")
                    view_3d,view_2d=st.tabs(["3D cargo preview","2D cargo layer"])
                    with view_3d:render_loading_3d(placements.to_dict("records"),vehicle,str(vid))
                    with view_2d:render_loading_layer(placements.to_dict("records"),vehicle,str(vid))
                st.caption("Positions and oriented dimensions are in centimetres. Verify access, restraint, stability, axle load, and handling before use.")
with edit_tab:
    st.markdown("Move a whole order to another available vehicle. The move is applied only if payload and parcel-fit checks pass.")
    if routes.empty and not plan["unassigned"]: st.info("There are no assigned or unassigned orders to edit.")
    else:
        assigned_ids=routes.Order_ID.astype(str).tolist() if not routes.empty else []
        unassigned_ids=[str(item["Order_ID"]) for item in plan["unassigned"]]
        order_ids=assigned_ids+unassigned_ids
        if order_ids:
            selected=st.selectbox("Order to reassign",order_ids,disabled=bool(plan.get("approver")))
            match=routes.loc[routes.Order_ID.astype(str)==selected,"Vehicle_ID"] if not routes.empty else pd.Series(dtype=object)
            current=str(match.iloc[0]) if not match.empty else "Unassigned"
            choices=fleet[fleet.Available_Today.astype(str).str.lower().isin(["yes","true","1","available"])].Vehicle_ID.astype(str).tolist()
            target=st.selectbox("Assign to vehicle",choices,index=choices.index(str(current)) if str(current) in choices else 0,disabled=bool(plan.get("approver")))
            if st.button("Apply reassignment",disabled=str(target)==str(current) or bool(plan.get("approver"))):
                try:
                    updated=reassign_order(plan,selected,target,orders,parcels,fleet,plan["distances"],plan["durations"],plan.get("departure_time","08:00"))
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.edits+=1; updated["manual_edits"]=st.session_state.edits; st.session_state.plan=updated
                    save_plan(st.session_state.plan_id,plan_date,updated,st.session_state.inputs,"Edited draft")
                    st.success("Reassignment applied and constraints rechecked."); st.rerun()
        if plan.get("placements"):
            st.markdown("#### Change a parcel unit orientation")
            unit_options={str(row["Parcel_Unit_ID"]):row for row in plan["placements"]}
            unit_id=st.selectbox("Parcel unit",list(unit_options),format_func=lambda value:
                "{} · {} · {} · {}".format(value,unit_options[value].get("Parcel_ID"),unit_options[value].get("Vehicle_ID"),unit_options[value].get("Orientation")),
                key="orientation_unit",disabled=bool(plan.get("approver")))
            selected_placement=unit_options[unit_id]
            source=parcels[(parcels.Order_ID.astype(str)==str(selected_placement["Order_ID"]))&
                           (parcels.Parcel_ID.astype(str)==str(selected_placement["Parcel_ID"]))]
            orientation_choices=[label for label,_ in orientation_options(source.iloc[0])] if not source.empty else [selected_placement["Orientation"]]
            current_orientation=str(selected_placement["Orientation"])
            selected_orientation=st.selectbox("Allowed orientation",orientation_choices,
                index=orientation_choices.index(current_orientation) if current_orientation in orientation_choices else 0,
                key="orientation_choice",disabled=bool(plan.get("approver")))
            if st.button("Apply parcel orientation",disabled=selected_orientation==current_orientation or bool(plan.get("approver"))):
                try:
                    updated=change_parcel_orientation(plan,unit_id,selected_orientation,parcels,fleet)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.edits+=1; updated["manual_edits"]=st.session_state.edits; st.session_state.plan=updated
                    save_plan(st.session_state.plan_id,plan_date,updated,st.session_state.inputs,"Edited draft")
                    st.success("Orientation applied; the vehicle load was repacked and revalidated."); st.rerun()
            st.markdown("#### Adjust a parcel unit position")
            st.caption("Set X/Y/Z in centimetres. The selected unit stays at these coordinates while other units in that vehicle may be repacked; the full load is checked before changes are saved.")
            position_unit=st.selectbox("Parcel unit to move",list(unit_options),format_func=lambda value:
                "{} · {} · {}".format(value,unit_options[value].get("Parcel_ID"),unit_options[value].get("Vehicle_ID")),
                key="position_unit",disabled=bool(plan.get("approver")))
            position=unit_options[position_unit]
            position_cols=st.columns(3)
            target_x=position_cols[0].number_input("X position (cm)",min_value=0.0,value=float(position["X_cm"]),step=1.0,format="%.1f",key="position_x_"+position_unit,disabled=bool(plan.get("approver")))
            target_y=position_cols[1].number_input("Y position (cm)",min_value=0.0,value=float(position["Y_cm"]),step=1.0,format="%.1f",key="position_y_"+position_unit,disabled=bool(plan.get("approver")))
            target_z=position_cols[2].number_input("Z position (cm)",min_value=0.0,value=float(position["Z_cm"]),step=1.0,format="%.1f",key="position_z_"+position_unit,disabled=bool(plan.get("approver")))
            position_unchanged=all(abs(float(target)-float(position[key]))<1e-6
                                   for target,key in ((target_x,"X_cm"),(target_y,"Y_cm"),(target_z,"Z_cm")))
            if st.button("Apply parcel position",disabled=position_unchanged or bool(plan.get("approver"))):
                try:
                    updated=move_parcel_position(plan,position_unit,target_x,target_y,target_z,parcels,fleet)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.edits+=1; updated["manual_edits"]=st.session_state.edits; st.session_state.plan=updated
                    save_plan(st.session_state.plan_id,plan_date,updated,st.session_state.inputs,"Edited draft")
                    st.success("Parcel position applied; the complete vehicle load was revalidated."); st.rerun()
        if not routes.empty:
            st.markdown("#### Adjust delivery sequence")
            stop_order=routes.Order_ID.astype(str).tolist()
            stop_id=st.selectbox("Stop to move",stop_order,key="stop_order_edit",disabled=bool(plan.get("approver")))
            move_col1,move_col2=st.columns(2)
            for column,direction,label in [(move_col1,"up","Move stop earlier"),(move_col2,"down","Move stop later")]:
                with column:
                    if st.button(label,key="move_"+direction,disabled=bool(plan.get("approver"))):
                        try:
                            updated=move_stop(plan,stop_id,direction,orders,plan["distances"],plan["durations"],plan.get("departure_time","08:00"),parcels,fleet)
                        except ValueError as exc: st.error(str(exc))
                        else:
                            st.session_state.edits+=1; updated["manual_edits"]=st.session_state.edits; st.session_state.plan=updated
                            save_plan(st.session_state.plan_id,plan_date,updated,st.session_state.inputs,"Edited draft")
                            st.success("Stop order updated; cargo was repacked and delivery access revalidated."); st.rerun()
    st.caption("Stop-order edits that would violate a configured delivery window are rejected.")
with export_tab:
    st.markdown("### Final dispatcher review")
    st.write("Approval is enabled only when there are no unassigned orders and every assigned vehicle passes the geometric fit estimate. A dispatcher must still confirm all operational and safety constraints.")
    current_loads=routes.groupby("Vehicle_ID").Demand_kg.sum().to_dict() if not routes.empty else {}
    overweight=[vid for vid,load in current_loads.items() if float(load)>float(fleet.loc[fleet.Vehicle_ID.astype(str)==str(vid),"Capacity_kg"].iloc[0])]
    current_fit={}
    for vid in current_loads:
        ids=routes.loc[routes.Vehicle_ID.astype(str)==str(vid),"Order_ID"].astype(str).tolist()
        current_fit[str(vid)]=check_fit(parcels[parcels.Order_ID.astype(str).isin(ids)],fleet.loc[fleet.Vehicle_ID.astype(str)==str(vid)].iloc[0])
    outstanding=(not plan.get("complete",False) or not unassigned.empty or bool(overweight)
                 or any(not r["fits"] for r in current_fit.values()))
    if overweight: st.error("Payload capacity exceeded for: "+", ".join(map(str,overweight)))
    if outstanding:
        st.warning("This is an incomplete planning result. Assign every selected shipment and resolve all fit or capacity issues before approval or dispatch export.")
    approver=st.text_input("Dispatcher name for approval record",placeholder="Name")
    approved=st.checkbox("I reviewed the assignments and understand the estimates and load-safety limitations.",disabled=outstanding or not approver.strip())
    if plan.get("approver"):
        st.success("This plan is approved and locked. Its shipment quantities have been allocated.")
    if st.button("Approve plan",type="primary",disabled=not approved or bool(plan.get("approver"))):
        try:
            save_plan(st.session_state.plan_id,plan_date,plan,st.session_state.inputs,"Draft")
            catalog_parcels=parcels if st.session_state.inputs.get("catalog_source") else None
            approve_plan(DB_PATH,st.session_state.plan_id,approver,catalog_parcels)
        except ValueError as exc:
            st.error(str(exc))
        else:
            plan["approver"]=approver.strip()
            st.session_state.plan=plan
            st.success("Plan approved. Selected shipment quantities were allocated and remaining units stay available.")
    book=export_plan(plan,parcels,fleet,st.session_state.plan_id,plan_date,st.session_state.edits,plan.get("approver", ""))
    st.download_button("Download dispatch workbook",data=book,file_name=f"paracoat_dispatch_{plan_date.isoformat()}.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",width="stretch",disabled=outstanding)
    if outstanding:st.caption("Dispatch export is enabled only after every selected shipment has a matching route and a complete validated parcel manifest.")
    else:st.caption("Workbook includes summary, vehicle capacities, route stops, parcel inputs, approval status and limitations.")
