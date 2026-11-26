"""Streamlit catalog, shipment and overview pages."""
from datetime import date, datetime, time as dt_time
import hashlib

import pandas as pd
import streamlit as st

from paracoat.repository import (
    archive_product, archive_vehicle, cancel_shipment, get_depot, get_products, get_settings,
    get_shipment_lines, get_vehicles, list_shipments, save_depot, save_product, save_settings,
    save_shipment, save_vehicle, shipment_has_allocations, update_shipment, update_shipment_statuses,
)


def _heading(eyebrow,title,description):
    st.markdown(f"<div class='eyebrow'>{eyebrow}</div>",unsafe_allow_html=True)
    st.title(title)
    st.write(description)


def _navigate(page):
    st.session_state["_navigate_to"]=page
    st.rerun()


def render_overview(db_path):
    vehicles=get_vehicles(db_path,active_only=True)
    shipments=list_shipments(db_path)
    today=date.today().isoformat()
    available=int((vehicles.Available_Today=="Yes").sum()) if not vehicles.empty else 0
    _heading("OPERATIONS · OVERVIEW","Dispatch control center","A compact daily queue for shipment selection, fleet readiness, and quick actions.")
    action1,action2,action3,action4=st.columns([1.25,1,1,1])
    with action1:
        if st.button("＋ Plan selected shipments",type="primary",width="stretch",key="overview_plan_selected",disabled=not st.session_state.get("overview_selected_shipments",[])):
            st.session_state["dispatch_prefill_shipments"]=st.session_state.get("overview_selected_shipments",[])
            _navigate("Plan dispatch")
    with action2:
        if st.button("＋ New shipment",width="stretch",key="overview_new_shipment"):_navigate("Shipments")
    with action3:
        if st.button("＋ Add vehicle",width="stretch",key="overview_add_vehicle"):_navigate("Fleet")
    with action4:
        if st.button("Parcel catalog",width="stretch",key="overview_parcel_catalog"):_navigate("Parcel catalog")

    controls=st.columns([1,1.7,1.3])
    dispatch_date=controls[0].date_input("Dispatch date",value=date.today(),key="overview_dispatch_date")
    status_options=["Ready","Open","Planned","Dispatched","On hold"]
    selected_statuses=controls[1].multiselect("Shipment statuses",status_options,default=["Ready","Open","Planned"],key="overview_status_filter")
    search=controls[2].text_input("Find shipment or customer",placeholder="Type to filter",key="overview_search").strip().lower()

    if shipments.empty:
        due_frame=shipments.copy()
    else:
        due_frame=shipments[(shipments.Required_Date.astype(str)<=dispatch_date.isoformat()) & shipments.Status.isin(selected_statuses)].copy()
        if search:
            mask=due_frame.Shipment_ID.astype(str).str.lower().str.contains(search,regex=False)|due_frame.Customer_Name.astype(str).str.lower().str.contains(search,regex=False)
            due_frame=due_frame[mask]
    ready_due=due_frame[due_frame.Status.isin(["Ready","Open"])] if not due_frame.empty else due_frame
    missing_coords=int((ready_due.Latitude.isna()|ready_due.Longitude.isna()).sum()) if not ready_due.empty else 0
    metrics=st.columns(4)
    metric_values=[(len(ready_due),"Ready to plan"),(int((due_frame.Status=="Planned").sum()) if not due_frame.empty else 0,"Planned"),("{}/{}".format(available,len(vehicles)),"Vehicles available"),(int(due_frame.Parcel_Units.fillna(0).sum()) if not due_frame.empty else 0,"Visible parcel units")]
    for column,(value,label) in zip(metrics,metric_values):column.metric(label,value)
    if missing_coords:
        st.warning("{} ready shipment(s) in this queue need delivery coordinates before route planning.".format(missing_coords))

    queue_col,fleet_col=st.columns([1.55,.85],gap="medium")
    with queue_col:
        st.subheader("Shipment work queue")
        if due_frame.empty:
            st.info("No shipments match this date and filter. Create a shipment or widen the dispatch date.")
            st.session_state["overview_selected_shipments"]=[]
        else:
            grid=due_frame[["Shipment_ID","Customer_Name","Required_Date","Priority","Parcel_Units","Status"]].copy()
            preselected=set(st.session_state.get("overview_selected_shipments",[]))
            grid.insert(0,"Plan",grid.Shipment_ID.astype(str).isin(preselected))
            data_key="overview_queue_"+hashlib.sha1((dispatch_date.isoformat()+"|"+"|".join(selected_statuses)+"|"+search).encode("utf-8")).hexdigest()[:10]
            edited=st.data_editor(grid,key=data_key,width="stretch",hide_index=True,disabled=[c for c in grid.columns if c!="Plan"],column_config={"Plan":st.column_config.CheckboxColumn("Plan",help="Select this shipment for the dispatch plan")},height=360)
            st.session_state["overview_selected_shipments"]=edited.loc[edited.Plan & edited.Status.isin(["Ready","Open"]),"Shipment_ID"].astype(str).tolist()
            st.caption("Select Ready/Open shipments to plan. Set exact parcel quantities on the dispatch screen.")
    with fleet_col:
        st.subheader("Fleet readiness")
        if vehicles.empty:st.info("No active vehicles. Add the first vehicle to enable planning.")
        else:
            view=vehicles[["Vehicle_ID","Vehicle_Name","Available_Today","Capacity_kg","Volume_m3"]].copy()
            st.dataframe(view,width="stretch",hide_index=True,height=360)
            capacity=float(vehicles.loc[vehicles.Available_Today=="Yes","Capacity_kg"].sum())
            st.caption("Available payload capacity · {:,.0f} kg".format(capacity))


