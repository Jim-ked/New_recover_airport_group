# Frontend Run Alignment Status

## Scope

This slice binds the first real frontend module, **Algorithm Run**, to the canonical backend APIs. It does not introduce frontend business formulas and does not revive legacy Scene/runtime APIs.

## Frozen stack

- Flask + Jinja
- Bootstrap-compatible server-rendered shell (no Vue/React rewrite)
- native JavaScript modules
- Leaflet remains reserved for Situation/GIS slices
- offline-first: no CDN references in this slice

## Added/changed backend support needed by Run UI

### Situation ownership/read model

Migration `v012_situation_metadata` adds current-record metadata:

- `owner_user_id`
- `created_at`
- `updated_at`

Rules:

- new canonical Situation saves require an explicit owner;
- owner is stable and cannot be silently reassigned;
- ordinary users only see their own saved Situations;
- admin can see all Situations, including legacy rows with no owner;
- legacy unowned rows are never silently claimed.

New read-only endpoints for this slice:

- `GET /api/situations`
- `GET /api/situations/{situation_id}`

Full Situation mutation remains a later Situation-editor slice; no member-level mutation API was added.

Run validate/submit now enforce the same Situation access rule, so knowing another user's `situation_id` does not bypass owner scope.

### Run list projection

`GET /api/runs` rows now include the frozen Run config and frozen Situation identity/name needed by Current/Queue/History. The frontend does not issue N detail requests and does not reconstruct configuration from files.

## Frontend implementation

### Shell

Files:

- `frontend/templates/base.html`
- `frontend/templates/pages/run.html`
- `frontend/static/css/run.css`
- `frontend/static/js/modules/api-client.js`
- `frontend/static/js/modules/run.js`

Primary navigation remains exactly:

1. 情境构建
2. 指标管理
3. 算法运行
4. 结果分析

Only the implemented Run page is active in this slice; unavailable modules are not backed by fake routes.

### Run configuration

The page reads saved Situations from the canonical Situation API and exposes only confirmed RunConfig business fields:

- saved Situation
- selected Damage scenario or no damage
- preference mode
- custom alpha only when custom mode is selected
- cluster on/off
- cluster size 1–8 only when cluster is on
- at most 2 core airports
- MIP time limit (no fake default)
- optional per-aircraft-type weights

`algorithm_seed` stays internal and is not exposed in the normal UI.

Form changes invalidate the last validation result. Submit stays disabled until the exact current form fingerprint has passed backend preflight.

### Current / Queue / History

- Current Run and Queue/History are read from `GET /api/runs`.
- events are incrementally read with `after_seq`.
- cancel calls the canonical cancel endpoint and preserves the documented non-immediate solver-cancel boundary.
- history-log inspection has a dedicated inspection mode and is not overwritten by periodic live refresh; “返回当前运行” exits inspection mode.
- successful History rows do **not** fake a Single Run route. “查看结果” stays disabled until the next Single Run frontend slice.

### Event presentation

The frontend consumes structured `stage`, `event`, `payload`, and `message` fields. It does not infer stage from message strings.

Backend candidate generation and LP quick evaluation may interleave. The UI groups both under one visual activity:

`候选搜索与快速评估`

The four visual activity groups are display-only:

1. 数据准备
2. 候选搜索与快速评估
3. 精确求解
4. 结果持久化

The percentage is an event/algorithm activity indicator only; it is not task completion rate or any business KPI.

## Explicitly absent

The frontend contains no:

- legacy `/api/runtime`, `/api/run/poll`, `/api/scenes` fallback;
- `scene_file`, `run_params_path`, client OD matrix or result-root input;
- task completion / shortfall / unmet calculations;
- R0/R1/R2 delta calculations;
- resource remaining formula;
- HHI formula;
- automatic "best" selection;
- fake Run IDs, fake logs, fake user names, or semantic defaults copied from the visual mock.

## Verification

Full Python suite in this worktree:

- **205 tests passed, 0 failed**

Additional frontend checks:

- Jinja parse: `base.html` and `pages/run.html` passed;
- Node syntax: `api-client.js` and `run.js` passed;
- Python backend compileall passed;
- no legacy API reference found in `frontend/`;
- no remote HTTP/HTTPS resource reference found in `frontend/`.

## Environment limitation

This container does not provide Flask runtime packages/wheels. Therefore this slice has not been claimed as Flask TestClient/browser-runtime verified here. The Flask UI/API bindings are syntax-checked and framework-neutral API/service contracts are covered by tests. Real Flask/browser smoke remains a runtime-environment acceptance step.

## Next frontend slice

The next logical slice is **Single Run**. It should consume only succeeded Run `RunSnapshot + Solution + Metrics`, keep all-airport output, and provide the future target for the currently-disabled History “查看结果” button. GIS Runtime and Results follow after that binding is stable.
