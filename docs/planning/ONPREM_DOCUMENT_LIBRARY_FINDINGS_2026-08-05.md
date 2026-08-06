# On-premises document-library inventory findings

## Scope and result

This is a read-only inventory of document sources explicitly referenced by the
application and configuration in `E:\Projects\LCDash`. No `.env` file, secret,
credential, database, backup, CAD payload, recording, or operational output was
opened or copied. No server, Google Drive remote, live service, or AWS resource
was contacted. The detailed machine-readable record is
`ONPREM_DOCUMENT_LIBRARY_MANIFEST_2026-08-05.json`.

The inspected worktree does not contain the synchronized CentralSquare,
Mindshare, public-site, or GIS source trees. It therefore cannot safely provide
an exhaustive file-level inventory of the live libraries. The manifest records
unknown sizes and hashes as `null`; it does not infer them from database counts
or documentation.

## Referenced source roots

| Source | Authoritative configured root | Application view | Disposition |
|---|---|---|---|
| CentralSquare documents | `/srv/lcdash-data/documents/centralsquare` | `/knowledge/centralsquare` | Review PDFs individually for a future cloud library. |
| Mindshare documents | `/srv/lcdash-data/documents/mindshare` | `/knowledge/mindshare` | Review approved current documentation and sanitized references individually. |
| Public CSS Mindshare snapshot | `/srv/lcdash-data/documents/mindshare/Public CSS Mindshare Website` | `/knowledge/mindshare/Public CSS Mindshare Website` | Prefer a fresh fetch from the fixed public allowlist after policy review. |
| GIS reference layers | `/srv/lcdash-data/gis-public` | `/gis/reference` | Exclude from the document library; use the separate GIS/data workflow. |

The configuration also names Google Drive remotes `lcdash-knowledge:Central
Squared CAD/pdf` and `lcdash-knowledge:Mindshare Documents`. These are source
identifiers only. No rclone configuration or remote listing was read.

## Explicitly named documents

The repository explicitly names two likely-current Mindshare application notes:

- `MS1007_AN_MRIToIcomF5060AppNote_rev102.pdf`
- `MS1014_AN_MRIToHyteraMD78XAppNote_v102.pdf`

It also records older `rev1.01` and `v101` copies as archive candidates, but
does not preserve their exact filenames. Three empty `Readme.md` placeholders
are documented without exact paths. None of these files is present in the
inspected worktree, so current size, hash, ownership, revision order, and content
sensitivity remain unverified.

## Candidate cloud-library material

Candidate material is limited to individually reviewed current manuals,
procedures, application notes, release notes, public product literature, and
sanitized system-reference documents. A later authorized inventory should
record each relative path, byte size, modification time, SHA-256, document
owner, publication/revision date, sensitivity, copyright or license basis,
retention class, and supersession relationship before any upload.

`Mindshare Documents/Software Catalog` is conditional metadata, not an automatic
content source. It must be checked for license secrets and unsafe operational
configuration. `Vendor Archives` remains excluded by default even when an
extracted current copy is eligible.

## Mandatory exclusions

Exclude credentials and secret-bearing files; backups and recovery bundles;
raw CAD exports or payloads; incident, unit, webhook, or protected operational
records; recordings and operational transcripts; executables, firmware, disk
images, model files, and installers; station-alert, EMS, paging, warning, radio,
ESInet, acknowledgement, and other operational-output material; empty
placeholders; and sync-status files.

Structured GIS layers are not documents and require their own reviewed,
tenant-bound import. The public-site snapshot should be re-fetched from the
fixed public allowlist rather than copied from on-premises storage, subject to
current copyright, retention, content-type, and size review.

## Application references and limits

The indexer recognizes PDF, DOCX, text, Markdown, common configuration, JSON,
XML, CSV, and YAML formats and blocks filenames containing `.env`, `credential`,
`password`, `private_key`, or `secret`. Those filename checks are useful but are
not sufficient for cloud admission: content-level secret scanning, malware
scanning, data-owner approval, and human classification are still required.

The CentralSquare sync is narrower and copies only PDFs. The Mindshare sync
copies the broader supported set while excluding several secret-shaped names.
The public-site worker is restricted to seven named public pages and same-site
PDFs below 25 MiB under `/wp-content/uploads/`.

## Next authorized step

Do not upload from this inventory. A separate human-approved, read-only source
enumeration should run against the exact server directories or approved Drive
roots without opening content unnecessarily. It should produce metadata and
hash evidence first, apply the exclusions above, and stop for data-owner and
security review before any S3 bucket, object, index, or cloud knowledge service
is created.
