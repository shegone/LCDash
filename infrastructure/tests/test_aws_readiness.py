import json
from unittest.mock import patch
import unittest

from infrastructure.tools.check_aws_readiness import (
    APPLICATION_STACKS,
    BOOTSTRAP_STACK,
    CommandResult,
    check_readiness,
    resolve_aws_executable,
)


class StubRunner:
    def __init__(self, *, account="862772137583", region="us-east-1", stacks=None, certificate="ISSUED"):
        self.account = account
        self.region = region
        self.stacks = stacks or {}
        self.certificate = certificate
        self.commands = []

    def __call__(self, command):
        command = list(command)
        self.commands.append(command)
        if command[1:3] == ["sts", "get-caller-identity"]:
            return CommandResult(0, json.dumps({"Account": self.account, "Arn": "secret-session-value"}))
        if command[1:4] == ["configure", "get", "region"]:
            return CommandResult(0, self.region)
        if command[1:3] == ["cloudformation", "describe-stacks"]:
            name = command[command.index("--stack-name") + 1]
            state = self.stacks.get(name)
            if state is None:
                return CommandResult(255, stderr="ValidationError: Stack with id does not exist")
            return CommandResult(0, json.dumps({"Stacks": [{"StackStatus": state}]}))
        if command[1:3] == ["acm", "describe-certificate"]:
            return CommandResult(0, json.dumps({"Certificate": {"Status": self.certificate}}))
        return CommandResult(1, stderr="unexpected")


class AwsReadinessTests(unittest.TestCase):
    def test_ready_with_bootstrap_and_absent_application_stacks(self):
        runner = StubRunner(stacks={BOOTSTRAP_STACK: "CREATE_COMPLETE"})
        report = check_readiness(runner=runner)
        self.assertEqual("READY", report.status)
        application = [c for c in report.checks if c.name.startswith("application_stack:")]
        self.assertEqual(len(application), len(APPLICATION_STACKS))
        self.assertTrue(all(c.detail == "stack is absent" for c in application))

    def test_wrong_account_region_and_missing_bootstrap_block(self):
        runner = StubRunner(account="000000000000", region="us-west-2")
        report = check_readiness(runner=runner)
        self.assertEqual("BLOCKED", report.status)
        blocked = {c.name for c in report.checks if c.status == "BLOCKED"}
        self.assertTrue({"caller_account", "caller_region", "bootstrap_stack"}.issubset(blocked))

    def test_present_stable_application_stacks_are_reported(self):
        stacks = {BOOTSTRAP_STACK: "UPDATE_COMPLETE"}
        stacks.update({name: "CREATE_COMPLETE" for name in APPLICATION_STACKS})
        report = check_readiness(runner=StubRunner(stacks=stacks))
        self.assertEqual("READY", report.status)
        self.assertTrue(all("accepted stable state" in c.detail for c in report.checks if c.name.startswith("application_stack:")))

    def test_transitional_or_failed_stack_state_blocks(self):
        runner = StubRunner(stacks={BOOTSTRAP_STACK: "CREATE_COMPLETE", APPLICATION_STACKS[0]: "ROLLBACK_IN_PROGRESS"})
        report = check_readiness(runner=runner)
        self.assertEqual("BLOCKED", report.status)
        self.assertIn("not in an accepted stable state", " ".join(c.detail for c in report.checks))

    def test_certificate_requires_exact_issued_arn_without_echo(self):
        arn = "arn:aws:acm:us-east-1:862772137583:certificate/12345678-abcd-1234-abcd-1234567890ab"
        ready = check_readiness(certificate_arn=arn, runner=StubRunner(stacks={BOOTSTRAP_STACK: "CREATE_COMPLETE"}))
        self.assertEqual("READY", ready.status)
        blocked = check_readiness(certificate_arn=arn, runner=StubRunner(stacks={BOOTSTRAP_STACK: "CREATE_COMPLETE"}, certificate="PENDING_VALIDATION"))
        self.assertEqual("BLOCKED", blocked.status)
        self.assertNotIn(arn, json.dumps(blocked.as_dict()))

    def test_invalid_certificate_arn_is_rejected_before_command(self):
        runner = StubRunner(stacks={BOOTSTRAP_STACK: "CREATE_COMPLETE"})
        report = check_readiness(certificate_arn="not-an-arn", runner=runner)
        self.assertEqual("BLOCKED", report.status)
        self.assertFalse(any(command[1:3] == ["acm", "describe-certificate"] for command in runner.commands))

    def test_only_read_operations_are_constructed_and_raw_values_are_sanitized(self):
        runner = StubRunner(stacks={BOOTSTRAP_STACK: "CREATE_COMPLETE"})
        report = check_readiness(profile="temporary-profile", runner=runner)
        allowed = {("sts", "get-caller-identity"), ("configure", "get"), ("cloudformation", "describe-stacks")}
        self.assertTrue(all(tuple(command[1:3]) in allowed for command in runner.commands))
        serialized = json.dumps(report.as_dict())
        self.assertNotIn("secret-session-value", serialized)
        self.assertNotIn("temporary-profile", serialized)

    def test_windows_path_discovery_uses_resolved_cli_executable(self):
        resolved = r"C:\Program Files\Amazon\AWSCLIV2\aws.exe"
        runner = StubRunner(stacks={BOOTSTRAP_STACK: "CREATE_COMPLETE"})
        with patch(
            "infrastructure.tools.check_aws_readiness.os.name",
            "nt",
        ), patch(
            "infrastructure.tools.check_aws_readiness.shutil.which",
            side_effect=lambda name: resolved if name == "aws.exe" else None,
        ):
            report = check_readiness(runner=runner)
        self.assertEqual("READY", report.status)
        self.assertTrue(runner.commands)
        self.assertTrue(all(command[0] == resolved for command in runner.commands))

    def test_explicit_cli_path_overrides_path_discovery(self):
        explicit = r"D:\Approved Tools\aws.cmd"
        with patch("infrastructure.tools.check_aws_readiness.shutil.which") as which:
            self.assertEqual(resolve_aws_executable(explicit), explicit)
        which.assert_not_called()


if __name__ == "__main__":
    unittest.main()
