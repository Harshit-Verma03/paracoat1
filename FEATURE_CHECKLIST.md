# Paracoat Dispatch Studio — Feature checklist

**Audit date:** 28 November 2026
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
- 🟡 Geometric packer combines four deterministic parcel orderings with three placement strategies under bounded backtracking, treats a vehicle load as all-or-nothing, and checks orientations, vehicle interior dimensions, door clearance, full support footprints, stackability, cumulative stack weight, and configured maximum load above. Unknown stack limits block a fit when a parcel rests on that base. Backtracking is bounded and does not prove a load infeasible when its search limit is exhausted.
- 🟡 Route summary, estimated mileage/duration, weight utilization, fit status, exceptions, interactive Folium map, whole-order reassignment, stop reordering with window checks, draft parcel-unit orientation changes with repack validation, and Excel export.
- 🟡 Bounded CP-SAT fleet assignment chooses vehicle groups; its lexicographic objective is shipments served, vehicles used, selected distance/time proxy, configured operating cost, then fleet volume. Payload is conservatively represented in grams. The orientation-aware packer uses alternate orderings and bounded backtracking to validate complete loads. Rejected assignment candidates trigger alternatives, but packing rejection after bounded search is not proof of infeasibility. Solver output distinguishes an optimal assignment under the assignment model from a feasible result found after retries or a search limit. Distance routes use nearest-neighbor plus bounded 2-opt refinement; time-window/travel-time routes use OR-Tools.
- ✅ Dispatcher approval records name and an approval event; catalog selections are allocated in the same transaction.
- ✅ Approved plans lock route edits and cannot be overwritten through the saved-plan helper.

### Persistence and deployment artifacts

- 🟡 SQLite stores catalogs, shipments, plans, and events for local single-user use.
- 🟡 Dockerfile, .dockerignore, Streamlit configuration, README, and GitHub Actions workflow are present.

## Verified baseline

- ✅ Full suite: 53 tests pass with `.venv/bin/python -m pytest -q` (28 Nov 2026). Python compilation passes with a workspace-safe bytecode cache, and `git diff --check` is clean. Warnings are limited to the environment's LibreSSL/urllib3 notice and OR-Tools SWIG deprecations.
- 🟡 The extended synthetic dataset contains 24 shipments, 147 variable-size parcel units, 8 available vehicles, and 3,177 kg total freight. The latest 30-second route-aware run assigned 19 shipments / 118 parcel units across 8 vehicles. The five remaining shipments each individually fit all eight demo vans when tested without competing cargo; the current failure is in combined packing and search, not individual vehicle capacity. A 60-second run before the latest swap-repair change assigned 20 / 125, so these results are not yet repeatable or monotonic with solve time. Repair now tries every capacity-eligible van and bounded one-order swaps, but complete route-ordered assignment remains unverified and approval/export stay blocked for partial plans.
- ✅ Tests cover input validation (including non-finite/malformed optimizer fields), geometry and stacking, vehicle assignment, distance/time objective priority, fractional payload boundaries, bounded route refinement, route/time-window checks, shipment editing/cancellation, approvals and allocation safeguards, workbook round-trip/export, 2D/3D rendering, position edits, optimizer candidate diagnostics, and all eight workspace startup checks. Regression tests verify that a failed candidate is not presented as proof of shipment infeasibility.
- 🟡 The recorded 24/50/100-shipment benchmark in [benchmarks/optimizer_benchmark.md](benchmarks/optimizer_benchmark.md) is a legacy pre-delivery-order baseline and must not be used as a current end-to-end capacity claim: it fully assigned the 24/50 cases, while the 100-shipment runs assigned 90/100 and 91/100. Fifty-one independently constructed feasible floor/grid/stack layouts passed; eight required backtracking and the slowest took 4.4 seconds. More irregular false-negative cases and scale beyond 100 remain open. Browser visual QA, full fleet/catalog UI interactions, provider failures, and plan restoration are also still open.
- ⚠️ Test-run warnings: urllib3 reports LibreSSL compatibility; OR-Tools emits SWIG deprecation warnings. They do not fail the suite.

