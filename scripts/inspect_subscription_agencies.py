#!/usr/bin/env python3
"""List distinct CentralSquare dispatch-agency objects without call details."""

from datetime import datetime, timedelta, timezone
import json

from app.integrations.cad.centralsquare import (
    CentralSquareCadAdapter as CentralSquareClient,
)


def _records(result: dict) -> list[dict]:
    values = (
        result.get("cfs_cores")
        or result.get("CFSCore")
        or result.get("CFSCoreReadMultiple")
        or []
    )
    return [value for value in values if isinstance(value, dict)]


def _agency_key(agency: dict) -> tuple:
    return (
        agency.get("UniqueIdentifier"),
        agency.get("Abbreviation"),
        agency.get("Name"),
    )


def main() -> None:
    client = CentralSquareClient()
    now = datetime.now(timezone.utc)
    searches = [
        {
            "CurrentlyActive": True,
            "OrderByField": "Created",
            "OrderByDirection": "Descending",
        },
        {
            "RecordClosedFrom": (now - timedelta(days=30)).isoformat(),
            "RecordClosedTo": now.isoformat(),
            "CurrentlyActive": False,
            "OrderByField": "Closed",
            "OrderByDirection": "Descending",
        },
    ]

    agencies = {}
    for search in searches:
        result = client.search_cfs_core(search, limit=100)
        for call in _records(result):
            agency = call.get("DispatchAgency")
            if isinstance(agency, dict):
                agencies[_agency_key(agency)] = {
                    key: agency.get(key)
                    for key in (
                        "UniqueIdentifier",
                        "Abbreviation",
                        "Name",
                        "ORI",
                        "RunsDispatch",
                        "DispatchedBy",
                        "PrimaryResponderType",
                    )
                }

    print(
        json.dumps(
            sorted(
                agencies.values(),
                key=lambda item: (
                    str(item.get("Abbreviation") or ""),
                    str(item.get("Name") or ""),
                ),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
