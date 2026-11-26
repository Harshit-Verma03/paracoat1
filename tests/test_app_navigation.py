from pathlib import Path

from streamlit.testing.v1 import AppTest
from paracoat.repository import get_shipment_lines, list_shipments, get_products, get_vehicles


def test_dashboard_shortcuts_and_selected_shipments(monkeypatch,tmp_path):
    monkeypatch.setenv("PARACOAT_DB_PATH",str(tmp_path/"dashboard.db"))
    app_path=Path(__file__).resolve().parents[1]/"app.py"
    app=AppTest.from_file(str(app_path),default_timeout=30).run()
    assert not app.exception

    app.session_state["overview_selected_shipments"]=["ORD-1001","ORD-1002"]
    app.run()
    assert not app.button[0].disabled
    app.button[0].click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value=="Plan dispatch"
    assert app.multiselect[0].value==["ORD-1001","ORD-1002"]

    app.sidebar.radio[0].set_value("Overview").run()
    app.button[1].click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value=="Shipments"

    app.sidebar.radio[0].set_value("Overview").run()
    app.button[2].click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value=="Fleet"


def test_shipment_create_edit_and_cancel_ui_flow(monkeypatch,tmp_path):
    db_path=tmp_path/"shipment-ui.db"
    monkeypatch.setenv("PARACOAT_DB_PATH",str(db_path))
    app_path=Path(__file__).resolve().parents[1]/"app.py"
    app=AppTest.from_file(str(app_path),default_timeout=30).run()
    app.sidebar.radio[0].set_value("Shipments").run()

    next(widget for widget in app.multiselect if widget.label=="Parcel types").set_value(["P-1001-A"]).run()
    for widget in app.text_input:
        if widget.label=="Shipment ID *":widget.set_value("UI-SHIP-1")
        elif widget.label=="Customer / delivery site *":widget.set_value("UI Customer")
        elif widget.label=="Delivery address *":widget.set_value("10 UI Street")
    next(widget for widget in app.button if widget.label=="Create shipment").click().run()
    assert not app.exception
    assert "UI-SHIP-1" in list_shipments(db_path).Shipment_ID.values
    assert int(get_shipment_lines(db_path,["UI-SHIP-1"]).iloc[0].Quantity)==1

    next(widget for widget in app.selectbox if widget.label=="Shipment record").set_value("UI-SHIP-1").run()
    next(widget for widget in app.text_input if widget.label=="Customer / delivery site").set_value("Updated UI Customer")
    next(widget for widget in app.button if widget.label=="Save shipment changes").click().run()
    assert not app.exception
    assert list_shipments(db_path).set_index("Shipment_ID").loc["UI-SHIP-1","Customer_Name"]=="Updated UI Customer"

    next(widget for widget in app.checkbox if widget.label=="I confirm this shipment should be cancelled").set_value(True).run()
    next(widget for widget in app.button if widget.label=="Cancel shipment").click().run()
    assert not app.exception
    assert list_shipments(db_path).set_index("Shipment_ID").loc["UI-SHIP-1","Status"]=="Cancelled"


def test_fleet_form_rejects_invalid_door_and_saves_valid_vehicle(monkeypatch,tmp_path):
    db_path=tmp_path/"fleet-ui.db"
    monkeypatch.setenv("PARACOAT_DB_PATH",str(db_path))
    app_path=Path(__file__).resolve().parents[1]/"app.py"
    app=AppTest.from_file(str(app_path),default_timeout=30).run()
    app.sidebar.radio[0].set_value("Fleet").run()
    next(widget for widget in app.selectbox if widget.label=="Vehicle record").set_value("+ Add new vehicle")
    values={"Vehicle ID *":"UI-VEH-1","Vehicle name":"UI van","Registration / plate":"UI-PLATE-1"}
    for widget in app.text_input:
        if widget.label in values:widget.set_value(values[widget.label])
    next(widget for widget in app.number_input if widget.label=="Door clear width (cm)").set_value(200)
    next(widget for widget in app.button if widget.label=="Save vehicle").click().run()
    assert not app.exception
    assert any("Door clear dimensions cannot exceed" in item.value for item in app.error)
    assert "UI-VEH-1" not in get_vehicles(db_path).Vehicle_ID.tolist()

    next(widget for widget in app.number_input if widget.label=="Door clear width (cm)").set_value(120)
    next(widget for widget in app.button if widget.label=="Save vehicle").click().run()
    assert not app.exception
    vehicles=get_vehicles(db_path)
    saved=vehicles[vehicles.Vehicle_ID=="UI-VEH-1"].iloc[0]
    assert saved.Registration=="UI-PLATE-1"
    assert float(saved.Door_Width_cm)==120


