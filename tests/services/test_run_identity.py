import unittest

from backend.services.run_assessment_labels import run_display_identity
from tests.algorithm.test_snapshot_adapter import make_snapshot


class RunIdentityTests(unittest.TestCase):
    def test_identity_has_configuration_time_and_unique_trace(self):
        payload = make_snapshot(run_id='RUN-7193a5b2afbe44729dd96b7691840a92').to_dict()
        identity = run_display_identity(payload, {'started_at': '2026-10-10 05:42:01'})
        self.assertEqual('R-7193A5B2', identity['short_id'])
        self.assertIn('出动优先', identity['subtitle'])
        self.assertIn('10-10 13:42', identity['subtitle'])
        self.assertIn(payload['run_id'], identity['detail'])
        self.assertNotIn('D0', identity['title'])

    def test_creation_time_fallback_is_explicit_and_missing_time_is_not_invented(self):
        payload = make_snapshot().to_dict()
        self.assertIn('创建 10-10 13:42', run_display_identity(payload, {'created_at': '2026-10-10T05:42:01Z'})['subtitle'])
        self.assertIn('时间未记录', run_display_identity(payload)['subtitle'])

    def test_configuration_key_ignores_instance_identity_and_normalizes_legacy_default(self):
        first = make_snapshot(run_id='A').to_dict()
        second = make_snapshot(run_id='B').to_dict()
        second['run_config']['resource_inventory_model'] = 'legacy_budget_v4'
        a = run_display_identity(first)
        b = run_display_identity(second)
        self.assertEqual(a['configuration_key'], b['configuration_key'])
        second['run_config']['algorithm_seed'] += 1
        self.assertNotEqual(a['configuration_key'], run_display_identity(second)['configuration_key'])
