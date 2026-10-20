#!/usr/bin/env python3
"""Percentage of domestic-related calls, by month, from CentralSquare (CLI).

The dashboard's Reports page runs the same report through the pilot's own
connector. This command-line form exists for use from a workstation: it reads
the standard CentralSquare environment variables (token/CAD/system URLs,
username, password) set by the operator in their own shell, never a file.

READ ONLY. The only CAD request is ``POST /cfs_core/search`` in one-month
windows, via app/services/domestic_call_report_service.py. It prints aggregate
counts only: no call numbers, addresses, narratives, or caller/patient data.

    python -m scripts.domestic_call_percentage
    python -m scripts.domestic_call_percentage --from 2026-01-01 --to 2026-10-21
    python -m scripts.domestic_call_percentage --list-codes
    python -m scripts.domestic_call_percentage --match domestic --match "family"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services import domestic_call_report_service as service


def _parse_day(value: str, tz: ZoneInfo) -> datetime:
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=tz)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="start", help="First day, YYYY-MM-DD (default: January 1 of this year)")
    parser.add_argument("--to", dest="end", help="Day after the last day, YYYY-MM-DD (default: tomorrow)")
    parser.add_argument("--timezone", default=service.TIMEZONE_NAME)
    parser.add_argument(
        "--match", action="append", dest="terms",
        help="Text to look for in the incident code, abbreviation, or description "
             "(repeatable; default: domestic)",
    )
    parser.add_argument("--any-incident", action="store_true",
                        help="Count a call if ANY attached incident code matches, not just the primary")
    parser.add_argument("--list-codes", action="store_true",
                        help="Also print the most common incident codes that were NOT counted")
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON")
    return parser


def main(argv=None, search=None, now=None) -> int:
    args = build_parser().parse_args(argv)
    tz = ZoneInfo(args.timezone)
    now = now or datetime.now(tz)
    start = _parse_day(args.start, tz) if args.start else datetime(now.year, 1, 1, tzinfo=tz)
    end = (
        _parse_day(args.end, tz)
        if args.end
        else _parse_day((now + timedelta(days=1)).date().isoformat(), tz)
    )
    if end <= start:
        print("--to must be after --from", file=sys.stderr)
        return 2
    terms = tuple(args.terms) if args.terms else service.DEFAULT_TERMS

    if search is None:
        # Imported here so the helpers can be used and tested with no
        # credentials in the environment.
        from app.services.centralsquare import CentralSquareClient

        search = CentralSquareClient().search_cfs_core

    result = service.summarize(
        search, start, end, terms,
        any_incident=args.any_incident, now=now,
        progress=lambda u: u["done"] and print(f"  read {u['month']}", file=sys.stderr),
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"\nDomestic-related calls, {result['from']} through {result['through']} ({result['timezone']})")
    print(f"Counted by {result['counted_by']} containing: {', '.join(terms)}\n")
    print(f"{'Month':<10}{'Total':>9}{'Domestic':>10}{'Percent':>9}")
    for row in result["months"]:
        pct = "-" if row["percent_domestic"] is None else f"{row['percent_domestic']:.2f}%"
        note = "  (month in progress)" if row["in_progress"] else ""
        print(f"{row['month']:<10}{row['total_calls']:>9}{row['domestic_calls']:>10}{pct:>9}{note}")
    pct = "-" if result["percent_domestic"] is None else f"{result['percent_domestic']:.2f}%"
    print(f"{'TOTAL':<10}{result['total_calls']:>9}{result['domestic_calls']:>10}{pct:>9}\n")
    print("Incident codes counted as domestic:")
    for item in result["matched_codes"]:
        print(f"  {item['calls']:>7}  {item['code']}")
    if not result["matched_codes"]:
        print("  (none matched - check --match against --list-codes)")
    if args.list_codes:
        print(f"\nMost common codes NOT counted ({result['other_code_kinds']} kinds in all):")
        for item in result["other_codes"]:
            print(f"  {item['calls']:>7}  {item['code']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
