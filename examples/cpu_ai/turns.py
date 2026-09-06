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


class TextChunker:
    """Split streaming model text into short, speakable TTS phrases."""

    def __init__(self, target_chars: int) -> None:
        """Create an empty phrase buffer.

        Args:
            target_chars: Preferred maximum phrase length before a word-boundary split.

        Raises:
            ValueError: If ``target_chars`` is not positive.
        """
        if target_chars <= 0:
            raise ValueError("target_chars must be positive")
        self._target_chars = target_chars
        self._buffer = ""

    def feed(self, fragment: str) -> list[str]:
        """Add one model fragment and return all complete phrases.

        Args:
            fragment: Incremental text emitted by the language model.

        Returns:
            Complete phrases ready for immediate speech synthesis.
        """
        self._buffer += fragment
        return self._extract_ready_phrases()

    def flush(self) -> str | None:
        """Return remaining text after the model stream ends.

        Returns:
            Final stripped phrase, or ``None`` when the buffer is empty.
        """
        phrase = self._buffer.strip()
        self._buffer = ""
        return phrase or None

    def _extract_ready_phrases(self) -> list[str]:
        """Remove all currently speakable phrases from the internal buffer.

        Returns:
            Ordered phrases ending at punctuation or a configured size boundary.
        """
        phrases: list[str] = []
        while boundary := self._next_boundary():
            phrase = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if phrase:
                phrases.append(phrase)
        return phrases

    def _next_boundary(self) -> int | None:
        """Locate the next natural or size-based phrase boundary.

        Returns:
            Exclusive character offset, or ``None`` when more text is needed.
        """
        for index, character in enumerate(self._buffer):
            if character in ".!?\n":
                return index + 1
            if character in ",;:" and index + 1 >= self._target_chars // 2:
                return index + 1
        if len(self._buffer) < self._target_chars:
            return None
        split_at = self._buffer.rfind(" ", 0, self._target_chars + 1)
        return split_at if split_at > 0 else self._target_chars
