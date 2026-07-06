"""Typed exceptions raised by the Rosreestr client and tool layer.

These exceptions are caught at the MCP boundary in :mod:`server` and converted
into structured error responses for the agent.
"""

from __future__ import annotations


class RosreestrError(Exception):
    """Base class for all errors originating from this package."""


class RosreestrValidationError(RosreestrError):
    """Input validation failed before any HTTP call was made."""


class RosreestrNotFoundError(RosreestrError):
    """Object (cadastral number, address, point) is not present in the registry."""


class RosreestrApiError(RosreestrError):
    """Upstream returned an HTTP error or an unexpected payload."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RosreestrTimeoutError(RosreestrError):
    """Upstream did not respond within the configured timeout."""


class RosreestrParseError(RosreestrError):
    """Upstream responded but the payload could not be parsed."""


class RosreestrUpstreamUnavailable(RosreestrError):
    """All known upstreams (NSPD primary, PKK fallback) are unreachable."""


class RosreestrRateLimited(RosreestrError):
    """Upstream is throttling our requests; retry later."""

    def __init__(self, message: str, retry_after_sec: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec
