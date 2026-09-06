"""Small energy-based end-of-speech detector for telephone PCM frames."""

from __future__ import annotations

import audioop

from wildix_media import AudioFrame

from .config import AiConfig


class TurnDetector:
    """Collect speech frames until trailing silence or a duration limit is reached."""

    def __init__(self, config: AiConfig) -> None:
        """Create an empty detector.

        Args:
            config: Thresholds and duration limits used to segment speech.
        """
        self._config = config
        self._buffer = bytearray()
        self._speech_ms = 0.0
        self._silence_ms = 0.0

    def feed(self, frame: AudioFrame) -> bytes | None:
        """Consume one PCM frame and possibly complete an utterance.

        Args:
            frame: Incoming signed 16-bit mono PCM frame.

        Returns:
            Completed PCM utterance, or ``None`` while more audio is required.
        """
        is_speech = audioop.rms(frame.pcm, 2) >= self._config.speech_rms
        if not self._buffer and not is_speech:
            return None
        self._buffer.extend(frame.pcm)
        if is_speech:
            self._speech_ms += frame.duration_ms
            self._silence_ms = 0.0
        else:
            self._silence_ms += frame.duration_ms
        reached_silence = self._silence_ms >= self._config.end_silence_ms
        reached_limit = self._duration_ms(frame.sample_rate) >= self._config.max_utterance_ms
        return self._finish() if reached_silence or reached_limit else None

    def flush(self) -> bytes | None:
        """Complete any valid speech remaining when a call closes.

        Returns:
            Buffered PCM when it meets the minimum speech duration, otherwise ``None``.
        """
        return self._finish()

    def _duration_ms(self, sample_rate: int) -> float:
        """Return the duration currently buffered.

        Args:
            sample_rate: Number of mono PCM samples per second.

        Returns:
            Buffered duration in milliseconds.
        """
        return len(self._buffer) // 2 * 1000 / sample_rate

    def _finish(self) -> bytes | None:
        """Return a valid utterance and reset detector state.

        Returns:
            Buffered PCM if enough speech was detected, otherwise ``None``.
        """
        utterance = bytes(self._buffer) if self._speech_ms >= self._config.min_speech_ms else None
        self._buffer.clear()
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        return utterance
