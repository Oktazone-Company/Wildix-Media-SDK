"""Tests for decoded PCM WAV recording helpers."""

from __future__ import annotations

import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wildix_media import AudioFrame, WaveRecorder, call_recording_path


def test_call_recording_path_sanitizes_sip_call_id(tmp_path: Path) -> None:
    """Unsafe Call-ID characters should not escape the recording directory."""
    directory = tmp_path / "recordings"
    recorded_at = datetime(2026, 9, 3, 13, 3, 24, tzinfo=UTC)

    path = call_recording_path(directory, "call/id@example.com", recorded_at=recorded_at)

    assert path == directory / "20260903T130324Z_call_id_example.com.wav"


def test_wave_recorder_writes_valid_pcm_file(tmp_path: Path) -> None:
    """Recorded SDK frames should produce a readable mono 8 kHz WAV file."""
    path = tmp_path / "nested" / "call.wav"
    pcm = b"\x00\x00" * 160
    frame = AudioFrame(pcm=pcm, timestamp=0, sample_rate=8000)

    with WaveRecorder(path, sample_rate=8000) as recorder:
        recorder.write(frame)
        recorder.write(frame)

    assert recorder.frame_count == 2
    assert recorder.byte_count == len(pcm) * 2
    assert recorder.duration_seconds == pytest.approx(0.04)
    with wave.open(str(path), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getsampwidth() == 2
        assert recording.getframerate() == 8000
        assert recording.getnframes() == len(pcm)
        assert recording.readframes(recording.getnframes()) == pcm * 2