## Ordered implementation roadmap

Work through these phases in order. Each item includes its use case and a completion check so the next feature builds on tested behavior. Priorities indicate implementation order, not a claim that the app is production-ready.

### P0 — Finish and verify the initial dispatcher prototype

1. **End-to-end workflow QA** — 🟡 Streamlit AppTests cover dashboard handoff, shipment create/edit/cancel, fleet create with invalid-dimension rejection, parcel create/archive, dispatch manifest/orientation/position controls, optimizer diagnostic visibility, and a delivery-order-constrained partial extended scenario. The scale test verifies assignment-to-route vehicle pairs, complete unit manifests, per-vehicle payload/fit, and reverse-stop load sequence; it caught and now guards a candidate/retained-assignment mismatch. Incomplete plans are explicitly flagged, cannot be approved, and cannot download as dispatch workbooks. Optimizer exception wording also has a regression test so candidate failures are not framed as proof of infeasibility. Still test shipment selection/quantity changes, fleet/parcel editing, multi-vehicle reassignment, stop reordering, approval, history, exports, multiple-vehicle and delivery-window UI errors, and browser layout at desktop/tablet widths. **Use case:** Catch UI, state, and route-edit issues before dispatchers rely on the workflow. **Depends on:** Current app. **Done when:** The main user journeys and error paths have repeatable AppTests, and pages have been visually reviewed at desktop/tablet widths.
2. **Realistic scale and solve feedback** — ✅ Initial baseline complete and recorded for two runs at 24, 50, and 100 shipments, including solve/wall time, assigned counts, candidate count, solver state, and unassigned reasons. Fifty-one known-feasible floor/grid/stack packings pass, including 30 varied floor tilings and 8 vertical-stack patterns; eight require backtracking, with a 4.4 second maximum in this fixture set. The 100-shipment result is partial and not proven optimal; broader real-world irregular cases and larger scale remain follow-up work. **Use case:** Know which daily volumes the current prototype handles and explain slow or incomplete plans. **Depends on:** Extended demo and stable test fixtures. **Completion evidence:** [Benchmark report](benchmarks/optimizer_benchmark.md), JSON run data, and CP-SAT/time-limit feedback in the dispatch review.
3. **Manual parcel position editing** — ✅ Dispatchers can set a parcel unit’s x/y/z coordinates with its current orientation preserved; other units may be repacked around it. The complete vehicle load is revalidated, invalid edits are rejected without mutating the source plan, approved plans remain locked, and successful changes update the saved draft and 2D/3D manifest views. **Use case:** Correct a workable loading arrangement when the heuristic’s placement order is impractical. **Completion evidence:** Dispatch API and AppTest cover valid move, invalid boundary/collision, plan immutability, approval lock, and UI control flow.

4. **Delivery-order cargo placement** — ✅ Initial rear-door sequencing is connected to the selected route: earliest stops are constrained nearest the rear door, later stops deeper in the vehicle, and the warehouse loading sequence is the reverse of the route. The manifest includes stop ranks and numbered physical load sequence; the 2D view labels the rear-door end; route reordering repacks and validates access; optimizer candidate packing runs after route sequencing. **Assumption:** single rear door at X = vehicle length, with conservative same-cross-section access checks. Side/multiple doors, complex access paths, unload simulation, and safety validation remain future work. **Use case:** Avoid placing a later delivery in front of freight needed earlier. **Completion evidence:** packing tests verify stop 1 lies behind stop 2, manifest loading order runs latest-stop-first, and route edits repack the selected vehicle.

