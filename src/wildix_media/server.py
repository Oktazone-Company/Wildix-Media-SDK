"""Async SIP user-agent server that owns calls and bidirectional RTP media."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeAlias

from aiosipua import CallSession, IncomingCall, SipUAS, TcpSipTransport, UdpSipTransport

from wildix_media.call import MediaCall
from wildix_media.config import ServerConfig, SignalingTransport
from wildix_media.errors import MediaCapacityError
from wildix_media.ports import RtpPortPool, SharedRtpPortPool
from wildix_media.shared_rtp import SharedRtpCallSession, SharedRtpMultiplexer

logger = logging.getLogger(__name__)

CallHandler: TypeAlias = Callable[[MediaCall], Awaitable[None]]
"""Coroutine that owns application processing for one accepted call."""

RtpCallSession: TypeAlias = CallSession | SharedRtpCallSession
"""Standard or multiplexed media session used by an accepted call."""


@dataclass(slots=True)
class _ActiveCall:
    """Resources owned by one accepted call."""

    call: MediaCall
    rtp_port: int
    handler_task: asyncio.Task[None] | None = None


class MediaServer:
    """Listen for direct inbound SIP calls and expose their decoded PCM media."""

    def __init__(
        self,
        config: ServerConfig | None = None,
        handler: CallHandler | None = None,
    ) -> None:
        """Initialize a stopped media server.

        Args:
            config: Validated listener and public-address configuration.
            handler: Optional coroutine invoked once for each accepted call.
        """
        self.config = config or ServerConfig()
        self._handler = handler
        self._shared_rtp = self._create_shared_rtp()
        self._ports = self._create_port_pool()
        self._uas: SipUAS | None = None
        self._active: dict[str, _ActiveCall] = {}
        self._setup_tasks: set[asyncio.Task[None]] = set()
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._stop_event = asyncio.Event()
        self._advertised_rtp_ip: str | None = None

    def _create_shared_rtp(self) -> SharedRtpMultiplexer | None:
        """Create the optional single-port RTP multiplexer.

        Returns:
            Configured multiplexer when shared mode is enabled, otherwise ``None``.
        """
        if not self.config.shared_rtp_max_calls:
            return None
        return SharedRtpMultiplexer(
            self.config.rtp_host,
            self.config.rtp_port_start,
            self.config.shared_rtp_max_calls,
        )

    def _create_port_pool(self) -> RtpPortPool | SharedRtpPortPool:
        """Create capacity tracking for the selected media topology.

        Returns:
            Exclusive RTP pair pool or multi-call slot pool.
        """
        if self.config.shared_rtp_max_calls:
            return SharedRtpPortPool(
                self.config.rtp_port_start,
                self.config.shared_rtp_max_calls,
            )
        return RtpPortPool(self.config.rtp_port_start, self.config.rtp_port_end)

    @property
    def is_running(self) -> bool:
        """Report whether the SIP listener is active.

        Returns:
            ``True`` after ``start`` and before ``stop``.
        """
        return self._uas is not None

    @property
    def active_calls(self) -> tuple[MediaCall, ...]:
        """Return a snapshot of calls currently owned by the server.

        Returns:
            Immutable tuple of active call wrappers.
        """
        return tuple(item.call for item in self._active.values())

    def on_call(self, handler: CallHandler) -> CallHandler:
        """Register the coroutine that processes accepted calls.

        Args:
            handler: Coroutine receiving one ``MediaCall``. Returning from the
                handler ends that call unless it already ended remotely.

        Returns:
            The unchanged handler, allowing decorator syntax.

        Raises:
            RuntimeError: If called after the server has started.

        Side effects:
            Replaces any previously registered call handler.
        """
        if self.is_running:
            raise RuntimeError("The call handler cannot change while the server is running")
        self._handler = handler
        return handler

    async def start(self) -> None:
        """Resolve public media addressing and bind the SIP listener.

        Returns:
            ``None`` once the listener is ready.

        Raises:
            RuntimeError: If no call handler has been registered.
            OSError: If DNS resolution or local socket binding fails.

        Side effects:
            Opens a TCP or UDP SIP listening socket.
        """
        if self.is_running:
            return
        if self._handler is None:
            raise RuntimeError("Register a call handler before starting the server")
        self._advertised_rtp_ip = await _resolve_rtp_ip(self.config)
        if self._shared_rtp is not None:
            await self._shared_rtp.start()
        transport = _create_transport(self.config)
        uas = SipUAS(
            transport,
            user_agent=self.config.user_agent,
            advertised_addr=self.config.advertised_sip_address,
        )
        uas.on_invite = self._schedule_invite
        uas.on_bye = self._handle_bye
        try:
            await uas.start()
        except Exception:
            if self._shared_rtp is not None:
                await self._shared_rtp.stop()
            raise
        self._uas = uas
        self._stop_event.clear()
        logger.info(
            "SIP media server listening on %s:%d/%s with RTP ports %d-%d/udp",
            self.config.sip_host,
            self.config.sip_port,
            self.config.sip_transport.value,
            self.config.rtp_port_start,
            self.config.rtp_port_end,
        )
        if self._shared_rtp is not None:
            logger.info(
                "Multi-call RTP multiplexer enabled on %s:%d/udp for up to %d calls",
                self.config.rtp_host,
                self.config.rtp_port_start,
                self.config.shared_rtp_max_calls,
            )

    async def stop(self) -> None:
        """End active calls and close every signaling and media socket.

        Returns:
            ``None`` after all owned resources are released. Repeated calls are safe.

        Side effects:
            Sends BYE for active calls, cancels handlers, and closes listeners.
        """
        self._stop_event.set()
        await self._cancel_setup_tasks()
        await self._wait_cleanup_tasks()
        for call_id in tuple(self._active):
            await self._finish_call(call_id, send_bye=True, cancel_handler=True)
        if self._uas is not None:
            await self._uas.stop()
            self._uas = None
        if self._shared_rtp is not None:
            await self._shared_rtp.stop()

    async def serve_forever(self) -> None:
        """Start the server and wait until ``stop`` or task cancellation.

        Returns:
            ``None`` after shutdown completes.

        Side effects:
            Owns the complete listener lifecycle for this invocation.
        """
        await self.start()
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()

    def run(self) -> None:
        """Run ``serve_forever`` in a new top-level event loop.

        Returns:
            ``None`` after the server is stopped.

        Raises:
            RuntimeError: If called from an already running event loop.
        """
        asyncio.run(self.serve_forever())

    def _schedule_invite(self, call: IncomingCall) -> None:
        """Schedule asynchronous SDP and RTP setup for an incoming INVITE.

        Args:
            call: Incoming transaction created by ``aiosipua``.

        Returns:
            ``None`` immediately; setup continues in a tracked task.
        """
        task = asyncio.create_task(self._accept_call(call), name=f"setup-{call.call_id}")
        self._setup_tasks.add(task)
        task.add_done_callback(self._setup_tasks.discard)

    async def _accept_call(self, sip_call: IncomingCall) -> None:
        """Negotiate, bind, and accept one incoming media call.

        Args:
            sip_call: Incoming SIP call with an SDP offer.

        Returns:
            ``None`` after the application handler has been scheduled or call rejected.

        Side effects:
            Allocates an RTP port and may send 180, 200, 486, or 488 SIP responses.
        """
        if sip_call.sdp_offer is None:
            sip_call.reject(488, "SDP offer required")
            return
        try:
            rtp_port = await self._ports.acquire()
        except MediaCapacityError:
            logger.warning("Call %s rejected: media capacity exhausted", sip_call.call_id)
            sip_call.reject(486, "No media capacity")
            return
        session: RtpCallSession | None = None
        try:
            session = self._new_rtp_session(sip_call, rtp_port)
            media_call = self._new_media_call(sip_call, session)
            session.on_audio = media_call._receive_audio
            session.on_dtmf = media_call._receive_dtmf
            sip_call.ringing()
            await session.start()
            sip_call.accept(session.sdp_answer)
            active = _ActiveCall(call=media_call, rtp_port=rtp_port)
            self._active[media_call.id] = active
            active.handler_task = asyncio.create_task(
                self._run_handler(media_call), name=f"call-{media_call.id}"
            )
            logger.info("Call %s accepted from %s", media_call.id, media_call.caller)
        except asyncio.CancelledError:
            if session is not None:
                await session.close()
            await self._ports.release(rtp_port)
            raise
        except Exception:
            logger.exception("Failed to establish call %s", sip_call.call_id)
            if session is not None:
                await session.close()
            await self._ports.release(rtp_port)
            sip_call.reject(488, "Media negotiation failed")

    def _new_rtp_session(self, sip_call: IncomingCall, rtp_port: int) -> RtpCallSession:
        """Create an unstarted RTP session and apply public port mapping.

        Args:
            sip_call: Incoming call containing the SDP offer.
            rtp_port: Local UDP port reserved for this call.

        Returns:
            Configured ``aiosipua.CallSession`` ready to start.

        Raises:
            ValueError: If SDP negotiation yields no audio media description.
        """
        if sip_call.sdp_offer is None:
            raise ValueError("Cannot create RTP without an SDP offer")
        if self._shared_rtp is not None:
            return self._new_shared_rtp_session(sip_call, rtp_port)
        session = CallSession(
            local_ip=self.config.rtp_host,
            rtp_port=rtp_port,
            offer=sip_call.sdp_offer,
            advertised_ip=self._advertised_rtp_ip,
            supported_codecs=self.config.payload_types,
            ptime=self.config.ptime_ms,
            symmetric_rtp=self.config.symmetric_rtp,
        )
        audio = session.sdp_answer.audio
        if audio is None:
            raise ValueError("SDP negotiation did not produce an audio stream")
        audio.port = self.config.advertised_rtp_port(rtp_port)
        return session

    def _new_shared_rtp_session(
        self,
        sip_call: IncomingCall,
        rtp_port: int,
    ) -> SharedRtpCallSession:
        """Create a virtual call session on the multi-call UDP listener.

        Args:
            sip_call: Incoming call containing a valid SDP offer.
            rtp_port: Shared local RTP port leased for capacity tracking.

        Returns:
            Unstarted multi-call session ready for SIP acceptance.

        Raises:
            ValueError: If required SDP or advertised addressing is unavailable.
        """
        if sip_call.sdp_offer is None or self._shared_rtp is None:
            raise ValueError("Multi-call RTP requires an SDP offer and active multiplexer")
        if self._advertised_rtp_ip is None:
            raise ValueError("Multi-call RTP advertised address has not been resolved")
        return SharedRtpCallSession(
            call_id=sip_call.call_id,
            offer=sip_call.sdp_offer,
            multiplexer=self._shared_rtp,
            advertised_ip=self._advertised_rtp_ip,
            advertised_port=self.config.advertised_rtp_port(rtp_port),
            supported_codecs=self.config.payload_types,
            ptime=self.config.ptime_ms,
        )

    def _new_media_call(self, sip_call: IncomingCall, session: RtpCallSession) -> MediaCall:
        """Wrap third-party signaling and RTP sessions in the public call API.

        Args:
            sip_call: Confirmed or soon-to-be-confirmed SIP call.
            session: Negotiated RTP session.

        Returns:
            A high-level media call bound to this server's lifecycle.
        """
        return MediaCall(
            sip_call,
            session,
            self._request_hangup,
            queue_frames=self.config.audio_queue_frames,
        )

    async def _run_handler(self, call: MediaCall) -> None:
        """Run application code and guarantee cleanup when it returns.

        Args:
            call: Accepted call passed to the registered handler.

        Returns:
            ``None`` after handler completion and call cleanup.
        """
        try:
            if self._handler is not None:
                await self._handler(call)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Call handler failed for %s", call.id)
        finally:
            await self._finish_call(call.id, send_bye=True, cancel_handler=False)

    def _handle_bye(self, sip_call: IncomingCall, request: object) -> None:
        """Schedule cleanup after a remote BYE has already been acknowledged.

        Args:
            sip_call: Incoming call being terminated.
            request: Parsed BYE request; no further response handling is needed here.

        Returns:
            ``None`` immediately; cleanup continues asynchronously.
        """
        del request
        task = asyncio.create_task(
            self._finish_call(sip_call.call_id, send_bye=False, cancel_handler=True),
            name=f"bye-{sip_call.call_id}",
        )
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _request_hangup(self, call_id: str) -> None:
        """Handle a public ``MediaCall.hangup`` request.

        Args:
            call_id: Identifier of the call to end.

        Returns:
            ``None`` after resources are released.
        """
        await self._finish_call(call_id, send_bye=True, cancel_handler=False)

    async def _finish_call(
        self,
        call_id: str,
        *,
        send_bye: bool,
        cancel_handler: bool,
    ) -> None:
        """Remove and close one active call exactly once.

        Args:
            call_id: Call to remove from active ownership.
            send_bye: Whether the SDK should send the terminating SIP BYE.
            cancel_handler: Whether a still-running application task should be cancelled.

        Returns:
            ``None`` after media closure and port release.
        """
        active = self._active.pop(call_id, None)
        if active is None:
            return
        await active.call._shutdown(send_bye=send_bye)
        await self._ports.release(active.rtp_port)
        task = active.handler_task
        current = asyncio.current_task()
        if cancel_handler and task is not None and task is not current:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        logger.info("Call %s closed", call_id)

    async def _cancel_setup_tasks(self) -> None:
        """Cancel pending INVITE setup tasks during server shutdown.

        Returns:
            ``None`` after all setup tasks have settled.
        """
        tasks = tuple(self._setup_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._setup_tasks.clear()

    async def _wait_cleanup_tasks(self) -> None:
        """Wait for remote-BYE cleanup already in progress.

        Returns:
            ``None`` after every retained cleanup task has settled.
        """
        tasks = tuple(self._cleanup_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cleanup_tasks.clear()


def _create_transport(config: ServerConfig) -> TcpSipTransport | UdpSipTransport:
    """Construct the configured ``aiosipua`` signaling transport.

    Args:
        config: Server listener configuration.

    Returns:
        A stopped TCP or UDP SIP transport.
    """
    local_addr = (config.sip_host, config.sip_port)
    if config.sip_transport is SignalingTransport.TCP:
        return TcpSipTransport(local_addr=local_addr)
    return UdpSipTransport(local_addr=local_addr)


async def _resolve_rtp_ip(config: ServerConfig) -> str:
    """Resolve the host that will be placed in SDP to one IPv4 address.

    Args:
        config: Server configuration containing local and optional public hosts.

    Returns:
        IPv4 address suitable for an SDP ``c=`` line.

    Raises:
        OSError: If a configured hostname has no IPv4 address.
        ValueError: If wildcard local RTP binding has no public host.
    """
    host = config.advertised_rtp_host or config.rtp_host
    if host == "0.0.0.0":
        raise ValueError("Set advertised_rtp_host when rtp_host is 0.0.0.0")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        loop = asyncio.get_running_loop()
        addresses = await loop.getaddrinfo(host, None, family=socket.AF_INET)
        if not addresses:
            raise OSError(f"Could not resolve advertised RTP host {host!r}") from None
        return str(addresses[0][4][0])
