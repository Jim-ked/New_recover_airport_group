# Backend → Frontend Readiness Audit

Date: 2026-08-16
Scope: reverse-audit frontend product tasks and hidden interaction states against backend authority before visual/frontend reconstruction. This audit does not treat “repository method exists” or “effect mock exists” as product readiness.

## 1. Verdict

The backend is now sufficiently complete to start the main frontend reconstruction for Base Data, Situation, Run, Single Run, GIS Runtime and Results **without forcing business logic into JavaScript**. Indicator structure/editor/scoring can also be built, but automatic score→weight calculation remains intentionally unavailable until the aggregation rule is approved.

The remaining gaps are explicit and narrow; none should be hidden behind fake buttons or guessed formulas:

1. Indicator expert score → published weight formula.
2. Bulk Base Data JSON/CSV import package shape, duplicate/update policy, atomicity and batch error semantics.
3. GIS “strong connection threshold” business meaning.
4. Final report file renderer/format/template; canonical report source facts are already available.
5. Flask dependency/login-host/real-browser integration and local map assets, intentionally deferred to Codex/runtime.
6. “Settings” product scope is not defined; the visual entry must not be activated by inventing a backend.

77-item frontend capability recheck: **{'FRONTEND_ONLY': 12, 'RUNTIME_DEFERRED': 2, 'READY_BACKEND': 58, 'PRODUCT_SCOPE_UNDEFINED': 1, 'READY_SOURCE_DATA_RENDERER_DEFERRED': 2, 'SEMANTIC_BLOCKER': 2}**.
Additional hidden-interaction audit: **{'READY_BACKEND': 13, 'SEMANTIC_BLOCKER': 3, 'READY_SOURCE_DATA_RENDERER_DEFERRED': 1, 'READY_BACKEND_RUNTIME_TEST_DEFERRED': 1, 'RUNTIME_DEFERRED': 1, 'PRODUCT_SCOPE_UNDEFINED': 1}**.

## 2. Module readiness

| Module | Status | Backend judgment |
|---|---|---|
| Base Data | `READY_WITH_IMPORT_BLOCKER` | Airport/Mission/AircraftType/ResourceType CRUD, revisions, reference protection, paging/search and historical mission source are ready. Bulk JSON/CSV import semantics remain blocked by package/conflict policy. |
| Situation | `READY_BACKEND` | Whole Working Copy create/read/update/delete, owner scope, content-hash optimistic locking, base/mission copy transforms and saved-list search are ready. |
| Indicator | `READY_WITH_WEIGHT_FORMULA_BLOCKER` | Default V1.1 tree, draft versioning, protected core nodes, Expert CRUD, aggregate draft/submitted score sheets are ready. Score→weight formula is not frozen. |
| Run | `READY_BACKEND` | Validate, exact validated-input fingerprint binding, submit, persistent history search, structured events, cancel, failure detail and snapshot-based retry are ready. |
| Single Run | `READY_BACKEND` | Succeeded-only immutable Snapshot/Solution/Metrics result facts and backend-derived chart/detail leaves are ready. |
| GIS Runtime | `READY_WITH_DISPLAY_SEMANTIC_BLOCKER` | Per-window server projection, damage facts, complete sortie chains and frozen all-OD facts are ready. Strong-connection threshold meaning is unresolved; Leaflet/tile assets are runtime-deferred. |
| Results | `READY_SOURCE_DATA_RENDERER_DEFERRED` | R0/R1/R2, multi-scenario, configuration comparisons, comparable candidate filtering and report-data.v1 source facts are ready. Final downloadable report renderer/format is not frozen. |
| Cross-cutting auth/audit | `READY_CODE_RUNTIME_TEST_DEFERRED` | /api/me effective permissions, CSRF integration points, unified errors, append-only audit storage/query and Flask audit hook are coded. Login host integration and real Flask hook/browser execution are deferred. |

## 3. Hidden interactions that now have server authority

