from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from backend.domain.run_config import RunConfigValidationError


_F2_FIELDS = frozenset({
    "f2_resource_reference_quantities",
    "f2_resource_weights",
})


def load_scenario_f2_calibration(
    db_path: str | Path, situation_id: str
) -> Mapping[str, Any] | None:
    """Read server-owned F2 tables for one database and saved Situation.

    Absence preserves the default profile; invalid configured data must fail validation.
    RunConfig validates values, paired resource IDs and required resource coverage later.
    """
    database = Path(db_path)
    path = database.with_name(database.name + ".f2-calibration.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise RunConfigValidationError(
            f"cannot read scenario F2 calibration {path}: {exc}",
            field="scenario_f2_calibration",
        ) from exc

    if not isinstance(payload, dict):
        raise RunConfigValidationError(
            "scenario F2 calibration must be an object keyed by situation ID",
            field="scenario_f2_calibration",
        )
    if situation_id not in payload:
        return None
    calibration = payload[situation_id]
    if not isinstance(calibration, dict):
        raise RunConfigValidationError(
            f"scenario F2 calibration for {situation_id} must be an object",
            field="scenario_f2_calibration",
        )
    unknown = set(calibration) - _F2_FIELDS
    if unknown:
        raise RunConfigValidationError(
            f"unknown scenario F2 calibration field: {sorted(unknown)[0]}",
            field="scenario_f2_calibration",
        )
    for field in sorted(_F2_FIELDS):
        if not isinstance(calibration.get(field), dict) or not calibration[field]:
            raise RunConfigValidationError(
                f"scenario F2 calibration {field} must be a nonempty object",
                field=field,
            )
    return calibration
