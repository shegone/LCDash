# Active Calls Parity Release Result — 2026-08-05

## Result

The bounded Active Calls display-parity release completed successfully in the
authorized LCDash AWS pilot. Production `.227`, PC `.15`, CAD configuration and
credentials, backups, and operational-output controls were not changed.

The cloud CAD integration remains read-only. The release adds only normalized,
allowlisted presentation fields: location label, latest timestamped call status,
incident description, and assigned-unit number/type/agency/status. It does not
add acknowledgement, dispatch, alert, page, tone, call-update, webhook, or other
write operations. Raw CAD payloads are not persisted or logged.

## Immutable release evidence

- Repository: `lcdash-p1-logan-use1-web`
- Release build: `lcdash-p1-logan-use1-release-builder:006009d5-d1aa-4bbf-847a-1879ef3ee940`
- Release tag: `release-9aa804755177`
- Deployed digest: `sha256:a1c471d414bf1260531c08ab632b3ef95d39586697e361e54d7948c84d535847`
- ECR basic scan: `COMPLETE`, zero findings at every severity
- Exact-image contract run: `lcdash-p1-logan-use1-release-builder:fed184e0-a1a4-44aa-98ce-39d10fc9468e`, `SUCCEEDED`
- Exact-image assertions: normalization, latest timestamped status, approved
  location and assigned-unit fields, output whitelist, forbidden-operation
  absence, and safe card/detail template contracts

## Deployment evidence

- Stack: `lcdash-p1-logan-use1-foundation`
- Change set: `active-calls-parity-20260805`
- Reviewed changes: replacement of `AWS::ECS::TaskDefinition` caused by
  `PilotImageDigest`, and non-replacement update of `AWS::ECS::Service` to the
  new task definition; no other resource changes
- Final CloudFormation state: `UPDATE_COMPLETE`
- Active task definition: `lcdash-p1-logan-use1-web:16`
- ECS service: desired `1`, running `1`, pending `0`, failed tasks `0`, rollout
  `COMPLETED`
- Load-balancer target: `healthy`
- Error-log check during the release window: zero events matching `ERROR`,
  `Exception`, or `Traceback`

## Authenticated functional verification

An existing authenticated session was used for read-only visual verification.
The dashboard showed live cards with the approved address/location label,
incident description, latest call status, and assigned-unit numbers. A call
detail page opened successfully and displayed only the normalized read-only
summary and assigned-unit fields. No control that acknowledges, dispatches,
alerts, pages, triggers tones, or changes CAD state was exercised or introduced.

No incident values or raw CAD payloads are reproduced in this evidence file.

## Rollback reference

The immediately previous immutable digest remains present in ECR:

`sha256:b84a677dac301b34eb5b9f977f1b9b2a87cdc942fe2e95ec089f3cc0b2bbab79`

Rollback is a reviewed foundation-stack update that changes only
`PilotImageDigest` back to that exact digest, confirms the generated change set
contains only the task-definition replacement and ECS-service pointer, executes
the change set, and waits for ECS and the load-balancer target to become healthy.
Do not use a mutable tag for rollback.
