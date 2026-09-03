from __future__ import annotations

from pathlib import Path

_ALLOWED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
_REGIONAL_SOURCES = (
    "tiles_china_east",
    "tiles_eastasia",
    "tiles_japan",
    "tiles_korea",
    "tiles_taiwan",
)
_WORLD_SOURCE = "tiles_world"
_SOURCE_DIRECTORIES = {
    "world": _WORLD_SOURCE,
    "eastasia": "tiles_eastasia",
    "china_east": "tiles_china_east",
    "japan": "tiles_japan",
    "korea": "tiles_korea",
    "taiwan": "tiles_taiwan",
}


def _valid_xyz(*, z: int, x: int, y: int) -> bool:
    return z >= 0 and 0 <= x < (1 << z) and 0 <= y < (1 << z)


def resolve_local_tile(
    root: Path,
    *,
    z: int,
    x: int,
    y: int,
    ext: str,
) -> tuple[Path, str] | None:
    """Resolve an XYZ tile from the narrowest available local coverage.

    XYZ coordinates already encode both map extent and zoom.  Looking for the exact
    coordinate in ordered regional packs selects the most detailed applicable source;
    the world pack is consulted only after every regional source misses.
    """
    relative = Path(str(z)) / str(x) / f"{y}.{ext}"
    for source in (*_REGIONAL_SOURCES, _WORLD_SOURCE):
        candidate = root / source / relative
        if candidate.is_file():
            return candidate.parent, candidate.name
    # Preserve compatibility with deployments that still provide a flat XYZ root.
    candidate = root / relative
    if candidate.is_file():
        return candidate.parent, candidate.name
    return None


def create_tile_blueprint(*, tile_root: str | Path):
    """Serve approved local XYZ raster tiles from one deployment-owned directory."""
    try:
        from flask import Blueprint, abort, send_from_directory
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("Flask runtime dependency is required to bind local tile route") from exc

    configured_root = Path(tile_root).expanduser().resolve()
    source_names = set(_SOURCE_DIRECTORIES.values())
    root = configured_root.parent if configured_root.name in source_names else configured_root
    bp = Blueprint("tiles_v1", __name__, url_prefix="/tiles")

    @bp.get("/<string:source>/<int:z>/<int:x>/<int:y>.<string:ext>")
    def source_tile(source: str, z: int, x: int, y: int, ext: str):
        ext = ext.lower()
        directory = _SOURCE_DIRECTORIES.get(source)
        if directory is None or ext not in _ALLOWED_EXTENSIONS or not _valid_xyz(z=z, x=x, y=y):
            abort(404)
        relative = Path(str(z)) / str(x) / f"{y}.{ext}"
        candidate = root / directory / relative
        if not candidate.is_file():
            abort(404)
        return send_from_directory(candidate.parent, candidate.name, conditional=True)

    @bp.get("/<int:z>/<int:x>/<int:y>.<string:ext>")
    def tile(z: int, x: int, y: int, ext: str):
        ext = ext.lower()
        if ext not in _ALLOWED_EXTENSIONS or not _valid_xyz(z=z, x=x, y=y):
            abort(404)
        resolved = resolve_local_tile(root, z=z, x=x, y=y, ext=ext)
        if resolved is None:
            abort(404)
        directory, filename = resolved
        return send_from_directory(directory, filename, conditional=True)

    return bp


__all__ = ["create_tile_blueprint", "resolve_local_tile"]
