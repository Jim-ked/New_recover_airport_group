# Local visual asset placement contract

This project does not download or bundle legacy visual assets during the web-design phase.
The operator/Codex deployment step should copy approved files from the existing local project materials into these slots.

## Reusable legacy visual files

Place only after manual review:

- `frontend/static/assets/legacy/logo.png`
  - source candidate: old project's `interface/app/static/images/logo.png`
  - use: product mark / login composition if the approved visual design needs it.
- `frontend/static/assets/legacy/login_bg.png`
  - source candidate: old project's `interface/app/static/images/login_bg.png`
  - use: optional login-page background; the current page has a CSS gradient fallback and does not require it.
- `frontend/static/assets/legacy/airforce-emblem.png`
  - source candidate: old project's `interface/app/static/images/airforce-emblem.png`
  - use only after confirming it is appropriate for the delivery environment.

Do not treat any image or GIS JSON copied here as business data authority.

## GIS auxiliary resources

Potentially reusable local-only resources from the old project include province boundaries and tile tooling. Put approved copies under `resources/gis/` or the deployment-specific tile directory, not under the browser asset directory when they are data inputs.

Airport JSON/GeoJSON from the old static directory must **not** become a second airport database. The current Base Data repository remains authoritative; old airport files may only be used for seed conversion, migration comparison, or manual verification.
