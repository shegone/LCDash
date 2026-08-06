# Local document preparation result

Status: **LOCAL PIPELINE READY; THREE APPROVED SOURCE DOCUMENTS ELIGIBLE**.

The local-only preparation pipeline is implemented in
`app/tools/document_preparation.py`. It accepts only individually listed files
under an explicitly declared repository-local source root. It records byte
size and SHA-256, extracts text without executing document content, applies the
existing approved-scope and hard-exclusion rules, creates deterministic chunks
and chunk identifiers, and returns a review manifest. Optional embeddings are
a model-free local feature hash for deterministic pipeline testing only; they
are disabled by default and are not suitable as production semantic vectors.

The user subsequently approved all existing on-prem/project documents for the
cloud knowledge library. The exact three repository-local Mindshare candidates
from the prior inventory were recorded in
`MINDSHARE_LOCAL_PREPARATION_REQUEST_2026-08-05.json` with approval ID
`user-approved-existing-onprem-project-documents-2026-08-05` and processed
directly from the explicitly allowed `E:\Projects\LCDash` source repository.

Current eligible source count: **3**. Rejected count: **0**.

| Source | Classification | Bytes | SHA-256 | Chunks | Exclusion result |
| --- | --- | ---: | --- | ---: | --- |
| `MINDSHARE_LIBRARY.md` | `mindshare-current` | 7801 | `29e673c31c60b9dd798852dac9ed1cb3edb40a243ea5c68aea1afa1c6f59f322` | 4 | no exclusion matched |
| `MINDSHARE_RADIO_CHECKLIST.md` | `mindshare-current` | 1875 | `9a2357d97076dc59abc5b5308947571dfed92a0ff76f847a8be4845173a59e9f` | 1 | no exclusion matched |
| `MINDSHARE_SOFTWARE_CATALOG.md` | `mindshare-current` | 2638 | `f0d41f86f0424bcdee5d45aa6951a909e6245b1fce6ba82579ea92c49e650724` | 2 | no exclusion matched |

Review-manifest SHA-256:
`e3f6c1b7dfc1ee25503de0fb329dd3372dd55610991510948ecdca826515a14d`.
Local embeddings remained disabled.

Source gaps:

- The production document library on `.227` requires a metadata refresh and is
  intentionally untouched by this task.
- No approved CentralSquare PDF or separately sanitized Mindshare system file
  was included in this bounded three-file package.

Test evidence: `python -m unittest tests.contracts.test_document_preparation
tests.contracts.test_document_intake_gate` completed 11 tests successfully.
Tests cover deterministic repeat output, SHA-256 inventory, multi-chunk output,
optional local embeddings, no-network behavior, mandatory recorded approval,
missing sources, path hard exclusions, scope-specific file types, and the
existing non-upload intake gate.

No upload, AWS resource creation, Bedrock access, RAG activation, production
connection, credential access, backup access, or operational-output access was
performed.
