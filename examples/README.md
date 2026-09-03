# Examples

## `echo.py`

The record-and-echo server is the smallest end-to-end media diagnostic:

```powershell
.\.venv\Scripts\python.exe .\examples\echo.py
```

It accepts calls using `ServerConfig.from_env()`, writes decoded incoming audio to
`recordings/`, and sends each frame back to the caller. It contains no AI provider or
business logic.

Use this example before integrating ASR or TTS. A successful echo separates telephony
transport correctness from higher-layer latency and provider behavior.

## Application Integration

Production applications should import the package and provide their own call handler.
Keep turn detection, ASR, model orchestration, and TTS in application modules that depend
on the SDK's `MediaCall` interface.
