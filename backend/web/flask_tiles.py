from __future__ import annotations

from pathlib import Path

_ALLOWED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})


def create_tile_blueprint(*, tile_root: str | Path):
    """Serve approved local XYZ raster tiles from one deployment-owned directory."""
    try:
        from flask import Blueprint, abort, send_from_directory
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("Flask runtime dependency is required to bind local tile route") from exc

    root = Path(tile_root).expanduser().resolve()
    bp = Blueprint("tiles_v1", __name__, url_prefix="/tiles")

    @bp.get("/<int:z>/<int:x>/<int:y>.<string:ext>")
    def tile(z: int, x: int, y: int, ext: str):
        ext = ext.lower()
        if ext not in _ALLOWED_EXTENSIONS or z < 0 or x < 0 or y < 0:
            abort(404)
        # werkzeug safe_join accepts forward slashes only; Path-based joins would
        # produce backslashes on Windows and be rejected as untrusted input.
        relative = f"{z}/{x}/{y}.{ext}"
        return send_from_directory(root, relative, conditional=True)

    return bp


__all__ = ["create_tile_blueprint"]