def render_fleet(db_path):
    _heading("MASTER DATA · FLEET","Vehicle fleet","Add and maintain vehicles with payload limits, usable cargo dimensions, door clearance, and operating details.")
    vehicles=get_vehicles(db_path,active_only=True)
    if not vehicles.empty:
        a,b,c,d=st.columns(4)
        a.metric("Active vehicles",len(vehicles));b.metric("Available today",int((vehicles.Available_Today=="Yes").sum()))
        c.metric("Payload capacity",f"{vehicles.Capacity_kg.sum():,.0f} kg")
        d.metric("Cargo volume",f"{vehicles.Volume_m3.sum():.1f} m³")
    choices=["+ Add new vehicle"]+(vehicles.Vehicle_ID.astype(str).tolist() if not vehicles.empty else [])
    selected=st.selectbox("Vehicle record",choices,key="vehicle_record")
    current=None if selected.startswith("+") else vehicles[vehicles.Vehicle_ID.astype(str)==selected].iloc[0]
    with st.form("vehicle_form"):
        left,right=st.columns(2)
        with left:
            vehicle_id=st.text_input("Vehicle ID *",value="" if current is None else str(current.Vehicle_ID),disabled=current is not None)
            name=st.text_input("Vehicle name",value="" if current is None else str(current.Vehicle_Name))
            registration=st.text_input("Registration / plate",value="" if current is None else str(current.Registration))
            vehicle_type=st.selectbox("Vehicle type",["Van","Rigid truck","Trailer","Container","Other"],index=["Van","Rigid truck","Trailer","Container","Other"].index(str(current.Vehicle_Type)) if current is not None and str(current.Vehicle_Type) in ["Van","Rigid truck","Trailer","Container","Other"] else 0)
            available=st.checkbox("Available for planning",value=True if current is None else current.Available_Today=="Yes")
            capacity=st.number_input("Payload capacity (kg) *",min_value=0.0,value=500.0 if current is None else float(current.Capacity_kg),step=50.0)
        with right:
            st.markdown("**Usable cargo compartment · cm**")
            dims=st.columns(3)
            length=dims[0].number_input("Length",min_value=0.0,value=300.0 if current is None else float(current.Interior_Length_cm),step=10.0)
            width=dims[1].number_input("Width",min_value=0.0,value=160.0 if current is None else float(current.Interior_Width_cm),step=10.0)
            height=dims[2].number_input("Height",min_value=0.0,value=150.0 if current is None else float(current.Interior_Height_cm),step=10.0)
            doors=st.columns(2)
            door_width=doors[0].number_input("Door clear width (cm)",min_value=0.0,value=0.0 if current is None else float(current.Door_Width_cm or 0),step=5.0)
            door_height=doors[1].number_input("Door clear height (cm)",min_value=0.0,value=0.0 if current is None else float(current.Door_Height_cm or 0),step=5.0)
            volume=st.number_input("Usable volume (m³)",min_value=0.0,value=(length*width*height/1_000_000) if current is None else float(current.Volume_m3 or 0),step=.1,format="%.2f")
            pallet_capacity=st.number_input("Pallet capacity (optional)",min_value=0.0,value=0.0 if current is None else float(current.Pallet_Capacity or 0),step=1.0)
        more1,more2,more3=st.columns(3)
        fuel=more1.text_input("Fuel / energy type",value="" if current is None else str(current.Fuel_Type))
        cost=more2.number_input("Operating cost per km (optional)",min_value=0.0,value=0.0 if current is None else float(current.Cost_Per_Km or 0),step=.1)
        depot=more3.text_input("Home depot",value="Main Depot" if current is None else str(current.Depot))
        notes=st.text_area("Vehicle notes / restrictions",value="" if current is None else str(current.Notes))
        submitted=st.form_submit_button("Save vehicle",type="primary",width="stretch")
    if submitted:
        if not vehicle_id.strip() or min(capacity,length,width,height)<=0:st.error("Enter a vehicle ID and positive payload and compartment dimensions.")
        elif (door_width and door_width>width) or (door_height and door_height>height):st.error("Door clear dimensions cannot exceed the interior compartment dimensions.")
        elif volume>length*width*height/1_000_000:st.error("Usable volume cannot exceed the geometric volume of the compartment ({:.2f} m³).".format(length*width*height/1_000_000))
        else:
            try:
                save_vehicle(db_path,{"vehicle_id":vehicle_id.strip(),"name":name.strip() or vehicle_id.strip(),"registration":registration.strip(),"vehicle_type":vehicle_type,"available":available,"capacity_kg":capacity,"interior_length_cm":length,"interior_width_cm":width,"interior_height_cm":height,"door_width_cm":door_width,"door_height_cm":door_height,"volume_m3":volume,"pallet_capacity":pallet_capacity,"fuel_type":fuel,"cost_per_km":cost,"depot":depot,"notes":notes})
            except ValueError as exc:st.error(str(exc))
            else:st.success("Vehicle record saved.");st.rerun()
    if current is not None:
        if st.button("Archive selected vehicle",help="Archived vehicles are preserved in history and removed from new planning."):
            archive_vehicle(db_path,selected);st.success("Vehicle archived.");st.rerun()
    st.subheader("Active vehicle register")
    if vehicles.empty:st.info("No active vehicles.")
    else:st.dataframe(vehicles[["Vehicle_ID","Vehicle_Name","Registration","Vehicle_Type","Available_Today","Capacity_kg","Interior_Length_cm","Interior_Width_cm","Interior_Height_cm","Door_Width_cm","Door_Height_cm","Volume_m3","Pallet_Capacity","Fuel_Type","Cost_Per_Km","Depot"]],width="stretch",hide_index=True)


