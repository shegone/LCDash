# Complete on-prem knowledge-base metadata inventory result

Status: **PASS - COMPLETE 341-FILE METADATA CAPTURE**.

The authorized repeat of the server-enforced command captured stdout directly
to `work/onprem_kb_metadata_inventory_2026-08-05.tsv`. The capture is 41,942
bytes and contains 341 tab-delimited rows. Each row has only relative path,
byte size, and modification time.

Exact totals:

| Result | Files |
| --- | ---: |
| Candidate | 164 |
| Excluded | 176 |
| Sanitization hold | 1 |
| Total | 341 |

Candidate totals are 131 Mindshare and 33 CentralSquare files.
The complete source metadata totals 597,625,769 bytes: 484,698,597 Mindshare
bytes and 112,927,172 CentralSquare bytes. Candidate bytes total 251,295,872;
excluded bytes total 346,217,858; the sanitization hold is 112,039 bytes.

Exact exclusion totals:

| Reason | Files |
| --- | ---: |
| Download or aggregate copy | 102 |
| Public-site copy, do not copy | 27 |
| Legacy copy superseded by Current Documentation | 15 |
| Duplicate specialty copy | 12 |
| Hard-exclusion path term | 8 |
| Generated or empty placeholder | 6 |
| Discontinued | 4 |
| Over 25 MiB | 2 |
| Total excluded | 176 |

The one hold is
`mindshare/MS3042_SI_LoganCountyWVSystemInformation_v113.pdf`, which requires a
separate content-level sanitization review before consideration for the
sanitized-system prefix.

Hard exclusion matching uses whole path-token boundaries for terms such as
`token`. This avoids falsely rejecting filenames containing `ToKenwood` while
still excluding explicit backup and firmware paths.

The complete per-file disposition is recorded in
`ONPREM_KB_COMPLETE_METADATA_MANIFEST_2026-08-05.json`. No hashes were gathered,
no document content was read, no transfer or upload occurred, no source was
changed, and no AWS, Bedrock, vector, or RAG resource was changed.
