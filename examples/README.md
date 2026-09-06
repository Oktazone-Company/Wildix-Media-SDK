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

## `cpu_ai_pipeline.py`

The smallest fully local AI path uses Faster Whisper `tiny.en` on CPU/int8, Ollama
`qwen3:0.6b`, and the operating system's installed speech voice:

```powershell
ollama pull qwen3:0.6b
ollama serve
.\.venv\Scripts\python.exe -m pip install -e ".[ai]"
.\.venv\Scripts\python.exe .\examples\cpu_ai_pipeline.py
```

The first run downloads the Whisper model. Configure provider and turn-detection values
with the `WILDIX_AI_*` entries in `.env.example`. The executable imports its AI
implementation from `examples/cpu_ai/`. Its named streaming stages consume RTP,
transcribe finalized speech turns, stream Ollama text, synthesize bounded phrases, and
send PCM frames as soon as each phrase is ready. See `cpu_ai/README.md` for extension
examples and the native-streaming limitations of the bundled CPU providers.

## Application Integration

Production applications should import the package and provide their own call handler.
Keep turn detection, ASR, model orchestration, and TTS in application modules that depend
on the SDK's `MediaCall` interface.
