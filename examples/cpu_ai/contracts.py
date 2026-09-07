"""Replaceable provider contracts for the streaming voice pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol


class SpeechRecognizer(Protocol):
    """Convert one bounded PCM speech turn into text."""

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Recognize one PCM utterance.

        Args:
            pcm: Signed 16-bit mono PCM audio.
            sample_rate: PCM sample rate in Hz.

        Returns:
            Recognized text, or an empty string when no speech is recognized.
        """
        ...


class LanguageModelConversation(Protocol):
    """Generate streaming text while retaining per-call conversation state."""

    def stream_reply(self, user_text: str) -> AsyncIterator[str]:
        """Stream response text fragments for one caller message.

        Args:
            user_text: Finalized caller transcription.

        Returns:
            Asynchronous iterator of response text fragments.
        """
        ...


class SpeechSynthesizer(Protocol):
    """Convert a streaming text response into streaming PCM audio."""

    def stream(
        self,
        text_stream: AsyncIterator[str],
        sample_rate: int,
    ) -> AsyncIterator[bytes]:
        """Stream synthesized PCM frames from incoming text fragments.

        Args:
            text_stream: Asynchronous stream of model-generated text.
            sample_rate: Required PCM sample rate in Hz.

        Returns:
            Asynchronous iterator of signed 16-bit mono PCM frames.
        """
        ...


ConversationFactory = Callable[[], LanguageModelConversation]
"""Factory creating isolated language-model state for each telephone call."""