| Hidden interaction | Status | Server-side rule |
|---|---|---|
| Situation optimistic save conflict | `READY_BACKEND` | expected_content_hash on PUT/DELETE; stale edits rejected instead of overwriting. |
| Situation delete while historical/active Runs refer to it | `READY_BACKEND` | Mutable current Situation can be deleted without touching immutable Run snapshots; reference counts returned for confirmation copy. |
| Base Data stale edit / delete | `READY_BACKEND` | Catalog revision on mutable records; stale write/delete conflicts and current-Situation reference protection. |
| Restore current base data into a Situation Working Copy | `READY_BACKEND` | Nonpersistent copy-airport transform owns base/profile copy semantics; user still explicitly saves the Working Copy. |
| Select mission from historical Run | `READY_BACKEND` | GET /api/missions/history reads immutable owner Run snapshots and returns distinct frozen mission objects. |
| Run validate then another tab changes input before submit | `READY_BACKEND` | validate returns validated_input_hash; submit echoes expected_input_hash; mismatch => 409 RUN_VALIDATION_STALE and no Run created. |
| Retry failed Run after current Situation changes | `READY_BACKEND` | POST /api/runs/{id}/retry clones the failed Run immutable snapshot; it does not read current Situation/catalogs. |
| Run cancel/complete race | `READY_BACKEND` | Persistent lifecycle/cancel_requested gate prevents canonical success from being published after acknowledged cancellation. |
| History filter after mutable Situation changes | `READY_BACKEND` | Run history filters use immutable snapshots/Solution facts, not current Situation. |
| Permission-aware button visibility | `READY_BACKEND` | GET /api/me returns effective permissions; HTTP permission checks remain final authority. |
| Expert edits same score sheet in two tabs | `READY_BACKEND` | Whole expert score sheet has aggregate revision; stale replacement rejected; submitted sheet must cover every enabled L3. |
| Core indicator deletion | `READY_BACKEND` | Core/protected L3 nodes cannot be deleted; indicator set edits are draft/version controlled. |
| Expert scores become published weights | `SEMANTIC_BLOCKER` | No approved score aggregation/normalization rule. Backend stores/versions scores and reports weights unavailable rather than inventing a formula. |
| GIS displays all OD / weak connections | `READY_BACKEND` | Frozen Run snapshot OD matrix is projected by backend; frontend never recomputes distances. |
| GIS strong-connection threshold | `SEMANTIC_BLOCKER` | Meaning of threshold (distance, sorties, or another measure) is not frozen. Control must remain hidden/neutral until decided. |
| Bulk Base Data import existing IDs | `SEMANTIC_BLOCKER` | Exact CSV representation, duplicate/update conflict policy, and atomic batch semantics are not frozen. Do not silently upsert. |
| Formatted Results report download | `READY_SOURCE_DATA_RENDERER_DEFERRED` | Canonical report-data.v1 is available with export permission; PDF/DOCX/CSV rendering template/format is not frozen. |
| Operation/security audit trail | `READY_BACKEND_RUNTIME_TEST_DEFERRED` | Append-only audit repository, filtered admin API and Flask after_request hook are coded; real Flask hook execution waits for Codex runtime. |
| Session login / expiry flow | `RUNTIME_DEFERRED` | Principal/session adapter and /api/me exist; actual host login route and browser session-expiry flow are runtime integration work. |
| Settings entry | `PRODUCT_SCOPE_UNDEFINED` | No frozen settings scope; do not create a fake backend/API merely to activate the visual entry. |

## 4. Critical race fixed: validate → submit

`POST /api/runs/validate` now returns `validated_input_hash`, computed from the frozen business-input closure with `run_id` excluded. The current Run frontend sends the same value as `expected_input_hash` on `POST /api/runs`. If Situation/catalog input changes in another tab between those actions, submit returns `409 RUN_VALIDATION_STALE` and creates no Run. The user must validate again.

This closes a hidden correctness gap that a browser-only form fingerprint could not detect.

## 5. Backend capabilities added/confirmed in this pass

