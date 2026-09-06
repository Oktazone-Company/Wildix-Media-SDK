"""Call orchestration for the lightweight local ASR-to-LLM-to-TTS example."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from wildix_media import MediaCall

from .config import AiConfig
from .providers import FasterWhisperAsr, OllamaChat, SystemTts
from .turns import TurnDetector

logger = logging.getLogger(__name__)
_END = object()


class CpuAiPipeline:
    """Run one turn-at-a-time local speech conversations over active calls."""

    def __init__(self, config: AiConfig) -> None:
        """Load reusable CPU ASR and TTS providers.

        Args:
            config: Provider and turn-detection settings.

        Raises:
            ImportError: If optional AI dependencies are missing.
        """
        self._config = config
        self._asr = FasterWhisperAsr(config.asr_model, config.asr_language)
        self._tts = SystemTts(rate=config.tts_rate, voice=config.tts_voice)

    async def handle_call(self, call: MediaCall) -> None:
        """Process caller utterances until the call ends.

        Args:
            call: Accepted bidirectional Wildix media call.

        Returns:
            ``None`` when the caller hangs up or media closes.

        Side effects:
            Runs local inference and sends synthesized PCM over RTP.
        """
        turns: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=2)
        capture_task = asyncio.create_task(self._capture_turns(call, turns))
        chat = OllamaChat(self._config)
        logger.info("AI call %s ready; listening for speech", call.id)
        try:
            while not call.is_closed:
                utterance = await turns.get()
                if utterance is _END:
                    return
                await self._respond(call, chat, utterance)
        finally:
            capture_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await capture_task

    async def _respond(self, call: MediaCall, chat: OllamaChat, utterance: object) -> None:
        """Run one ASR, LLM, and TTS turn.

        Args:
            call: Active call receiving the response.
            chat: Per-call Ollama conversation state.
            utterance: PCM bytes emitted by the capture queue.

        Returns:
            ``None`` after playback or when ASR produces no text.
        """
        if not isinstance(utterance, bytes):
            return
        text = await self._asr.transcribe(utterance, call.sample_rate)
        if not text:
            logger.info("AI call %s: ASR produced no text", call.id)
            return
        logger.info("AI call %s caller: %s", call.id, text)
        reply = await chat.reply(text)
        logger.info("AI call %s assistant: %s", call.id, reply)
        pcm = await self._tts.synthesize(reply, call.sample_rate)
        await call.play_pcm(pcm)

    async def _capture_turns(
        self,
        call: MediaCall,
        turns: asyncio.Queue[bytes | object],
    ) -> None:
        """Continuously segment incoming audio without blocking inference.

        Args:
            call: Active call supplying decoded PCM frames.
            turns: Bounded destination queue for completed utterances.

        Returns:
            ``None`` after incoming media closes.
        """
        detector = TurnDetector(self._config)
        try:
            async for frame in call.audio_frames():
                utterance = detector.feed(frame)
                if utterance:
                    _offer(turns, utterance)
        finally:
            remaining = detector.flush()
            if remaining:
                _offer(turns, remaining)
            _offer(turns, _END)


def _offer(queue: asyncio.Queue[bytes | object], item: bytes | object) -> None:
    """Put a recent item into a bounded queue without blocking RTP consumption.

    Args:
        queue: Turn queue shared with the inference loop.
        item: PCM utterance or end marker.

    Returns:
        ``None``. The oldest pending item is discarded when the queue is full.
    """
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)
