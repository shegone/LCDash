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