- Catalog mutable objects carry revisions/timestamps; stale edits/deletes are rejected.
- Airport/Mission deletion refuses deletion while referenced by current saved Situations; immutable historical Runs are not retroactively rewritten.
- Situation is a whole Working Copy API with owner scope and `expected_content_hash` optimistic concurrency.
- Nonpersistent Working Copy transforms copy the current AirportBase/profile or Mission into a client Working Copy without silently saving it.
- Historical mission selection reads frozen Run snapshots rather than mutable current catalog data.
- Failed Run retry clones the original immutable snapshot; it does not recreate a request from current Situation.
- Run history is server-filterable/paginated by status, ID, Situation, task, selected airport, damage/no-damage, clustering and time range.
- `/api/me` exposes server-authoritative effective permissions for permission-aware UI.
- Default Indicator V1.1 set and three-level node model are persisted; core nodes are protected; scoring is an atomic per-expert sheet with optimistic revision.
- GIS Runtime receives frozen OD facts from the Run snapshot in addition to canonical complete sortie chains and per-window damage projection.
- Results export source returns `report-data.v1` facts rather than asking JavaScript to recalculate a report.
- Append-only operation audit storage/query and a Flask `after_request` audit hook are coded.

## 6. Public API surface available for frontend binding

| Method | Path | Binding file |
|---|---|---|
| `GET` | `/api/me` | `flask_account.py` |
| `GET` | `/api/audit-events` | `flask_audit.py` |
| `GET` | `/api/airports` | `flask_catalog.py` |
| `POST` | `/api/airports` | `flask_catalog.py` |
| `GET` | `/api/airports/<airport_id>` | `flask_catalog.py` |
| `PUT` | `/api/airports/<airport_id>` | `flask_catalog.py` |
| `DELETE` | `/api/airports/<airport_id>` | `flask_catalog.py` |
| `GET` | `/api/missions` | `flask_catalog.py` |
| `POST` | `/api/missions` | `flask_catalog.py` |
| `GET` | `/api/missions/history` | `flask_catalog.py` |
| `GET` | `/api/missions/<mission_id>` | `flask_catalog.py` |
| `PUT` | `/api/missions/<mission_id>` | `flask_catalog.py` |
| `DELETE` | `/api/missions/<mission_id>` | `flask_catalog.py` |
| `GET` | `/api/aircraft-types` | `flask_catalog.py` |
| `POST` | `/api/aircraft-types` | `flask_catalog.py` |
| `PUT` | `/api/aircraft-types/<aircraft_type_id>` | `flask_catalog.py` |
| `DELETE` | `/api/aircraft-types/<aircraft_type_id>` | `flask_catalog.py` |
| `PUT` | `/api/aircraft-types/<aircraft_type_id>/resource-requirements` | `flask_catalog.py` |
| `GET` | `/api/resource-types` | `flask_catalog.py` |
| `POST` | `/api/resource-types` | `flask_catalog.py` |
| `PUT` | `/api/resource-types/<resource_type_id>` | `flask_catalog.py` |
| `DELETE` | `/api/resource-types/<resource_type_id>` | `flask_catalog.py` |
| `GET` | `/api/aircraft-resource-requirements` | `flask_catalog.py` |
| `GET` | `/api/indicators` | `flask_indicators.py` |
| `POST` | `/api/indicators` | `flask_indicators.py` |
| `PUT` | `/api/indicators/<indicator_id>` | `flask_indicators.py` |
| `DELETE` | `/api/indicators/<indicator_id>` | `flask_indicators.py` |
| `GET` | `/api/indicator-sets` | `flask_indicators.py` |
| `POST` | `/api/indicator-sets/drafts` | `flask_indicators.py` |
| `POST` | `/api/indicator-sets/<set_id>/publish` | `flask_indicators.py` |
| `GET` | `/api/experts` | `flask_indicators.py` |
| `POST` | `/api/experts` | `flask_indicators.py` |
| `PUT` | `/api/experts/<expert_id>` | `flask_indicators.py` |
| `DELETE` | `/api/experts/<expert_id>` | `flask_indicators.py` |
| `GET` | `/api/expert-scores/<expert_id>` | `flask_indicators.py` |
| `PUT` | `/api/expert-scores/<expert_id>` | `flask_indicators.py` |
| `GET` | `/api/indicator-weights` | `flask_indicators.py` |
| `GET` | `/api/results/comparable-runs` | `flask_results.py` |
| `GET` | `/api/results/damage-candidates` | `flask_results.py` |
| `POST` | `/api/results/damage-comparison` | `flask_results.py` |
| `POST` | `/api/results/scenario-comparison` | `flask_results.py` |
| `POST` | `/api/results/config-comparison` | `flask_results.py` |
| `POST` | `/api/results/export-data` | `flask_results.py` |
| `POST` | `/api/runs/validate` | `flask_runs.py` |
| `POST` | `/api/runs` | `flask_runs.py` |
| `GET` | `/api/runs` | `flask_runs.py` |
| `GET` | `/api/runs/<run_id>` | `flask_runs.py` |
| `GET` | `/api/runs/<run_id>/events` | `flask_runs.py` |
| `POST` | `/api/runs/<run_id>/retry` | `flask_runs.py` |
| `POST` | `/api/runs/<run_id>/cancel` | `flask_runs.py` |
| `GET` | `/api/runs/<run_id>/situation` | `flask_runs.py` |
| `GET` | `/api/runs/<run_id>/runtime` | `flask_runs.py` |
| `GET` | `/api/runs/<run_id>/solution` | `flask_runs.py` |
| `GET` | `/api/runs/<run_id>/metrics` | `flask_runs.py` |
| `GET` | `/api/situations` | `flask_situations.py` |
| `POST` | `/api/situations` | `flask_situations.py` |
| `POST` | `/api/situations/working-copy/copy-airport` | `flask_situations.py` |
| `POST` | `/api/situations/working-copy/copy-mission` | `flask_situations.py` |
| `GET` | `/api/situations/<situation_id>` | `flask_situations.py` |
| `PUT` | `/api/situations/<situation_id>` | `flask_situations.py` |
| `DELETE` | `/api/situations/<situation_id>` | `flask_situations.py` |
| `GET` | `/` | `flask_ui.py` |
| `GET` | `/run` | `flask_ui.py` |
| `GET` | `/runs/<run_id>` | `flask_ui.py` |
| `GET` | `/runs/<run_id>/runtime` | `flask_ui.py` |
| `GET` | `/results` | `flask_ui.py` |

