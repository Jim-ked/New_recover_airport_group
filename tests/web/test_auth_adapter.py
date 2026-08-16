from __future__ import annotations

import unittest

from backend.auth.csrf import CsrfValidationError, require_csrf_match
from backend.auth.session_policy import validate_session
from backend.auth.principal import (
    Principal,
    PrincipalValidationError,
    principal_from_session_user,
)


class AuthAdapterTests(unittest.TestCase):
    def test_existing_session_user_shape_maps_to_stable_principal_permissions(self):
        viewer = principal_from_session_user({"user_id": "U1", "name": "x", "role": "viewer"})
        self.assertIsNotNone(viewer)
        self.assertTrue(viewer.has_permission("runs.read"))
        self.assertTrue(viewer.has_permission("results.read"))
        self.assertFalse(viewer.has_permission("runs.execute"))

        operator = principal_from_session_user({"user_id": "U2", "role": "user"})
        self.assertEqual("user", operator.role)
        self.assertTrue(operator.has_permission("runs.execute"))

        admin = principal_from_session_user({"user_id": "A1", "role": "admin"})
        self.assertTrue(admin.is_admin)
        self.assertTrue(admin.has_permission("runs.execute"))

    def test_unknown_role_degrades_to_viewer_not_operator(self):
        principal = principal_from_session_user({"user_id": "U1", "role": "mystery"})
        self.assertEqual("viewer", principal.role)
        self.assertFalse(principal.has_permission("runs.execute"))

    def test_missing_or_malformed_session_identity_is_not_guessed(self):
        self.assertIsNone(principal_from_session_user(None))
        with self.assertRaises(PrincipalValidationError):
            principal_from_session_user({"role": "operator"})

    def test_csrf_requires_exact_nonblank_session_and_supplied_tokens(self):
        require_csrf_match(expected="abc", supplied="abc")
        for expected, supplied in ((None, "abc"), ("abc", None), ("abc", "def"), ("", "")):
            with self.assertRaises(CsrfValidationError):
                require_csrf_match(expected=expected, supplied=supplied)

    def test_session_policy_enforces_idle_absolute_and_auth_revision(self):
        authority = {
            "user_id": "U1", "role": "operator", "is_disabled": False, "auth_revision": 4,
        }
        session_user = {
            "user_id": "U1", "role": "operator", "auth_revision": 4,
            "issued_at": 1000, "last_seen_at": 1500,
        }
        ok = validate_session(session_user, authority, now=1600, idle_timeout_seconds=300, absolute_timeout_seconds=1000)
        self.assertTrue(ok.valid)
        self.assertTrue(ok.refresh_last_seen)
        idle = validate_session(session_user, authority, now=1901, idle_timeout_seconds=300, absolute_timeout_seconds=1000)
        self.assertEqual("idle_timeout", idle.reason)
        absolute = validate_session(session_user, authority, now=2101, idle_timeout_seconds=9999, absolute_timeout_seconds=1000)
        self.assertEqual("absolute_timeout", absolute.reason)
        changed = validate_session(session_user, {**authority, "auth_revision": 5}, now=1600)
        self.assertEqual("auth_revision_changed", changed.reason)
        disabled = validate_session(session_user, {**authority, "is_disabled": True}, now=1600)
        self.assertEqual("account_disabled", disabled.reason)

    def test_explicit_permission_set_is_preserved_for_contract_tests(self):
        principal = Principal("U1", permissions=frozenset({"runs.read"}))
        self.assertTrue(principal.has_permission("runs.read"))
        self.assertFalse(principal.has_permission("runs.execute"))


if __name__ == "__main__":
    unittest.main()
