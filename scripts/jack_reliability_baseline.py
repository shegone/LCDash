from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _json_request(url: str, payload: dict | None = None, timeout: int = 135) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the complete JACK manual-grounded reliability baseline."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8010",
        help="LCDash base URL",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report path",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    started_at = datetime.now(timezone.utc)
    output_path = Path(args.output) if args.output else Path(
        f"jack-baseline-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )

    try:
        catalog = _json_request(f"{base_url}/api/mindshare/evaluations")
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"Could not load the JACK evaluation catalog: {exc}", file=sys.stderr)
        return 1

    cases = catalog.get("cases") or []
    results = []
    print(f"Running {len(cases)} JACK tests in sequence.")
    for index, case in enumerate(cases, start=1):
        case_id = case.get("case_id") or ""
        question = case.get("question") or ""
        print(f"[{index:02d}/{len(cases):02d}] {case_id}: {question}", flush=True)
        try:
            result = _json_request(
                f"{base_url}/api/mindshare/evaluations/run",
                {"case_id": case_id},
            )
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            result = {
                "case_id": case_id,
                "question": question,
                "passed": False,
                "error": str(exc),
            }
        results.append(result)
        duration = float(result.get("duration_ms") or 0) / 1000
        print(
            f"    {'PASS' if result.get('passed') else 'REVIEW'} "
            f"({duration:.1f}s)",
            flush=True,
        )

    passed = sum(1 for result in results if result.get("passed"))
    report = {
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "total": len(results),
        "passed": passed,
        "review": len(results) - passed,
        "pass_rate": round((passed / len(results)) * 100, 1) if results else 0,
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"Completed: {passed}/{len(results)} passed. Report: {output_path}",
        flush=True,
    )
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
