"""Validated configuration for SIP signaling and RTP media endpoints."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import partial

from wildix_media.errors import ConfigurationError


class SignalingTransport(StrEnum):
    """Supported SIP signaling transports."""

    TCP = "tcp"
    UDP = "udp"

    @classmethod
    def parse(cls, value: str) -> SignalingTransport:
        """Parse a transport name without requiring exact letter case.

        Args:
            value: User-provided transport name.

        Returns:
            The matching signaling transport.

        Raises:
            ConfigurationError: If the value is not ``tcp`` or ``udp``.
        """
        try:
            return cls(value.strip().lower())
        except ValueError as error:
            raise ConfigurationError(f"Unsupported SIP transport: {value!r}") from error


class AudioCodec(StrEnum):
    """G.711 codecs supported by the initial SDK release."""

    PCMA = "PCMA"
    PCMU = "PCMU"

    @property
    def payload_type(self) -> int:
        """Return the static RTP payload type for this codec.

        Returns:
            ``8`` for PCMA or ``0`` for PCMU.
        """
        return 8 if self is AudioCodec.PCMA else 0

    @classmethod
    def parse(cls, value: str) -> AudioCodec:
        """Parse a codec name without requiring exact letter case.

        Args:
            value: Codec name such as ``PCMA`` or ``PCMU``.

        Returns:
            The matching audio codec.

        Raises:
            ConfigurationError: If the codec is not supported.
        """
        try:
            return cls(value.strip().upper())
        except ValueError as error:
            raise ConfigurationError(f"Unsupported audio codec: {value!r}") from error


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Configuration for a direct inbound SIP and RTP media server.

    TCP signaling can share a numeric port with UDP RTP. UDP signaling must use
    a port outside the RTP range because both would otherwise bind one UDP port.
    """

    sip_host: str = "127.0.0.1"
    sip_port: int = 5060
    sip_transport: SignalingTransport = SignalingTransport.TCP
    rtp_host: str = "127.0.0.1"
    rtp_port_start: int = 10000
    rtp_port_end: int = 10100
    advertised_sip_host: str | None = None
    advertised_sip_port: int | None = None
    advertised_rtp_host: str | None = None
    advertised_rtp_port_start: int | None = None
    codecs: tuple[AudioCodec, ...] = (AudioCodec.PCMA, AudioCodec.PCMU)
    ptime_ms: int = 20
    audio_queue_frames: int = 250
    symmetric_rtp: bool = True
    user_agent: str = "OktaZone-Wildix-Media-SDK/0.1"

    def __post_init__(self) -> None:
        """Validate addresses, port mappings, codecs, and frame settings.

        Raises:
            ConfigurationError: If any value cannot produce a valid server.
        """
        _validate_port("sip_port", self.sip_port)
        _validate_port("rtp_port_start", self.rtp_port_start)
        _validate_port("rtp_port_end", self.rtp_port_end)
        if self.rtp_port_end < self.rtp_port_start:
            raise ConfigurationError("rtp_port_end must be greater than or equal to the start")
        if self.sip_transport is SignalingTransport.UDP and self._sip_overlaps_rtp():
            raise ConfigurationError("UDP SIP signaling cannot share a UDP port with RTP")
        if not self.codecs:
            raise ConfigurationError("At least one audio codec must be enabled")
        if self.ptime_ms not in {10, 20, 30, 40, 60}:
            raise ConfigurationError("ptime_ms must be one of 10, 20, 30, 40, or 60")
        if self.audio_queue_frames < 1:
            raise ConfigurationError("audio_queue_frames must be at least 1")
        self._validate_advertised_ports()

    @property
    def payload_types(self) -> list[int]:
        """Return codec RTP payload types in preference order.

        Returns:
            A new list suitable for ``aiosipua.CallSession``.
        """
        return [codec.payload_type for codec in self.codecs]

    @property
    def advertised_sip_address(self) -> tuple[str, int] | None:
        """Return the public SIP address used in Via and Contact headers.

        Returns:
            ``(host, port)`` when a public host is configured, otherwise ``None``.
        """
        if self.advertised_sip_host is None:
            return None
        return self.advertised_sip_host, self.advertised_sip_port or self.sip_port

    def advertised_rtp_port(self, local_port: int) -> int:
        """Map a local RTP port to the corresponding public RTP port.

        Args:
            local_port: Port allocated from the configured local RTP range.

        Returns:
            The public port to place in the SDP answer.

        Raises:
            ConfigurationError: If the local port is outside the configured range.
        """
        if not self.rtp_port_start <= local_port <= self.rtp_port_end:
            raise ConfigurationError(f"RTP port {local_port} is outside the configured range")
        if self.advertised_rtp_port_start is None:
            return local_port
        return self.advertised_rtp_port_start + local_port - self.rtp_port_start

    @classmethod
    def for_tunnel(
        cls,
        public_host: str,
        public_port: int,
        *,
        local_host: str = "0.0.0.0",
        local_port: int = 5060,
    ) -> ServerConfig:
        """Create a single-call config for one combined TCP/UDP tunnel.

        Args:
            public_host: Public tunnel hostname or IP address.
            public_port: Public TCP and UDP port exposed by the tunnel.
            local_host: Local interface used by both listeners.
            local_port: Local TCP SIP and UDP RTP port targeted by the tunnel.

        Returns:
            A config using TCP SIP and one UDP RTP port.

        Raises:
            ConfigurationError: If a port or host value is invalid.
        """
        return cls(
            sip_host=local_host,
            sip_port=local_port,
            sip_transport=SignalingTransport.TCP,
            rtp_host=local_host,
            rtp_port_start=local_port,
            rtp_port_end=local_port,
            advertised_sip_host=public_host,
            advertised_sip_port=public_port,
            advertised_rtp_host=public_host,
            advertised_rtp_port_start=public_port,
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "WILDIX_MEDIA_",
    ) -> ServerConfig:
        """Build configuration from environment variables.

        Args:
            environ: Source mapping. Uses ``os.environ`` when omitted.
            prefix: Prefix applied to every supported variable name.

        Returns:
            A fully validated server configuration.

        Raises:
            ConfigurationError: If an environment value is malformed.
        """
        values = os.environ if environ is None else environ
        get = partial(_get_env, values, prefix)
        codecs = tuple(AudioCodec.parse(item) for item in get("CODECS", "PCMA,PCMU").split(","))
        return cls(
            sip_host=get("SIP_HOST", "127.0.0.1"),
            sip_port=_parse_int(get("SIP_PORT", "5060"), "SIP_PORT"),
            sip_transport=SignalingTransport.parse(get("SIP_TRANSPORT", "tcp")),
            rtp_host=get("RTP_HOST", "127.0.0.1"),
            rtp_port_start=_parse_int(get("RTP_PORT_START", "10000"), "RTP_PORT_START"),
            rtp_port_end=_parse_int(get("RTP_PORT_END", "10100"), "RTP_PORT_END"),
            advertised_sip_host=_optional(get("ADVERTISED_SIP_HOST")),
            advertised_sip_port=_optional_int(get("ADVERTISED_SIP_PORT"), "ADVERTISED_SIP_PORT"),
            advertised_rtp_host=_optional(get("ADVERTISED_RTP_HOST")),
            advertised_rtp_port_start=_optional_int(
                get("ADVERTISED_RTP_PORT_START"), "ADVERTISED_RTP_PORT_START"
            ),
            codecs=codecs,
            ptime_ms=_parse_int(get("PTIME_MS", "20"), "PTIME_MS"),
            audio_queue_frames=_parse_int(get("AUDIO_QUEUE_FRAMES", "250"), "AUDIO_QUEUE_FRAMES"),
            symmetric_rtp=_parse_bool(get("SYMMETRIC_RTP", "true"), "SYMMETRIC_RTP"),
            user_agent=get("USER_AGENT", "OktaZone-Wildix-Media-SDK/0.1"),
        )

    def _sip_overlaps_rtp(self) -> bool:
        """Check whether the SIP UDP port falls inside the RTP range.

        Returns:
            ``True`` when the numeric ports overlap.
        """
        return self.rtp_port_start <= self.sip_port <= self.rtp_port_end

    def _validate_advertised_ports(self) -> None:
        """Validate optional public SIP and RTP port mappings.

        Raises:
            ConfigurationError: If a public port falls outside the valid range.
        """
        if self.advertised_sip_port is not None:
            _validate_port("advertised_sip_port", self.advertised_sip_port)
        if self.advertised_rtp_port_start is None:
            return
        _validate_port("advertised_rtp_port_start", self.advertised_rtp_port_start)
        mapped_end = self.advertised_rtp_port(self.rtp_port_end)
        _validate_port("advertised RTP range end", mapped_end)


