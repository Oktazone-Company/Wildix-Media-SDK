"""Run the SDK's smallest bidirectional PCM echo application."""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

from wildix_media import (
    MediaCall,
    MediaServer,
    ServerConfig,
    WaveRecorder,
    call_recording_path,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
server = MediaServer(ServerConfig.from_env())
recording_directory = Path("recordings")


@server.on_call
async def echo(call: MediaCall) -> None:
    """Save and echo each decoded caller frame through the negotiated codec.

    Args:
        call: Accepted call with incoming and outgoing PCM access.

    Returns:
        ``None`` when the caller ends the call.
    """
    path = call_recording_path(recording_directory, call.id)
    logging.info("Call %s connected from %s; recording to %s", call.id, call.caller, path)
    with WaveRecorder(path, sample_rate=call.sample_rate) as recorder:
        try:
            async for frame in call.audio_frames():
                if recorder.frame_count == 0:
                    logging.info("Call %s received its first audio frame", call.id)
                recorder.write(frame)
                call.send_frame(frame.pcm)
        finally:
            logging.info(
                "Call %s ended; saved %d frames, %d bytes, %.2f seconds to %s",
                call.id,
                recorder.frame_count,
                recorder.byte_count,
                recorder.duration_seconds,
                path,
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server.run()
