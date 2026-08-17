# Development validation helpers — checked revision 2

These scripts are development-only helpers. They are designed for the local project root and use a disposable validation SQLite database. They do not add product features.

## What was checked in revision 2

- Windows SQLite handles are explicitly closed before `os.replace()` or delete operations. `sqlite3.Connection` context-manager exit is not treated as `close()`.
- Stale `.tmp` database cleanup cannot hide the original exception.
- `prepare_situation.py` and `run_comparison.py` explicitly add the project root to `sys.path` before importing `backend`.
- The scripts warn when they are not running with the project's `.venv` interpreter.
- Validation constants are parsed through the current domain classes before any Base Data mutation.
- The current global aircraft catalog is checked for the numeric fields required by the snapshot adapter, so an unrelated incomplete aircraft row fails early instead of failing inside the worker.
- Damage events start no earlier than T42 and remain inside the mission envelope, so R0/R1/R2 use one common Metrics time axis.
- All seven Run configurations are preflight-validated before the Situation preparation step is declared ready.
- The comparison script refuses to start a new 7-run batch if the validation Situation already has Run history; reset the work DB instead.
- Each submitted Run must be queued, executed by the existing `RunWorker`, succeed, persist Solution/Metrics, expose the frozen Situation/runtime/events, and contain a `run_succeeded` event.
- After all seven Runs, the script checks direct damage/config/scenario comparisons *and* the frontend-facing comparison discovery APIs (`damage-candidates`, `comparable-runs`).

## Validation data

Situation: `DEV-VALIDATION-01`

- six current Base Data airports: Nanjing Lukou, Xuzhou Guanyin, Nantong Xingdong, Suzhou Guangfu, Yancheng Nanyang, Sunan Shuofang;
- old-project operational/profile values adapted to the current schema;
- missions N1/N2/N3 adapted to current half-open time windows;
- `DS-LOW`, `DS-MEDIUM`, `DS-HIGH` using current absolute damage semantics.

## Important: use the project Python

Current Windows deployment scripts use:

```text
.venv\Scripts\python.exe
```

Do not use a bare system `python` for the two application scripts. A system Python may not have the same Flask/PySCIPOpt dependencies as the project environment.

## 1. Capture/reset the validation database

The reset script itself only uses the standard library, but using the same project interpreter keeps commands consistent.

From `D:\Data\New_recover_airport_group`:

```powershell
& .\.venv\Scripts\python.exe tools\dev_validation\reset_validation_db.py --capture-base
```

If `validation_base.sqlite3` already exists and you intentionally want to recreate it:

```powershell
& .\.venv\Scripts\python.exe tools\dev_validation\reset_validation_db.py --capture-base --force-base
```

For every clean rerun after the base has been captured:

```powershell
& .\.venv\Scripts\python.exe tools\dev_validation\reset_validation_db.py
```

The source `airport_group.sqlite3` may be open because SQLite's backup API is used. However, no application or worker should be using `validation_base.sqlite3` or `validation_work.sqlite3` while they are being replaced. A stale `validation_*.sqlite3.tmp` left by the old revision is removed automatically when it is not held by another process.

## 2. Load the normal runtime environment, then point to validation_work

In PowerShell from the project root:

```powershell
if (Test-Path .\.env.local.ps1) { . .\.env.local.ps1 }
$env:AIRPORT_GROUP_DB_PATH = "D:\Data\New_recover_airport_group\runtime\db\validation_work.sqlite3"
$env:AIRPORT_GROUP_VALIDATION_USERNAME = "admin"
$env:AIRPORT_GROUP_VALIDATION_PASSWORD = "<password>"
```

The normal `AIRPORT_GROUP_SECRET_KEY` and other runtime settings must remain available. Both application scripts refuse the normal `airport_group.sqlite3` filename and require a database filename containing `validation`.

## 3. Prepare the Situation

```powershell
& .\.venv\Scripts\python.exe tools\dev_validation\prepare_situation.py
```

The script uses `build_application(...).test_client()` and calls the real login/CSRF/Base Data/Situation/Run-validation routes. It does not insert Situation rows directly with SQL.

If `DEV-VALIDATION-01` already exists, reset `validation_work.sqlite3`; do not manually delete dependent rows.

## 4. Run the 7-run comparison set

```powershell
& .\.venv\Scripts\python.exe tools\dev_validation\run_comparison.py
```

Runs:

- R0: no damage, clustering off;
- DS-LOW-R1 / DS-LOW-R2;
- DS-MEDIUM-R1 / DS-MEDIUM-R2;
- DS-HIGH-R1 / DS-HIGH-R2.

Do not run a separate Run worker against `validation_work.sqlite3` while this script is executing. The script submits through `/api/runs` and then synchronously invokes the project's existing `RunWorker` for exactly that queued Run.

## 5. If a run fails

Do not clean rows manually. Reset the work database:

```powershell
& .\.venv\Scripts\python.exe tools\dev_validation\reset_validation_db.py
```

Then rerun preparation and comparison.

## 6. Browser smoke check after script success

Start the normal application using the same `AIRPORT_GROUP_DB_PATH` and check one representative browser path:

```text
login -> DEV-VALIDATION-01 -> airports/missions/damage -> one Run -> comparison results
```

The scripts validate the business/API/algorithm/persistence path. Browser rendering and browser-only interaction still require this small manual check.
