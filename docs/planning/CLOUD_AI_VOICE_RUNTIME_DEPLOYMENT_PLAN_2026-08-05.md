# Cloud advisory AI and voice runtime deployment plan

Status: **LOCAL POLLY IMPLEMENTATION READY FOR REVIEW - NO DEPLOYMENT OR SMOKE TEST AUTHORIZED**.

The local runtime is deny-by-default. Advisory generation is unavailable unless
`mode=advisory-rag`, an exact reviewed knowledge-base ID is configured, and the
application receives an explicit `documents_ingested=true` activation value
after approved document ingestion is reconciled. Voice has a separate
configuration flag, but TTS readiness is true only when a bounded Polly provider
is actually injected. Transcribe readiness remains false until its separately
reviewed provider exists. Neither capability exposes tools, CAD operations,
dispatch actions, alerts, radio, ESInet, paging, acknowledgements, or other
operational outputs.

## Runtime request and response envelope

- Retrieve approved tenant-bound passages first, then use Bedrock Converse with
  no `toolConfig` and an explicit `maxTokens` from the 64-1024 allowlist.
- Require citations for every supported answer. Limit answer text to 6,000
  characters and citations to 10. Return only a sanitized denial category when
  retrieval, generation, tenant, or citation checks fail.
- Limit questions and final transcripts to 4,000 characters. Push-to-talk audio
  is at most 30 seconds and 2,000,000 bytes. Accept only `en-US` final text.
- Polly accepts at most 3,000 displayed characters and returns at most 5,000,000
  bytes of neural MP3. Voice is selectable only between Matthew and Joanna.
  Speech text rewrites `911` to "nine one one" without changing display text.
- Raw audio, transcripts, synthesized audio, prompts, retrieved passages, and
  model responses are not persisted. Telemetry may contain only request ID,
  tenant ID, latency/counts, selected voice, and a sanitized status category.

## Exact application-role permissions

The task role must contain only the permissions needed by providers actually
implemented and separately authorized. For the Polly-only package, this is:

1. `polly:SynthesizeSpeech` on `*`, because Polly does not expose a useful
   resource ARN for this action.
2. A `StringEquals` condition requiring `aws:RequestedRegion=us-east-1`.
3. Attachment to the ECS application task role only, never its execution role,
   release-builder role, deployment role, or knowledge-base role.

The local IaC removes `transcribe:StartStreamTranscription` until the Transcribe
provider and its separate activation gate exist. A later Transcribe review may
restore only that single action on `*`, with the same region condition.

Explicitly omit `polly:DescribeVoices`, Polly lexicon or task operations,
Transcribe batch jobs or streaming calls, Secrets Manager additions, S3 writes,
and every CAD, dispatch, alert, paging, radio, or ESInet action.

## Exact image deployment sequence after authorization

1. Keep the deployed voice package unchanged while building the immutable image.
2. Run local contracts and the relevant full suite. Build exactly one tagged
   image from the reviewed source hash, require a complete zero-finding ECR scan,
   and record its immutable digest.
3. Review an IaC diff containing only the Transcribe permission removal, reviewed
   Polly environment values, task-definition digest, and ECS pointer. Any CAD,
   secret, database, network, storage, or unrelated IAM change fails the gate.
4. Deploy the exact digest, wait for one healthy task, and verify TTS controls
   match provider readiness while Transcribe controls remain disabled. Confirm no
   managed-service call occurs merely from startup, status checks, or page loads.
5. Perform no Polly request until the separate one-time authorization below is
   recorded. Roll back to the prior task revision on any failed gate.

## Exact one-time Polly smoke-test authorization

The smoke test remains prohibited until a named human authorizes all of the
following in one time-bounded release window:

- account `862772137583`, region `us-east-1`, the exact ECS task role, task
  revision, and immutable image digest;
- exactly one `polly:SynthesizeSpeech` request, with no Transcribe, Bedrock, CAD,
  dispatch, alert, paging, radio, or ESInet call;
- one fixed reviewed voice, `Joanna` or `Matthew`, neural engine, MP3 output;
- synthetic plain text of at most 100 characters, containing no CAD content,
  address, person, medical detail, secret, or operational identifier; the
  recommended phrase is `LCDash voice test. Nine one one.`;
- no retry, audio upload, audio persistence, transcript, prompt logging, or raw
  provider response logging;
- sanitized evidence limited to request ID, tenant ID, selected voice, elapsed
  time, byte count, HTTP result, and pass/fail pronunciation observation.

Before the request, require passing local and in-image contracts, a zero-finding
image scan, healthy ECS/ALB state, a reviewed CloudFormation diff, and confirmed
`tts.ready=true` with `stt.ready=false`. Stop and roll back to the recorded prior
digest on any timeout, non-200 response, empty or oversized audio, log leak,
unexpected AWS call, or service-health regression. Success is one decodable MP3
played once by the authorized reviewer with `911` pronounced "nine one one".
Afterward, review sanitized logs and delayed cost evidence. The authorization
expires after that single request and does not authorize general voice use.

This plan authorizes no AWS resource, IAM, model, voice request, document,
secret, deployment, commit, or push action.
