from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence


RATING_LEVELS: tuple[str, ...] = ("very_low", "low", "medium", "high", "very_high")
RATING_SCORES: Mapping[str, float] = {
    "very_low": 0.0,
    "low": 0.25,
    "medium": 0.5,
    "high": 0.75,
    "very_high": 1.0,
}
_MEMBERSHIP_BREAKPOINTS: Mapping[str, tuple[float, float, float, float]] = {
    "very_low": (0.00, 0.00, 0.20, 0.35),
    "low": (0.15, 0.30, 0.40, 0.55),
    "medium": (0.40, 0.50, 0.60, 0.75),
    "high": (0.60, 0.72, 0.82, 0.92),
    "very_high": (0.78, 0.90, 1.00, 1.00),
}


class FuzzyInputError(ValueError):
    pass


def _score(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FuzzyInputError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise FuzzyInputError(f"{field} must be finite and in [0, 1]")
    return number


def trapezoid(x: float, a: float, b: float, c: float, d: float) -> float:
    if x <= a or x >= d:
        return 0.0 if x < a or x > d else 1.0
    left = 1.0 if a == b else (min(1.0, (x - a) / (b - a)) if x <= b else 1.0)
    right = 1.0 if c == d else (min(1.0, (d - x) / (d - c)) if x >= c else 1.0)
    return max(0.0, min(1.0, min(left, right)))


def membership_vector(value: float) -> Mapping[str, float]:
    x = _score(value, "indicator value")
    raw = {
        level: trapezoid(x, *breakpoints)
        for level, breakpoints in _MEMBERSHIP_BREAKPOINTS.items()
    }
    total = sum(raw.values())
    if total <= 1e-12:
        return {level: (1.0 if level == "medium" else 0.0) for level in RATING_LEVELS}
    return {level: raw[level] / total for level in RATING_LEVELS}


def _validate_weights(keys: Sequence[str], weights: Mapping[str, float]) -> Dict[str, float]:
    missing = set(keys) - set(weights)
    if missing:
        raise FuzzyInputError(f"missing weights for elements: {sorted(missing)}")
    unknown = set(weights) - set(keys)
    if unknown:
        raise FuzzyInputError(f"weights for unknown elements: {sorted(unknown)}")
    parsed: Dict[str, float] = {}
    for key in keys:
        raw = weights[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise FuzzyInputError(f"weight for {key} must be numeric")
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise FuzzyInputError("weights must be finite and nonnegative")
        parsed[key] = value
    total = sum(parsed.values())
    if total <= 0:
        raise FuzzyInputError("weights must have a positive sum")
    return {key: parsed[key] / total for key in keys}


@dataclass(frozen=True)
class FuzzyComprehensiveResult:
    element_scores: Mapping[str, float]
    normalized_weights: Mapping[str, float]
    element_capabilities: Mapping[str, float]
    element_memberships: Mapping[str, Mapping[str, float]]
    overall_membership: Mapping[str, float]
    support_index: float


def evaluate_elements(
    element_scores: Mapping[str, float],
    weights: Mapping[str, float],
) -> FuzzyComprehensiveResult:
    elements = list(element_scores)
    if not elements:
        raise FuzzyInputError("element_scores must not be empty")
    scores = {element: _score(element_scores[element], f"indicator {element}") for element in elements}
    normalized_weights = _validate_weights(elements, weights)
    memberships = {element: membership_vector(scores[element]) for element in elements}
    capabilities = {
        element: sum(memberships[element][level] * RATING_SCORES[level] for level in RATING_LEVELS)
        for element in elements
    }
    overall = {
        level: sum(normalized_weights[element] * memberships[element][level] for element in elements)
        for level in RATING_LEVELS
    }
    support_index = sum(overall[level] * RATING_SCORES[level] for level in RATING_LEVELS)
    return FuzzyComprehensiveResult(
        element_scores=scores,
        normalized_weights=normalized_weights,
        element_capabilities=capabilities,
        element_memberships=memberships,
        overall_membership=overall,
        support_index=round(support_index, 6),
    )


def evaluate_two_layer(
    capability_groups: Mapping[str, Mapping[str, float]],
    group_factor_weights: Mapping[str, Mapping[str, float]],
    group_weights: Mapping[str, float],
) -> FuzzyComprehensiveResult:
    if not capability_groups:
        raise FuzzyInputError("capability_groups must not be empty")
    group_capabilities: Dict[str, float] = {}
    for group, scores in capability_groups.items():
        if group not in group_factor_weights:
            raise FuzzyInputError(f"missing factor weights for group: {group}")
        group_capabilities[group] = evaluate_elements(scores, group_factor_weights[group]).support_index
    return evaluate_elements(group_capabilities, group_weights)


__all__ = [
    "FuzzyComprehensiveResult",
    "FuzzyInputError",
    "RATING_LEVELS",
    "RATING_SCORES",
    "evaluate_elements",
    "evaluate_two_layer",
    "membership_vector",
    "trapezoid",
]
