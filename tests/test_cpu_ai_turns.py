"""Unit tests for the lightweight example's end-of-speech detector."""

from __future__ import annotations

from examples.cpu_ai.config import AiConfig
from examples.cpu_ai.turns import TextChunker, TurnDetector
from wildix_media import AudioFrame


def test_turn_detector_emits_speech_after_trailing_silence() -> None:
    """Verify that sufficient speech followed by silence completes one utterance."""
    detector = TurnDetector(
        AiConfig(speech_rms=400, min_speech_ms=200, end_silence_ms=400)
    )
    result: bytes | None = None
    for index in range(35):
        amplitude = 1_000 if index < 15 else 0
        pcm = amplitude.to_bytes(2, "little", signed=True) * 160
        result = detector.feed(AudioFrame(pcm=pcm, timestamp=index * 160)) or result

    assert result is not None
    assert len(result) == 35 * 320


def test_turn_detector_discards_short_noise() -> None:
    """Verify that a brief loud impulse does not create an ASR utterance."""
    detector = TurnDetector(
        AiConfig(speech_rms=400, min_speech_ms=300, end_silence_ms=200)
    )
    frames = [1_000] * 5 + [0] * 10

    results = [
        detector.feed(
            AudioFrame(
                pcm=value.to_bytes(2, "little", signed=True) * 160,
                timestamp=index * 160,
            )
        )
        for index, value in enumerate(frames)
    ]

    assert all(result is None for result in results)


def test_text_chunker_emits_complete_sentence_immediately() -> None:
    """Verify punctuation releases a phrase before the model stream ends."""
    chunker = TextChunker(target_chars=48)

    assert chunker.feed("Good morning") == []
    assert chunker.feed(". How may") == ["Good morning."]
    assert chunker.flush() == "How may"


def test_text_chunker_limits_unpunctuated_text_at_word_boundary() -> None:
    """Verify long text can reach TTS without waiting for final punctuation."""
    chunker = TextChunker(target_chars=12)

    phrases = chunker.feed("one two three four")

    assert phrases == ["one two"]
    assert chunker.flush() == "three four"
