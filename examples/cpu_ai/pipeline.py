"""Composable streaming ASR-to-LLM-to-TTS call orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from wildix_media import MediaCall
from wildix_media.errors import CallClosedError

from .config import AiConfig
from .contracts import (
    ConversationFactory,
    LanguageModelConversation,
    SpeechRecognizer,
    SpeechSynthesizer,
)
from .providers import FasterWhisperAsr, OllamaChat, SystemTts
from .turns import TurnDetector

logger = logging.getLogger(__name__)
_END = object()


class StreamingVoicePipeline:
    """Compose replaceable streaming voice stages over one bidirectional call."""

    def __init__(
        self,
        config: AiConfig,
        *,
        asr: SpeechRecognizer | None = None,
        conversation_factory: ConversationFactory | None = None,
        tts: SpeechSynthesizer | None = None,
    ) -> None:
        """Configure the pipeline and its replaceable providers.

        Args:
            config: Turn detection and default local-provider settings.
            asr: Optional speech recognizer replacing Faster Whisper.
            conversation_factory: Optional per-call LLM conversation factory.
            tts: Optional speech synthesizer replacing system TTS.

        Raises:
            ImportError: If default optional AI dependencies are unavailable.
        """
        self._config = config
        self._asr = asr or FasterWhisperAsr(config.asr_model, config.asr_language)
        self._conversation_factory = conversation_factory
        self._tts = tts or SystemTts(
            rate=config.tts_rate,
            voice=config.tts_voice,
            chunk_chars=config.tts_chunk_chars,
        )

    async def handle_call(self, call: MediaCall) -> None:
        """Serve one call through the complete streaming pipeline.

        Args:
            call: Accepted bidirectional Wildix media call.

        Returns:
            ``None`` when the caller hangs up or incoming media closes.
        """
        await self.run_pipeline(call)

    async def run_pipeline(self, call: MediaCall) -> None:
        """Combine ASR, LLM, TTS, and RTP stages for one call.

        Incoming RTP continues to be consumed while a response is generated and
        played. A newly completed caller turn cancels any response still playing.

        Args:
            call: Active call supplying and receiving PCM audio.

        Returns:
            ``None`` after the call's incoming media stream ends.
        """
        conversation = self.create_conversation()
        response_task: asyncio.Task[None] | None = None
        logger.info("AI call %s ready; streaming pipeline active", call.id)
        try:
            async for transcript in self.stream_asr(call):
                if response_task is not None:
                    await _cancel_task(response_task)
                response_task = asyncio.create_task(
                    self.run_turn(call, conversation, transcript)
                )
        finally:
            if response_task is not None:
                if call.is_closed:
                    response_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, CallClosedError):
                    await response_task

    async def stream_asr(self, call: MediaCall) -> AsyncIterator[str]:
        """Stream incoming RTP frames into ASR and yield finalized transcripts.

        The default Faster Whisper adapter receives bounded utterances because it
        is not a native online recognizer. Override this method to connect a vendor
        that emits partial or final transcripts directly from streaming audio.

        Args:
            call: Active call supplying decoded PCM frames.

        Yields:
            Non-empty finalized caller transcriptions.
        """
        utterances: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=2)
        capture_task = asyncio.create_task(self._capture_utterances(call, utterances))
        try:
            while True:
                utterance = await utterances.get()
                if utterance is _END:
                    return
                if not isinstance(utterance, bytes):
                    continue
                transcript = await self._asr.transcribe(utterance, call.sample_rate)
                if transcript:
                    logger.info("AI call %s caller: %s", call.id, transcript)
                    yield transcript
                else:
                    logger.info("AI call %s: ASR produced no text", call.id)
        finally:
            capture_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await capture_task

    async def stream_llm(
        self,
        conversation: LanguageModelConversation,
        transcript: str,
    ) -> AsyncIterator[str]:
        """Stream LLM text fragments for one caller transcription.

        Override this method, or inject ``conversation_factory``, to use another
        local model, hosted model, RAG agent, or tool-calling workflow.

        Args:
            conversation: Per-call language-model state.
            transcript: Finalized caller transcription.

        Yields:
            Incremental response text fragments.
        """
        fragments: list[str] = []
        async for fragment in conversation.stream_reply(transcript):
            fragments.append(fragment)
            yield fragment
        logger.info("AI call assistant: %s", "".join(fragments).strip())

    async def stream_tts(
        self,
        text_stream: AsyncIterator[str],
        sample_rate: int,
    ) -> AsyncIterator[bytes]:
        """Stream generated text through TTS and yield PCM frames.

        Override this method, or inject ``tts``, to use a native streaming speech
        service. The default adapter synthesizes short phrases as text arrives.

        Args:
            text_stream: Incremental text from ``stream_llm``.
            sample_rate: Required telephone PCM sample rate in Hz.

        Yields:
            Signed 16-bit mono PCM frames ready for RTP transmission.
        """
        async for frame in self._tts.stream(text_stream, sample_rate):
            yield frame

    async def stream_rtp(
        self,
        call: MediaCall,
        audio_stream: AsyncIterator[bytes],
    ) -> None:
        """Pace streaming PCM frames onto the active RTP session.

        Args:
            call: Active call receiving synthesized speech.
            audio_stream: Incremental PCM frames from ``stream_tts``.

        Returns:
            ``None`` after all generated audio has been transmitted.
        """
        async for pcm in audio_stream:
            await call.play_pcm(pcm)

    async def run_turn(
        self,
        call: MediaCall,
        conversation: LanguageModelConversation,
        transcript: str,
    ) -> None:
        """Connect the streaming LLM, TTS, and RTP stages for one turn.

        Args:
            call: Active call receiving the generated response.
            conversation: Per-call language-model state.
            transcript: Finalized caller transcription.

        Returns:
            ``None`` after response playback finishes or is cancelled.
        """
        text_stream = self.stream_llm(conversation, transcript)
        audio_stream = self.stream_tts(text_stream, call.sample_rate)
        await self.stream_rtp(call, audio_stream)

    def create_conversation(self) -> LanguageModelConversation:
        """Create isolated LLM state for a newly accepted call.

        Returns:
            Injected conversation instance or the default Ollama conversation.
        """
        if self._conversation_factory is not None:
            return self._conversation_factory()
        return OllamaChat(self._config)

    async def _capture_utterances(
        self,
        call: MediaCall,
        utterances: asyncio.Queue[bytes | object],
    ) -> None:
        """Continuously segment incoming PCM without blocking model inference.

        Args:
            call: Active call supplying decoded PCM frames.
            utterances: Bounded destination queue for completed speech turns.

        Returns:
            ``None`` after incoming media closes.
        """
        detector = TurnDetector(self._config)
        try:
            async for frame in call.audio_frames():
                utterance = detector.feed(frame)
                if utterance:
                    _offer(utterances, utterance)
        finally:
            remaining = detector.flush()
            if remaining:
                _offer(utterances, remaining)
            _offer(utterances, _END)


CpuAiPipeline = StreamingVoicePipeline
"""Backward-compatible name for integrations using the original example class."""


async def _cancel_task(task: asyncio.Task[None]) -> None:
    """Cancel and join an in-progress response task.

    Args:
        task: Response generation or playback task.

    Returns:
        ``None`` after the task stops.
    """
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, CallClosedError):
        await task


def _offer(queue: asyncio.Queue[bytes | object], item: bytes | object) -> None:
    """Put the newest item into a bounded queue without blocking RTP input.

    Args:
        queue: Queue shared by media capture and ASR inference.
        item: PCM utterance or private end marker.

    Returns:
        ``None``. The oldest item is discarded when the queue is full.
    """
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)
