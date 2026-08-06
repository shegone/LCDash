# Mindshare cloud document upload evidence

Status: **PASS - EXACTLY THREE APPROVED OBJECTS UPLOADED**.

Authorized account: `862772137583`; region: `us-east-1`.

Bucket: `lcdash-p1-logan-use1-862772137583-document-library`

Manifest-scoped prefix:
`tenants/logan-synthetic/document-library/mindshare/current/user-approved-existing-onprem-project-documents-2026-08-05/`

| Object | Bytes | Source/stored SHA-256 | S3 checksum SHA-256 (base64) | Encryption |
| --- | ---: | --- | --- | --- |
| `MINDSHARE_LIBRARY.md` | 7801 | `29e673c31c60b9dd798852dac9ed1cb3edb40a243ea5c68aea1afa1c6f59f322` | `KeZzwxxgud15iFLaye0cs+20CiQ+pcaK6hr6HG9Z8yI=` | `AES256` |
| `MINDSHARE_RADIO_CHECKLIST.md` | 1875 | `9a2357d97076dc59abc5b5308947571dfed92a0ff76f847a8be4845173a59e9f` | `miNX2XB23FmrxbUwiUdXHf7ZKg/3b4R6i+SEUXOlnp8=` | `AES256` |
| `MINDSHARE_SOFTWARE_CATALOG.md` | 2638 | `f0d41f86f0424bcdee5d45aa6951a909e6245b1fce6ba82579ea92c49e650724` | `8NQfhvBCS83uXUWqaVGpCeYkWx/Oa6gleeqSxJ5lByQ=` | `AES256` |

Each object has content type `text/markdown; charset=utf-8` and metadata:

- `approval-id=user-approved-existing-onprem-project-documents-2026-08-05`
- `classification=mindshare-current`
- `sha256=<matching source SHA-256>`
- `source-name=<matching source filename>`

Post-upload prefix enumeration returned exactly these three keys and no others.
Each `HeadObject` with checksum mode enabled returned the expected byte count,
checksum, metadata, and `ServerSideEncryption=AES256`.

The bucket public-access configuration remained:

- `BlockPublicAcls=true`
- `IgnorePublicAcls=true`
- `BlockPublicPolicy=true`
- `RestrictPublicBuckets=true`

Bucket policy status remained `IsPublic=false`. Default bucket encryption
remained SSE-S3 `AES256`, with SSE-C blocked.

No other document was uploaded. No Bedrock knowledge base, vector store, or RAG
feature was created or enabled. No CAD, foundation, production `.227`, on-prem
service, credential content, backup, or operational output was accessed or
changed.
