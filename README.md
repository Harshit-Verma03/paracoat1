# Paracoat Dispatch Studio

A Python and Streamlit logistics planning prototype for maintaining vehicle and parcel catalogs, preparing shipments, selecting quantities for a dispatch day, estimating routes, and reviewing a vehicle plan before approval.

This is an early single-depot, single-day prototype. It is not a production dispatch system or a certified cargo safety tool.

## Current features

- Compact operations dashboard with date/status/search filters, selectable shipments, prefilled dispatch handoff, coordinate alerts, fleet readiness, and quick actions.
- Vehicle catalog with registration, vehicle type, availability, payload, interior and door dimensions, usable volume, pallet capacity, fuel, cost, depot, and notes. Saves reject duplicate active registration plates, door openings larger than the compartment, and usable volumes larger than its geometric capacity; planning applies the configured usable-volume limit.
- Parcel/SKU catalog with variable dimensions, per-unit weight, stackability, max stack weight, allowed orientation, and handling notes.
- Shipment book with customer/address, required date, optional coordinates, priority, time window, service time, notes, SKU lines, and quantities; unallocated shipments can be edited or soft-cancelled.
- Daily selection of shipments and per-line quantities; zero quantity holds a line for a later plan.
- OR-Tools route sequencing/assignment for whole shipments, payload checks, heuristic geometric fit checks, route map, whole-order reassignment, stop reordering, and Excel dispatch export.
- OR-Tools CP-SAT fleet loading assigns whole shipments to available vehicles under payload and volume limits, prioritizing shipments served, vehicle count, the selected distance/time route proxy, configured per-km cost as a lower-priority tie-break (when supplied), and total active cargo volume. Candidate loads are checked by a bounded geometric packer using four parcel-ordering strategies and three placement strategies, followed by a best-fit repair pass that tries currently unassigned shipments in every capacity-eligible van. This remains a best-found heuristic, not a proof of global optimality or infeasibility.
- A vehicle-by-vehicle parcel manifest reports per-vehicle load sequence, delivery stop, stable parcel-unit ID, shipment, selected orientation, oriented dimensions, and x/y/z position. With the current single rear-door vehicle model, stop 1 is placed nearest the rear door and later stops deeper toward the cab; the warehouse loads in reverse route order, numbered in the manifest. The 2D view marks the rear-door end, and stop-order edits trigger a new packing check. This is a conservative geometric access heuristic, not a verified handling or safety procedure.
- An extended synthetic scenario is selectable directly in Plan dispatch: 24 delivery points, 147 variable-size parcel units (about 3.18 tonnes), 12 parcel shapes/orientation rules, and eight available vehicles. A matching workbook can be downloaded from the sidebar; selecting the scenario does not change the catalog. An explicit append-only sidebar action can add its records to the catalogs.
- Draft editing supports whole-order vehicle reassignment, delivery-stop reordering, and individual parcel-unit orientation changes. A parcel orientation change repacks and revalidates that vehicle; approved plans are locked.
- Approval with an allocation ledger. Catalog quantities selected in an approved plan are consumed transactionally; unselected remaining quantities stay available. Approved plans are locked.
- Local SQLite persistence for catalogs, shipments, plans, allocation records, and events.
- Tabular, 2D layer, and basic interactive 3D parcel-box placement review. Advanced 3D VFX and safety analysis are outside this prototype.

## Quick start

Python 3.11 is the supported development runtime and the version used by continuous integration.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

On Windows, create and activate the environment with:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

After the project is published on GitHub, use GitHub's **Code → Clone** command to clone it, then follow the steps above.

The local database is `paracoat.db` in the project directory. To use a different path, set `PARACOAT_DB_PATH` before starting Streamlit.

The app seeds an empty database with a small synthetic demonstration catalog. Plan dispatch also has an **Extended demo scenario** with 24 synthetic deliveries, 147 parcel units, and eight available vehicles. Selecting that source does not alter the catalog. Use the sidebar download for its workbook or the append-only **Add demo data to catalogs** action to add its shipments, SKUs, and vehicles to the catalog workspaces. Existing records are kept; repeated imports skip existing demo IDs. Replace synthetic records with verified company data before operational use.

## Docker

```bash
docker build -t paracoat-dispatch .
docker run --rm -p 8501:8501 -v paracoat-data:/app/data paracoat-dispatch
```

The Docker image is configured to persist its local database under `/app/data`. SQLite is suitable only for local single-user prototyping; use a managed database and concurrency controls for a shared deployment.

## Run checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The test suite covers route and fit logic, delivery-order packing, validation, workbook import/export, repository catalog/shipment round-trips, plan allocation safeguards, orientation and position edits, and the interactive load preview. The extended synthetic dispatch can return a partial plan under the end-to-end time limit; each unassigned shipment includes a reason. GitHub Actions runs the suite on pushes and pull requests using Python 3.11.

## Optimizer benchmark

Run the repeatable synthetic benchmark with:

```bash
python scripts/benchmark_optimizer.py --sizes 24 50 100 --time-limit-seconds 3 --candidate-iterations 2 --repeats 2
```

