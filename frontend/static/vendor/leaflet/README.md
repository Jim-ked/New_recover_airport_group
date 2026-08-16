# Leaflet local runtime asset slot

Do not download Leaflet during this design session. The existing local project already contains a complete Leaflet distribution.

During the Codex/deployment step, manually copy the **existing local** Leaflet files into this directory:

- `leaflet.js`
- `leaflet.css`
- `images/layers.png`
- `images/layers-2x.png`
- `images/marker-icon.png`
- `images/marker-icon-2x.png`
- `images/marker-shadow.png`

Expected browser URLs:

- `/static/vendor/leaflet/leaflet.js`
- `/static/vendor/leaflet/leaflet.css`

Current GIS Runtime uses `L.divIcon`, so the default marker images are not required by current page logic, but copying the complete local Leaflet `images/` directory avoids future path surprises.

No CDN fallback is allowed. If the local kernel is absent, the GIS page must show an explicit missing-kernel state rather than requesting the network.
