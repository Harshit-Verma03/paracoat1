# Paracoat Dispatch Studio — Feature checklist

**Audit date:** 26 November 2026  
**Purpose:** Distinguish implemented prototype capabilities from verified behavior, limitations, and future work.  
**Status key:** ✅ implemented and covered by automated checks; 🟡 implemented in the app but has a meaningful limitation or has not been fully verified; ❌ missing.

## Current release scope

A single-depot, single-day, dispatcher-reviewed logistics planning prototype. It is not yet a complete EasyCargo replacement or production dispatch platform. The requested Streamlit/Python direction is in place. Parcel placements are shown in a manifest, interactive basic 3D box preview, and approximate 2D cargo-layer view; advanced 3D VFX remain deferred. Plan dispatch includes an extended synthetic scenario and downloadable workbook.

## Implemented features

### Dashboard and navigation

- 🟡 Streamlit workspaces: Overview, Plan dispatch, Shipments, Parcel catalog, Fleet, Plan history, Settings, and About. Extended demo data can be used directly for planning, downloaded as a workbook, or added append-only to catalogs.
- ✅ Interactive operations dashboard with date/status/search filters, selectable Ready/Open shipment rows, prefilled dispatch handoff, coordinate readiness alerts, compact KPIs, fleet snapshot, and quick navigation to shipment/fleet/catalog workspaces.
- 🟡 Professional compact styling, dense metric cards, clear page hierarchy, and action-oriented tables. Browser visual/responsive QA remains to be done.

### Fleet / vehicle catalog

- 🟡 Add and edit vehicles with ID/name, registration, type, availability, payload, interior and door dimensions, volume, pallet capacity, fuel, cost/km, depot, and notes. Active registration plate uniqueness and physical door/interior/usable-volume consistency are validated on save; configured usable volume constrains fleet assignment. Fleet form coverage now includes invalid door dimensions and a successful save. Restore of archived vehicles remains missing.
- 🟡 Archive vehicles; archived fleet is excluded from planning.
- 🟡 Fleet records feed the daily dispatch planner.

### Parcel / SKU catalog

- 🟡 Add/edit parcel types with SKU, description/category, dimensions, per-unit weight, stackable flag, maximum stack weight, allowed orientation, and handling notes.
- 🟡 Archive parcel types; historical shipment lines retain references, while new shipments use active SKUs.
- 🟡 Variable parcel dimensions are supported by the geometric fit heuristic.

### Shipments and daily selection

- ✅ Create and edit unallocated shipments with ID, customer/site, address, required date, optional coordinates, priority, delivery window, service time, notes, SKU lines, and quantities.
- ✅ Soft-cancel unallocated shipments; retain their records and prevent cancelled items from being selected for planning.
- 🟡 Status board filters shipment lifecycle states and permits safe manual updates among Ready, Open, and On hold. Planned/Dispatched/Cancelled transitions are guarded.
- 🟡 Daily planning allows shipment selection and editable quantity per parcel line; quantity zero holds a line back.

### Planning and routing

- ✅ Input normalization and validation for orders, parcels, fleet, depot, quantities, dimensions, weights, coordinates, and delivery-window syntax.
- ✅ OR-Tools vehicle-routing plan with fallback behavior if the dependency is unavailable.
- ✅ Whole-order assignment checks payload and heuristic parcel fit; infeasible orders are reported.
- 🟡 Distance or travel-time objective, departure time, optimizer time limit, OSRM-compatible estimates, and labelled straight-line fallback.
- 🟡 Single depot and one planning day only; no split orders. Addresses require manually supplied coordinates.

### Capacity, review, editing, exports

- ✅ Payload demand uses per-unit weight times quantity and checks vehicle capacity.
- ✅ Geometric heuristic considers orientations, vehicle interior dimensions, full support footprints, stackability, cumulative stack weight, and configured maximum load above. Unknown stack limits block a fit when a parcel rests on that base.
- 🟡 Route summary, estimated mileage/duration, weight utilization, fit status, exceptions, interactive Folium map, whole-order reassignment, stop reordering with window checks, draft parcel-unit orientation changes with repack validation, and Excel export.
- ✅ Bounded CP-SAT fleet assignment chooses vehicle groups; an orientation-aware placement heuristic validates each load; infeasible load groups and route-window failures trigger alternative assignment candidates. The route solver sequences accepted vehicle groups.
- ✅ Dispatcher approval records name and an approval event; catalog selections are allocated in the same transaction.
- ✅ Approved plans lock route edits and cannot be overwritten through the saved-plan helper.

### Persistence and deployment artifacts

- 🟡 SQLite stores catalogs, shipments, plans, and events for local single-user use.
- 🟡 Dockerfile, .dockerignore, Streamlit configuration, README, and GitHub Actions workflow are present.

## Verified baseline

