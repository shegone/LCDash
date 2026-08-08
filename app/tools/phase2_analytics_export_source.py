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


def resolve_database_url(environment: dict[str, str] | None = None) -> str:
    """Honour the deployment's ``*_FILE`` secret convention.

    The on-prem container sets ``DATABASE_URL_FILE`` pointing at a mounted
    Docker secret rather than exporting the URL, mirroring
    ``app.config.settings._env``. Reading only the plain variable would leave
    the export unable to find the database on the real server.
    """
    source = environment if environment is not None else os.environ
    direct = str(source.get("DATABASE_URL", "")).strip()
    if direct:
        return direct
    secret_path = str(source.get("DATABASE_URL_FILE", "")).strip()
    if secret_path:
        try:
            with open(secret_path, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""
    return ""


def _dict_cursor_connection(database_url: str):
    """Return a connection whose cursors yield mappings keyed by column name."""
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, autocommit=False, row_factory=dict_row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default="",
        help="Read-only analytics connection string "
        "(defaults to $DATABASE_URL or the file named by $DATABASE_URL_FILE).",
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

    database_url = args.database_url.strip() or resolve_database_url()
    if not database_url:
        print(
            "No database URL: set --database-url, DATABASE_URL, or DATABASE_URL_FILE.",
            file=sys.stderr,
        )
        return 2

    window_start = datetime.fromisoformat(args.window_start)
    window_end = (
        datetime.fromisoformat(args.window_end)
        if args.window_end
        else datetime.now(timezone.utc)
    )

    connection = _dict_cursor_connection(database_url)
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
