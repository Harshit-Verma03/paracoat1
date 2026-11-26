"""Synthetic datasets for quick-start and extended dispatch demonstrations."""
import random
import pandas as pd


def sample_orders():
    return pd.DataFrame([
        {"Order_ID":"ORD-1001","Customer_Name":"Northstar Retail","Address":"Connaught Place, New Delhi","Latitude":28.6315,"Longitude":77.2167,"Priority":"Normal","Window_Start":"","Window_End":"","Service_Minutes":12},
        {"Order_ID":"ORD-1002","Customer_Name":"Cedar Home","Address":"Karol Bagh, New Delhi","Latitude":28.6514,"Longitude":77.1907,"Priority":"High","Window_Start":"","Window_End":"","Service_Minutes":15},
        {"Order_ID":"ORD-1003","Customer_Name":"Studio Market","Address":"Lajpat Nagar, New Delhi","Latitude":28.5677,"Longitude":77.2433,"Priority":"Normal","Window_Start":"","Window_End":"","Service_Minutes":10},
        {"Order_ID":"ORD-1004","Customer_Name":"Greenline Supply","Address":"Rohini, New Delhi","Latitude":28.7495,"Longitude":77.0565,"Priority":"Normal","Window_Start":"","Window_End":"","Service_Minutes":15},
        {"Order_ID":"ORD-1005","Customer_Name":"Amber House","Address":"Dwarka, New Delhi","Latitude":28.5921,"Longitude":77.0460,"Priority":"High","Window_Start":"","Window_End":"","Service_Minutes":12},
    ])


def sample_parcels():
    return pd.DataFrame([
        {"Order_ID":"ORD-1001","Parcel_ID":"P-1001-A","Quantity":2,"Length_cm":60,"Width_cm":40,"Height_cm":35,"Weight_kg":18,"Stackable":"Yes","Max_Stack_Weight_kg":25,"Rotation":"Any"},
        {"Order_ID":"ORD-1002","Parcel_ID":"P-1002-A","Quantity":1,"Length_cm":100,"Width_cm":60,"Height_cm":45,"Weight_kg":32,"Stackable":"No","Max_Stack_Weight_kg":0,"Rotation":"Any"},
        {"Order_ID":"ORD-1003","Parcel_ID":"P-1003-A","Quantity":3,"Length_cm":45,"Width_cm":35,"Height_cm":30,"Weight_kg":11,"Stackable":"Yes","Max_Stack_Weight_kg":20,"Rotation":"Any"},
        {"Order_ID":"ORD-1004","Parcel_ID":"P-1004-A","Quantity":1,"Length_cm":120,"Width_cm":50,"Height_cm":40,"Weight_kg":27,"Stackable":"Yes","Max_Stack_Weight_kg":30,"Rotation":"Any"},
        {"Order_ID":"ORD-1005","Parcel_ID":"P-1005-A","Quantity":2,"Length_cm":55,"Width_cm":45,"Height_cm":35,"Weight_kg":16,"Stackable":"Yes","Max_Stack_Weight_kg":24,"Rotation":"Any"},
    ])


def sample_fleet():
    return pd.DataFrame([
        {"Vehicle_ID":"VAN-01","Available_Today":"Yes","Capacity_kg":500,"Interior_Length_cm":300,"Interior_Width_cm":160,"Interior_Height_cm":150,"Depot":"Main Depot"},
        {"Vehicle_ID":"VAN-02","Available_Today":"Yes","Capacity_kg":350,"Interior_Length_cm":250,"Interior_Width_cm":150,"Interior_Height_cm":140,"Depot":"Main Depot"},
        {"Vehicle_ID":"VAN-03","Available_Today":"No","Capacity_kg":250,"Interior_Length_cm":220,"Interior_Width_cm":140,"Interior_Height_cm":130,"Depot":"Main Depot"},
    ])


def sample_depot():
    return pd.DataFrame([{"Depot":"Main Depot","Address":"Okhla Industrial Estate, New Delhi","Latitude":28.5355,"Longitude":77.2732}])


