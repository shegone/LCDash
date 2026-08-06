"""Offline Package 2B tests for trusted profile resolution and GIS wiring."""

import json
import socket
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.config.settings import settings
from app.core.county_profiles import (
    CountyProfileValidationError,
    load_builtin_county_profile,
    resolve_county_profile,
)
from app.core.tenancy import TenantContext
from app.core.tenant_authorization import TenantAuthorizationDenied
from app.services.gis_reference_service import get_reference_catalog, get_reference_layer


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-operator",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id="synthetic-request",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class CountyProfileResolutionWiringTests(unittest.TestCase):
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
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def _write_layer(self, directory: str, filename: str) -> None:
        Path(directory, filename).write_text(
            json.dumps({"type": "FeatureCollection", "features": []}),
            encoding="utf-8",
        )

    def test_logan_context_cannot_resolve_a_northstar_profile(self):
        logan_context = trusted_context("logan-synthetic")
        northstar_profile = load_builtin_county_profile("northstar-fictional")

        with patch(
            "app.core.county_profiles.load_builtin_county_profile",
            return_value=northstar_profile,
        ):
            with self.assertRaisesRegex(
                CountyProfileValidationError,
                "tenant binding mismatch",
            ):
                resolve_county_profile(logan_context)
        self.assert_no_service_used()

    def test_catalog_route_uses_only_the_trusted_context_profile(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._write_layer(temporary_directory, "roads.geojson")
            self._write_layer(temporary_directory, "psap_boundary.geojson")
            with patch.object(settings, "gis_reference_dir", temporary_directory):
                result = get_reference_catalog(
                    trusted_context("logan-synthetic"),
                )

        self.assertEqual([item["id"] for item in result["layers"]], ["roads"])
        self.assert_no_service_used()

    def test_catalog_route_preserves_no_profile_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._write_layer(temporary_directory, "roads.geojson")
            self._write_layer(temporary_directory, "psap_boundary.geojson")
            with (
                patch.object(settings, "gis_reference_dir", temporary_directory),
                patch(
                    "app.services.gis_reference_service.authorize_tenant_action",
                    side_effect=AssertionError("legacy path must not authorize"),
                ) as authorize_mock,
            ):
                result = get_reference_catalog(None)

        self.assertEqual(
            [item["id"] for item in result["layers"]],
            ["psap", "roads"],
        )
        authorize_mock.assert_not_called()
        self.assert_no_service_used()

    def test_disabled_gis_denies_before_layer_path_inspection(self):
        profile = load_builtin_county_profile("logan-synthetic")
        disabled_profile = replace(
            profile,
            modules=profile.modules - {"gis"},
        )

        with (
            patch(
                "app.services.gis_reference_service.resolve_county_profile",
                return_value=disabled_profile,
            ),
            patch(
                "app.services.gis_reference_service._reference_path",
                side_effect=AssertionError("GIS path inspected before authorization"),
            ) as path_mock,
        ):
            with self.assertRaisesRegex(TenantAuthorizationDenied, "not enabled"):
                get_reference_catalog(trusted_context("logan-synthetic"))

        path_mock.assert_not_called()
        self.assert_no_service_used()

    def test_cross_tenant_and_unknown_tenants_deny_before_layer_inspection(self):
        northstar = load_builtin_county_profile("northstar-fictional")
        with (
            patch(
                "app.services.gis_reference_service.resolve_county_profile",
                return_value=northstar,
            ),
            patch(
                "app.services.gis_reference_service._reference_path",
                side_effect=AssertionError("GIS path inspected before authorization"),
            ) as mismatch_path_mock,
        ):
            with self.assertRaisesRegex(TenantAuthorizationDenied, "binding mismatch"):
                get_reference_catalog(trusted_context("logan-synthetic"))
        mismatch_path_mock.assert_not_called()

        with patch(
            "app.services.gis_reference_service._reference_path",
            side_effect=AssertionError("GIS path inspected for unknown tenant"),
        ) as unknown_path_mock:
            with self.assertRaises(CountyProfileValidationError):
                get_reference_catalog(trusted_context("unknown-synthetic"))
        unknown_path_mock.assert_not_called()
        self.assert_no_service_used()

    def test_layer_detail_allows_only_configured_trusted_layer(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._write_layer(temporary_directory, "roads.geojson")
            self._write_layer(temporary_directory, "psap_boundary.geojson")
            with patch.object(settings, "gis_reference_dir", temporary_directory):
                roads = get_reference_layer(
                    "roads",
                    tenant_context=trusted_context("logan-synthetic"),
                )
                psap = get_reference_layer(
                    "psap",
                    tenant_context=trusted_context("logan-synthetic"),
                )

        self.assertEqual(roads["layer"], "roads")
        self.assertIsNone(psap)
        self.assert_no_service_used()

    def test_layer_detail_preserves_legacy_no_context_behavior(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._write_layer(temporary_directory, "roads.geojson")
            with (
                patch.object(settings, "gis_reference_dir", temporary_directory),
                patch(
                    "app.services.gis_reference_service.resolve_county_profile",
                    side_effect=AssertionError("legacy path must not resolve"),
                ) as resolve_mock,
                patch(
                    "app.services.gis_reference_service.authorize_tenant_action",
                    side_effect=AssertionError("legacy path must not authorize"),
                ) as authorize_mock,
            ):
                roads = get_reference_layer("roads")

        self.assertEqual(roads["layer"], "roads")
        resolve_mock.assert_not_called()
        authorize_mock.assert_not_called()
        self.assert_no_service_used()

    def test_layer_detail_denies_before_layer_name_or_path_inspection(self):
        profile = load_builtin_county_profile("logan-synthetic")
        disabled_profile = replace(profile, modules=profile.modules - {"gis"})
        northstar = load_builtin_county_profile("northstar-fictional")

        for resolved_profile, error_pattern in (
            (disabled_profile, "not enabled"),
            (northstar, "binding mismatch"),
        ):
            with self.subTest(error=error_pattern):
                with (
                    patch(
                        "app.services.gis_reference_service.resolve_county_profile",
                        return_value=resolved_profile,
                    ),
                    patch(
                        "app.services.gis_reference_service._reference_path",
                        side_effect=AssertionError("layer path inspected before denial"),
                    ) as path_mock,
                ):
                    with self.assertRaisesRegex(
                        TenantAuthorizationDenied,
                        error_pattern,
                    ):
                        get_reference_layer(
                            "not-a-layer",
                            tenant_context=trusted_context("logan-synthetic"),
                        )
                path_mock.assert_not_called()

        with patch(
            "app.services.gis_reference_service._reference_path",
            side_effect=AssertionError("layer path inspected for unknown tenant"),
        ) as unknown_path_mock:
            with self.assertRaises(CountyProfileValidationError):
                get_reference_layer(
                    "not-a-layer",
                    tenant_context=trusted_context("unknown-synthetic"),
                )
        unknown_path_mock.assert_not_called()
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
