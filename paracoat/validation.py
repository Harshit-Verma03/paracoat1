"""Input validation for uploaded planning workbooks."""
import math
import pandas as pd
from datetime import datetime, time

ORDER_REQUIRED = ["Order_ID", "Customer_Name", "Address"]
PARCEL_REQUIRED = ["Order_ID", "Parcel_ID", "Quantity", "Length_cm", "Width_cm", "Height_cm", "Weight_kg"]
FLEET_REQUIRED = ["Vehicle_ID", "Available_Today", "Capacity_kg", "Interior_Length_cm", "Interior_Width_cm", "Interior_Height_cm"]


def _normalize(frame):
    data = frame.copy()
    data.columns = [str(c).strip() for c in data.columns]
    for column in data.select_dtypes(include="object").columns:
        data[column] = data[column].map(lambda x: x.strip() if isinstance(x, str) else x)
    return data


def _missing_columns(frame, required, name):
    return [f"{name}: missing required column '{c}'" for c in required if c not in frame.columns]


def validate_inputs(orders, parcels, fleet, depot):
    orders, parcels, fleet, depot = map(_normalize, (orders, parcels, fleet, depot))
    errors, warnings = [], []
    errors += _missing_columns(orders, ORDER_REQUIRED, "Orders")
    errors += _missing_columns(parcels, PARCEL_REQUIRED, "Parcels")
    errors += _missing_columns(fleet, FLEET_REQUIRED, "Fleet")
    if errors:
        return orders, parcels, fleet, depot, errors, warnings
    if orders.empty: errors.append("Orders: provide at least one delivery order.")
    if parcels.empty: errors.append("Parcels: provide at least one parcel record.")
    if fleet.empty: errors.append("Fleet: provide at least one vehicle.")
    for label, frame, key in [("Orders", orders, "Order_ID"), ("Fleet", fleet, "Vehicle_ID")]:
        if frame[key].isna().any() or (frame[key].astype(str).str.strip() == "").any():
            errors.append(f"{label}: {key} cannot be blank.")
        duplicates = frame[frame[key].astype(str).duplicated(keep=False)][key].astype(str).unique().tolist()
        if duplicates:
            errors.append(f"{label}: duplicate {key} values: {', '.join(duplicates[:10])}.")
    if parcels.Parcel_ID.isna().any() or (parcels.Parcel_ID.astype(str).str.strip() == "").any():
        errors.append("Parcels: Parcel_ID cannot be blank.")
    # Parcel_ID identifies a reusable SKU/type, so it can legitimately occur
    # on many orders. A repeated line is ambiguous only within the same order.
    duplicate_lines=parcels.duplicated(subset=["Order_ID","Parcel_ID"],keep=False)
    if duplicate_lines.any():
        duplicates=(parcels.loc[duplicate_lines, ["Order_ID","Parcel_ID"]]
                    .astype(str).drop_duplicates())
        examples=["{} / {}".format(row.Order_ID,row.Parcel_ID)
                  for row in duplicates.itertuples(index=False)]
        errors.append("Parcels: duplicate Parcel_ID within Order_ID: {}.".format(", ".join(examples[:10])))
    for col in ("Latitude", "Longitude"):
        raw=orders[col] if col in orders.columns else pd.Series([pd.NA]*len(orders),index=orders.index)
        if col not in orders.columns:
            orders[col] = pd.NA
        orders[col] = pd.to_numeric(orders[col], errors="coerce")
        invalid_text=raw.notna() & raw.astype(str).str.strip().ne("") & orders[col].isna()
        if invalid_text.any():errors.append(f"Orders: {col} must contain valid decimal-degree coordinates when supplied.")
    if (orders.Latitude.notna() != orders.Longitude.notna()).any():
        errors.append("Orders: provide both Latitude and Longitude for each delivery, or leave both blank.")
    bad_coords=orders.Latitude.notna() & orders.Longitude.notna() & (~orders.Latitude.between(-90,90) | ~orders.Longitude.between(-180,180))
    if bad_coords.any(): errors.append("Orders: coordinates must use decimal degrees (latitude -90..90, longitude -180..180).")
    for col in ("Latitude", "Longitude"):
        raw=depot[col] if col in depot.columns else pd.Series([pd.NA]*len(depot),index=depot.index)
        if col not in depot.columns:
            depot[col] = pd.NA
        depot[col] = pd.to_numeric(depot[col], errors="coerce")
        invalid_text=raw.notna() & raw.astype(str).str.strip().ne("") & depot[col].isna()
        if invalid_text.any():errors.append(f"Depot: {col} must contain valid decimal-degree coordinates when supplied.")
    if not depot.empty:
        if "Depot" in depot.columns and (depot.Depot.isna().any() or (depot.Depot.astype(str).str.strip()=="").any()):
            errors.append("Depot: Depot name/ID cannot be blank.")
        if (depot.Latitude.notna() != depot.Longitude.notna()).any():
            errors.append("Depot: provide both Latitude and Longitude, or leave both blank.")
        bad_depot=depot.Latitude.notna() & depot.Longitude.notna() & (~depot.Latitude.between(-90,90) | ~depot.Longitude.between(-180,180))
        if bad_depot.any(): errors.append("Depot: coordinates must use decimal degrees (latitude -90..90, longitude -180..180).")
    for col in ("Quantity", "Length_cm", "Width_cm", "Height_cm", "Weight_kg"):
        parcels[col] = pd.to_numeric(parcels[col], errors="coerce")
        finite=parcels[col].map(lambda value:pd.notna(value) and math.isfinite(float(value)))
        if parcels[col].isna().any() or not finite.all() or (parcels[col] <= 0).any():
            errors.append(f"Parcels: {col} must contain finite positive numeric values on every row.")
    if parcels.Quantity.notna().all() and ((parcels.Quantity % 1) != 0).any():
        errors.append("Parcels: Quantity must be a whole number.")
    if "Stackable" not in parcels.columns: parcels["Stackable"]="Yes"
    stack_values=parcels.Stackable.astype(str).str.lower()
    if not stack_values.isin(["yes","no","true","false","1","0"]).all():
        errors.append("Parcels: Stackable must be Yes or No.")
    if "Rotation" not in parcels.columns: parcels["Rotation"]="Any"
    allowed_rotations={"any","none","upright","upright only","no height rotation","base only"}
    invalid_rotation=~parcels.Rotation.fillna("Any").astype(str).str.lower().isin(allowed_rotations)
    if invalid_rotation.any(): errors.append("Parcels: Rotation must be Any, Upright only, or No height rotation.")
    if "Max_Stack_Weight_kg" not in parcels.columns:
        parcels["Max_Stack_Weight_kg"] = 0
    raw_stack=parcels["Max_Stack_Weight_kg"]
    parsed_stack=pd.to_numeric(raw_stack,errors="coerce")
    if (raw_stack.notna() & raw_stack.astype(str).str.strip().ne("") & parsed_stack.isna()).any():
        errors.append("Parcels: Max_Stack_Weight_kg must be numeric when supplied.")
    parcels["Max_Stack_Weight_kg"] = parsed_stack.fillna(0)
    if not parcels["Max_Stack_Weight_kg"].map(lambda value:math.isfinite(float(value))).all():
        errors.append("Parcels: Max_Stack_Weight_kg must be finite.")
    if (parcels["Max_Stack_Weight_kg"]<0).any(): errors.append("Parcels: Max_Stack_Weight_kg cannot be negative.")
    for col in ("Capacity_kg", "Interior_Length_cm", "Interior_Width_cm", "Interior_Height_cm"):
        fleet[col] = pd.to_numeric(fleet[col], errors="coerce")
        finite=fleet[col].map(lambda value:pd.notna(value) and math.isfinite(float(value)))
        if fleet[col].isna().any() or not finite.all() or (fleet[col] <= 0).any():
            errors.append(f"Fleet: {col} must contain finite positive numeric values on every row.")
    optional_fleet={"Door_Width_cm":("Interior_Width_cm",0),"Door_Height_cm":("Interior_Height_cm",0),
                    "Volume_m3":(None,0),"Cost_Per_Km":(None,0),"Pallet_Capacity":(None,0)}
    for col,(_,minimum) in optional_fleet.items():
        if col not in fleet.columns:
            fleet[col]=minimum
            continue
        raw=fleet[col]
        parsed=pd.to_numeric(raw,errors="coerce")
        invalid_text=raw.notna() & raw.astype(str).str.strip().ne("") & parsed.isna()
        if invalid_text.any():errors.append(f"Fleet: {col} must contain numeric values when supplied.")
        fleet[col]=parsed.fillna(minimum)
        if not fleet[col].map(lambda value:math.isfinite(float(value))).all():
            errors.append(f"Fleet: {col} must contain finite values.")
        if (fleet[col]<minimum).any():errors.append(f"Fleet: {col} cannot be negative.")
    if (fleet.Door_Width_cm>fleet.Interior_Width_cm).any() or (fleet.Door_Height_cm>fleet.Interior_Height_cm).any():
        errors.append("Fleet: door clear dimensions cannot exceed the cargo interior dimensions.")
    geometric_volume=fleet.Interior_Length_cm*fleet.Interior_Width_cm*fleet.Interior_Height_cm/1_000_000
    if (fleet.Volume_m3>geometric_volume+1e-9).any():
        errors.append("Fleet: usable volume cannot exceed the geometric cargo volume.")
    if ((fleet.Pallet_Capacity%1)!=0).any():errors.append("Fleet: Pallet_Capacity must be a whole number.")
    for col in ("Window_Start","Window_End"):
        if col not in orders.columns: orders[col]=""
        def normalize_time(value):
            if pd.isna(value): return ""
            if isinstance(value,datetime): return value.strftime("%H:%M")
            if isinstance(value,time): return value.strftime("%H:%M")
            return str(value).strip()
        orders[col]=orders[col].map(normalize_time)
    has_start=orders.Window_Start.fillna("").astype(str).str.strip().ne("")
    has_end=orders.Window_End.fillna("").astype(str).str.strip().ne("")
    if (has_start != has_end).any(): errors.append("Orders: provide both Window_Start and Window_End for each time-windowed order.")
    if "Service_Minutes" not in orders.columns: orders["Service_Minutes"]=0
    raw_service=orders.Service_Minutes
    parsed_service=pd.to_numeric(raw_service,errors="coerce")
    if (raw_service.notna() & raw_service.astype(str).str.strip().ne("") & parsed_service.isna()).any():
        errors.append("Orders: Service_Minutes must be numeric when supplied.")
    orders["Service_Minutes"]=parsed_service.fillna(0)
    if (not orders.Service_Minutes.map(lambda value:math.isfinite(float(value))).all()
            or (orders.Service_Minutes<0).any()):
        errors.append("Orders: Service_Minutes must be a finite number greater than or equal to zero.")
    for col, active in (("Window_Start",has_start),("Window_End",has_end)):
        parsed=pd.to_datetime(orders.loc[active,col],format="%H:%M",errors="coerce")
        if parsed.isna().any(): errors.append(f"Orders: {col} must use 24-hour HH:MM format.")
    paired=has_start & has_end
    starts=pd.to_datetime(orders.loc[paired,"Window_Start"],format="%H:%M",errors="coerce")
    ends=pd.to_datetime(orders.loc[paired,"Window_End"],format="%H:%M",errors="coerce")
    if (ends < starts).any(): errors.append("Orders: Window_End must be at or after Window_Start on the same day.")
    ids = set(orders.Order_ID.astype(str))
    for col in ("Customer_Name","Address"):
        if orders[col].isna().any() or (orders[col].astype(str).str.strip()=="").any():
            errors.append(f"Orders: {col} cannot be blank.")
    unknown = sorted(set(parcels.Order_ID.astype(str)) - ids)
    missing_parcels = sorted(ids - set(parcels.Order_ID.astype(str)))
    if unknown:
        errors.append("Parcels reference unknown orders: " + ", ".join(unknown[:10]) + ".")
    if missing_parcels:
        errors.append("Orders without parcel records: " + ", ".join(missing_parcels[:10]) + ".")
    if len(depot) != 1:
        errors.append("Depot: this prototype requires exactly one depot row.")
    if "Available_Today" not in fleet.columns:
        fleet["Available_Today"] = "Yes"
    availability=fleet.Available_Today.astype(str).str.lower()
    if not availability.isin(["yes","no","true","false","1","0","available","unavailable"]).all():
        errors.append("Fleet: Available_Today must be Yes or No.")
    available = availability.isin(["yes", "true", "1", "available"])
    if not available.any():
        errors.append("Fleet: mark at least one vehicle Available_Today = Yes.")
    if not depot.empty and not ("Latitude" in depot and "Longitude" in depot and depot.Latitude.notna().all() and depot.Longitude.notna().all()):
        warnings.append("Depot coordinates are incomplete; estimates will use a clearly labelled straight-line fallback.")
    if not (orders.Latitude.notna().all() and orders.Longitude.notna().all()):
        warnings.append("Some delivery coordinates are missing. Add verified coordinates before generating a route; address geocoding is not implemented yet.")
    return orders, parcels, fleet, depot, errors, warnings
