# Approved 164-document transfer, preparation, and upload result

Status: **PASS**.

## Transfer and validation

- Forced transfer sessions used successfully: 1
- Archive bytes: 251,555,840
- Archive SHA-256:
  `206e640648db476da1f15fca1cb7025f1b471511a775889d6d42dcdabf31e671`
- Archive members: 164 unique regular files
- Allowlist reconciliation: exact; no extras, omissions, links, directories,
  absolute paths, or traversal paths
- Metadata size reconciliation: 164 of 164
- Local content SHA-256 calculation: 164 of 164

## Preparation

- Eligible documents: 164
- Rejected documents: 0
- Mindshare: 131
- CentralSquare: 33
- Deterministic chunks: 2,099
- Embeddings: disabled
- Chunk review-manifest SHA-256:
  `4d27dee58f0f2c62889ac7de2087ac129a0dea2b13541cdfd5bb57c7ca74230b`

The path exclusion matcher was corrected to use token boundaries so the word
`token` does not falsely match the letters spanning `ToKenwood`. Backup and
firmware path exclusions remain fail-closed. Twelve focused tests pass.

Artifact SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| Transfer content manifest | `085fb5dd81f0fcf0a1a142b0865621647b9c745fb2d393272be9232a271a173d` |
| Chunk manifest | `509f5be9a188417f020db42577868c9fda1b540b7577a20f38de92e14001aff9` |
| Local chunk JSONL | `17bcb1200c5420dfb3607d84db690f385315118ceb42567e2fd6886619851618` |
| Cloud upload manifest | `0192ff605ca8f8c90bfe1f9e5fb36f1328ee9e32f8e46105bec60eb08cf60d36` |
| Cloud upload result | `d307b4ed3f85a93b31285acfd96516b2ddaa4efdc449946a255ad9e88cab533e` |

## Cloud object verification

- Bucket: `lcdash-p1-logan-use1-862772137583-document-library`
- Uploaded objects: 164
- Uploaded bytes: 251,295,872
- Mindshare prefix objects: 131
- CentralSquare prefix objects: 33
- Exact remote key-set reconciliation: passed
- `HeadObject` byte/checksum/metadata/encryption verification: 164 of 164
- Server-side encryption: `AES256` on every object
- Approval, manifest, classification, and source SHA-256 metadata: verified on
  every object
- Public-access blocks: all four remain enabled
- Bucket policy: non-public
- Default encryption: SSE-S3 `AES256`; SSE-C blocked

The 176 metadata exclusions and one sanitization hold were absent from the
allowlist and were not uploaded. No Bedrock knowledge base, vector store,
ingestion job, RAG/provider call, CAD change, foundation change, or on-prem
source modification occurred.

## Cleanup recommendation

After the operator accepts this evidence:

1. On `.227`, remove the transfer-key authorization and root-owned allowlist
   using the revocation block in
   `ONPREM_KB_TRANSFER_KEY_AUTHORIZATION_2026-08-05.md`.
2. Confirm the public-key comment no longer exists in `authorized_keys` and the
   server staging/installed allowlist files are absent.
3. Delete the local private transfer key
   `C:\Users\tedsp\.ssh\lcdash_kb_transfer_20260805` and its `.pub` file.
4. After retaining the signed manifests and upload evidence, securely remove
   the local tar archive, extracted document tree, and full-text chunk JSONL
   from `work/`; these contain approved private document content and are no
   longer needed for cloud object verification.
5. Do not delete the cloud objects or begin ingestion/RAG work without the next
   explicit authorization gate.