## 7. Controls that must remain hidden/neutral until a decision exists

- Do not implement “calculate/recalculate indicator weights” with average, normalization or AHP unless the weight rule is explicitly approved. Submitted scores can be stored and shown; the weight endpoint explicitly reports unavailable when published weights are absent.
- Do not enable bulk import with silent upsert. Until D7 is closed, the UI may expose no bulk import action or an explicit “not configured” state; single-object canonical CRUD is ready.
- Do not show a GIS “strong connection threshold” selector as functional until its measure and threshold semantics are frozen. All OD can still be rendered from backend facts.
- Do not label `report-data.v1` as a PDF/DOCX/CSV report. It is renderer-ready canonical source data only.
- Remove/hide the decorative Settings entry until a real settings scope is approved.

## 8. Frontend-only responsibilities after this audit

- Dirty-state prompts, focus return, ESC behavior, disclosure animation and scroll preservation.
- Responsive layout, typography/spacing rhythm, skeleton/empty/error/permission presentation.
- Map/list selection synchronization and fit policy using backend-provided object facts.
- Chart hover/tooltips/linked selection using backend-provided values; no business aggregation in JS.
- Run progress presentation must use truthful event/stage facts and must not fabricate a continuous percentage when backend progress is absent.

## 9. Verification status

Current logic regression after the validate→submit binding: **275 tests passed, 0 failed**.

Real Flask/TestClient execution, dependency installation, Leaflet/tile physical assets and browser smoke remain explicitly deferred to the Codex/runtime phase per the project decision. This is a runtime verification deferral, not permission to change the API/business contracts.

## 10. Frontend gate

Frontend reconstruction may proceed against the ready backend contracts. Any frontend component whose only dependency is one of the explicit semantic/runtime blockers above must remain hidden, disabled with a truthful reason, or renderer-deferred; it must not be implemented by client-side guesses.
