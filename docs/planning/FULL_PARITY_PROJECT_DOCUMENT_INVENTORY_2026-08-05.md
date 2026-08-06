# Full-parity project and document inventory — Package 1A

Date: 2026-08-05
Status: metadata-only planning artifact; execution is not authorized

## Result

The local on-prem LCDash project contains 119 candidate files across the reviewed roots, totaling 5,208,580 bytes. These files fall into three different destinations and must not be treated as one migration payload:

| Source | Files | Bytes | Classification | Proposed destination |
|---|---:|---:|---|---|
| `E:\Projects\LCDash\config` | 0 | 0 | Operational configuration | Database, if approved structured records are later found |
| `E:\Projects\LCDash\database` | 3 | 18,555 | Schema definitions | Application release |
| `E:\Projects\LCDash\docs` | 34 | 144,444 | Project and operational documentation candidates | Private document library after approval review |
| `E:\Projects\LCDash\static` | 53 | 4,764,891 | Dashboard, report, and application assets | Application release |
| `E:\Projects\LCDash\templates` | 29 | 280,690 | Dashboard and report templates | Application release |

The application-release category is intentionally separate from the requested database-versus-document-library decision. Loading templates, JavaScript, styles, images, schema files, or vendored packages into either data destination would blur release provenance and complicate review.

## Candidate operational material

- Database: the already inventoried `lcdash_analytics` operational history remains the structured-data parity candidate. Its evidence is in `work/phase2_analytics_source_inventory_2026-08-05.json`; no export or import occurred.
- Private document library: `E:\Projects\LCDash\docs` is a review queue, not an approved corpus. Metadata identifies three Mindshare-named candidates totaling 12,314 bytes: `MINDSHARE_LIBRARY.md`, `MINDSHARE_RADIO_CHECKLIST.md`, and `MINDSHARE_SOFTWARE_CATALOG.md`.
- CentralSquare material: no local filename in the reviewed project roots identifies a CentralSquare document. The production document tree must be grouped in a later authorized metadata-only refresh before its CentralSquare candidate set can be stated reliably.
- Reports: `COUNTY_COMMISSION_MONTHLY_REPORT.md` plus the report CSS, JavaScript, and HTML template total four files and 20,034 bytes. These are definitions and presentation assets, not generated operational reports.
- Dashboard/report configuration: the local `config` directory contains no files. The reviewed templates and static assets belong in the versioned application release. Any runtime configuration elsewhere remains an open inventory item.

Filename matches do not establish that a document is approved. Admission to the private library requires an explicit approval record, especially for vendor material.

## Production document tree limitation

The candidate production library is `/srv/lcdash-data/documents` on `.227`. A metadata-only file walk established that the tree contains files, but the result was too large and was truncated before reliable grouped counts and sizes were captured. A subsequent non-interactive connection was rejected because the prior login session was unavailable. No credential was requested, tested, or changed, and no further production action was attempted.

The manifest therefore records this source with `null` counts rather than inventing totals. The next permitted read-only session should return only aggregate counts and byte totals by first-level directory and extension, followed by bounded filename matches for CentralSquare and Mindshare review.

## Hard exclusions

The inventory excludes credentials and secrets, backups and restore archives, binaries and models, raw CAD payloads, recordings, operational output/control records, virtual environments, and generated caches. It also excludes generated reports from the application-release set unless a later policy explicitly admits them as approved operational records.

## Gate status

- Source content read: no; metadata only.
- Source files copied: no.
- Data exported: no.
- Data uploaded: no.
- AWS resources created: no.
- Authorization gate complete: no.

No migration action should begin until the production-library metadata gap, document approvals, field policy, validation plan, and documented authorization gate are complete.
