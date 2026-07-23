import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.analytics_collector import run_analytics_sync
from app.services.analytics_database import AnalyticsDatabaseError
from app.services.centralsquare import CentralSquareAPIError


def _interval_seconds() -> int:
    try:
        return max(60, int(os.getenv("ANALYTICS_SYNC_INTERVAL_SECONDS", "300")))
    except ValueError:
        return 300


def main() -> None:
    interval = _interval_seconds()
    while True:
        started = time.monotonic()
        try:
            result = run_analytics_sync()
            print(
                "Analytics synchronization finished: "
                f"status={result.get('status')} "
                f"calls={result.get('calls_processed', 0)}",
                flush=True,
            )
        except (AnalyticsDatabaseError, CentralSquareAPIError) as exc:
            print(f"Analytics synchronization failed: {exc}", flush=True)
        except Exception as exc:
            print(
                f"Unexpected analytics worker failure: {type(exc).__name__}: {exc}",
                flush=True,
            )

        elapsed = time.monotonic() - started
        time.sleep(max(5, interval - elapsed))


if __name__ == "__main__":
    main()
