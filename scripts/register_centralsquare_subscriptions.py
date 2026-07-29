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


def _existing_subscription_record(
    client: CentralSquareClient,
    callback_url: str,
) -> dict | None:
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
            return subscription
    return None


def _existing_subscription(
    client: CentralSquareClient,
    callback_url: str,
) -> int | None:
    subscription = _existing_subscription_record(client, callback_url)
    if not subscription:
        return None
    value = subscription.get("WebhookUniqueIdentifier")
    return value if isinstance(value, int) else None


def _safe_subscription_summary(subscription: dict | None) -> dict | None:
    if not subscription:
        return None
    return {
        key: value
        for key, value in subscription.items()
        if key != "CallbackURL"
    }


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


def inspect_subscriptions(
    client: CentralSquareClient,
    public_base_url: str,
    secret: str,
) -> dict:
    if not secret:
        raise RuntimeError("The CentralSquare webhook secret is not configured.")

    cfs_record = _existing_subscription_record(
        client,
        _callback_url(public_base_url, "cfs", secret),
    )
    unit_record = _existing_subscription_record(
        client,
        _callback_url(public_base_url, "units", secret),
    )
    return {
        "cfs_subscription": _safe_subscription_summary(cfs_record),
        "unit_subscription": _safe_subscription_summary(unit_record),
    }


def register_unit_subscription(
    client: CentralSquareClient,
    public_base_url: str,
    secret: str,
) -> dict:
    if not secret:
        raise RuntimeError("The CentralSquare webhook secret is not configured.")

    unit_callback = _callback_url(public_base_url, "units", secret)
    unit_id = _existing_subscription(client, unit_callback)
    unit_created = unit_id is None
    if unit_created:
        unit_id = _create_subscription(
            client,
            "/units/subscription",
            {"CallbackURL": unit_callback},
        )

    return {
        "unit_subscription_id": unit_id,
        "unit_created": unit_created,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--public-base-url",
        default=DEFAULT_PUBLIC_BASE_URL,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect-only", action="store_true")
    mode.add_argument("--unit-only", action="store_true")
    parser.add_argument("--agency-id", type=int)
    parser.add_argument("--agency-abbreviation")
    parser.add_argument("--agency-name")
    parser.add_argument("--agency-ori", default="")
    args = parser.parse_args()

    client = CentralSquareClient()
    if args.inspect_only:
        result = inspect_subscriptions(
            client,
            args.public_base_url,
            settings.centralsquare_webhook_secret,
        )
    elif args.unit_only:
        result = register_unit_subscription(
            client,
            args.public_base_url,
            settings.centralsquare_webhook_secret,
        )
    else:
        missing = [
            name
            for name, value in (
                ("--agency-id", args.agency_id),
                ("--agency-abbreviation", args.agency_abbreviation),
                ("--agency-name", args.agency_name),
            )
            if value in (None, "")
        ]
        if missing:
            parser.error(
                "full registration requires " + ", ".join(missing)
            )
        result = register_subscriptions(
            client=client,
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
