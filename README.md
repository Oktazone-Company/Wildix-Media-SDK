# Wildix Media SDK

An asynchronous Python SDK for accepting SIP calls and exchanging bidirectional voice
media with Wildix or another standards-based SIP peer.

The SDK terminates SIP/SDP and RTP/G.711, then exposes ordinary signed PCM bytes to
application code. It deliberately stops at the media boundary: ASR, VAD, language
models, TTS, RAG, and business workflows remain independent application concerns.

```text
Wildix -> SIP/SDP -> RTP/G.711 -> 16-bit PCM -> application
Wildix <- SIP/SDP <- RTP/G.711 <- 16-bit PCM <- application
```

## Capabilities

- Incoming SIP calls over TCP or UDP
- SDP offer/answer negotiation
- PCMA (G.711 A-law) and PCMU (G.711 mu-law)
- Bidirectional RTP audio exposed as asynchronous PCM frames
- Public-to-local address mapping for NAT and tunnels
- Symmetric RTP, jitter buffering, packet-loss concealment, and RFC 2833 DTMF
- Bounded media queues that discard stale frames instead of increasing latency
- Call-scoped WAV recording utility
- A tested record-and-echo diagnostic server

The current `0.1.x` line is an alpha for direct inbound trunks and controlled networks.
SIP registration, digest authentication, TLS, SRTP, transfers, and outbound dialing are
not implemented. See [Architecture](docs/architecture.md) for the precise boundary.

## Repository Structure

```text
wildix-media-sdk/
|-- .github/workflows/       Continuous integration
|-- docs/                    Architecture, API, setup, and operations
|-- examples/                Small executable integrations
|-- src/wildix_media/        Installable SDK package
|-- tests/                   Unit and real loopback SIP/RTP tests
|-- .env.example             Configuration template
|-- pyproject.toml           Packaging and quality-tool configuration
`-- README.md                Project entry point
```

## Installation

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Linux and macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Minimal Application

```python
from wildix_media import MediaCall, MediaServer, ServerConfig

server = MediaServer(ServerConfig(sip_port=5060))


@server.on_call
async def handle_call(call: MediaCall) -> None:
    async for frame in call.audio_frames():
        # 8 kHz, mono, signed 16-bit little-endian PCM for G.711 calls.
        call.send_frame(frame.pcm)


server.run()
```

Use `call.send_frame()` for an already paced stream. Use `await call.play_pcm()` for a
complete PCM response that the SDK should packetize and pace. Returning from the call
handler ends the call; `await call.hangup()` ends it explicitly.

## Proven Tunnel Topology

The simplest one-port tunnel uses different transports on the same numeric port:

```text
Wildix SIP signaling -- TCP --> public-host:PUBLIC_PORT -- TCP --> localhost:5060
Wildix RTP audio     -- UDP --> public-host:PUBLIC_PORT -- UDP --> localhost:5060
```

This topology was validated with a combined TCP/UDP Localtonet tunnel. RTP audio is UDP;
only SIP signaling uses TCP. Full UDP operation is supported when SIP and RTP have
separate UDP tunnel endpoints. See [Wildix Setup](docs/wildix-setup.md).

Run the diagnostic application after exporting the settings from `.env.example`:

```powershell
.\.venv\Scripts\python.exe .\examples\echo.py
```

For each accepted call it:

1. Logs call establishment.
2. Logs arrival of the first decoded audio frame.
3. Saves incoming PCM to `recordings/<timestamp>_<call-id>.wav`.
4. Echoes decoded audio back through the negotiated codec.
5. Reports frame count, byte count, and recorded duration at hangup.

Hearing the echo and finding a non-empty WAV proves signaling, both RTP directions,
codec decoding, and codec encoding.

## Audio Contract

| Property | Value |
| --- | --- |
| Encoding | Signed linear PCM |
| Sample width | 16 bit |
| Byte order | Little-endian |
| Channels | 1 (mono) |
| Sample rate | 8000 Hz for PCMA/PCMU |
| Typical packet | 20 ms, 160 samples, 320 bytes |

## Documentation

- [Architecture and design decisions](docs/architecture.md)
- [Public API and audio lifecycle](docs/api.md)
- [Wildix and tunnel configuration](docs/wildix-setup.md)
- [Troubleshooting and diagnostics](docs/troubleshooting.md)
- [Development and verification](docs/development.md)
- [Runnable examples](examples/README.md)

## Verification

```powershell
ruff check .
ruff format --check .
mypy src
pytest
python -m build
```

The integration suite places a real loopback SIP call, negotiates PCMA, sends RTP in
both directions, and compares the decoded audio. It requires no Wildix account.

## Security

Do not expose this alpha listener to an unrestricted network. Public SIP ports are
routinely scanned. Restrict trusted source networks and read [SECURITY.md](SECURITY.md)
before evaluating the SDK outside a private development environment.

## License

Copyright OktaZone Company. Distributed under the [MIT License](LICENSE).
