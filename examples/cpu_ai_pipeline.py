"""Run the lightweight CPU ASR-to-Ollama-to-system-TTS call example."""

from __future__ import annotations

import logging
from pathlib import Path

from cpu_ai.config import AiConfig
from cpu_ai.pipeline import StreamingVoicePipeline
from dotenv import load_dotenv

from wildix_media import MediaServer, ServerConfig


def main() -> None:
    """Load local settings and serve the CPU voice pipeline.

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
    pipeline = StreamingVoicePipeline(AiConfig.from_env())
    MediaServer(ServerConfig.from_env(), pipeline.handle_call).run()


if __name__ == "__main__":
    main()
