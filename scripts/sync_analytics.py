import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.analytics_collector import run_analytics_sync  # noqa: E402
from app.services.analytics_database import AnalyticsDatabaseError  # noqa: E402
from app.services.centralsquare import CentralSquareAPIError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect completed CentralSquare CAD analytics into PostgreSQL."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Initial lookback in hours when no prior successful sync exists.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="Maximum completed calls to process in this run.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_analytics_sync(
            lookback_hours=args.hours,
            max_calls=args.max_calls,
        )
    except (AnalyticsDatabaseError, CentralSquareAPIError) as exc:
        print(str(exc))
        return 1

    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
