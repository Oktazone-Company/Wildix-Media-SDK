"""Single-port RTP multiplexing for development tunnels that expose one UDP port."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable

from aiortp.codecs import get_codec
from aiortp.dtmf import DtmfReceiver, DtmfSender
from aiortp.jitterbuffer import JitterBuffer
from aiortp.packet import RtpPacket, is_rtcp
from aiortp.sender import RtpSender
from aiosipua import SdpMessage, negotiate_sdp

from wildix_media.errors import MediaCapacityError

logger = logging.getLogger(__name__)

AudioCallback = Callable[[bytes, int], None]
DtmfCallback = Callable[[str, int], None]


class SharedRtpMultiplexer(asyncio.DatagramProtocol):
    """Route independent RTP flows through one tunnel-facing UDP socket."""

    def __init__(self, host: str, port: int, max_calls: int) -> None:
        """Configure a stopped shared RTP listener.

        Args:
            host: Local interface receiving tunnel-forwarded UDP packets.
            port: Single local UDP port exposed by the tunnel.
            max_calls: Maximum number of simultaneously registered sessions.
        """
        self._host = host
        self._port = port
        self._max_calls = max_calls
        self._transport: asyncio.DatagramTransport | None = None
        self._pending: deque[SharedRtpCallSession] = deque()
        self._session_flows: dict[SharedRtpCallSession, tuple[str, int] | None] = {}
        self._flow_sessions: dict[tuple[str, int], SharedRtpCallSession] = {}
        self._closed = asyncio.Event()

    async def start(self) -> None:
        """Bind the shared UDP listener.

        Returns:
            ``None`` once the UDP socket is ready.

        Raises:
            OSError: If the configured local address cannot be bound.
        """
        if self._transport is not None:
            return
        self._closed.clear()
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: self,
            local_addr=(self._host, self._port),
        )
        self._transport = transport

    async def stop(self) -> None:
        """Close the listener and forget all call-flow associations.

        Returns:
            ``None`` after the UDP transport reports closure.
        """
        transport = self._transport
        if transport is None:
            return
        self._transport = None
        transport.close()
        await self._closed.wait()
        self._pending.clear()
        self._session_flows.clear()
        self._flow_sessions.clear()

    def register(self, session: SharedRtpCallSession) -> None:
        """Register a session waiting for its first tunnel RTP packet.

        Args:
            session: Negotiated call session to attach to a new UDP flow.

        Returns:
            ``None`` after registration.

        Raises:
            MediaCapacityError: If the configured shared-call limit is reached.
        """
        if len(self._session_flows) >= self._max_calls:
            raise MediaCapacityError("No shared RTP capacity remains")
        self._session_flows[session] = None
        self._pending.append(session)

    def unregister(self, session: SharedRtpCallSession) -> None:
        """Remove a session and release its associated external flow.

        Args:
            session: Session being closed.

        Returns:
            ``None``. Unknown sessions are ignored.
        """
        flow = self._session_flows.pop(session, None)
        if flow is not None:
            self._flow_sessions.pop(flow, None)
        try:
            self._pending.remove(session)
        except ValueError:
            pass

    def send(self, session: SharedRtpCallSession, data: bytes) -> None:
        """Send one RTP datagram back through a session's learned tunnel flow.

        Args:
            session: Session that produced the packet.
            data: Serialized RTP datagram.

        Returns:
            ``None``. Packets produced before flow discovery are dropped.
        """
        flow = self._session_flows.get(session)
        if flow is None or self._transport is None:
            return
        self._transport.sendto(data, flow)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Store the UDP transport created by the event loop.

        Args:
            transport: Newly bound asyncio datagram transport.

        Returns:
            ``None``.
        """
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Route one incoming RTP datagram to its isolated call session.

        Args:
            data: Received UDP payload.
            addr: Tunnel client's per-flow local source address.

        Returns:
            ``None`` after routing or discarding an unsupported packet.
        """
        if is_rtcp(data):
            return
        try:
            packet = RtpPacket.parse(data)
        except ValueError:
            logger.debug("Ignoring invalid shared-port RTP datagram from %s", addr)
            return
        session = self._flow_sessions.get(addr)
        if session is None:
            session = self._claim_pending_session(addr, packet.ssrc)
        if session is not None:
            session.receive_packet(packet)

    def error_received(self, exc: Exception) -> None:
        """Log an asynchronous UDP transport error.

        Args:
            exc: Socket error reported by asyncio.

        Returns:
            ``None``.
        """
        logger.warning("Shared RTP transport error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Signal completion when the shared UDP listener closes.

        Args:
            exc: Optional socket error that caused closure.

        Returns:
            ``None``.
        """
        if exc is not None:
            logger.warning("Shared RTP listener closed with an error: %s", exc)
        self._closed.set()

    def _claim_pending_session(
        self,
        addr: tuple[str, int],
        ssrc: int,
    ) -> SharedRtpCallSession | None:
        """Associate a new tunnel flow with a waiting SIP call.

        Args:
            addr: New tunnel-side UDP flow address.
            ssrc: RTP synchronization source from the first packet.

        Returns:
            Matching session, or ``None`` when no call is waiting.
        """
        session = self._take_ssrc_match(ssrc) or self._take_oldest_pending()
        if session is None:
            logger.debug("No pending call for shared RTP flow %s", addr)
            return None
        self._session_flows[session] = addr
        self._flow_sessions[addr] = session
        logger.info("Shared RTP flow %s attached to call %s", addr, session.call_id)
        return session

    def _take_ssrc_match(self, ssrc: int) -> SharedRtpCallSession | None:
        """Prefer a pending session whose SDP declared the packet SSRC.

        Args:
            ssrc: SSRC parsed from an incoming RTP packet.

        Returns:
            Matching pending session, or ``None`` when SDP gave no match.
        """
        for session in self._pending:
            if session.offered_ssrc == ssrc:
                self._pending.remove(session)
                return session
        return None

    def _take_oldest_pending(self) -> SharedRtpCallSession | None:
        """Return the oldest still-active session awaiting media.

        Returns:
            Pending session, or ``None`` when every session already has a flow.
        """
        while self._pending:
            session = self._pending.popleft()
            if session in self._session_flows:
                return session
        return None


