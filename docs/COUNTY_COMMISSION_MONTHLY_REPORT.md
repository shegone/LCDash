# County Commission Monthly Report

## Purpose

The Pre-Built Reports page provides a repeatable Logan County 911 County
Commission report for a selected month. It reproduces the CAD-derived Fire,
Law, and LEASA run totals from the existing spreadsheet format and supports a
printable page and aggregate PDF download.

Phone Totals are intentionally excluded. They are supplied by the separate
phone system and are not a CentralSquare CAD field.

## Report definition

The service makes a read-only `POST /cfs_core/search` request using:

- `RecordCreatedFrom`: first day of the selected month at midnight in
  `America/New_York`, inclusive
- `RecordCreatedTo`: first day of the following month at midnight in
  `America/New_York`, exclusive
- `OrderByField`: `Created`
- `OrderByDirection`: `Ascending`
- 100 records per page, fetched sequentially

Each CFS record is deduplicated by CFS number. Every assigned unit is counted
under `Unit.Agency.Abbreviation`. The report includes only the approved agency
codes below; other assignments are excluded from the displayed totals.

### Fire mappings

| CAD agency | Report department |
| --- | --- |
| FC 100 | Henlawson |
| FC 200 | Man #2 |
| FC 300 | Chapmanville |
| FC 400 | Lake |
| FC 500 | Sharples |
| FC 600 | Harts |
| FC 700 | Cora |
| FC 800 | Main Island Creek |
| FC 900 | Verdunville |
| FC 1000 | City Of Logan |
| FC 1100 | Buffalo Creek |
| FC 1200 | Town Of Man |

### Law and EMS mappings

| CAD agency | Report department |
| --- | --- |
| LCSO | Logan SO |
| DPS | Logan State Police |
| LPD | Logan City Police |
| MPD | Man Police |
| CPD | Chapmanville Police |
| WLPD | West Logan Police |
| MHPD | Mitchell Heights PD |
| LEASA | LEASA |

## Operation and safety

- The browser starts a background report job and polls for aggregate progress,
  avoiding a long browser or Cloudflare request timeout.
- Only one CentralSquare monthly report query can run at a time. A duplicate
  request for the same month reuses the active job.
- The result contains aggregate department counts and query-quality metadata;
  it does not retain or return raw CAD records, narratives, addresses, patient
  details, or caller information.
- The feature has no CAD write path.
- Print and PDF output use only the completed aggregate result.

## Acceptance reference

The direct query was reconciled against the supplied June 2026 County Report.
The CAD totals matched exactly:

- Fire Total: 499
- Law Total: 1,651
- LEASA Total: 1,985

The workbook's Phone Total was not used or reproduced because it belongs to
the phone system rather than CAD.
