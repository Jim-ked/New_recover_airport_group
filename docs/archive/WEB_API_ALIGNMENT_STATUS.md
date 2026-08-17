# Web/API Alignment Status — Phase 2

## Completed boundary

`backend/web` remains a thin HTTP/API layer. It does not read SQLite directly, call the optimizer directly, derive Metrics, scan result directories, or implement comparison formulas.

Run endpoints:

- `POST /api/runs/validate`
- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `POST /api/runs/{run_id}/cancel`
- `GET /api/runs/{run_id}/situation`
- `GET /api/runs/{run_id}/solution`
- `GET /api/runs/{run_id}/metrics`

Results endpoints:

- `GET /api/results/comparable-runs?base_run_id=...&mode=multi_scenario|configuration`
- `POST /api/results/damage-comparison`
- `POST /api/results/scenario-comparison`
- `POST /api/results/config-comparison`

## Results workspaces

### Damage / optimization

Input is three real Run IDs. Backend enforces fixed R0/R1/R2 semantics and returns `R1-R0` and `R2-R1` facts. Non-comparable roles return 422.

### Multi-scenario

Input:

```json
{"run_ids": ["RUN1", "RUN2"]}
```

Contract:

- 2–6 distinct successful Runs;
- same frozen problem and run configuration except selected damage scenario;
- identical Metrics schema and time axis;
- returns per-Run summaries, timelines, all-airport rows, task/aircraft structure, resource minima and scheme structure;
- `difference_overview` reports only deterministic extrema (highest/lowest, earliest/latest);
- no `best`, recommendation or causal judgment is generated.

### Configuration comparison

Input:

```json
{
  "run_ids": ["BASE", "PLAN2"],
  "baseline_run_id": "BASE"
}
```

Contract:

- 2–5 distinct successful Runs total;
- baseline must be one of selected Runs;
- same frozen Situation/damage/object closure;
- solver time limit and algorithm seed remain fixed;
- allowed business configuration changes are compared against the explicit baseline;
- backend returns summary, timeline, all-airport, task, aircraft, resource and scheme deltas;
- frontend never computes business deltas itself.

## Run submission boundary

External submission accepts only canonical business inputs:

```json
{
  "situation_id": "S1",
  "run_config": {"...": "canonical configuration"}
}
```

Run ID is server generated. OD is derived inside the service using the recovered WGS-84 Vincenty distance semantics. Legacy file paths and client-provided OD matrices are rejected.

`/runs/validate` is side-effect free and checks Situation closure, business configuration, OD closure, solver availability, persistent queue access and identical active input. Submit reruns the same preflight.

## Auth / RBAC / CSRF

The established session security semantics are now adapted instead of recreated inside Run/Results code.

Session adapter consumes only:

- `session["user"]["user_id"]`
- `session["user"]["role"]`

Roles follow the confirmed legacy hierarchy:

- viewer: `runs.read`, `results.read`
- operator / legacy user: viewer permissions + `runs.execute`
- admin: operator permissions + administrative permissions

Run contract gates:

- validate / submit / cancel -> `runs.execute`
- list / detail / events / situation / solution / metrics -> `runs.read`
- Results APIs -> `results.read`

Unknown roles degrade to viewer, never to operator.

Canonical mutation endpoints use the established session CSRF token and `X-CSRF-Token`/form token comparison through a dedicated adapter. Tokens are compared with `secrets.compare_digest`; missing or mismatched tokens fail explicitly with `CSRF_FAILED`.

The framework-neutral APIs know only `Principal`; Flask session/cookie details do not leak into services.

## Error envelope

Canonical shape:

```json
{
  "error": {
    "code": "...",
    "message": "...",
    "field": "optional"
  }
}
```

Important security mappings include:

- unauthenticated Flask request -> 401
- missing permission -> 403 `PERMISSION_DENIED`
- CSRF failure -> 403 `CSRF_FAILED`
- owner violation -> 403 `FORBIDDEN`
- invalid comparison -> 422 `RUNS_NOT_COMPARABLE`

## Flask runtime integration

`create_app()` can now use the established Flask session adapter by default when a `secret_key` is explicitly supplied. It can still accept injected principal/CSRF resolvers for integration into a larger host application.

The current execution container has no Flask installation and no offline Flask wheel/cache. Therefore real Flask TestClient/browser HTTP smoke is **not claimed as completed**. Flask modules are lazy-imported and syntax/compile checked. Real HTTP smoke remains an environment integration gate after installing the declared runtime dependency.

`requirements.txt` declares `Flask>=2.3,<4`.

## Automated result

`python -m unittest discover -v`

- 196 tests passed
- 0 failed
