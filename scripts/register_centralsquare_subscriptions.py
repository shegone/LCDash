#!/usr/bin/env python3
"""Idempotently register LCDash's read-only CentralSquare webhook subscriptions."""

import argparse
import json
from urllib.parse import quote

from app.config.settings import settings
from app.services.centralsquare import CentralSquareClient


DEFAULT_PUBLIC_BASE_URL = "https://supervisor.logan911.com"


def _callback_url(public_base_url: str, source: str, secret: str) -> str:
    authority = public_base_url.removeprefix("https://").rstrip("/")
    username = quote("lcdash", safe="")
    password = quote(secret, safe="")
    return (
        f"https://{username}:{password}@{authority}"
        f"/api/integrations/centralsquare/webhooks/{source}"
    )


def _existing_subscription(
    client: CentralSquareClient,
    callback_url: str,
) -> int | None:
    result = client.post(
        f"{settings.system_base_url}/subscriptions/search",
        json={"CallbackURL": callback_url},
        params={"skip": 0, "limit": 100},
    )
    subscriptions = result.get("Subscriptions") or []
    for subscription in subscriptions:
        if (
            isinstance(subscription, dict)
            and subscription.get("CallbackURL") == callback_url
        ):
            value = subscription.get("WebhookUniqueIdentifier")
            if isinstance(value, int):
                return value
    return None


def _create_subscription(
    client: CentralSquareClient,
    endpoint: str,
    body: dict,
) -> int:
    response = client.post(
        f"{settings.cad_base_url}{endpoint}",
        json=body,
    )
    value = response.get("SubscriptionUniqueIdentifier")
    if not isinstance(value, int):
        raise RuntimeError("CentralSquare did not return a subscription identifier.")
    return value


def register_subscriptions(
    client: CentralSquareClient,
    public_base_url: str,
    secret: str,
    dispatch_agency: dict,
) -> dict:
    if not secret:
        raise RuntimeError("The CentralSquare webhook secret is not configured.")

    cfs_callback = _callback_url(public_base_url, "cfs", secret)
    unit_callback = _callback_url(public_base_url, "units", secret)

    cfs_id = _existing_subscription(client, cfs_callback)
    cfs_created = cfs_id is None
    if cfs_created:
        cfs_id = _create_subscription(
            client,
            "/cfs_core/subscription",
            {
                "CallbackURL": cfs_callback,
                "DispatchAgency": [dispatch_agency],
                "CurrentlyActive": True,
                "ExcludeHistoricalRecordUpdates": True,
            },
        )

    unit_id = _existing_subscription(client, unit_callback)
    unit_created = unit_id is None
    if unit_created:
        unit_id = _create_subscription(
            client,
            "/units/subscription",
            {
                "CallbackURL": unit_callback,
            },
        )

    return {
        "cfs_subscription_id": cfs_id,
        "cfs_created": cfs_created,
        "unit_subscription_id": unit_id,
        "unit_created": unit_created,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--public-base-url",
        default=DEFAULT_PUBLIC_BASE_URL,
    )
    parser.add_argument("--agency-id", type=int, required=True)
    parser.add_argument("--agency-abbreviation", required=True)
    parser.add_argument("--agency-name", required=True)
    parser.add_argument("--agency-ori", default="")
    args = parser.parse_args()

    result = register_subscriptions(
        client=CentralSquareClient(),
        public_base_url=args.public_base_url,
        secret=settings.centralsquare_webhook_secret,
        dispatch_agency={
            "UniqueIdentifier": args.agency_id,
            "Abbreviation": args.agency_abbreviation,
            "Name": args.agency_name,
            "ORI": args.agency_ori or None,
            "RunsDispatch": True,
            "DispatchedBy": "Dispatch",
            "PrimaryResponderType": "Dispatch Supervisor",
        },
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
