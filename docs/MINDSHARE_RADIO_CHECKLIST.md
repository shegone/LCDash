# Mindshare Radio Intelligence Connection Checklist

The Radio Intelligence module remains inactive until this checklist is
completed at the center. The first connection is listen-only.

## Required equipment information

- Computer model, CPU, RAM, storage, and GPU model
- Proposed operating system
- Dedicated network-interface make, model, and MAC address
- Mindshare radio-network IP address, subnet, and gateway requirements
- Whether the switch port uses an untagged network or a VLAN
- Switch IGMP snooping and multicast-router configuration

## Required Mindshare information

- Channel display name
- Multicast group address
- UDP port
- Audio codec and sample rate
- Packet format or gateway source
- Whether separate transmit and receive streams exist
- Any control, metadata, or PTT-identification stream
- Expected packet rate
- Approved test channel and maintenance window

## Listen-only validation

1. Disable routing and forwarding between the operations network and the radio
   network.
2. Connect only the dedicated radio-network interface.
3. Confirm the host does not transmit application traffic on that interface.
4. Capture packet headers on one approved multicast group.
5. Confirm source, destination, port, codec, and packet timing.
6. Decode one test stream locally without saving or retransmitting audio.
7. Verify that console and radio operations are unaffected.

## Governance decisions before transcription

- Authorized users
- Live-only versus retained audio
- Transcription retention
- Incident and channel access restrictions
- Audit logging
- CJIS, agency-policy, public-records, and legal review
- Notification when transcription is unavailable or uncertain

MAE/JACK voice output, JACK technical assistance, and Mindshare radio
transcription remain separate services and permissions.
