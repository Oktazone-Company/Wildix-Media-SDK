"""Tests for validated listener and tunnel configuration."""

from __future__ import annotations

import pytest

from wildix_media import ConfigurationError, ServerConfig, SignalingTransport


def test_tunnel_config_maps_public_and_local_ports() -> None:
    """A combined tunnel should map public TCP/UDP to one local numeric port."""
    config = ServerConfig.for_tunnel("voice.example.com", 2241, local_port=5060)

    assert config.sip_transport is SignalingTransport.TCP
    assert config.sip_port == 5060
    assert config.rtp_port_start == 5060
    assert config.advertised_sip_address == ("voice.example.com", 2241)
    assert config.advertised_rtp_port(5060) == 2241


def test_udp_signaling_cannot_share_rtp_port() -> None:
    """UDP SIP and RTP must not attempt to bind the same local UDP socket."""
    with pytest.raises(ConfigurationError, match="cannot share"):
        ServerConfig(
            sip_port=5060,
            sip_transport=SignalingTransport.UDP,
            rtp_port_start=5060,
            rtp_port_end=5060,
        )


def test_environment_parses_codec_order_and_boolean() -> None:
    """Environment loading should preserve codec preference and boolean values."""
    config = ServerConfig.from_env(
        {
            "WILDIX_MEDIA_SIP_TRANSPORT": "UDP",
            "WILDIX_MEDIA_SIP_PORT": "5070",
            "WILDIX_MEDIA_RTP_PORT_START": "12000",
            "WILDIX_MEDIA_RTP_PORT_END": "12010",
            "WILDIX_MEDIA_CODECS": "PCMU,PCMA",
            "WILDIX_MEDIA_SYMMETRIC_RTP": "false",
        }
    )

    assert config.payload_types == [0, 8]
    assert config.symmetric_rtp is False
    assert config.sip_transport is SignalingTransport.UDP


def test_public_rtp_range_must_fit_valid_ports() -> None:
    """Mapped public RTP ranges may not overflow the maximum UDP port."""
    with pytest.raises(ConfigurationError, match="between 1 and 65535"):
        ServerConfig(
            rtp_port_start=10000,
            rtp_port_end=10010,
            advertised_rtp_port_start=65530,
        )
