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

## Document-derived product definitions

JACK can answer approved short product-definition questions from the indexed
Mindshare library without asking the conversational model to infer a procedure.
For example, `What does MRI stand for?` returns `Mindshare Radio Interface`
only after the matching indexed manual is found. These answers carry source
evidence and do not relax the documented-procedure, credential, or read-only
boundaries.

## General technical guidance and suggestions

When no product-specific procedure is needed, JACK may answer ordinary technical
concept questions from its local general knowledge. Those answers are explicitly
labeled `General technical guidance` and do not claim Mindshare-document support.
For a documented product question, JACK may offer one clearly marked suggestion
only when it logically follows from cited material. He still will not invent or
recommend exact ports, frequencies, credentials, firmware steps, configuration
values, or equipment-changing actions without direct approved documentation.

## Supervisor-approved local knowledge

JACK has a local learning ledger in the Reliability Center. A supervisor may
create a proposed title, recall phrase, and guidance item. New items remain
pending and cannot affect answers until a supervisor explicitly approves them.
Approved local guidance is labeled separately from vendor documentation, keeps
its creator and approver audit fields, records use counts, and can be retired.
Secret-like values are rejected, and no memory item grants CAD or equipment
write access or overrides JACK's credential and action boundaries.

## Public company website source

JACK also indexes a fixed allowlist of public pages from `css-mindshare.com`
once per day. This source is limited to public company, product, download,
case-study, and FAQ material. PDF links found on the allowlisted Downloads and
Case Studies pages are collected only when they remain on the CSS Mindshare
site under its public uploads path and pass PDF and size checks. This includes
public product literature, data sheets, market papers, and case studies. It
does not crawl or authenticate to the customer portal, support portal, forms,
store, or any login-protected area. Website-derived material remains separate
from Logan County system documents and has no CAD access.

## Conversational streaming

In JACK voice mode, Ollama response tokens are sent through a private NDJSON
stream. The browser detects complete sentences and queues them to the fixed
synthetic JACK voice in order, allowing speech to begin before the entire
answer is complete. The final answer is still audited with its evidence and
assurance metadata. If streaming fails, the existing complete-answer route is
used automatically.

The first complete sentence begins promptly. Later short sentences are grouped
into more natural phrases, and the next audio phrase is synthesized while the
current phrase is playing. This removes the avoidable generate-play-generate
gaps without running concurrent inference against the voice model.

JACK asks Ollama to keep the local conversational model warm for two hours
after use. This avoids a full model reload during a normal supervisor work
session while still allowing GPU memory to be released after extended idle
time. A first question after deployment, model replacement, or a long idle
period can still require a cold model load.

JACK retains its supporting citations in the written answer. In voice mode,
the browser omits inline document-title and page-number labels so the answer
sounds conversational; the on-screen supporting-document titles remain
clickable for approved PDFs.

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
