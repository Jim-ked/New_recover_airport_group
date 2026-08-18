# Standard development workspace

The single supported entry point is:

```powershell
python tools/dev_validation/build_demo_workspace.py
```

Without `--apply-default-db` it performs a read-only plan against
`runtime/db/airport_group.sqlite3` and does not build the Flask application or write the
database.

Explicit write modes:

```powershell
# Create or update only the standard Situation.
python tools/dev_validation/build_demo_workspace.py --prepare-only --apply-default-db

# Run seven comparisons only when the standard Situation has no prior Runs.
python tools/dev_validation/build_demo_workspace.py --run --apply-default-db

# Remove only the terminal Runs owned by the standard Situation, rebuild it, and run all seven.
python tools/dev_validation/build_demo_workspace.py --rebuild --apply-default-db
```

Credentials are read from:

- `AIRPORT_GROUP_VALIDATION_USERNAME`
- `AIRPORT_GROUP_VALIDATION_PASSWORD`

The tool always requires the normal authenticated API, CSRF, Domain, Repository and Run
worker paths. It refuses a non-default database, refuses duplicate demo Situation names,
does not modify other Situations or Runs, and stops when the demo batch contains a queued
or running Run.

The historical `validation_work.sqlite3` file is not modified or deleted. The old
`prepare_situation.py` and `run_comparison.py` files are compatibility wrappers only and
contain no separate dataset constants.
