# Cloud AI, knowledge, and voice local release readiness

Status: **LOCAL RELEASE CANDIDATE READY FOR REVIEW - NO DEPLOYMENT OR PROVIDER CALL AUTHORIZED**.

This package joins the completed citation-only knowledge/RAG boundary and the
prepared Polly text-to-speech boundary into one release review. It authorizes no
AWS change, IAM change, ingestion, deployment, provider request, CAD access,
credential access, commit, or push.

## Presentation contract

- MAE is advisory-only and must fail closed when approved document retrieval is
  unavailable. Answers require approved citations and expose no action tools.
- Knowledge is a read-only approved-document library. `NOT INGESTED` is shown
  until the explicit document gate is complete; local document counts must not
  be presented as proof of cloud ingestion.
- Voice reports text-to-speech and speech-to-text independently. Polly TTS may
  be ready while Transcribe remains disabled. MAE conversational microphone mode
  requires both and stays disabled otherwise.
- No CAD, dispatch, alert, paging, station-tone, radio, ESInet, call-routing, or
  operational-control capability is granted by AI, knowledge, or voice readiness.

## Deployment authorization gate

A named human approver must record the account `862772137583`, region
`us-east-1`, release window, source revision, immutable image digest, exact ECS
task revision and task role, prior rollback digest/revision, and test evidence.
Authorization is invalid unless all of these checks pass:

1. Focused cloud AI, retrieval, Polly, presentation, and route contracts pass
   locally and in the candidate image without network access during startup,
   status checks, or page rendering.
2. The image scan has zero actionable findings and the reviewed image digest is
   the digest deployed. Tags alone are insufficient.
3. The infrastructure diff is limited to the reviewed task definition, image
   digest, cloud AI environment values, and the task-role Polly statement.
   `polly:SynthesizeSpeech` on `*` must carry
   `aws:RequestedRegion=us-east-1`; no Transcribe permission is allowed.
4. There are no CAD, database, secret, network, Cognito, storage, backup, alert,
   paging, station-tone, radio, ESInet, source-document, or knowledge-stack
   changes.
5. After deployment, exactly one task is healthy, ALB health passes, page/status
   requests make no provider call, `tts.ready=true`, `stt.ready=false`, and MAE
   conversational microphone mode remains disabled.

Any mismatch, unexpected permission, provider call, log disclosure, failed
health check, or UI readiness overclaim cancels the authorization and requires
rollback to the recorded prior revision and digest.

## Separate one-call Polly smoke authorization

Deployment approval does not authorize speech synthesis. A named human approver
must separately authorize exactly one `polly:SynthesizeSpeech` request in the
same bounded window for the exact account, region, task role, task revision, and
immutable image digest.

- Use one fixed reviewed voice: `Joanna` or `Matthew`; engine `neural`; format
  `mp3`; plain text at most 100 characters.
- Use only: `LCDash voice test. Nine one one.`
- No retry, upload, persistence, transcript, prompt logging, raw response
  logging, or second playback.
- No `Retrieve`, `Converse`, `InvokeModel`, or Transcribe call may occur.
- Record only sanitized request ID, tenant ID, voice, elapsed time, byte count,
  HTTP result, and a pass/fail pronunciation observation.
- Stop on timeout, non-200 result, empty/oversized/undecodable audio, unexpected
  AWS call, sensitive logging, health regression, or incorrect pronunciation.

Success is one decodable MP3, played once by the authorized reviewer, with
`911` pronounced "nine one one." The authorization expires immediately after
that request whether it passes or fails. It does not authorize general voice
use, knowledge retrieval, ingestion, generation, transcription, or operational
integration.

## Evidence to retain

Retain the reviewed source revision and diff, focused test output, candidate
image digest and scan result, synthesized infrastructure diff, exact task role
policy, pre/post health evidence, rollback target, authorization record, and the
sanitized one-call result. Never retain raw audio, source text, prompts,
retrieved passages, model responses, CAD content, credentials, or operational
identifiers in release evidence.
