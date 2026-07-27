import argparse
import json
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.analytics_database import (
    AnalyticsDatabaseError,
    AnalyticsRepository,
)
from app.services.analytics_models import normalize_personnel_identity
from app.services.centralsquare import (
    CentralSquareAPIError,
    CentralSquareClient,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill stable dispatcher identifiers and personnel names "
            "from original CentralSquare CFS records."
        )
    )
    parser.add_argument("--max-calls", type=int, default=250)
    parser.add_argument("--delay-ms", type=int, default=100)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    max_calls = max(args.max_calls, 1)
    delay_seconds = max(args.delay_ms, 0) / 1000
    client = CentralSquareClient()
    updated = 0
    unresolved = 0
    failures = []

    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            candidates = repository.get_call_taker_backfill_candidates(
                limit=max_calls
            )

            for cfs_number in candidates:
                try:
                    raw_call = client.get_cfs_core(cfs_number)
                    unique_identifier, display_name = (
                        normalize_personnel_identity(
                            raw_call.get("CallTaker")
                        )
                    )
                    if not unique_identifier or not display_name:
                        unresolved += 1
                    elif repository.update_call_taker_identity(
                        cfs_number,
                        unique_identifier=unique_identifier,
                        display_name=display_name,
                    ):
                        updated += 1
                except CentralSquareAPIError:
                    failures.append(cfs_number)

                if delay_seconds:
                    time.sleep(delay_seconds)

            remaining = repository.count_call_taker_backfill_candidates()
    except (AnalyticsDatabaseError, CentralSquareAPIError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "examined": len(candidates),
                "updated": updated,
                "unresolved": unresolved,
                "failed": len(failures),
                "remaining": remaining,
                "failed_cfs_numbers": failures[:10],
            },
            indent=2,
        )
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
