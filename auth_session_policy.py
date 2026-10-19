"""POLO session expiry rules.

Timestamps are set by the server inside Flask's signed session cookie. A
background dashboard poll cannot renew the idle clock; only a verified,
CSRF-protected user-activity heartbeat can do so.
"""
import math
import time

IDLE_TIMEOUT_SECONDS = 30 * 60
ABSOLUTE_TIMEOUT_SECONDS = 8 * 60 * 60


def remaining_seconds(session_data, now=None):
    """Return (idle, absolute) seconds remaining; invalid/legacy sessions expire.

    Fail closed on missing, tampered, non-finite, future, or inconsistent times.
    """
    now = time.time() if now is None else float(now)
    try:
        started = float(session_data["auth_started_at"])
        active = float(session_data["auth_last_active_at"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return 0.0, 0.0
    if not all(math.isfinite(v) for v in (now, started, active)):
        return 0.0, 0.0
    if started <= 0 or active < started or started > now + 30 or active > now + 30:
        return 0.0, 0.0
    return (max(0.0, active + IDLE_TIMEOUT_SECONDS - now),
            max(0.0, started + ABSOLUTE_TIMEOUT_SECONDS - now))


def expiry_payload(session_data, now=None):
    idle, absolute = remaining_seconds(session_data, now)
    return {
        "idle_seconds_remaining": max(0, int(idle)),
        "absolute_seconds_remaining": max(0, int(absolute)),
    }
