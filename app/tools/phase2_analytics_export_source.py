"""Stage 1 of the analytics backfill: read on-prem, emit the bundle to stdout.

Runs INSIDE the on-prem application container, which is the only place that can
reach the analytics database (Postgres is published on a private Docker network
with no host port). Dependencies are deliberately minimal -- psycopg, the
stdlib, and the pure-stdlib table plans -- so nothing AWS-related is needed on
the production server.

The bundle is written to stdout as canonical JSON so it can be piped straight
into stage 2 (``phase2_analytics_export_cli``) for encryption and upload. That
pipe is the whole point: protected call data goes from the database into the
encryptor without ever touching disk, a log, or an operator's terminal. Only
counts and checksums are written to stderr.

Usage (from an operator workstation, one pipeline, never split):

    ssh <server> "docker exec -i lcdash-web python /tmp/lcdash-export/stage1.py" \\
        | python -m app.tools.phase2_analytics_export_cli --object-key ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from app.tools.phase2_analytics_contract import canonical
from app.tools.phase2_analytics_export import build_bundle

# A backfill takes everything; source_collected_at is bounded by these.
DEFAULT_WINDOW_START = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _connect(database_url: str):
    import psycopg  # imported here so --help works without the driver

    return psycopg.connect(database_url, autocommit=False)


def _dict_cursor_connection(database_url: str):
    """Return a connection whose cursors yield mappings keyed by column name."""
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, autocommit=False, row_factory=dict_row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Read-only analytics connection string (defaults to $DATABASE_URL).",
    )
    parser.add_argument(
        "--window-start",
        default=DEFAULT_WINDOW_START.isoformat(),
        help="ISO-8601 lower bound on source_collected_at (default: all history).",
    )
    parser.add_argument(
        "--window-end",
        default="",
        help="ISO-8601 upper bound on source_collected_at (default: now).",
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        print("A database URL is required.", file=sys.stderr)
        return 2

    window_start = datetime.fromisoformat(args.window_start)
    window_end = (
        datetime.fromisoformat(args.window_end)
        if args.window_end
        else datetime.now(timezone.utc)
    )

    connection = _dict_cursor_connection(args.database_url)
    try:
        bundle = build_bundle(
            connection, window_start=window_start, window_end=window_end
        )
    finally:
        connection.close()

    payload = canonical(bundle)
    # Counts only -- never row content -- so an operator can eyeball the export
    # without protected data reaching a terminal or a log.
    evidence = {
        name: entry["row_count"] for name, entry in bundle["manifest"].items()
    }
    print(
        json.dumps({"stage": "source", "table_counts": evidence}, sort_keys=True),
        file=sys.stderr,
    )
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
