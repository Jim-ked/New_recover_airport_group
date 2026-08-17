# Step 7 — original algorithm chain adaptation and resource alignment (complete candidate)

Working rule: this is an in-place adaptation of the GitHub `26-4-4` SA -> LP -> MIP logic into the new `New_recover_airport_group` domain boundary. It is not a second optimizer.

## Completed algorithm boundary

- Immutable `RunSnapshot -> ds/run_params/runtime` adapter; algorithm execution never re-reads mutable scene/parameter/runtime files.
- Complete feasible sortie paths with return-airport aircraft support, per-leg `max_range`, time-indexed navigation delay and canonical full path identity.
- Shared `model_facts` for hard demand, departure/arrival capacity, aircraft flow, airport-local shared consumables and objective coefficients.
- Confirmed fuel rule: `actual_fuel_required = base_mission_fuel / (1 - reserve_ratio)`, with base fuel covering outbound flight + mission work + return flight.
- Path-variable `model_builder`: one `X_PATH` variable per complete sortie; no independent outbound/return decision variables.
- `cluster_selector` LP evaluation reuses the same `X_PATH` model and `model_facts.objective_coefficients` as final MIP; SA seed/neighbour/Metropolis/cooling policy is retained.
- Cluster LP raises on objective drift instead of silently reporting a second F1/F2/F3 calculation.
- Canonical `Solution` with complete `sortie_chains`; no legacy split `operations`, and no canonical Solution on infeasible runs.
- Solution export independently re-validates hard demand, capacity, aircraft inventory and shared resources before emitting facts.
- Snapshot-only `backend.algorithm.run_once(snapshot)` boundary. It accepts no scene path, parameter path, runtime path or result directory.
- `algorithm_seed` is frozen into `RunConfig`/`RunSnapshot` (default 42) and is consumed by the runner.

## Completed resource-state alignment

Resource semantics are intentionally separated by physical state model.

1. `per_window_capacity`: airport departure/arrival capacity. Usage in one slot does not consume future-slot capacity.
2. `consumable_stock_with_replenishment`: fuel/material/munition. Inventory evolves with damage-adjusted boundary, actual replenishment and task consumption.
3. `retained_recyclable`: aircraft. Aircraft are occupied by a sortie and released after return/reset; they are not cumulative consumables.
4. `retained_service`: maintenance teams/support equipment. Semantic category is frozen, but these assets are not yet in the optimizer fact chain and therefore no canonical Metrics are fabricated for them.

For consumables:

- Airport baseline stores `initial_quantity` and `replenishment_capacity_per_window` separately.
- Situation stores sparse actual replenishment arrivals by `(airport, resource_type, slot)`.
- Missing actual replenishment means zero; capacity alone never creates inventory.
- Actual replenishment must be non-negative and cannot exceed the per-window replenishment capacity.
- Snapshot timeview exposes damage-adjusted base boundary, replenishment capacity, actual replenishment, cumulative replenishment and the final effective resource boundary used by LP/MIP.
- Replenishment arriving at slot `t` is available for slot `t` consumption.
- Actual replenishment before a cropped algorithm horizon is folded into the cumulative resource boundary at the first visible slot.
- Current Damage resource effects are not silently reused to reduce replenishment capacity. If that behavior is required later, it needs an explicit Damage effect.
- Remaining ratio is always relative to the frozen pre-damage initial stock. Zero initial stock yields `null`, not 0 or infinity.

The RunSnapshot schema is now `run_input_snapshot_v4`.

## Completed Metrics / comparison alignment

- `backend.analysis.metrics` emits `metrics.v1` from RunSnapshot + canonical Solution/model facts; frontend business arithmetic is not required.
- Native analysis slot is 15 minutes.
- All airports are retained in canonical Metrics; no Top-N truncation.
- Successful-run Metrics do not re-express hard demand as `completion_ratio`/`shortfall`.
- Airport collaboration concentration uses raw departure-share HHI, numeric only, with no invented grade thresholds.
- Consumable resource Metrics expose initial stock, replenishment capacity/actual/cumulative, damage-adjusted boundary, consumption, remaining and initial-stock-relative remaining ratio.
- Aircraft Metrics use a separate retained/recyclable state timeline.
- R0/R1/R2 comparison is calculated in backend analysis, not in frontend JavaScript.
- R0/R1/R2 must be three distinct Run IDs, share the same frozen Situation/catalog/OD facts, use identical preference/alpha/aircraft-weight/time-limit/algorithm-seed facts, and satisfy the fixed roles: R0=no damage/no cluster, R1=target damage/no cluster, R2=same target damage/cluster on.
- Comparison requires matching Metrics schema and slot size before deltas are produced.
- Strict configuration/multi-scenario comparability checks are implemented separately.

## Automated baseline

Latest packaging-environment regression:

- `147 tests passed`, `0 failed`.
- Full output: `ALIGNMENT_TEST_RESULT.txt`.
- Includes domain/storage/service lifecycle, migration v010, Situation replenishment persistence, snapshot adapter, Step-7 path/model/cluster/Solution/runner, Metrics and comparison contract tests.

PySCIPOpt is not installed in this packaging runtime, so model construction/evaluation is exercised through the injectable symbolic model. The real SCIP solve remains an environment-validation gate, not a known code-semantic blocker.

## Remaining gates / explicit non-coverage

1. Run one no-cluster and one cluster-enabled canonical RunSnapshot end to end in the project's actual PySCIPOpt environment and verify the same invariants against a real optimizer.
2. Maintenance teams and support equipment are only classified as retained service assets; they must not appear as calculated canonical Metrics until their domain/optimizer semantics are implemented.
3. Damage effects on replenishment capacity are not defined. Add an explicit Damage effect if the business later requires them.
4. Web/API/frontend binding remains intentionally outside this step. The next layer should consume these canonical facts rather than recreate business calculations.