def _validate_port(name: str, value: int) -> None:
    """Validate a TCP or UDP port number.

    Args:
        name: Human-readable field name for error messages.
        value: Port number to validate.

    Returns:
        ``None``.

    Raises:
        ConfigurationError: If the value is outside ``1..65535``.
    """
    if not 1 <= value <= 65535:
        raise ConfigurationError(f"{name} must be between 1 and 65535")


def _get_env(values: Mapping[str, str], prefix: str, name: str, default: str = "") -> str:
    """Read one prefixed value from an environment-like mapping.

    Args:
        values: Source environment mapping.
        prefix: Prefix applied before the variable name.
        name: Unprefixed variable name.
        default: Value returned when the variable is absent.

    Returns:
        Configured text or the supplied default.
    """
    return values.get(f"{prefix}{name}", default)


def _parse_int(value: str, name: str) -> int:
    """Parse one required integer environment value.

    Args:
        value: Text to parse.
        name: Variable suffix used in error messages.

    Returns:
        Parsed integer.

    Raises:
        ConfigurationError: If the text is not an integer.
    """
    try:
        return int(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error


def _optional_int(value: str, name: str) -> int | None:
    """Parse an optional integer environment value.

    Args:
        value: Empty text or an integer.
        name: Variable suffix used in error messages.

    Returns:
        Parsed integer, or ``None`` when the text is empty.

    Raises:
        ConfigurationError: If non-empty text is not an integer.
    """
    return None if not value.strip() else _parse_int(value, name)


def _optional(value: str) -> str | None:
    """Normalize an optional string environment value.

    Args:
        value: Possibly empty text.

    Returns:
        Stripped text, or ``None`` when empty.
    """
    stripped = value.strip()
    return stripped or None


def _parse_bool(value: str, name: str) -> bool:
    """Parse a conventional boolean environment value.

    Args:
        value: Boolean text such as ``true`` or ``false``.
        name: Variable suffix used in error messages.

    Returns:
        Parsed boolean.

    Raises:
        ConfigurationError: If the text is not a recognized boolean.
    """
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")