class SharedRtpCallSession:
    """Negotiate and process one call on a shared tunnel-facing RTP socket."""

    def __init__(
        self,
        *,
        call_id: str,
        offer: SdpMessage,
        multiplexer: SharedRtpMultiplexer,
        advertised_ip: str,
        advertised_port: int,
        supported_codecs: list[int],
        ptime: int,
        dtmf_payload_type: int = 101,
    ) -> None:
        """Create an unstarted virtual RTP session.

        Args:
            call_id: SIP Call-ID used for diagnostics.
            offer: Remote SDP offer to negotiate.
            multiplexer: Shared UDP listener owning network I/O.
            advertised_ip: Public media IP written into the SDP answer.
            advertised_port: Public media port written into the SDP answer.
            supported_codecs: RTP payload types accepted by the SDK.
            ptime: Packetization interval advertised in milliseconds.
            dtmf_payload_type: RFC 4733 telephone-event payload type.
        """
        answer, chosen_payload = negotiate_sdp(
            offer=offer,
            local_ip=advertised_ip,
            rtp_port=advertised_port,
            supported_codecs=supported_codecs,
            dtmf_payload_type=dtmf_payload_type,
            ptime=ptime,
        )
        self.call_id = call_id
        self.sdp_answer = answer
        self.chosen_payload_type = chosen_payload
        self.offered_ssrc = _offered_ssrc(offer)
        self.on_audio: AudioCallback | None = None
        self.on_dtmf: DtmfCallback | None = None
        self._multiplexer = multiplexer
        self._codec = get_codec(chosen_payload)
        self._jitter = JitterBuffer(capacity=16, prefetch=4)
        self._remote_ssrc: int | None = None
        self._active = False
        self._closed = False
        self._received_packets = 0
        self._transport = _SharedSessionTransport(multiplexer, self)
        self._sender = RtpSender(self._transport, chosen_payload, enable_history=False)  # type: ignore[arg-type]
        self._dtmf_sender = DtmfSender(self._sender, dtmf_payload_type=dtmf_payload_type)
        self._dtmf_receiver = DtmfReceiver(self._dispatch_dtmf)
        self._dtmf_payload_type = dtmf_payload_type

    @property
    def codec_sample_rate(self) -> int:
        """Return the negotiated codec sample rate in Hz.

        Returns:
            Sample rate expected by ``send_audio_pcm`` and emitted callbacks.
        """
        return int(self._codec.sample_rate)

    @property
    def stats(self) -> dict[str, int]:
        """Return lightweight packet counters for this virtual session.

        Returns:
            Dictionary containing the received RTP packet count.
        """
        return {"packets_received": self._received_packets}

    async def start(self) -> None:
        """Register this call with the shared RTP multiplexer.

        Returns:
            ``None`` once the session is ready to claim a tunnel flow.

        Raises:
            MediaCapacityError: If the multiplexer already owns its maximum calls.
        """
        if self._active:
            return
        self._multiplexer.register(self)
        self._active = True

    async def close(self) -> None:
        """Detach this session from the shared UDP flow.

        Returns:
            ``None`` after the association is released. Repeated calls are safe.
        """
        if self._closed:
            return
        self._closed = True
        self._active = False
        self._multiplexer.unregister(self)

    def send_audio_pcm(self, pcm: bytes, timestamp: int) -> None:
        """Encode and transmit one PCM audio frame.

        Args:
            pcm: Signed 16-bit mono PCM at ``codec_sample_rate``.
            timestamp: RTP media timestamp for the frame.

        Returns:
            ``None``. Frames produced before flow discovery are dropped.
        """
        if not self._active or self._closed:
            return
        self._sender.send_frame(self._codec.encode(pcm), timestamp)

    def send_dtmf(self, digit: str, duration_ms: int = 160) -> None:
        """Transmit one RFC 4733 DTMF event.

        Args:
            digit: Telephone digit from ``0-9``, ``*``, ``#``, or ``A-D``.
            duration_ms: Event duration in milliseconds.

        Returns:
            ``None`` after the event packets are queued.
        """
        if self._active and not self._closed:
            self._dtmf_sender.send_digit(digit, duration_ms)

    def receive_packet(self, packet: RtpPacket) -> None:
        """Decode and dispatch one RTP packet assigned by the multiplexer.

        Args:
            packet: Parsed RTP datagram belonging to this call.

        Returns:
            ``None`` after buffering or callback dispatch.
        """
        if not self._active or self._closed:
            return
        self._received_packets += 1
        if packet.payload_type == self._dtmf_payload_type:
            self._dtmf_receiver.handle_packet(packet)
            return
        if packet.payload_type != self.chosen_payload_type:
            return
        if packet.ssrc != self._remote_ssrc:
            self._remote_ssrc = packet.ssrc
            self._jitter.reset()
        _, frame = self._jitter.add(packet)
        if frame is not None and self.on_audio is not None:
            self.on_audio(self._codec.decode(frame.data), frame.timestamp)

    def _dispatch_dtmf(self, digit: str, duration: int) -> None:
        """Forward a decoded telephone event to the public callback.

        Args:
            digit: Decoded telephone digit.
            duration: RTP event duration in samples.

        Returns:
            ``None`` after optional callback invocation.
        """
        if self.on_dtmf is not None:
            self.on_dtmf(digit, duration)


