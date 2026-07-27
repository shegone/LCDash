# Mindshare Technical Library

## Reliability and coverage

The Mindshare module includes two read-only quality tools:

- `/mindshare/reliability` provides 30 realistic JACK questions grounded in
  the indexed Console, MRI/MRI2, gateway, service, release-note, and safety
  documentation. Tests score the selected evidence, supported/refusal behavior,
  and response time.
- `/mindshare/coverage` summarizes product and document-type coverage, identifies
  documents with no searchable passages, and lists possible duplicate or older
  revisions for human review.

The coverage review never deletes, moves, renames, or archives a source document.

### Initial revision review

The first coverage review identified:

- `MS1007_AN_MRIToIcomF5060AppNote_rev102.pdf` as the likely current revision,
  with `rev1.01` retained as an archive candidate.
- `MS1014_AN_MRIToHyteraMD78XAppNote_v102.pdf` as the likely current revision,
  with `v101` retained as an archive candidate.
- Three empty `Readme.md` folder placeholders. They should remain at their
  source if needed for folder guidance, but they do not count as searchable
  technical coverage.

No file is moved to `Vendor Archives` until a human confirms the revision order.

## Purpose

LCDash maintains a Mindshare knowledge library that is separate from MAE and
CentralSquare CAD. It is intended for technical manuals, procedures,
application notes, release notes, and approved Logan County system-reference
documents.

JACK, the Mindshare Technical Assistant, is read-only. It cannot change consoles,
gateways, radios, firmware, software, or CAD records.

## Memorial identity

JACK is named in honor of John Joseph "Jack" Hines III. Its conversational
style reflects the qualities consistently associated with Jack in public
professional and memorial accounts: direct communication, technical
confidence, practical business judgment, customer commitment, mentorship,
warmth, and good-natured humor.

JACK must always identify itself as an AI technical assistant. It must not
claim to be Jack Hines or invent his memories, quotations, opinions,
experiences, or relationships.

## Google Drive layout

`Mindshare Documents/Current Documentation`

- Current vendor user manuals
- Current vendor procedures
- Current vendor application notes
- Current vendor release notes

`Mindshare Documents/Logan County System`

- Current approved Logan County system-information documents
- Sanitized configuration references suitable for technical support
- No passwords, private keys, tokens, or license secrets

`Mindshare Documents/Software Catalog`

- Software and firmware version inventory
- Product/model applicability
- Vendor publication date
- File size and checksum when an archive is retained
- Installation status and review notes

`Mindshare Documents/Vendor Archives`

- Dated source archives downloaded from the authorized customer portal
- Retained as a recovery and provenance copy
- Not indexed directly when an extracted current-document copy exists

## Server layout

The Linux server synchronizes supported documents into:

`/home/ted/lcdash-platform/knowledge/mindshare`

Only document and configuration-reference formats are synchronized. Executable
installers, disk images, firmware archives, license files, credentials, and
secrets are excluded from the assistant index.

## Firmware and software rule

Never select or install a package only because it is the newest version.
Confirm:

1. Exact Mindshare product and hardware model.
2. Current installed software and firmware versions.
3. Vendor-supported upgrade path.
4. Required backups and rollback method.
5. Maintenance window and operational approval.
6. Matching release notes and installation procedure.

## Access boundaries

- JACK access can be granted independently of MAE/CAD.
- Future Radio Intelligence access will be controlled separately.
- Radio Intelligence remains inactive until the isolated multicast network is
  connected, validated, and covered by approved retention and audit rules.
