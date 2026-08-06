"""Sanitized, read-only AWS readiness checks for a future authorized session."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import re
import shutil
import subprocess
from typing import Callable, Sequence


EXPECTED_ACCOUNT = "862772137583"
EXPECTED_REGION = "us-east-1"
BOOTSTRAP_STACK = "CDKToolkit"
APPLICATION_STACKS = (
    "lcdash-p1-logan-use1-certificate",
    "lcdash-p1-logan-use1-foundation",
)
STABLE_STACK_STATES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
}
CERTIFICATE_ARN_PATTERN = re.compile(
    rf"^arn:aws:acm:{EXPECTED_REGION}:{EXPECTED_ACCOUNT}:certificate/[0-9a-f-]+$"
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    status: str
    checks: tuple[ReadinessCheck, ...]

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
        }


Runner = Callable[[Sequence[str]], CommandResult]


def resolve_aws_executable(explicit_path: str = "") -> str:
    """Resolve AWS CLI without relying on PowerShell command resolution."""
    if explicit_path.strip():
        return explicit_path.strip()
    candidates = ("aws.exe", "aws.cmd", "aws") if os.name == "nt" else ("aws",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "aws"


def _aws_command(executable: str, profile: str, *arguments: str) -> list[str]:
    command = [executable, *arguments, "--no-cli-pager"]
    if profile:
        command.extend(["--profile", profile])
    return command


def _default_runner(command: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(1)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _json_object(result: CommandResult) -> dict | None:
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _stack_state(
    stack_name: str,
    *,
    aws_executable: str,
    profile: str,
    runner: Runner,
) -> tuple[str, str]:
    result = runner(
        _aws_command(
            aws_executable,
            profile,
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
            "--region",
            EXPECTED_REGION,
            "--output",
            "json",
        )
    )
    if result.returncode != 0:
        if "does not exist" in result.stderr.lower():
            return "ABSENT", "stack is absent"
        return "UNKNOWN", "stack state could not be verified"
    value = _json_object(result)
    stacks = value.get("Stacks", []) if value else []
    state = stacks[0].get("StackStatus") if len(stacks) == 1 else None
    if not isinstance(state, str):
        return "UNKNOWN", "stack response was not valid"
    if state not in STABLE_STACK_STATES:
        return "UNSAFE", "stack is present but not in an accepted stable state"
    return "PRESENT", "stack is present in an accepted stable state"


def check_readiness(
    *,
    profile: str = "",
    certificate_arn: str = "",
    aws_executable: str = "",
    runner: Runner = _default_runner,
) -> ReadinessReport:
    """Run only allowlisted read operations and return no raw AWS values."""
    checks: list[ReadinessCheck] = []
    executable = resolve_aws_executable(aws_executable)

    identity = _json_object(
        runner(
            _aws_command(
                executable, profile, "sts", "get-caller-identity", "--output", "json"
            )
        )
    )
    if identity and identity.get("Account") == EXPECTED_ACCOUNT:
        checks.append(ReadinessCheck("caller_account", "PASS", "expected account verified"))
    else:
        checks.append(ReadinessCheck("caller_account", "BLOCKED", "expected account not verified"))

    region_result = runner(
        _aws_command(executable, profile, "configure", "get", "region")
    )
    configured_region = region_result.stdout.strip() if region_result.returncode == 0 else ""
    if configured_region == EXPECTED_REGION:
        checks.append(ReadinessCheck("caller_region", "PASS", "expected region verified"))
    else:
        checks.append(ReadinessCheck("caller_region", "BLOCKED", "expected region not verified"))

    bootstrap_state, bootstrap_detail = _stack_state(
        BOOTSTRAP_STACK,
        aws_executable=executable,
        profile=profile,
        runner=runner,
    )
    checks.append(
        ReadinessCheck(
            "bootstrap_stack",
            "PASS" if bootstrap_state == "PRESENT" else "BLOCKED",
            bootstrap_detail,
        )
    )

    for stack_name in APPLICATION_STACKS:
        state, detail = _stack_state(
            stack_name,
            aws_executable=executable,
            profile=profile,
            runner=runner,
        )
        checks.append(
            ReadinessCheck(
                f"application_stack:{stack_name}",
                "PASS" if state in {"ABSENT", "PRESENT"} else "BLOCKED",
                detail,
            )
        )

    if not certificate_arn:
        checks.append(
            ReadinessCheck(
                "certificate",
                "NOT_CHECKED",
                "no certificate ARN was supplied",
            )
        )
    elif not CERTIFICATE_ARN_PATTERN.fullmatch(certificate_arn):
        checks.append(
            ReadinessCheck(
                "certificate",
                "BLOCKED",
                "certificate ARN format, account, or region is invalid",
            )
        )
    else:
        certificate = _json_object(
            runner(
                _aws_command(
                    executable,
                    profile,
                    "acm",
                    "describe-certificate",
                    "--certificate-arn",
                    certificate_arn,
                    "--region",
                    EXPECTED_REGION,
                    "--output",
                    "json",
                )
            )
        )
        state = certificate.get("Certificate", {}).get("Status") if certificate else None
        checks.append(
            ReadinessCheck(
                "certificate",
                "PASS" if state == "ISSUED" else "BLOCKED",
                "certificate is issued" if state == "ISSUED" else "certificate is not verified as issued",
            )
        )

    blocked = any(check.status == "BLOCKED" for check in checks)
    return ReadinessReport("BLOCKED" if blocked else "READY", tuple(checks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sanitized read-only AWS readiness checks.")
    parser.add_argument("--profile", default="", help="temporary AWS CLI profile name")
    parser.add_argument(
        "--aws-executable",
        default="",
        help="explicit path to aws.exe/aws.cmd when PATH discovery is insufficient",
    )
    parser.add_argument("--certificate-arn", default="", help="optional ACM certificate ARN")
    args = parser.parse_args(argv)
    report = check_readiness(
        profile=args.profile,
        certificate_arn=args.certificate_arn,
        aws_executable=args.aws_executable,
    )
    print(json.dumps(report.as_dict(), indent=2))
    return 0 if report.status == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