See [the latest recorded benchmark](benchmarks/optimizer_benchmark.md) for measured solve and wall times, assignment counts, and packing fixtures. Results are machine-specific and synthetic.

## Workbook import

The sidebar provides an input workbook template with four sheets:

- **Orders:** `Order_ID`, `Customer_Name`, `Address`, `Latitude`, `Longitude`, `Priority`, optional `Window_Start`, `Window_End`, and `Service_Minutes`.
- **Parcels:** `Order_ID`, `Parcel_ID`, `Quantity`, `Length_cm`, `Width_cm`, `Height_cm`, `Weight_kg`, optional `Stackable`, `Max_Stack_Weight_kg`, and `Rotation`.
- **Fleet:** `Vehicle_ID`, `Available_Today`, `Capacity_kg`, `Interior_Length_cm`, `Interior_Width_cm`, `Interior_Height_cm`, and `Depot`.
- **Depot:** `Depot`, `Address`, `Latitude`, and `Longitude`.

Dimensions use centimetres, mass uses kilograms, coordinates use decimal degrees, and time windows use local 24-hour `HH:MM`. Workbook import is an alternate planning source; approval from workbook input records approval but does not modify saved shipment catalog quantities.

## Current limitations

- One depot and one planning day; whole orders are assigned to routes and split deliveries are not supported.
- Delivery coordinates must be entered manually. Address geocoding is not implemented.
- Priority is recorded but not an optimization objective. Driver hours, breaks, vehicle eligibility, fuel/emissions, loading cutoff, and traffic-aware ETAs are not implemented; the fleet allocator does use configured cost per kilometre as an assignment tie-breaker.
- OSRM-compatible distance/time results are estimates. In road-routing mode, the selected routing endpoint receives the supplied coordinates. The public OSRM demo endpoint is for development; configure an approved routing provider or use straight-line estimates for sensitive locations. A clearly labelled fallback may be used when road routing fails.
- Parcel fit is an explainable heuristic, not an exact packing solver. It combines four deterministic parcel orderings with three placement strategies under shared backtracking and time limits, and checks dimensions, rotations, door clearance, same-cross-section delivery order, full support footprints, stackability, and cumulative configured stack load limits. It assumes one rear cargo door at X = vehicle length; side or multiple doors and special unloading paths are not modeled. A parcel stacked on a base with an unknown maximum load is marked unfit. Axle load, center of gravity, securement, stability, and cargo compatibility are not validated. A fit result is not a safety certification.
- Loading and routing are connected through bounded candidate validation. The planner returns the best feasible candidate found within its search limit; the heuristic does not establish a globally optimal 3D packing. The CP-SAT assignment model uses payload, volume, vehicle count, the selected distance/time route proxy, configured cost as a lower-priority tie-break, and bounded placement checks. Distance-only stop sequences use nearest-neighbor ordering with bounded 2-opt refinement; travel-time routes use OR-Tools. Payload constraints are rounded conservatively to grams. Whole shipments stay together on one vehicle; parcel-level split deliveries are not supported.
- Draft plans support parcel orientation changes and direct x/y/z position edits. The moved unit stays at the requested coordinates while other units on that vehicle may be repacked and revalidated. Moving parcel units between vehicles or splitting shipments is not available.
- The app has no authentication or role management. Do not expose it publicly with operational data.
- Plan history can open saved plans, inspect route stops/exceptions/input snapshots/approval events, and regenerate the dispatch workbook. Editing or restoring a historical plan is not implemented.

Before a shared deployment, add authentication/authorization, PostgreSQL or another managed database, schema migrations, backups, monitoring, secrets management, concurrency control, and an approved routing/geocoding service.

## Deployment and data safety

Docker is provided for local or private testing. The prototype has no authentication and uses local SQLite by default; do not expose an instance containing customer, route, or operational data to the public internet. A public source repository does not make a running instance safe for public hosting. Before shared deployment, complete the production-readiness items in [FEATURE_CHECKLIST.md](FEATURE_CHECKLIST.md), including authentication, a managed database, backups, and concurrency protection.

## Contributing and support

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations, and [SUPPORT.md](SUPPORT.md) for help. After the repository is published, report bugs and request features through GitHub Issues. Do not include real customer or shipment records in issues.

## License

This project is licensed under the [MIT License](LICENSE).

## Project structure

```text
app.py                   Streamlit application and dispatch workflow
paracoat/catalog_views.py Dashboard, fleet, parcel, shipment, and settings screens
paracoat/repository.py   SQLite schema and catalog/dispatch persistence
paracoat/validation.py   Workbook validation and normalization
paracoat/routing.py      Travel matrix and route geometry
paracoat/planner.py      Vehicle routing optimization
paracoat/dispatch.py     Plan creation and dispatcher edits
paracoat/packing.py      Geometric parcel-fit heuristic
paracoat/loading_optimizer.py Fleet-level load assignment
paracoat/loading_views.py 2D and interactive 3D cargo previews
paracoat/workbooks.py    Workbook template and dispatch export
tests/                   Route, validation, repository, and workbook checks
```
