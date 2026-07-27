import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.centralsquare import CentralSquareAPIError
from app.services.ems_delay_alert_service import send_ems_delay_unit_page


CONFIRMATION_PHRASE = "SEND-ONE-PAGE"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send exactly one LCDash EMS delay test page through "
            "CentralSquare."
        )
    )
    parser.add_argument("--cfs-number", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must be exactly {CONFIRMATION_PHRASE}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.confirm != CONFIRMATION_PHRASE:
        print(
            "No page sent. The confirmation phrase did not match.",
            file=sys.stderr,
        )
        return 2

    try:
        result = send_ems_delay_unit_page(
            cfs_number=args.cfs_number,
            unit_number=args.unit,
        )
    except (CentralSquareAPIError, ValueError) as exc:
        print(f"Test page failed: {exc}", file=sys.stderr)
        return 1

    print(
        "One CentralSquare test page was accepted: "
        f"CFS={result['cfs_number']} "
        f"unit={result['unit_number']} "
        f"run_command_id={result['run_command_unique_identifier']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