def render_parcels(db_path):
    _heading("MASTER DATA · PARCELS","Parcel catalog","Maintain reusable products and parcel types. Daily shipments reference these records, so dimensions and handling data stay consistent.")
    products=get_products(db_path)
    if not products.empty:
        a,b,c=st.columns(3);a.metric("Active parcel types",len(products));b.metric("Categories",products.Category.nunique());c.metric("Catalog weight range",f"{products.Weight_kg.min():g}–{products.Weight_kg.max():g} kg")
    choices=["+ Add parcel type"]+(products.SKU.astype(str).tolist() if not products.empty else [])
    selected=st.selectbox("Parcel record",choices,key="parcel_record")
    current=None if selected.startswith("+") else products[products.SKU.astype(str)==selected].iloc[0]
    with st.form("parcel_form"):
        c1,c2,c3=st.columns(3)
        sku=c1.text_input("SKU / parcel ID *",value="" if current is None else str(current.SKU),disabled=current is not None)
        description=c2.text_input("Description *",value="" if current is None else str(current.Description))
        category=c3.text_input("Category",value="General" if current is None else str(current.Category))
        dims=st.columns(4)
        length=dims[0].number_input("Length (cm) *",min_value=0.0,value=40.0 if current is None else float(current.Length_cm),step=5.0)
        width=dims[1].number_input("Width (cm) *",min_value=0.0,value=30.0 if current is None else float(current.Width_cm),step=5.0)
        height=dims[2].number_input("Height (cm) *",min_value=0.0,value=25.0 if current is None else float(current.Height_cm),step=5.0)
        weight=dims[3].number_input("Weight per parcel (kg) *",min_value=0.0,value=5.0 if current is None else float(current.Weight_kg),step=.5)
        a,b,c=st.columns(3)
        stackable=a.selectbox("Stackable",["Yes","No"],index=0 if current is None or current.Stackable=="Yes" else 1)
        max_stack=b.number_input("Maximum load above (kg; 0 = unknown)",min_value=0.0,value=0.0 if current is None else float(current.Max_Stack_Weight_kg or 0),step=5.0)
        rotation=c.selectbox("Allowed orientation",["Any","Upright only","No height rotation"],index=["Any","Upright only","No height rotation"].index(str(current.Rotation)) if current is not None and str(current.Rotation) in ["Any","Upright only","No height rotation"] else 0)
        handling=st.text_area("Handling instructions",value="" if current is None else str(current.Handling))
        submitted=st.form_submit_button("Save parcel type",type="primary",width="stretch")
    if submitted:
        if not sku.strip() or not description.strip() or min(length,width,height,weight)<=0:st.error("Enter a unique SKU, description, positive dimensions, and positive parcel weight.")
        else:
            save_product(db_path,{"sku":sku.strip(),"description":description.strip(),"category":category,"length_cm":length,"width_cm":width,"height_cm":height,"weight_kg":weight,"stackable":stackable,"max_stack_weight_kg":max_stack,"rotation":rotation,"handling":handling})
            st.success("Parcel catalog record saved.");st.rerun()
    if current is not None:
        if st.button("Archive selected parcel type",help="Archived SKUs remain in historical shipment records but cannot be selected for new shipments."):
            archive_product(db_path,selected);st.success("Parcel type archived.");st.rerun()
    st.subheader("Active parcel register")
    if products.empty:st.info("No active parcel records.")
    else:st.dataframe(products[["SKU","Description","Category","Length_cm","Width_cm","Height_cm","Weight_kg","Stackable","Max_Stack_Weight_kg","Rotation","Handling"]],width="stretch",hide_index=True)


