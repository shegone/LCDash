"""Contract tests for the in-memory-only cloud CAD display bridge."""

from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.integrations.cad.cloud_read_runtime import (
    CloudCadDisplayState,
    _items,
    _normalize_calls,
    _normalize_units,
)
from app.services.operations_service import (
    build_cloud_call_detail,
    build_cloud_operations_snapshot,
    build_cloud_unit_snapshot,
    get_live_operations_snapshot,
)


class CloudCadDisplayBridgeTests(unittest.TestCase):
    def state(self):
        return CloudCadDisplayState(
            calls=(
                {
                    "cfs_number": "synthetic-call-1",
                    "incident_code": "TEST",
                    "incident_description": "Approved Medical Call",
                    "priority": "1",
                    "agency": "SYN",
                    "status": "Active",
                    "call_datetime": "2026-08-05T16:00:00+00:00",
                    "location_label": "100 Synthetic Street, Logan",
                    "beat": "Beat 1",
                    "zone": "North",
                    "city": "Logan",
                    "latitude": 37.8487,
                    "longitude": -81.9935,
                    "assigned_units": (
                        {
                            "unit_number": "SYN1",
                            "unit_type": "Ambulance",
                            "agency": "SYN",
                            "status": "On Scene",
                        },
                        {"raw_nested_payload": "must not stringify"},
                    ),
                    "command_logs": (
                        {
                            "timestamp": "2026-08-05T16:02:00Z",
                            "unit_number": "SYN1",
                            "text": "Unit assigned",
                            "status": "Assigned",
                            "creator": "Synthetic Dispatcher",
                        },
                        {
                            "timestamp": "2026-08-05T16:08:00Z",
                            "unit_number": "SYN1",
                            "text": "Unit arrived",
                            "status": "On Scene",
                            "creator": "Synthetic Dispatcher",
                        },
                        {"raw_nested_payload": "must not stringify"},
                    ),
                    "authorization": "forbidden-header",
                    "password": "forbidden-secret",
                },
            ),
            units=(
                {
                    "unit_number": "SYN1",
                    "agency": "SYN",
                    "unit_type": "Synthetic",
                    "status": "Assigned",
                    "station": "Station 1",
                    "assignment_cfs_number": "synthetic-call-1",
                    "narrative": "raw unit narrative",
                    "token": "forbidden-token",
                },
            ),
            last_success_at=datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc),
            last_attempt_at=datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc),
        )

    def test_bridge_uses_only_supplied_state_and_never_calls_cad_or_secret_providers(self):
        with (
            patch(
                "app.services.operations_service.get_active_calls",
                side_effect=AssertionError("CAD HTTP path must not run"),
            ) as active_calls,
            patch(
                "app.services.operations_service.get_all_units",
                side_effect=AssertionError("CAD roster path must not run"),
            ) as all_units,
        ):
            operations = build_cloud_operations_snapshot(self.state())
            units = build_cloud_unit_snapshot(self.state())
        active_calls.assert_not_called()
        all_units.assert_not_called()
        self.assertEqual(operations["dashboard_stats"]["active_calls"], 1)
        self.assertEqual(units["roster_stats"]["total_units"], 1)

    def test_centralsquare_pascal_case_shape_maps_to_whitelisted_display_fields(self):
        calls = _normalize_calls(
            [
                {
                    "CFSNumber": "synthetic-cfs",
                    "IncidentCode": [
                        {
                            "IsPrimary": True,
                            "IncidentCode": {
                                "Code": "MED",
                                "Description": "Medical Call",
                            },
                        }
                    ],
                    "Priority": {"Level": 10},
                    "PrimaryResponseAgency": {"Abbreviation": "EMS"},
                    "Status": {"Description": "Active"},
                    "CommandLog": [
                        {"Timestamp": "2026-08-05T17:02:00Z", "Status": {"Description": "Assigned"}},
                        {
                            "Timestamp": "2026-08-05T17:08:00Z",
                            "Status": {"Description": "On Scene"},
                            "Text": "Arrived at scene",
                            "Unit": {"UnitNumber": "MED10"},
                            "Creator": {"FullDescription": "Dispatcher 1"},
                        },
                        {"Narrative": "Approved chronological note"},
                    ],
                    "CallDateTime": "2026-08-05T17:00:00Z",
                    "Beat": {"Description": "Beat 1"},
                    "Zone": {"Name": "North"},
                    "City": {"Name": "Logan"},
                    "Address": {"Street": "100 Synthetic Street", "City": "Logan"},
                    "Description": "must not pass",
                    "Unit": [
                        {
                            "UnitNumber": "MED10",
                            "UnitType": {"Description": "Ambulance"},
                            "Agency": {"Abbreviation": "EMS"},
                            "Status": {"Description": "Enroute"},
                        }
                    ],
                }
            ]
        )
        units = _normalize_units(
            _items(
                {
                    "Units": [
                        {
                            "UnitNumber": "MED10",
                            "Agency": {"Abbreviation": "EMS"},
                            "UnitType": {"Description": "Ambulance"},
                            "Status": {"Description": "Assigned"},
                            "Station": {"Description": "Station 1"},
                            "IncidentInformation": {"CFSNumber": "synthetic-cfs"},
                            "UnitDetails": "must not pass",
                        }
                    ]
                },
                ("Units", "units", "items"),
            )
        )
        self.assertEqual(
            dict(calls[0]),
            {
                "cfs_number": "synthetic-cfs",
                "incident_code": "MED",
                "incident_description": "Medical Call",
                "priority": "10",
                "agency": "EMS",
                "status": "On Scene",
                "call_datetime": "2026-08-05T17:00:00Z",
                "location_label": "100 Synthetic Street, Logan",
                "beat": "Beat 1",
                "zone": "North",
                "city": "Logan",
                "latitude": None,
                "longitude": None,
                "assigned_units": (
                    {
                        "unit_number": "MED10",
                        "unit_type": "Ambulance",
                        "agency": "EMS",
                        "status": "Enroute",
                    },
                ),
                "command_logs": (
                    {
                        "timestamp": "2026-08-05T17:02:00Z",
                        "unit_number": "",
                        "text": "",
                        "status": "Assigned",
                        "creator": "",
                    },
                    {
                        "timestamp": "2026-08-05T17:08:00Z",
                        "unit_number": "MED10",
                        "text": "Arrived at scene",
                        "status": "On Scene",
                        "creator": "Dispatcher 1",
                    },
                    {
                        "timestamp": "",
                        "unit_number": "",
                        "text": "Approved chronological note",
                        "status": "",
                        "creator": "",
                    },
                ),
            },
        )
        self.assertEqual(units[0]["unit_number"], "MED10")
        self.assertEqual(units[0]["assignment_cfs_number"], "synthetic-cfs")
        rendered = json.dumps({"calls": [dict(calls[0])], "units": [dict(units[0])]}).lower()
        self.assertNotIn("must not pass", rendered)

    def test_bridge_whitelists_display_fields_and_retains_approved_location(self):
        output = {
            "operations": build_cloud_operations_snapshot(self.state()),
            "units": build_cloud_unit_snapshot(self.state()),
        }
        rendered = json.dumps(output).lower()
        for forbidden in (
            "raw unit narrative",
            "forbidden-header",
            "forbidden-secret",
            "forbidden-token",
            "must not stringify",
            "authorization",
            "password",
            "token",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(output["operations"]["calls"][0]["location"], "100 Synthetic Street, Logan")
        self.assertEqual(output["operations"]["calls"][0]["units"], "SYN1")
        self.assertEqual(output["operations"]["calls"][0]["incident_description"], "Approved Medical Call")
        self.assertNotIn("beat", output["operations"]["calls"][0])
        self.assertNotIn("zone", output["operations"]["calls"][0])
        self.assertEqual(output["operations"]["calls"][0]["city"], "Logan")

    def test_cloud_detail_contains_only_normalized_read_only_fields(self):
        detail = build_cloud_call_detail(self.state(), "synthetic-call-1")
        self.assertEqual(
            set(detail),
            {
                "cfs_number",
                "incident_code",
                "priority",
                "agency",
                "status",
                "call_datetime",
                "incident_description",
                "location",
                "city",
                "units",
                "assigned_units",
                "command_logs",
                "latitude",
                "longitude",
            },
        )
        rendered = json.dumps(detail).lower()
        for forbidden in ("raw", "authorization", "password", "token"):
            self.assertNotIn(forbidden, rendered)
        self.assertIsNone(build_cloud_call_detail(self.state(), "missing-call"))

    def test_incident_card_and_cloud_detail_template_are_safe_and_navigable(self):
        repository = Path(__file__).parents[2]
        card = (repository / "templates" / "components" / "incident_card.html").read_text(encoding="utf-8")
        detail = (repository / "templates" / "call_detail_cloud.html").read_text(encoding="utf-8").lower()
        self.assertIn('href="/calls/{{ call.cfs_number }}"', card)
        self.assertIn("cloud-call-facts", card)
        self.assertIn("ASSIGNED UNITS", card)
        self.assertNotIn("call.beat", card)
        self.assertNotIn("call.zone", card)
        self.assertIn("call.city", card)
        self.assertIn("if cloud_presentation", card)
        self.assertNotIn('target="_blank"', card)
        self.assertIn("command log timeline", detail)
        self.assertIn("call.command_logs", detail)
        for forbidden in ("reporter", "raw payload"):
            self.assertNotIn(forbidden, detail)

    def test_cloud_command_log_is_chronological_and_detail_only(self):
        calls = _normalize_calls(
            [
                {
                    "CFSNumber": "ordered-1",
                    "CommandLog": [
                        {"Timestamp": "2026-08-05T17:08:00Z", "Text": "Second"},
                        {"Timestamp": "2026-08-05T17:02:00Z", "Text": "First"},
                        {"Text": "Untimestamped"},
                        {"RawPayload": "must not pass", "Authorization": "must not pass"},
                    ],
                }
            ]
        )
        state = CloudCadDisplayState(calls=calls)
        snapshot_call = build_cloud_operations_snapshot(state)["calls"][0]
        detail = build_cloud_call_detail(state, "ordered-1")

        self.assertNotIn("command_logs", snapshot_call)
        self.assertEqual(snapshot_call["command_log_count"], 4)
        self.assertEqual(
            [entry["text"] for entry in detail["command_logs"]],
            ["First", "Second", "Untimestamped", ""],
        )
        rendered = json.dumps(detail).lower()
        self.assertNotIn("rawpayload", rendered)
        self.assertNotIn("authorization", rendered)
        self.assertNotIn("must not pass", rendered)

    def test_cloud_coordinates_are_validated_then_available_for_the_map(self):
        # Coordinates now flow into the list snapshot to power the map view,
        # but they are pre-validated at the CAD ingestion boundary
        # (_address_coordinates) before build_cloud_operations_snapshot ever
        # sees them, and no template renders latitude/longitude as text.
        normalized = _normalize_calls(
            [
                {
                    "CFSNumber": "mapped",
                    "Address": {"Latitude": "37.8487", "Longitude": "-81.9935"},
                },
                {
                    "CFSNumber": "zero",
                    "Address": {"Latitude": 0, "Longitude": 0},
                },
                {
                    "CFSNumber": "infinite",
                    "Address": {"Latitude": "NaN", "Longitude": "Infinity"},
                },
                {
                    "CFSNumber": "range",
                    "Address": {"Latitude": 91, "Longitude": -181},
                },
            ]
        )
        state = CloudCadDisplayState(calls=normalized)
        snapshot = build_cloud_operations_snapshot(state)
        by_cfs = {call["cfs_number"]: call for call in snapshot["calls"]}
        self.assertEqual(by_cfs["mapped"]["latitude"], 37.8487)
        self.assertEqual(by_cfs["mapped"]["longitude"], -81.9935)
        for cfs_number in ("zero", "infinite", "range"):
            self.assertIsNone(by_cfs[cfs_number]["latitude"])
            self.assertIsNone(by_cfs[cfs_number]["longitude"])

        mapped = build_cloud_call_detail(state, "mapped")
        self.assertEqual(mapped["latitude"], 37.8487)
        self.assertEqual(mapped["longitude"], -81.9935)
        for cfs_number in ("zero", "infinite", "range"):
            detail = build_cloud_call_detail(state, cfs_number)
            self.assertIsNone(detail["latitude"])
            self.assertIsNone(detail["longitude"])

    def test_cloud_detail_map_renders_only_for_verified_coordinates(self):
        repository = Path(__file__).parents[2]
        environment = Environment(
            loader=FileSystemLoader(repository / "templates"),
            autoescape=select_autoescape(("html",)),
        )
        template = environment.get_template("call_detail_cloud.html")
        mapped = build_cloud_call_detail(self.state(), "synthetic-call-1")
        request = SimpleNamespace(url=SimpleNamespace(path="/calls/synthetic-call-1"))
        rendered_map = template.render(call=mapped, connected=True, error=None, request=request)
        rendered_missing = template.render(
            call={**mapped, "latitude": None, "longitude": None},
            connected=True,
            error=None,
            request=request,
        )

        self.assertIn('id="incident-map"', rendered_map)
        self.assertIn('id="incident-map-data"', rendered_map)
        self.assertIn("/static/vendor/leaflet/leaflet.js", rendered_map)
        self.assertNotIn("No verified GIS coordinates", rendered_map)
        self.assertNotIn('id="incident-map"', rendered_missing)
        self.assertNotIn('id="incident-map-data"', rendered_missing)
        self.assertNotIn("/static/vendor/leaflet/leaflet.js", rendered_missing)
        self.assertIn("No verified GIS coordinates", rendered_missing)

    def test_cloud_detail_matches_sanitized_command_layout_hierarchy(self):
        repository = Path(__file__).parents[2]
        source = (repository / "templates" / "call_detail_cloud.html").read_text(
            encoding="utf-8"
        )
        shared = (
            repository / "static" / "css" / "lcdash-command-center.css"
        ).read_text(encoding="utf-8")

        self.assertIn("READ-ONLY INCIDENT COMMAND VIEW", source)
        self.assertEqual(source.count('class="incident-kpi"'), 3)
        self.assertIn('id="incident-elapsed"', source)
        self.assertIn('class="col-xl-8"', source)
        self.assertIn('class="col-xl-4"', source)
        self.assertLess(source.index('id="incident-map"'), source.index("Incident Details"))
        self.assertIn("Assigned Units", source)
        self.assertIn("Command Log", source)
        self.assertIn("normalized, authorized fields", source)
        self.assertNotIn("call.reporter", source)
        self.assertNotIn("call.raw", source)
        self.assertIn("--command-touch-target: 44px", shared)
        self.assertIn("@media (max-width: 575.98px)", shared)

    def test_cloud_card_is_dense_and_excludes_on_prem_sensitive_fields(self):
        repository = Path(__file__).parents[2]
        environment = Environment(
            loader=FileSystemLoader(repository / "templates"),
            autoescape=select_autoescape(("html",)),
        )
        call = build_cloud_operations_snapshot(self.state())["calls"][0]
        cloud = environment.get_template("components/incident_card.html").render(
            call=call,
            cloud_presentation=True,
        )
        self.assertIn("Approved Medical Call", cloud)
        self.assertNotIn("Beat 1", cloud)
        self.assertNotIn("North", cloud)
        self.assertIn("Logan", cloud)
        self.assertIn("100 Synthetic Street", cloud)
        self.assertIn("SYN1", cloud)
        self.assertIn("On Scene", cloud)
        self.assertIn("CALL RECEIVED", cloud)
        self.assertNotIn("CALL TAKER", cloud)
        self.assertNotIn("raw unit narrative", cloud)

        legacy = environment.get_template("components/incident_card.html").render(
            call={
                **call,
                "location": "100 Main Street",
                "call_taker": "Dispatcher 1",
            },
            cloud_presentation=False,
        )
        self.assertIn("100 Main Street", legacy)
        self.assertIn("Dispatcher 1", legacy)
        self.assertIn("CALL TAKER", legacy)

    def test_detail_route_selects_cloud_snapshot_before_legacy_cad_detail(self):
        source = (Path(__file__).parents[2] / "app" / "main.py").read_text(encoding="utf-8")
        route_start = source.index('def call_detail(request: Request, cfs_number: str):')
        route_end = source.index('\n\n@app.get("/map")', route_start)
        route = source[route_start:route_end]
        self.assertLess(
            route.index("if cloud_normalized_detail:"),
            route.index("get_call_detail(cfs_number)"),
        )
        self.assertIn("build_cloud_call_detail(cloud_cad_runtime.state, cfs_number)", route)
        self.assertIn('"call_detail_cloud.html" if cloud_normalized_detail', route)

    def test_synthetic_disconnected_default_remains_network_free(self):
        with (
            patch("app.services.operations_service.settings.deployment_mode", "synthetic-disconnected"),
            patch(
                "app.services.operations_service.get_active_calls",
                side_effect=AssertionError("CAD must remain disconnected"),
            ) as active_calls,
        ):
            snapshot = get_live_operations_snapshot()
        active_calls.assert_not_called()
        self.assertEqual(snapshot["calls"], [])

    def test_main_handlers_select_bridge_without_constructing_a_connector(self):
        source = (Path(__file__).parents[2] / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def _current_operations_snapshot()", source)
        self.assertIn("def _current_unit_snapshot(", source)
        self.assertIn("build_cloud_operations_snapshot(cloud_cad_runtime.state)", source)
        self.assertIn("build_cloud_unit_snapshot(cloud_cad_runtime.state)", source)
        operations_start = source.index("def _current_operations_snapshot()")
        units_start = source.index("def _current_unit_snapshot(", operations_start)
        lifespan_start = source.index("\n\n@asynccontextmanager", units_start)
        operations_helper = source[operations_start:units_start]
        units_helper = source[units_start:lifespan_start]
        for helper, empty_builder in (
            (operations_helper, "build_empty_operations_snapshot()"),
            (units_helper, "build_empty_unit_snapshot()"),
        ):
            self.assertIn('settings.deployment_mode == "synthetic-disconnected"', helper)
            self.assertIn(empty_builder, helper)
            self.assertLess(helper.index(empty_builder), helper.index("get_live_"))
        bridge_source = inspect.getsource(build_cloud_operations_snapshot) + inspect.getsource(
            build_cloud_unit_snapshot
        )
        for forbidden in (
            "CloudCentralSquareReadConnector",
            "SecretsManager",
            "httpx",
            "update_call",
            "dispatch",
            "acknowledge",
            "send_alert",
            "trigger_tone",
        ):
            self.assertNotIn(forbidden, bridge_source)


if __name__ == "__main__":
    unittest.main()