5. **End-to-end scale with delivery-order constraints** — 🟡 The current 24-shipment, 147-unit synthetic scenario remains partly assigned. After adding bounded parcel-order strategies, multiple greedy seeds, repair across every eligible van, and bounded one-order swaps, the latest 30-second route-aware run assigned 19 shipments / 118 units across 8 vehicles. A 60-second run before the latest swap-repair change assigned 20 / 125, showing that longer runs and search changes still produce variable results. The five omitted shipments each fit individually in every demo van, but combined route-ordered packing times out. No complete delivery-order-constrained assignment has been verified. Approval and final dispatch workbook export remain blocked until every selected shipment, vehicle route, and parcel-unit manifest passes consistency checks. **Use case:** Produce complete, explainable dispatch plans without losing delivery access. **Depends on:** Delivery-order cargo placement and repeatable benchmarks. **Done when:** Every selected shipment is assigned in a validated multi-vehicle plan within a declared solve budget, repeat runs are stable, and no partial plan can be approved or exported as dispatch-ready.
6. **Parcel-unit moves and optional shipment splitting** — Add an explicit split control; keep whole-shipment assignment as the default. Update vehicle loads, routes, remaining quantities, approval ledger, and plan history transactionally. **Use case:** Move selected packages when a shipment exceeds one vehicle’s capacity or operationally needs multiple trucks. **Depends on:** Position editing, parcel-unit identity, route revalidation, allocation-ledger changes. **Done when:** Each unit is assigned at most once, split quantities reconcile to shipment quantities, both affected vehicles pass load checks, and route/approval/export records reflect the split.
7. **Operational manifests and route exports** — Add print-friendly per-vehicle load sheets and PDF/CSV exports with route stops, parcel IDs, orientation, position, dimensions, weight, handling notes, and exceptions. **Use case:** Give warehouse and driver teams an actionable dispatch packet. **Depends on:** Stable manifests and route data. **Done when:** Each vehicle has a printable, exportable manifest and exports match the approved plan.

### P1 — Stronger dispatcher controls and records

8. **Catalog validation and import/export** — ✅ First increment: active vehicle registration uniqueness, door/interior consistency, usable-volume consistency, and configured volume enforcement during planning. 🟡 Remaining: clearer duplicate shipment/SKU/vehicle ID pre-submit messages, bulk shipment/SKU/fleet import, reconciliation, catalog export, and restore actions for archived vehicles/SKUs. **Use case:** Reduce data-entry errors, recover archived records, and load larger customer/fleet lists efficiently. **Depends on:** P0 workflow QA. **Done when:** Invalid/conflicting files show row-level feedback and valid imports can be reconciled without duplicate records.
9. **Plan versioning, audit, undo, and approval reversal** — Record who changed routes, assignments, orientations, positions, shipment statuses, catalogs, archives, approvals, and revocations; allow safe route/edit undo/redo, historical plan restore/side-by-side comparison, PDF/CSV history exports, and controlled reopen/revoke with allocation reversal. **Use case:** Explain changes and recover from an incorrect draft or approval. **Depends on:** Transactional parcel split and allocation behavior. **Done when:** Every edit is attributable, approved-plan reversal is permission-checked and atomic, and history can restore or compare revisions.
10. **Parcel and delivery data enrichment** — Add shipment-specific parcel dimensions, units/conversions, handling classes (fragile, temperature-controlled, hazardous, and compatibility), barcode/photo references, pallets/packaging variants, customer/address book, shipment activity timeline, status timestamps, and proof-of-delivery fields. **Use case:** Represent real freight and avoid treating one catalog SKU as identical for every shipment. **Depends on:** Versioned schema/migrations and expanded validation. **Done when:** New fields survive catalog editing, planning, exports, and historical snapshots.

### P2 — Operational routing and fleet constraints

