"""Read-only health report for the Logan County AWS pilot.

One command answers "is :NN still the revision that's live, and is it
healthy?" without touching the console: ECS rollout state, the exact image
digest each running task pulled, ALB target health, recent error lines from
the web log group, and month-to-date spend.

Requires only the observer credentials described in
docs/planning/SESSION_OBSERVER_ACCESS_2026-08-18.md
(infrastructure/iam/LCDashObserverReadOnlyPolicy.json). Every call this
script makes is a read; it has no deploy or mutation path.

Usage:
    python infrastructure/tools/pilot_status.py [--json] [--log-minutes 60]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover - environment guard, not logic
    sys.exit("boto3 is required: pip install boto3")


EXPECTED_ACCOUNT = "862772137583"
REGION = "us-east-1"
NAME_PREFIX = "lcdash-p1-logan-use1"
CLUSTER = f"{NAME_PREFIX}-cluster"
SERVICES = (f"{NAME_PREFIX}-web", f"{NAME_PREFIX}-analytics-collector")
WEB_LOG_GROUP = f"/lcdash/{NAME_PREFIX}/web"
ERROR_FILTER = "?ERROR ?CRITICAL ?Traceback"


def check_identity(session) -> dict:
    identity = session.client("sts").get_caller_identity()
    account = identity["Account"]
    return {
        "account": account,
        "arn": identity["Arn"],
        "expected_account": account == EXPECTED_ACCOUNT,
    }


def describe_service(ecs, service_name: str) -> dict:
    response = ecs.describe_services(cluster=CLUSTER, services=[service_name])
    services = response.get("services", [])
    if not services:
        return {"service": service_name, "found": False}
    service = services[0]

    task_def_arn = service["taskDefinition"]
    task_def = ecs.describe_task_definition(taskDefinition=task_def_arn)[
        "taskDefinition"
    ]
    containers = [
        {"name": c["name"], "image": c["image"]}
        for c in task_def["containerDefinitions"]
    ]

    deployments = [
        {
            "status": d["status"],
            "rolloutState": d.get("rolloutState"),
            "taskDefinition": d["taskDefinition"].rsplit("/", 1)[-1],
            "desired": d["desiredCount"],
            "running": d["runningCount"],
            "failed": d.get("failedTasks", 0),
        }
        for d in service.get("deployments", [])
    ]

    task_arns = ecs.list_tasks(cluster=CLUSTER, serviceName=service_name).get(
        "taskArns", []
    )
    tasks = []
    if task_arns:
        for task in ecs.describe_tasks(cluster=CLUSTER, tasks=task_arns)["tasks"]:
            tasks.append(
                {
                    "lastStatus": task.get("lastStatus"),
                    "healthStatus": task.get("healthStatus"),
                    "taskDefinition": task["taskDefinitionArn"].rsplit("/", 1)[-1],
                    "imageDigests": [
                        c.get("imageDigest")
                        for c in task.get("containers", [])
                        if c.get("imageDigest")
                    ],
                    "startedAt": str(task.get("startedAt", "")),
                }
            )

    return {
        "service": service_name,
        "found": True,
        "status": service.get("status"),
        "taskDefinition": task_def_arn.rsplit("/", 1)[-1],
        "desired": service.get("desiredCount"),
        "running": service.get("runningCount"),
        "pending": service.get("pendingCount"),
        "containers": containers,
        "deployments": deployments,
        "tasks": tasks,
        "targetGroups": [
            lb["targetGroupArn"]
            for lb in service.get("loadBalancers", [])
            if lb.get("targetGroupArn")
        ],
    }


def target_health(elbv2, target_group_arns: list[str]) -> list[dict]:
    results = []
    for arn in target_group_arns:
        for description in elbv2.describe_target_health(TargetGroupArn=arn)[
            "TargetHealthDescriptions"
        ]:
            health = description["TargetHealth"]
            results.append(
                {
                    "targetGroup": arn.rsplit("/", 2)[-2],
                    "target": description["Target"].get("Id"),
                    "state": health.get("State"),
                    "reason": health.get("Reason", ""),
                }
            )
    return results


def recent_errors(logs, minutes: int) -> dict:
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    events = []
    paginator = logs.get_paginator("filter_log_events")
    for page in paginator.paginate(
        logGroupName=WEB_LOG_GROUP,
        startTime=int(start.timestamp() * 1000),
        filterPattern=ERROR_FILTER,
    ):
        events.extend(page.get("events", []))
    return {
        "logGroup": WEB_LOG_GROUP,
        "windowMinutes": minutes,
        "errorCount": len(events),
        "samples": [e["message"].strip()[:300] for e in events[:5]],
    }


def month_to_date_cost(ce) -> dict:
    today = datetime.now(timezone.utc).date()
    start = today.replace(day=1)
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": str(today + timedelta(days=1))},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )
    amount = response["ResultsByTime"][0]["Total"]["UnblendedCost"]
    return {
        "monthToDateUSD": round(float(amount["Amount"]), 2),
        "budgetUSD": 200,
    }


def build_report(log_minutes: int) -> dict:
    session = boto3.Session(region_name=REGION)
    report: dict = {"generatedAt": datetime.now(timezone.utc).isoformat()}

    report["identity"] = check_identity(session)
    if not report["identity"]["expected_account"]:
        report["error"] = (
            f"Credentials belong to account {report['identity']['account']}, "
            f"not the pilot account {EXPECTED_ACCOUNT}; refusing to continue."
        )
        return report

    ecs = session.client("ecs")
    elbv2 = session.client("elbv2")
    logs = session.client("logs")

    report["services"] = [describe_service(ecs, name) for name in SERVICES]

    all_target_groups = [
        arn
        for service in report["services"]
        for arn in service.get("targetGroups", [])
    ]
    report["targetHealth"] = target_health(elbv2, all_target_groups)

    try:
        report["logs"] = recent_errors(logs, log_minutes)
    except ClientError as error:
        report["logs"] = {"error": str(error)}

    try:
        report["cost"] = month_to_date_cost(session.client("ce"))
    except ClientError as error:
        report["cost"] = {"error": str(error)}

    return report


def print_human(report: dict) -> None:
    identity = report.get("identity", {})
    print(f"Account {identity.get('account')} as {identity.get('arn')}")
    if "error" in report:
        print(f"ABORTED: {report['error']}")
        return

    for service in report["services"]:
        if not service.get("found"):
            print(f"\n{service['service']}: NOT FOUND")
            continue
        print(
            f"\n{service['service']}: {service['status']} "
            f"{service['running']}/{service['desired']} running "
            f"({service['pending']} pending) on {service['taskDefinition']}"
        )
        for deployment in service["deployments"]:
            print(
                f"  deployment {deployment['status']}: "
                f"{deployment['taskDefinition']} "
                f"rollout={deployment['rolloutState']} "
                f"failed={deployment['failed']}"
            )
        for task in service["tasks"]:
            digests = ", ".join(task["imageDigests"]) or "no digest reported"
            print(
                f"  task {task['lastStatus']}/{task['healthStatus']} "
                f"since {task['startedAt']}: {digests}"
            )

    for target in report.get("targetHealth", []):
        print(
            f"target {target['target']} in {target['targetGroup']}: "
            f"{target['state']} {target['reason']}"
        )

    logs_section = report.get("logs", {})
    if "error" in logs_section:
        print(f"logs: {logs_section['error']}")
    else:
        print(
            f"logs ({logs_section['logGroup']}, last "
            f"{logs_section['windowMinutes']}m): "
            f"{logs_section['errorCount']} error lines"
        )
        for sample in logs_section.get("samples", []):
            print(f"  {sample}")

    cost = report.get("cost", {})
    if "error" in cost:
        print(f"cost: {cost['error']}")
    else:
        print(
            f"cost: ${cost['monthToDateUSD']} month-to-date "
            f"of ${cost['budgetUSD']} pilot budget"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument(
        "--log-minutes",
        type=int,
        default=60,
        help="how far back to scan the web log group for errors (default 60)",
    )
    args = parser.parse_args()

    try:
        report = build_report(args.log_minutes)
    except (BotoCoreError, ClientError) as error:
        print(f"AWS call failed: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_human(report)
    return 1 if "error" in report else 0


if __name__ == "__main__":
    sys.exit(main())
