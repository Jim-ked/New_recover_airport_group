from __future__ import annotations

from typing import Any, Mapping, Sequence, Union

from backend.domain.objective_profile import (
    RESEARCH_OBJECTIVE_SLOT_MINUTES,
    resolve_objective_profile,
    validate_research_profile_semantics,
)
from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import ODDistance, RunSnapshot
from backend.services.scenario_objective_calibration import load_scenario_f2_calibration
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

        aircraft_types = self.airports.list_aircraft_types()
        resource_types = self.airports.list_resource_types()
        requirements = self.airports.list_aircraft_resource_requirements()

        scenario_calibration = load_scenario_f2_calibration(
            self.situations.db_path, situation.situation_id
        )
        # Resolve new Runs before freezing. Trusted pre-built configs retain non-F2
        # settings; historical snapshot loading does not call this service.
        if isinstance(run_config, RunConfig):
            canonical_config = run_config
            if scenario_calibration is not None:
                canonical_config = RunConfig.from_mapping({
                    **run_config.to_dict(), **scenario_calibration,
                })
        else:
            canonical_config = RunConfig.from_mapping(resolve_objective_profile(
                run_config, scenario_calibration=scenario_calibration,
            ))
        used_aircraft = sorted({
            row.aircraft_type_id
            for mission in situation.missions
            for row in mission.aircraft_requirements
        })
        canonical_config = canonical_config.with_default_aircraft_weights(used_aircraft)
        used_aircraft_set = set(used_aircraft)
        used_resources = sorted({
            row.resource_type_id
            for row in requirements
            if row.aircraft_type_id in used_aircraft_set and float(row.quantity) > 0
        })
        canonical_config.require_objective_calibration(
            aircraft_type_ids=used_aircraft,
            resource_type_ids=used_resources,
        )
        validate_research_profile_semantics(
            profile_id=canonical_config.objective_profile_id,
            resource_types=resource_types,
            requirements=requirements,
            used_aircraft_type_ids=used_aircraft,
            slot_minutes=RESEARCH_OBJECTIVE_SLOT_MINUTES,
        )
        return RunSnapshot.build(
            run_id=run_id,
            situation=situation,
            aircraft_types=aircraft_types,
            resource_types=resource_types,
            aircraft_resource_requirements=requirements,
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
