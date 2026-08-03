# Custom MAE Analytics

## Purpose

Supervisors can ask MAE for a chart that is not already visible on the
Analytics page, preview it in the chat, download a matching PDF, and save the
view to the Analytics dashboard.

## Safety boundary

- MAE never generates or executes SQL.
- The server selects from a fixed chart allowlist.
- Chart labels and values come from the existing aggregate completed-call
  analytics snapshot.
- Saved widgets store only a title, allowlisted view key, creator, timestamps,
  and active or retired status.
- Saved widgets read fresh aggregate data for the reporting window currently
  selected on the Analytics page; chart values are not persisted.
- The feature does not write to CentralSquare or store caller, address,
  narrative, recording, credential, or raw CAD data.

## Supported views

- calls by day
- calls by hour
- calls by day of week
- calls by agency
- incident types
- dispatcher call-taker workload
- busiest units
- busiest stations

## Operator use

Ask MAE explicitly for a chart or graph, for example:

`Show me a chart of the busiest days of the week for the last 30 days.`

When aggregate analytics are available, the response includes a chart preview,
a PDF download action, and a **Save to Analytics** action. Saved cards appear
under **Supervisor-Saved Views** on the Analytics page and can be retired with
the remove button.

## Validation

- Focused analytics, MAE, PDF, and API tests: 60 passed.
- Full application test suite: 241 passed.
- Both changed JavaScript files passed `node --check`.
- The repository-wide unscoped pytest command also discovers the standalone
  iClone helper `scripts/iclone_create_disposable_test.py`; that helper requires
  iClone's `RLPy` runtime and is intentionally outside the application test
  suite.
