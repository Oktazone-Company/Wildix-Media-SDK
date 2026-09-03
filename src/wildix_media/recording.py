"""WAV recording helpers for decoded call audio."""

from __future__ import annotations

import re
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from wildix_media.models import AudioFrame

_UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9_.-]+")


def call_recording_path(
    directory: Path,
    call_id: str,
    *,
    recorded_at: datetime | None = None,
) -> Path:
    """Build a filesystem-safe WAV path for one call.

    Args:
        directory: Directory that will contain the recording.
        call_id: SIP Call-ID used to identify the recording.
        recorded_at: Optional timestamp override, primarily for deterministic tests.

    Returns:
        A timestamped path ending in ``.wav``.
    """
    timestamp = (recorded_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    safe_call_id = _UNSAFE_FILENAME_CHARACTERS.sub("_", call_id).strip("._")[:96]
    return directory / f"{timestamp}_{safe_call_id or 'unknown-call'}.wav"


class WaveRecorder:
    """Write incoming SDK audio frames to a mono, signed 16-bit PCM WAV file."""

    def __init__(self, path: Path, *, sample_rate: int) -> None:
        """Configure a recorder without opening its output file.

        Args:
            path: Destination WAV path.
            sample_rate: Expected sample rate for every incoming frame.

        Raises:
            ValueError: If ``sample_rate`` is not positive.
        """
        if sample_rate <= 0:
            raise ValueError("Sample rate must be positive")
        self.path = path
        self.sample_rate = sample_rate
        self.frame_count = 0
        self.byte_count = 0
        self._writer: wave.Wave_write | None = None

    @property
    def duration_seconds(self) -> float:
        """Return the recorded PCM duration.

        Returns:
            Duration derived from 16-bit mono sample count and sample rate.
        """
        return self.byte_count / 2 / self.sample_rate

    def __enter__(self) -> Self:
        """Create the destination directory and open the WAV file.

        Returns:
            This recorder, ready to accept audio frames.

        Raises:
            RuntimeError: If this recorder is already open.
            OSError: If the destination cannot be created or opened.
        """
        if self._writer is not None:
            raise RuntimeError("Recorder is already open")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        writer = wave.open(str(self.path), "wb")
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(self.sample_rate)
        self._writer = writer
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the WAV file and finalize its header.

        Args:
            exc_type: Exception type raised inside the context, if any.
            exc_value: Exception raised inside the context, if any.
            traceback: Exception traceback, if any.

        Returns:
            ``None`` so exceptions from the call handler remain visible.
        """
        del exc_type, exc_value, traceback
        self.close()

    def write(self, frame: AudioFrame) -> None:
        """Append one decoded audio frame to the recording.

        Args:
            frame: Mono signed 16-bit PCM frame supplied by ``MediaCall``.

        Raises:
            RuntimeError: If the recorder is not open.
            ValueError: If frame format differs from the WAV format.
        """
        if self._writer is None:
            raise RuntimeError("Recorder is not open")
        if frame.channels != 1:
            raise ValueError("WaveRecorder supports mono audio only")
        if frame.sample_rate != self.sample_rate:
            raise ValueError("Audio frame sample rate changed during the call")
        if len(frame.pcm) % 2:
            raise ValueError("PCM audio must contain complete signed 16-bit samples")
        self._writer.writeframesraw(frame.pcm)
        self.frame_count += 1
        self.byte_count += len(frame.pcm)

    def close(self) -> None:
        """Finalize and close the WAV file if it is open.

        Returns:
            ``None``. Repeated calls are safe.
        """
        if self._writer is None:
            return
        self._writer.close()
        self._writer = None
