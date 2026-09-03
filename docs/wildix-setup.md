# Wildix Setup

This guide describes direct inbound testing through a TCP/UDP tunnel. Field names can
vary slightly by Wildix release.

## Recommended One-Port Development Topology

Create one combined `TCP_UDP` tunnel:

```text
public-host.example.com:PUBLIC_PORT -> 127.0.0.1:5060
```

Use TCP for SIP signaling and UDP for RTP audio. TCP and UDP can share numeric port
`5060` because the operating system treats them as separate transports.

### SDK Environment

```powershell
$env:WILDIX_MEDIA_SIP_HOST = "127.0.0.1"
$env:WILDIX_MEDIA_SIP_PORT = "5060"
$env:WILDIX_MEDIA_SIP_TRANSPORT = "tcp"
$env:WILDIX_MEDIA_RTP_HOST = "127.0.0.1"
$env:WILDIX_MEDIA_RTP_PORT_START = "5060"
$env:WILDIX_MEDIA_RTP_PORT_END = "5060"
$env:WILDIX_MEDIA_ADVERTISED_SIP_HOST = "public-host.example.com"
$env:WILDIX_MEDIA_ADVERTISED_SIP_PORT = "PUBLIC_PORT"
$env:WILDIX_MEDIA_ADVERTISED_RTP_HOST = "public-host.example.com"
$env:WILDIX_MEDIA_ADVERTISED_RTP_PORT_START = "PUBLIC_PORT"

.\.venv\Scripts\python.exe .\examples\echo.py
```

The startup message must report SIP TCP on `5060` and RTP UDP on `5060-5060`.

### Wildix Trunk

Configure the direct test trunk with:

| Field | Value |
| --- | --- |
| Address or Host | Tunnel public hostname |
| Port | Tunnel public port |
| Enable registration | Disabled for direct static routing |
| Audio codecs | `alaw`, then `ulaw` |
| Packetization | 20 ms |
| Transport | TCP |
| DTMF mode | RFC 2833 |

Route a dedicated test number to **Dial the trunk**. Avoid routing a production DID
until the echo test is verified.

## Full UDP Topology

SIP and RTP cannot independently bind the same local UDP address and port. To use UDP
for SIP signaling, create separate tunnel endpoints:

```text
SIP: public-sip.example.com:SIP_PUBLIC_PORT -> 127.0.0.1:5060/udp
RTP: public-rtp.example.com:RTP_PUBLIC_PORT -> 127.0.0.1:10000/udp
```

Then use:

```powershell
$env:WILDIX_MEDIA_SIP_TRANSPORT = "udp"
$env:WILDIX_MEDIA_SIP_PORT = "5060"
$env:WILDIX_MEDIA_RTP_PORT_START = "10000"
$env:WILDIX_MEDIA_RTP_PORT_END = "10000"
$env:WILDIX_MEDIA_ADVERTISED_SIP_HOST = "public-sip.example.com"
$env:WILDIX_MEDIA_ADVERTISED_SIP_PORT = "SIP_PUBLIC_PORT"
$env:WILDIX_MEDIA_ADVERTISED_RTP_HOST = "public-rtp.example.com"
$env:WILDIX_MEDIA_ADVERTISED_RTP_PORT_START = "RTP_PUBLIC_PORT"
```

Set the Wildix trunk transport to UDP. This offers no RTP performance advantage over
the one-port topology because voice media uses UDP in both designs.

## Multiple Concurrent Calls

Expose a contiguous local and public RTP range with one-to-one port mapping. Configure
the first advertised public port with `ADVERTISED_RTP_PORT_START`; later ports are mapped
by offset. A tunnel that exposes only one UDP port supports one active call.

## Verification

1. Confirm only the intended SDK process owns the local listener ports.
2. Place a call and verify the `connected` log line.
3. Speak for several seconds and verify the `first audio frame` line.
4. Hang up and inspect `recordings/`.
5. Play the WAV and confirm the caller audio is intelligible.
6. Confirm the caller heard an echo during the call.
