# Run Worker Alignment Status

Status: **worker-boundary complete candidate**

## 1. Execution boundary

`RunWorker.execute(run_id)` is synchronous and owns one queued Run lifecycle:

`queued -> running -> succeeded | failed | cancelled`

It loads only the immutable RunSnapshot already persisted for the Run ID. It accepts no Scene/parameter/runtime file path and never rebuilds the snapshot. Queue scheduling/thread/process ownership remains outside this class.

## 2. Success gate

A successful path is:

`claim running -> snapshot-only algorithm runner -> cancellation recheck -> Metrics derivation -> atomic Solution+Metrics persistence -> succeeded`

Canonical success is impossible if cancellation has been requested, Metrics derivation fails, or the solver is infeasible.

## 3. Cancellation

Cancellation is cooperative at algorithm stage boundaries. If cancellation arrives while a blocking solver `optimize()` owns control, the current integration cannot truthfully claim immediate solver interruption. When control returns, cancellation is checked before canonical result publication and wins over success/failure publication. Solver-level interrupt wiring is deferred to real PySCIPOpt integration.

## 4. Structured events

Internal algorithm events are mapped to public RunEvent labels without treating those labels as a strict sequential state machine. In particular, current SA candidate generation and LP quick evaluation interleave. The cluster event payload records:

`activity_semantics = candidate_generation_and_quick_evaluation_interleaved`

No fake standalone `quick_evaluation` event is emitted when the algorithm did not expose one as a distinct fact. Frontend display can later group/rename these truthful events.

## 5. Failure semantics

- solver infeasible -> `failed`, code `INFEASIBLE`, no Solution/Metrics;
- algorithm boundary error -> `failed`, code `ALGORITHM_ERROR`;
- unexpected worker failure -> `failed`, code `WORKER_ERROR`;
- observed cancellation -> `cancelled`, no canonical result.

## 6. Automated regression

Latest full regression including worker tests: **166 passed, 0 failed**.
