# Analysis / Metrics / Resource Alignment Status

Status: **alignment stage complete candidate**

This document records the frozen analysis boundary after aligning the current new engineering project, frontend requirements, V1.1 technical semantics and the useful parts of the old `26-4-4` result-processing code.

## 1. Authoritative inputs

Canonical analysis is derived only from:

- immutable `RunSnapshot` (`run_input_snapshot_v4`),
- canonical successful `Solution.sortie_chains`,
- the same algorithm/model facts used by the optimizer.

Analysis must not reread mutable Situation/catalog files, infer business state from logs, pair legacy outbound/return operations, or silently fall back to global/default data.

## 2. Resource state models

### Per-window capacity

Airport departure/arrival capacity is a per-slot upper bound. It is used for `available / used / utilization` analysis and does not carry consumption to future slots.

### Consumable stock with replenishment

Fuel, material and munition inventory has separate baseline stock and replenishment facts:

- `initial_quantity`: frozen pre-damage baseline stock;
- `replenishment_capacity_per_window`: maximum allowed actual replenishment in a slot;
- Situation `resource_replenishments`: sparse actual arrivals;
- damage-adjusted base boundary;
- task consumption derived from the same `model_facts.path_resource_use()` used by LP/MIP.

Capacity alone never creates stock. Missing actual replenishment is zero.

The effective resource boundary presented to the optimizer is the damage-adjusted base boundary plus cumulative actual replenishment. No automatic refill is performed.

`remaining_ratio_initial = remaining / initial_quantity` when the initial quantity is positive; otherwise it is `null`.

### Retained recyclable assets

Aircraft are tracked by availability, departure/occupation, ready release and in-use state. They are not represented as cumulative consumable inventory.

### Retained service assets

Maintenance teams and support equipment are conceptually retained/reusable service assets. Their optimizer fact chain is not implemented yet, so Metrics v1 explicitly reports this non-coverage instead of inventing numbers.

## 3. Metrics v1

`backend.analysis.metrics` emits `metrics.v1` and covers:

- summary facts;
- absolute 15-minute time axis;
- total/airport/mission/aircraft sortie timelines;
- all-airport load and capacity analysis;
- task allocation facts without success-run shortfall/completion reinterpretation;
- aircraft investment/availability facts;
- resource stock/replenishment/consumption/remaining facts;
- selected/core/participating airport and cross-return collaboration facts;
- raw departure-share HHI;
- technical state-model metadata.

No Top-N truncation is part of canonical Metrics.

## 4. Results comparability

### R0 / R1 / R2

Roles are fixed:

- R0: no damage, no cluster;
- R1: target damage, no cluster;
- R2: same target damage, cluster enabled.

They must be distinct Run IDs and share the same frozen Situation/catalog/OD facts. Strict comparison also requires the same preference mode, alpha, aircraft weights, MIP time limit and algorithm seed. Metrics schema and slot size must match before any delta is produced.

Backend calculates:

- damage delta = R1 - R0;
- cluster adjustment = R2 - R1;
- total departure/return timeline deltas;
- full-airport departure deltas;
- task and aircraft allocation deltas;
- resource-category minimum remaining-ratio deltas;
- HHI and cross-return-ratio deltas.

Frontend should render these values; it should not recalculate them.

### Multi-scenario / configuration comparisons

Dedicated comparability functions enforce which RunConfig dimensions must remain identical and which are allowed to change. A differing time limit or algorithm seed invalidates strict comparison.

## 5. Explicit decisions frozen in this alignment

- peak period = native 15-minute slot;
- remaining ratio denominator = initial stock;
- resource-category summary = minimum concrete-resource remaining ratio across participating airports;
- collaboration concentration = departure-share HHI, raw numeric value only;
- algorithm seed = RunSnapshot fact, default 42;
- no completion/shortfall cards for a successful hard-demand Run;
- all-airport canonical result, no Top-N/Other replacement;
- actual replenishment is separate from replenishment capacity;
- unspecified actual replenishment = zero.

## 6. Remaining implementation gates

- real PySCIPOpt end-to-end solve verification;
- future explicit modeling of retained service assets if required;
- future explicit Damage effect if replenishment capacity itself can be damaged;
- service/web/frontend binding on top of this canonical analysis boundary.

Automated regression at freeze point: **147 passed, 0 failed**.
