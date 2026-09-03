# Public API

The stable application surface is exported from `wildix_media`. Lower-level protocol
objects from `aiosipua` are intentionally not exposed.

## `ServerConfig`

`ServerConfig` validates listener, RTP, codec, and public-address settings.

```python
from wildix_media import ServerConfig

config = ServerConfig.from_env()
```

For a combined TCP/UDP single-port tunnel:

```python
config = ServerConfig.for_tunnel(
    "public-host.example.com",
    12345,
    local_port=5060,
)
```

## `MediaServer`

`MediaServer` owns listeners and active call resources. Supply a handler to the
constructor or register one with `on_call`.

```python
from wildix_media import MediaCall, MediaServer

server = MediaServer(config)


@server.on_call
async def handle(call: MediaCall) -> None:
    await call.wait_closed()
```

For integration into another asyncio service, use `await server.start()` and
`await server.stop()`. For a standalone process, use `server.run()`.

## `MediaCall`

| Member | Contract |
| --- | --- |
| `id` | SIP Call-ID |
| `caller` | Caller SIP URI |
| `callee` | Called SIP URI |
| `sample_rate` | Negotiated decoded PCM rate |
| `audio_frames()` | Async iterator of decoded `AudioFrame` values |
| `dtmf_events()` | Async iterator of decoded RFC 2833 events |
| `send_frame(pcm)` | Immediately encode and send one PCM block |
| `play_pcm(pcm)` | Packetize and pace a complete PCM buffer |
| `send_dtmf(digit)` | Send an RFC 2833 keypad event |
| `hangup()` | End the call and release resources |
| `wait_closed()` | Wait for remote or local termination |

A call should have one consumer for `audio_frames()` and one for `dtmf_events()`.

## `AudioFrame`

`AudioFrame.pcm` contains mono signed 16-bit little-endian PCM. The frame also exposes
its incoming RTP timestamp, sample rate, sample count, and duration.

## `WaveRecorder`

`WaveRecorder` persists decoded frames while preserving their PCM format.

```python
from pathlib import Path

from wildix_media import WaveRecorder, call_recording_path

path = call_recording_path(Path("recordings"), call.id)
with WaveRecorder(path, sample_rate=call.sample_rate) as recorder:
    async for frame in call.audio_frames():
        recorder.write(frame)
```

The recorder finalizes the WAV header when its context exits, including when the call
handler is cancelled during teardown.

## Errors

All SDK-specific exceptions derive from `WildixMediaError`:

| Exception | Meaning |
| --- | --- |
| `ConfigurationError` | Invalid or contradictory network settings |
| `MediaCapacityError` | No RTP port is available for a new call |
| `CallClosedError` | An application attempted media I/O after teardown |
