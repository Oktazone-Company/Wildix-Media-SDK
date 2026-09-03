"""Immutable audio and keypad events emitted by active calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One decoded mono PCM audio frame received from a caller.

    Attributes:
        pcm: Signed 16-bit little-endian PCM bytes.
        timestamp: RTP media timestamp supplied by the remote stream.
        sample_rate: PCM samples per second.
        channels: Number of interleaved channels; currently always one.
    """

    pcm: bytes
    timestamp: int
    sample_rate: int = 8000
    channels: int = 1

    @property
    def sample_count(self) -> int:
        """Return the number of mono PCM samples in this frame.

        Returns:
            Number of signed 16-bit samples.
        """
        return len(self.pcm) // 2

    @property
    def duration_ms(self) -> float:
        """Return the frame duration in milliseconds.

        Returns:
            Floating-point duration derived from sample count and sample rate.
        """
        return self.sample_count * 1000 / self.sample_rate


@dataclass(frozen=True, slots=True)
class DtmfEvent:
    """One RFC 2833 telephone keypad event received during a call.

    Attributes:
        digit: Keypad symbol, for example ``1`` or ``#``.
        duration_ms: Reported event duration in milliseconds.
    """

    digit: str
    duration_ms: int
