"""Canonical V5 build/execute/retry contracts, using explicit numeric fixture facts."""
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.algorithm.snapshot_adapter import AlgorithmInputError, build_algorithm_input
from backend.domain.airport_operations import AirportResourceStock
from backend.domain.catalog import AircraftResourceRequirement, ResourceType
from backend.domain.damage import DamageScenario
from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import ODDistance, RunSnapshot, RunSnapshotValidationError
from backend.domain.situation import SituationAirport
from original_algorithm_overlay.model.decision_vars import build_path_map
from backend.services.dynamic_inventory import replay_dynamic_inventory
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.storage.airport_repository import AirportRepository
from backend.storage.database import initialize_database
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository
from backend.storage.user_repository import UserRepository
from tests.domain import test_run_snapshot as fixture_module


def repack(snapshot, payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return RunSnapshot(snapshot.run_id, snapshot.situation_id, hashlib.sha256(encoded.encode()).hexdigest(), encoded)


class V5SnapshotTests(unittest.TestCase):
    def setUp(self):
        fixtures = fixture_module.RunSnapshotTests()
        base = fixtures._situation()
        self.aircraft, resources, requirements = fixtures._catalogs()
        self.resources = [*resources, ResourceType('MAT-1', 'Material', 'material', 't')]
        self.requirements = [*requirements, AircraftResourceRequirement('fighter', 'MAT-1', 'per_sortie', 1)]
        airport = base.airports[0]
        profile = replace(airport.operational_profile, resource_stocks=(
            *airport.operational_profile.resource_stocks, AirportResourceStock('MAT-1', 7, 0, 20)))
        self.situation = replace(base, airports=(SituationAirport(airport.airport, profile),))

        def event(identity, sequence, kind, slot, effect):
            return {'event_id': identity, 'sequence': sequence,
                    'target': {'airport_id': 'A1', 'target_type': 'airport', 'target_id': None},
                    'damage_type': kind, 'start_slot': slot, 'end_slot': slot + 1, 'effect': effect,
                    'recovery_mode': 'instant' if kind == 'capacity_damage' else 'none'}

        def physical(**remaining):
            return {'inventory_semantics': 'physical_inventory_v1', 'physical_loss': {'remaining_quantity': remaining}}

        scenarios = {
            'DS01': [event('CAP' + str(i), i, 'capacity_damage', 2 * i, {'closed': True}) for i in (1, 2, 3)],
            'DS02': [event('RES01', 1, 'resource_damage', 2, physical(**{'FUEL-1': 40})),
                     event('RES02', 2, 'resource_damage', 4, physical(**{'FUEL-1': 30, 'MAT-1': 4})),
                     event('RES03', 3, 'resource_damage', 6, physical(**{'FUEL-1': 20, 'MAT-1': 3}))],
            'DS03': [event('AIR' + str(i), i, 'aircraft_damage', 2 * i, {'aircraft_loss': {'fighter': 1}}) for i in (1, 2, 3)],
            'DS04': [event('MIX01', 1, 'resource_damage', 2, physical(**{'FUEL-1': 40})),
                     event('MIX02', 2, 'capacity_damage', 4, {'closed': True}),
                     event('MIX03', 3, 'aircraft_damage', 6, {'aircraft_loss': {'fighter': 1}}),
                     event('MIX04', 4, 'resource_damage', 8, physical(**{'FUEL-1': 20}))],
        }
        self.situation = replace(self.situation, damage_scenarios=tuple(
            DamageScenario.from_mapping({'damage_scenario_id': key, 'name': key, 'category': 'custom', 'events': rows})
            for key, rows in scenarios.items()))
        self.raw_config = {**fixtures._config().to_dict(), 'resource_inventory_model': 'physical_inventory_v1',
                           'f2_resource_reference_quantities': {'FUEL-1': 10, 'MAT-1': 1},
                           'f2_resource_weights': {'FUEL-1': .5, 'MAT-1': .5},
                           'f3_time_reference_slots': 20, 'f3_tardiness_coefficient': 1,
                           'unmet_demand_penalty': 3, 'core_airport_reward_weight': 0}

    def snapshot(self, *, scenario=None, situation=None, model='physical_inventory_v1'):
        return RunSnapshot.build(run_id='V5', situation=situation or self.situation, aircraft_types=self.aircraft,
                                 resource_types=self.resources, aircraft_resource_requirements=self.requirements,
                                 od_distances=[ODDistance('A1', 'M1', 100)],
                                 run_config=RunConfig.from_mapping({**self.raw_config, 'damage_scenario_id': scenario,
                                                                   'resource_inventory_model': model}))

    def test_v4_json_is_byte_compatible_and_has_no_dynamic_contract(self):
        f = fixture_module.RunSnapshotTests()
        a, r, q = f._catalogs()
        snapshot = RunSnapshot.build(run_id='R1', situation=f._situation(), aircraft_types=a, resource_types=r,
                                     aircraft_resource_requirements=q, od_distances=[ODDistance('A1', 'M1', 321.5)], run_config=f._config())
        self.assertEqual(snapshot.content_hash, '1ddf51ab8a5665e93a8038c1a2c86ca7178235f3a06322b6099645e62853f2c7')
        self.assertEqual(snapshot.to_dict()['schema'], 'run_input_snapshot_v4')
        self.assertNotIn('dynamic_inventory', snapshot.to_dict())
        self.assertNotIn('dynamic_inventory', build_algorithm_input(snapshot).ds['timeview'])

    def test_v5_builder_freezes_all_storage_from_situation(self):
        snapshot = self.snapshot()
        raw = snapshot.to_dict()
        self.assertEqual(raw['schema'], 'run_input_snapshot_v5')
        self.assertEqual(raw['dynamic_inventory'], {'semantics': 'physical_inventory_v1',
                                                   'storage_capacity': {'A1': {'FUEL-1': 75, 'MAT-1': 20}}})
        raw['dynamic_inventory']['storage_capacity']['A1']['MAT-1'] = 999
        self.assertEqual(snapshot.to_dict()['dynamic_inventory']['storage_capacity']['A1']['MAT-1'], 20)

    def test_v5_build_rejects_missing_storage_even_without_resource_damage(self):
        # No events here: selection must depend on inventory model, not event types.
        airport = self.situation.airports[0]
        stocks = tuple(replace(s, storage_capacity=None) if s.resource_type_id == 'MAT-1' else s
                       for s in airport.operational_profile.resource_stocks)
        situation = replace(self.situation, damage_scenarios=(), airports=(replace(airport,
            operational_profile=replace(airport.operational_profile, resource_stocks=stocks)),))
        with self.assertRaisesRegex(RunSnapshotValidationError, 'storage_capacity'):
            self.snapshot(situation=situation)
        self.assertEqual(self.snapshot(situation=situation, model='legacy_budget_v4').to_dict()['schema'], 'run_input_snapshot_v4')

    def test_v5_clone_changes_only_run_id_and_hash(self):
        source = self.snapshot(scenario='DS04')
        retry = source.clone_for_run('V5_RETRY')
        expected = source.to_dict()
        expected['run_id'] = 'V5_RETRY'
        self.assertEqual(retry.to_dict(), expected)
        self.assertNotEqual(retry.content_hash, source.content_hash)
        self.assertEqual(retry.content_hash, hashlib.sha256(retry.payload_json.encode()).hexdigest())
        self.assertIn('dynamic_inventory', build_algorithm_input(retry).ds['timeview'])

    def test_mismatches_remain_readable_but_cannot_execute(self):
        for source in (self.snapshot(), self.snapshot(model='legacy_budget_v4')):
            raw = source.to_dict()
            raw['schema'] = 'run_input_snapshot_v4' if raw['schema'].endswith('v5') else 'run_input_snapshot_v5'
            historical = repack(source, raw)
            self.assertEqual(historical.to_dict(), raw)  # Read/audit compatibility.
            with self.assertRaises(AlgorithmInputError):
                build_algorithm_input(historical)

    def test_v5_missing_or_wrong_contract_fails_execution(self):
        source = self.snapshot()
        for value in (None, {}, {'semantics': 'legacy', 'storage_capacity': {}}, {'semantics': 'physical_inventory_v1'}):
            raw = source.to_dict()
            if value is None:
                del raw['dynamic_inventory']
            else:
                raw['dynamic_inventory'] = value
            with self.assertRaises(AlgorithmInputError):
                build_algorithm_input(repack(source, raw))

    def test_v4_rejects_unexpected_dynamic_contract_without_rewriting_history(self):
        source = self.snapshot(model='legacy_budget_v4')
        raw = source.to_dict()
        raw['dynamic_inventory'] = self.snapshot().to_dict()['dynamic_inventory']
        historical = repack(source, raw)
        self.assertEqual(historical.to_dict(), raw)
        with self.assertRaises(AlgorithmInputError):
            build_algorithm_input(historical)

    def test_all_five_cases_enter_v5_and_preserve_damage_sequences(self):
        for scenario, loss_slots, shock, closed in [(None, 0, 0, 0), ('DS01', 0, 0, 3),
                                                   ('DS02', 5, 0, 0), ('DS03', 0, -3, 0), ('DS04', 2, -1, 1)]:
            with self.subTest(scenario=scenario):
                snapshot = self.snapshot(scenario=scenario)
                bundle = build_algorithm_input(snapshot)
                tv = bundle.ds['timeview']
                offset = bundle.ds['range'][0]
                self.assertEqual(snapshot.to_dict()['schema'], 'run_input_snapshot_v5')
                self.assertEqual(tv['resource_inventory_model'], 'physical_inventory_v1')
                facts = tv['dynamic_inventory']
                paths = build_path_map(bundle.ds, bundle.run_params)
                self.assertGreater(len(paths.path_records), 0)
                self.assertEqual(sum(sum(v is not None for v in seq) for rows in facts['physical_loss_cap'].values() for seq in rows.values()), loss_slots)
                self.assertEqual(sum(sum(seq) for rows in tv['aircraft_shock'].values() for seq in rows.values()), shock)
                self.assertEqual(sum(sum(v == 0 for v in seq) for seq in tv['cap'].values()), closed)
                if scenario == 'DS02':
                    self.assertEqual(facts['physical_loss_cap']['A1']['FUEL-1'][2 - offset], 40)
                    self.assertEqual(facts['physical_loss_cap']['A1']['MAT-1'][4 - offset], 4)
                    self.assertEqual(facts['physical_loss_cap']['A1']['MAT-1'][6 - offset], 3)
                if scenario == 'DS04':
                    self.assertEqual(facts['physical_loss_cap']['A1']['FUEL-1'][2 - offset], 40)
                    self.assertEqual(facts['physical_loss_cap']['A1']['FUEL-1'][8 - offset], 20)

    def test_formal_v5_timeview_replay_never_raises_stock(self):
        source = self.snapshot(scenario='DS02')
        for inherited, expected in ((7, 4), (2, 2)):
            raw = source.to_dict()
            stock = raw['situation']['airports'][0]['operational_profile']['resource_stocks'][1]
            stock['initial_quantity'] = inherited  # Explicit isolated unit fixture state.
            bundle = build_algorithm_input(repack(source, raw))
            tv = bundle.ds['timeview']
            state = replay_dynamic_inventory(tv, {})['A1']['MAT-1']
            index = 4 - bundle.ds['range'][0]
            self.assertEqual(state['after_physical_loss'][index], expected)
            self.assertLessEqual(state['after_physical_loss'][index], state['inherited'][index])

    def test_missing_replenishment_capacity_is_not_filled_or_downgraded(self):
        airport = self.situation.airports[0]
        stocks = tuple(replace(s, replenishment_capacity_per_window=None) for s in airport.operational_profile.resource_stocks)
        situation = replace(self.situation, airports=(replace(airport, operational_profile=replace(airport.operational_profile, resource_stocks=stocks)),))
        snapshot = self.snapshot(situation=situation)
        self.assertNotIn('replenishment_capacity_per_window', snapshot.to_dict()['situation']['airports'][0]['operational_profile']['resource_stocks'][0])
        with self.assertRaisesRegex(AlgorithmInputError, 'V5 replenishment capacity is missing/invalid'):
            build_algorithm_input(snapshot)

    def test_historical_v4_without_inventory_model_remains_executable_and_cloneable(self):
        source = self.snapshot(model='legacy_budget_v4')
        raw = source.to_dict()
        raw['run_config'].pop('resource_inventory_model', None)
        historical = repack(source, raw)
        cloned = historical.clone_for_run('LEGACY_RETRY')
        self.assertEqual(cloned.to_dict()['schema'], 'run_input_snapshot_v4')
        self.assertNotIn('dynamic_inventory', build_algorithm_input(cloned).ds['timeview'])

    def test_invalid_inventory_model_type_fails_at_execution_not_read(self):
        source = self.snapshot()
        raw = source.to_dict()
        raw['run_config']['resource_inventory_model'] = ['physical_inventory_v1']
        historical = repack(source, raw)
        self.assertEqual(historical.to_dict(), raw)
        with self.assertRaises(AlgorithmInputError):
            build_algorithm_input(historical)

    def test_service_submit_and_retry_freeze_identical_v5_business_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'test.sqlite3'
            initialize_database(db)
            UserRepository(db).bootstrap_admin(login_name='admin', password='test-password')
            airport_repo = AirportRepository(db)
            for item in self.aircraft:
                airport_repo.save_aircraft_type(item)
            for item in self.resources:
                airport_repo.save_resource_type(item)
            for item in self.requirements:
                airport_repo.save_aircraft_resource_requirement(item)
            situations = SituationRepository(db)
            situations.save_situation(self.situation, owner_user_id='admin')
            snapshots = RunSnapshotRepository(db)
            snapshot_service = RunSnapshotService(airport_repository=airport_repo, situation_repository=situations, snapshot_repository=snapshots)
            service = RunService(snapshot_service=snapshot_service, run_repository=RunRepository(db))
            config = RunConfig.from_mapping({**self.raw_config, 'damage_scenario_id': 'DS04'})
            service.submit(run_id='SOURCE', situation_id='S1', owner_user_id='admin', run_config=config, od_distances=[ODDistance('A1', 'M1', 100)])
            frozen = snapshots.get('SOURCE')
            service.claim_for_worker('SOURCE')
            service.mark_failed_by_worker('SOURCE', message='unit fixture')
            situations.save_situation(replace(self.situation, name='changed later'), owner_user_id='admin')
            result = service.retry_failed('SOURCE', new_run_id='RETRY', actor_user_id='admin')
            expected = frozen.to_dict()
            expected['run_id'] = 'RETRY'
            self.assertEqual(snapshots.get('RETRY').to_dict(), expected)
            self.assertEqual(result.status, 'queued')
            self.assertEqual(snapshots.get('RETRY').to_dict()['schema'], 'run_input_snapshot_v5')
            malformed = frozen.to_dict()
            malformed['schema'] = 'run_input_snapshot_v4'
            with self.assertRaises(RunSnapshotValidationError):
                service.submit_snapshot(snapshot=repack(frozen, malformed), owner_user_id='admin')


if __name__ == '__main__':
    unittest.main()
