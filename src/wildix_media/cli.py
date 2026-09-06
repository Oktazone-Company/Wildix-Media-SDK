"""Command-line echo server used for installation and Wildix media checks."""

from __future__ import annotations

import argparse
import logging
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

from wildix_media.call import MediaCall
from wildix_media.config import ServerConfig
from wildix_media.recording import WaveRecorder, call_recording_path
from wildix_media.server import MediaServer


async def echo_call(call: MediaCall, *, recording_dir: Path) -> None:
    """Record and echo every received PCM frame until the caller hangs up.

    Args:
        call: Accepted call supplied by ``MediaServer``.
        recording_dir: Directory in which to save the caller's decoded audio.

    Returns:
        ``None`` when the incoming audio iterator closes.

    Side effects:
        Writes each decoded frame to disk and back to the remote caller.
    """
    logger = logging.getLogger(__name__)
    path = call_recording_path(recording_dir, call.id)
    logger.info("Call %s connected from %s; recording to %s", call.id, call.caller, path)
    with WaveRecorder(path, sample_rate=call.sample_rate) as recorder:
        try:
            async for frame in call.audio_frames():
                if recorder.frame_count == 0:
                    logger.info("Call %s received its first audio frame", call.id)
                recorder.write(frame)
                call.send_frame(frame.pcm)
        finally:
            logger.info(
                "Call %s ended; saved %d frames, %d bytes, %.2f seconds to %s",
                call.id,
                recorder.frame_count,
                recorder.byte_count,
                recorder.duration_seconds,
                path,
            )


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser.

    Returns:
        Parser for logging and recording options; network settings come from environment variables.
    """
    parser = argparse.ArgumentParser(
        description="Run a bidirectional SIP/RTP echo server using WILDIX_MEDIA_* settings."
    )
    parser.add_argument("--debug", action="store_true", help="enable verbose SDK logs")
    parser.add_argument(
        "--record-dir",
        type=Path,
        default=Path("recordings"),
        help="directory for decoded incoming WAV recordings (default: recordings)",
    )
    return parser


def main() -> None:
    """Load environment configuration and run the echo server.

    Returns:
        ``None`` after the process is interrupted.

    Raises:
        ConfigurationError: If an environment setting is invalid.
        OSError: If a listener cannot bind or a public hostname cannot resolve.
    """
    load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    handler = partial(echo_call, recording_dir=args.record_dir)
    server = MediaServer(ServerConfig.from_env(), handler)
    try:
        server.run()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Echo server stopped")
