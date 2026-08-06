# PC .227 Ollama exposure evidence and non-destructive options

Date: 2026-08-06
Scope: read-only production verification; no configuration or service changes

## Verified evidence

An authenticated, non-interactive SSH check used the documented
`administrator@14.1.1.227` access path and existing workstation key. The host
identified itself as `lcdash-server`. Docker and SSH were active, the host had
been up for more than six days, and system load was low.

`docker compose ps -a` in `/srv/lcdash-platform/current` reported the Ollama
service running with this published binding:

```text
14.1.1.227:11434->11434/tcp
```

The same read-only check confirmed the expected local model inventory through
`ollama list`. No prompt, generation, embedding, CAD, or operational request was
sent.

LCDash, Open WebUI, and Open WebUI Computer loopback health endpoints returned
HTTP 200. Core database, speech, and application containers reported healthy.

## Documentation discrepancy

`docs/SERVER_DEPLOYMENT.md` states that Ollama is not published to the host
network. The verified Compose state publishes it on the production host's
`14.1.1.227` interface. Later PC .15 coordination notes describe a protected
.227 model gateway, so the binding may be intentional, but the current evidence
does not establish an authentication layer or network allowlist for port 11434.

This is a configuration-intent discrepancy, not evidence that unauthorized
access occurred. Port reachability and API access were deliberately not probed
outside the authenticated host session.

## Decisions required before remediation

1. Confirm whether PC `.15` or another approved client currently depends on
   `14.1.1.227:11434` directly.
2. Identify the intended authentication and source-network boundary for that
   gateway.
3. Confirm a maintenance window and rollback owner. Ollama supports production
   MAE and Open WebUI, so an uncoordinated binding change could interrupt local
   AI while ordinary LCDash and CAD functions must remain unaffected.

## Non-destructive remediation options

### Option A: restore loopback/private-container-only access

Remove the LAN-published port and keep Ollama reachable only through the private
Compose network. This best matches the older deployment documentation and
preserves Open WebUI-to-Ollama communication. Choose it only after confirming
that PC `.15` does not require the direct gateway or has a replacement path.

### Option B: retain the binding with a host-firewall source allowlist

Keep the existing port mapping but permit only explicitly approved source
addresses, such as the separately documented PC `.15` address. Deny other LAN
sources and document the exception. This limits exposure but does not add
application-layer authentication.

### Option C: place an authenticated gateway in front of Ollama

Return Ollama itself to the private network and expose only a narrowly scoped,
authenticated proxy for approved remote model clients. Prefer short-lived or
rotatable credentials, request-size and timeout limits, source allowlisting,
and logs that exclude prompt content. This is the strongest option when remote
model access is required, but it is a separate designed change and must not be
improvised in production.

### Option D: use an approved private tunnel for the remote client

Keep Ollama private and provide PC `.15` access through an authenticated tunnel
or private overlay with explicit peer identity. This avoids a general LAN
listener but adds tunnel operations and monitoring responsibilities.

## Safe verification after an authorized change

During a separately approved maintenance window:

- record the pre-change Compose binding and service health;
- apply only the selected network-boundary change;
- verify LCDash, Open WebUI, and Ollama health without sending a model prompt;
- verify the approved client path and confirm unapproved source paths are denied;
- verify CAD, station-alert tones, backups, and operational outputs were not
  modified or made dependent on the AI gateway; and
- restore the recorded prior binding if local AI health or the approved client
  path fails.

No remediation was performed as part of this evidence collection.
