#!/usr/bin/env bash
# Create the read-only "lcdash-claude-observer" IAM user for remote Claude
# Code sessions. Run this in AWS CloudShell while signed into the pilot
# account (862772137583) — never from a machine where the printed access key
# could land in chat, logs, or Git.
#
# It is idempotent: re-running rotates the access key (old keys are deleted)
# and re-applies the policy from this repository.
#
# Usage (CloudShell):
#   git clone --depth 1 --branch aws/modular-county-platform \
#     https://github.com/shegone/LCDash.git && bash LCDash/infrastructure/tools/create_observer.sh
set -euo pipefail

USER_NAME="lcdash-claude-observer"
POLICY_NAME="LCDashObserverReadOnly"
EXPECTED_ACCOUNT="862772137583"
POLICY_FILE="$(dirname "$0")/../iam/LCDashObserverReadOnlyPolicy.json"

account="$(aws sts get-caller-identity --query Account --output text)"
if [[ "$account" != "$EXPECTED_ACCOUNT" ]]; then
  echo "Signed into account $account, not the pilot account $EXPECTED_ACCOUNT. Aborting." >&2
  exit 1
fi

aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1 \
  || aws iam create-user --user-name "$USER_NAME" \
       --tags Key=purpose,Value=claude-code-readonly-observer

aws iam put-user-policy --user-name "$USER_NAME" \
  --policy-name "$POLICY_NAME" --policy-document "file://$POLICY_FILE"

# Rotate: drop any existing keys so exactly one is live.
for key in $(aws iam list-access-keys --user-name "$USER_NAME" \
    --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
  aws iam delete-access-key --user-name "$USER_NAME" --access-key-id "$key"
done

echo
echo "Copy these two values ONLY into the Claude Code environment settings"
echo "(claude.ai/code -> your LCDash environment -> environment variables):"
echo
aws iam create-access-key --user-name "$USER_NAME" \
  --query '{AWS_ACCESS_KEY_ID: AccessKey.AccessKeyId, AWS_SECRET_ACCESS_KEY: AccessKey.SecretAccessKey}' \
  --output table
echo
echo "Also set AWS_DEFAULT_REGION=us-east-1. Do not paste the secret anywhere else."
