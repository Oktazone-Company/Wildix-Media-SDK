"""Real loopback SIP and RTP test for bidirectional G.711 media."""

from __future__ import annotations

import asyncio
import math
import socket
import struct

import pytest
from aiosipua import CallSession, SipUAC, SipUAS, TcpSipTransport, build_sdp

from wildix_media import AudioCodec, MediaCall, MediaServer, ServerConfig


def _free_dual_protocol_port() -> int:
    """Find a local number free for both TCP and UDP binding.

    Returns:
        Best-effort free port suitable for the single-port tunnel topology.
    """
    for _attempt in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.bind(("127.0.0.1", 0))
            port = int(udp_socket.getsockname()[1])
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_socket:
                    tcp_socket.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Could not find a free TCP/UDP test port")


def _free_udp_port() -> int:
    """Find one best-effort free local UDP port.

    Returns:
        Non-ephemeral port released immediately for use by the loopback caller.

    Raises:
        RuntimeError: If the small test range has no available port.
    """
    for port in range(20_000, 20_100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
                udp_socket.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("Could not find a free UDP test port")


def _tone_frame() -> bytes:
    """Build one 20 ms, 440 Hz, signed 16-bit PCM test frame.

    Returns:
        Exactly 160 mono samples at 8 kHz.
    """
    samples = [int(9000 * math.sin(2 * math.pi * 440 * index / 8000)) for index in range(160)]
    return struct.pack("<160h", *samples)


def _mean_absolute_error(left: bytes, right: bytes) -> float:
    """Measure sample error between equal-length signed 16-bit PCM buffers.

    Args:
        left: First PCM buffer.
        right: Second PCM buffer.

    Returns:
        Mean absolute sample difference.
    """
    count = len(left) // 2
    left_samples = struct.unpack(f"<{count}h", left)
    right_samples = struct.unpack(f"<{count}h", right)
    return sum(abs(a - b) for a, b in zip(left_samples, right_samples, strict=True)) / count


async def _wait_until_no_calls(server: MediaServer) -> None:
    """Wait briefly for remote-BYE cleanup to finish.

    Args:
        server: Server expected to release its only active call.

    Returns:
        ``None`` when no call remains.

    Raises:
        TimeoutError: If cleanup does not finish within two seconds.
    """
    calls = server.active_calls
    if calls:
        await asyncio.wait_for(
            asyncio.gather(*(call.wait_closed() for call in calls)),
            timeout=2.0,
        )
    assert not server.active_calls


@pytest.mark.asyncio
async def test_tcp_sip_and_udp_rtp_echo_on_same_numeric_port() -> None:
    """A real loopback call should move decoded audio in both directions."""
    server_port = _free_dual_protocol_port()
    caller_rtp_port = _free_udp_port()
    received = bytearray()
    audio_returned = asyncio.Event()

    async def echo(call: MediaCall) -> None:
        """Echo incoming SDK frames for this integration call."""
        async for frame in call.audio_frames():
            call.send_frame(frame.pcm)

    server = MediaServer(
        ServerConfig(
            sip_host="127.0.0.1",
            sip_port=server_port,
            rtp_host="127.0.0.1",
            rtp_port_start=server_port,
            rtp_port_end=server_port,
            codecs=(AudioCodec.PCMA,),
        ),
        echo,
    )
    await server.start()

    caller_transport = TcpSipTransport(local_addr=("127.0.0.1", 0))
    caller_uac = SipUAC(caller_transport)
    caller_uas = SipUAS(caller_transport, uac=caller_uac)
    await caller_uas.start()
    await caller_transport.connect(("127.0.0.1", server_port))
    caller_session: CallSession | None = None

    try:
        offer = build_sdp("127.0.0.1", caller_rtp_port, 8, "PCMA", 8000)
        outgoing = caller_uac.send_invite(
            "sip:test-caller@127.0.0.1",
            f"sip:echo@127.0.0.1:{server_port}",
            ("127.0.0.1", server_port),
            sdp_offer=offer,
        )
        await outgoing.wait_answered(timeout=3.0)
        assert outgoing.sdp_answer is not None

        caller_session = CallSession(
            local_ip="127.0.0.1",
            rtp_port=caller_rtp_port,
            offer=outgoing.sdp_answer,
            supported_codecs=[8],
            jitter_prefetch=1,
        )

        def collect_audio(pcm: bytes, timestamp: int) -> None:
            """Collect returned media and signal the waiting test."""
            del timestamp
            received.extend(pcm)
            audio_returned.set()

        caller_session.on_audio = collect_audio
        await caller_session.start()
        source_frame = _tone_frame()
        for index in range(8):
            caller_session.send_audio_pcm(source_frame, index * 160)
            await asyncio.sleep(0.02)

        await asyncio.wait_for(audio_returned.wait(), timeout=3.0)
        returned_frame = bytes(received[: len(source_frame)])
        assert len(returned_frame) == len(source_frame)
        assert _mean_absolute_error(source_frame, returned_frame) < 500

        outgoing.hangup(caller_uac)
        await _wait_until_no_calls(server)
    finally:
        if caller_session is not None:
            await caller_session.close()
        await caller_uas.stop()
        await server.stop()
