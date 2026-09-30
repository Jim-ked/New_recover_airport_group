from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


_RANDOM_INDEX = {
    1: 0.0,
    2: 0.0,
    3: 0.58,
    4: 0.90,
    5: 1.12,
    6: 1.24,
    7: 1.32,
    8: 1.41,
    9: 1.45,
    10: 1.49,
}


class AHPError(ValueError):
    pass


@dataclass(frozen=True)
class AHPResult:
    weights: Mapping[int, float]
    lambda_max: float
    consistency_index: float
    consistency_ratio: float
    consistent: bool


@dataclass(frozen=True)
class RiskResult:
    normalized_weights: Mapping[str, float]
    weighted_composite: float
    risk_score: float
    risk_level: int


def _validated_matrix(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    if not isinstance(matrix, Sequence) or isinstance(matrix, (str, bytes)) or not matrix:
        raise AHPError("comparison matrix must be nonempty")
    size = len(matrix)
    if size > 10:
        raise AHPError("comparison matrix supports at most 10 indicators")
    rows: list[list[float]] = []
    for raw_row in matrix:
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes)) or len(raw_row) != size:
            raise AHPError("comparison matrix must be square")
        row: list[float] = []
        for raw_value in raw_row:
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise AHPError("comparison matrix entries must be numeric")
            value = float(raw_value)
            if not math.isfinite(value) or value <= 0:
                raise AHPError("comparison matrix entries must be finite and positive")
            row.append(value)
        rows.append(row)
    for index in range(size):
        if not math.isclose(rows[index][index], 1.0, rel_tol=0.0, abs_tol=1e-8):
            raise AHPError("comparison matrix diagonal entries must equal 1")
        for other in range(index + 1, size):
            if not math.isclose(rows[index][other] * rows[other][index], 1.0, rel_tol=1e-6, abs_tol=1e-8):
                raise AHPError("comparison matrix must be reciprocal")
    return rows


def analyze_ahp_matrix(
    matrix: Sequence[Sequence[float]],
    *,
    check_consistency: bool = True,
) -> AHPResult:
    """Apply the reference normalized-column AHP method and retain its consistency trace."""
    rows = _validated_matrix(matrix)
    size = len(rows)
    column_sums = [sum(rows[row][column] for row in range(size)) for column in range(size)]
    weights = [
        sum(rows[row][column] / column_sums[column] for column in range(size)) / size
        for row in range(size)
    ]
    total = sum(weights)
    if not math.isfinite(total) or total <= 0:
        raise AHPError("failed to normalize weights")
    weights = [value / total for value in weights]

    weighted = [sum(rows[row][column] * weights[column] for column in range(size)) for row in range(size)]
    denominator = sum(value * value for value in weights)
    lambda_max = sum(weights[index] * weighted[index] for index in range(size)) / denominator
    consistency_index = (lambda_max - size) / (size - 1) if size > 1 else 0.0
    random_index = _RANDOM_INDEX.get(size, 1.49)
    consistency_ratio = consistency_index / random_index if random_index > 0 else 0.0
    consistent = consistency_ratio <= 0.1 + 1e-12
    if check_consistency and not consistent:
        raise AHPError(f"inconsistent comparison matrix (CR={consistency_ratio:.3f} > 0.1)")
    return AHPResult(
        weights={index: float(value) for index, value in enumerate(weights)},
        lambda_max=float(lambda_max),
        consistency_index=float(consistency_index),
        consistency_ratio=float(consistency_ratio),
        consistent=consistent,
    )


def ahp_weights(
    matrix: Sequence[Sequence[float]],
    *,
    check_consistency: bool = True,
) -> Mapping[int, float]:
    return analyze_ahp_matrix(matrix, check_consistency=check_consistency).weights


def compute_risk(
    indicator_values: Mapping[str, float],
    weights: Mapping[str, float],
) -> RiskResult:
    if not indicator_values:
        raise AHPError("indicator_values must not be empty")
    missing = set(indicator_values) - set(weights)
    unknown = set(weights) - set(indicator_values)
    if missing:
        raise AHPError(f"missing weights for indicators: {sorted(missing)}")
    if unknown:
        raise AHPError(f"weights for unknown indicators: {sorted(unknown)}")
    parsed_values: dict[str, float] = {}
    parsed_weights: dict[str, float] = {}
    for key, raw in indicator_values.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise AHPError(f"indicator value for {key} must be numeric")
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise AHPError(f"indicator value for {key} must be in [0,1]")
        parsed_values[key] = value
        raw_weight = weights[key]
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise AHPError(f"weight for {key} must be numeric")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise AHPError("weights must be finite and nonnegative")
        parsed_weights[key] = weight
    total = sum(parsed_weights.values())
    if total <= 0:
        raise AHPError("weights must have a positive sum")
    normalized = {key: value / total for key, value in parsed_weights.items()}
    composite = sum(normalized[key] * parsed_values[key] for key in parsed_values)
    risk_score = 1.0 - composite
    if risk_score >= 0.8:
        level = 5
    elif risk_score >= 0.6:
        level = 4
    elif risk_score >= 0.4:
        level = 3
    elif risk_score >= 0.2:
        level = 2
    else:
        level = 1
    return RiskResult(
        normalized_weights=normalized,
        weighted_composite=round(composite, 6),
        risk_score=round(risk_score, 6),
        risk_level=level,
    )


__all__ = ["AHPError", "AHPResult", "RiskResult", "ahp_weights", "analyze_ahp_matrix", "compute_risk"]
