"""Tests for the composable streaming ASR-to-LLM-to-TTS pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from examples.cpu_ai.config import AiConfig
from examples.cpu_ai.pipeline import StreamingVoicePipeline
from wildix_media import AudioFrame


class _FakeAsr:
    """Return deterministic text for each captured utterance."""

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Return a fixed transcription.

        Args:
            pcm: Captured PCM utterance.
            sample_rate: PCM sample rate in Hz.

        Returns:
            Fixed caller text when PCM is present.
        """
        return "hello" if pcm and sample_rate == 8_000 else ""


class _FakeConversation:
    """Emit two model fragments and record pipeline ordering."""

    def __init__(self, events: list[str]) -> None:
        """Store the shared event list.

        Args:
            events: Mutable list receiving stage events.
        """
        self._events = events

    async def stream_reply(self, user_text: str) -> AsyncIterator[str]:
        """Yield a deterministic two-fragment reply.

        Args:
            user_text: Caller transcription supplied by the pipeline.

        Yields:
            Two response fragments.
        """
        for fragment in ("Good ", "morning."):
            self._events.append(f"llm:{fragment}")
            yield fragment


class _FakeTts:
    """Convert each text fragment into one distinguishable PCM frame."""

    def __init__(self, events: list[str]) -> None:
        """Store the shared event list.

        Args:
            events: Mutable list receiving stage events.
        """
        self._events = events

    async def stream(
        self,
        text_stream: AsyncIterator[str],
        sample_rate: int,
    ) -> AsyncIterator[bytes]:
        """Yield one PCM frame as each text fragment arrives.

        Args:
            text_stream: Incremental model text.
            sample_rate: Required PCM sample rate in Hz.

        Yields:
            One short PCM frame per text fragment.
        """
        assert sample_rate == 8_000
        async for fragment in text_stream:
            self._events.append(f"tts:{fragment}")
            yield fragment.encode("utf-16-le")


class _FakeCall:
    """Provide incoming PCM and record outgoing playback calls."""

    id = "test-call"
    sample_rate = 8_000
    is_closed = False

    def __init__(self, frames: list[AudioFrame] | None = None) -> None:
        """Create a fake call.

        Args:
            frames: Optional incoming frames yielded to the ASR stage.
        """
        self._frames = frames or []
        self.played: list[bytes] = []

    async def audio_frames(self) -> AsyncIterator[AudioFrame]:
        """Yield configured incoming audio.

        Yields:
            Configured PCM frames in order.
        """
        for frame in self._frames:
            yield frame

    async def play_pcm(self, pcm: bytes) -> None:
        """Record one outgoing PCM frame.

        Args:
            pcm: Synthesized PCM frame.

        Returns:
            ``None`` after recording the frame.
        """
        self.played.append(pcm)


@pytest.mark.asyncio
async def test_run_turn_streams_each_stage_before_next_fragment() -> None:
    """Verify LLM fragments flow through TTS and RTP incrementally."""
    events: list[str] = []
    conversation = _FakeConversation(events)
    tts = _FakeTts(events)
    call = _FakeCall()
    pipeline = StreamingVoicePipeline(
        AiConfig(),
        asr=_FakeAsr(),
        conversation_factory=lambda: conversation,
        tts=tts,
    )

    await pipeline.run_turn(call, conversation, "hello")  # type: ignore[arg-type]

    assert events == [
        "llm:Good ",
        "tts:Good ",
        "llm:morning.",
        "tts:morning.",
    ]
    assert call.played == ["Good ".encode("utf-16-le"), "morning.".encode("utf-16-le")]


@pytest.mark.asyncio
async def test_stream_asr_consumes_rtp_frames_and_yields_text() -> None:
    """Verify streaming RTP capture becomes a finalized ASR transcript."""
    config = AiConfig(speech_rms=400, min_speech_ms=200, end_silence_ms=200)
    frames = [_audio_frame(1_000, index) for index in range(15)]
    frames.extend(_audio_frame(0, index) for index in range(15, 25))
    pipeline = StreamingVoicePipeline(
        config,
        asr=_FakeAsr(),
        conversation_factory=lambda: _FakeConversation([]),
        tts=_FakeTts([]),
    )

    transcripts = [
        text async for text in pipeline.stream_asr(_FakeCall(frames))  # type: ignore[arg-type]
    ]

    assert transcripts == ["hello"]


def _audio_frame(amplitude: int, index: int) -> AudioFrame:
    """Create one 20 ms telephone PCM frame.

    Args:
        amplitude: Signed sample value repeated through the frame.
        index: Sequential frame index used for its RTP timestamp.

    Returns:
        Mono 8 kHz PCM audio frame.
    """
    pcm = amplitude.to_bytes(2, "little", signed=True) * 160
    return AudioFrame(pcm=pcm, timestamp=index * 160)
