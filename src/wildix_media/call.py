"""High-level active-call API built on negotiated SIP and RTP sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol, TypeAlias, cast

from wildix_media.errors import CallClosedError
from wildix_media.models import AudioFrame, DtmfEvent

_QUEUE_END = object()


class _SipCall(Protocol):
    """Minimum signaling operations required by ``MediaCall``."""

    @property
    def call_id(self) -> str:
        """Return the SIP Call-ID for this call."""
        ...

    @property
    def caller(self) -> str:
        """Return the caller's SIP URI."""
        ...

    @property
    def callee(self) -> str:
        """Return the called SIP URI."""
        ...

    def hangup(self) -> object | None:
        """Send a SIP BYE when the dialog is confirmed."""
        ...


class _RtpSession(Protocol):
    """Minimum media operations required by ``MediaCall``."""

    @property
    def codec_sample_rate(self) -> int:
        """Return the decoded PCM sample rate."""
        ...

    def send_audio_pcm(self, pcm: bytes, timestamp: int) -> None:
        """Encode and send one PCM frame at the supplied RTP timestamp."""
        ...

    def send_dtmf(self, digit: str, duration_ms: int = 160) -> None:
        """Send one RFC 2833 keypad event."""
        ...

    async def close(self) -> None:
        """Close sockets and release RTP resources."""
        ...


HangupCallback: TypeAlias = Callable[[str], Awaitable[None]]
"""Async callback used by a call to request server-owned teardown."""


