import time
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import settings
from app.services.centralsquare import CentralSquareAPIError
from app.services.ems_delay_alert_database import (
    EMSDelayAlertDatabaseError,
)
from app.services.ems_delay_alert_service import evaluate_ems_delay_alerts


def main() -> None:
    interval = max(settings.ems_delay_poll_seconds, 30)

    while True:
        started = time.monotonic()

        if not settings.ems_delay_alert_enabled:
            print(
                "EMS delayed-call alerting is disabled.",
                flush=True,
            )
        else:
            try:
                result = evaluate_ems_delay_alerts()
                print(
                    "EMS delayed-call evaluation finished: "
                    f"mode={result['status']} "
                    f"monitored={result['monitored_calls']} "
                    f"due={result['due_calls']} "
                    f"would_send={result['dry_run_notifications']}",
                    flush=True,
                )
            except (
                CentralSquareAPIError,
                EMSDelayAlertDatabaseError,
            ) as exc:
                print(
                    f"EMS delayed-call evaluation failed: {exc}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    "Unexpected EMS delayed-call worker failure: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

        elapsed = time.monotonic() - started
        time.sleep(max(5, interval - elapsed))


if __name__ == "__main__":
    main()
