"""Environment-backed settings for the lightweight CPU voice pipeline."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AiConfig:
    """Configure local turn detection, ASR, LLM, and TTS providers.

    Attributes:
        asr_model: Faster Whisper model identifier.
        asr_language: ISO language hint supplied to transcription.
        ollama_url: Base URL of the local Ollama service.
        llm_model: Ollama model identifier used for replies.
        system_prompt: Instruction constraining telephone responses.
        tts_rate: System speech rate in words per minute.
        tts_voice: Optional substring used to select an installed system voice.
        tts_chunk_chars: Preferred maximum text length synthesized per phrase.
        speech_rms: Minimum frame RMS treated as speech.
        end_silence_ms: Silence required to complete an utterance.
        min_speech_ms: Minimum speech duration accepted for transcription.
        max_utterance_ms: Maximum buffered utterance duration.
    """

    asr_model: str = "tiny.en"
    asr_language: str = "en"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:0.6b"
    system_prompt: str = (
        "You are a professional telephone receptionist. Reply in one short sentence. "
        "Stay courteous, neutral, and focused on assisting the caller. "
        "Do not invent missing information. If the caller's request is incomplete "
        "or unclear, ask: How may I help you?"
    )
    tts_rate: int = 185
    tts_voice: str | None = None
    tts_chunk_chars: int = 48
    speech_rms: int = 450
    end_silence_ms: int = 700
    min_speech_ms: int = 300
    max_utterance_ms: int = 12_000

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AiConfig:
        """Create settings from ``WILDIX_AI_*`` environment variables.

        Args:
            environ: Source mapping. Uses ``os.environ`` when omitted.

        Returns:
            Validated AI example configuration.

        Raises:
            ValueError: If a numeric value is malformed or outside its valid range.
        """
        values = os.environ if environ is None else environ
        config = cls(
            asr_model=values.get("WILDIX_AI_ASR_MODEL", "tiny.en"),
            asr_language=values.get("WILDIX_AI_ASR_LANGUAGE", "en"),
            ollama_url=values.get("WILDIX_AI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            llm_model=values.get("WILDIX_AI_LLM_MODEL", "qwen3:0.6b"),
            system_prompt=values.get(
                "WILDIX_AI_SYSTEM_PROMPT",
                "You are a professional telephone receptionist. Reply in one short sentence. "
                "Stay courteous, neutral, and focused on assisting the caller. "
                "Do not invent missing information. If the caller's request is incomplete "
                "or unclear, ask: How may I help you?",
            ),
            tts_rate=_read_int(values, "WILDIX_AI_TTS_RATE", 185),
            tts_voice=values.get("WILDIX_AI_TTS_VOICE") or None,
            tts_chunk_chars=_read_int(values, "WILDIX_AI_TTS_CHUNK_CHARS", 48),
            speech_rms=_read_int(values, "WILDIX_AI_SPEECH_RMS", 450),
            end_silence_ms=_read_int(values, "WILDIX_AI_END_SILENCE_MS", 700),
            min_speech_ms=_read_int(values, "WILDIX_AI_MIN_SPEECH_MS", 300),
            max_utterance_ms=_read_int(values, "WILDIX_AI_MAX_UTTERANCE_MS", 12_000),
        )
        config._validate()
        return config

    def _validate(self) -> None:
        """Validate numeric limits used by the real-time detector.

        Returns:
            ``None`` when all values are usable.

        Raises:
            ValueError: If any duration, threshold, or speech rate is invalid.
        """
        positive = {
            "tts_rate": self.tts_rate,
            "tts_chunk_chars": self.tts_chunk_chars,
            "speech_rms": self.speech_rms,
            "end_silence_ms": self.end_silence_ms,
            "min_speech_ms": self.min_speech_ms,
            "max_utterance_ms": self.max_utterance_ms,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"AI configuration values must be positive: {', '.join(invalid)}")
        if self.min_speech_ms >= self.max_utterance_ms:
            raise ValueError("WILDIX_AI_MIN_SPEECH_MS must be below MAX_UTTERANCE_MS")


def _read_int(values: Mapping[str, str], name: str, default: int) -> int:
    """Read one integer from an environment mapping.

    Args:
        values: Environment-style key/value mapping.
        name: Variable name to retrieve.
        default: Value used when the variable is absent.

    Returns:
        Parsed integer value.

    Raises:
        ValueError: If the configured value is not an integer.
    """
    try:
        return int(values.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