class MediaCall:
    """One accepted call with asynchronous incoming and outgoing PCM streams."""

    def __init__(
        self,
        sip_call: _SipCall,
        rtp_session: _RtpSession,
        request_hangup: HangupCallback,
        *,
        queue_frames: int,
    ) -> None:
        """Create a call wrapper around established signaling and media sessions.

        Args:
            sip_call: Confirmed incoming SIP call.
            rtp_session: Started RTP session with a negotiated G.711 codec.
            request_hangup: Server callback that owns lifecycle cleanup.
            queue_frames: Maximum number of unread audio frames buffered in memory.
        """
        self._sip_call = sip_call
        self._rtp_session = rtp_session
        self._request_hangup = request_hangup
        self._audio_queue: asyncio.Queue[object] = asyncio.Queue(queue_frames)
        self._dtmf_queue: asyncio.Queue[object] = asyncio.Queue(queue_frames)
        self._closed = asyncio.Event()
        self._play_lock = asyncio.Lock()
        self._tx_timestamp = 0
        self._dropped_frames = 0

    @property
    def id(self) -> str:
        """Return the stable SIP Call-ID.

        Returns:
            Call identifier supplied by the remote endpoint.
        """
        return self._sip_call.call_id

    @property
    def caller(self) -> str:
        """Return the caller's SIP URI.

        Returns:
            Value derived from the SIP From header.
        """
        return self._sip_call.caller

    @property
    def callee(self) -> str:
        """Return the called SIP URI.

        Returns:
            Value derived from the request URI or To header.
        """
        return self._sip_call.callee

    @property
    def sample_rate(self) -> int:
        """Return the PCM sample rate negotiated for this call.

        Returns:
            Samples per second; G.711 calls return ``8000``.
        """
        return self._rtp_session.codec_sample_rate

    @property
    def is_closed(self) -> bool:
        """Report whether signaling and media cleanup has completed.

        Returns:
            ``True`` after either party ends the call.
        """
        return self._closed.is_set()

    @property
    def dropped_frames(self) -> int:
        """Return how many unread incoming frames were dropped under backpressure.

        Returns:
            Cumulative number of frames removed from a full audio queue.
        """
        return self._dropped_frames

    async def audio_frames(self) -> AsyncIterator[AudioFrame]:
        """Yield decoded incoming PCM frames until the call ends.

        Yields:
            ``AudioFrame`` objects containing signed 16-bit little-endian mono PCM.

        Notes:
            A call should have one audio consumer. Slow consumers cause the oldest
            unread frames to be dropped so real-time audio does not build latency.
        """
        while True:
            item = await self._audio_queue.get()
            if item is _QUEUE_END:
                return
            yield cast(AudioFrame, item)

    async def dtmf_events(self) -> AsyncIterator[DtmfEvent]:
        """Yield keypad events until the call ends.

        Yields:
            DTMF digit and duration events decoded from RTP.
        """
        while True:
            item = await self._dtmf_queue.get()
            if item is _QUEUE_END:
                return
            yield cast(DtmfEvent, item)

    def send_frame(self, pcm: bytes, *, timestamp: int | None = None) -> int:
        """Send one PCM block immediately as an RTP packet.

        Args:
            pcm: Signed 16-bit little-endian mono PCM at ``sample_rate``.
            timestamp: Optional RTP timestamp. An internal continuous timestamp is
                used when omitted.

        Returns:
            Timestamp assigned to the outgoing packet.

        Raises:
            CallClosedError: If the call has ended.
            ValueError: If the PCM block is empty or not sample-aligned.

        Side effects:
            Encodes PCM with the negotiated codec and transmits one RTP packet.
        """
        self._ensure_active()
        _validate_pcm(pcm)
        assigned_timestamp = self._tx_timestamp if timestamp is None else timestamp
        self._rtp_session.send_audio_pcm(pcm, assigned_timestamp)
        self._tx_timestamp = assigned_timestamp + len(pcm) // 2
        return assigned_timestamp

    async def play_pcm(self, pcm: bytes, *, frame_duration_ms: int = 20) -> None:
        """Pace an arbitrary PCM buffer over the call in real time.

        Args:
            pcm: Signed 16-bit little-endian mono PCM at ``sample_rate``.
            frame_duration_ms: Packet duration; ``20`` matches common Wildix setup.

        Returns:
            ``None`` after the complete buffer has been sent.

        Raises:
            CallClosedError: If the call ends before or during playback.
            ValueError: If PCM or frame duration is invalid.

        Side effects:
            Sends a paced sequence of RTP audio packets. Cancelling the coroutine
            stops future packets, which applications can use for interruption.
        """
        self._ensure_active()
        _validate_pcm(pcm)
        frame_bytes = _frame_bytes(self.sample_rate, frame_duration_ms)
        async with self._play_lock:
            await self._play_chunks(pcm, frame_bytes, frame_duration_ms)

    def send_dtmf(self, digit: str, *, duration_ms: int = 160) -> None:
        """Send one telephone keypad event to the caller.

        Args:
            digit: One of ``0-9``, ``*``, ``#``, or ``A-D``.
            duration_ms: Requested event duration in milliseconds.

        Returns:
            ``None``.

        Raises:
            CallClosedError: If the call has ended.
            ValueError: If the digit or duration is invalid.
        """
        self._ensure_active()
        if digit.upper() not in set("0123456789*#ABCD"):
            raise ValueError(f"Unsupported DTMF digit: {digit!r}")
        if duration_ms < 40:
            raise ValueError("DTMF duration must be at least 40 ms")
        self._rtp_session.send_dtmf(digit.upper(), duration_ms)

    async def wait_closed(self) -> None:
        """Wait until either party ends the call.

        Returns:
            ``None`` after media resources have been closed.
        """
        await self._closed.wait()

    async def hangup(self) -> None:
        """Ask the owning server to send BYE and release this call.

        Returns:
            ``None`` after local cleanup completes. Repeated calls are safe.
        """
        if self.is_closed:
            return
        await self._request_hangup(self.id)

    def _receive_audio(self, pcm: bytes, timestamp: int) -> None:
        """Queue one decoded RTP frame without blocking the media callback.

        Args:
            pcm: Signed 16-bit little-endian mono PCM bytes.
            timestamp: Incoming RTP timestamp.

        Returns:
            ``None``. Frames received after closure are ignored.

        Side effects:
            Drops the oldest unread frame if the queue is full.
        """
        if self.is_closed:
            return
        frame = AudioFrame(pcm=pcm, timestamp=timestamp, sample_rate=self.sample_rate)
        if self._audio_queue.full():
            self._audio_queue.get_nowait()
            self._dropped_frames += 1
        self._audio_queue.put_nowait(frame)

    def _receive_dtmf(self, digit: str, duration_ms: int) -> None:
        """Queue one decoded keypad event without blocking RTP handling.

        Args:
            digit: Decoded keypad symbol.
            duration_ms: Event duration reported by the RTP layer.

        Returns:
            ``None``. Events received after closure are ignored.
        """
        if self.is_closed:
            return
        if self._dtmf_queue.full():
            self._dtmf_queue.get_nowait()
        self._dtmf_queue.put_nowait(DtmfEvent(digit=digit, duration_ms=duration_ms))

    async def _shutdown(self, *, send_bye: bool) -> None:
        """Close signaling and media resources exactly once.

        Args:
            send_bye: Whether to terminate the confirmed SIP dialog locally.

        Returns:
            ``None`` after queues and RTP resources are closed.
        """
        if self.is_closed:
            return
        self._closed.set()
        if send_bye:
            self._sip_call.hangup()
        await self._rtp_session.close()
        _end_queue(self._audio_queue)
        _end_queue(self._dtmf_queue)

    async def _play_chunks(self, pcm: bytes, frame_bytes: int, duration_ms: int) -> None:
        """Send paced chunks while preserving a monotonic media clock.

        Args:
            pcm: Valid sample-aligned PCM bytes.
            frame_bytes: Maximum bytes sent in each RTP packet.
            duration_ms: Nominal delay between full frames.

        Returns:
            ``None`` after all chunks are sent.

        Raises:
            CallClosedError: If the call closes during playback.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        chunks = range(0, len(pcm), frame_bytes)
        for index, offset in enumerate(chunks):
            self.send_frame(pcm[offset : offset + frame_bytes])
            target = started + (index + 1) * duration_ms / 1000
            await asyncio.sleep(max(0.0, target - loop.time()))

    def _ensure_active(self) -> None:
        """Reject operations after call teardown.

        Returns:
            ``None`` for an active call.

        Raises:
            CallClosedError: If the call is closed.
        """
        if self.is_closed:
            raise CallClosedError(f"Call {self.id} is closed")


def _validate_pcm(pcm: bytes) -> None:
    """Validate signed 16-bit PCM byte alignment.

    Args:
        pcm: Candidate PCM bytes.

    Returns:
        ``None`` for a non-empty sample-aligned buffer.

    Raises:
        ValueError: If no complete signed 16-bit samples are present.
    """
    if not pcm:
        raise ValueError("PCM audio cannot be empty")
    if len(pcm) % 2:
        raise ValueError("PCM audio must contain complete signed 16-bit samples")


def _frame_bytes(sample_rate: int, duration_ms: int) -> int:
    """Calculate bytes in one mono signed 16-bit PCM frame.

    Args:
        sample_rate: Audio samples per second.
        duration_ms: Requested frame duration in milliseconds.

    Returns:
        Number of bytes in one frame.

    Raises:
        ValueError: If duration is non-positive or not sample-aligned.
    """
    if duration_ms <= 0:
        raise ValueError("Frame duration must be positive")
    samples_numerator = sample_rate * duration_ms
    if samples_numerator % 1000:
        raise ValueError("Frame duration does not produce a whole number of samples")
    return samples_numerator // 1000 * 2


def _end_queue(queue: asyncio.Queue[object]) -> None:
    """Insert an end marker even when a bounded queue is full.

    Args:
        queue: Audio or DTMF queue being closed.

    Returns:
        ``None``.

    Side effects:
        May discard the oldest queued event to make room for the marker.
    """
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(_QUEUE_END)
