from __future__ import annotations

from typing import Any, Mapping, Sequence, Union

from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import ODDistance, RunSnapshot
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository


class RunSnapshotServiceError(ValueError):
    pass


class RunSnapshotService:
    """
    Freeze one already-saved Situation into an immutable Run input closure.

    This service assembles authoritative stored data and canonicalizes RunConfig using
    the frozen preset/custom rules. It does not calculate OD distances.
    """

    def __init__(
        self,
        *,
        airport_repository: AirportRepository,
        situation_repository: SituationRepository,
        snapshot_repository: RunSnapshotRepository,
    ) -> None:
        self.airports = airport_repository
        self.situations = situation_repository
        self.snapshots = snapshot_repository

    def build_snapshot(
        self,
        *,
        run_id: str,
        situation_id: str,
        run_config: Union[Mapping[str, Any], RunConfig],
        od_distances: Sequence[ODDistance],
    ) -> RunSnapshot:
        """Build the immutable input closure without persisting it.

        RunService uses this form so snapshot + RunRecord can be committed atomically.
        The existing create_snapshot method remains for lower-level callers/tests.
        """
        situation = self.situations.get_situation(situation_id)
        if situation is None:
            raise RunSnapshotServiceError(f"situation not found: {situation_id}")

        canonical_config = run_config if isinstance(run_config, RunConfig) else RunConfig.from_mapping(run_config)
        return RunSnapshot.build(
            run_id=run_id,
            situation=situation,
            aircraft_types=self.airports.list_aircraft_types(),
            resource_types=self.airports.list_resource_types(),
            aircraft_resource_requirements=self.airports.list_aircraft_resource_requirements(),
            od_distances=od_distances,
            run_config=canonical_config,
        )

    def create_snapshot(
        self,
        *,
        run_id: str,
        situation_id: str,
        run_config: Union[Mapping[str, Any], RunConfig],
        od_distances: Sequence[ODDistance],
    ) -> RunSnapshot:
        snapshot = self.build_snapshot(
            run_id=run_id,
            situation_id=situation_id,
            run_config=run_config,
            od_distances=od_distances,
        )
        self.snapshots.save_new(snapshot)
        return snapshot
