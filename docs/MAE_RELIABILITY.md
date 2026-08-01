# MAE Reliability and Learning

MAE remains inquiry-only. No tool in this release can add, update, dispatch,
acknowledge, close, or delete data in CentralSquare.

## Reliability Center

Open `/mae/reliability` to:

- run any of 50 supervisor-style evaluation questions;
- confirm source selection, non-empty answers, and read-only behavior;
- review recorded supervisor feedback;
- create pending local-memory guidance;
- approve, reject, or retire memory.

Evaluation runs are intentionally executed one at a time so testing does not
create unnecessary CentralSquare traffic.

## Hybrid knowledge retrieval

The knowledge index retains PostgreSQL full-text search and can add local
semantic embeddings from Ollama using `nomic-embed-text`. If the embedding
model is unavailable, MAE continues using the existing keyword search.

The knowledge worker backfills embeddings for documents that were indexed
before semantic search was enabled.

## Google Drive synchronization

The optional `knowledge-drive-sync` service copies PDF files from the
configured rclone remote every 15 minutes. It uses `rclone copy`; it does not
remove files from Google Drive or delete local documents.

The current server rclone connection can see the LCDash backup folder but must
also be granted read access to the CentralSquare documentation folder before
this feature can retrieve those PDFs. The service remains in the
`knowledge-drive` Compose profile until that authorization is complete.

## Answer assurance

Every MAE answer includes:

- confidence: high, moderate, or limited;
- freshness: live age, historical snapshot, or indexed documentation;
- authority: live operational evidence, database history, vendor documents,
  approved local guidance, or MAE safety policy;
- measured total, research, and model-generation time.

## Incident-type call review

Authorized supervisors can ask MAE for the latest one to ten completed calls
matching an incident description or code, for example:

`Show me the last five chest pain calls.`

MAE returns a deterministic PostgreSQL-backed list with CFS number, received
time, incident description, city, and priority. The result is explicitly
labeled as completed-call history; an active call may not appear until the
analytics collector stores it as completed.

After selecting a CFS number, the supervisor can request one of three read-only
CentralSquare responses:

- `Give me a call summary for CFS26-12345.` for core call and unit fields;
- `Give me a detailed call report for CFS26-12345.` for returned reporter,
  command-log, and attached-data indicators; or
- `Give me a call narrative for CFS26-12345.` for a chronological account based
  strictly on returned CAD fields and command-log entries.

The narrative must not infer an event, patient condition, or outcome that was
not returned by CentralSquare.

## Controlled memory

MAE does not self-modify. Memory candidates remain pending until an authorized
supervisor approves them. Approved memory is guidance only and cannot override
live CAD, historical records, vendor documentation, or the read-only boundary.
