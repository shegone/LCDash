import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock

import psycopg

from app.config.settings import settings
from app.services.analytics_database import analytics_database_is_configured


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "realtime_schema.sql"
DEDUPLICATION_CACHE_SIZE = 5000


class RealtimeDatabaseError(Exception):
    """Raised when realtime delivery metadata cannot be stored."""


class WebhookEventDeduplicator:
    def __init__(self, max_events: int = DEDUPLICATION_CACHE_SIZE):
        self.max_events = max_events
        self._events = OrderedDict()
        self._lock = Lock()

    def check_and_store(self, event_id: str) -> bool:
        with self._lock:
            if event_id in self._events:
                self._events.move_to_end(event_id)
                return True

            self._events[event_id] = None
            while len(self._events) > self.max_events:
                self._events.popitem(last=False)
            return False


class RealtimeEventBroker:
    def __init__(self):
        self._subscribers = set()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self):
        queue = asyncio.Queue(maxsize=20)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def publish(self, event: dict):
        async with self._lock:
            subscribers = tuple(self._subscribers)

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


class RealtimeRepository:
    def __init__(self, database_url: str | None = None):
        self.database_url = (
            database_url if database_url is not None else settings.database_url
        )

    def record_event(
        self,
        event_id: str,
        source: str,
        received_at: datetime,
        payload_size: int,
    ) -> bool:
        if not analytics_database_is_configured(self.database_url):
            raise RealtimeDatabaseError("Realtime database is not configured.")

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        try:
            with psycopg.connect(self.database_url, connect_timeout=10) as connection:
                connection.execute(schema_sql)
                inserted = connection.execute(
                    """
                    INSERT INTO lcdash_realtime.webhook_events (
                        event_id,
                        source,
                        received_at,
                        last_seen_at,
                        payload_size
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (
                        event_id,
                        source,
                        received_at,
                        received_at,
                        payload_size,
                    ),
                ).fetchone()

                if inserted:
                    return True

                connection.execute(
                    """
                    UPDATE lcdash_realtime.webhook_events
                    SET
                        last_seen_at = %s,
                        duplicate_count = duplicate_count + 1
                    WHERE event_id = %s
                    """,
                    (received_at, event_id),
                )
                return False
        except (OSError, psycopg.Error) as exc:
            raise RealtimeDatabaseError(
                "Realtime delivery metadata could not be stored."
            ) from exc

    def get_delivery_summary(self) -> list[tuple]:
        if not analytics_database_is_configured(self.database_url):
            raise RealtimeDatabaseError("Realtime database is not configured.")

        try:
            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
            with psycopg.connect(
                self.database_url,
                connect_timeout=10,
            ) as connection:
                connection.execute(schema_sql)
                return connection.execute(
                    """
                    SELECT
                        source,
                        COUNT(*) AS unique_events,
                        COALESCE(SUM(duplicate_count), 0) AS duplicates,
                        MAX(received_at) AS latest_unique_event,
                        MAX(last_seen_at) AS latest_delivery
                    FROM lcdash_realtime.webhook_events
                    GROUP BY source
                    ORDER BY source
                    """
                ).fetchall()
        except (OSError, psycopg.Error) as exc:
            raise RealtimeDatabaseError(
                "Realtime delivery metadata could not be read."
            ) from exc


event_broker = RealtimeEventBroker()
event_deduplicator = WebhookEventDeduplicator()


def canonical_webhook_payload(payload) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def webhook_event_id(source: str, payload) -> str:
    digest = hashlib.sha256()
    digest.update(source.encode("ascii"))
    digest.update(b"\0")
    digest.update(canonical_webhook_payload(payload))
    return digest.hexdigest()


def process_webhook_event(source: str, payload, payload_size: int) -> dict:
    event_id = webhook_event_id(source, payload)
    received_at = datetime.now(timezone.utc)

    if event_deduplicator.check_and_store(event_id):
        return {
            "accepted": True,
            "duplicate": True,
            "persisted": False,
            "event_id": event_id,
            "source": source,
            "received_at": received_at.isoformat(),
        }

    persisted = False
    duplicate = False
    if analytics_database_is_configured():
        try:
            inserted = RealtimeRepository().record_event(
                event_id=event_id,
                source=source,
                received_at=received_at,
                payload_size=payload_size,
            )
            persisted = True
            duplicate = not inserted
        except RealtimeDatabaseError:
            persisted = False

    return {
        "accepted": True,
        "duplicate": duplicate,
        "persisted": persisted,
        "event_id": event_id,
        "source": source,
        "received_at": received_at.isoformat(),
    }


def browser_event(event: dict) -> dict:
    return {
        "source": event["source"],
        "received_at": event["received_at"],
    }


def _empty_source_health(source: str, label: str) -> dict:
    return {
        "source": source,
        "label": label,
        "status": "awaiting",
        "status_label": "Awaiting first event",
        "delivery_observed": False,
        "unique_events": 0,
        "duplicate_deliveries": 0,
        "total_deliveries": 0,
        "latest_unique_event": "",
        "latest_delivery": "",
    }


def get_realtime_health(database_url: str | None = None) -> dict:
    receiver_configured = bool(settings.centralsquare_webhook_secret)
    database_configured = analytics_database_is_configured(database_url)
    sources = {
        "cfs": _empty_source_health("cfs", "Calls for Service"),
        "units": _empty_source_health("units", "Unit Updates"),
    }

    result = {
        "status": "ready",
        "status_label": "Ready",
        "receiver_configured": receiver_configured,
        "database_configured": database_configured,
        "database_available": False,
        "metadata_only": True,
        "reconciliation_poll_seconds": 30,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }

    if not receiver_configured or not database_configured:
        result["status"] = "degraded"
        result["status_label"] = "Configuration incomplete"

    if not database_configured:
        for source_health in sources.values():
            source_health["status"] = "unavailable"
            source_health["status_label"] = "Metadata unavailable"
        return result

    try:
        rows = RealtimeRepository(database_url).get_delivery_summary()
    except RealtimeDatabaseError:
        result["status"] = "degraded"
        result["status_label"] = "Metadata unavailable"
        for source_health in sources.values():
            source_health["status"] = "unavailable"
            source_health["status_label"] = "Metadata unavailable"
        return result

    result["database_available"] = True
    for (
        source,
        unique_events,
        duplicates,
        latest_unique_event,
        latest_delivery,
    ) in rows:
        if source not in sources:
            continue

        unique_count = int(unique_events or 0)
        duplicate_count = int(duplicates or 0)
        source_health = sources[source]
        source_health.update(
            {
                "status": "observed",
                "status_label": "Delivery observed",
                "delivery_observed": True,
                "unique_events": unique_count,
                "duplicate_deliveries": duplicate_count,
                "total_deliveries": unique_count + duplicate_count,
                "latest_unique_event": (
                    latest_unique_event.isoformat()
                    if latest_unique_event
                    else ""
                ),
                "latest_delivery": (
                    latest_delivery.isoformat()
                    if latest_delivery
                    else ""
                ),
            }
        )

    return result
