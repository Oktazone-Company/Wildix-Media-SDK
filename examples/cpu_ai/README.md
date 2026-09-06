# Lightweight CPU Voice Pipeline

This example connects the Wildix media SDK to a fully local, turn-based AI pipeline:

```text
Wildix RTP -> speech segmentation -> Faster Whisper tiny.en
           -> Ollama qwen3:0.6b -> Windows TTS -> Wildix RTP
```

It is intended as the smallest practical AI demonstration. It uses CPU/int8 ASR, a
compact 523 MB language model, and an installed Windows voice rather than a separate TTS
model. The first ASR run downloads the `tiny.en` model into the Hugging Face cache.

## Requirements

- Windows 10 or later
- Python 3.11 or 3.12
- A working Wildix/Localtonet SIP and RTP route
- [Ollama for Windows](https://ollama.com/download/windows)

Test `examples/echo.py` first. A working echo proves that SIP, RTP, codec negotiation,
and the tunnel are correct before AI latency is introduced.

## 1. Install Python Dependencies

Run these commands from the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[ai]"
```

If PowerShell blocks activation, the interpreter can be called directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ai]"
```

## 2. Install and Prepare Ollama

Install Ollama from its Windows installer, then open a new PowerShell window. Ollama's
Windows application normally runs its API in the background on `127.0.0.1:11434`.

```powershell
ollama pull qwen3:0.6b
ollama list
```

Verify the local API:

```powershell
$body = @{ model = "qwen3:0.6b"; prompt = "Reply with OK"; stream = $false; think = $false } | ConvertTo-Json
(Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/generate" -ContentType "application/json" -Body $body).response
```

The response should contain `OK`. If the desktop application is not running, start it
from the Windows Start menu or run `ollama serve` in a separate PowerShell window.

## 3. Configure `.env`

Copy `.env.example` to `.env` and replace the public host and port with the active
Localtonet endpoint. Resolve the endpoint hostname to an IPv4 address for SDP:

```powershell
[System.Net.Dns]::GetHostAddresses("YOUR-TUNNEL.localto.net") |
    ForEach-Object { $_.IPAddressToString }
```

Use this template:

```dotenv
WILDIX_MEDIA_SIP_HOST=127.0.0.1
WILDIX_MEDIA_SIP_PORT=5060
WILDIX_MEDIA_SIP_TRANSPORT=tcp

WILDIX_MEDIA_RTP_HOST=127.0.0.1
WILDIX_MEDIA_RTP_PORT_START=5060
WILDIX_MEDIA_RTP_PORT_END=5060

WILDIX_MEDIA_ADVERTISED_SIP_HOST=YOUR_RESOLVED_PUBLIC_IPV4
WILDIX_MEDIA_ADVERTISED_SIP_PORT=YOUR_PUBLIC_PORT
WILDIX_MEDIA_ADVERTISED_RTP_HOST=YOUR_RESOLVED_PUBLIC_IPV4
WILDIX_MEDIA_ADVERTISED_RTP_PORT_START=YOUR_PUBLIC_PORT

WILDIX_MEDIA_CODECS=PCMA,PCMU
WILDIX_MEDIA_PTIME_MS=20
WILDIX_MEDIA_AUDIO_QUEUE_FRAMES=250
WILDIX_MEDIA_SYMMETRIC_RTP=true

WILDIX_AI_ASR_MODEL=tiny.en
WILDIX_AI_ASR_LANGUAGE=en
WILDIX_AI_OLLAMA_URL=http://127.0.0.1:11434
WILDIX_AI_LLM_MODEL=qwen3:0.6b
WILDIX_AI_SYSTEM_PROMPT=You are a professional telephone receptionist. Reply in one short sentence. Never make personal, romantic, affectionate, or emotional statements. Never say I love you. If the caller's request is incomplete or unclear, ask: How may I help you?
WILDIX_AI_TTS_RATE=185
WILDIX_AI_TTS_VOICE=
WILDIX_AI_SPEECH_RMS=450
WILDIX_AI_END_SILENCE_MS=700
WILDIX_AI_MIN_SPEECH_MS=300
WILDIX_AI_MAX_UTTERANCE_MS=12000
```

PowerShell environment variables override matching values from `.env`. Open a fresh
PowerShell window if it may contain stale `$env:WILDIX_MEDIA_*` values.

## 4. Configure the Route

For the tested one-port topology:

- Localtonet protocol: `UDP_TCP`
- Localtonet client address: `127.0.0.1`
- Localtonet client port: `5060`
- Wildix trunk host: the Localtonet hostname, not the resolved IP
- Wildix trunk port: the Localtonet public port
- Wildix transport: `TCP`
- Wildix codecs: `alaw`, followed by `ulaw`

The one-port configuration supports one active media call. Use a public UDP port range
and a matching SDK RTP range for concurrent production calls.

## 5. Run the AI Example

Stop `examples/echo.py` first so only one process owns local port `5060`, then run:

```powershell
cd "D:\path\to\Wildix-Media-SDK"
.\.venv\Scripts\python.exe -m examples.cpu_ai
```

Expected startup output:

```text
SIP media server listening on 127.0.0.1:5060/tcp with RTP ports 5060-5060/udp
```

Call the configured Wildix number, speak for at least one second, then pause for about
one second. A successful turn produces logs similar to:

```text
AI call ... caller: What time do you close?
AI call ... assistant: We close at five o'clock.
```

## Troubleshooting

- `RTP ports 10000-10100`: `.env` was not loaded or stale PowerShell variables override it.
- `WinError 10048`: another server or call already owns the single local UDP port.
- `0 frames`: SIP connected, but RTP did not reach `127.0.0.1:5060/udp`.
- `WinError 1231`: the remote SDP address is unreachable; confirm symmetric RTP and the tunnel.
- Ollama connection refused: start Ollama and verify `http://127.0.0.1:11434`.
- No reply after speech: lower `WILDIX_AI_SPEECH_RMS` gradually, for example from `450` to `300`.
- False speech detection: increase `WILDIX_AI_SPEECH_RMS` gradually.

This demonstration intentionally omits streaming ASR, barge-in, RAG, tools, and call
transfer. Those concerns belong in a production agent layer built above this SDK.
