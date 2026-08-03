# Real-Time CAD Delivery Acceptance - August 3, 2026

## Result

PASS for the controlled, read-only receiver-to-browser delivery path on `.227`.
No CentralSquare CAD record was created, updated, or deleted.

## Production evidence

- Both CFS and unit delivery metadata already showed real CentralSquare traffic.
- The authenticated local receiver accepted one unique synthetic notification
  for each source with HTTP 202.
- Repeating each notification returned `duplicate: true` and did not emit a
  second browser event.
- Exactly two `operations_changed` server-sent events were observed, one per
  unique source notification.
- Browser events contained only `source` and `received_at`.
- Both unique notifications were persisted as delivery metadata.
- The live operations snapshot was connected before and after the test.
- The 30-second reconciliation interval remained enabled.
- Production health reported `ready`, metadata-only storage, and an available
  realtime database.
- The production metadata table contains only event identifier, source,
  received/last-seen times, payload size, and duplicate count. It has no raw
  payload, call, patient, caller, location, narrative, or unit-detail column.

## Additional verification

- `tests/test_realtime.py` and `tests/test_active_calls.py`: 23 passed.
- Existing tests cover invalid authentication, invalid JSON, payload-size
  limits, deduplication, metadata-only browser events, and the 30-second
  fallback path.

## Public path note

A self-call from `.227` to its own public Cloudflare hostname returned HTTP 403
before reaching LCDash. The test was not repeated through that route. Existing
production metadata proves that CentralSquare reaches both public receiver
paths, while the controlled local test proves the common authenticated receiver,
deduplication, browser event, persistence, and reconciliation behavior.

## Safety boundaries retained

- The receiver remains read-only with respect to CentralSquare.
- Raw webhook bodies are neither stored nor sent to browsers.
- Human pages and browser event streams remain behind Cloudflare Access.
- Only the narrow authenticated webhook path bypasses human login.
- The periodic reconciliation poll remains active if streaming is interrupted.
