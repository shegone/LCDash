#!/usr/bin/env python3
"""Create LCDash's webhook secret and update the protected credential record."""

import argparse
import os
from pathlib import Path
import secrets


DEFAULT_SECRET_PATH = Path(
    "/home/ted/lcdash-platform/secrets/centralsquare_webhook_secret"
)
DEFAULT_RECORD_PATH = Path(
    "/home/ted/lcdash-platform/secrets/platform-credentials.txt"
)
RECORD_LABEL = "CentralSquare webhook secret: "


def ensure_realtime_secret(secret_path: Path, record_path: Path) -> None:
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    existing_secret = ""
    if secret_path.exists():
        existing_secret = secret_path.read_text(encoding="utf-8").strip()

    secret = existing_secret or secrets.token_hex(32)
    secret_path.write_text(f"{secret}\n", encoding="utf-8")
    os.chmod(secret_path, 0o600)

    existing_record = ""
    if record_path.exists():
        existing_record = record_path.read_text(encoding="utf-8")

    retained_lines = [
        line
        for line in existing_record.splitlines()
        if not line.startswith(RECORD_LABEL)
    ]
    retained_lines.append(f"{RECORD_LABEL}{secret}")
    record_path.write_text(
        "\n".join(retained_lines) + "\n",
        encoding="utf-8",
    )
    os.chmod(record_path, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--secret-path",
        type=Path,
        default=DEFAULT_SECRET_PATH,
    )
    parser.add_argument(
        "--record-path",
        type=Path,
        default=DEFAULT_RECORD_PATH,
    )
    args = parser.parse_args()

    ensure_realtime_secret(args.secret_path, args.record_path)
    print("Webhook secret and protected credential record are ready.")


if __name__ == "__main__":
    main()
