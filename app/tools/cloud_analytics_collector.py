"""Scheduled cloud entrypoint for incremental analytics collection.

Phase 1 backfilled cloud analytics from an authorized on-prem export. This is
Phase 2: a periodic task that keeps that data fresh by collecting newly closed
calls straight from CentralSquare, so the cloud warehouse stops being a
point-in-time snapshot.

The collection logic itself is unchanged -- ``run_analytics_sync`` is the same
function on-prem runs, including its watermark, overlap window, and idempotent
upserts keyed on CFS number. Only the CAD transport differs: on-prem uses
``CentralSquareClient`` directly, while here a thin adapter satisfies the same
two-method interface using the reviewed cloud read connector (Secrets Manager
credentials, allowlisted read operations, documented endpoints).

Read-only by construction: the adapter exposes exactly the two GET/search
operations the collector needs and nothing else. It cannot reach a command,
dispatch, or update route -- those have no method here and are absent from the
connector's allowlist.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping

from app.config.settings import settings
from app.integrations.cad.cloud_read_runtime import build_cloud_cad_connector
from app.services.analytics_collector import run_analytics_sync
from app.services.analytics_database import AnalyticsRepository


class CollectorReadOnlyCadClient:
    """Adapts the cloud read connector to the collector's client interface.

    ``run_analytics_sync`` duck-types its ``client`` and calls exactly two
    methods. Implementing only those keeps the write surface structurally
    empty rather than relying on the collector to behave.
    """

    def __init__(self, connector: Any) -> None:
        self._connector = connector

    def search_cfs_core(
        self, body: Mapping[str, Any], *, skip: int = 0, limit: int = 100
    ) -> Any:
        return self._connector.search_calls(body, skip=skip, limit=limit)

    def get_cfs_analytics(self, cfs_number: str) -> Any:
        return self._connector.get_cfs_analytics(cfs_number)


def collect(*, lookback_hours: int | None = None) -> dict:
    """Run one incremental collection cycle and return its summary counts."""
    connector = build_cloud_cad_connector(settings)
    if connector is None:
        raise RuntimeError(
            "Cloud CAD read access is disabled; refusing to run the collector."
        )
    client = CollectorReadOnlyCadClient(connector)
    with AnalyticsRepository() as repository:
        repository.initialize_schema()
        return run_analytics_sync(
            repository=repository,
            client=client,
            lookback_hours=lookback_hours,
        )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    lookback_hours: int | None = None
    if "--lookback-hours" in arguments:
        lookback_hours = int(arguments[arguments.index("--lookback-hours") + 1])
    elif os.environ.get("ANALYTICS_LOOKBACK_HOURS", "").strip():
        lookback_hours = int(os.environ["ANALYTICS_LOOKBACK_HOURS"])

    try:
        summary = collect(lookback_hours=lookback_hours)
    except Exception as error:
        # Counts and error type only -- never call content in a task log.
        print(
            json.dumps({"status": "failed", "error": type(error).__name__}),
            file=sys.stderr,
        )
        raise

    print(json.dumps({"status": "complete", **summary}, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
