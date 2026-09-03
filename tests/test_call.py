"""Tests for the high-level PCM and call-lifecycle interface."""

from __future__ import annotations

import pytest

from wildix_media.call import MediaCall
from wildix_media.errors import CallClosedError


class FakeSipCall:
    """In-memory signaling double used by call API tests."""

    call_id = "call-123"
    caller = "sip:caller@example.com"
    callee = "sip:agent@example.com"

    def __init__(self) -> None:
        """Initialize a signaling double with no sent BYE."""
        self.hangup_count = 0

    def hangup(self) -> object | None:
        """Record a local BYE request.

        Returns:
            ``None`` because no network request is created by this test double.
        """
        self.hangup_count += 1
        return None


class FakeRtpSession:
    """In-memory RTP double that records outgoing media operations."""

    codec_sample_rate = 8000

    def __init__(self) -> None:
        """Initialize empty media and DTMF records."""
        self.audio: list[tuple[bytes, int]] = []
        self.dtmf: list[tuple[str, int]] = []
        self.closed = False

    def send_audio_pcm(self, pcm: bytes, timestamp: int) -> None:
        """Record one outgoing PCM packet.

        Args:
            pcm: Outgoing signed 16-bit PCM bytes.
            timestamp: Assigned RTP timestamp.

        Returns:
            ``None``.
        """
        self.audio.append((pcm, timestamp))

    def send_dtmf(self, digit: str, duration_ms: int = 160) -> None:
        """Record one outgoing keypad event.

        Args:
            digit: Keypad symbol.
            duration_ms: Requested event duration.

        Returns:
            ``None``.
        """
        self.dtmf.append((digit, duration_ms))

    async def close(self) -> None:
        """Mark the fake media session closed.

        Returns:
            ``None``.
        """
        self.closed = True


def make_call(*, queue_frames: int = 4) -> tuple[MediaCall, FakeSipCall, FakeRtpSession, list[str]]:
    """Build a call and lifecycle doubles for one unit test.

    Args:
        queue_frames: Bounded incoming frame capacity.

    Returns:
        Call, signaling double, media double, and requested-hangup identifiers.
    """
    sip = FakeSipCall()
    rtp = FakeRtpSession()
    hangups: list[str] = []

    async def request_hangup(call_id: str) -> None:
        """Record one application hangup request.

        Args:
            call_id: Call requested for termination.

        Returns:
            ``None``.
        """
        hangups.append(call_id)

    return MediaCall(sip, rtp, request_hangup, queue_frames=queue_frames), sip, rtp, hangups


@pytest.mark.asyncio
async def test_incoming_audio_drops_oldest_frame_under_backpressure() -> None:
    """A full queue should preserve current real-time audio instead of stale frames."""
    call, _sip, _rtp, _hangups = make_call(queue_frames=2)
    call._receive_audio(b"\x01\x00" * 160, 0)
    call._receive_audio(b"\x02\x00" * 160, 160)
    call._receive_audio(b"\x03\x00" * 160, 320)

    frames = call.audio_frames()
    first = await anext(frames)
    second = await anext(frames)

    assert first.timestamp == 160
    assert second.timestamp == 320
    assert call.dropped_frames == 1
    await frames.aclose()


@pytest.mark.asyncio
async def test_play_pcm_chunks_and_advances_timestamps() -> None:
    """Whole-buffer playback should produce paced 20 ms RTP-sized PCM blocks."""
    call, _sip, rtp, _hangups = make_call()
    pcm = b"\x10\x00" * 320

    await call.play_pcm(pcm)

    assert [len(frame) for frame, _timestamp in rtp.audio] == [320, 320]
    assert [timestamp for _frame, timestamp in rtp.audio] == [0, 160]


@pytest.mark.asyncio
async def test_shutdown_closes_iterators_and_rejects_new_audio() -> None:
    """Call shutdown should wake consumers and reject subsequent writes."""
    call, sip, rtp, _hangups = make_call()
    frames = call.audio_frames()

    await call._shutdown(send_bye=True)

    with pytest.raises(StopAsyncIteration):
        await anext(frames)
    with pytest.raises(CallClosedError):
        call.send_frame(b"\x00\x00" * 160)
    assert sip.hangup_count == 1
    assert rtp.closed is True


@pytest.mark.asyncio
async def test_public_hangup_delegates_lifecycle_to_server() -> None:
    """The public hangup method should ask the owning server to perform cleanup."""
    call, _sip, _rtp, hangups = make_call()

    await call.hangup()

    assert hangups == ["call-123"]
