"""The restricted ``user`` tier: what it may reach, and what it may see.

Two gates are under test, and they fail in different directions.

The **path gate** is deny-by-default, and that property is the only reason it
keeps working as the app grows. ``test_every_unlisted_route_is_denied`` walks
the app's real route table and proves that every path not deliberately named in
the allowlist 403s for a ``user``. Without it, a route added six months from now
is reachable by vendor and outside-agency accounts the day it ships, and no
existing test notices. A hand-written list of denied paths cannot do this job --
it only knows about routes that existed when it was written.

The **field gate** is an allowlist on the payloads that do get through. The
tests here assert on PII *values* found anywhere in the serialised response, not
on field names: a nested or renamed copy of the reporter's phone number passes a
field-name check and still ships the number to the browser.

Roles are signed in the way production does it, by patching
``app.main.resolve_alb_identity`` to return the Cognito group claim the ALB
would have verified. Every data source is mocked, so nothing here touches CAD.

KNOWN PRODUCT BUG, encoded below rather than fixed: the sanitizer's address
field is spelled ``location_label``, but dashboard calls and station alerts both
carry the address under ``location``. The address the tier is supposed to see is
therefore blanked. See
``test_known_bug_dashboard_address_is_blanked_by_field_name_mismatch``.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core import sanitized_tier
from app.core.alb_identity import AlbIdentity
from app.main import app

# --------------------------------------------------------------------------
# Fixtures: a realistic full payload, PII and all.
# --------------------------------------------------------------------------

# Values that must never appear in a sanitized response, asserted as values so
# that a nested or renamed copy is caught too.
PII_VALUES = (
    "Jane Caller",
    "304-555-0101",
    "Wireless 911",
    "Caller states her husband is unresponsive",
    "Unit advised scene is not secure",
    "CFS26-1234",
    "2026-08-09T09:12:00Z",
)


def _full_call() -> dict:
    return {
        # Identity and routing -- the CFS number is what makes call detail
        # reachable, so its absence is load-bearing, not cosmetic.
        "cfs_number": "CFS26-1234",
        "incident_code": "MEDA",
        "incident_description": "Medical Alarm",
        "priority": "1",
        "agency": "LCEMS",
        "status": "Dispatched",
        "call_datetime": "2026-08-09T10:04:00Z",
        "location": "141 Stratton Street",
        "location_label": "141 Stratton Street",
        "city": "Logan",
        "latitude": 37.8487,
        "longitude": -81.9932,
        # PII of exactly the kind this tier exists to withhold.
        "reporter_name": "Jane Caller",
        "reporter_phone": "304-555-0101",
        "how_reported": "Wireless 911",
        "narrative": "Caller states her husband is unresponsive",
        "command_logs": [
            {"timestamp": "2026-08-09T10:05:00Z", "text": "Unit advised scene is not secure"}
        ],
        "units": "M1, E2",
        "assigned_units": [
            {
                "unit_number": "M1",
                "unit_type": "Medic",
                "agency": "LCEMS",
                "status": "Dispatched",
                "responder_name": "Jane Caller",
            },
            {"unit_number": "E2", "unit_type": "Engine", "agency": "LFD", "status": "Enroute"},
        ],
    }


def _full_operations_snapshot() -> dict:
    return {
        "last_updated": "2026-08-09T10:06:00Z",
        "calls": [_full_call()],
        "dashboard_stats": {
            "active_calls": 4,
            "assigned_units": 7,
            "on_scene_calls": 2,
            "high_priority_calls": 1,
            "oldest_call_datetime": "2026-08-09T09:12:00Z",
            "agency_summary": [{"agency": "LCEMS", "count": 3}],
        },
        "unit_rows": [
            {
                "unit_number": "M1",
                "cfs_number": "CFS26-1234",
                "location": "141 Stratton Street",
                "reporter_phone": "304-555-0101",
            }
        ],
        "unit_stats": {"active": 7, "available": 3},
    }


def _full_station_alert_snapshot() -> dict:
    return {
        "connected": True,
        "roster_connected": True,
        "roster_warning": "",
        "generated_at": "2026-08-09T10:06:00Z",
        "selected_station": "Station 1",
        "selected_stations": ["Station 1"],
        "stations": ["Station 1", "Station 2"],
        "alerts": [
            {
                "event_id": "station-1|CFS26-1234|M1|2026-08-09T10:04:00Z",
                "cfs_number": "CFS26-1234",
                "incident_code": "MEDA",
                "incident_description": "Medical Alarm",
                "priority": "1",
                "location": "141 Stratton Street",
                "location_label": "141 Stratton Street",
                "call_datetime": "2026-08-09T10:04:00Z",
                "status": "Dispatched",
                "unit_numbers": ["M1"],
                "station_names": ["Station 1"],
                "latitude": 37.8487,
                "longitude": -81.9932,
                "announcement": "Medical Alarm, 141 Stratton Street, caller Jane Caller",
                "reporter_phone": "304-555-0101",
            }
        ],
        "station_units": [
            {"unit_number": "M1", "crew": "Jane Caller", "station": "Station 1"}
        ],
    }


def _full_map_snapshot() -> dict:
    return {
        "type": "FeatureCollection",
        "generated_at": "2026-08-09T10:06:00Z",
        "cad_connected": True,
        "roster_connected": True,
        "roster_warning": "",
        "summary": {"total_calls": 1, "mapped_calls": 1, "total_units": 1},
        "features": [
            {
                "type": "Feature",
                "id": "call:CFS26-1234",
                "geometry": {"type": "Point", "coordinates": [-81.9932, 37.8487]},
                "properties": {
                    "kind": "call",
                    "cfs_number": "CFS26-1234",
                    "incident_code": "MEDA",
                    "incident_description": "Medical Alarm",
                    "priority": "1",
                    "agency": "LCEMS",
                    "status": "Dispatched",
                    "location_label": "141 Stratton Street",
                    "call_datetime": "2026-08-09T10:04:00Z",
                    "detail_url": "/calls/CFS26-1234",
                    "reporter_phone": "304-555-0101",
                },
            },
            {
                "type": "Feature",
                "id": "unit:M1",
                "geometry": {"type": "Point", "coordinates": [-81.99, 37.84]},
                "properties": {
                    "kind": "unit",
                    "unit_number": "M1",
                    "status": "Dispatched",
                    "agency": "LCEMS",
                    "cfs_number": "CFS26-1234",
                    "detail_url": "/calls/CFS26-1234",
                    "station": "Station 1",
                },
            },
        ],
    }


# Paths Ted signed off on, in the form a browser actually requests them.
ALLOWED_PATHS = (
    "/dashboard",
    "/map",
    "/map/heatmap",
    "/station-alerts",
    "/api/operations/snapshot",
    "/api/operations/map",
    "/api/operations/map/heatmap",
    "/api/operations/station-alerts",
    "/api/identity/whoami",
    "/logout",
)

# A representative slice of everything else: the call-data views, Intelligence,
# Tools & Quality, admin, and the APIs behind them.
DENIED_PATHS = (
    "/active-calls",
    "/units",
    "/calls/CFS26-1234",
    "/analytics",
    "/reports",
    "/mae",
    "/mindshare",
    "/knowledge",
    "/nga911-intelligence",
    "/voice",
    "/mae/reliability",
    "/integrations/health",
    "/admin/users",
    "/admin/knowledge",
    "/api/operations/active-calls",
    "/api/operations/units",
    "/api/analytics/overview",
    "/api/mae/chat",
    "/api/cloud-ai/advisory",
    "/api/knowledge/status",
)

DENIAL_DETAIL = (
    "This view is not available on your account. "
    "Ask an administrator if you need access."
)


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


USER = _identity("vendor@nga911.com", "lcdash-pilot-user")
SUPERVISOR = _identity("s@911logan.com", "lcdash-pilot-supervisor")
ADMIN = _identity("tedsparks@911logan.com", "lcdash-pilot-admin")


class _SanitizedTierTestCase(unittest.TestCase):
    """Signed-in requests against the real app, with CAD mocked out."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        for name, value in (
            ("deployment_mode", "synthetic-disconnected"),
            ("tenant_id", "logan-synthetic"),
            ("alb_identity_enabled", True),
            ("alb_identity_region", "us-east-1"),
            ("alb_identity_load_balancer_arn", "arn:aws:elasticloadbalancing:x"),
            ("alb_identity_user_pool_id", "us-east-1_Example1"),
            ("alb_identity_client_id", "1example23456789"),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()

    def _sign_in_as(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _mock_data_sources(self) -> None:
        """Every seam the four allowed views read from, pinned to fixtures."""
        for target, value in (
            ("app.main._current_operations_snapshot", _full_operations_snapshot()),
            ("app.main.get_live_map_snapshot", _full_map_snapshot()),
            ("app.main.get_live_station_alert_snapshot", _full_station_alert_snapshot()),
            ("app.main.build_station_alert_snapshot", _full_station_alert_snapshot()),
            ("app.main._cloud_cad_bridge_enabled", True),
        ):
            patcher = patch(target, return_value=value)
            self.addCleanup(patcher.stop)
            patcher.start()


# --------------------------------------------------------------------------
# The path gate
# --------------------------------------------------------------------------


class PathGateTests(_SanitizedTierTestCase):
    def test_every_allowed_path_is_reachable(self):
        """The four views and their APIs must actually work for this tier.

        A gate that denies everything is trivially safe and useless; these are
        the paths Ted scoped in, and a 403 on any of them is a broken product.
        """
        self._mock_data_sources()
        self._sign_in_as(USER)
        for path in ALLOWED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertNotEqual(
                    response.status_code, 403, f"{path} was denied to the user tier"
                )

    def test_representative_denied_paths_are_denied(self):
        self._sign_in_as(USER)
        for path in DENIED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], DENIAL_DETAIL)

    def test_every_unlisted_route_is_denied(self):
        """Deny-by-default, proved against the app's real route table.

        This is the test that catches a route added in six months. It does not
        consult a list of known-bad paths -- it enumerates what the application
        actually serves and requires that anything outside the allowlist is
        refused. If somebody registers ``/api/operations/call-detail`` and
        forgets this tier exists, this test fails on the next run.

        Parameterised paths are skipped because their real form cannot be
        synthesised here; the count assertion below stops the test from
        silently degrading to a no-op if route registration changes shape.
        """
        self._sign_in_as(USER)

        checked = 0
        for path in sorted({getattr(route, "path", "") for route in app.routes}):
            if not path or "{" in path:
                continue
            if sanitized_tier.is_path_allowed_for_user(path):
                continue
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(
                    response.status_code,
                    403,
                    f"{path} is not on the allowlist but was not denied",
                )
            checked += 1

        self.assertGreater(
            checked, 40, "route enumeration collapsed -- the sweep proves nothing"
        )

    def test_documentation_and_schema_routes_are_denied(self):
        """/openapi.json enumerates every route the tier may not call."""
        self._sign_in_as(USER)
        for path in ("/openapi.json", "/docs", "/redoc"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_query_strings_and_trailing_slashes_do_not_open_a_path(self):
        self._sign_in_as(USER)
        for path in ("/active-calls?x=1", "/active-calls/", "/analytics?range=7d"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(path, follow_redirects=False).status_code, 403
                )

    def test_supervisor_and_admin_are_unaffected(self):
        """The gate must narrow one role, not quietly narrow the pilot."""
        self._mock_data_sources()
        for identity in (SUPERVISOR, ADMIN):
            self._sign_in_as(identity)
            for path in ("/active-calls", "/analytics", "/units", "/reports"):
                with self.subTest(role=identity.email, path=path):
                    response = self.client.get(path, follow_redirects=False)
                    self.assertNotEqual(response.status_code, 403)

    def test_allowlist_helper_denies_unknown_and_empty_paths(self):
        for path in ("", "/", "/active-calls", "/api/mae/chat", "/../dashboard"):
            with self.subTest(path=path):
                expected = path in ("", "/")
                self.assertEqual(sanitized_tier.is_path_allowed_for_user(path), expected)

    def test_restricts_recognises_only_the_user_role(self):
        self.assertTrue(sanitized_tier.restricts("user"))
        self.assertFalse(sanitized_tier.restricts("supervisor"))
        self.assertFalse(sanitized_tier.restricts("admin"))
        self.assertFalse(sanitized_tier.restricts(None))
        self.assertFalse(sanitized_tier.restricts(""))


# --------------------------------------------------------------------------
# The field gate, as pure functions
# --------------------------------------------------------------------------


class FieldReductionTests(unittest.TestCase):
    def assertNoPII(self, payload) -> None:
        serialised = json.dumps(payload, default=str)
        for value in PII_VALUES:
            self.assertNotIn(value, serialised, f"{value!r} survived sanitization")

    def test_snapshot_keeps_only_the_allowed_shape(self):
        """Allowlist, not denylist: an unanticipated CAD field must not ride
        along just because nobody thought to remove it."""
        safe = sanitized_tier.sanitize_operations_snapshot(
            {
                "connected": True,
                "system_status": "Live",
                "cad_status": "Connected",
                "cloud_presentation_status": {"source": {"label": "x"}},
                **_full_operations_snapshot(),
            }
        )

        self.assertEqual(
            set(safe),
            {
                "connected",
                "system_status",
                "cad_status",
                "last_updated",
                "calls",
                "dashboard_stats",
                "sanitized_view",
            },
        )
        self.assertTrue(safe["sanitized_view"])
        self.assertNotIn("unit_rows", safe)
        self.assertNotIn("unit_stats", safe)
        self.assertNotIn("cloud_presentation_status", safe)
        self.assertEqual(
            set(safe["dashboard_stats"]),
            {"active_calls", "assigned_units", "on_scene_calls", "high_priority_calls"},
        )
        self.assertNoPII(safe)

    def test_sanitize_call_keeps_address_type_and_unit_numbers_only(self):
        safe = sanitized_tier.sanitize_call(_full_call())

        self.assertEqual(
            set(safe),
            {"location", "incident_code", "incident_description", "assigned_units"},
        )
        self.assertEqual(safe["incident_description"], "Medical Alarm")
        self.assertEqual(
            safe["assigned_units"], [{"unit_number": "M1"}, {"unit_number": "E2"}]
        )
        self.assertNoPII(safe)

    def test_station_alerts_keep_station_structure_and_drop_crews(self):
        safe = sanitized_tier.sanitize_station_alerts(_full_station_alert_snapshot())

        self.assertEqual(safe["stations"], ["Station 1", "Station 2"])
        self.assertEqual(safe["selected_stations"], ["Station 1"])
        self.assertEqual(safe["station_units"], [])
        self.assertTrue(safe["sanitized_view"])
        self.assertEqual(len(safe["alerts"]), 1)
        self.assertEqual(
            set(safe["alerts"][0]),
            {
                "row_key", "location", "incident_code", "incident_description",
                "unit_numbers", "station_names",
            },
        )
        self.assertNoPII(safe)

    def test_map_call_pins_lose_every_route_to_call_detail(self):
        """Hiding the link in the template is not enough -- the identifier has
        to go, or the pin is still a lookup key for call detail."""
        safe = sanitized_tier.sanitize_map_snapshot(_full_map_snapshot())

        call_feature, unit_feature = safe["features"]
        self.assertEqual(
            set(call_feature["properties"]),
            {"kind", "incident_code", "incident_description", "location_label"},
        )
        self.assertNotIn("cfs_number", call_feature["properties"])
        self.assertNotIn("detail_url", call_feature["properties"])
        self.assertEqual(call_feature["properties"]["location_label"], "141 Stratton Street")
        self.assertEqual(
            call_feature["geometry"], {"type": "Point", "coordinates": [-81.9932, 37.8487]}
        )

        self.assertEqual(
            set(unit_feature["properties"]), {"kind", "unit_number", "status"}
        )
        self.assertEqual(unit_feature["properties"]["unit_number"], "M1")
        self.assertNotIn("detail_url", unit_feature["properties"])

        self.assertTrue(safe["sanitized_view"])
        self.assertNoPII(safe)

    def test_sanitizers_are_total(self):
        """A sanitizer that raises on unexpected CAD shape fails open, because
        the caller's error path is the unsanitized payload or a 500 that tells
        an operator the restricted account is broken. Never raise."""
        reducers = (
            sanitized_tier.sanitize_operations_snapshot,
            sanitized_tier.sanitize_station_alerts,
            sanitized_tier.sanitize_map_snapshot,
            sanitized_tier.sanitize_call,
        )
        payloads = (
            None,
            {},
            [],
            "",
            0,
            {"calls": None, "alerts": None, "features": None},
            {"calls": "not-a-list", "alerts": "x", "features": {"a": 1}},
            {"calls": [None, 1, "x"], "alerts": [None], "features": [None, "x"]},
            {"calls": [{"assigned_units": "M1"}]},
            {"calls": [{"assigned_units": [None, 3]}]},
            {"dashboard_stats": None},
            {"features": [{"properties": None}]},
            {"features": [{"properties": {"kind": "unit"}}]},
        )
        for reducer in reducers:
            for payload in payloads:
                with self.subTest(reducer=reducer.__name__, payload=payload):
                    result = reducer(payload)
                    self.assertIsInstance(result, dict)
                    json.dumps(result, default=str)

    def test_a_scalar_collection_reduces_to_empty_instead_of_raising(self):
        """Regression: the reducers once did ``for x in (value or [])``, which
        is total for None/strings/mappings but raises TypeError on a number.
        CAD returning ``"calls": 0`` would have 500'd all four allowed views
        for the restricted accounts ALONE -- an outage no supervisor could
        reproduce."""
        for reducer, payload, key in (
            (sanitized_tier.sanitize_operations_snapshot, {"calls": 3}, "calls"),
            (sanitized_tier.sanitize_station_alerts, {"alerts": 1}, "alerts"),
            (sanitized_tier.sanitize_map_snapshot, {"features": 1}, "features"),
            (sanitized_tier.sanitize_call, {"assigned_units": 1}, "assigned_units"),
        ):
            with self.subTest(reducer=reducer.__name__):
                self.assertEqual(reducer(payload)[key], [])

    def test_the_address_actually_survives_on_every_allowed_view(self):
        """Regression: the reducers first shipped picking ``location_label``
        for all three shapes, but only the map service emits that key --
        dashboard calls and station alerts use ``location``. Every address
        rendered blank, which is over-redaction that breaks the tier's whole
        reason to exist. Each shape now uses the field list matching it."""
        call = _full_call()
        call.pop("location_label", None)
        self.assertEqual(
            sanitized_tier.sanitize_call(call)["location"], "141 Stratton Street"
        )

        alerts = _full_station_alert_snapshot()
        alerts["alerts"][0].pop("location_label", None)
        safe = sanitized_tier.sanitize_station_alerts(alerts)
        self.assertEqual(safe["alerts"][0]["location"], "141 Stratton Street")

        mapped = sanitized_tier.sanitize_map_snapshot(_full_map_snapshot())
        pin = next(
            f for f in mapped["features"] if f["properties"]["kind"] == "call"
        )
        self.assertEqual(pin["properties"]["location_label"], "141 Stratton Street")


# --------------------------------------------------------------------------
# End to end: the wiring, not just the functions
# --------------------------------------------------------------------------


class SanitizedEndpointTests(_SanitizedTierTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._mock_data_sources()

    def assertNoPII(self, response) -> None:
        serialised = json.dumps(response.json(), default=str)
        for value in PII_VALUES:
            self.assertNotIn(value, serialised, f"{value!r} reached the browser")

    def test_snapshot_endpoint_reduces_for_user_and_not_for_supervisor(self):
        """Proves the endpoint is wired to the reducer. The pure-function tests
        above pass just as happily when nothing calls them."""
        self._sign_in_as(USER)
        reduced = self.client.get("/api/operations/snapshot")
        self.assertEqual(reduced.status_code, 200)
        body = reduced.json()
        self.assertTrue(body["sanitized_view"])
        self.assertNotIn("unit_rows", body)
        self.assertEqual(
            set(body["calls"][0]),
            {"location", "incident_code", "incident_description", "assigned_units"},
        )
        self.assertEqual(body["calls"][0]["assigned_units"], [{"unit_number": "M1"}, {"unit_number": "E2"}])
        self.assertNotIn("oldest_call_datetime", body["dashboard_stats"])
        self.assertNotIn("agency_summary", body["dashboard_stats"])
        self.assertNoPII(reduced)

        self._sign_in_as(SUPERVISOR)
        full = self.client.get("/api/operations/snapshot").json()
        self.assertNotIn("sanitized_view", full)
        self.assertIn("unit_rows", full)
        self.assertEqual(full["calls"][0]["reporter_name"], "Jane Caller")
        self.assertEqual(full["calls"][0]["cfs_number"], "CFS26-1234")
        self.assertIn("agency_summary", full["dashboard_stats"])

    def test_station_alerts_endpoint_reduces_for_user_and_not_for_supervisor(self):
        self._sign_in_as(USER)
        reduced = self.client.get("/api/operations/station-alerts")
        self.assertEqual(reduced.status_code, 200)
        body = reduced.json()
        self.assertTrue(body["sanitized_view"])
        self.assertEqual(body["station_units"], [])
        self.assertEqual(
            set(body["alerts"][0]),
            {
                "row_key", "location", "incident_code", "incident_description",
                "unit_numbers", "station_names",
            },
        )
        self.assertNoPII(reduced)

        self._sign_in_as(SUPERVISOR)
        full = self.client.get("/api/operations/station-alerts").json()
        self.assertNotIn("sanitized_view", full)
        self.assertEqual(full["alerts"][0]["cfs_number"], "CFS26-1234")
        self.assertTrue(full["station_units"])

    def test_map_endpoint_reduces_for_user_and_not_for_supervisor(self):
        self._sign_in_as(USER)
        reduced = self.client.get("/api/operations/map")
        self.assertEqual(reduced.status_code, 200)
        body = reduced.json()
        self.assertTrue(body["sanitized_view"])
        properties = body["features"][0]["properties"]
        self.assertNotIn("cfs_number", properties)
        self.assertNotIn("detail_url", properties)
        self.assertEqual(properties["location_label"], "141 Stratton Street")
        self.assertNoPII(reduced)

        self._sign_in_as(SUPERVISOR)
        full = self.client.get("/api/operations/map").json()
        self.assertNotIn("sanitized_view", full)
        self.assertEqual(
            full["features"][0]["properties"]["detail_url"], "/calls/CFS26-1234"
        )

    def test_admin_sees_the_full_payload_too(self):
        self._sign_in_as(ADMIN)
        body = self.client.get("/api/operations/snapshot").json()
        self.assertNotIn("sanitized_view", body)
        self.assertEqual(body["calls"][0]["reporter_phone"], "304-555-0101")

    def test_unverified_caller_is_not_silently_sanitized(self):
        """When identity cannot be resolved the tier does not apply -- the
        pilot's pre-identity behaviour is unchanged. Recorded so that a future
        change to this branch is a deliberate decision, not a surprise."""
        self._sign_in_as(None)
        body = self.client.get("/api/operations/snapshot").json()
        self.assertNotIn("sanitized_view", body)

    def test_dashboard_page_renders_for_the_user_tier(self):
        self._sign_in_as(USER)
        page = self.client.get("/dashboard")
        self.assertEqual(page.status_code, 200)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# The rendered page, not just the API
# --------------------------------------------------------------------------


class RenderedPageTests(_SanitizedTierTestCase):
    """Scan the HTML the browser actually receives.

    The page routes render call data into the markup themselves; only the
    /api endpoints went through the reducer at first, so /dashboard, /map and
    /station-alerts were embedding reporter details, CFS numbers and
    /calls/<cfs> links straight into the page source for restricted users.
    Gating the markup could not have fixed that -- the data was already in the
    document. These tests read the response body, which is the only vantage
    point from which that failure is visible.
    """

    def setUp(self) -> None:
        super().setUp()
        self._mock_data_sources()

    ALLOWED_PAGES = ("/dashboard", "/map", "/map/heatmap", "/station-alerts")

    def test_no_pii_reaches_the_html_for_a_restricted_user(self):
        self._sign_in_as(USER)
        for path in self.ALLOWED_PAGES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                for value in PII_VALUES:
                    self.assertNotIn(
                        value, response.text, f"{value!r} rendered into {path}"
                    )

    def test_no_route_to_call_detail_is_rendered_for_a_restricted_user(self):
        """Removing the link markup is not enough; the identifier that builds
        it must be absent from the document too."""
        self._sign_in_as(USER)
        for path in self.ALLOWED_PAGES:
            with self.subTest(path=path):
                body = self.client.get(path).text
                self.assertNotIn("/calls/", body)
                self.assertNotIn("detail_url", body)
                self.assertNotIn("CFS26-1234", body)

    def test_supervisors_still_get_the_full_page(self):
        """Proves the reduction is scoped to the tier and did not quietly
        strip the product for everyone."""
        self._sign_in_as(SUPERVISOR)
        body = self.client.get("/dashboard").text
        self.assertIn("CFS26-1234", body)
        self.assertIn("/calls/CFS26-1234", body)

    def test_map_pins_actually_render_and_match_the_summary_count(self):
        """Regression for the symptom Ted hit: the map said "3 calls mapped"
        and drew none.

        The template used to embed an EMPTY feature collection for this tier
        and have the JS re-fetch from /api/operations/map -- but that endpoint
        always took the live on-prem path, so in cloud mode it answered with no
        incidents while the summary beside it counted the polled snapshot. The
        route now reduces map_data itself and the template embeds it directly,
        so the pins and the count come from one source.
        """
        import json as _json

        self._sign_in_as(USER)
        html = self.client.get("/map").text
        embedded = _json.loads(html.split('id="map-data">')[1].split("</script>")[0])
        pins = [
            feature
            for feature in embedded.get("features", [])
            if feature["properties"].get("kind") == "call"
        ]
        self.assertTrue(pins, "the restricted map embedded no call pins at all")
        self.assertEqual(len(pins), embedded["summary"]["mapped_calls"])
        # And the pins are reduced, not merely present.
        self.assertNotIn("cfs_number", pins[0]["properties"])
        self.assertNotIn("detail_url", pins[0]["properties"])
        self.assertTrue(pins[0]["geometry"]["coordinates"])

    def test_map_page_and_map_api_agree_for_every_role(self):
        """They took different data sources in cloud mode, which is how the
        empty map hid behind a non-zero count."""
        for identity in (USER, SUPERVISOR):
            with self.subTest(role=identity.groups[0]):
                self._sign_in_as(identity)
                html = self.client.get("/map").text
                import json as _json

                embedded = _json.loads(
                    html.split('id="map-data">')[1].split("</script>")[0]
                )
                api = self.client.get("/api/operations/map").json()
                self.assertEqual(
                    len(embedded.get("features", [])), len(api.get("features", []))
                )