11. **Priority and dispatch rules** — Add priority-aware objectives and hard rules for customer/vehicle eligibility, loading cutoff, service territory, road restrictions, and broader fuel/emissions/operating-cost objectives (per-km cost is already supported). **Use case:** Prefer urgent jobs and ensure assignments comply with business policies. **Depends on:** Benchmark baseline and clear hard-versus-soft objective settings. **Done when:** Test cases demonstrate each rule changing or rejecting a plan as expected.
12. **Drivers, shifts, hours, and maintenance availability** — Add driver assignment, shifts, breaks, work-hour limits, inspection dates, maintenance blocks, availability calendars, vehicle history, and restore. **Use case:** Avoid assigning routes to unavailable drivers or vehicles and expose upcoming capacity gaps. **Depends on:** Fleet/driver master data and P2 dispatch rules. **Done when:** Route duration and vehicle availability obey calendars, shifts, and maintenance status.
13. **Geocoding and approved routing providers** — Add address geocoding, coordinate/address checks, provider health, visible provider errors, clear fallback controls, approved road routing, live traffic/ETAs where supported, route locking/comparison, navigation links, and dispatch integration. The public OSRM demo endpoint is for development, not production routing. **Use case:** Reduce manual coordinate work and improve route estimates for real operations. **Depends on:** Provider selection, credentials/secrets handling, and monitoring. **Done when:** Provider failures are visible, fallbacks are explicit, and users can validate selected routes/ETAs.
14. **Time-window semantics and multi-depot/day planning** — Verify time zones, overnight windows, waiting/service-time and route-duration semantics, then support multiple depots and planning days. **Use case:** Plan cross-day and multi-branch operations without misreading delivery windows. **Depends on:** P0 route tests and P2 provider/routing behavior. **Done when:** Overnight and cross-depot fixtures have correct feasible/infeasible results and exports.

### P3 — Prepare for shared production use

15. **Identity and roles** — Add authentication and dispatcher/admin/driver permissions for catalogs, planning, approval, and history. **Use case:** Restrict sensitive actions and identify accountable users. **Depends on:** Deployment target and organization identity provider. **Done when:** Unauthorized actions are blocked and permitted actions are audited.
16. **Managed database and concurrency** — Move SQLite to PostgreSQL or equivalent; add schema migrations, backups/restore, retention, transaction locking, idempotent approvals, and UUID-based plan IDs (current timestamp IDs still need multi-process collision protection). Prevent concurrent dispatchers from approving overlapping shipment quantities. **Use case:** Support shared use without losing data or double-allocating shipments. **Depends on:** Stable schema and role model. **Done when:** Concurrent approvals cannot consume the same quantity twice and restore/migration procedures are tested.
17. **Operations and security controls** — Add structured logs, monitoring, error reporting, health checks, secrets management, privacy/retention review, and deployment runbooks. **Use case:** Detect and resolve production failures while protecting customer and route data. **Depends on:** Managed deployment environment. **Done when:** Health and error signals are actionable and backup/incident procedures are documented and exercised.

### P4 — Expand into a logistics platform

18. **Driver mobile workflow** — Add route handoff, stop status, GPS/location updates, proof of delivery, exception reporting, and customer notifications. **Use case:** Connect the dispatch plan to execution in the field. **Depends on:** Authentication, APIs, and production route data.
19. **ERP/API/webhook integrations** — Add order ingestion, update callbacks, reconciliation, and integration monitoring. **Use case:** Avoid manual re-entry between sales/order systems and dispatch.
20. **Advanced loading and 3D analysis** — Evaluate a stronger or exact 3D packing solver and improve visualization; investigate loading sequence, axle load, centre of gravity, restraints, stability, and compatibility with domain validation. **Use case:** Make loading review more informative and catch additional physical constraints. **Depends on:** Verified vehicle/load data, scale benchmarks, and specialist requirements. **Done when:** Clearly bounded checks are validated; visualization is never described as safety certification unless supported by a validated process.

## Current technical limits

- Parcel placement and fleet assignment are heuristic/bounded; they can miss a feasible arrangement and do not prove globally optimal packing.
- The current assignment keeps a whole shipment on one vehicle. Parcel-level splitting is a planned behavior change and must update allocation/approval semantics before it is enabled.
- The 3D preview shows estimated box positions for inspection. A rear-door stop-order heuristic is implemented, but axle load, centre of gravity, securement, stability, and verified safe handling are not evaluated.
- The prototype is single-depot and single-day, uses manually supplied coordinates, and is designed for local single-user SQLite use.

## Operational note

The application provides planning estimates. The geometric-fit heuristic does not establish a safe or legally compliant load. Routes/ETAs depend on provider quality and supplied coordinates. Human review remains necessary before dispatch.

