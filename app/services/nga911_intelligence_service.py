from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.config.settings import settings


class NGA911ProviderError(RuntimeError):
    """Raised when the configured NGA911 intelligence provider is unavailable."""


class NGA911IntelligenceProvider(Protocol):
    provider_name: str

    def get_overview(self) -> dict:
        """Return the normalized NGA911 intelligence overview contract."""

    def get_county_detail(self, county_id: str) -> dict | None:
        """Return one isolated county intelligence contract when available."""

    def get_logan_operations(self, days: int = 14) -> dict:
        """Return the normalized director operations contract."""


def _timestamp(now: datetime, minutes_ago: int = 0) -> str:
    return (now - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


class MockNGA911IntelligenceProvider:
    provider_name = "NGA911 GovCloud Simulation"

    def get_overview(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "schema_version": "nga911-intelligence.v1",
            "generated_at": _timestamp(now),
            "provider": self.provider_name,
            "provider_mode": "mock",
            "synthetic_data": True,
            "environment_label": "DEMONSTRATION - SYNTHETIC DATA",
            "connection": {
                "status": "simulated",
                "status_label": "GOVCLOUD SIMULATION ACTIVE",
                "region": "AWS GovCloud (US) - conceptual",
                "last_sync": _timestamp(now, 1),
                "api_latency_ms": 42,
            },
            "summary": {
                "participating_counties": 8,
                "psaps_online": 12,
                "call_sessions_24h": 1842,
                "active_service_events": 3,
                "network_availability_percent": 99.997,
                "location_confidence_percent": 97.4,
            },
            "counties": [
                {
                    "id": "demo-logan",
                    "name": "Logan County Demonstration",
                    "status": "operational",
                    "psaps": 1,
                    "sessions_24h": 126,
                    "availability_percent": 100.0,
                    "location_confidence_percent": 98.7,
                    "open_events": 0,
                },
                {
                    "id": "demo-mountain",
                    "name": "Mountain Region Demonstration",
                    "status": "monitoring",
                    "psaps": 4,
                    "sessions_24h": 684,
                    "availability_percent": 99.991,
                    "location_confidence_percent": 96.8,
                    "open_events": 2,
                },
                {
                    "id": "demo-river",
                    "name": "River Region Demonstration",
                    "status": "operational",
                    "psaps": 3,
                    "sessions_24h": 511,
                    "availability_percent": 99.999,
                    "location_confidence_percent": 97.9,
                    "open_events": 0,
                },
                {
                    "id": "demo-valley",
                    "name": "Valley Region Demonstration",
                    "status": "advisory",
                    "psaps": 4,
                    "sessions_24h": 521,
                    "availability_percent": 99.984,
                    "location_confidence_percent": 95.6,
                    "open_events": 1,
                },
            ],
            "intelligence": [
                {
                    "id": "intel-location-drift",
                    "severity": "advisory",
                    "category": "Location Intelligence",
                    "title": "Wireless location confidence drift detected",
                    "summary": "A synthetic regional pattern shows a 2.8% decrease in high-confidence device location during the last 30 minutes.",
                    "recommendation": "Review the simulated carrier and location-source breakdown before escalating.",
                    "confidence_percent": 91,
                    "evidence_count": 38,
                    "detected_at": _timestamp(now, 7),
                },
                {
                    "id": "intel-route-resilience",
                    "severity": "positive",
                    "category": "Call Path Resilience",
                    "title": "Alternate delivery path validated",
                    "summary": "The demonstration model observed successful synthetic routing through the designated alternate path without session loss.",
                    "recommendation": "Retain the result as evidence for the next resilience exercise.",
                    "confidence_percent": 99,
                    "evidence_count": 12,
                    "detected_at": _timestamp(now, 18),
                },
                {
                    "id": "intel-volume-forecast",
                    "severity": "information",
                    "category": "Operational Forecast",
                    "title": "Elevated regional session volume projected",
                    "summary": "Synthetic trend analysis projects a 19% increase in incoming sessions during the next two hours.",
                    "recommendation": "Use the projection as a planning cue; supervisors remain responsible for staffing decisions.",
                    "confidence_percent": 84,
                    "evidence_count": 144,
                    "detected_at": _timestamp(now, 24),
                },
            ],
            "service_events": [
                {
                    "id": "evt-demo-1003",
                    "severity": "advisory",
                    "service": "Location Services",
                    "scope": "Mountain Region Demonstration",
                    "status": "investigating",
                    "description": "Synthetic confidence degradation affecting a subset of wireless sessions.",
                    "opened_at": _timestamp(now, 21),
                    "updated_at": _timestamp(now, 6),
                },
                {
                    "id": "evt-demo-1002",
                    "severity": "information",
                    "service": "Text-to-911",
                    "scope": "Valley Region Demonstration",
                    "status": "monitoring",
                    "description": "Demonstration queue latency briefly exceeded the presentation threshold.",
                    "opened_at": _timestamp(now, 54),
                    "updated_at": _timestamp(now, 12),
                },
                {
                    "id": "evt-demo-1001",
                    "severity": "positive",
                    "service": "ESInet Call Delivery",
                    "scope": "Multi-region demonstration",
                    "status": "resolved",
                    "description": "Synthetic alternate-route validation completed successfully.",
                    "opened_at": _timestamp(now, 96),
                    "updated_at": _timestamp(now, 38),
                },
            ],
            "capabilities": [
                {
                    "id": "call-path",
                    "name": "Call Path Intelligence",
                    "description": "Correlate delivery, routing, carrier, transfer, and alternate-path signals.",
                    "readiness": "prototype",
                },
                {
                    "id": "location-quality",
                    "name": "Location Quality",
                    "description": "Monitor source, confidence, freshness, and regional location trends.",
                    "readiness": "prototype",
                },
                {
                    "id": "service-health",
                    "name": "Service Health",
                    "description": "Summarize ESInet and NGCS service events without exposing call content.",
                    "readiness": "prototype",
                },
                {
                    "id": "multi-county",
                    "name": "Multi-county Operations",
                    "description": "Provide isolated county views with an authorized regional roll-up.",
                    "readiness": "concept",
                },
            ],
        }

    def get_county_detail(self, county_id: str) -> dict | None:
        now = datetime.now(timezone.utc)
        overview = self.get_overview()
        county = next((item for item in overview["counties"] if item["id"] == county_id), None)
        if county is None:
            return None

        profiles = {
            "demo-logan": ("Logan Primary PSAP", "Charleston NGCS", 18, 99.1),
            "demo-mountain": ("Mountain Regional PSAP", "Morgantown NGCS", 31, 95.8),
            "demo-river": ("River Regional PSAP", "Charleston NGCS", 24, 98.3),
            "demo-valley": ("Valley Regional PSAP", "Morgantown NGCS", 37, 94.9),
        }
        psap_name, ngcs, latency, device_confidence = profiles[county_id]
        sessions = county["sessions_24h"]
        return {
            "schema_version": "nga911-county-intelligence.v1",
            "generated_at": _timestamp(now),
            "provider": self.provider_name,
            "provider_mode": "mock",
            "synthetic_data": True,
            "environment_label": "DEMONSTRATION - SYNTHETIC DATA",
            "county": county,
            "summary": {
                "sessions_24h": sessions,
                "availability_percent": county["availability_percent"],
                "location_confidence_percent": county["location_confidence_percent"],
                "median_delivery_ms": latency + 71,
                "alternate_path_ready": True,
            },
            "psaps": [
                {
                    "id": f"{county_id}-primary",
                    "name": psap_name,
                    "status": "online" if county["status"] == "operational" else "monitoring",
                    "sessions_24h": sessions,
                    "ngcs": ngcs,
                    "median_latency_ms": latency + 71,
                    "last_heartbeat": _timestamp(now, 1),
                }
            ],
            "call_paths": [
                {"name": "Primary ESInet ingress", "role": "primary", "status": "healthy", "latency_ms": latency, "last_validated": _timestamp(now, 4)},
                {"name": "Alternate regional ingress", "role": "alternate", "status": "ready", "latency_ms": latency + 16, "last_validated": _timestamp(now, 18)},
                {"name": "NGCS policy route", "role": "routing", "status": "healthy", "latency_ms": 12, "last_validated": _timestamp(now, 3)},
            ],
            "location_quality": [
                {"source": "Device-based hybrid", "share_percent": 61.4, "confidence_percent": device_confidence, "freshness_seconds": 7},
                {"source": "Network-derived", "share_percent": 28.2, "confidence_percent": county["location_confidence_percent"] - 2.1, "freshness_seconds": 13},
                {"source": "Civic / registered", "share_percent": 10.4, "confidence_percent": 99.4, "freshness_seconds": 4},
            ],
            "session_trend": [
                {"hour": f"{hour:02d}:00", "sessions": max(2, round(sessions / 24 + ((hour % 6) - 2) * 2))}
                for hour in range(24)
            ],
            "intelligence": [item for item in overview["intelligence"] if (
                county_id != "demo-logan" or item["severity"] in {"positive", "information"}
            )][:2],
            "service_events": [item for item in overview["service_events"] if (
                county["name"] in item["scope"] or "Multi-region" in item["scope"]
            )],
            "guardrail": "Advisory intelligence only. Human authorization is required for every operational action.",
        }

    def get_logan_operations(self, days: int = 14) -> dict:
        now = datetime.now(timezone.utc)
        days = max(1, min(days, 14))
        paths = [
            {"id": "verizon-fiber", "name": "Verizon Fiber", "technology": "Fiber", "status": "healthy", "latency_ms": 18, "jitter_ms": 2.1, "packet_loss_percent": 0.02, "availability_percent": 99.999, "last_change": _timestamp(now, 340)},
            {"id": "optimum-fiber", "name": "Optimum Fiber", "technology": "Fiber", "status": "healthy", "latency_ms": 23, "jitter_ms": 3.4, "packet_loss_percent": 0.04, "availability_percent": 99.997, "last_change": _timestamp(now, 510)},
            {"id": "firstnet-cradlepoint", "name": "FirstNet Cradlepoint", "technology": "LTE", "status": "healthy", "latency_ms": 51, "jitter_ms": 8.8, "packet_loss_percent": 0.18, "availability_percent": 99.982, "last_change": _timestamp(now, 74)},
            {"id": "verizon-cradlepoint", "name": "Verizon Cradlepoint", "technology": "LTE", "status": "degraded", "latency_ms": 94, "jitter_ms": 31.7, "packet_loss_percent": 1.42, "availability_percent": 99.941, "last_change": _timestamp(now, 9)},
            {"id": "starlink", "name": "Starlink", "technology": "LEO Satellite", "status": "healthy", "latency_ms": 68, "jitter_ms": 12.5, "packet_loss_percent": 0.31, "availability_percent": 99.963, "last_change": _timestamp(now, 132)},
        ]
        consoles = [
            {"id": f"position-{number}", "name": f"Position {number}", "status": status, "dispatcher": dispatcher, "session_started": _timestamp(now, minutes), "calls_answered": calls, "average_answer_seconds": answer, "active_call_seconds": active, "barge_count": barge, "whisper_count": whisper, "observe_count": observe}
            for number, status, dispatcher, minutes, calls, answer, active, barge, whisper, observe in [
                (1, "active_call", "A. Bryant", 287, 34, 7.8, 184, 0, 1, 2),
                (2, "ready", "M. Ellis", 252, 29, 8.4, 0, 0, 0, 1),
                (3, "ringing", "J. Carter", 198, 22, 6.9, 0, 1, 0, 3),
                (4, "ready", "S. Hall", 176, 18, 9.1, 0, 0, 1, 0),
                (5, "signed_out", None, 0, 0, 0, 0, 0, 0, 0),
                (6, "ready", "T. Morgan", 91, 11, 7.2, 0, 0, 0, 1),
            ]
        ]
        event_specs = [
            ("evt-logan-2401", "warning", "verizon-cradlepoint", "Elevated jitter on Verizon LTE path", 9, 0, "Calls remain protected by four alternate paths."),
            ("evt-logan-2398", "critical", "starlink", "Starlink path temporarily unavailable", 1260, 11, "The path recovered automatically; no call delivery loss was observed."),
            ("evt-logan-2393", "warning", "firstnet-cradlepoint", "FirstNet packet loss exceeded baseline", 2780, 17, "Traffic continued over the active fiber paths."),
            ("evt-logan-2387", "critical", "optimum-fiber", "Optimum fiber heartbeat lost", 4870, 6, "The Verizon fiber path remained healthy during the interruption."),
            ("evt-logan-2379", "warning", "starlink", "Satellite latency above normal range", 7220, 23, "No user-visible call impact was detected."),
            ("evt-logan-2368", "information", "verizon-fiber", "Scheduled path validation completed", 11320, 4, "Primary and alternate delivery checks completed successfully."),
            ("evt-logan-2359", "warning", "verizon-cradlepoint", "LTE signal quality degraded", 16240, 31, "The path remained available at reduced performance."),
            ("evt-logan-2344", "critical", "firstnet-cradlepoint", "FirstNet tunnel re-established", 19110, 8, "Automatic recovery completed and alternate paths remained available."),
        ]
        path_names = {path["id"]: path["name"] for path in paths}
        events = []
        for event_id, severity, path_id, title, minutes_ago, duration, impact in event_specs:
            if minutes_ago > days * 1440:
                continue
            events.append({
                "id": event_id, "severity": severity, "path_id": path_id,
                "path_name": path_names[path_id], "title": title,
                "status": "active" if event_id == "evt-logan-2401" else "resolved",
                "opened_at": _timestamp(now, minutes_ago),
                "resolved_at": None if event_id == "evt-logan-2401" else _timestamp(now, minutes_ago - duration),
                "duration_minutes": duration, "plain_language_impact": impact,
                "calls_affected": 0, "automatic_failover": severity == "critical",
                "metrics": {"latency_ms": 94 if path_id == "verizon-cradlepoint" else 72, "jitter_ms": 31.7 if severity == "warning" else 18.2, "packet_loss_percent": 1.42 if severity != "information" else 0.03},
                "timeline": [
                    {"at": _timestamp(now, minutes_ago), "label": "Condition detected"},
                    {"at": _timestamp(now, max(0, minutes_ago - 2)), "label": "Alternate-path protection verified"},
                    {"at": _timestamp(now, max(0, minutes_ago - duration)), "label": "Recovered and validated" if duration else "Supervisor review pending"},
                ],
            })
        daily_history = [
            {"date": (now - timedelta(days=offset)).date().isoformat(), "availability_percent": round(99.94 + ((offset * 7) % 6) / 100, 3), "events": 1 + (offset % 3), "sessions": 118 + ((offset * 17) % 42)}
            for offset in range(days - 1, -1, -1)
        ]
        return {
            "schema_version": "nga911-director-operations.v1", "generated_at": _timestamp(now),
            "provider": self.provider_name, "provider_mode": "mock", "synthetic_data": True,
            "environment_label": "DEMONSTRATION - SYNTHETIC DATA", "history_days": days,
            "core": {"name": "NGA ESInet / NEXiSCore", "status": "operational", "region": "Cloud-native active-active simulation"},
            "center": {"name": "Logan County 911", "status": "protected", "healthy_paths": 4, "total_paths": 5},
            "paths": paths, "consoles": consoles, "events": events, "daily_history": daily_history,
            "alert_policy": {"audible_enabled_by_user": False, "warning": "Yellow: impaired path", "critical": "Red: path unavailable", "unknown": "Gray: monitoring unavailable"},
        }

    def get_logan_event(self, event_id: str) -> dict | None:
        return next((event for event in self.get_logan_operations(14)["events"] if event["id"] == event_id), None)


def get_nga911_provider() -> NGA911IntelligenceProvider:
    provider_mode = settings.nga911_provider_mode.strip().lower()
    if provider_mode == "mock":
        return MockNGA911IntelligenceProvider()
    raise NGA911ProviderError(
        "The NGA911 GovCloud provider is not configured. Use mock mode until "
        "NGA911 supplies the approved API and authentication details."
    )


def get_nga911_intelligence_overview() -> dict:
    return get_nga911_provider().get_overview()


def get_nga911_counties() -> list[dict]:
    return get_nga911_intelligence_overview()["counties"]


def get_nga911_county_detail(county_id: str) -> dict | None:
    return get_nga911_provider().get_county_detail(county_id)


def get_nga911_logan_operations(days: int = 14) -> dict:
    return get_nga911_provider().get_logan_operations(days)


def get_nga911_logan_event(event_id: str) -> dict | None:
    provider = get_nga911_provider()
    return provider.get_logan_event(event_id) if hasattr(provider, "get_logan_event") else None
