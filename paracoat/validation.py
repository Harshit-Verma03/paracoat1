"""Input validation for uploaded planning workbooks."""
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
    for label, frame, key in [("Orders", orders, "Order_ID"), ("Parcels", parcels, "Parcel_ID"), ("Fleet", fleet, "Vehicle_ID")]:
        if frame[key].isna().any() or (frame[key].astype(str).str.strip() == "").any():
            errors.append(f"{label}: {key} cannot be blank.")
        duplicates = frame[frame[key].astype(str).duplicated(keep=False)][key].astype(str).unique().tolist()
        if duplicates:
            errors.append(f"{label}: duplicate {key} values: {', '.join(duplicates[:10])}.")
    for col in ("Latitude", "Longitude"):
        if col not in orders.columns:
            orders[col] = pd.NA
        orders[col] = pd.to_numeric(orders[col], errors="coerce")
    if (orders.Latitude.notna() != orders.Longitude.notna()).any():
        errors.append("Orders: provide both Latitude and Longitude for each delivery, or leave both blank.")
    bad_coords=orders.Latitude.notna() & orders.Longitude.notna() & (~orders.Latitude.between(-90,90) | ~orders.Longitude.between(-180,180))
    if bad_coords.any(): errors.append("Orders: coordinates must use decimal degrees (latitude -90..90, longitude -180..180).")
    for col in ("Latitude", "Longitude"):
        if col not in depot.columns:
            depot[col] = pd.NA
        depot[col] = pd.to_numeric(depot[col], errors="coerce")
    if not depot.empty:
        if "Depot" in depot.columns and (depot.Depot.isna().any() or (depot.Depot.astype(str).str.strip()=="").any()):
            errors.append("Depot: Depot name/ID cannot be blank.")
        if (depot.Latitude.notna() != depot.Longitude.notna()).any():
            errors.append("Depot: provide both Latitude and Longitude, or leave both blank.")
        bad_depot=depot.Latitude.notna() & depot.Longitude.notna() & (~depot.Latitude.between(-90,90) | ~depot.Longitude.between(-180,180))
        if bad_depot.any(): errors.append("Depot: coordinates must use decimal degrees (latitude -90..90, longitude -180..180).")
    for col in ("Quantity", "Length_cm", "Width_cm", "Height_cm", "Weight_kg"):
        parcels[col] = pd.to_numeric(parcels[col], errors="coerce")
        if parcels[col].isna().any() or (parcels[col] <= 0).any():
            errors.append(f"Parcels: {col} must contain positive numeric values on every row.")
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
    parcels["Max_Stack_Weight_kg"] = pd.to_numeric(parcels["Max_Stack_Weight_kg"], errors="coerce").fillna(0)
    if (parcels["Max_Stack_Weight_kg"]<0).any(): errors.append("Parcels: Max_Stack_Weight_kg cannot be negative.")
    for col in ("Capacity_kg", "Interior_Length_cm", "Interior_Width_cm", "Interior_Height_cm"):
        fleet[col] = pd.to_numeric(fleet[col], errors="coerce")
        if fleet[col].isna().any() or (fleet[col] <= 0).any():
            errors.append(f"Fleet: {col} must contain positive numeric values on every row.")
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
    orders["Service_Minutes"]=pd.to_numeric(orders.Service_Minutes,errors="coerce").fillna(0)
    if (orders.Service_Minutes<0).any():
        errors.append("Orders: Service_Minutes must be a number greater than or equal to zero.")
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
