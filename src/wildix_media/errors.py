"""Exceptions exposed by the Wildix media SDK."""


class WildixMediaError(Exception):
    """Base exception for SDK configuration, call, and media failures."""


class ConfigurationError(WildixMediaError, ValueError):
    """Raised when server or media configuration is internally inconsistent."""


class CallClosedError(WildixMediaError, RuntimeError):
    """Raised when an operation requires a call that is still active."""


class MediaCapacityError(WildixMediaError, RuntimeError):
    """Raised when every configured RTP port is already assigned to a call."""
