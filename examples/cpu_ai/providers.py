"""Minimal local ASR, Ollama LLM, and operating-system TTS adapters."""

from __future__ import annotations

import asyncio
import audioop
import json
import os
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Any

from .config import AiConfig


class FasterWhisperAsr:
    """Transcribe telephone PCM using Faster Whisper in CPU int8 mode."""

    def __init__(self, model_name: str, language: str) -> None:
        """Load a reusable Faster Whisper model.

        Args:
            model_name: Whisper model identifier, normally ``tiny.en``.
            language: Language hint supplied to every transcription.

        Raises:
            ImportError: If the optional AI dependencies are not installed.
        """
        try:
            import numpy
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ImportError('Install AI dependencies with: pip install -e ".[ai]"') from exc
        self._numpy = numpy
        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")
        self._language = language or None

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Convert one utterance from PCM to text without blocking the event loop.

        Args:
            pcm: Signed 16-bit mono PCM.
            sample_rate: PCM sample rate in Hz.

        Returns:
            Concatenated transcription text, or an empty string when no speech is recognized.
        """
        return await asyncio.to_thread(self._transcribe_sync, pcm, sample_rate)

    def _transcribe_sync(self, pcm: bytes, sample_rate: int) -> str:
        """Run synchronous model inference for one utterance.

        Args:
            pcm: Signed 16-bit mono PCM.
            sample_rate: PCM sample rate in Hz.

        Returns:
            Normalized transcription text.
        """
        pcm_16k = _resample_pcm(pcm, sample_rate, 16_000)
        audio = self._numpy.frombuffer(pcm_16k, dtype=self._numpy.int16)
        audio = audio.astype(self._numpy.float32) / 32768.0
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class OllamaChat:
    """Generate concise replies through Ollama's local HTTP chat endpoint."""

    def __init__(self, config: AiConfig) -> None:
        """Create a per-call conversation.

        Args:
            config: Ollama endpoint, model, and system instruction.
        """
        self._url = f"{config.ollama_url}/api/chat"
        self._model = config.llm_model
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": config.system_prompt}
        ]

    async def reply(self, user_text: str) -> str:
        """Generate and retain one conversational response.

        Args:
            user_text: Recognized caller utterance.

        Returns:
            Assistant response text suitable for TTS.

        Raises:
            RuntimeError: If Ollama returns a malformed or empty response.
            OSError: If the local Ollama endpoint cannot be reached.
        """
        self._messages.append({"role": "user", "content": user_text})
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages[-7:],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.0, "num_predict": 64},
        }
        response = await asyncio.to_thread(_post_json, self._url, payload)
        message = response.get("message")
        text = message.get("content", "").strip() if isinstance(message, dict) else ""
        if not text:
            raise RuntimeError("Ollama returned an empty response")
        self._messages.append({"role": "assistant", "content": text})
        return text


class SystemTts:
    """Synthesize replies with the operating system's offline speech engine."""

    def __init__(self, *, rate: int, voice: str | None) -> None:
        """Create a serialized system-TTS adapter.

        Args:
            rate: Desired speech rate in words per minute.
            voice: Optional case-insensitive substring matching an installed voice.
        """
        self._rate = rate
        self._voice = voice
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        """Render text and normalize it to call-compatible PCM.

        Args:
            text: Assistant reply to speak.
            sample_rate: Required output sample rate in Hz.

        Returns:
            Signed 16-bit mono PCM bytes.

        Raises:
            RuntimeError: If the speech engine does not produce audio.
            ImportError: If ``pyttsx3`` is not installed.
        """
        async with self._lock:
            return await asyncio.to_thread(self._synthesize_sync, text, sample_rate)

    def _synthesize_sync(self, text: str, sample_rate: int) -> bytes:
        """Run the blocking system speech engine and decode its WAV output.

        Args:
            text: Assistant reply to render.
            sample_rate: Required output sample rate in Hz.

        Returns:
            Signed 16-bit mono PCM bytes.
        """
        try:
            import pyttsx3
        except ImportError as exc:
            raise ImportError('Install AI dependencies with: pip install -e ".[ai]"') from exc
        file_descriptor, filename = tempfile.mkstemp(suffix=".wav", dir=Path.cwd())
        os.close(file_descriptor)
        path = Path(filename)
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", self._rate)
            _select_voice(engine, self._voice)
            engine.save_to_file(text, str(path))
            engine.runAndWait()
            engine.stop()
            return _read_wave_pcm(path, sample_rate)
        finally:
            path.unlink(missing_ok=True)


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Post JSON to a local HTTP endpoint.

    Args:
        url: Complete endpoint URL.
        payload: JSON-serializable request object.

    Returns:
        Parsed JSON response object.

    Raises:
        OSError: If the endpoint cannot be reached.
        RuntimeError: If the response is not a JSON object.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("Ollama returned a non-object JSON response")
    return decoded


def _select_voice(engine: Any, requested_voice: str | None) -> None:
    """Select the first installed voice matching a configured substring.

    Args:
        engine: Initialized ``pyttsx3`` engine.
        requested_voice: Optional voice name or identifier fragment.

    Returns:
        ``None`` after retaining the default or selecting a match.
    """
    if not requested_voice:
        return
    needle = requested_voice.casefold()
    for voice in engine.getProperty("voices"):
        searchable = f"{voice.id} {voice.name}".casefold()
        if needle in searchable:
            engine.setProperty("voice", voice.id)
            return


def _read_wave_pcm(path: Path, target_rate: int) -> bytes:
    """Read a speech-engine WAV file as mono 16-bit PCM.

    Args:
        path: WAV file created by the operating system speech engine.
        target_rate: Required output sample rate in Hz.

    Returns:
        Normalized PCM bytes.

    Raises:
        RuntimeError: If the speech engine produced no samples or unsupported channels.
    """
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("The system speech engine produced no audio file")
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        source_rate = source.getframerate()
        pcm = source.readframes(source.getnframes())
    if not pcm:
        raise RuntimeError("The system speech engine produced an empty WAV file")
    pcm = audioop.lin2lin(pcm, sample_width, 2) if sample_width != 2 else pcm
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    elif channels != 1:
        raise RuntimeError(f"Unsupported TTS channel count: {channels}")
    return _resample_pcm(pcm, source_rate, target_rate)


def _resample_pcm(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Resample mono signed 16-bit PCM with the standard rate converter.

    Args:
        pcm: Source PCM bytes.
        source_rate: Current sample rate in Hz.
        target_rate: Desired sample rate in Hz.

    Returns:
        Resampled PCM, or the original bytes when rates already match.
    """
    if source_rate == target_rate:
        return pcm
    converted, _ = audioop.ratecv(pcm, 2, 1, source_rate, target_rate, None)
    return converted
