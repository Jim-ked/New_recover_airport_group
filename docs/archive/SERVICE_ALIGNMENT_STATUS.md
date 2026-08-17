# Run / Result Service Alignment Status

Status: **service-boundary batch complete candidate**

This batch connects the already-frozen RunSnapshot / Solution / Metrics / comparison facts to an application-service boundary. It deliberately does not add Flask routes or frontend code.

## 1. Run lifecycle authority

External Run status remains exactly:

`queued | running | succeeded | failed | cancelled`

No `postprocessing`, `cancelling` or `interrupted` public status is introduced.

Migration `v011_run_lifecycle` adds:

- `runs`: owner-scoped Run record, immutable snapshot hash, five-state lifecycle, cancellation request flag and terminal failure facts;
- `run_events`: append-only structured events with monotonic per-Run `seq` and the five frozen stages;
- `run_results`: insert-only canonical Solution + Metrics JSON with SHA-256 content hashes.

Queued Run creation commits the immutable RunSnapshot and RunRecord in one SQLite transaction. There is no file-path authority or result-directory scan.

## 2. Cancellation semantics

- queued Run cancellation becomes `cancelled` immediately;
- running Run remains externally `running` and sets `cancel_requested=true` until the worker actually stops;
- a cancel-requested running Run cannot publish a successful canonical result;
- terminal states remain terminal.

This preserves the five-status external contract without inventing a `cancelling` state.

## 3. Canonical result success gate

`RunResultService.persist_success()`:

1. requires the Run to be `running` and not cancellation-requested;
2. loads the immutable RunSnapshot by Run ID;
3. derives `metrics.v1` from that snapshot + canonical Solution;
4. atomically inserts Solution and Metrics together;
5. only then transitions the Run to `succeeded`.

If Metrics derivation fails, no canonical result is written and the Run remains non-successful for the worker to fail explicitly. A succeeded Run can never exist without both result payloads.

## 4. Service surfaces prepared for later Web/API binding

`RunService` provides business-input submission, owner-scoped list/detail, cancellation and incremental structured event reads.

`RunResultService` provides:

- canonical successful Solution;
- canonical Metrics;
- historical Situation read strictly from the RunSnapshot;
- Single Run bundle;
- backend-derived R0/R1/R2 comparison.

The later Web layer should translate these service calls into the already-frozen `/api/runs*` endpoints. It must not read SQLite directly, scan result files or recompute comparison formulas.

## 5. Event-stage display decision

The algorithm runner emits fine-grained internal stages (`prepare/cluster/paths/model/solve/...`). Public RunEvent stages remain the five coarse labels:

- `data_preparation`;
- `candidate_generation`;
- `quick_evaluation`;
- `exact_optimization`;
- `persistence`.

These labels are **not a monotonic state machine** and the frontend must not assume every stage appears exactly once or in a strict 1→5 sequence. SA candidate generation and LP quick evaluation interleave inside the current selector. The worker preserves that truth in structured event payload instead of fabricating a false sequential split. Frontend stage presentation may be adjusted later without changing backend facts.

## 6. Automated regression

Run/result service batch regression: **161 passed, 0 failed**.
