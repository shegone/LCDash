from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.config.settings import settings


class NGA911ProviderError(RuntimeError):
    """Raised when the configured NGA911 intelligence provider is unavailable."""


class NGA911IntelligenceProvider(Protocol):
    provider_name: str

    def get_overview(self) -> dict:
        """Return the normalized NGA911 intelligence overview contract."""


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
