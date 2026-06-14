"""Shared helpers for HTTP-based OSINT providers.

This isolates two concerns so individual provider modules stay small and, more
importantly, so the **response-parsing logic is pure and unit-testable** without
network access or API keys:

* :func:`http_get_json` -- the only place that performs network I/O.
* ``parse_*`` functions in each provider -- pure ``dict -> list[ResultEntity]``.

Credentials always come from environment variables (never code). Missing keys
are reported with an actionable message rather than raising.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import httpx

DEFAULT_TIMEOUT = 20.0


def get_env(name: str) -> Optional[str]:
    """Return the value of environment variable ``name`` (or ``None``)."""

    val = os.environ.get(name)
    return val if val else None


def require_keys(*names: str) -> Tuple[bool, str]:
    """Check that all env vars in ``names`` are set.

    Returns ``(ok, message)``. When not ok, ``message`` lists the missing vars
    and is suitable to surface directly to the user.
    """

    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        return False, (
            "Missing API credential(s): "
            + ", ".join(missing)
            + ". Set the environment variable(s) and restart the server."
        )
    return True, ""


async def http_get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    auth: Optional[Tuple[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[dict], Optional[str]]:
    """GET ``url`` and parse JSON.

    Returns ``(data, error)``. Exactly one is non-None. ``error`` is a short,
    actionable message describing what went wrong (HTTP status, timeout, ...).
    """

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params, auth=auth)
    except httpx.TimeoutException:
        return None, "Request timed out. Try again later."
    except httpx.HTTPError as exc:  # connection errors, etc.
        return None, f"Network error: {type(exc).__name__}."

    if resp.status_code == 401 or resp.status_code == 403:
        return None, "Authentication failed (401/403). Check your API key."
    if resp.status_code == 404:
        return None, "Not found (404): the queried entity has no data."
    if resp.status_code == 429:
        return None, "Rate limit exceeded (429). Wait before retrying."
    if resp.status_code >= 400:
        return None, f"API request failed with HTTP {resp.status_code}."

    try:
        return resp.json(), None
    except ValueError:
        return None, "API returned a non-JSON response."