def render_shipments(db_path):
    _heading("DAILY OPERATIONS · SHIPMENTS","Shipment book","Create customer deliveries, attach catalog parcel types, and manage their dispatch status.")
    products=get_products(db_path)
    if products.empty:
        st.warning("Add parcel types to the Parcel catalog before creating shipments.")
        return
    with st.expander("＋ Create shipment",expanded=False):
        options=products.SKU.astype(str).tolist()
        selected=st.multiselect("Parcel types",options,format_func=lambda sku:f"{sku} · {products.loc[products.SKU.astype(str)==sku,'Description'].iloc[0]}",key="shipment_create_skus")
        chosen=products[products.SKU.astype(str).isin(selected)]
        lines=chosen[["SKU","Description","Length_cm","Width_cm","Height_cm","Weight_kg"]].copy()
        lines["Quantity"]=1
        if not lines.empty:
            selection_key=hashlib.sha1("|".join(sorted(selected)).encode("utf-8")).hexdigest()[:12]
            lines=st.data_editor(lines,key="new_shipment_lines_"+selection_key,hide_index=True,width="stretch",disabled=["SKU","Description","Length_cm","Width_cm","Height_cm","Weight_kg"],column_config={"Quantity":st.column_config.NumberColumn("Quantity",min_value=1,max_value=100000,step=1,format="%d")})
        with st.form("shipment_create"):
            top=st.columns(3)
            shipment_id=top[0].text_input("Shipment ID *")
            customer=top[1].text_input("Customer / delivery site *")
            due=top[2].date_input("Required delivery date",value=date.today())
            address=st.text_input("Delivery address *")
            coord_enabled=st.checkbox("I have verified delivery coordinates")
            coord=None
            if coord_enabled:
                coords=st.columns(2);lat=coords[0].number_input("Latitude",min_value=-90.0,max_value=90.0,value=0.0,format="%.6f");lon=coords[1].number_input("Longitude",min_value=-180.0,max_value=180.0,value=0.0,format="%.6f");coord=(lat,lon)
            priority=st.selectbox("Priority",["Normal","High","Urgent"])
            has_window=st.checkbox("Customer receiving time window")
            if has_window:
                wins=st.columns(2);start=wins[0].time_input("Window start",value=dt_time(9,0)).strftime("%H:%M");end=wins[1].time_input("Window end",value=dt_time(17,0)).strftime("%H:%M")
            else:start=end=""
            service=st.number_input("Estimated service / unloading time (minutes)",min_value=0.0,value=10.0,step=5.0)
            notes=st.text_input("Delivery notes / handling instructions")
            submitted=st.form_submit_button("Create shipment",type="primary",width="stretch")
        if submitted:
            if not shipment_id.strip() or not customer.strip() or not address.strip() or not selected:st.error("Enter a shipment ID, customer, address, and at least one parcel type.")
            elif lines.empty or (lines.Quantity<=0).any():st.error("Every selected parcel needs a positive whole-number quantity.")
            else:
                items=[(str(row.SKU),int(row.Quantity)) for _,row in lines.iterrows()]
                data={"shipment_id":shipment_id.strip(),"customer_name":customer.strip(),"address":address.strip(),"latitude":coord[0] if coord else None,"longitude":coord[1] if coord else None,"required_date":due,"priority":priority,"window_start":start,"window_end":end,"service_minutes":service,"status":"Ready","notes":notes}
                try:save_shipment(db_path,data,items)
                except Exception as exc:st.error(f"Shipment could not be saved: {exc}")
                else:st.success("Shipment created and added to the ready queue.");st.rerun()
    shipments=list_shipments(db_path)
    if shipments.empty:st.info("No shipments have been created yet.");return

    with st.expander("✎ Edit or cancel shipment",expanded=False):
        shipment_ids=shipments.Shipment_ID.astype(str).tolist()
        selected_id=st.selectbox("Shipment record",shipment_ids,key="shipment_manage_id")
        current=shipments[shipments.Shipment_ID.astype(str)==selected_id].iloc[0]
        current_lines=get_shipment_lines(db_path,[selected_id])
        allocated=shipment_has_allocations(db_path,selected_id)
        locked=allocated or str(current.Status) in ("Dispatched","Cancelled")
        if allocated:st.warning("This shipment has approved plan allocations. Its details and remaining quantity cannot be edited or cancelled here.")
        elif str(current.Status) in ("Dispatched","Cancelled"):st.info("Dispatched or cancelled shipment records are read-only.")
        current_skus=current_lines.SKU.astype(str).tolist() if not current_lines.empty else []
        all_products=get_products(db_path,active_only=False)
        allowed_products=all_products[(all_products.active==1)|all_products.SKU.astype(str).isin(current_skus)]
        manage_options=allowed_products.SKU.astype(str).tolist()
        manage_selected=st.multiselect("Shipment parcel types",manage_options,default=[sku for sku in current_skus if sku in manage_options],format_func=lambda sku:f"{sku} · {allowed_products.loc[allowed_products.SKU.astype(str)==sku,'Description'].iloc[0]}"+(" · archived" if int(allowed_products.loc[allowed_products.SKU.astype(str)==sku,'active'].iloc[0])==0 else ""),key="shipment_edit_skus_"+hashlib.sha1(selected_id.encode("utf-8")).hexdigest()[:10],disabled=locked)
        edit_products=allowed_products[allowed_products.SKU.astype(str).isin(manage_selected)]
        edit_lines=edit_products[["SKU","Description","Length_cm","Width_cm","Height_cm","Weight_kg"]].copy()
        old_quantities=current_lines.set_index("SKU").Quantity.to_dict() if not current_lines.empty else {}
        edit_lines["Quantity"]=[int(old_quantities.get(sku,1)) for sku in edit_lines.SKU]
        if not edit_lines.empty:
            line_key=hashlib.sha1((selected_id+"|"+"|".join(sorted(manage_selected))).encode("utf-8")).hexdigest()[:12]
            edit_lines=st.data_editor(edit_lines,key="edit_shipment_lines_"+line_key,hide_index=True,width="stretch",disabled=["SKU","Description","Length_cm","Width_cm","Height_cm","Weight_kg"],column_config={"Quantity":st.column_config.NumberColumn("Quantity",min_value=1,max_value=100000,step=1,format="%d")})
        with st.form("shipment_edit_form"):
            top=st.columns(3)
            edit_customer=top[0].text_input("Customer / delivery site",value=str(current.Customer_Name),disabled=locked)
            edit_due=top[1].date_input("Required delivery date",value=pd.to_datetime(current.Required_Date).date(),disabled=locked)
            edit_priority=top[2].selectbox("Priority",["Normal","High","Urgent"],index=["Normal","High","Urgent"].index(str(current.Priority)) if str(current.Priority) in ["Normal","High","Urgent"] else 0,disabled=locked)
            edit_address=st.text_input("Delivery address",value=str(current.Address),disabled=locked)
            has_coordinates=current.Latitude is not None and pd.notna(current.Latitude) and current.Longitude is not None and pd.notna(current.Longitude)
            edit_coordinates=st.checkbox("Verified coordinates available",value=has_coordinates,disabled=locked)
            if edit_coordinates:
                coords=st.columns(2)
                edit_lat=coords[0].number_input("Latitude",min_value=-90.0,max_value=90.0,value=float(current.Latitude) if has_coordinates else 0.0,format="%.6f",disabled=locked)
                edit_lon=coords[1].number_input("Longitude",min_value=-180.0,max_value=180.0,value=float(current.Longitude) if has_coordinates else 0.0,format="%.6f",disabled=locked)
            else:edit_lat=edit_lon=None
            has_window=bool(str(current.Window_Start).strip() and str(current.Window_End).strip())
            edit_has_window=st.checkbox("Customer receiving time window",value=has_window,disabled=locked)
            if edit_has_window:
                try:start_default=datetime.strptime(str(current.Window_Start),"%H:%M").time();end_default=datetime.strptime(str(current.Window_End),"%H:%M").time()
                except ValueError:start_default,end_default=dt_time(9,0),dt_time(17,0)
                wins=st.columns(2);edit_start=wins[0].time_input("Window start",value=start_default,disabled=locked).strftime("%H:%M");edit_end=wins[1].time_input("Window end",value=end_default,disabled=locked).strftime("%H:%M")
            else:edit_start=edit_end=""
            edit_service=st.number_input("Service time (minutes)",min_value=0.0,value=float(current.Service_Minutes),step=5.0,disabled=locked)
            edit_notes=st.text_input("Delivery notes",value=str(current.Notes or ""),disabled=locked)
            save_edit=st.form_submit_button("Save shipment changes",type="primary",disabled=locked or not manage_selected)
        if save_edit:
            if not edit_customer.strip() or not edit_address.strip() or edit_lines.empty:st.error("A shipment needs a customer, address, and at least one parcel type.")
            elif (edit_lines.Quantity<=0).any() or ((edit_lines.Quantity%1)!=0).any():st.error("Parcel quantities must be positive whole numbers.")
            else:
                payload={"customer_name":edit_customer.strip(),"address":edit_address.strip(),"latitude":edit_lat if edit_coordinates else None,"longitude":edit_lon if edit_coordinates else None,"required_date":edit_due,"priority":edit_priority,"window_start":edit_start,"window_end":edit_end,"service_minutes":edit_service,"notes":edit_notes}
                items=[(str(row.SKU),int(row.Quantity)) for _,row in edit_lines.iterrows()]
                try:update_shipment(db_path,selected_id,payload,items)
                except Exception as exc:st.error("Shipment was not updated: {}".format(exc))
                else:st.success("Shipment updated.");st.rerun()
        confirm_cancel=st.checkbox("I confirm this shipment should be cancelled",key="cancel_confirm_"+hashlib.sha1(selected_id.encode("utf-8")).hexdigest()[:10],disabled=locked)
        if st.button("Cancel shipment",disabled=locked or not confirm_cancel,key="cancel_shipment_"+hashlib.sha1(selected_id.encode("utf-8")).hexdigest()[:10]):
            try:cancel_shipment(db_path,selected_id)
            except ValueError as exc:st.error(str(exc))
            else:st.success("Shipment cancelled. Its record is retained in history.");st.rerun()

    st.subheader("Shipment status board")
    statuses=st.multiselect("Filter by status",["Ready","Open","Planned","Dispatched","On hold","Cancelled"],default=["Ready","Open","Planned","On hold","Cancelled"])
    visible=shipments[shipments.Status.isin(statuses)].copy()
    columns=["Shipment_ID","Customer_Name","Address","Required_Date","Priority","Window_Start","Window_End","Parcel_Lines","Parcel_Units","Status"]
    edited=st.data_editor(visible[columns],key="shipment_status_editor",width="stretch",hide_index=True,disabled=[c for c in columns if c!="Status"],column_config={"Status":st.column_config.SelectboxColumn("Status",options=["Ready","Open","Planned","Dispatched","On hold","Cancelled"],required=True)})
    if st.button("Save shipment status changes",type="primary",width="stretch"):
        before=visible.set_index("Shipment_ID").Status.to_dict()
        changes=[(str(row.Shipment_ID),str(row.Status)) for _,row in edited.iterrows() if before.get(str(row.Shipment_ID))!=str(row.Status)]
        try:update_shipment_statuses(db_path,changes)
        except ValueError as exc:st.error(str(exc))
        else:st.success(f"Saved {len(changes)} shipment status change(s).");st.rerun()