- ✅ Current suite: 39 tests pass with `.venv/bin/python -m pytest -q` (run 26 Nov 2026).
- ✅ The extended synthetic dataset contains 24 shipments, 147 variable-size parcel units, 8 available vehicles, and 3,177 kg total freight. A full dispatch AppTest assigns all freight across multiple vehicles and produces the manifest and interactive 3D preview.
- ✅ Tests cover input validation, geometry and stacking, vehicle assignment, route/time-window checks, shipment editing/cancellation, approvals and allocation safeguards, workbook round-trip/export, 2D/3D rendering, and all eight workspace startup checks.
- 🟡 A 20-shipment synthetic case checks deterministic assignment and geometric invariants. Performance benchmarks at larger load sizes, browser visual QA, full fleet/catalog UI interactions, provider failures, and plan restoration are still open.
- ⚠️ Test-run warnings: urllib3 reports LibreSSL compatibility; OR-Tools emits SWIG deprecation warnings. They do not fail the suite.

## Ordered implementation roadmap

Work through these phases in order. Each item includes its use case and a completion check so the next feature builds on tested behavior. Priorities indicate implementation order, not a claim that the app is production-ready.

### P0 — Finish and verify the initial dispatcher prototype

1. **End-to-end workflow QA** — 🟡 Streamlit AppTests now cover dashboard handoff, shipment create/edit/cancel, fleet create with invalid-dimension rejection, parcel create/archive, dispatch manifest/orientation controls, and extended multi-vehicle dispatch. Still test shipment selection/quantity changes, fleet/parcel editing, multi-vehicle reassignment, stop reordering, orientation edits, approval, history, exports, multiple-vehicle and delivery-window UI errors, and browser layout at desktop/tablet widths. **Use case:** Catch UI, state, and route-edit issues before dispatchers rely on the workflow. **Depends on:** Current app. **Done when:** The main user journeys and error paths have repeatable AppTests, and pages have been visually reviewed at desktop/tablet widths.
2. **Realistic scale and solve feedback** — Benchmark 24, 50, and 100+ shipment/parcel loads; record planning time, candidate count, unassigned reasons, and whether the time limit was reached. Compare heuristic fit results against independently constructed known-feasible cases to characterize false negatives. **Use case:** Know which daily volumes the current prototype handles and explain slow or incomplete plans. **Depends on:** Extended demo and stable test fixtures. **Done when:** A benchmark report records results and the UI clearly distinguishes optimal, feasible-within-limit, and incomplete outcomes.
3. **Manual parcel position editing** — Let dispatchers adjust a unit’s x/y/z position with orientation preserved or deliberately changed; repack/revalidate the entire vehicle and reject collisions, boundary violations, unsupported stacks, unknown stack limits, or payload violations. **Use case:** Correct a workable loading arrangement when the heuristic’s placement order is impractical. **Depends on:** Stable unit manifest, geometric validation, edit persistence. **Done when:** A valid move updates the 3D/2D views and saved draft; an invalid move leaves the plan unchanged and explains why.
4. **Parcel-unit moves and optional shipment splitting** — Add an explicit split control; keep whole-shipment assignment as the default. Update vehicle loads, routes, remaining quantities, approval ledger, and plan history transactionally. **Use case:** Move selected packages when a shipment exceeds one vehicle’s capacity or operationally needs multiple trucks. **Depends on:** Position editing, parcel-unit identity, route revalidation, allocation-ledger changes. **Done when:** Each unit is assigned at most once, split quantities reconcile to shipment quantities, both affected vehicles pass load checks, and route/approval/export records reflect the split.
5. **Operational manifests and route exports** — Add print-friendly per-vehicle load sheets and PDF/CSV exports with route stops, parcel IDs, orientation, position, dimensions, weight, handling notes, and exceptions. **Use case:** Give warehouse and driver teams an actionable dispatch packet. **Depends on:** Stable manifests and route data. **Done when:** Each vehicle has a printable, exportable manifest and exports match the approved plan.

### P1 — Stronger dispatcher controls and records

6. **Catalog validation and import/export** — ✅ First increment: active vehicle registration uniqueness, door/interior consistency, usable-volume consistency, and configured volume enforcement during planning. 🟡 Remaining: clearer duplicate shipment/SKU/vehicle ID pre-submit messages, bulk shipment/SKU/fleet import, reconciliation, catalog export, and restore actions for archived vehicles/SKUs. **Use case:** Reduce data-entry errors, recover archived records, and load larger customer/fleet lists efficiently. **Depends on:** P0 workflow QA. **Done when:** Invalid/conflicting files show row-level feedback and valid imports can be reconciled without duplicate records.
7. **Plan versioning, audit, undo, and approval reversal** — Record who changed routes, assignments, orientations, positions, shipment statuses, catalogs, archives, approvals, and revocations; allow safe route/edit undo/redo, historical plan restore/side-by-side comparison, PDF/CSV history exports, and controlled reopen/revoke with allocation reversal. **Use case:** Explain changes and recover from an incorrect draft or approval. **Depends on:** Transactional parcel split and allocation behavior. **Done when:** Every edit is attributable, approved-plan reversal is permission-checked and atomic, and history can restore or compare revisions.
8. **Parcel and delivery data enrichment** — Add shipment-specific parcel dimensions, units/conversions, handling classes (fragile, temperature-controlled, hazardous, and compatibility), barcode/photo references, pallets/packaging variants, customer/address book, shipment activity timeline, status timestamps, and proof-of-delivery fields. **Use case:** Represent real freight and avoid treating one catalog SKU as identical for every shipment. **Depends on:** Versioned schema/migrations and expanded validation. **Done when:** New fields survive catalog editing, planning, exports, and historical snapshots.

