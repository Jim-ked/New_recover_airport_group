from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple


class SolutionValidationError(ValueError):
    pass


def _nonblank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SolutionValidationError(f"{field} must be a nonblank string")
    return value


@dataclass(frozen=True)
class SortieChain:
    path_id: str
    origin_airport_id: str
    mission_id: str
    return_airport_id: str
    aircraft_type: str
    depart_window: int
    return_window: int
    ready_window: int
    sorties: int

    def __post_init__(self) -> None:
        for field in ("path_id", "origin_airport_id", "mission_id", "return_airport_id", "aircraft_type"):
            _nonblank(getattr(self, field), field)
        for field in ("depart_window", "return_window", "ready_window"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SolutionValidationError(f"{field} must be a nonnegative integer")
        if not (self.depart_window <= self.return_window <= self.ready_window):
            raise SolutionValidationError("sortie windows must satisfy depart <= return <= ready")
        if isinstance(self.sorties, bool) or not isinstance(self.sorties, int) or self.sorties <= 0:
            raise SolutionValidationError("sorties must be a positive integer")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_id": self.path_id,
            "origin_airport_id": self.origin_airport_id,
            "mission_id": self.mission_id,
            "return_airport_id": self.return_airport_id,
            "aircraft_type": self.aircraft_type,
            "depart_window": self.depart_window,
            "return_window": self.return_window,
            "ready_window": self.ready_window,
            "sorties": self.sorties,
        }


@dataclass(frozen=True)
class Solution:
    run_id: str
    selected_cluster: Tuple[str, ...]
    sortie_chains: Tuple[SortieChain, ...]

    def __post_init__(self) -> None:
        _nonblank(self.run_id, "run_id")
        if len(self.selected_cluster) != len(set(self.selected_cluster)):
            raise SolutionValidationError("selected_cluster must not contain duplicates")
        if any(not isinstance(x, str) or not x.strip() for x in self.selected_cluster):
            raise SolutionValidationError("selected_cluster IDs must be nonblank strings")
        if not self.sortie_chains:
            raise SolutionValidationError("successful canonical Solution must contain sortie_chains")
        ids = [row.path_id for row in self.sortie_chains]
        if len(ids) != len(set(ids)):
            raise SolutionValidationError("sortie_chains path_id must be unique")

    @classmethod
    def build(cls, *, run_id: str, selected_cluster: Iterable[str], sortie_chains: Iterable[SortieChain]) -> "Solution":
        cluster = tuple(sorted(str(x) for x in selected_cluster))
        chains = tuple(sorted(
            sortie_chains,
            key=lambda x: (
                x.depart_window, x.origin_airport_id, x.mission_id,
                x.return_airport_id, x.aircraft_type, x.path_id,
            ),
        ))
        return cls(run_id=run_id, selected_cluster=cluster, sortie_chains=chains)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "selected_cluster": list(self.selected_cluster),
            "sortie_chains": [x.to_dict() for x in self.sortie_chains],
        }


__all__ = ["Solution", "SortieChain", "SolutionValidationError"]
