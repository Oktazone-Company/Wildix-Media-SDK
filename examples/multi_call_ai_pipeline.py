"""Run the CPU voice pipeline for multiple concurrent calls."""

from __future__ import annotations

import logging
from pathlib import Path

from cpu_ai.config import AiConfig
from cpu_ai.pipeline import StreamingVoicePipeline
from dotenv import load_dotenv

from wildix_media import MediaServer, ServerConfig


def load_server_config() -> ServerConfig:
    """Load and validate the multi-call media configuration.

    Returns:
        A server configuration that permits at least two simultaneous calls.

    Raises:
        ValueError: If multi-call mode is disabled or limited to one call.
    """
    config = ServerConfig.from_env()
    if config.shared_rtp_max_calls < 2:
        raise ValueError(
            "Set WILDIX_MEDIA_SHARED_RTP_MAX_CALLS to 2 or more "
            "before running the multi-call example."
        )
    return config


def main() -> None:
    """Load configuration and serve concurrent AI calls.

    Returns:
        ``None`` after the process is interrupted.

    Raises:
        ImportError: If optional AI dependencies are not installed.
        OSError: If Ollama, SIP, or RTP resources are unavailable.
        ValueError: If environment configuration is invalid.
    """
    repository_root = Path(__file__).resolve().parents[1]
    load_dotenv(repository_root / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    server_config = load_server_config()
    pipeline = StreamingVoicePipeline(AiConfig.from_env())
    logging.getLogger(__name__).info(
        "Starting multi-call AI example with capacity for %d calls",
        server_config.shared_rtp_max_calls,
    )
    MediaServer(server_config, pipeline.handle_call).run()


if __name__ == "__main__":
    main()
