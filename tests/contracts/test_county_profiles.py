"""Offline contract tests for Package 2A county profile configuration."""

import json
import socket
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest.mock import patch

from app.core.county_profiles import (
    COUNTY_PROFILE_DIRECTORY,
    COUNTY_PROFILE_SCHEMA,
    CountyProfileValidationError,
    county_profile_from_data,
    load_builtin_county_profile,
)
from app.core.tenancy import CountyProfile
from app.integrations.contracts import ModuleCapability


def load_json(name: str):
    return json.loads((COUNTY_PROFILE_DIRECTORY / name).read_text(encoding="utf-8"))


class CountyProfileContractTests(unittest.TestCase):
    def setUp(self):
        self.blockers = [
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("network access blocked"),
            ),
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network access blocked"),
            ),
            patch("httpx.get", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.post", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.put", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.Client", side_effect=AssertionError("HTTP access blocked")),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_network(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_schema_is_versioned_and_matches_county_profile_fields(self):
        schema = json.loads(COUNTY_PROFILE_SCHEMA.read_text(encoding="utf-8"))
        dataclass_fields = {item.name for item in fields(CountyProfile)}

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["profile_version"], {"const": "1.0"})
        self.assertEqual(schema["properties"]["contract_version"], {"const": "1.0"})
        self.assertEqual(set(schema["required"]), dataclass_fields)
        self.assertFalse(schema["additionalProperties"])
        self.assert_no_network()

    def test_schema_enumerates_every_module_capability_once(self):
        schema = json.loads(COUNTY_PROFILE_SCHEMA.read_text(encoding="utf-8"))
        schema_capabilities = schema["$defs"]["moduleCapability"]["enum"]
        contract_capabilities = [item.value for item in ModuleCapability]

        self.assertEqual(set(schema_capabilities), set(contract_capabilities))
        self.assertEqual(len(schema_capabilities), len(set(schema_capabilities)))
        self.assert_no_network()

    def test_both_synthetic_profiles_validate_to_immutable_contracts(self):
        logan = load_builtin_county_profile("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")

        self.assertIsInstance(logan, CountyProfile)
        self.assertIsInstance(northstar, CountyProfile)
        self.assertEqual(logan.profile_version, "1.0")
        self.assertEqual(northstar.contract_version, "1.0")
        with self.assertRaises(FrozenInstanceError):
            logan.region = "changed"
        with self.assertRaises(TypeError):
            northstar.branding["accent_color"] = "#000000"
        self.assert_no_network()

    def test_fixtures_represent_full_capability_breadth_with_safe_enabled_subsets(self):
        expected = {item.value for item in ModuleCapability}
        for name in ("logan-synthetic.json", "northstar-fictional.json"):
            fixture = load_json(name)
            self.assertEqual(set(fixture["capabilities"]), expected)
            self.assertTrue(set(fixture["modules"]) < expected)
            self.assertFalse(
                {
                    "station_alerts",
                    "ems_delay",
                    "cad_messages",
                    "realtime_webhooks",
                    "paging",
                    "public_warning",
                }
                & set(fixture["modules"])
            )
            self.assertTrue(
                all(
                    permission.endswith(("_preview", "_dry_run"))
                    for permission in fixture["alert_permissions"]
                )
            )
        self.assert_no_network()

    def test_profiles_differ_by_configuration_without_python_forks(self):
        logan = load_builtin_county_profile("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")

        self.assertIs(type(logan), type(northstar))
        self.assertNotEqual(logan.tenant_id, northstar.tenant_id)
        self.assertNotEqual(logan.cad_provider, northstar.cad_provider)
        self.assertNotEqual(logan.timezone, northstar.timezone)
        self.assertNotEqual(logan.region, northstar.region)
        self.assertNotEqual(logan.branding, northstar.branding)
        self.assertNotEqual(logan.agencies, northstar.agencies)
        self.assertNotEqual(logan.unit_status_mappings, northstar.unit_status_mappings)
        self.assertNotEqual(logan.gis_sources, northstar.gis_sources)
        self.assertNotEqual(logan.identity_federation, northstar.identity_federation)
        self.assertNotEqual(logan.retention, northstar.retention)
        self.assertNotEqual(logan.ai_policy, northstar.ai_policy)
        self.assertNotEqual(logan.voice_profile, northstar.voice_profile)
        self.assertNotEqual(logan.modules, northstar.modules)
        self.assertNotEqual(logan.alert_permissions, northstar.alert_permissions)

        app_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (Path(__file__).parents[2] / "app").rglob("*.py")
        )
        self.assertNotIn("logan-synthetic", app_sources)
        self.assertNotIn("northstar-fictional", app_sources)
        self.assert_no_network()

    def test_secret_and_credential_shaped_keys_are_rejected_recursively(self):
        fixture = load_json("logan-synthetic.json")
        mutations = []

        root_secret = deepcopy(fixture)
        root_secret["password"] = "synthetic-value"
        mutations.append(root_secret)

        identity_secret = deepcopy(fixture)
        identity_secret["identity_federation"]["credential_reference"] = "synthetic"
        mutations.append(identity_secret)

        ai_secret = deepcopy(fixture)
        ai_secret["ai_policy"]["api_key"] = "synthetic-value"
        mutations.append(ai_secret)

        mapping_secret = deepcopy(fixture)
        mapping_secret["unit_status_mappings"]["access_token"] = "Available"
        mutations.append(mapping_secret)

        for mutation in mutations:
            with self.assertRaisesRegex(
                CountyProfileValidationError,
                "Secret or credential-shaped key",
            ):
                county_profile_from_data(mutation)
        self.assert_no_network()

    def test_missing_unknown_and_unsafe_operational_fields_fail_closed(self):
        fixture = load_json("northstar-fictional.json")

        missing = deepcopy(fixture)
        del missing["retention"]
        with self.assertRaisesRegex(CountyProfileValidationError, "missing required"):
            county_profile_from_data(missing)

        unknown = deepcopy(fixture)
        unknown["branding"]["theme_script"] = "synthetic"
        with self.assertRaisesRegex(CountyProfileValidationError, "unsupported fields"):
            county_profile_from_data(unknown)

        unsafe = deepcopy(fixture)
        unsafe["alert_permissions"] = ["station_alert_release"]
        with self.assertRaisesRegex(CountyProfileValidationError, "unsafe alert permission"):
            county_profile_from_data(unsafe)
        self.assert_no_network()

    def test_ai_and_voice_safety_invariants_fail_closed(self):
        fixture = load_json("logan-synthetic.json")

        ai_write = deepcopy(fixture)
        ai_write["ai_policy"]["advisory_only"] = False
        with self.assertRaisesRegex(CountyProfileValidationError, "AI must remain advisory"):
            county_profile_from_data(ai_write)

        protected_data = deepcopy(fixture)
        protected_data["ai_policy"]["protected_data_allowed"] = True
        with self.assertRaisesRegex(CountyProfileValidationError, "AI must remain advisory"):
            county_profile_from_data(protected_data)

        voice = deepcopy(fixture)
        voice["voice_profile"]["pronunciation_911"] = "nine hundred eleven"
        with self.assertRaisesRegex(CountyProfileValidationError, "pronounce 911 safely"):
            county_profile_from_data(voice)
        self.assert_no_network()


if __name__ == "__main__":
    unittest.main()
