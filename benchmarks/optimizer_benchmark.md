# Loading optimizer benchmark

**Run:** 28 November 2026, Python 3.9.6, macOS ARM64 27.2.1  
**Scenario:** scaled synthetic Delhi demo data; 3 second CP-SAT limit per size, up to 2 assignment candidates, 2 repetitions.  
**Code state:** benchmarked the local uncommitted optimizer changes based on HEAD `0fc185a`.  
**Interpretation:** local, machine-specific results. These are planning-quality and performance observations, not operational guarantees.

**Scope note:** The table below is the pre-delivery-order assignment/packing baseline. It does not include route-stop access constraints and must not be used to claim the current end-to-end planner can assign the same volume.

| Shipments | Parcel units | Vehicles available | Assigned | Vehicles used | Wall time | CP-SAT status | Result |
|---:|---:|---:|---:|---:|---:|---|---|
| 24 | 147 | 9 | 24 (both runs) | 8 (both runs) | 3.28–3.37 s | FEASIBLE | Feasible; optimum not proven |
| 50 | 307 | 18 | 50 (both runs) | 16 (both runs) | 3.63–3.63 s | FEASIBLE | Feasible; optimum not proven |
| 100 | 611 | 36 | 90–91 | 31–32 | 5.06–5.10 s | FEASIBLE | Partial; 9–10 shipments unassigned |

The 100 shipment result had a 3 second CP-SAT limit but took about 5.1 seconds wall time because packing validation and candidate processing occur outside the solver's time limit. The 100 shipment case varied by one assigned shipment across repetitions. Unassigned results included both packing constraint issues and shipments the solver did not assign. Review each reason in the JSON report before treating this as a capacity limit.

All 51 independently feasible floor/grid/stack layouts passed the packer, ranging from 6 to 37 units. This includes 30 deterministic random tilings of a 100 × 100 cm floor with varied parcel widths and 8 generated four-column vertical stack patterns. Eight cases needed backtracking; the median took 0.038 seconds and the slowest took 4.40 seconds with 2,058 backtracks across placement strategies. The other 43 cases needed no backtracking. This set checks these constructed layouts, but it does not establish a general false-negative rate for irregular real freight.

## Delivery-order end-to-end check

After adding rear-door stop-order constraints, an overall optimization time budget, retained partial candidates, and adaptive smaller stop groups after repeated geometric failures, one 20-second run of the 24-shipment / 147-unit synthetic scenario produced 82 parcel placements for 13 shipments across 8 vehicles; 11 shipments remained unassigned. The optimizer checked 3 candidates and improved on its greedy seed. A separate 30-second run produced 59 units for 10 shipments across 6 vehicles, so a longer limit did not improve this local result; the search path is sensitive to the time budget and needs repeated-run analysis. This remains a partial plan. The dispatch UI reports partial results and reasons instead of claiming a complete plan. The pre-change figures above are not comparable.

## Reproduce

From the repository root:

```bash
python scripts/benchmark_optimizer.py --sizes 24 50 100 --time-limit-seconds 3 --candidate-iterations 2 --repeats 2
```

The full machine-specific run data is in [optimizer_benchmark.json](optimizer_benchmark.json). Increase time limits or repeat runs when comparing code changes; compare assignment feasibility and reasons as well as solve time.
