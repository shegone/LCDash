"""Provision the Bedrock data source for admin-uploaded knowledge documents.

The live knowledge base (BPKT5MB6UW) and its two approved data sources were
created out-of-band -- the repo's knowledge-search stack is plan-only and has
never been deployed -- so this third data source is provisioned the same way:
a one-time scripted change, recorded here so the repo stays truthful about
what exists.

What it does, idempotently:

1. Creates data source ``lcdash_cloud_uploads`` on the knowledge base with the
   two admin-upload inclusion prefixes, copying the chunking and parsing
   configuration VERBATIM from the live centralsquare data source (chunking is
   irreversible per docs/planning/PRIVATE_BEDROCK_KB_RAG_READINESS_2026-08-05;
   uploads must not fragment differently from the approved sets).
2. Extends the knowledge-base service role's inline policy so Bedrock can read
   the upload prefixes: adds them to the s3:ListBucket prefix condition and
   the s3:GetObject resources. Nothing is removed and no other statement is
   touched.

Dry-run by default; pass --apply to execute. Prints the data source id --
that value becomes the CloudAiUploadsDataSourceId stack parameter, and the two
upload prefixes must be appended to CloudAiAllowedS3Prefixes in the same
stack update.

Prefix shapes (why they look like this):
- ``mae-uploads/current/`` -- retrievable by MAE only.
- ``jack-uploads/mindshare/current/`` -- the persona filter confines JACK to
  prefixes containing ``/mindshare/``; the distinct FIRST segment keeps the
  document-library key parser from colliding with the approved mindshare set.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys

import boto3

KNOWLEDGE_BASE_ID = "BPKT5MB6UW"
REGION = "us-east-1"
BUCKET = "lcdash-p1-logan-use1-862772137583-document-library"
BUCKET_ARN = f"arn:aws:s3:::{BUCKET}"
DATA_SOURCE_NAME = "lcdash_cloud_uploads"
TEMPLATE_DATA_SOURCE_NAME = "lcdash_centralsquare_approved_20260805"
KB_ROLE_NAME = "AmazonBedrockExecutionRoleForKnowledgeBase_lcdash_p1_logan_use1"

UPLOAD_PREFIXES = (
    "tenants/logan-synthetic/document-library/mae-uploads/current/",
    "tenants/logan-synthetic/document-library/jack-uploads/mindshare/current/",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the plan. Without this the script only prints it.",
    )
    return parser.parse_args(argv)


def _find_data_source(bedrock, name: str) -> dict | None:
    token = None
    while True:
        kwargs = {"knowledgeBaseId": KNOWLEDGE_BASE_ID, "maxResults": 20}
        if token:
            kwargs["nextToken"] = token
        page = bedrock.list_data_sources(**kwargs)
        for summary in page.get("dataSourceSummaries", []):
            if summary["name"] == name:
                return bedrock.get_data_source(
                    knowledgeBaseId=KNOWLEDGE_BASE_ID,
                    dataSourceId=summary["dataSourceId"],
                )["dataSource"]
        token = page.get("nextToken")
        if not token:
            return None


def _plan_role_policy(iam_client) -> tuple[str, dict, bool]:
    """Return (policy_name, updated_document, changed)."""
    policy_names = iam_client.list_role_policies(RoleName=KB_ROLE_NAME)["PolicyNames"]
    if len(policy_names) != 1:
        raise SystemExit(
            f"error: expected exactly one inline policy on {KB_ROLE_NAME}, "
            f"found {policy_names}; refusing to guess."
        )
    policy_name = policy_names[0]
    document = iam_client.get_role_policy(
        RoleName=KB_ROLE_NAME, PolicyName=policy_name
    )["PolicyDocument"]
    updated = copy.deepcopy(document)
    changed = False

    wildcards = [prefix + "*" for prefix in UPLOAD_PREFIXES]
    object_arns = [f"{BUCKET_ARN}/{prefix}*" for prefix in UPLOAD_PREFIXES]

    for statement in updated.get("Statement", []):
        action = statement.get("Action")
        actions = action if isinstance(action, list) else [action]
        if actions == ["s3:ListBucket"] or action == "s3:ListBucket":
            prefixes = statement["Condition"]["StringLike"]["s3:prefix"]
            for wildcard in wildcards:
                if wildcard not in prefixes:
                    prefixes.append(wildcard)
                    changed = True
        if actions == ["s3:GetObject"] or action == "s3:GetObject":
            resources = statement["Resource"]
            if isinstance(resources, str):
                resources = [resources]
                statement["Resource"] = resources
            for arn in object_arns:
                if arn not in resources:
                    resources.append(arn)
                    changed = True
    return policy_name, updated, changed


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    session = boto3.Session(region_name=REGION)
    bedrock = session.client("bedrock-agent")
    iam_client = session.client("iam")

    existing = _find_data_source(bedrock, DATA_SOURCE_NAME)
    template = _find_data_source(bedrock, TEMPLATE_DATA_SOURCE_NAME)
    if template is None:
        print(f"error: template data source {TEMPLATE_DATA_SOURCE_NAME} not found.")
        return 1

    policy_name, updated_policy, policy_changed = _plan_role_policy(iam_client)

    print(f"Knowledge base: {KNOWLEDGE_BASE_ID}")
    print(f"Data source {DATA_SOURCE_NAME}: "
          f"{'EXISTS id=' + existing['dataSourceId'] if existing else 'to be CREATED'}")
    print(f"KB role policy {policy_name}: "
          f"{'to be EXTENDED with upload prefixes' if policy_changed else 'already grants upload prefixes'}")
    for prefix in UPLOAD_PREFIXES:
        print(f"  inclusion prefix: {prefix}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to make these changes.")
        return 0

    if policy_changed:
        iam_client.put_role_policy(
            RoleName=KB_ROLE_NAME,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(updated_policy),
        )
        print("applied: KB role policy extended.")

    if existing is None:
        created = bedrock.create_data_source(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            name=DATA_SOURCE_NAME,
            description="Admin-uploaded documents (mae-uploads + jack-uploads).",
            # RETAIN matches the approved data sources: deleting the data
            # source must never delete vectors out from under the index.
            dataDeletionPolicy="RETAIN",
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": BUCKET_ARN,
                    "bucketOwnerAccountId": "862772137583",
                    "inclusionPrefixes": list(UPLOAD_PREFIXES),
                },
            },
            # Copied verbatim from the live approved source, not restated.
            vectorIngestionConfiguration=template["vectorIngestionConfiguration"],
        )["dataSource"]
        print(f"applied: created data source {created['dataSourceId']}")
        data_source_id = created["dataSourceId"]
    else:
        data_source_id = existing["dataSourceId"]

    print(
        "\nNext steps:\n"
        f"  1. Stack parameter CloudAiUploadsDataSourceId = {data_source_id}\n"
        "  2. Append to CloudAiAllowedS3Prefixes:\n"
        + "".join(f"     s3://{BUCKET}/{prefix},\n" for prefix in UPLOAD_PREFIXES)
        + "  3. Deploy; then upload a test PDF and run a sync from /admin/knowledge."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
