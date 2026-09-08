"""Tests for isolated calls multiplexed through one local UDP port."""

from __future__ import annotations

import asyncio
import math
import socket
import struct

import pytest
from aiortp.codecs import get_codec
from aiortp.packet import RtpPacket
from aiosipua import build_sdp

from wildix_media.shared_rtp import SharedRtpCallSession, SharedRtpMultiplexer


def _free_udp_port() -> int:
    """Find a best-effort free local UDP port.

    Returns:
        Port released immediately for the test listener.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind(("127.0.0.1", 0))
        return int(udp_socket.getsockname()[1])


def _tone_frame(frequency: int) -> bytes:
    """Build one signed 16-bit PCM telephone audio frame.

    Args:
        frequency: Tone frequency in Hz.

    Returns:
        Exactly 160 mono samples at 8 kHz.
    """
    samples = [
        int(8_000 * math.sin(2 * math.pi * frequency * index / 8_000)) for index in range(160)
    ]
    return struct.pack("<160h", *samples)


async def _send_audio_flow(
    udp_socket: socket.socket,
    destination: tuple[str, int],
    pcm: bytes,
    ssrc: int,
) -> None:
    """Send enough RTP frames to satisfy the session jitter prefetch.

    Args:
        udp_socket: Non-blocking caller socket.
        destination: Shared multiplexer address.
        pcm: Signed 16-bit PCM frame to encode.
        ssrc: Stable RTP source identifier for this flow.

    Returns:
        ``None`` after six packets are sent.
    """
    loop = asyncio.get_running_loop()
    codec = get_codec(8)
    for index in range(6):
        packet = RtpPacket(
            payload_type=8,
            sequence_number=index,
            timestamp=index * 160,
            ssrc=ssrc,
            payload=codec.encode(pcm),
        )
        await loop.sock_sendto(udp_socket, packet.serialize(), destination)


def _new_session(
    call_id: str,
    caller_port: int,
    public_port: int,
    multiplexer: SharedRtpMultiplexer,
) -> SharedRtpCallSession:
    """Create one PCMA session for a synthetic caller.

    Args:
        call_id: Diagnostic call identifier.
        caller_port: UDP port announced by the synthetic caller.
        public_port: Shared server port advertised in the SDP answer.
        multiplexer: Shared listener under test.

    Returns:
        Unstarted shared RTP call session.
    """
    return SharedRtpCallSession(
        call_id=call_id,
        offer=build_sdp("127.0.0.1", caller_port, 8, "PCMA", 8_000),
        multiplexer=multiplexer,
        advertised_ip="127.0.0.1",
        advertised_port=public_port,
        supported_codecs=[8],
        ptime=20,
    )


@pytest.mark.asyncio
async def test_two_flows_remain_isolated_on_one_udp_port() -> None:
    """Two callers should receive only audio emitted by their assigned sessions."""
    public_port = _free_udp_port()
    caller_ports = (_free_udp_port(), _free_udp_port())
    multiplexer = SharedRtpMultiplexer("127.0.0.1", public_port, max_calls=2)
    sessions = [
        _new_session("call-1", caller_ports[0], public_port, multiplexer),
        _new_session("call-2", caller_ports[1], public_port, multiplexer),
    ]
    sockets = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
    received: list[list[bytes]] = [[], []]
    try:
        for udp_socket, caller_port in zip(sockets, caller_ports, strict=True):
            udp_socket.bind(("127.0.0.1", caller_port))
            udp_socket.setblocking(False)
        for index, session in enumerate(sessions):
            session.on_audio = lambda pcm, timestamp, i=index: received[i].append(pcm)
            await session.start()
        await multiplexer.start()
        tones = (_tone_frame(440), _tone_frame(660))
        await _send_audio_flow(sockets[0], ("127.0.0.1", public_port), tones[0], 101)
        await _send_audio_flow(sockets[1], ("127.0.0.1", public_port), tones[1], 202)
        await asyncio.sleep(0.05)

        assert received[0]
        assert received[1]
        assert received[0][0] != received[1][0]

        sessions[0].send_audio_pcm(tones[0], 0)
        sessions[1].send_audio_pcm(tones[1], 0)
        loop = asyncio.get_running_loop()
        replies = await asyncio.gather(
            loop.sock_recvfrom(sockets[0], 2048),
            loop.sock_recvfrom(sockets[1], 2048),
        )
        decoded = [get_codec(8).decode(RtpPacket.parse(item[0]).payload) for item in replies]
        assert decoded[0] != decoded[1]
    finally:
        for session in sessions:
            await session.close()
        await multiplexer.stop()
        for udp_socket in sockets:
            udp_socket.close()