### P2 — Operational routing and fleet constraints

9. **Priority and dispatch rules** — Add priority-aware objectives and hard rules for customer/vehicle eligibility, loading cutoff, service territory, road restrictions, and broader fuel/emissions/operating-cost objectives (per-km cost is already supported). **Use case:** Prefer urgent jobs and ensure assignments comply with business policies. **Depends on:** Benchmark baseline and clear hard-versus-soft objective settings. **Done when:** Test cases demonstrate each rule changing or rejecting a plan as expected.
10. **Drivers, shifts, hours, and maintenance availability** — Add driver assignment, shifts, breaks, work-hour limits, inspection dates, maintenance blocks, availability calendars, vehicle history, and restore. **Use case:** Avoid assigning routes to unavailable drivers or vehicles and expose upcoming capacity gaps. **Depends on:** Fleet/driver master data and P2 dispatch rules. **Done when:** Route duration and vehicle availability obey calendars, shifts, and maintenance status.
11. **Geocoding and approved routing providers** — Add address geocoding, coordinate/address checks, provider health, visible provider errors, clear fallback controls, approved road routing, live traffic/ETAs where supported, route locking/comparison, navigation links, and dispatch integration. The public OSRM demo endpoint is for development, not production routing. **Use case:** Reduce manual coordinate work and improve route estimates for real operations. **Depends on:** Provider selection, credentials/secrets handling, and monitoring. **Done when:** Provider failures are visible, fallbacks are explicit, and users can validate selected routes/ETAs.
12. **Time-window semantics and multi-depot/day planning** — Verify time zones, overnight windows, waiting/service-time and route-duration semantics, then support multiple depots and planning days. **Use case:** Plan cross-day and multi-branch operations without misreading delivery windows. **Depends on:** P0 route tests and P2 provider/routing behavior. **Done when:** Overnight and cross-depot fixtures have correct feasible/infeasible results and exports.

### P3 — Prepare for shared production use

13. **Identity and roles** — Add authentication and dispatcher/admin/driver permissions for catalogs, planning, approval, and history. **Use case:** Restrict sensitive actions and identify accountable users. **Depends on:** Deployment target and organization identity provider. **Done when:** Unauthorized actions are blocked and permitted actions are audited.
14. **Managed database and concurrency** — Move SQLite to PostgreSQL or equivalent; add schema migrations, backups/restore, retention, transaction locking, idempotent approvals, and UUID-based plan IDs (current timestamp IDs still need multi-process collision protection). Prevent concurrent dispatchers from approving overlapping shipment quantities. **Use case:** Support shared use without losing data or double-allocating shipments. **Depends on:** Stable schema and role model. **Done when:** Concurrent approvals cannot consume the same quantity twice and restore/migration procedures are tested.
15. **Operations and security controls** — Add structured logs, monitoring, error reporting, health checks, secrets management, privacy/retention review, and deployment runbooks. **Use case:** Detect and resolve production failures while protecting customer and route data. **Depends on:** Managed deployment environment. **Done when:** Health and error signals are actionable and backup/incident procedures are documented and exercised.

### P4 — Expand into a logistics platform

16. **Driver mobile workflow** — Add route handoff, stop status, GPS/location updates, proof of delivery, exception reporting, and customer notifications. **Use case:** Connect the dispatch plan to execution in the field. **Depends on:** Authentication, APIs, and production route data.
17. **ERP/API/webhook integrations** — Add order ingestion, update callbacks, reconciliation, and integration monitoring. **Use case:** Avoid manual re-entry between sales/order systems and dispatch.
18. **Advanced loading and 3D analysis** — Evaluate a stronger or exact 3D packing solver and improve visualization; investigate loading sequence, axle load, centre of gravity, restraints, stability, and compatibility with domain validation. **Use case:** Make loading review more informative and catch additional physical constraints. **Depends on:** Verified vehicle/load data, scale benchmarks, and specialist requirements. **Done when:** Clearly bounded checks are validated; visualization is never described as safety certification unless supported by a validated process.

## Current technical limits

- Parcel placement and fleet assignment are heuristic/bounded; they can miss a feasible arrangement and do not prove globally optimal packing.
- The current assignment keeps a whole shipment on one vehicle. Parcel-level splitting is a planned behavior change and must update allocation/approval semantics before it is enabled.
- The 3D preview shows estimated box positions for inspection; axle load, centre of gravity, securement, stability, and safe loading sequence are not evaluated.
- The prototype is single-depot and single-day, uses manually supplied coordinates, and is designed for local single-user SQLite use.

## Operational note

The application provides planning estimates. The geometric-fit heuristic does not establish a safe or legally compliant load. Routes/ETAs depend on provider quality and supplied coordinates. Human review remains necessary before dispatch.