class _SharedSessionTransport:
    """Adapt a virtual call session to aiortp's packet sender interface."""

    def __init__(
        self,
        multiplexer: SharedRtpMultiplexer,
        session: SharedRtpCallSession,
    ) -> None:
        """Bind sender output to one multiplexer session.

        Args:
            multiplexer: Shared UDP transport receiving serialized packets.
            session: Session used to select the destination flow.
        """
        self._multiplexer = multiplexer
        self._session = session

    def send(self, data: bytes, addr: tuple[str, int] | None = None) -> None:
        """Forward one serialized RTP packet through the shared flow.

        Args:
            data: Serialized RTP datagram.
            addr: Ignored explicit target retained for sender compatibility.

        Returns:
            ``None`` after forwarding.
        """
        del addr
        self._multiplexer.send(self._session, data)


def _offered_ssrc(offer: SdpMessage) -> int | None:
    """Extract an optional RFC 5576 SSRC identifier from an SDP offer.

    Args:
        offer: Remote SDP offer.

    Returns:
        Declared numeric SSRC, or ``None`` when the offer omits one.
    """
    audio = offer.audio
    if audio is None:
        return None
    for value in audio.attributes.get("ssrc", []):
        identifier = value.partition(" ")[0]
        try:
            return int(identifier)
        except ValueError:
            continue
    return None