def test_parcel_catalog_create_and_archive_ui_flow(monkeypatch,tmp_path):
    db_path=tmp_path/"parcel-ui.db"
    monkeypatch.setenv("PARACOAT_DB_PATH",str(db_path))
    app_path=Path(__file__).resolve().parents[1]/"app.py"
    app=AppTest.from_file(str(app_path),default_timeout=30).run()
    app.sidebar.radio[0].set_value("Parcel catalog").run()
    next(widget for widget in app.selectbox if widget.label=="Parcel record").set_value("+ Add parcel type")
    fields={"SKU / parcel ID *":"UI-BOX-1","Description *":"UI carton","Category":"Carton"}
    for widget in app.text_input:
        if widget.label in fields:widget.set_value(fields[widget.label])
    next(widget for widget in app.text_area if widget.label=="Handling instructions").set_value("Keep upright")
    next(widget for widget in app.button if widget.label=="Save parcel type").click().run()
    assert not app.exception
    saved=get_products(db_path)
    record=saved[saved.SKU=="UI-BOX-1"].iloc[0]
    assert record.Description=="UI carton"
    assert record.Rotation=="Any"

    next(widget for widget in app.selectbox if widget.label=="Parcel record").set_value("UI-BOX-1").run()
    next(widget for widget in app.button if widget.label=="Archive selected parcel type").click().run()
    assert not app.exception
    assert "UI-BOX-1" not in get_products(db_path).SKU.tolist()


def test_dispatch_build_shows_parcel_vehicle_orientation_manifest(monkeypatch,tmp_path):
    monkeypatch.setenv("PARACOAT_DB_PATH",str(tmp_path/"dispatch-ui.db"))
    app_path=Path(__file__).resolve().parents[1]/"app.py"
    app=AppTest.from_file(str(app_path),default_timeout=45).run()
    app.sidebar.radio[0].set_value("Plan dispatch").run()
    next(widget for widget in app.selectbox if widget.label=="Travel estimate source").set_value("Straight-line estimate")
    next(widget for widget in app.button if widget.label=="Build dispatch plan").click().run()
    assert not app.exception
    plan=app.session_state["plan"]
    assert not plan["unassigned"]
    assert len(plan["placements"])==9
    assert all(item["Vehicle_ID"] and item["Orientation"] for item in plan["placements"])
    manifest=next(widget.value for widget in app.dataframe if "Parcel_Unit_ID" in widget.value.columns)
    assert {"Vehicle_ID","Orientation","X_cm","Y_cm","Z_cm"}.issubset(manifest.columns)
    assert any(widget.label=="Parcel unit" for widget in app.selectbox)
    assert any(widget.label=="Allowed orientation" for widget in app.selectbox)
    assert len(app.get("plotly_chart"))>=1


def test_extended_demo_source_exposes_large_replaceable_scenario(monkeypatch,tmp_path):
    monkeypatch.setenv("PARACOAT_DB_PATH",str(tmp_path/"extended-demo-ui.db"))
    app_path=Path(__file__).resolve().parents[1]/"app.py"
    app=AppTest.from_file(str(app_path),default_timeout=45).run()
    db_path=tmp_path/"extended-demo-ui.db"
    next(widget for widget in app.sidebar.button if widget.label=="Add demo data to catalogs").click().run()
    assert not app.exception
    assert len(list_shipments(db_path))==29
    assert len(get_products(db_path))==17
    assert len(get_vehicles(db_path))==11
    next(widget for widget in app.sidebar.button if widget.label=="Add demo data to catalogs").click().run()
    assert not app.exception
    assert len(list_shipments(db_path))==29
    app.sidebar.radio[0].set_value("Plan dispatch").run()
    next(widget for widget in app.radio if widget.label=="Planning source").set_value("Extended demo scenario").run()
    assert not app.exception
    assert any(widget.label=="Synthetic shipments" for widget in app.multiselect)
    assert any("not saved to your catalog" in widget.value for widget in app.caption)
    assert any("147 parcel units" in widget.value for widget in app.caption)
    next(widget for widget in app.selectbox if widget.label=="Travel estimate source").set_value("Straight-line estimate")
    next(widget for widget in app.button if widget.label=="Build dispatch plan").click().run()
    assert not app.exception
    plan=app.session_state["plan"]
    assert plan["vehicles_used"]>=2
    assert not plan["unassigned"]
    assert len(plan["placements"])==147
