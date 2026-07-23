import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.analytics_database import (  # noqa: E402
    AnalyticsDatabaseError,
    AnalyticsRepository,
    analytics_database_is_configured,
)


def main() -> int:
    if not analytics_database_is_configured():
        print("PostgreSQL analytics is not configured.")
        print("Set DATABASE_URL in .env, then run this command again.")
        return 1

    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            status = repository.status()
    except AnalyticsDatabaseError as exc:
        print(str(exc))
        return 1

    print("LCDash analytics schema is ready.")
    print(f"Calls stored: {status['calls_stored']}")
    print(f"Unit responses stored: {status['unit_responses_stored']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
