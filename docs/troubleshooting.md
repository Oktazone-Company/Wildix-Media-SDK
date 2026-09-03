# Troubleshooting

## Server Listens but No Call Appears

Check whether another process owns the target TCP or UDP port:

```powershell
Get-NetTCPConnection -LocalPort 5060 -State Listen
Get-NetUDPEndpoint -LocalPort 5060
```

Confirm that the Wildix trunk transport matches `WILDIX_MEDIA_SIP_TRANSPORT`. A log from
another SIP process showing `transport=UDP` means a TCP-only SDK listener cannot receive
that call.

## Call Connects but No Recording Appears

The recording directory is created only after the SDK accepts a call. If the terminal
contains no `Call ... connected` line, signaling reached another process or endpoint.

If a connected line appears but no `first audio frame` line follows, inspect the SDP
advertised RTP host and port. They must match the active UDP tunnel endpoint.

## Header-Only or Silent Recording

A WAV file of approximately 44 bytes contains only its header. This means the call was
accepted but no decoded RTP frames arrived. Common causes are:

- An outdated advertised RTP port after a tunnel restart
- RTP forwarded to a different local port
- A firewall blocking UDP
- An unsupported codec offer
- Another process owning the RTP port

## One-Way Audio

Receiving a valid recording proves inbound RTP only. Hearing the echo proves outbound
RTP as well. With NAT or a tunnel, keep symmetric RTP enabled so the SDK can latch to the
actual source address.

## Tunnel Port Changed

Temporary tunnel services can assign a different public port after reconnecting. Update
all four advertised settings before restarting the SDK:

```text
ADVERTISED_SIP_HOST
ADVERTISED_SIP_PORT
ADVERTISED_RTP_HOST
ADVERTISED_RTP_PORT_START
```

Update the Wildix trunk host and port at the same time. Never leave the SDK advertising
an earlier tunnel endpoint.

## Useful Debug Command

```powershell
wildix-media-echo --debug --record-dir .\recordings
```

Debug logs may include network identifiers. Review them before sharing externally.