def extended_demo_orders():
    """Twenty-four reproducible stops across Delhi NCR for multi-vehicle demos."""
    regions=[
        ("North","Rohini",28.738,77.106),
        ("West","Dwarka",28.592,77.046),
        ("South","Saket",28.524,77.206),
        ("East","Mayur Vihar",28.608,77.296),
    ]
    rows=[]
    for index in range(24):
        region,name,latitude,longitude=regions[index//6]
        row=index%6
        rows.append({
            "Order_ID":"DEMO-{:04d}".format(2001+index),
            "Customer_Name":"{} District Delivery {:02d}".format(region,row+1),
            "Address":"Sector {}, {}, New Delhi".format(row+1,name),
            "Latitude":round(latitude+(row-2.5)*0.012,6),
            "Longitude":round(longitude+((row%3)-1)*0.016,6),
            "Priority":["High","Normal","Normal","Low","Normal","High"][row],
            "Window_Start":"09:00" if row in (0,3) else "",
            "Window_End":"18:00" if row in (0,3) else "",
            "Service_Minutes":[12,18,10,22,15,14][row],
        })
    return pd.DataFrame(rows)


def extended_demo_catalog():
    """Varied non-uniform parcel SKUs used by the larger synthetic scenario."""
    rows=[
        ("DEMO-SKU-01","Compact carton",32,26,20,9,"Yes",45,"Any"),
        ("DEMO-SKU-02","Standard carton",45,35,30,14,"Yes",70,"Any"),
        ("DEMO-SKU-03","Heavy carton",50,40,38,24,"Yes",90,"No height rotation"),
        ("DEMO-SKU-04","Long carton",95,32,28,22,"Yes",55,"No height rotation"),
        ("DEMO-SKU-05","Tall upright case",42,38,78,21,"Yes",65,"Upright only"),
        ("DEMO-SKU-06","Flat wide case",82,62,18,17,"Yes",50,"No height rotation"),
        ("DEMO-SKU-07","Large carton",76,58,48,29,"Yes",100,"Any"),
        ("DEMO-SKU-08","Dense component case",38,34,32,31,"Yes",110,"Upright only"),
        ("DEMO-SKU-09","Fragile display crate",68,48,52,19,"No",0,"Upright only"),
        ("DEMO-SKU-10","Long equipment case",118,42,34,27,"Yes",60,"No height rotation"),
        ("DEMO-SKU-11","Light bulky carton",72,58,42,12,"Yes",35,"Any"),
        ("DEMO-SKU-12","Small dense case",28,24,22,26,"Yes",90,"Any"),
    ]
    return pd.DataFrame([{
        "Parcel_ID":sku,"Description":description,"Category":"Extended demo",
        "Length_cm":length,"Width_cm":width,"Height_cm":height,"Weight_kg":weight,
        "Stackable":stackable,"Max_Stack_Weight_kg":max_stack,"Rotation":rotation,
        "Handling":"Synthetic demo data — replace with measured parcel specifications.",
    } for sku,description,length,width,height,weight,stackable,max_stack,rotation in rows])


def extended_demo_parcels():
    """Roughly 150 varied package units spread across 24 shipments."""
    rng=random.Random(20261126)
    catalog=extended_demo_catalog()
    orders=extended_demo_orders()
    rows=[]
    for _,order in orders.iterrows():
        count=rng.choice([4,4,5,5,6])
        selected=rng.sample(catalog.Parcel_ID.tolist(),count)
        for line_index,sku in enumerate(selected,1):
            item=catalog[catalog.Parcel_ID==sku].iloc[0]
            rows.append({
                "Order_ID":order.Order_ID,"Parcel_ID":"{}-L{:02d}".format(order.Order_ID,line_index),
                "SKU":sku,"Quantity":rng.choice([1,1,1,2]),
                "Length_cm":item.Length_cm,"Width_cm":item.Width_cm,"Height_cm":item.Height_cm,
                "Weight_kg":item.Weight_kg,"Stackable":item.Stackable,
                "Max_Stack_Weight_kg":item.Max_Stack_Weight_kg,"Rotation":item.Rotation,
            })
    return pd.DataFrame(rows)


def extended_demo_fleet():
    """Eight available vehicles with different cargo and door dimensions."""
    rows=[]
    for index in range(1,9):
        is_long=index>4
        rows.append({
            "Vehicle_ID":"DEMO-{}-{:02d}".format("LONG" if is_long else "VAN",index if not is_long else index-4),
            "Available_Today":"Yes","Capacity_kg":450,
            "Interior_Length_cm":420 if is_long else 360,
            "Interior_Width_cm":185 if is_long else 170,
            "Interior_Height_cm":175 if is_long else 155,
            "Door_Width_cm":165 if is_long else 150,
            "Door_Height_cm":160 if is_long else 140,
            "Volume_m3":(420*185*175/1_000_000) if is_long else (360*170*155/1_000_000),
            "Pallet_Capacity":0,"Cost_Per_Km":30 if is_long else 24,
            "Fuel_Type":"Diesel","Depot":"Main Depot",
        })
    return pd.DataFrame(rows)