def render_settings(db_path):
    _heading("CONFIGURATION · OPERATING RULES","Planning settings","Set the primary depot and routing defaults used when creating a daily plan.")
    depot=get_depot(db_path);settings=get_settings(db_path)
    st.subheader("Primary depot")
    with st.form("depot_settings"):
        name=st.text_input("Depot name / ID",value=str(depot["name"]))
        address=st.text_input("Depot address",value=str(depot["address"]))
        coordinates=st.checkbox("Depot coordinates verified",value=depot["latitude"] is not None and depot["longitude"] is not None)
        if coordinates:
            c1,c2=st.columns(2);lat=c1.number_input("Depot latitude",min_value=-90.0,max_value=90.0,value=float(depot["latitude"] or 0),format="%.6f");lon=c2.number_input("Depot longitude",min_value=-180.0,max_value=180.0,value=float(depot["longitude"] or 0),format="%.6f")
        else:lat=lon=None
        cutoff=st.text_input("Loading cutoff (optional)",value=str(depot.get("loading_cutoff", "")))
        depot_submitted=st.form_submit_button("Save depot",type="primary")
    if depot_submitted:
        if not name.strip() or not address.strip():st.error("Depot name and address are required.")
        else:save_depot(db_path,{"name":name.strip(),"address":address.strip(),"latitude":lat,"longitude":lon,"loading_cutoff":cutoff});st.success("Depot saved.");st.rerun()
    st.subheader("Routing defaults")
    with st.form("routing_defaults"):
        url=st.text_input("OSRM-compatible routing service",value=settings.get("routing_url","https://router.project-osrm.org"))
        objective=st.selectbox("Default planning objective",["Distance","Travel time"],index=0 if settings.get("default_objective","Distance")=="Distance" else 1)
        try:departure=datetime.strptime(settings.get("default_departure","08:00"),"%H:%M").time()
        except ValueError:departure=dt_time(8,0)
        dep=st.time_input("Default departure time",value=departure).strftime("%H:%M")
        settings_submitted=st.form_submit_button("Save planning defaults",type="primary")
    if settings_submitted:
        if not url.strip().startswith(("http://","https://")):st.error("Routing service must start with http:// or https://.")
        else:save_settings(db_path,{"routing_url":url.strip().rstrip("/"),"default_objective":objective,"default_departure":dep});st.success("Planning defaults saved.");st.rerun()
    st.info("The public OSRM endpoint is for development. Use an approved company routing service before operational deployment.")
